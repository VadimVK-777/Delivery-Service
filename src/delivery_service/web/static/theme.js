(() => {
  const storageKey = 'parcel-theme';
  const root = document.documentElement;
  let theme = 'light';
  try {
    const saved = localStorage.getItem(storageKey);
    if (saved === 'light' || saved === 'dark') theme = saved;
  } catch {
    // Theme switching still works when browser storage is unavailable.
  }
  // Apply before the stylesheet loads to avoid flashing the wrong theme.
  root.dataset.theme = theme;

  document.addEventListener('DOMContentLoaded', () => {
    const button = document.querySelector('#theme-toggle');
    if (!button) return;
    function updateButton() {
      const dark = root.dataset.theme === 'dark';
      button.textContent = dark ? '☀ Светлая тема' : '☾ Тёмная тема';
      button.setAttribute('aria-label', dark ? 'Включить светлую тему' : 'Включить тёмную тему');
    }
    updateButton();
    button.addEventListener('click', () => {
      root.dataset.theme = root.dataset.theme === 'dark' ? 'light' : 'dark';
      updateButton();
      try {
        localStorage.setItem(storageKey, root.dataset.theme);
      } catch {
        // Keep the selected theme for this page even without persistence.
      }
    });
  });
})();
