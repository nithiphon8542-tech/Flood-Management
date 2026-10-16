const statusLabels = {
  active: "กำลังเกิดเหตุ",
  warning: "เฝ้าระวัง",
  evacuating: "อพยพ",
  resolved: "คลี่คลายแล้ว",
  unknown: "ไม่ระบุสถานะ",
};
const floodTypeLabels = {
  river: "น้ำล้นตลิ่ง/น้ำแม่น้ำ",
  flash: "น้ำป่าไหลหลาก/น้ำท่วมฉับพลัน",
  urban: "น้ำท่วมขังในชุมชน",
  coastal: "น้ำทะเลหนุน/น้ำท่วมชายฝั่ง",
  other: "อื่น ๆ",
  unknown: "ไม่ระบุประเภท",
};

const recordsBody = document.querySelector("#records-body");
const dialog = document.querySelector("#record-dialog");
const form = document.querySelector("#record-form");
const loginDialog = document.querySelector("#login-dialog");
const loginForm = document.querySelector("#login-form");
const loginError = document.querySelector("#login-error");
const authButton = document.querySelector("#auth-button");
const addButton = document.querySelector("#add-button");
const notice = document.querySelector("#notice");
const mapStatus = document.querySelector("#map-status");
let adminAuthorization = "";
const districtCoordinates = {
  "เชียงใหม่|เมืองเชียงใหม่": [18.7904, 98.9847],
  "เชียงใหม่|แม่วาง": [18.675, 98.774],
  "เชียงใหม่|อมก๋อย": [17.798, 98.362],
  "เชียงใหม่|แม่แจ่ม": [18.499, 98.365],
  "เชียงใหม่|กัลยาณิวัฒนา": [18.941, 98.257],
  "เชียงใหม่|ฝาง": [19.916, 99.215],
  "เชียงใหม่|ไชยปราการ": [19.731, 99.149],
  "เชียงใหม่|ไชยปการ": [19.731, 99.149],
  "เชียงใหม่|เชียงดาว": [19.366, 98.964],
  "เชียงใหม่|พร้าว": [19.364, 99.202],
  "เชียงใหม่|สะเมิง": [18.848, 98.732],
  "เชียงใหม่|แม่แตง": [19.117, 98.941],
  "เชียงใหม่|สันทราย": [18.847, 99.044],
  "เชียงราย|เทิง": [19.674, 100.195],
  "พิจิตร|โพทะเล": [16.091, 100.262],
  "นครราชสีมา|เมืองนครราชสีมา": [14.9799, 102.0978],
};
const provinceCoordinates = {
  "เชียงใหม่": [18.7904, 98.9847],
  "เชียงราย": [19.9072, 99.8309],
  "พิจิตร": [16.4429, 100.3488],
  "นครราชสีมา": [14.9799, 102.0978],
};
let floodMap;
let mapMarkers;
let searchTimer;

