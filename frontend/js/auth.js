/* Authentication JS Module */

document.addEventListener('DOMContentLoaded', () => {
  const loginForm = document.getElementById('loginForm');
  if (loginForm) {
    loginForm.addEventListener('submit', async (e) => {
      e.preventDefault();

      const email = document.getElementById('loginEmail').value.trim();
      const password = document.getElementById('loginPassword').value;

      if (!email || !password) {
        showToast('Please enter both email and password', 'warning');
        return;
      }

      try {
        const res = await fetchAPI('/auth/login', {
          method: 'POST',
          body: JSON.stringify({ email, password })
        });

        if (res.success && res.data) {
          showToast(`Welcome back, ${res.data.name}!`, 'success');
          localStorage.setItem('apexflow_user', JSON.stringify(res.data));
          setTimeout(() => {
            window.location.href = '/dashboard.html';
          }, 600);
        }
      } catch (err) {
        // Handled by fetchAPI toast
      }
    });
  }
});
