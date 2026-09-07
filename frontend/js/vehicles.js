/* Vehicles Management JS Module */

async function loadVehicles() {
  try {
    const res = await fetchAPI('/vehicles');
    if (res.success && res.data) {
      renderVehiclesTable(res.data);
    }
  } catch (err) {
    console.error('Failed to load vehicles:', err);
  }
}

function renderVehiclesTable(vehicles) {
  const tbody = document.getElementById('vehiclesTableBody');
  if (!tbody) return;

  if (!vehicles || vehicles.length === 0) {
    tbody.innerHTML = `<tr><td colspan="10" style="text-align:center; padding: 20px;">No vehicles found</td></tr>`;
    return;
  }

  tbody.innerHTML = vehicles.map(v => `
    <tr>
      <td><strong>${escapeHtml(v.vehicle_id)}</strong></td>
      <td><strong>${escapeHtml(v.registration_number)}</strong></td>
      <td>${escapeHtml(v.vehicle_type)}</td>
      <td>${escapeHtml(v.make)} ${escapeHtml(v.model)} (${escapeHtml(v.year)})</td>
      <td>${escapeHtml(v.capacity_mt)} MT</td>
      <td>${escapeHtml(v.fuel_type)}</td>
      <td>📍 ${escapeHtml(v.current_location)}</td>
      <td>${escapeHtml(v.service_due_date)}</td>
      <td><span class="badge badge-${statusClass(v.status)}">${escapeHtml(v.status)}</span></td>
      <td></td>
    </tr>
  `).join('');
}

async function handleAddVehicleSubmit(event) {
  event.preventDefault();

  const payload = {
    registration_number: document.getElementById('vehRegNum').value,
    vehicle_type: document.getElementById('vehType').value,
    make: document.getElementById('vehMake').value,
    model: document.getElementById('vehModel').value,
    capacity_mt: parseFloat(document.getElementById('vehCapacity').value) || 15,
    fuel_type: document.getElementById('vehFuelType').value,
    current_location: document.getElementById('vehLocation').value
  };

  try {
    const res = await fetchAPI('/vehicles', {
      method: 'POST',
      body: JSON.stringify(payload)
    });

    if (res.success) {
      showToast(res.message || 'Vehicle added successfully', 'success');
      closeModal('addVehicleModal');
      document.getElementById('addVehicleForm').reset();
      loadVehicles();
    }
  } catch (err) {}
}

document.addEventListener('DOMContentLoaded', () => {
  if (document.getElementById('vehiclesTableBody')) {
    loadVehicles();
    setupTableSearch('vehicleSearchInput', 'vehiclesTable');
  }
});