async function request(url, options = {}) {
  const headers = { "Content-Type": "application/json", ...options.headers };
  if (adminAuthorization) headers.Authorization = adminAuthorization;
  const response = await fetch(url, {
    ...options,
    headers,
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || "เกิดข้อผิดพลาด กรุณาลองอีกครั้ง");
  return data;
}

function addCell(row, text, className = "") {
  const cell = document.createElement("td");
  cell.textContent = text;
  if (className) cell.className = className;
  row.append(cell);
  return cell;
}

function formatCount(value) {
  return value == null ? "ไม่ระบุ" : Number(value).toLocaleString("th-TH");
}

function addOptionalNumberLine(container, label, value) {
  const line = document.createElement("div");
  line.textContent = `${label}: ${formatCount(value)}`;
  container.append(line);
}

function renderRecords(records) {
  recordsBody.replaceChildren();
  renderMap(records);
  if (records.length === 0) {
    const row = document.createElement("tr");
    const cell = document.createElement("td");
    cell.colSpan = 8;
    cell.className = "empty-cell";
    cell.textContent = "ยังไม่มีข้อมูล ลองเพิ่มรายการสถานการณ์น้ำท่วม";
    row.append(cell);
    recordsBody.append(row);
    return;
  }

  for (const record of records) {
    const row = document.createElement("tr");
    addCell(row, new Date(`${record.occurred_at}T00:00:00`).toLocaleDateString("th-TH", {
      day: "numeric", month: "short", year: "numeric",
    }));
    addCell(row, floodTypeLabels[record.flood_type] || floodTypeLabels.unknown);

    const location = document.createElement("td");
    const province = document.createElement("span");
    province.className = "location-name";
    province.textContent = record.province;
    const district = document.createElement("span");
    district.className = "location-district";
    district.textContent = record.district;
    location.append(province, district);
    const areaDetails = [record.subdistrict, record.villages].filter(Boolean).join(" · ");
    if (areaDetails) {
      const areaDetail = document.createElement("small");
      areaDetail.className = "location-details";
      areaDetail.textContent = areaDetails;
      location.append(document.createElement("br"), areaDetail);
    }
    row.append(location);

    addCell(
      row,
      record.water_level == null
        ? "ไม่ระบุ"
        : `${Number(record.water_level).toLocaleString("th-TH")} ม.`,
      "water-level",
    );
    const peopleCell = document.createElement("td");
    const households = document.createElement("div");
    households.textContent = `ครัวเรือน ${formatCount(record.affected_households)}`;
    const affectedPeople = document.createElement("small");
    affectedPeople.className = "location-details";
    affectedPeople.textContent = `คน ${formatCount(record.affected_people)}`;
    peopleCell.append(households, affectedPeople);
    row.append(peopleCell);
    const statusCell = document.createElement("td");
    const badge = document.createElement("span");
    badge.className = `status-badge status-${record.status}`;
    badge.textContent = statusLabels[record.status] || record.status;
    statusCell.append(badge);
    row.append(statusCell);
    const notesCell = document.createElement("td");
    notesCell.className = "record-details";
    const impact = document.createElement("small");
    impact.textContent = `อพยพ ${formatCount(record.evacuated_people)} · บาดเจ็บ ${formatCount(record.injured)} · เสียชีวิต ${formatCount(record.deaths)}`;
    notesCell.append(impact);
    if (record.notes) {
      const note = document.createElement("div");
      note.textContent = record.notes;
      notesCell.append(note);
    }
    if (record.reported_at) {
      const reported = document.createElement("small");
      reported.className = "location-details";
      reported.textContent = `รายงาน ${new Date(`${record.reported_at}T00:00:00`).toLocaleDateString("th-TH")}`;
      notesCell.append(reported);
    }
    if (record.source_url) {
      const sourceContainer = document.createElement("div");
      const sourceLink = document.createElement("a");
      sourceLink.href = record.source_url;
      sourceLink.target = "_blank";
      sourceLink.rel = "noopener noreferrer";
      sourceLink.textContent = "แหล่งข้อมูล";
      sourceContainer.append(sourceLink);
      notesCell.append(sourceContainer);
    }
    row.append(notesCell);

    const actionsCell = document.createElement("td");
    if (adminAuthorization) {
      const actions = document.createElement("div");
      actions.className = "row-actions";
      const editButton = document.createElement("button");
      editButton.type = "button";
      editButton.dataset.action = "edit";
      editButton.dataset.id = record.id;
      editButton.textContent = "แก้ไข";
      const deleteButton = document.createElement("button");
      deleteButton.type = "button";
      deleteButton.className = "delete-button";
      deleteButton.dataset.action = "delete";
      deleteButton.dataset.id = record.id;
      deleteButton.textContent = "ลบ";
      actions.append(editButton, deleteButton);
      actionsCell.append(actions);
    }
    row.append(actionsCell);
    recordsBody.append(row);
  }
}

function getRecordCoordinates(record) {
  const key = `${record.province}|${record.district}`;
  if (record.latitude != null && record.longitude != null) {
    return {
      coordinates: [Number(record.latitude), Number(record.longitude)],
      approximate: false,
    };
  }
  return {
    coordinates: districtCoordinates[key] || provinceCoordinates[record.province] || null,
    approximate: true,
  };
}

function createPopupContent(group) {
  const container = document.createElement("div");
  container.className = "flood-map-popup";
  const heading = document.createElement("strong");
  heading.textContent = `${group.records[0].province} · ${group.records[0].district}`;
  container.append(heading);

  for (const record of group.records) {
    const item = document.createElement("div");
    item.className = "flood-map-popup-item";
    const date = document.createElement("div");
    date.textContent = new Date(`${record.occurred_at}T00:00:00`).toLocaleDateString("th-TH", {
      day: "numeric", month: "short", year: "numeric",
    });
    const status = document.createElement("div");
    status.textContent = statusLabels[record.status] || record.status;
    const reportDate = document.createElement("div");
    reportDate.textContent = record.reported_at
      ? `รายงาน ${new Date(`${record.reported_at}T00:00:00`).toLocaleDateString("th-TH")}`
      : `พิกัด${group.approximate ? "โดยประมาณ" : "ที่บันทึก"}`;
    const floodType = document.createElement("div");
    floodType.textContent = floodTypeLabels[record.flood_type] || floodTypeLabels.unknown;
    item.append(date, reportDate, floodType, status);
    addOptionalNumberLine(item, "ครัวเรือน", record.affected_households);
    addOptionalNumberLine(item, "ผู้ได้รับผลกระทบ", record.affected_people);
    addOptionalNumberLine(item, "อพยพ", record.evacuated_people);
    addOptionalNumberLine(item, "บาดเจ็บ", record.injured);
    addOptionalNumberLine(item, "เสียชีวิต", record.deaths);
    if (record.notes) {
      const note = document.createElement("div");
      note.textContent = record.notes;
      item.append(note);
    }
    if (record.source_url) {
      const source = document.createElement("a");
      source.href = record.source_url;
      source.target = "_blank";
      source.rel = "noopener noreferrer";
      source.textContent = "เปิดแหล่งข้อมูล";
      item.append(source);
    }
    container.append(item);
  }
  return container;
}

function renderMap(records) {
  if (!floodMap || !mapMarkers) return;
  mapMarkers.clearLayers();
  const groups = new Map();
  let skipped = 0;

  for (const record of records) {
    const { coordinates, approximate } = getRecordCoordinates(record);
    if (!coordinates) {
      skipped += 1;
      continue;
    }
    const key = coordinates.join(",");
    if (!groups.has(key)) {
      groups.set(key, { coordinates, records: [], approximate });
    }
    groups.get(key).records.push(record);
  }

  for (const group of groups.values()) {
    const statuses = new Set(group.records.map((record) => record.status));
    const markerStatus = statuses.has("evacuating")
      ? "evacuating"
      : statuses.has("warning")
        ? "warning"
        : statuses.has("active")
          ? "active"
          : statuses.has("resolved")
            ? "resolved"
            : "unknown";
    const marker = L.circleMarker(group.coordinates, {
      radius: Math.min(9 + group.records.length, 16),
      color: "#ffffff",
      weight: 3,
      fillColor: {
        active: "#d38a2d",
        warning: "#cf5c4c",
        evacuating: "#ad3e43",
        resolved: "#26816f",
        unknown: "#71828c",
      }[markerStatus],
      fillOpacity: 0.95,
    });
    marker.bindPopup(createPopupContent(group));
    marker.addTo(mapMarkers);
  }

  const message = skipped
    ? `มี ${skipped} รายการที่ยังไม่มีพิกัดจังหวัด จึงไม่แสดงบนแผนที่`
    : "";
  mapStatus.textContent = message;
  mapStatus.hidden = !message;

  const bounds = L.latLngBounds(Array.from(groups.values(), (group) => group.coordinates));
  if (bounds.isValid()) {
    floodMap.fitBounds(bounds.pad(0.2), { maxZoom: 9, animate: false });
  } else {
    floodMap.setView([15.5, 101], 5);
  }
}

function initializeMap() {
  if (typeof L === "undefined") {
    mapStatus.textContent = "โหลดแผนที่ไม่สำเร็จ กรุณาตรวจสอบการเชื่อมต่ออินเทอร์เน็ตแล้วรีเฟรชหน้า";
    return;
  }
  floodMap = L.map("flood-map", { scrollWheelZoom: false }).setView([15.5, 101], 5);
  L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 18,
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
  }).addTo(floodMap);
  mapMarkers = L.layerGroup().addTo(floodMap);
  mapStatus.textContent = "";
  mapStatus.hidden = true;
  window.setTimeout(() => floodMap.invalidateSize(), 0);
}

