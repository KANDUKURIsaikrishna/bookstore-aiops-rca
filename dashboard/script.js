const listEl = document.getElementById("report-list");
const detailEl = document.getElementById("report-detail");
const detailTitleEl = document.getElementById("detail-title");
const detailNarrativeEl = document.getElementById("detail-narrative");
document.getElementById("back-button").addEventListener("click", showList);

async function loadReports() {
  const res = await fetch(`${window.READ_API_URL}/reports`);
  const { reports } = await res.json();
  listEl.innerHTML = "";
  if (reports.length === 0) {
    listEl.textContent = "No RCA reports yet.";
    return;
  }
  for (const report of reports) {
    const row = document.createElement("div");
    row.className = "report-row";
    row.innerHTML = `
      <span class="severity-${report.severity}">${report.alertname}</span>
      — ${report.service} — ${report.created_at}
    `;
    row.addEventListener("click", () => showDetail(report.alert_id, report.report_timestamp));
    listEl.appendChild(row);
  }
}

async function showDetail(alertId, reportTimestamp) {
  const res = await fetch(`${window.READ_API_URL}/reports/${encodeURIComponent(alertId)}/${encodeURIComponent(reportTimestamp)}`);
  const report = await res.json();
  detailTitleEl.textContent = `${report.alertname} — ${report.service}`;
  detailNarrativeEl.textContent = report.narrative;
  listEl.hidden = true;
  detailEl.hidden = false;
}

function showList() {
  detailEl.hidden = true;
  listEl.hidden = false;
}

loadReports();
