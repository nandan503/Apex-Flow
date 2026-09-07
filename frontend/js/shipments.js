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

  const role = (currentUser() || {}).role;
  const canMutate = role === 'ADMIN' || role === 'MANAGER' || role === 'DRIVER';
  const canDelete = role === 'ADMIN';

  tbody.innerHTML = shipments.map(s => {
    const id = safeId(s.shipment_id);
    const st = statusClass(s.status);
    const pay = statusClass(s.payment_status);
    return `
    <tr>
      <td><strong>${escapeHtml(s.shipment_id)}</strong></td>
      <td>${escapeHtml(s.customer_name)}</td>
      <td>${escapeHtml(s.pickup_location)} → ${escapeHtml(s.destination)}</td>
      <td>${escapeHtml(s.goods_type)}</td>
      <td>${escapeHtml(s.weight_kg)} kg</td>
      <td>${escapeHtml(s.vehicle_reg || 'Unassigned')}</td>
      <td>${escapeHtml(s.driver_name || 'Unassigned')}</td>
      <td>${escapeHtml(s.booking_date)}</td>
      <td>${escapeHtml(s.expected_delivery)}</td>
      <td><span class="badge badge-${st}">${escapeHtml(s.status)}</span></td>
      <td><span class="badge badge-${pay}">${escapeHtml(s.payment_status)}</span></td>
      <td>
        <div class="action-btns">
          <button class="btn-icon" title="View Details & Timeline" onclick="viewShipmentTimeline('${id}')">👁</button>
          ${canMutate ? `<button class="btn-icon" title="Update Status" onclick="openStatusModal('${id}', '${st}')">🔄</button>` : ''}
          ${canDelete ? `<button class="btn-icon" title="Delete Shipment" onclick="confirmDeleteShipment('${id}')">🗑</button>` : ''}
        </div>
      </td>
    </tr>`;
  }).join('');
}

async function handleCreateShipment(event) {
  event.preventDefault();

  const payload = {
    customer_name: document.getElementById('shipmentCustomer').value,
    pickup_location: document.getElementById('shipmentPickup').value,
    destination: document.getElementById('shipmentDestination').value,
    goods_type: document.getElementById('shipmentGoodsType').value,
    weight_kg: parseFloat(document.getElementById('shipmentWeight').value) || 1000,
    quantity: parseInt(document.getElementById('shipmentQuantity').value, 10) || 1,
    booking_date: document.getElementById('shipmentBookingDate').value,
    expected_delivery: document.getElementById('shipmentDeliveryDate').value,
    payment_method: document.getElementById('shipmentPaymentMethod').value,
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
    const res = await fetchAPI(`/shipments/${encodeURIComponent(shipmentId)}`);
    if (res.success && res.data) {
      const s = res.data;
      document.getElementById('timelineShipmentId').textContent = s.shipment_id;
      document.getElementById('timelineCustomer').textContent = s.customer_name;
      document.getElementById('timelineRoute').textContent = `${s.pickup_location} → ${s.destination}`;
      document.getElementById('timelineStatus').textContent = s.status;

      const historyContainer = document.getElementById('timelineHistoryList');
      if (historyContainer) {
        historyContainer.textContent = '';
        (s.history || []).forEach(h => {
          const wrap = document.createElement('div');
          wrap.style.marginBottom = '16px';
          wrap.style.position = 'relative';
          wrap.style.paddingLeft = '24px';
          wrap.style.borderLeft = '3px solid var(--accent-blue)';
          const st = document.createElement('div');
          st.style.fontWeight = '700';
          st.style.fontSize = '13px';
          st.textContent = h.status;
          const meta = document.createElement('div');
          meta.style.fontSize = '11px';
          meta.style.color = 'var(--text-muted)';
          meta.textContent = `${h.timestamp} - ${h.location} (${h.updated_by})`;
          const notes = document.createElement('div');
          notes.style.fontSize = '12px';
          notes.style.marginTop = '2px';
          notes.textContent = h.notes || '';
          wrap.appendChild(st);
          wrap.appendChild(meta);
          wrap.appendChild(notes);
          historyContainer.appendChild(wrap);
        });
      }

      openModal('timelineModal');
    }
  } catch (err) {
    console.error(err);
  }
}

function openStatusModal(shipmentId, currentStatus) {
  document.getElementById('statusModalShipmentId').value = shipmentId;
  const select = document.getElementById('statusModalSelect');
  if (select) {
    const wanted = String(currentStatus || '').replace(/-/g, ' ');
    for (const opt of select.options) {
      if (opt.value.toLowerCase() === wanted.toLowerCase()) {
        select.value = opt.value;
        break;
      }
    }
  }
  openModal('updateStatusModal');
}

async function handleUpdateStatusSubmit(event) {
  event.preventDefault();
  const shipmentId = document.getElementById('statusModalShipmentId').value;
  const newStatus = document.getElementById('statusModalSelect').value;
  const location = document.getElementById('statusModalLocation').value;
  const notes = document.getElementById('statusModalNotes').value;

  try {
    const res = await fetchAPI(`/shipments/${encodeURIComponent(shipmentId)}/status`, {
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
      const res = await fetchAPI(`/shipments/${encodeURIComponent(shipmentId)}`, { method: 'DELETE' });
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
