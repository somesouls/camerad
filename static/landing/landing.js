(() => {
  'use strict';
  const body = document.body;
  const header = document.querySelector('.lp-header');
  const menu = document.querySelector('.lp-menu');
  const links = document.querySelector('.lp-links');
  const topButton = document.querySelector('.lp-top');
  const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  const onScroll = () => {
    const moved = window.scrollY > 24;
    header?.classList.toggle('scrolled', moved);
    topButton?.classList.toggle('show', window.scrollY > 700);
  };
  onScroll();
  window.addEventListener('scroll', onScroll, { passive: true });

  menu?.addEventListener('click', () => {
    const open = !links.classList.contains('open');
    links.classList.toggle('open', open);
    menu.setAttribute('aria-expanded', String(open));
    body.classList.toggle('menu-open', open);
  });
  links?.addEventListener('click', event => {
    if (!event.target.closest('a')) return;
    links.classList.remove('open');
    menu?.setAttribute('aria-expanded', 'false');
    body.classList.remove('menu-open');
  });
  topButton?.addEventListener('click', () => window.scrollTo({ top: 0, behavior: reduceMotion ? 'auto' : 'smooth' }));

  const revealItems = document.querySelectorAll('.lp-reveal');
  if (reduceMotion || !('IntersectionObserver' in window)) {
    revealItems.forEach(item => item.classList.add('visible'));
  } else {
    const revealObserver = new IntersectionObserver(entries => {
      entries.forEach(entry => {
        if (!entry.isIntersecting) return;
        entry.target.classList.add('visible');
        revealObserver.unobserve(entry.target);
      });
    }, { threshold: 0.12, rootMargin: '0px 0px -40px' });
    revealItems.forEach(item => revealObserver.observe(item));
  }

  const animateCount = element => {
    const target = Number(element.dataset.count || 0);
    const duration = 1300;
    const started = performance.now();
    const tick = now => {
      const progress = Math.min((now - started) / duration, 1);
      const eased = 1 - Math.pow(1 - progress, 3);
      element.textContent = Math.round(target * eased).toLocaleString('id-ID');
      if (progress < 1) requestAnimationFrame(tick);
    };
    requestAnimationFrame(tick);
  };
  const counters = document.querySelectorAll('[data-count]');
  if (reduceMotion || !('IntersectionObserver' in window)) {
    counters.forEach(counter => counter.textContent = Number(counter.dataset.count).toLocaleString('id-ID'));
  } else {
    const countObserver = new IntersectionObserver(entries => entries.forEach(entry => {
      if (!entry.isIntersecting) return;
      animateCount(entry.target);
      countObserver.unobserve(entry.target);
    }), { threshold: 0.8 });
    counters.forEach(counter => countObserver.observe(counter));
  }

  const query = document.getElementById('lp-query-text');
  const messages = [
    'Menganalisis pertanyaan, intent, dan sumber hukum…',
    'Menyinkronkan sinyal Dialogflow dan Avaya…',
    'Memverifikasi konteks dengan knowledge governance…',
    'Menyiapkan insight untuk tinjauan manusia…'
  ];
  if (query && !reduceMotion) {
    let index = 0;
    window.setInterval(() => {
      index = (index + 1) % messages.length;
      query.animate([{ opacity: 0, transform: 'translateY(5px)' }, { opacity: 1, transform: 'none' }], { duration: 380 });
      query.textContent = messages[index];
    }, 3200);
  }

  const sections = [...document.querySelectorAll('main section[id]')];
  const navLinks = [...document.querySelectorAll('.lp-links a')];
  if ('IntersectionObserver' in window) {
    const spy = new IntersectionObserver(entries => entries.forEach(entry => {
      if (!entry.isIntersecting) return;
      navLinks.forEach(link => link.classList.toggle('active', link.hash === `#${entry.target.id}`));
    }), { rootMargin: '-30% 0px -60%' });
    sections.forEach(section => spy.observe(section));
  }

  document.querySelectorAll('.lp-cap, .lp-problem, .lp-govern-grid article').forEach(card => {
    card.addEventListener('pointermove', event => {
      const rect = card.getBoundingClientRect();
      card.style.backgroundImage = `radial-gradient(360px circle at ${event.clientX - rect.left}px ${event.clientY - rect.top}px, rgba(124,92,255,.13), transparent 55%)`;
    });
    card.addEventListener('pointerleave', () => card.style.backgroundImage = '');
  });

  document.getElementById('lp-year').textContent = String(new Date().getFullYear());
})();
