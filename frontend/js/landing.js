// Forensic Platform — Landing Page JS
// Minimal, no inline scripts, CSP-safe

document.addEventListener('DOMContentLoaded', () => {
  // Smooth scroll for anchor links
  document.querySelectorAll('a[href^="#"]').forEach(a => {
    a.addEventListener('click', (e) => {
      e.preventDefault();
      const target = document.querySelector(a.getAttribute('href'));
      if (target) target.scrollIntoView({ behavior: 'smooth', block: 'start' });
    });
  });

  // Navbar scroll effect
  const navbar = document.querySelector('.navbar');
  if (navbar) {
    window.addEventListener('scroll', () => {
      navbar.style.background = window.scrollY > 50 ? 'rgba(17,24,39,0.95)' : 'var(--bg-secondary)';
      navbar.style.backdropFilter = window.scrollY > 50 ? 'blur(10px)' : 'none';
    });
  }

  // Check if already logged in
  fetch('/api/auth/me', { credentials: 'same-origin' })
    .then(res => { if (res.ok) window.location.href = '/pages/dashboard.html'; })
    .catch(() => {});
});
