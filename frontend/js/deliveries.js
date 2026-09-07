/* Delivery Progress & OTP Confirmation Module */

async function loadDeliveries() {
  try {
    const res = await fetchAPI('/deliveries');
    if (res.success && res.data) {
      renderDeliveriesTable(res.data);
    }
  } catch (err) {}
}

function renderDeliveriesTable(deliveries) {
  const tbody = document.getElementById('deliveriesTableBody');
  if (!tbody) return;

  const role = (currentUser() || {}).role;
  const canConfirm = role === 'DRIVER' || role === 'ADMIN' || role === 'MANAGER';

  tbody.innerHTML = deliveries.map(d => {
    const id = safeId(d.shipment_id);
    const st = statusClass(d.status);
    return `
    <tr>
      <td><strong>${escapeHtml(d.delivery_id)}</strong></td>
      <td><strong>${escapeHtml(d.shipment_id)}</strong></td>
      <td>${escapeHtml(d.customer_name)}</td>
      <td>${escapeHtml(d.pickup)} → ${escapeHtml(d.destination)}</td>
      <td>${escapeHtml(d.expected_delivery)}</td>
      <td><span class="badge badge-${st}">${escapeHtml(d.status)}</span></td>
      <td>
        ${d.status !== 'Delivered' && canConfirm ? `
          <button class="btn btn-sm btn-success" onclick="openOTPConfirmModal('${id}')">Confirm Delivery</button>
        ` : (d.status === 'Delivered'
          ? `<span style="color:var(--success); font-weight:bold;">✓ Complete</span>`
          : `<span style="color:var(--text-muted);">Awaiting driver</span>`)}
      </td>
    </tr>`;
  }).join('');
}

function openOTPConfirmModal(shipmentId) {
  document.getElementById('otpModalShipmentId').value = shipmentId;
  openModal('otpDeliveryModal');
}

async function handleOTPConfirmSubmit(event) {
  event.preventDefault();
  const shipmentId = document.getElementById('otpModalShipmentId').value;
  const otpCode = document.getElementById('otpInputCode').value.trim();
  const receiverName = document.getElementById('otpReceiverName').value.trim();

  try {
    const res = await fetchAPI('/deliveries/confirm', {
      method: 'POST',
      body: JSON.stringify({ shipment_id: shipmentId, otp_code: otpCode, receiver_name: receiverName })
    });

    if (res.success) {
      showToast(res.message || 'Delivery confirmed successfully!', 'success');
      closeModal('otpDeliveryModal');
      document.getElementById('otpConfirmForm').reset();
      loadDeliveries();
    }
  } catch (err) {}
}

document.addEventListener('DOMContentLoaded', () => {
  if (document.getElementById('deliveriesTableBody')) {
    loadDeliveries();
  }
});
