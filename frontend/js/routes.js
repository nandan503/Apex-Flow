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
      document.getElementById('optDistance').textContent = `${r.distance_km} km`;
      document.getElementById('optETA').textContent = r.estimated_time;
      document.getElementById('optFuelCost').textContent = `₹${Number(r.fuel_cost_est || 0).toLocaleString()}`;
      document.getElementById('optRouteName').textContent = r.recommended_route;

      const stopsContainer = document.getElementById('optStopsList');
      if (stopsContainer && r.stops) {
        stopsContainer.textContent = '';
        r.stops.forEach(s => {
          const li = document.createElement('li');
          li.style.margin = '8px 0';
          li.style.fontSize = '13px';
          li.textContent = `${s.stop} (ETA: ${s.eta})`;
          stopsContainer.appendChild(li);
        });
      }

      showToast('Optimal route calculated successfully!', 'success');
    }
  } catch (err) {}
}

async function loadRoutesTable() {
  try {
    const res = await fetchAPI('/routes');
    if (res.success && res.data) {
      const tbody = document.getElementById('routesTableBody');
      if (!tbody) return;

      tbody.innerHTML = res.data.map(r => `
        <tr>
          <td><strong>${escapeHtml(r.route_id)}</strong></td>
          <td>${escapeHtml(r.pickup)} → ${escapeHtml(r.destination)}</td>
          <td>${escapeHtml(r.distance_km)} km</td>
          <td>${escapeHtml(r.estimated_time)}</td>
          <td>₹${Number(r.fuel_cost_est || 0).toLocaleString()}</td>
          <td><span class="badge badge-available">${escapeHtml(r.recommended_route)}</span></td>
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
