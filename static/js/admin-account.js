/* admin-account.js — Account settings page.
   Three small jobs: reveal the create-client form, regenerate a suggested
   password client-side, and copy handed-over credentials to the clipboard. */
(function () {
  'use strict';

  var $ = function (id) { return document.getElementById(id); };

  // --- Reveal / hide the create-client form -------------------------------
  var reveal = $('ac-reveal');
  var formEl = $('ac-createform');
  var cancel = $('ac-cancel');

  function showForm(on) {
    if (!formEl) return;
    formEl.hidden = !on;
    if (reveal) {
      reveal.setAttribute('aria-expanded', on ? 'true' : 'false');
      // The button is the empty state's only CTA, so hide it while the form is
      // open rather than leaving two competing "create" buttons on screen.
      reveal.hidden = on;
    }
    if (on) {
      var first = formEl.querySelector('input');
      if (first) first.focus();
    } else if (reveal) {
      reveal.focus();
    }
  }

  if (reveal) reveal.addEventListener('click', function () { showForm(true); });
  if (cancel) cancel.addEventListener('click', function () { showForm(false); });

  // --- Password generator -------------------------------------------------
  // Mirrors db.suggest_password(): same alphabet, same "one of each class"
  // guarantee, and it avoids look-alike characters (l, I, O, 0, 1) so the
  // password survives being read aloud or retyped.
  var LOWER = 'abcdefghijkmnopqrstuvwxyz';
  var UPPER = 'ABCDEFGHJKLMNPQRSTUVWXYZ';
  var DIGITS = '23456789';
  var SYMBOLS = '!@#$%^&*?-_';

  function randomInt(max) {
    if (window.crypto && window.crypto.getRandomValues) {
      var buf = new Uint32Array(1);
      // Reject the tail of the range so every value stays equally likely.
      var limit = Math.floor(0xFFFFFFFF / max) * max;
      do { window.crypto.getRandomValues(buf); } while (buf[0] >= limit);
      return buf[0] % max;
    }
    return Math.floor(Math.random() * max);
  }

  function pick(pool) { return pool.charAt(randomInt(pool.length)); }

  function generate(length) {
    var pools = [LOWER, UPPER, DIGITS, SYMBOLS];
    var all = pools.join('');
    var chars = pools.map(pick);
    while (chars.length < length) chars.push(pick(all));
    for (var i = chars.length - 1; i > 0; i--) {          // Fisher-Yates
      var j = randomInt(i + 1);
      var t = chars[i]; chars[i] = chars[j]; chars[j] = t;
    }
    return chars.join('');
  }

  document.querySelectorAll('[data-regenerate]').forEach(function (btn) {
    btn.addEventListener('click', function () {
      var target = $(btn.dataset.regenerate);
      if (!target) return;
      var value = generate(16);
      target.value = value;
      // Keep the confirm field in step so the form stays submittable.
      var confirm = target.form && target.form.querySelector('[name=confirm_password]');
      if (confirm) confirm.value = value;
      target.focus();
      target.select();
    });
  });

  // Show/hide toggles live in password-toggle.js, shared with the sign-in page.

  // --- Warn when renaming the client's username ---------------------------
  // It's the credential they type, so changing it silently breaks their login.
  var cpUsername = $('cp-username');
  var cpHint = $('cp-username-hint');
  if (cpUsername && cpHint) {
    var originalName = cpUsername.dataset.original || cpUsername.value;
    var defaultHint = cpHint.textContent;
    cpUsername.addEventListener('input', function () {
      var changed = cpUsername.value.trim() !== originalName;
      cpHint.textContent = changed
        ? 'Changing this changes how they sign in — “' + originalName +
          '” will stop working.'
        : defaultHint;
      cpHint.classList.toggle('ac-hint--warn', changed);
    });
  }

  // --- Copy credentials ---------------------------------------------------
  // Two flavours: a literal string (data-copy) or the live value of a field
  // (data-copy-field), so a regenerated password copies what's on screen.
  document.querySelectorAll('[data-copy-field]').forEach(function (btn) {
    btn.dataset.copyDynamic = btn.dataset.copyField;
  });

  document.querySelectorAll('[data-copy], [data-copy-dynamic]').forEach(function (btn) {
    btn.addEventListener('click', function () {
      var dynamic = btn.dataset.copyDynamic ? $(btn.dataset.copyDynamic) : null;
      var text = dynamic ? dynamic.value : btn.dataset.copy;
      var done = function () {
        var original = btn.textContent;
        btn.textContent = 'Copied';
        window.setTimeout(function () { btn.textContent = original; }, 1600);
      };
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(text).then(done, fallback);
      } else {
        fallback();
      }
      // execCommand is deprecated but is the only option on a non-secure
      // origin, which is exactly how this gets used over plain HTTP in dev.
      function fallback() {
        var ta = document.createElement('textarea');
        ta.value = text;
        ta.setAttribute('readonly', '');
        ta.style.position = 'fixed';
        ta.style.opacity = '0';
        document.body.appendChild(ta);
        ta.select();
        try { document.execCommand('copy'); done(); } catch (e) { /* ignore */ }
        document.body.removeChild(ta);
      }
    });
  });
})();
