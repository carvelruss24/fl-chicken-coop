/* admin.js — dashboard interactivity.
   Three small jobs: the off-canvas sidebar (mobile), confirm-before-delete,
   and auto-filling a post slug from its title. No dependencies. */
(function () {
  'use strict';

  // --- Off-canvas sidebar -------------------------------------------------
  var shell = document.querySelector('.ad-shell');
  var burger = document.querySelector('.ad-burger');
  var scrim = document.querySelector('.ad-scrim');
  var closeBtn = document.querySelector('.ad-sidebar__close');

  function setDrawer(open) {
    if (!shell) return;
    shell.classList.toggle('is-open', open);
    document.body.classList.toggle('ad-body--locked', open);
    if (burger) burger.setAttribute('aria-expanded', open ? 'true' : 'false');
  }

  if (burger) burger.addEventListener('click', function () {
    setDrawer(!shell.classList.contains('is-open'));
  });
  if (scrim) scrim.addEventListener('click', function () { setDrawer(false); });
  if (closeBtn) closeBtn.addEventListener('click', function () { setDrawer(false); });

  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape') setDrawer(false);
  });

  // Tapping a nav link closes the drawer before the new page paints, so a
  // back-navigation from cache doesn't restore it half-open.
  document.querySelectorAll('.ad-sidebar a').forEach(function (link) {
    link.addEventListener('click', function () { setDrawer(false); });
  });

  // Resizing past the desktop breakpoint drops the drawer state entirely.
  var desktop = window.matchMedia('(min-width: 1024px)');
  var onBreak = function (e) { if (e.matches) setDrawer(false); };
  if (desktop.addEventListener) desktop.addEventListener('change', onBreak);
  else if (desktop.addListener) desktop.addListener(onBreak);

  // --- Confirm destructive actions ---------------------------------------
  document.querySelectorAll('form[data-confirm]').forEach(function (form) {
    form.addEventListener('submit', function (e) {
      if (!window.confirm(form.getAttribute('data-confirm'))) e.preventDefault();
    });
  });

  // --- Slug helper (post editor) ----------------------------------------
  var title = document.getElementById('post-title');
  var slug = document.getElementById('post-slug');
  if (title && slug) {
    // Only mirror the title while the slug is untouched — never clobber a
    // hand-written one (which would break links to a published post).
    var pristine = slug.value.trim() === '';
    slug.addEventListener('input', function () { pristine = false; });
    title.addEventListener('input', function () {
      if (!pristine) return;
      slug.value = title.value
        .toLowerCase()
        .replace(/[^a-z0-9]+/g, '-')
        .replace(/^-+|-+$/g, '');
    });
  }
})();
