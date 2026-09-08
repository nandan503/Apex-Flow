/* Analytics Reports Module */

let currentReportData = [];

async function generateReport(type = 'shipments') {
  try {
    const res = await fetchAPI(`/reports/${encodeURIComponent(type)}`);
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

  table.textContent = '';
  if (!data || data.length === 0) {
    table.innerHTML = `<thead><tr><th>Data</th></tr></thead><tbody><tr><td>No data available for this report type</td></tr></tbody>`;
    return;
  }

  const keys = Object.keys(data[0]);
  const thead = document.createElement('thead');
  const hr = document.createElement('tr');
  keys.forEach(k => {
    const th = document.createElement('th');
    th.textContent = k.replace('_', ' ').toUpperCase();
    hr.appendChild(th);
  });
  thead.appendChild(hr);

  const tbody = document.createElement('tbody');
  data.forEach(row => {
    const tr = document.createElement('tr');
    keys.forEach(k => {
      const td = document.createElement('td');
      td.textContent = row[k] !== null && row[k] !== undefined ? row[k] : '';
      tr.appendChild(td);
    });
    tbody.appendChild(tr);
  });

  table.appendChild(thead);
  table.appendChild(tbody);
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

function csvCell(value) {
  let text = String(value ?? '');
  if (/^[\s]*[=+@-]/.test(text) || /^[\t\r\n]/.test(text)) text = "'" + text;
  return `"${text.replace(/"/g, '""')}"`;
}

function exportReportCSV() {
  if (currentReportData.length === 0) {
    showToast('No report data to export', 'warning');
    return;
  }
  const keys = Object.keys(currentReportData[0]);
  let csv = keys.join(',') + '\n';
  currentReportData.forEach(row => {
    csv += keys.map(k => csvCell(row[k])).join(',') + '\n';
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
