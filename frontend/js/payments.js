/* Payments & Billing Invoicing Module */

let currentPayments = [];

async function loadPayments() {
  try {
    const res = await fetchAPI('/payments');
    if (res.success && res.data) {
      currentPayments = res.data;
      renderPaymentsTable(currentPayments);
    }
  } catch (err) {}
}

function renderPaymentsTable(payments) {
  const tbody = document.getElementById('paymentsTableBody');
  if (!tbody) return;

  tbody.innerHTML = payments.map(p => `
    <tr>
      <td><strong>${escapeHtml(p.invoice_id)}</strong></td>
      <td>${escapeHtml(p.shipment_id)}</td>
      <td>${escapeHtml(p.customer_name)}</td>
      <td>₹${Number(p.amount || 0).toLocaleString()}</td>
      <td>₹${Number(p.tax_amount || 0).toLocaleString()}</td>
      <td><strong>₹${Number(p.total_amount || 0).toLocaleString()}</strong></td>
      <td>${escapeHtml(p.payment_method)}</td>
      <td><span class="badge badge-${statusClass(p.payment_status)}">${escapeHtml(p.payment_status)}</span></td>
      <td>${escapeHtml(p.invoice_date)}</td>
      <td>
        <button class="btn-icon" title="Print Invoice" onclick="printInvoice('${safeId(p.invoice_id)}')">🖨</button>
      </td>
    </tr>
  `).join('');
}

function printInvoice(invoiceId) {
  const p = currentPayments.find(item => item.invoice_id === invoiceId);
  if (!p) return;

  const setText = (id, value) => {
    const el = document.getElementById(id);
    if (el) el.textContent = value;
  };

  setText('invNum', p.invoice_id);
  setText('invCustomer', p.customer_name);
  setText('invShipmentId', p.shipment_id);
  setText('invDate', p.invoice_date);
  setText('invSubtotal', `₹${Number(p.amount || 0).toLocaleString()}`);
  setText('invTax', `₹${Number(p.tax_amount || 0).toLocaleString()}`);
  setText('invTotal', `₹${Number(p.total_amount || 0).toLocaleString()}`);
  setText('invStatus', p.payment_status);

  openModal('invoiceModal');
}

function triggerPrint() {
  window.print();
}

document.addEventListener('DOMContentLoaded', () => {
  if (document.getElementById('paymentsTableBody')) {
    loadPayments();
    setupTableSearch('paymentSearchInput', 'paymentsTable');
  }
});
