/* Dashboard JS Module */

async function loadDashboardData() {
  try {
    const res = await fetchAPI('/reports/dashboard');
    if (res.success && res.data) {
      const data = res.data;

      // Populate KPI numbers
      document.getElementById('kpiTotalShipments').innerText = data.total_shipments || 0;
      document.getElementById('kpiInTransit').innerText = data.in_transit || 0;
      document.getElementById('kpiDelivered').innerText = data.delivered || 0;
      document.getElementById('kpiDelayed').innerText = data.delayed || 0;

      document.getElementById('kpiAvailVehicles').innerText = `${data.available_vehicles}/${data.total_vehicles}`;
      document.getElementById('kpiActiveDrivers').innerText = `${data.active_drivers}/${data.total_drivers}`;
      document.getElementById('kpiRevenue').innerText = `₹${(data.total_revenue || 0).toLocaleString()}`;
      document.getElementById('kpiFuelCost').innerText = `₹${(data.estimated_fuel_cost || 0).toLocaleString()}`;

      // Populate Recent Shipments Table
      renderRecentShipments(data.recent_shipments || []);

      // Populate Recent Vehicles Table
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
      <td><strong>${s.shipment_id}</strong></td>
      <td>${s.pickup_location} → ${s.destination}</td>
      <td><span class="badge badge-${s.status.toLowerCase().replace(/\s+/g, '-')}">${s.status}</span></td>
      <td>${s.expected_delivery}</td>
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
      <td><strong>${v.registration_number}</strong></td>
      <td>${v.vehicle_type}</td>
      <td>${v.capacity_mt} MT</td>
      <td><span class="badge badge-${v.status.toLowerCase().replace(/\s+/g, '-')}">${v.status}</span></td>
    </tr>
  `).join('');
}

function trackLiveDemo() {
  showToast("Live GPS tracking started for Vehicle HR26BX4587 (Delhi → Jaipur). ETA: 3h 20m", "info");
}

document.addEventListener('DOMContentLoaded', () => {
  if (document.getElementById('kpiTotalShipments')) {
    loadDashboardData();
  }
});
