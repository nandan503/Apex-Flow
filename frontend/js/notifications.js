/* Notifications Center Module */

async function loadNotificationsPage() {
  try {
    const res = await fetchAPI('/notifications');
    if (res.success && res.data) {
      renderNotificationsList(res.data);
    }
  } catch (e) {}
}

function renderNotificationsList(notifs) {
  const container = document.getElementById('notificationsList');
  if (!container) return;

  if (!notifs || notifs.length === 0) {
    container.innerHTML = `<div style="text-align:center; padding:30px; color:var(--text-muted);">No notifications</div>`;
    return;
  }

  container.innerHTML = notifs.map(n => `
    <div class="card" style="padding:16px; margin-bottom:12px; border-left: 4px solid var(--${n.type === 'danger' ? 'danger' : (n.type === 'warning' ? 'warning' : 'accent-blue')}); opacity: ${n.is_read ? 0.75 : 1};">
      <div style="display:flex; justify-content:space-between; align-items:center;">
        <h4 style="font-size:14px; font-weight:700; color:var(--primary-navy);">${n.title}</h4>
        <span style="font-size:11px; color:var(--text-muted);">${n.timestamp}</span>
      </div>
      <p style="font-size:13px; color:var(--text-main); margin-top:6px;">${n.message}</p>
      ${!n.is_read ? `
        <button class="btn btn-sm btn-secondary" style="margin-top:10px;" onclick="markSingleRead('${n.notification_id}')">Mark as Read</button>
      ` : `<span style="font-size:11px; color:var(--success); margin-top:8px; display:inline-block;">✓ Read</span>`}
    </div>
  `).join('');
}

async function markSingleRead(notifId) {
  try {
    const res = await fetchAPI(`/notifications/${notifId}/read`, { method: 'PUT' });
    if (res.success) {
      loadNotificationsPage();
      loadNotificationBadge();
    }
  } catch (e) {}
}

document.addEventListener('DOMContentLoaded', () => {
  if (document.getElementById('notificationsList')) {
    loadNotificationsPage();
  }
});
