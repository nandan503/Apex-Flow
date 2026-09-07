/* APEX FLOW - Global JavaScript Core Client Module */

const API_BASE = '/api';

const NAV_BY_ROLE = {
  CUSTOMER: [
    'dashboard.html', 'shipments.html', 'tracking.html',
    'deliveries.html', 'notifications.html', 'settings.html', 'payments.html'
  ],
  DRIVER: [
    'dashboard.html', 'shipments.html', 'tracking.html', 'deliveries.html',
    'routes.html', 'notifications.html', 'settings.html'
  ]
};

function escapeHtml(value) {
  if (value === null || value === undefined) return '';
  return String(value)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

function safeId(value) {
  return String(value || '').replace(/[^A-Za-z0-9_-]/g, '');
}

function statusClass(status) {
  return String(status || '')
    .toLowerCase()
    .replace(/\s+/g, '-')
    .replace(/[^a-z0-9-]/g, '');
}

function currentUser() {
  try {
    return JSON.parse(localStorage.getItem('apexflow_user') || 'null');
  } catch (e) {
    return null;
  }
}

async function fetchAPI(endpoint, options = {}) {
  const { headers: extraHeaders, ...rest } = options;
  const config = {
    credentials: 'same-origin',
    headers: {
      'Content-Type': 'application/json',
      ...(extraHeaders || {})
    },
    ...rest
  };

  try {
    const response = await fetch(`${API_BASE}${endpoint}`, config);
    const result = await response.json().catch(() => ({}));

    if (!response.ok) {
      throw new Error(result.message || 'API error occurred');
    }
    return result;
  } catch (error) {
    console.error(`API Error [${endpoint}]:`, error);
    showToast(error.message || 'Server communication failed', 'danger');
    throw error;
  }
}

function showToast(message, type = 'info') {
  let container = document.getElementById('toastContainer');
  if (!container) {
    container = document.createElement('div');
    container.id = 'toastContainer';
    container.className = 'toast-container';
    document.body.appendChild(container);
  }

  const toast = document.createElement('div');
  toast.className = `toast ${type}`;

  const icon = document.createElement('span');
  icon.style.fontWeight = 'bold';
  icon.style.fontSize = '16px';
  icon.textContent = type === 'success' ? '✓' : (type === 'danger' ? '⚠' : 'ℹ');

  const text = document.createElement('span');
  text.textContent = message || '';

  toast.appendChild(icon);
  toast.appendChild(text);
  container.appendChild(toast);

  setTimeout(() => {
    toast.style.opacity = '0';
    toast.style.transform = 'translateX(100%)';
    setTimeout(() => toast.remove(), 300);
  }, 3500);
}

function openModal(modalId) {
  const modal = document.getElementById(modalId);
  if (modal) modal.classList.add('active');
}

function closeModal(modalId) {
  const modal = document.getElementById(modalId);
  if (modal) modal.classList.remove('active');
}

async function initGlobalApp() {
  try {
    const res = await fetchAPI('/auth/me');
    if (res.success && res.data) {
      const user = res.data;
      localStorage.setItem('apexflow_user', JSON.stringify(user));

      const userNameEl = document.getElementById('topbarUserName');
      const userRoleEl = document.getElementById('topbarUserRole');
      const userAvatarEl = document.getElementById('topbarUserAvatar');

      if (userNameEl) userNameEl.textContent = user.name;
      if (userRoleEl) userRoleEl.textContent = user.role;
      if (userAvatarEl) userAvatarEl.textContent = (user.name || '?').charAt(0).toUpperCase();

      applyRoleRestrictions(user.role);
    }
  } catch (err) {
    if (!window.location.pathname.includes('login.html')) {
      window.location.href = '/login.html';
    }
  }

  loadNotificationBadge();
}

async function loadNotificationBadge() {
  try {
    const res = await fetchAPI('/notifications');
    if (res.success && res.data) {
      const unreadCount = res.data.filter(n => !n.is_read).length;
      const countEl = document.getElementById('notifCount');
      if (countEl) {
        countEl.textContent = unreadCount;
        countEl.style.display = unreadCount > 0 ? 'inline-block' : 'none';
      }
    }
  } catch (e) {
    // ignore
  }
}

function applyRoleRestrictions(role) {
  const adminOnlyBtns = document.querySelectorAll('.admin-only');
  if (role === 'DRIVER' || role === 'CUSTOMER') {
    adminOnlyBtns.forEach(btn => { btn.style.display = 'none'; });
  }

  const allowed = NAV_BY_ROLE[role];
  if (allowed) {
    document.querySelectorAll('.menu a').forEach(link => {
      const href = (link.getAttribute('href') || '').split('/').pop();
      if (href && !allowed.includes(href)) {
        const li = link.closest('li');
        if (li) li.style.display = 'none';
      }
    });
    const page = (window.location.pathname.split('/').pop() || 'dashboard.html');
    if (page.endsWith('.html') && page !== 'login.html' && !allowed.includes(page)) {
      window.location.href = '/dashboard.html';
    }
  }
}

async function handleLogout() {
  try {
    await fetchAPI('/auth/logout', { method: 'POST' });
    localStorage.removeItem('apexflow_user');
    showToast('Logged out successfully', 'info');
    setTimeout(() => { window.location.href = '/login.html'; }, 500);
  } catch (err) {
    window.location.href = '/login.html';
  }
}

function setupTableSearch(inputId, tableId) {
  const input = document.getElementById(inputId);
  if (!input) return;

  input.addEventListener('keyup', function () {
    const value = this.value.toLowerCase();
    const rows = document.querySelectorAll(`#${tableId} tbody tr`);
    rows.forEach(row => {
      row.style.display = row.innerText.toLowerCase().includes(value) ? '' : 'none';
    });
  });
}

function setupMobileDrawer() {
  const toggleBtn = document.getElementById('sidebarToggleBtn');
  const sidebar = document.querySelector('.sidebar');
  let overlay = document.getElementById('sidebarOverlay');

  if (!overlay) {
    overlay = document.createElement('div');
    overlay.id = 'sidebarOverlay';
    overlay.className = 'sidebar-overlay';
    document.body.appendChild(overlay);
  }

  function toggleSidebar() {
    if (sidebar) sidebar.classList.toggle('active');
    if (overlay) overlay.classList.toggle('active');
  }

  function closeSidebar() {
    if (sidebar) sidebar.classList.remove('active');
    if (overlay) overlay.classList.remove('active');
  }

  if (toggleBtn) {
    toggleBtn.addEventListener('click', (e) => {
      e.stopPropagation();
      toggleSidebar();
    });
  }

  if (overlay) overlay.addEventListener('click', closeSidebar);

  const menuLinks = document.querySelectorAll('.menu a');
  menuLinks.forEach(link => {
    link.addEventListener('click', () => {
      if (window.innerWidth <= 850) closeSidebar();
    });
  });
}

document.addEventListener('DOMContentLoaded', () => {
  setupMobileDrawer();
  if (!window.location.pathname.includes('login.html')) {
    initGlobalApp();
  }
});
