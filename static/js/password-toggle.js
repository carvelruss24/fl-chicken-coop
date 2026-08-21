/* password-toggle.js — one show/hide implementation for every password field.
   Markup contract: a button with data-toggle-pw="<input id>". If the button
   contains [data-pw-icon="show"] / [data-pw-icon="hide"] elements it swaps
   those; otherwise it swaps its own text between "Show" and "Hide". Loaded by
   the sign-in page and by the dashboard shell. */
(function () {
  'use strict';

  document.querySelectorAll('[data-toggle-pw]').forEach(function (btn) {
    var field = document.getElementById(btn.getAttribute('data-toggle-pw'));
    if (!field) return;

    var showIcon = btn.querySelector('[data-pw-icon="show"]');
    var hideIcon = btn.querySelector('[data-pw-icon="hide"]');

    // NOTE: setAttribute, not `.hidden`. The `hidden` property is defined on
    // HTMLElement, so assigning it to an <svg> sets a JS expando that never
    // reflects to the attribute — the icons would silently never swap.
    function toggleAttr(el, off) {
      if (off) el.setAttribute('hidden', '');
      else el.removeAttribute('hidden');
    }

    function paint(revealed) {
      if (showIcon && hideIcon) {
        toggleAttr(showIcon, revealed);
        toggleAttr(hideIcon, !revealed);
      } else {
        btn.textContent = revealed ? 'Hide' : 'Show';
      }
      btn.setAttribute('aria-label', revealed ? 'Hide password' : 'Show password');
      btn.setAttribute('aria-pressed', revealed ? 'true' : 'false');
    }

    btn.addEventListener('click', function () {
      var revealed = field.type !== 'password';
      field.type = revealed ? 'password' : 'text';
      paint(!revealed);
      // Keep the caret where it was — changing `type` moves it to the end in
      // some browsers, which is jarring mid-edit.
      if (field === document.activeElement) {
        var end = field.value.length;
        try { field.setSelectionRange(end, end); } catch (e) { /* ignore */ }
      }
    });

    paint(field.type !== 'password');
  });
})();
