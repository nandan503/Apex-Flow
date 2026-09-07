/* Warehouse Management JS Module */

async function loadWarehouses() {
  try {
    const res = await fetchAPI('/warehouses');
    if (res.success && res.data) {
      renderWarehouses(res.data);
    }
  } catch (err) {}
}

function renderWarehouses(warehouses) {
  const container = document.getElementById('warehousesGrid');
  if (!container) return;

  container.innerHTML = warehouses.map(w => {
    const occupancyPercent = Math.min(Math.round((w.current_occupancy_tons / w.capacity_tons) * 100), 100);
    const progressColor = occupancyPercent > 90 ? 'var(--danger)' : (occupancyPercent > 70 ? 'var(--warning)' : 'var(--success)');

    return `
      <div class="card" style="padding: 20px;">
        <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:12px;">
          <h3 style="font-size:16px; color:var(--primary-navy);">${escapeHtml(w.name)}</h3>
          <span class="badge badge-${statusClass(w.status)}">${escapeHtml(w.status)}</span>
        </div>
        <p style="font-size:13px; color:var(--text-muted); margin-bottom:16px;">📍 ${escapeHtml(w.location)} | Manager: <strong>${escapeHtml(w.manager_name)}</strong> (${escapeHtml(w.contact_phone)})</p>
        <div style="margin-bottom:8px; display:flex; justify-content:space-between; font-size:13px;">
          <span>Occupancy: <strong>${escapeHtml(w.current_occupancy_tons)} / ${escapeHtml(w.capacity_tons)} Tons</strong></span>
          <strong>${occupancyPercent}%</strong>
        </div>
        <div style="width:100%; height:10px; background:#e2e8f0; border-radius:5px; overflow:hidden;">
          <div style="width:${occupancyPercent}%; height:100%; background:${progressColor}; border-radius:5px;"></div>
        </div>
      </div>
    `;
  }).join('');
}

document.addEventListener('DOMContentLoaded', () => {
  if (document.getElementById('warehousesGrid')) {
    loadWarehouses();
  }
});
