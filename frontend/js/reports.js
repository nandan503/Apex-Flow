/* Analytics Reports Module */

let currentReportData = [];

async function generateReport(type = 'shipments') {
  try {
    const res = await fetchAPI(`/reports/${type}`);
    if (res.success && res.data) {
      currentReportData = res.data;
      renderReportTable(currentReportData);
      showToast(`Report '${type.toUpperCase()}' generated successfully`, 'success');
    }
  } catch (e) {}
}

function renderReportTable(data) {
  const table = document.getElementById('reportOutputTable');
  if (!table) return;

  if (!data || data.length === 0) {
    table.innerHTML = `<thead><tr><th>Data</th></tr></thead><tbody><tr><td>No data available for this report type</td></tr></tbody>`;
    return;
  }

  const keys = Object.keys(data[0]);
  const theadHtml = `<thead><tr>${keys.map(k => `<th>${k.replace('_', ' ').toUpperCase()}</th>`).join('')}</tr></thead>`;
  const tbodyHtml = `<tbody>${data.map(row => `<tr>${keys.map(k => `<td>${row[k] !== null ? row[k] : ''}</td>`).join('')}</tr>`).join('')}</tbody>`;

  table.innerHTML = theadHtml + tbodyHtml;
}

function exportReportJSON() {
  if (currentReportData.length === 0) {
    showToast('No report data to export', 'warning');
    return;
  }
  const dataStr = "data:text/json;charset=utf-8," + encodeURIComponent(JSON.stringify(currentReportData, null, 2));
  const downloadAnchor = document.createElement('a');
  downloadAnchor.setAttribute("href", dataStr);
  downloadAnchor.setAttribute("download", `apexflow_report_${Date.now()}.json`);
  document.body.appendChild(downloadAnchor);
  downloadAnchor.click();
  downloadAnchor.remove();
}

function exportReportCSV() {
  if (currentReportData.length === 0) {
    showToast('No report data to export', 'warning');
    return;
  }
  const keys = Object.keys(currentReportData[0]);
  let csv = keys.join(',') + '\n';
  currentReportData.forEach(row => {
    csv += keys.map(k => `"${row[k] || ''}"`).join(',') + '\n';
  });

  const blob = new Blob([csv], { type: 'text/csv' });
  const url = window.URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.setAttribute('href', url);
  a.setAttribute('download', `apexflow_report_${Date.now()}.csv`);
  document.body.appendChild(a);
  a.click();
  a.remove();
}

document.addEventListener('DOMContentLoaded', () => {
  if (document.getElementById('reportOutputTable')) {
    generateReport('shipments');
  }
});
