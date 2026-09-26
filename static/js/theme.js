/* Apply the saved theme before paint; keep this external for the strict CSP. */
(function () {
  'use strict';
  var key = 'ff-theme', root = document.documentElement;
  var system = window.matchMedia('(prefers-color-scheme: dark)'), saved = null;
  try { saved = localStorage.getItem(key); } catch (e) { /* Storage may be unavailable. */ }
  function apply(theme) {
    root.dataset.theme = theme;
    var light = theme === 'light', button = document.getElementById('theme-toggle');
    document.querySelector('meta[name="theme-color"]').content = light ? '#f5f6fb' : '#06050a';
    if (button) {
      button.hidden = false;
      button.setAttribute('aria-label', 'Switch to ' + (light ? 'dark' : 'light') + ' mode');
      button.querySelector('[data-theme-label]').textContent = light ? 'Dark mode' : 'Light mode';
      button.querySelector('[data-theme-icon]').textContent = light ? '☾' : '☀';
    }
    window.dispatchEvent(new Event('ff-theme-change'));
  }
  function preferred() { return saved === 'light' || saved === 'dark' ? saved : system.matches ? 'dark' : 'light'; }
  apply(preferred());
  document.addEventListener('DOMContentLoaded', function () {
    apply(preferred());
    document.getElementById('theme-toggle').addEventListener('click', function () {
      saved = root.dataset.theme === 'light' ? 'dark' : 'light';
      try { localStorage.setItem(key, saved); } catch (e) { /* Still works for this page. */ }
      apply(saved);
    });
  });
  system.addEventListener('change', function () { apply(preferred()); });
  window.addEventListener('storage', function (event) {
    if (event.key === key || event.key === null) { saved = event.newValue; apply(preferred()); }
  });
})();
