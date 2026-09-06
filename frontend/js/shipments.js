/* Shipments Management JS Module */

let currentShipments = [];

async function loadShipments(statusFilter = '') {
  try {
    const url = statusFilter ? `/shipments?status=${encodeURIComponent(statusFilter)}` : '/shipments';
    const res = await fetchAPI(url);
    if (res.success && res.data) {
      currentShipments = res.data;
      renderShipmentsTable(currentShipments);
    }
  } catch (err) {
    console.error('Failed to load shipments:', err);
  }
}

function renderShipmentsTable(shipments) {
  const tbody = document.getElementById('shipmentsTableBody');
  if (!tbody) return;

  if (!shipments || shipments.length === 0) {
    tbody.innerHTML = `<tr><td colspan="12" style="text-align:center; padding: 20px;">No shipments found</td></tr>`;
    return;
  }

  tbody.innerHTML = shipments.map(s => `
    <tr>
      <td><strong>${s.shipment_id}</strong></td>
      <td>${s.customer_name}</td>
      <td>${s.pickup_location} → ${s.destination}</td>
      <td>${s.goods_type}</td>
      <td>${s.weight_kg} kg</td>
      <td>${s.vehicle_reg || 'Unassigned'}</td>
      <td>${s.driver_name || 'Unassigned'}</td>
      <td>${s.booking_date}</td>
      <td>${s.expected_delivery}</td>
      <td><span class="badge badge-${s.status.toLowerCase().replace(/\s+/g, '-')}">${s.status}</span></td>
      <td><span class="badge badge-${s.payment_status.toLowerCase()}">${s.payment_status}</span></td>
      <td>
        <div class="action-btns">
          <button class="btn-icon" title="View Details & Timeline" onclick="viewShipmentTimeline('${s.shipment_id}')">👁</button>
          <button class="btn-icon" title="Update Status" onclick="openStatusModal('${s.shipment_id}', '${s.status}')">🔄</button>
          <button class="btn-icon" title="Delete Shipment" onclick="confirmDeleteShipment('${s.shipment_id}')">🗑</button>
        </div>
      </td>
    </tr>
  `).join('');
}

async function handleCreateShipment(event) {
  event.preventDefault();
  
  const payload = {
    customer_name: document.getElementById('shipmentCustomer').value,
    pickup_location: document.getElementById('shipmentPickup').value,
    destination: document.getElementById('shipmentDestination').value,
    goods_type: document.getElementById('shipmentGoodsType').value,
    weight_kg: parseFloat(document.getElementById('shipmentWeight').value) || 1000,
    quantity: parseInt(document.getElementById('shipmentQuantity').value) || 1,
    vehicle_reg: document.getElementById('shipmentVehicle').value,
    driver_name: document.getElementById('shipmentDriver').value,
    booking_date: document.getElementById('shipmentBookingDate').value,
    expected_delivery: document.getElementById('shipmentDeliveryDate').value,
    shipping_cost: parseFloat(document.getElementById('shipmentCost').value) || 5000,
    payment_method: document.getElementById('shipmentPaymentMethod').value,
    payment_status: document.getElementById('shipmentPaymentStatus').value,
    special_instructions: document.getElementById('shipmentInstructions').value
  };

  try {
    const res = await fetchAPI('/shipments', {
      method: 'POST',
      body: JSON.stringify(payload)
    });

    if (res.success) {
      showToast(res.message || 'Shipment created successfully!', 'success');
      closeModal('createShipmentModal');
      document.getElementById('createShipmentForm').reset();
      loadShipments();
    }
  } catch (err) {
    // Handled by fetchAPI toast
  }
}

async function viewShipmentTimeline(shipmentId) {
  try {
    const res = await fetchAPI(`/shipments/${shipmentId}`);
    if (res.success && res.data) {
      const s = res.data;
      document.getElementById('timelineShipmentId').innerText = s.shipment_id;
      document.getElementById('timelineCustomer').innerText = s.customer_name;
      document.getElementById('timelineRoute').innerText = `${s.pickup_location} → ${s.destination}`;
      document.getElementById('timelineStatus').innerText = s.status;

      const historyContainer = document.getElementById('timelineHistoryList');
      if (historyContainer) {
        historyContainer.innerHTML = (s.history || []).map(h => `
          <div style="margin-bottom: 16px; position: relative; padding-left: 24px; border-left: 3px solid var(--accent-blue);">
            <div style="font-weight: 700; font-size: 13px;">${h.status}</div>
            <div style="font-size: 11px; color: var(--text-muted);">${h.timestamp} - ${h.location} (${h.updated_by})</div>
            <div style="font-size: 12px; margin-top: 2px;">${h.notes || ''}</div>
          </div>
        `).join('');
      }

      openModal('timelineModal');
    }
  } catch (err) {
    console.error(err);
  }
}

function openStatusModal(shipmentId, currentStatus) {
  document.getElementById('statusModalShipmentId').value = shipmentId;
  document.getElementById('statusModalSelect').value = currentStatus;
  openModal('updateStatusModal');
}

async function handleUpdateStatusSubmit(event) {
  event.preventDefault();
  const shipmentId = document.getElementById('statusModalShipmentId').value;
  const newStatus = document.getElementById('statusModalSelect').value;
  const location = document.getElementById('statusModalLocation').value;
  const notes = document.getElementById('statusModalNotes').value;

  try {
    const res = await fetchAPI(`/shipments/${shipmentId}/status`, {
      method: 'PUT',
      body: JSON.stringify({ status: newStatus, location, notes })
    });

    if (res.success) {
      showToast(`Shipment status updated to ${newStatus}`, 'success');
      closeModal('updateStatusModal');
      loadShipments();
    }
  } catch (err) {
    // Handled by fetchAPI toast
  }
}

async function confirmDeleteShipment(shipmentId) {
  if (confirm(`Are you sure you want to delete shipment ${shipmentId}?`)) {
    try {
      const res = await fetchAPI(`/shipments/${shipmentId}`, { method: 'DELETE' });
      if (res.success) {
        showToast(res.message || 'Shipment deleted successfully', 'success');
        loadShipments();
      }
    } catch (err) {
      // Handled by fetchAPI
    }
  }
}

document.addEventListener('DOMContentLoaded', () => {
  if (document.getElementById('shipmentsTableBody')) {
    loadShipments();
    setupTableSearch('shipmentSearchInput', 'shipmentsTable');
  }
});