async function loadRecords() {
  const params = new URLSearchParams();
  const query = document.querySelector("#search-input").value.trim();
  const status = document.querySelector("#status-filter").value;
  const dateFrom = document.querySelector("#date-from").value;
  const dateTo = document.querySelector("#date-to").value;
  if (query) params.set("q", query);
  if (status) params.set("status", status);
  if (dateFrom) params.set("from", dateFrom);
  if (dateTo) params.set("to", dateTo);
  const suffix = params.size ? `?${params}` : "";
  document.querySelector("#export-button").href = `/api/export.csv${suffix}`;
  renderRecords(await request(`/api/records${suffix}`));
}

async function loadSummary() {
  const summary = await request("/api/summary");
  const reportedCount = (value, count) => (
    Number(count) > 0
      ? Number(value).toLocaleString("th-TH")
      : "ไม่ระบุ"
  );
  document.querySelector("#summary-records").textContent = Number(summary.record_count).toLocaleString("th-TH");
  document.querySelector("#summary-households").textContent =
    reportedCount(summary.affected_households, summary.households_reported_count);
  document.querySelector("#summary-urgent").textContent = Number(summary.urgent_count).toLocaleString("th-TH");
  document.querySelector("#summary-provinces").textContent = Number(summary.province_count).toLocaleString("th-TH");
  document.querySelector("#summary-people").textContent =
    reportedCount(summary.affected_people, summary.people_reported_count);
  document.querySelector("#summary-casualties").textContent = [
    reportedCount(summary.evacuated_people, summary.evacuated_reported_count),
    reportedCount(summary.injured, summary.injured_reported_count),
    reportedCount(summary.deaths, summary.deaths_reported_count),
  ].join(" / ");
  document.querySelector("#summary-casualty-detail").textContent =
    "อพยพ / บาดเจ็บ / เสียชีวิต · รวมตามวันที่รายงาน";
}

