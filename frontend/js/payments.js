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
      <td><strong>${p.invoice_id}</strong></td>
      <td>${p.shipment_id}</td>
      <td>${p.customer_name}</td>
      <td>₹${p.amount.toLocaleString()}</td>
      <td>₹${p.tax_amount.toLocaleString()}</td>
      <td><strong>₹${p.total_amount.toLocaleString()}</strong></td>
      <td>${p.payment_method}</td>
      <td><span class="badge badge-${p.payment_status.toLowerCase()}">${p.payment_status}</span></td>
      <td>${p.invoice_date}</td>
      <td>
        <button class="btn-icon" title="Print Invoice" onclick="printInvoice('${p.invoice_id}')">🖨</button>
      </td>
    </tr>
  `).join('');
}

function printInvoice(invoiceId) {
  const p = currentPayments.find(item => item.invoice_id === invoiceId);
  if (!p) return;

  document.getElementById('invNum').innerText = p.invoice_id;
  document.getElementById('invCustomer').innerText = p.customer_name;
  document.getElementById('invShipmentId').innerText = p.shipment_id;
  document.getElementById('invDate').innerText = p.invoice_date;
  document.getElementById('invSubtotal').innerText = `₹${p.amount.toLocaleString()}`;
  document.getElementById('invTax').innerText = `₹${p.tax_amount.toLocaleString()}`;
  document.getElementById('invTotal').innerText = `₹${p.total_amount.toLocaleString()}`;
  document.getElementById('invStatus').innerText = p.payment_status;

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
