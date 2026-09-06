/* Route Optimization JS Module */

async function handleOptimizeRouteSubmit(event) {
  event.preventDefault();

  const pickup = document.getElementById('routePickup').value.trim();
  const destination = document.getElementById('routeDestination').value.trim();

  if (!pickup || !destination) {
    showToast('Please enter both pickup location and destination', 'warning');
    return;
  }

  try {
    const res = await fetchAPI('/routes/optimize', {
      method: 'POST',
      body: JSON.stringify({ pickup, destination })
    });

    if (res.success && res.data) {
      const r = res.data;
      document.getElementById('optDistance').innerText = `${r.distance_km} km`;
      document.getElementById('optETA').innerText = r.estimated_time;
      document.getElementById('optFuelCost').innerText = `₹${r.fuel_cost_est.toLocaleString()}`;
      document.getElementById('optRouteName').innerText = r.recommended_route;

      const stopsContainer = document.getElementById('optStopsList');
      if (stopsContainer && r.stops) {
        stopsContainer.innerHTML = r.stops.map(s => `
          <li style="margin: 8px 0; font-size:13px;">
            🏁 <strong>${s.stop}</strong> (ETA: ${s.eta})
          </li>
        `).join('');
      }

      showToast('Optimal route calculated successfully!', 'success');
    }
  } catch (err) {
    // Handled by fetchAPI toast
  }
}

async function loadRoutesTable() {
  try {
    const res = await fetchAPI('/routes');
    if (res.success && res.data) {
      const tbody = document.getElementById('routesTableBody');
      if (!tbody) return;

      tbody.innerHTML = res.data.map(r => `
        <tr>
          <td><strong>${r.route_id}</strong></td>
          <td>${r.pickup} → ${r.destination}</td>
          <td>${r.distance_km} km</td>
          <td>${r.estimated_time}</td>
          <td>₹${r.fuel_cost_est.toLocaleString()}</td>
          <td><span class="badge badge-available">${r.recommended_route}</span></td>
        </tr>
      `).join('');
    }
  } catch (err) {}
}

document.addEventListener('DOMContentLoaded', () => {
  if (document.getElementById('routeOptimizeForm')) {
    loadRoutesTable();
  }
});
