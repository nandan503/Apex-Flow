/* Customers Management JS Module */

async function loadCustomers() {
  try {
    const res = await fetchAPI('/customers');
    if (res.success && res.data) {
      renderCustomersTable(res.data);
    }
  } catch (err) {
    console.error(err);
  }
}

function renderCustomersTable(customers) {
  const tbody = document.getElementById('customersTableBody');
  if (!tbody) return;

  if (!customers || customers.length === 0) {
    tbody.innerHTML = `<tr><td colspan="8" style="text-align:center; padding: 20px;">No customers found</td></tr>`;
    return;
  }

  tbody.innerHTML = customers.map(c => `
    <tr>
      <td><strong>${escapeHtml(c.customer_id)}</strong></td>
      <td><strong>${escapeHtml(c.company)}</strong></td>
      <td>${escapeHtml(c.name)}</td>
      <td>${escapeHtml(c.phone)}</td>
      <td>${escapeHtml(c.email)}</td>
      <td>${escapeHtml(c.address || '')}</td>
      <td>${escapeHtml(c.total_shipments)}</td>
      <td><strong>₹${Number(c.total_spent || 0).toLocaleString()}</strong></td>
    </tr>
  `).join('');
}

async function handleAddCustomerSubmit(event) {
  event.preventDefault();

  const payload = {
    name: document.getElementById('custContactName').value,
    company: document.getElementById('custCompany').value,
    phone: document.getElementById('custPhone').value,
    email: document.getElementById('custEmail').value,
    address: document.getElementById('custAddress').value
  };

  try {
    const res = await fetchAPI('/customers', {
      method: 'POST',
      body: JSON.stringify(payload)
    });

    if (res.success) {
      showToast(res.message || 'Customer created successfully', 'success');
      closeModal('addCustomerModal');
      document.getElementById('addCustomerForm').reset();
      loadCustomers();
    }
  } catch (e) {}
}

document.addEventListener('DOMContentLoaded', () => {
  if (document.getElementById('customersTableBody')) {
    loadCustomers();
    setupTableSearch('customerSearchInput', 'customersTable');
  }
});
