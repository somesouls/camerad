(() => {
  'use strict';

  const storageKey = 'camerad-landing-theme';
  const root = document.documentElement;
  const media = window.matchMedia('(prefers-color-scheme: light)');

  const savedTheme = (() => {
    try {
      const value = window.localStorage.getItem(storageKey);
      return value === 'light' || value === 'dark' ? value : null;
    } catch (_) {
      return null;
    }
  })();

  if (savedTheme) root.dataset.theme = savedTheme;

  const currentTheme = () => root.dataset.theme || (media.matches ? 'light' : 'dark');

  const syncChrome = theme => {
    const meta = document.querySelector('meta[name="theme-color"]');
    if (meta) meta.content = theme === 'light' ? '#ffe8d1' : '#23231a';
  };

  syncChrome(currentTheme());

  window.addEventListener('DOMContentLoaded', () => {
    const button = document.querySelector('.lp-theme-toggle');
    if (!button) return;

    const syncButton = () => {
      const theme = currentTheme();
      const next = theme === 'dark' ? 'light' : 'dark';
      button.setAttribute('aria-label', `Aktifkan mode ${next === 'light' ? 'terang' : 'gelap'}`);
      button.setAttribute('title', `Mode ${next === 'light' ? 'terang' : 'gelap'}`);
      button.setAttribute('aria-pressed', String(theme === 'light'));
      syncChrome(theme);
    };

    button.addEventListener('click', () => {
      const next = currentTheme() === 'dark' ? 'light' : 'dark';
      root.dataset.theme = next;
      try { window.localStorage.setItem(storageKey, next); } catch (_) { /* storage may be blocked */ }
      syncButton();
    });

    media.addEventListener?.('change', () => {
      if (!root.dataset.theme) syncButton();
    });

    syncButton();
  });
})();
