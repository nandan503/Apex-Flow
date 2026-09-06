/* APEX FLOW - Global JavaScript Core Client Module */

const API_BASE = '/api';

// API Fetch Helper
async function fetchAPI(endpoint, options = {}) {
  const config = {
    headers: {
      'Content-Type': 'application/json',
      ...options.headers
    },
    ...options
  };

  try {
    const response = await fetch(`${API_BASE}${endpoint}`, config);
    const result = await response.json();

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

// Toast Notification Manager
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
  
  const icon = type === 'success' ? '✓' : (type === 'danger' ? '⚠' : 'ℹ');
  toast.innerHTML = `
    <span style="font-weight:bold; font-size:16px;">${icon}</span>
    <span>${message}</span>
  `;

  container.appendChild(toast);

  setTimeout(() => {
    toast.style.opacity = '0';
    toast.style.transform = 'translateX(100%)';
    setTimeout(() => toast.remove(), 300);
  }, 3500);
}

// Modal Helpers
function openModal(modalId) {
  const modal = document.getElementById(modalId);
  if (modal) {
    modal.classList.add('active');
  }
}

function closeModal(modalId) {
  const modal = document.getElementById(modalId);
  if (modal) {
    modal.classList.remove('active');
  }
}

// Session & Auth UI Check
async function initGlobalApp() {
  try {
    const res = await fetchAPI('/auth/me');
    if (res.success && res.data) {
      const user = res.data;
      localStorage.setItem('apexflow_user', JSON.stringify(user));
      
      const userNameEl = document.getElementById('topbarUserName');
      const userRoleEl = document.getElementById('topbarUserRole');
      const userAvatarEl = document.getElementById('topbarUserAvatar');

      if (userNameEl) userNameEl.innerText = user.name;
      if (userRoleEl) userRoleEl.innerText = user.role;
      if (userAvatarEl) userAvatarEl.innerText = user.name.charAt(0).toUpperCase();

      // Check role restrictions if needed
      applyRoleRestrictions(user.role);
    }
  } catch (err) {
    // If not logged in and not on login page, redirect
    if (!window.location.pathname.includes('login.html')) {
      window.location.href = '/login.html';
    }
  }

  // Load unread notification badge count
  loadNotificationBadge();
}

async function loadNotificationBadge() {
  try {
    const res = await fetchAPI('/notifications');
    if (res.success && res.data) {
      const unreadCount = res.data.filter(n => !n.is_read).length;
      const countEl = document.getElementById('notifCount');
      if (countEl) {
        countEl.innerText = unreadCount;
        countEl.style.display = unreadCount > 0 ? 'inline-block' : 'none';
      }
    }
  } catch (e) {
    // Silently ignore if not loaded
  }
}

function applyRoleRestrictions(role) {
  // Hide admin-only buttons for driver or customer roles if applicable
  if (role === 'DRIVER' || role === 'CUSTOMER') {
    const adminOnlyBtns = document.querySelectorAll('.admin-only');
    adminOnlyBtns.forEach(btn => btn.style.display = 'none');
  }
}

async function handleLogout() {
  try {
    await fetchAPI('/auth/logout', { method: 'POST' });
    localStorage.removeItem('apexflow_user');
    showToast('Logged out successfully', 'info');
    setTimeout(() => window.location.href = '/login.html', 500);
  } catch (err) {
    window.location.href = '/login.html';
  }
}

// Global Filter/Search Helper for HTML Tables
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

// Setup Mobile Drawer Toggle Navigation
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

  if (overlay) {
    overlay.addEventListener('click', closeSidebar);
  }

  // Auto-close on menu link tap for mobile
  const menuLinks = document.querySelectorAll('.menu a');
  menuLinks.forEach(link => {
    link.addEventListener('click', () => {
      if (window.innerWidth <= 850) {
        closeSidebar();
      }
    });
  });
}

document.addEventListener('DOMContentLoaded', () => {
  setupMobileDrawer();
  if (!window.location.pathname.includes('login.html')) {
    initGlobalApp();
  }
});