function showNotice(message) {
  notice.textContent = message;
  notice.hidden = !message;
}

function updateAdminControls() {
  addButton.hidden = !adminAuthorization;
  authButton.textContent = adminAuthorization
    ? "ออกจากระบบผู้ดูแล"
    : "เข้าสู่ระบบผู้ดูแล";
}

function showFormError(message) {
  const error = document.querySelector("#form-error");
  error.textContent = message;
  error.hidden = !message;
}

function resetForm() {
  form.reset();
  document.querySelector("#record-id").value = "";
  document.querySelector("#dialog-title").textContent = "เพิ่มข้อมูลน้ำท่วม";
  document.querySelector("#save-button").textContent = "บันทึกข้อมูล";
  const today = new Date();
  document.querySelector("#occurred-at").value = [
    today.getFullYear(),
    String(today.getMonth() + 1).padStart(2, "0"),
    String(today.getDate()).padStart(2, "0"),
  ].join("-");
  showFormError("");
}

function openCreateDialog() {
  resetForm();
  dialog.showModal();
  document.querySelector("#province").focus();
}

async function openEditDialog(recordId) {
  try {
    const records = await request("/api/records");
    const record = records.find((item) => String(item.id) === recordId);
    if (!record) throw new Error("ไม่พบรายการที่ต้องการแก้ไข");
    resetForm();
    document.querySelector("#record-id").value = record.id;
    document.querySelector("#occurred-at").value = record.occurred_at;
    document.querySelector("#reported-at").value = record.reported_at || "";
    document.querySelector("#flood-type").value = record.flood_type || "unknown";
    document.querySelector("#province").value = record.province;
    document.querySelector("#district").value = record.district;
    document.querySelector("#subdistrict").value = record.subdistrict || "";
    document.querySelector("#villages").value = record.villages || "";
    document.querySelector("#latitude").value = record.latitude ?? "";
    document.querySelector("#longitude").value = record.longitude ?? "";
    document.querySelector("#water-level").value = record.water_level;
    document.querySelector("#households").value = record.affected_households;
    document.querySelector("#affected-people").value = record.affected_people ?? "";
    document.querySelector("#evacuated-people").value = record.evacuated_people ?? "";
    document.querySelector("#injured").value = record.injured ?? "";
    document.querySelector("#deaths").value = record.deaths ?? "";
    document.querySelector("#record-status").value = record.status;
    document.querySelector("#notes").value = record.notes;
    document.querySelector("#source-url").value = record.source_url;
    document.querySelector("#dialog-title").textContent = "แก้ไขข้อมูลน้ำท่วม";
    document.querySelector("#save-button").textContent = "บันทึกการแก้ไข";
    dialog.showModal();
  } catch (error) {
    showNotice(error.message);
  }
}

