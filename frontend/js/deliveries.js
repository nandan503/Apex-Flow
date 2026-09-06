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

  tbody.innerHTML = deliveries.map(d => `
    <tr>
      <td><strong>${d.delivery_id}</strong></td>
      <td><strong>${d.shipment_id}</strong></td>
      <td>${d.customer_name}</td>
      <td>${d.pickup} → ${d.destination}</td>
      <td>${d.expected_delivery}</td>
      <td><span class="badge badge-${d.status.toLowerCase().replace(/\s+/g, '-')}">${d.status}</span></td>
      <td><strong style="letter-spacing:1px; color:var(--accent-blue);">${d.otp_code}</strong></td>
      <td>
        ${d.status !== 'Delivered' ? `
          <button class="btn btn-sm btn-success" onclick="openOTPConfirmModal('${d.shipment_id}', '${d.otp_code}')">Confirm Delivery</button>
        ` : `<span style="color:var(--success); font-weight:bold;">✓ Complete</span>`}
      </td>
    </tr>
  `).join('');
}

function openOTPConfirmModal(shipmentId, expectedOTP) {
  document.getElementById('otpModalShipmentId').value = shipmentId;
  document.getElementById('otpModalExpected').innerText = expectedOTP;
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
