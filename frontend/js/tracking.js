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

  container.innerHTML = trackingList.map((t) => `
    <div class="card" style="margin-bottom: 12px; cursor: pointer; padding: 14px;" onclick="selectVehicleForTracking('${safeId(t.vehicle_id)}')">
      <div style="display:flex; justify-content:space-between; align-items:center;">
        <div>
          <strong style="font-size:15px; color:var(--primary-navy);">${escapeHtml(t.vehicle_reg)}</strong>
          <div style="font-size:12px; color:var(--text-muted);">${escapeHtml(t.pickup)} → ${escapeHtml(t.destination)} (${escapeHtml(t.shipment_id)})</div>
        </div>
        <span class="badge badge-transit">${escapeHtml(t.status)}</span>
      </div>
      <div style="display:flex; justify-content:space-between; margin-top:10px; font-size:12px;">
        <span>⚡ ${escapeHtml(t.speed_kmh)} km/h</span>
        <span>⏱ ETA: ${escapeHtml(t.eta)}</span>
        <span>📍 ${escapeHtml(t.distance_remaining_km)} km left</span>
      </div>
    </div>
  `).join('');
}

function updateTrackingTelemetry(t) {
  const setText = (id, value) => {
    const el = document.getElementById(id);
    if (el) el.textContent = value;
  };
  setText('trackVehReg', t.vehicle_reg);
  setText('trackShipmentId', t.shipment_id);
  setText('trackDriver', t.driver_name);
  setText('trackRoute', `${t.pickup} → ${t.destination}`);
  setText('trackSpeed', `${t.speed_kmh} km/h`);
  setText('trackETA', t.eta);
  setText('trackDistance', `${t.distance_remaining_km} km`);
  setText('trackCoords', `${t.latitude}, ${t.longitude}`);
  setText('trackProgressPercent', `${t.progress_percent}%`);

  const progressBar = document.getElementById('trackProgressBar');
  if (progressBar) progressBar.style.width = `${t.progress_percent}%`;
}

function drawSimulatedMapCanvas(t) {
  const canvas = document.getElementById('trackingMapCanvas');
  if (!canvas) return;
  const ctx = canvas.getContext('2d');

  canvas.width = canvas.parentElement.clientWidth || 700;
  canvas.height = 360;

  ctx.fillStyle = '#eaf2f8';
  ctx.fillRect(0, 0, canvas.width, canvas.height);

  ctx.strokeStyle = '#d0dfed';
  ctx.lineWidth = 1;
  for (let x = 0; x < canvas.width; x += 40) {
    ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, canvas.height); ctx.stroke();
  }
  for (let y = 0; y < canvas.height; y += 40) {
    ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(canvas.width, y); ctx.stroke();
  }

  const p1 = { x: 80, y: 80 };
  const p2 = { x: canvas.width - 80, y: canvas.height - 80 };

  ctx.strokeStyle = '#0789ff';
  ctx.lineWidth = 6;
  ctx.lineCap = 'round';
  ctx.beginPath();
  ctx.moveTo(p1.x, p1.y);
  ctx.lineTo(p2.x, p2.y);
  ctx.stroke();

  ctx.fillStyle = '#08a75a';
  ctx.beginPath(); ctx.arc(p1.x, p1.y, 10, 0, Math.PI * 2); ctx.fill();
  ctx.fillStyle = '#14213d'; ctx.font = '10px Arial'; ctx.fillText(String(t.pickup || ''), p1.x - 15, p1.y - 14);

  ctx.fillStyle = '#ef3348';
  ctx.beginPath(); ctx.arc(p2.x, p2.y, 10, 0, Math.PI * 2); ctx.fill();
  ctx.fillStyle = '#14213d'; ctx.fillText(String(t.destination || ''), p2.x - 15, p2.y + 24);

  const factor = (t.progress_percent || 50) / 100.0;
  const vx = p1.x + (p2.x - p1.x) * factor;
  const vy = p1.y + (p2.y - p1.y) * factor;

  ctx.fillStyle = '#062b57';
  ctx.beginPath(); ctx.arc(vx, vy, 14, 0, Math.PI * 2); ctx.fill();
}

async function selectVehicleForTracking(vehicleId) {
  try {
    const res = await fetchAPI(`/tracking/${encodeURIComponent(vehicleId)}`);
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
    trackingInterval = setInterval(loadLiveTrackingData, 4000);
  }
});
