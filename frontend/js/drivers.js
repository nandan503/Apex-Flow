/* Drivers Management JS Module */

async function loadDrivers() {
  try {
    const res = await fetchAPI('/drivers');
    if (res.success && res.data) {
      renderDriversTable(res.data);
    }
  } catch (err) {
    console.error('Failed to load drivers:', err);
  }
}

function renderDriversTable(drivers) {
  const tbody = document.getElementById('driversTableBody');
  if (!tbody) return;

  if (!drivers || drivers.length === 0) {
    tbody.innerHTML = `<tr><td colspan="10" style="text-align:center; padding: 20px;">No drivers found</td></tr>`;
    return;
  }

  tbody.innerHTML = drivers.map(d => `
    <tr>
      <td><strong>${d.driver_id}</strong></td>
      <td><strong>${d.name}</strong></td>
      <td>${d.phone}</td>
      <td>${d.license_number}</td>
      <td>${d.license_expiry}</td>
      <td>${d.experience_years} yrs</td>
      <td>${d.total_trips} trips (${d.completed_trips} completed)</td>
      <td>⭐ ${d.rating}</td>
      <td><span class="badge badge-${d.status.toLowerCase().replace(/\s+/g, '-')}">${d.status}</span></td>
      <td>
        <button class="btn-icon" onclick="showToast('Driver ${d.name} details loaded', 'info')">👁</button>
      </td>
    </tr>
  `).join('');
}

async function handleAddDriverSubmit(event) {
  event.preventDefault();

  const payload = {
    name: document.getElementById('driverName').value,
    phone: document.getElementById('driverPhone').value,
    email: document.getElementById('driverEmail').value,
    license_number: document.getElementById('driverLicense').value,
    experience_years: parseInt(document.getElementById('driverExperience').value) || 5,
    status: document.getElementById('driverStatus').value
  };

  try {
    const res = await fetchAPI('/drivers', {
      method: 'POST',
      body: JSON.stringify(payload)
    });

    if (res.success) {
      showToast(res.message || 'Driver added successfully', 'success');
      closeModal('addDriverModal');
      document.getElementById('addDriverForm').reset();
      loadDrivers();
    }
  } catch (err) {
    // Handled by fetchAPI toast
  }
}

document.addEventListener('DOMContentLoaded', () => {
  if (document.getElementById('driversTableBody')) {
    loadDrivers();
    setupTableSearch('driverSearchInput', 'driversTable');
  }
});
