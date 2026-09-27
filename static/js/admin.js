(function () {
  'use strict';
  // Confirm before a destructive admin action submits (kept out of inline attributes: CSP is script-src 'self').
  document.querySelectorAll('.confirm-form').forEach(function (form) {
    form.addEventListener('submit', function (e) {
      if (!window.confirm(form.getAttribute('data-confirm') || 'Are you sure?')) e.preventDefault();
    });
  });
})();
