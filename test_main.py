import base64
import json
import sqlite3
import tempfile
import threading
import unittest
from contextlib import closing
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from main import (
    FloodRequestHandler,
    create_record,
    delete_record,
    export_records_csv,
    get_summary,
    initialize_database,
    list_records,
    update_record,
    validate_record,
    _postgres_query,
)


VALID_RECORD = {
    "occurred_at": "2026-10-16",
    "province": "เชียงใหม่",
    "district": "เมืองเชียงใหม่",
    "water_level": "1.25",
    "affected_households": "12",
    "status": "warning",
    "notes": "เฝ้าระวังบริเวณริมน้ำ",
}


class FloodManagementTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test.db"
        initialize_database(self.db_path)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_create_and_summarize_record(self) -> None:
        record_id = create_record(self.db_path, validate_record(VALID_RECORD))

        self.assertEqual(record_id, 1)
        self.assertEqual(
            get_summary(self.db_path),
            {
                "record_count": 1,
                "affected_households": 12,
                "province_count": 1,
                "urgent_count": 1,
                "households_reported_count": 1,
                "affected_people": 0,
                "people_reported_count": 0,
                "evacuated_people": 0,
                "evacuated_reported_count": 0,
                "injured": 0,
                "injured_reported_count": 0,
                "deaths": 0,
                "deaths_reported_count": 0,
            },
        )

    def test_search_and_status_filter(self) -> None:
        create_record(self.db_path, validate_record(VALID_RECORD))
        resolved = {
            **VALID_RECORD,
            "occurred_at": "2026-10-15",
            "province": "ลำปาง",
            "district": "เมืองลำปาง",
            "status": "resolved",
        }
        create_record(self.db_path, validate_record(resolved))

        self.assertEqual(len(list_records(self.db_path, query="เชียงใหม่")), 1)
        self.assertEqual(len(list_records(self.db_path, status="resolved")), 1)
        self.assertEqual(
            len(list_records(self.db_path, date_from="2026-10-16", date_to="2026-10-16")),
            1,
        )

    def test_update_and_delete_record(self) -> None:
        record_id = create_record(self.db_path, validate_record(VALID_RECORD))
        updated = {**VALID_RECORD, "status": "resolved", "affected_households": "15"}

        self.assertTrue(update_record(self.db_path, record_id, validate_record(updated)))
        self.assertEqual(list_records(self.db_path)[0]["status"], "resolved")
        self.assertTrue(delete_record(self.db_path, record_id))
        self.assertFalse(delete_record(self.db_path, record_id))

    def test_reject_invalid_values(self) -> None:
        with self.assertRaisesRegex(ValueError, "วันที่ไม่ถูกต้อง"):
            validate_record({**VALID_RECORD, "occurred_at": "not-a-date"})
        with self.assertRaisesRegex(ValueError, "ระดับน้ำ"):
            validate_record({**VALID_RECORD, "water_level": "-1"})
        with self.assertRaisesRegex(ValueError, "สถานะไม่ถูกต้อง"):
            validate_record({**VALID_RECORD, "status": "bogus"})
        with self.assertRaisesRegex(ValueError, "จำนวนครัวเรือน"):
            validate_record({**VALID_RECORD, "affected_households": 12.5})
        with self.assertRaisesRegex(ValueError, "พิกัดละติจูดและลองจิจูด"):
            validate_record({**VALID_RECORD, "latitude": 18.7})
        with self.assertRaisesRegex(ValueError, "ละติจูด"):
            validate_record({**VALID_RECORD, "latitude": 91, "longitude": 100})
        with self.assertRaisesRegex(ValueError, "จำนวนผู้เสียชีวิต"):
            validate_record({**VALID_RECORD, "deaths": -1})

    def test_postgres_query_parameter_translation(self) -> None:
        self.assertEqual(
            _postgres_query(
                "INSERT INTO flood_records (province, status) "
                "VALUES (:province, :status) WHERE id = ?"
            ),
            "INSERT INTO flood_records (province, status) "
            "VALUES (%(province)s, %(status)s) WHERE id = %s",
        )

    def test_extended_impact_fields_and_csv_formula_protection(self) -> None:
        record = validate_record(
            {
                **VALID_RECORD,
                "reported_at": "2026-10-17",
                "flood_type": "flash",
                "subdistrict": "ช้างคลาน",
                "villages": "หมู่ 1",
                "latitude": "18.778",
                "longitude": "98.999",
                "affected_people": "36",
                "evacuated_people": "12",
                "injured": "1",
                "deaths": "0",
                "notes": "=HYPERLINK(\"https://bad.example\")",
            }
        )
        record_id = create_record(self.db_path, record)
        stored = list_records(self.db_path)[0]

        self.assertEqual(record_id, 1)
        self.assertEqual(stored["flood_type"], "flash")
        self.assertEqual(stored["affected_people"], 36)
        self.assertEqual(stored["evacuated_people"], 12)
        self.assertEqual(stored["latitude"], 18.778)
        self.assertEqual(len(list_records(self.db_path, query="flash")), 1)
        csv_data = export_records_csv([stored]).decode("utf-8-sig")
        self.assertIn("ประชาชนที่ได้รับผลกระทบ", csv_data)
        self.assertIn("'=HYPERLINK", csv_data)

    def test_optional_report_values_and_source_link(self) -> None:
        record = validate_record(
            {
                **VALID_RECORD,
                "water_level": "",
                "affected_households": None,
                "source_url": "https://example.gov/report",
            }
        )
        record_id = create_record(self.db_path, record)
        stored = list_records(self.db_path)[0]

        self.assertEqual(record_id, 1)
        self.assertIsNone(stored["water_level"])
        self.assertIsNone(stored["affected_households"])
        self.assertEqual(stored["source_url"], "https://example.gov/report")
        self.assertEqual(get_summary(self.db_path)["affected_households"], 0)

    def test_migrates_existing_database_to_optional_report_values(self) -> None:
        legacy_path = Path(self.temp_dir.name) / "legacy.db"
        with closing(sqlite3.connect(legacy_path)) as connection:
            with connection:
                connection.execute(
                    """
                    CREATE TABLE flood_records (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        occurred_at TEXT NOT NULL,
                        province TEXT NOT NULL,
                        district TEXT NOT NULL,
                        water_level REAL NOT NULL CHECK (water_level >= 0),
                        affected_households INTEGER NOT NULL
                            CHECK (affected_households >= 0),
                        status TEXT NOT NULL
                            CHECK (status IN ('active', 'warning', 'evacuating', 'resolved')),
                        notes TEXT NOT NULL DEFAULT ''
                    )
                    """
                )
                connection.execute(
                    """
                    INSERT INTO flood_records
                        (occurred_at, province, district, water_level,
                         affected_households, status, notes)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    ("2026-10-16", "เชียงใหม่", "เมืองเชียงใหม่", 1.25, 12, "warning", ""),
                )

        initialize_database(legacy_path)
        self.assertEqual(list_records(legacy_path)[0]["affected_households"], 12)
        missing_values = validate_record({**VALID_RECORD, "water_level": None})
        missing_values["affected_households"] = None
        missing_values["status"] = "unknown"
        self.assertTrue(update_record(legacy_path, 1, missing_values))
        migrated = list_records(legacy_path)[0]
        self.assertIsNone(migrated["water_level"])
        self.assertEqual(migrated["status"], "unknown")

    def test_http_api_create_update_delete_and_static_page(self) -> None:
        authorization = "Basic " + base64.b64encode(
            b"flood-admin:test-password"
        ).decode("ascii")
        server = ThreadingHTTPServer(
            ("127.0.0.1", 0),
            lambda *args, **kwargs: FloodRequestHandler(
                *args,
                db_path=str(self.db_path),
                admin_username="flood-admin",
                admin_password="test-password",
                **kwargs,
            ),
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base_url = f"http://127.0.0.1:{server.server_port}"
        try:
            with urlopen(base_url) as response:
                self.assertEqual(response.status, 200)
                self.assertIn("FloodWatch", response.read().decode("utf-8"))

            with urlopen(f"{base_url}/api/records") as response:
                self.assertEqual(json.loads(response.read()), [])
            with self.assertRaises(HTTPError) as error:
                urlopen(f"{base_url}/api/auth/check")
            self.assertEqual(error.exception.code, 401)
            error.exception.close()

            unauthorized_create = Request(
                f"{base_url}/api/records",
                data=json.dumps(VALID_RECORD).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with self.assertRaises(HTTPError) as error:
                urlopen(unauthorized_create)
            self.assertEqual(error.exception.code, 401)
            error.exception.close()

            with urlopen(
                Request(
                    f"{base_url}/api/auth/check",
                    headers={"Authorization": authorization},
                )
            ) as response:
                self.assertTrue(json.loads(response.read())["success"])

            request = Request(
                f"{base_url}/api/records",
                data=json.dumps(VALID_RECORD).encode("utf-8"),
                headers={
                    "Content-Type": "application/json",
                    "Authorization": authorization,
                },
                method="POST",
            )
            with urlopen(request) as response:
                record_id = json.loads(response.read())["id"]
                self.assertEqual(response.status, 201)

            unauthorized_update = Request(
                f"{base_url}/api/records/{record_id}",
                data=json.dumps({**VALID_RECORD, "status": "resolved"}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="PUT",
            )
            with self.assertRaises(HTTPError) as error:
                urlopen(unauthorized_update)
            self.assertEqual(error.exception.code, 401)
            error.exception.close()

            update = Request(
                f"{base_url}/api/records/{record_id}",
                data=json.dumps({**VALID_RECORD, "status": "resolved"}).encode("utf-8"),
                headers={
                    "Content-Type": "application/json",
                    "Authorization": authorization,
                },
                method="PUT",
            )
            with urlopen(update) as response:
                self.assertTrue(json.loads(response.read())["success"])

            with urlopen(f"{base_url}/api/records") as response:
                self.assertEqual(json.loads(response.read())[0]["status"], "resolved")
            with urlopen(f"{base_url}/api/summary") as response:
                self.assertEqual(json.loads(response.read())["record_count"], 1)
            with urlopen(
                f"{base_url}/api/records?from=2026-10-16&to=2026-10-16"
            ) as response:
                self.assertEqual(len(json.loads(response.read())), 1)
            with urlopen(f"{base_url}/api/export.csv") as response:
                self.assertEqual(response.headers.get_content_type(), "text/csv")
                self.assertTrue(response.read().startswith(b"\xef\xbb\xbf"))

            unauthorized_delete = Request(
                f"{base_url}/api/records/{record_id}",
                method="DELETE",
            )
            with self.assertRaises(HTTPError) as error:
                urlopen(unauthorized_delete)
            self.assertEqual(error.exception.code, 401)
            error.exception.close()

            delete = Request(
                f"{base_url}/api/records/{record_id}",
                headers={"Authorization": authorization},
                method="DELETE",
            )
            with urlopen(delete) as response:
                self.assertTrue(json.loads(response.read())["success"])

            invalid = Request(
                f"{base_url}/api/records",
                data=b'{"status":"invalid"}',
                headers={
                    "Content-Type": "application/json",
                    "Authorization": authorization,
                },
                method="POST",
            )
            with self.assertRaises(HTTPError) as error:
                urlopen(invalid)
            self.assertEqual(error.exception.code, 400)
            error.exception.close()
        finally:
            server.shutdown()
            thread.join()
            server.server_close()


if __name__ == "__main__":
    unittest.main()
