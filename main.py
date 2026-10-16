from __future__ import annotations

import argparse
import base64
import binascii
import csv
import hmac
import io
import json
import math
import mimetypes
import os
import re
import sqlite3
from contextlib import closing
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit


BASE_DIR = Path(__file__).resolve().parent
STATUSES = {"active", "warning", "evacuating", "resolved", "unknown"}
FLOOD_TYPES = {"river", "flash", "urban", "coastal", "other", "unknown"}
RECORD_COLUMNS = (
    "occurred_at",
    "reported_at",
    "flood_type",
    "province",
    "district",
    "subdistrict",
    "villages",
    "latitude",
    "longitude",
    "water_level",
    "affected_households",
    "affected_people",
    "evacuated_people",
    "injured",
    "deaths",
    "status",
    "notes",
    "source_url",
)
CSV_HEADERS = (
    ("occurred_at", "วันที่เกิดเหตุ"),
    ("reported_at", "วันที่รายงาน"),
    ("flood_type", "ประเภทอุทกภัย"),
    ("province", "จังหวัด"),
    ("district", "อำเภอ/เขต"),
    ("subdistrict", "ตำบล/แขวง"),
    ("villages", "หมู่บ้าน/พื้นที่"),
    ("latitude", "ละติจูด"),
    ("longitude", "ลองจิจูด"),
    ("water_level", "ระดับน้ำ (เมตร)"),
    ("affected_households", "ครัวเรือนที่ได้รับผลกระทบ"),
    ("affected_people", "ประชาชนที่ได้รับผลกระทบ"),
    ("evacuated_people", "ผู้อพยพ"),
    ("injured", "ผู้บาดเจ็บ"),
    ("deaths", "ผู้เสียชีวิต"),
    ("status", "สถานะ"),
    ("notes", "หมายเหตุ"),
    ("source_url", "แหล่งข้อมูล"),
)
STATIC_FILES = {
    "/static/app.css": BASE_DIR / "static" / "app.css",
    "/static/app.js": BASE_DIR / "static" / "app.js",
}
CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS flood_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    occurred_at TEXT NOT NULL,
    reported_at TEXT,
    flood_type TEXT NOT NULL DEFAULT 'unknown'
        CHECK (flood_type IN ('river', 'flash', 'urban', 'coastal', 'other', 'unknown')),
    province TEXT NOT NULL,
    district TEXT NOT NULL,
    subdistrict TEXT NOT NULL DEFAULT '',
    villages TEXT NOT NULL DEFAULT '',
    latitude REAL CHECK (latitude IS NULL OR (latitude >= -90 AND latitude <= 90)),
    longitude REAL CHECK (longitude IS NULL OR (longitude >= -180 AND longitude <= 180)),
    water_level REAL CHECK (water_level IS NULL OR water_level >= 0),
    affected_households INTEGER
        CHECK (affected_households IS NULL OR affected_households >= 0),
    affected_people INTEGER CHECK (affected_people IS NULL OR affected_people >= 0),
    evacuated_people INTEGER CHECK (evacuated_people IS NULL OR evacuated_people >= 0),
    injured INTEGER CHECK (injured IS NULL OR injured >= 0),
    deaths INTEGER CHECK (deaths IS NULL OR deaths >= 0),
    status TEXT NOT NULL
        CHECK (status IN ('active', 'warning', 'evacuating', 'resolved', 'unknown')),
    notes TEXT NOT NULL DEFAULT '',
    source_url TEXT NOT NULL DEFAULT ''
)
"""


def connect_database(db_path: str | Path) -> sqlite3.Connection:
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    return connection


def initialize_database(db_path: str | Path) -> None:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(connect_database(path)) as connection:
        with connection:
            connection.execute(
                CREATE_TABLE_SQL
            )
            columns = {
                row["name"]: row
                for row in connection.execute("PRAGMA table_info(flood_records)")
            }
            additive_columns = {
                "reported_at": "TEXT",
                "flood_type": "TEXT NOT NULL DEFAULT 'unknown'",
                "subdistrict": "TEXT NOT NULL DEFAULT ''",
                "villages": "TEXT NOT NULL DEFAULT ''",
                "latitude": "REAL",
                "longitude": "REAL",
                "affected_people": "INTEGER",
                "evacuated_people": "INTEGER",
                "injured": "INTEGER",
                "deaths": "INTEGER",
                "source_url": "TEXT NOT NULL DEFAULT ''",
            }
            for name, definition in additive_columns.items():
                if name not in columns:
                    connection.execute(
                        f"ALTER TABLE flood_records ADD COLUMN {name} {definition}"
                    )
                    columns[name] = {"name": name, "notnull": int("NOT NULL" in definition)}
            table_sql = connection.execute(
                "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'flood_records'"
            ).fetchone()["sql"]
            status_definition = table_sql.split(
                "status TEXT NOT NULL", 1
            )[1].split("notes TEXT", 1)[0]
            if (
                columns["water_level"]["notnull"]
                or columns["affected_households"]["notnull"]
                or "'unknown'" not in status_definition
            ):
                connection.execute("ALTER TABLE flood_records RENAME TO flood_records_legacy")
                connection.execute(CREATE_TABLE_SQL)
                column_names = ", ".join(RECORD_COLUMNS)
                connection.execute(
                    f"INSERT INTO flood_records (id, {column_names}) "
                    f"SELECT id, {column_names} FROM flood_records_legacy"
                )
                connection.execute("DROP TABLE flood_records_legacy")


def _required_text(payload: dict[str, object], field: str, label: str, limit: int) -> str:
    value = payload.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"กรุณาระบุ{label}")
    value = value.strip()
    if len(value) > limit:
        raise ValueError(f"{label}ต้องมีความยาวไม่เกิน {limit} ตัวอักษร")
    return value


def _optional_text(payload: dict[str, object], field: str, label: str, limit: int) -> str:
    value = payload.get(field, "")
    if value is None:
        return ""
    if not isinstance(value, str) or len(value) > limit:
        raise ValueError(f"{label}ต้องเป็นข้อความและมีความยาวไม่เกิน {limit} ตัวอักษร")
    return value.strip()


def _optional_integer(
    payload: dict[str, object], field: str, label: str
) -> int | None:
    raw_value = payload.get(field)
    if raw_value in (None, ""):
        return None
    try:
        if isinstance(raw_value, bool) or not isinstance(raw_value, (int, float, str)):
            raise ValueError
        if isinstance(raw_value, float) and not raw_value.is_integer():
            raise ValueError
        value = int(raw_value)
        if value < 0:
            raise ValueError
    except (TypeError, ValueError):
        raise ValueError(f"{label}ต้องเป็นจำนวนเต็มตั้งแต่ 0 ขึ้นไป หรือไม่ระบุ") from None
    return value


def _optional_coordinate(
    payload: dict[str, object], field: str, label: str, minimum: float, maximum: float
) -> float | None:
    raw_value = payload.get(field)
    if raw_value in (None, ""):
        return None
    try:
        if isinstance(raw_value, bool) or not isinstance(raw_value, (int, float, str)):
            raise ValueError
        value = float(raw_value)
        if not math.isfinite(value) or not minimum <= value <= maximum:
            raise ValueError
    except (TypeError, ValueError):
        raise ValueError(f"{label}ต้องอยู่ระหว่าง {minimum} ถึง {maximum}") from None
    return value


def validate_record(payload: object) -> dict[str, object]:
    if not isinstance(payload, dict):
        raise ValueError("รูปแบบข้อมูลไม่ถูกต้อง")

    occurred_at = _required_text(payload, "occurred_at", "วันที่", 10)
    try:
        date.fromisoformat(occurred_at)
    except ValueError:
        raise ValueError("วันที่ไม่ถูกต้อง กรุณาใช้รูปแบบ YYYY-MM-DD") from None

    reported_at = payload.get("reported_at", "")
    if reported_at is None:
        reported_at = ""
    if reported_at:
        if not isinstance(reported_at, str):
            raise ValueError("วันที่รายงานไม่ถูกต้อง กรุณาใช้รูปแบบ YYYY-MM-DD")
        try:
            date.fromisoformat(reported_at)
        except ValueError:
            raise ValueError("วันที่รายงานไม่ถูกต้อง กรุณาใช้รูปแบบ YYYY-MM-DD") from None
    else:
        reported_at = None

    flood_type = payload.get("flood_type", "unknown")
    if not isinstance(flood_type, str) or flood_type not in FLOOD_TYPES:
        raise ValueError("ประเภทอุทกภัยไม่ถูกต้อง")
    province = _required_text(payload, "province", "จังหวัด", 100)
    district = _required_text(payload, "district", "อำเภอ/เขต", 100)
    subdistrict = _optional_text(payload, "subdistrict", "ตำบล/แขวง", 100)
    villages = _optional_text(payload, "villages", "หมู่บ้าน/พื้นที่", 500)
    latitude = _optional_coordinate(payload, "latitude", "ละติจูด", -90, 90)
    longitude = _optional_coordinate(payload, "longitude", "ลองจิจูด", -180, 180)
    if (latitude is None) != (longitude is None):
        raise ValueError("กรุณาระบุพิกัดละติจูดและลองจิจูดให้ครบทั้งคู่")

    raw_water_level = payload.get("water_level")
    if raw_water_level in (None, ""):
        water_level = None
    else:
        try:
            if isinstance(raw_water_level, bool) or not isinstance(
                raw_water_level, (int, float, str)
            ):
                raise ValueError
            water_level = float(raw_water_level)
            if not math.isfinite(water_level) or water_level < 0:
                raise ValueError
        except (TypeError, ValueError):
            raise ValueError("ระดับน้ำต้องเป็นตัวเลขตั้งแต่ 0 เมตรขึ้นไป หรือไม่ระบุ") from None

    affected_households = _optional_integer(
        payload, "affected_households", "จำนวนครัวเรือน"
    )
    affected_people = _optional_integer(payload, "affected_people", "จำนวนผู้ได้รับผลกระทบ")
    evacuated_people = _optional_integer(payload, "evacuated_people", "จำนวนผู้อพยพ")
    injured = _optional_integer(payload, "injured", "จำนวนผู้บาดเจ็บ")
    deaths = _optional_integer(payload, "deaths", "จำนวนผู้เสียชีวิต")

    status = payload.get("status")
    if not isinstance(status, str) or status not in STATUSES:
        raise ValueError("สถานะไม่ถูกต้อง")

    notes = _optional_text(payload, "notes", "หมายเหตุ", 3000)

    source_url = payload.get("source_url", "")
    if not isinstance(source_url, str) or len(source_url) > 2000:
        raise ValueError("ลิงก์แหล่งข้อมูลไม่ถูกต้อง")
    source_url = source_url.strip()
    if source_url and not source_url.startswith(("https://", "http://")):
        raise ValueError("ลิงก์แหล่งข้อมูลต้องขึ้นต้นด้วย http:// หรือ https://")

    return {
        "occurred_at": occurred_at,
        "reported_at": reported_at,
        "flood_type": flood_type,
        "province": province,
        "district": district,
        "subdistrict": subdistrict,
        "villages": villages,
        "latitude": latitude,
        "longitude": longitude,
        "water_level": water_level,
        "affected_households": affected_households,
        "affected_people": affected_people,
        "evacuated_people": evacuated_people,
        "injured": injured,
        "deaths": deaths,
        "status": status,
        "notes": notes,
        "source_url": source_url,
    }


def list_records(
    db_path: str | Path,
    query: str = "",
    status: str = "",
    date_from: str = "",
    date_to: str = "",
) -> list[dict[str, object]]:
    conditions: list[str] = []
    parameters: list[object] = []
    if query:
        conditions.append(
            "(province LIKE ? OR district LIKE ? OR subdistrict LIKE ? "
            "OR villages LIKE ? OR flood_type LIKE ? OR notes LIKE ? "
            "OR source_url LIKE ?)"
        )
        search = f"%{query}%"
        parameters.extend((search, search, search, search, search, search, search))
    if status in STATUSES:
        conditions.append("status = ?")
        parameters.append(status)
    if date_from:
        conditions.append("occurred_at >= ?")
        parameters.append(date_from)
    if date_to:
        conditions.append("occurred_at <= ?")
        parameters.append(date_to)

    where_clause = f" WHERE {' AND '.join(conditions)}" if conditions else ""
    with closing(connect_database(db_path)) as connection:
        rows = connection.execute(
            f"SELECT * FROM flood_records{where_clause} "
            "ORDER BY occurred_at DESC, id DESC",
            parameters,
        ).fetchall()
    return [dict(row) for row in rows]


def get_summary(db_path: str | Path) -> dict[str, int]:
    with closing(connect_database(db_path)) as connection:
        row = connection.execute(
            """
            SELECT
                COUNT(*) AS record_count,
                COALESCE(SUM(affected_households), 0) AS affected_households,
                COUNT(DISTINCT province) AS province_count,
                COALESCE(SUM(CASE WHEN status IN ('warning', 'evacuating')
                    THEN 1 ELSE 0 END), 0) AS urgent_count,
                COUNT(affected_households) AS households_reported_count,
                COALESCE(SUM(affected_people), 0) AS affected_people,
                COUNT(affected_people) AS people_reported_count,
                COALESCE(SUM(evacuated_people), 0) AS evacuated_people,
                COUNT(evacuated_people) AS evacuated_reported_count,
                COALESCE(SUM(injured), 0) AS injured,
                COUNT(injured) AS injured_reported_count,
                COALESCE(SUM(deaths), 0) AS deaths,
                COUNT(deaths) AS deaths_reported_count
            FROM flood_records
            """
        ).fetchone()
    return dict(row)


def _record_values(record: dict[str, object]) -> dict[str, object]:
    return {column: record.get(column) for column in RECORD_COLUMNS}


def create_record(db_path: str | Path, record: dict[str, object]) -> int:
    with closing(connect_database(db_path)) as connection:
        with connection:
            columns = ", ".join(RECORD_COLUMNS)
            values = ", ".join(f":{column}" for column in RECORD_COLUMNS)
            cursor = connection.execute(
                f"INSERT INTO flood_records ({columns}) VALUES ({values})",
                _record_values(record),
            )
            return int(cursor.lastrowid)


def update_record(db_path: str | Path, record_id: int, record: dict[str, object]) -> bool:
    with closing(connect_database(db_path)) as connection:
        with connection:
            assignments = ", ".join(
                f"{column} = :{column}" for column in RECORD_COLUMNS
            )
            cursor = connection.execute(
                f"UPDATE flood_records SET {assignments} WHERE id = :id",
                {**_record_values(record), "id": record_id},
            )
            return cursor.rowcount > 0


def _csv_safe(value: object) -> object:
    if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@")):
        return f"'{value}"
    return value


def export_records_csv(records: list[dict[str, object]]) -> bytes:
    status_labels = {
        "active": "กำลังเกิดเหตุ",
        "warning": "เฝ้าระวัง",
        "evacuating": "อพยพ",
        "resolved": "คลี่คลายแล้ว",
        "unknown": "ไม่ระบุสถานะ",
    }
    flood_type_labels = {
        "river": "น้ำล้นตลิ่ง/น้ำแม่น้ำ",
        "flash": "น้ำป่าไหลหลาก/น้ำท่วมฉับพลัน",
        "urban": "น้ำท่วมขังในชุมชน",
        "coastal": "น้ำทะเลหนุน/น้ำท่วมชายฝั่ง",
        "other": "อื่น ๆ",
        "unknown": "ไม่ระบุประเภท",
    }
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow([label for _, label in CSV_HEADERS])
    for record in records:
        values = dict(record)
        values["flood_type"] = flood_type_labels.get(
            str(record.get("flood_type")), "ไม่ระบุประเภท"
        )
        values["status"] = status_labels.get(
            str(record.get("status")), "ไม่ระบุสถานะ"
        )
        writer.writerow(
            [_csv_safe(values.get(key)) for key, _ in CSV_HEADERS]
        )
    return ("\ufeff" + output.getvalue()).encode("utf-8")


def delete_record(db_path: str | Path, record_id: int) -> bool:
    with closing(connect_database(db_path)) as connection:
        with connection:
            cursor = connection.execute(
                "DELETE FROM flood_records WHERE id = ?", (record_id,)
            )
            return cursor.rowcount > 0


class FloodRequestHandler(BaseHTTPRequestHandler):
    db_path: str

    def __init__(
        self,
        *args: object,
        db_path: str,
        admin_username: str | None = None,
        admin_password: str | None = None,
        **kwargs: object,
    ) -> None:
        self.db_path = db_path
        self.admin_username = admin_username or os.environ.get(
            "FLOODWATCH_ADMIN_USERNAME", "admin"
        )
        self.admin_password = admin_password or os.environ.get(
            "FLOODWATCH_ADMIN_PASSWORD"
        )
        super().__init__(*args, **kwargs)

    def _admin_authorized(self) -> bool:
        if not self.admin_password:
            self._send_error_json("ยังไม่ได้ตั้งค่ารหัสผ่านผู้ดูแลระบบ", 503)
            return False

        scheme, separator, encoded_credentials = self.headers.get(
            "Authorization", ""
        ).partition(" ")
        if not separator or scheme.lower() != "basic":
            self._send_error_json("กรุณาเข้าสู่ระบบผู้ดูแล", 401)
            return False
        try:
            decoded = base64.b64decode(encoded_credentials, validate=True).decode("utf-8")
            username, password = decoded.split(":", 1)
        except (binascii.Error, UnicodeDecodeError, ValueError):
            self._send_error_json("ข้อมูลเข้าสู่ระบบไม่ถูกต้อง", 401)
            return False

        username_matches = hmac.compare_digest(
            username.encode("utf-8"), self.admin_username.encode("utf-8")
        )
        password_matches = hmac.compare_digest(
            password.encode("utf-8"), self.admin_password.encode("utf-8")
        )
        if not (username_matches and password_matches):
            self._send_error_json("ชื่อผู้ใช้หรือรหัสผ่านไม่ถูกต้อง", 401)
            return False
        return True

    def _send_json(self, data: object, status_code: int = 200) -> None:
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def _send_error_json(self, message: str, status_code: int) -> None:
        self._send_json({"error": message}, status_code)

    def _send_csv(self, body: bytes) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/csv; charset=utf-8")
        self.send_header(
            "Content-Disposition", 'attachment; filename="flood_records.csv"'
        )
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> object:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            raise ValueError("ขนาดข้อมูลไม่ถูกต้อง") from None
        if length <= 0 or length > 1_000_000:
            raise ValueError("ขนาดข้อมูลไม่ถูกต้อง")
        try:
            return json.loads(self.rfile.read(length))
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise ValueError("ข้อมูล JSON ไม่ถูกต้อง") from None

    def do_GET(self) -> None:
        parsed = urlsplit(self.path)
        if parsed.path == "/":
            self._send_static(BASE_DIR / "static" / "index.html")
        elif parsed.path == "/api/auth/check":
            if self._admin_authorized():
                self._send_json({"success": True})
        elif parsed.path in {"/api/records", "/api/export.csv"}:
            params = parse_qs(parsed.query)
            query = params.get("q", [""])[0].strip()[:100]
            status = params.get("status", [""])[0]
            date_from = params.get("from", [""])[0]
            date_to = params.get("to", [""])[0]
            if status and status not in STATUSES:
                self._send_error_json("ตัวกรองสถานะไม่ถูกต้อง", 400)
                return
            try:
                if date_from:
                    date.fromisoformat(date_from)
                if date_to:
                    date.fromisoformat(date_to)
            except ValueError:
                self._send_error_json("ช่วงวันที่ไม่ถูกต้อง กรุณาใช้รูปแบบ YYYY-MM-DD", 400)
                return
            if date_from and date_to and date_from > date_to:
                self._send_error_json("วันที่เริ่มต้นต้องไม่อยู่หลังวันที่สิ้นสุด", 400)
                return
            records = list_records(
                self.db_path, query, status, date_from, date_to
            )
            if parsed.path == "/api/export.csv":
                self._send_csv(export_records_csv(records))
            else:
                self._send_json(records)
        elif parsed.path == "/api/summary":
            self._send_json(get_summary(self.db_path))
        elif parsed.path in STATIC_FILES:
            self._send_static(STATIC_FILES[parsed.path])
        else:
            self._send_error_json("ไม่พบข้อมูลที่ร้องขอ", 404)

    def do_POST(self) -> None:
        if urlsplit(self.path).path != "/api/records":
            self._send_error_json("ไม่พบข้อมูลที่ร้องขอ", 404)
            return
        if not self._admin_authorized():
            return
        try:
            record = validate_record(self._read_json())
        except ValueError as error:
            self._send_error_json(str(error), 400)
            return
        record_id = create_record(self.db_path, record)
        self._send_json({"id": record_id}, 201)

    def do_PUT(self) -> None:
        record_id = self._record_id_from_path()
        if record_id is None:
            self._send_error_json("ไม่พบข้อมูลที่ร้องขอ", 404)
            return
        if not self._admin_authorized():
            return
        try:
            record = validate_record(self._read_json())
        except ValueError as error:
            self._send_error_json(str(error), 400)
            return
        if not update_record(self.db_path, record_id, record):
            self._send_error_json("ไม่พบรายการที่ต้องการแก้ไข", 404)
            return
        self._send_json({"success": True})

    def do_DELETE(self) -> None:
        record_id = self._record_id_from_path()
        if record_id is None:
            self._send_error_json("ไม่พบข้อมูลที่ร้องขอ", 404)
            return
        if not self._admin_authorized():
            return
        if not delete_record(self.db_path, record_id):
            self._send_error_json("ไม่พบรายการที่ต้องการลบ", 404)
            return
        self._send_json({"success": True})

    def _record_id_from_path(self) -> int | None:
        match = re.fullmatch(r"/api/records/(\d{1,10})", urlsplit(self.path).path)
        if match is None:
            return None
        return int(match.group(1))

    def _send_static(self, path: Path) -> None:
        try:
            body = path.read_bytes()
        except OSError:
            self._send_error_json("ไม่สามารถอ่านไฟล์หน้าเว็บได้", 500)
            return
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", f"{content_type}; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format_string: str, *args: object) -> None:
        print(f"[{self.log_date_time_string()}] {format_string % args}")


def main() -> None:
    parser = argparse.ArgumentParser(description="ระบบจัดการข้อมูลสถานการณ์น้ำท่วม")
    parser.add_argument("--host", default="127.0.0.1", help="โฮสต์สำหรับเปิดเว็บ")
    parser.add_argument("--port", type=int, default=8000, help="พอร์ตสำหรับเปิดเว็บ")
    parser.add_argument(
        "--db",
        default=str(BASE_DIR / "flood_data.db"),
        help="ตำแหน่งไฟล์ฐานข้อมูล SQLite",
    )
    args = parser.parse_args()

    initialize_database(args.db)
    with ThreadingHTTPServer((args.host, args.port), lambda *a, **kw: FloodRequestHandler(
        *a, db_path=args.db, **kw
    )) as server:
        print(f"FloodWatch is running at http://{args.host}:{args.port}")
        print(f"Database: {Path(args.db).resolve()}")
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            print("\nFloodWatch stopped.")


if __name__ == "__main__":
    main()