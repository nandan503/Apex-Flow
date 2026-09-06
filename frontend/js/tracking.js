/* Live GPS Tracking JS Module */

let trackingInterval = null;

async function loadLiveTrackingData() {
  try {
    const res = await fetchAPI('/tracking');
    if (res.success && res.data) {
      renderTrackingList(res.data);
      if (res.data.length > 0) {
        updateTrackingTelemetry(res.data[0]);
        drawSimulatedMapCanvas(res.data[0]);
      }
    }
  } catch (err) {
    console.error('Tracking fetch error:', err);
  }
}

function renderTrackingList(trackingList) {
  const container = document.getElementById('trackingActiveList');
  if (!container) return;

  container.innerHTML = trackingList.map((t, idx) => `
    <div class="card" style="margin-bottom: 12px; cursor: pointer; padding: 14px;" onclick="selectVehicleForTracking('${t.vehicle_id}')">
      <div style="display:flex; justify-content:space-between; align-items:center;">
        <div>
          <strong style="font-size:15px; color:var(--primary-navy);">${t.vehicle_reg}</strong>
          <div style="font-size:12px; color:var(--text-muted);">${t.pickup} → ${t.destination} (${t.shipment_id})</div>
        </div>
        <span class="badge badge-transit">${t.status}</span>
      </div>
      <div style="display:flex; justify-content:space-between; margin-top:10px; font-size:12px;">
        <span>⚡ ${t.speed_kmh} km/h</span>
        <span>⏱ ETA: ${t.eta}</span>
        <span>📍 ${t.distance_remaining_km} km left</span>
      </div>
    </div>
  `).join('');
}

function updateTrackingTelemetry(t) {
  document.getElementById('trackVehReg').innerText = t.vehicle_reg;
  document.getElementById('trackShipmentId').innerText = t.shipment_id;
  document.getElementById('trackDriver').innerText = t.driver_name;
  document.getElementById('trackRoute').innerText = `${t.pickup} → ${t.destination}`;
  document.getElementById('trackSpeed').innerText = `${t.speed_kmh} km/h`;
  document.getElementById('trackETA').innerText = t.eta;
  document.getElementById('trackDistance').innerText = `${t.distance_remaining_km} km`;
  document.getElementById('trackCoords').innerText = `${t.latitude}, ${t.longitude}`;
  document.getElementById('trackProgressPercent').innerText = `${t.progress_percent}%`;

  const progressBar = document.getElementById('trackProgressBar');
  if (progressBar) progressBar.style.width = `${t.progress_percent}%`;
}

function drawSimulatedMapCanvas(t) {
  const canvas = document.getElementById('trackingMapCanvas');
  if (!canvas) return;
  const ctx = canvas.getContext('2d');
  
  canvas.width = canvas.parentElement.clientWidth || 700;
  canvas.height = 360;

  // Background map texture
  ctx.fillStyle = '#eaf2f8';
  ctx.fillRect(0, 0, canvas.width, canvas.height);

  // Draw grid roads
  ctx.strokeStyle = '#d0dfed';
  ctx.lineWidth = 1;
  for (let x = 0; x < canvas.width; x += 40) {
    ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, canvas.height); ctx.stroke();
  }
  for (let y = 0; y < canvas.height; y += 40) {
    ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(canvas.width, y); ctx.stroke();
  }

  // Draw Highway Route line
  const p1 = { x: 80, y: 80 };
  const p2 = { x: canvas.width - 80, y: canvas.height - 80 };

  ctx.strokeStyle = '#0789ff';
  ctx.lineWidth = 6;
  ctx.lineCap = 'round';
  ctx.beginPath();
  ctx.moveTo(p1.x, p1.y);
  ctx.lineTo(p2.x, p2.y);
  ctx.stroke();

  // Draw Origin & Destination nodes
  ctx.fillStyle = '#08a75a';
  ctx.beginPath(); ctx.arc(p1.x, p1.y, 10, 0, Math.PI * 2); ctx.fill();
  ctx.fillStyle = '#ffffff'; ctx.font = '10px Arial'; ctx.fillText(t.pickup, p1.x - 15, p1.y - 14);

  ctx.fillStyle = '#ef3348';
  ctx.beginPath(); ctx.arc(p2.x, p2.y, 10, 0, Math.PI * 2); ctx.fill();
  ctx.fillStyle = '#14213d'; ctx.fillText(t.destination, p2.x - 15, p2.y + 24);

  // Calculate current vehicle position on canvas line
  const factor = (t.progress_percent || 50) / 100.0;
  const vx = p1.x + (p2.x - p1.x) * factor;
  const vy = p1.y + (p2.y - p1.y) * factor;

  // Draw Vehicle Marker
  ctx.fillStyle = '#062b57';
  ctx.beginPath(); ctx.arc(vx, vy, 14, 0, Math.PI * 2); ctx.fill();
  ctx.fillStyle = '#ffffff'; ctx.font = '14px Arial'; ctx.fillText('🚚', vx - 9, vy + 5);
}

async function selectVehicleForTracking(vehicleId) {
  try {
    const res = await fetchAPI(`/tracking/${vehicleId}`);
    if (res.success && res.data) {
      updateTrackingTelemetry(res.data);
      drawSimulatedMapCanvas(res.data);
      showToast(`Tracking focused on Vehicle ${res.data.vehicle_reg}`, 'info');
    }
  } catch (e) {}
}

document.addEventListener('DOMContentLoaded', () => {
  if (document.getElementById('trackingMapCanvas')) {
    loadLiveTrackingData();
    // Auto refresh GPS telemetry every 4 seconds
    trackingInterval = setInterval(loadLiveTrackingData, 4000);
  }
});