authButton.addEventListener("click", () => {
  if (adminAuthorization) {
    adminAuthorization = "";
    updateAdminControls();
    loadRecords().catch((error) => showNotice(error.message));
    return;
  }
  loginForm.reset();
  loginError.hidden = true;
  loginDialog.showModal();
  document.querySelector("#admin-username").focus();
});
document.querySelector("#close-login").addEventListener("click", () => loginDialog.close());
document.querySelector("#cancel-login").addEventListener("click", () => loginDialog.close());
loginForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const submitButton = document.querySelector("#submit-login");
  const username = document.querySelector("#admin-username").value;
  const password = document.querySelector("#admin-password").value;
  const bytes = new TextEncoder().encode(`${username}:${password}`);
  const encoded = btoa(Array.from(bytes, (byte) => String.fromCharCode(byte)).join(""));
  submitButton.disabled = true;
  loginError.hidden = true;
  try {
    await request("/api/auth/check", {
      headers: { Authorization: `Basic ${encoded}` },
    });
  } catch (error) {
    loginError.textContent = error.message;
    loginError.hidden = false;
    return;
  } finally {
    submitButton.disabled = false;
  }
  adminAuthorization = `Basic ${encoded}`;
  loginDialog.close();
  loginForm.reset();
  updateAdminControls();
  loadRecords().catch((error) => showNotice(error.message));
});
addButton.addEventListener("click", openCreateDialog);
document.querySelector("#close-dialog").addEventListener("click", () => dialog.close());
document.querySelector("#cancel-dialog").addEventListener("click", () => dialog.close());
document.querySelector("#search-input").addEventListener("input", () => {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(() => loadRecords().catch((error) => showNotice(error.message)), 250);
});
document.querySelector("#status-filter").addEventListener("change", () => {
  loadRecords().catch((error) => showNotice(error.message));
});
for (const selector of ["#date-from", "#date-to"]) {
  document.querySelector(selector).addEventListener("change", () => {
    const dateFrom = document.querySelector("#date-from").value;
    const dateTo = document.querySelector("#date-to").value;
    if (dateFrom && dateTo && dateFrom > dateTo) {
      showNotice("วันที่เริ่มต้นต้องไม่อยู่หลังวันที่สิ้นสุด");
      return;
    }
    showNotice("");
    loadRecords().catch((error) => showNotice(error.message));
  });
}

recordsBody.addEventListener("click", async (event) => {
  const button = event.target.closest("button[data-action]");
  if (!button) return;
  if (button.dataset.action === "edit") {
    await openEditDialog(button.dataset.id);
    return;
  }
  if (button.dataset.action === "delete") {
    if (!window.confirm("ลบรายการนี้หรือไม่? การลบไม่สามารถย้อนกลับได้")) return;
    try {
      await request(`/api/records/${button.dataset.id}`, { method: "DELETE" });
      showNotice("");
      await Promise.all([loadRecords(), loadSummary()]);
    } catch (error) {
      showNotice(error.message);
    }
  }
});

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const recordId = document.querySelector("#record-id").value;
  const payload = Object.fromEntries(new FormData(form));
  for (const field of [
    "water_level",
    "affected_households",
    "affected_people",
    "evacuated_people",
    "injured",
    "deaths",
    "latitude",
    "longitude",
  ]) {
    payload[field] = payload[field] === "" ? null : Number(payload[field]);
  }
  const saveButton = document.querySelector("#save-button");
  saveButton.disabled = true;
  showFormError("");
  try {
    await request(recordId ? `/api/records/${recordId}` : "/api/records", {
      method: recordId ? "PUT" : "POST",
      body: JSON.stringify(payload),
    });
    dialog.close();
    showNotice("");
    await Promise.all([loadRecords(), loadSummary()]);
  } catch (error) {
    showFormError(error.message);
  } finally {
    saveButton.disabled = false;
  }
});

updateAdminControls();
Promise.all([loadRecords(), loadSummary()]).catch((error) => {
  showNotice(error.message);
  recordsBody.replaceChildren();
  const row = document.createElement("tr");
  const cell = document.createElement("td");
  cell.colSpan = 8;
  cell.className = "empty-cell";
  cell.textContent = "โหลดข้อมูลไม่สำเร็จ กรุณารีเฟรชหน้าเว็บ";
  row.append(cell);
  recordsBody.append(row);
});

initializeMap();
