(() => {
  'use strict';

  const nav = document.querySelector('#mainNav');
  const menu = document.querySelector('#navbarMenu');
  const navCollapse = menu && window.bootstrap
    ? bootstrap.Collapse.getOrCreateInstance(menu, { toggle: false })
    : null;

  const setNavState = () => nav?.classList.toggle('scrolled', window.scrollY > 24);
  setNavState();
  window.addEventListener('scroll', setNavState, { passive: true });

  document.querySelectorAll('#navbarMenu a[href^="#"]').forEach((link) => {
    link.addEventListener('click', () => {
      if (window.innerWidth < 992 && menu?.classList.contains('show') && navCollapse) navCollapse.hide();
    });
  });

  const revealItems = document.querySelectorAll('.reveal');
  if ('IntersectionObserver' in window && !window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
    const observer = new IntersectionObserver((entries, instance) => {
      entries.forEach((entry) => {
        if (entry.isIntersecting) {
          entry.target.classList.add('visible');
          instance.unobserve(entry.target);
        }
      });
    }, { threshold: 0.12, rootMargin: '0px 0px -40px' });
    revealItems.forEach((item) => observer.observe(item));
  } else {
    revealItems.forEach((item) => item.classList.add('visible'));
  }
})();
