/* Dashboard JS Module */

async function loadDashboardData() {
  try {
    const res = await fetchAPI('/reports/dashboard');
    if (res.success && res.data) {
      const data = res.data;

      const setText = (id, value) => {
        const el = document.getElementById(id);
        if (el) el.textContent = value;
      };

      setText('kpiTotalShipments', data.total_shipments || 0);
      setText('kpiInTransit', data.in_transit || 0);
      setText('kpiDelivered', data.delivered || 0);
      setText('kpiDelayed', data.delayed || 0);
      setText('kpiAvailVehicles', `${data.available_vehicles}/${data.total_vehicles}`);
      setText('kpiActiveDrivers', `${data.active_drivers}/${data.total_drivers}`);
      setText('kpiRevenue', `₹${Number(data.total_revenue || 0).toLocaleString()}`);
      setText('kpiFuelCost', `₹${(data.estimated_fuel_cost || 0).toLocaleString()}`);

      renderRecentShipments(data.recent_shipments || []);
      renderRecentVehicles(data.recent_vehicles || []);
    }
  } catch (err) {
    console.error('Failed to load dashboard data:', err);
  }
}

function renderRecentShipments(shipments) {
  const tbody = document.getElementById('recentShipmentsTableBody');
  if (!tbody) return;

  if (shipments.length === 0) {
    tbody.innerHTML = `<tr><td colspan="4" style="text-align:center;">No recent shipments</td></tr>`;
    return;
  }

  tbody.innerHTML = shipments.map(s => `
    <tr>
      <td><strong>${escapeHtml(s.shipment_id)}</strong></td>
      <td>${escapeHtml(s.pickup_location)} → ${escapeHtml(s.destination)}</td>
      <td><span class="badge badge-${statusClass(s.status)}">${escapeHtml(s.status)}</span></td>
      <td>${escapeHtml(s.expected_delivery)}</td>
    </tr>
  `).join('');
}

function renderRecentVehicles(vehicles) {
  const tbody = document.getElementById('recentVehiclesTableBody');
  if (!tbody) return;

  if (vehicles.length === 0) {
    tbody.innerHTML = `<tr><td colspan="4" style="text-align:center;">No recent vehicles</td></tr>`;
    return;
  }

  tbody.innerHTML = vehicles.map(v => `
    <tr>
      <td><strong>${escapeHtml(v.registration_number)}</strong></td>
      <td>${escapeHtml(v.vehicle_type)}</td>
      <td>${escapeHtml(v.capacity_mt)} MT</td>
      <td><span class="badge badge-${statusClass(v.status)}">${escapeHtml(v.status)}</span></td>
    </tr>
  `).join('');
}

function trackLiveDemo() {
  showToast("Open Live Tracking for assigned vehicles.", "info");
}

document.addEventListener('DOMContentLoaded', () => {
  if (document.getElementById('kpiTotalShipments')) {
    loadDashboardData();
  }
});
