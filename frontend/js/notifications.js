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
    container.textContent = '';
    const empty = document.createElement('div');
    empty.style.textAlign = 'center';
    empty.style.padding = '30px';
    empty.style.color = 'var(--text-muted)';
    empty.textContent = 'No notifications';
    container.appendChild(empty);
    return;
  }

  container.textContent = '';
  notifs.forEach(n => {
    const card = document.createElement('div');
    card.className = 'card';
    card.style.padding = '16px';
    card.style.marginBottom = '12px';
    card.style.opacity = n.is_read ? 0.75 : 1;

    const row = document.createElement('div');
    row.style.display = 'flex';
    row.style.justifyContent = 'space-between';
    row.style.alignItems = 'center';

    const title = document.createElement('h4');
    title.style.fontSize = '14px';
    title.style.fontWeight = '700';
    title.style.color = 'var(--primary-navy)';
    title.textContent = n.title;

    const ts = document.createElement('span');
    ts.style.fontSize = '11px';
    ts.style.color = 'var(--text-muted)';
    ts.textContent = n.timestamp;

    row.appendChild(title);
    row.appendChild(ts);

    const msg = document.createElement('p');
    msg.style.fontSize = '13px';
    msg.style.color = 'var(--text-main)';
    msg.style.marginTop = '6px';
    msg.textContent = n.message;

    card.appendChild(row);
    card.appendChild(msg);

    if (!n.is_read) {
      const btn = document.createElement('button');
      btn.className = 'btn btn-sm btn-secondary';
      btn.style.marginTop = '10px';
      btn.textContent = 'Mark as Read';
      btn.addEventListener('click', () => markSingleRead(n.notification_id));
      card.appendChild(btn);
    }

    container.appendChild(card);
  });
}

async function markSingleRead(notifId) {
  try {
    const res = await fetchAPI(`/notifications/${encodeURIComponent(notifId)}/read`, { method: 'PUT' });
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
