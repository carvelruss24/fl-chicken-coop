/* ==========================================================================
   main.js — client-side interactivity (Vanilla JS, no dependencies)

   The header/footer markup is rendered server-side by Flask/Jinja, so this
   script just enhances the already-present DOM. Currently: the responsive
   mobile navigation toggle. Page-specific behaviour can be added below.
   ========================================================================== */

'use strict';

document.addEventListener('DOMContentLoaded', () => {
  initMobileNav();
  initHeaderScroll();
  initGallery();
  initFaqAccordion();
});

/**
 * Transparent → solid header on scroll.
 * Adds .is-scrolled to the header once the page moves past a small threshold.
 * Drives the home page's transparent overlay header (see style.css); harmless
 * on other pages, whose header is solid in every state.
 */
function initHeaderScroll() {
  const header = document.querySelector('.site-header');
  if (!header) return;

  const THRESHOLD = 24;            // px scrolled before the header turns solid
  let ticking = false;

  const update = () => {
    header.classList.toggle('is-scrolled', window.scrollY > THRESHOLD);
    ticking = false;
  };

  window.addEventListener('scroll', () => {
    if (!ticking) {
      ticking = true;
      window.requestAnimationFrame(update);
    }
  }, { passive: true });

  update();                        // set correct state if loaded mid-page
}

/**
 * Gallery region filter + pagination + editorial masonry layout.
 * The filter buttons (hero bar) and pager (footer) drive the grid below, whose
 * cards each carry a data-region and a data-page. A card shows only when it
 * matches both the active region and the active page. Changing either keeps the
 * relevant ARIA state in sync, updates the result count, and recomputes which
 * visible cards span wide so the 3-up / 2-up pattern stays tiled.
 */
function initGallery() {
  const grid = document.querySelector('.gallery__grid');
  if (!grid) return;

  const items = Array.from(grid.children);
  const buttons = Array.from(document.querySelectorAll('.filter-bar__btn'));
  const countEl = document.querySelector('.gallery__count');
  const WIDE = new Set([3, 4]);    // 4th & 5th card in each run of 5 spans wide

  // Pager: numbered buttons carry data-page; prev/next carry data-page-nav.
  const pageButtons = Array.from(document.querySelectorAll('.pagination__btn[data-page]'));
  const prevBtn = document.querySelector('.pagination__btn[data-page-nav="prev"]');
  const nextBtn = document.querySelector('.pagination__btn[data-page-nav="next"]');
  const lastPage = items.reduce((max, it) => Math.max(max, Number(it.dataset.page) || 1), 1);

  let region = 'all';
  let page = 1;

  const layout = () => {
    items.forEach((it) => {
      const onPage = (Number(it.dataset.page) || 1) === page;
      const inRegion = region === 'all' || it.dataset.region === region;
      it.hidden = !(onPage && inRegion);
    });
    const visible = items.filter((it) => !it.hidden);
    visible.forEach((it, i) => {
      it.classList.toggle('gallery__item--wide', WIDE.has(i % 5));
    });
    if (countEl) {
      const n = visible.length;
      countEl.textContent = n
        ? `Showing 1–${n} of ${n}`
        : 'No installations in this region yet';
    }
  };

  const goToPage = (p) => {
    page = Math.min(Math.max(p, 1), lastPage);
    pageButtons.forEach((b) => {
      const on = Number(b.dataset.page) === page;
      b.classList.toggle('is-active', on);
      if (on) b.setAttribute('aria-current', 'page');
      else b.removeAttribute('aria-current');
    });
    if (prevBtn) prevBtn.disabled = page === 1;
    if (nextBtn) nextBtn.disabled = page === lastPage;
    layout();
  };

  buttons.forEach((btn) => {
    btn.addEventListener('click', () => {
      buttons.forEach((b) => {
        const on = b === btn;
        b.classList.toggle('is-active', on);
        b.setAttribute('aria-pressed', String(on));
      });
      region = btn.dataset.region;
      layout();
    });
  });

  pageButtons.forEach((b) => {
    b.addEventListener('click', () => goToPage(Number(b.dataset.page)));
  });
  if (prevBtn) prevBtn.addEventListener('click', () => goToPage(page - 1));
  if (nextBtn) nextBtn.addEventListener('click', () => goToPage(page + 1));

  goToPage(1);                     // establish initial page, span pattern + count
}

/**
 * FAQ accordion — a smooth-height enhancement over native <details>.
 * The markup works on its own (native open/close, keyboard, SEO-readable text);
 * this only animates the panel height. With reduced motion we leave the native
 * instant toggle alone.
 */
function initFaqAccordion() {
  const items = document.querySelectorAll('.faq__item');
  if (!items.length) return;
  if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;

  items.forEach((details) => {
    const summary = details.querySelector('summary');
    const panel = details.querySelector('.faq__panel');
    if (!summary || !panel) return;

    summary.addEventListener('click', (event) => {
      event.preventDefault();        // drive open/close ourselves so it can animate

      if (details.open) {
        panel.style.height = `${panel.scrollHeight}px`;
        requestAnimationFrame(() => { panel.style.height = '0px'; });
        panel.addEventListener('transitionend', () => {
          details.open = false;
          panel.style.height = '';
        }, { once: true });
      } else {
        details.open = true;         // render content so it can be measured
        const target = panel.scrollHeight;
        panel.style.height = '0px';
        requestAnimationFrame(() => { panel.style.height = `${target}px`; });
        panel.addEventListener('transitionend', () => {
          panel.style.height = '';   // release back to auto height
        }, { once: true });
      }
    });
  });
}

/**
 * Responsive hamburger navigation.
 * Toggles the mobile menu, keeps ARIA state in sync, traps the collapsed menu
 * out of the tab order (inert), restores focus on Escape, and resets cleanly
 * when the viewport crosses the desktop/mobile breakpoint.
 */
function initMobileNav() {
  const toggle = document.querySelector('.nav-toggle');
  const navWrap = document.querySelector('.nav-wrap');
  if (!toggle || !navWrap) return;

  const mq = window.matchMedia('(max-width: 900px)');

  const setMenu = (open) => {
    navWrap.classList.toggle('is-open', open);
    toggle.setAttribute('aria-expanded', String(open));
    toggle.setAttribute('aria-label', open ? 'Close menu' : 'Open menu');
    // On mobile, a collapsed menu must not be focusable; on desktop the nav
    // is always available (inert is cleared in applyMode).
    if (mq.matches) navWrap.inert = !open;
  };

  const applyMode = () => {
    if (mq.matches) {
      setMenu(false);            // mobile: start closed (also sets inert)
    } else {
      navWrap.inert = false;     // desktop: nav always available
      navWrap.classList.remove('is-open');
      toggle.setAttribute('aria-expanded', 'false');
      toggle.setAttribute('aria-label', 'Open menu');
    }
  };

  toggle.addEventListener('click', () => {
    setMenu(!navWrap.classList.contains('is-open'));
  });

  // Close after tapping a link (so the menu doesn't linger on navigation).
  navWrap.addEventListener('click', (e) => {
    if (e.target.closest('a')) setMenu(false);
  });

  // Escape closes the open menu and returns focus to the toggle.
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && navWrap.classList.contains('is-open')) {
      setMenu(false);
      toggle.focus();
    }
  });

  // Reset state when crossing the mobile/desktop breakpoint.
  mq.addEventListener('change', applyMode);

  applyMode();                   // establish correct initial state
}
