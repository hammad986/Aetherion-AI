/* ═══════════════════════════════════════════════════════════════════════════
   NX Modal System — Z22 Module
   Owns: focus-trap lifecycle, aria-hidden management, modal stack.
   Z23: focus trap, Escape routing, aria-hidden on backdrop, screen-reader
        announcements, dialog semantics.
   Works alongside runtime.js openSettings / closeSettings — does NOT
   replace them; augments them with proper module-owned focus management.
   ═══════════════════════════════════════════════════════════════════════════ */
(function NxModalSystem() {
  'use strict';

  /* ── Modal registry ─────────────────────────────────────────────────────── */
  /* Map<id, { el, prevFocus, trapListener }> */
  const _stack = [];

  const FOCUSABLE = [
    'a[href]', 'button:not([disabled])', 'input:not([disabled])',
    'select:not([disabled])', 'textarea:not([disabled])',
    '[tabindex]:not([tabindex="-1"])', 'details > summary',
  ].join(',');

  /* ── Focus trap ─────────────────────────────────────────────────────────── */
  function _trapFocus(modal) {
    return function(e) {
      if (e.key !== 'Tab') return;
      const focusable = Array.from(modal.querySelectorAll(FOCUSABLE));
      if (!focusable.length) { e.preventDefault(); return; }
      const first = focusable[0];
      const last  = focusable[focusable.length - 1];
      if (e.shiftKey) {
        if (document.activeElement === first) { e.preventDefault(); last.focus(); }
      } else {
        if (document.activeElement === last)  { e.preventDefault(); first.focus(); }
      }
    };
  }

  /* ── Live-region announcement (Z23) ─────────────────────────────────────── */
  function _announce(msg) {
    let ar = document.getElementById('nxModalAnnounce');
    if (!ar) {
      ar = document.createElement('div');
      ar.id = 'nxModalAnnounce';
      ar.className = 'nx-sr-only';
      ar.setAttribute('aria-live', 'polite');
      ar.setAttribute('aria-atomic', 'true');
      document.body.appendChild(ar);
    }
    ar.textContent = '';
    requestAnimationFrame(() => { ar.textContent = msg; });
  }

  /* ── Open ───────────────────────────────────────────────────────────────── */
  function open(modalId, options) {
    const el = typeof modalId === 'string' ? document.getElementById(modalId) : modalId;
    if (!el) return;

    const opts = options || {};
    const prevFocus = document.activeElement;

    /* ARIA */
    el.setAttribute('aria-hidden', 'false');
    if (!el.getAttribute('role'))       el.setAttribute('role', 'dialog');
    if (!el.getAttribute('aria-modal')) el.setAttribute('aria-modal', 'true');

    const trapListener = _trapFocus(el);
    el.addEventListener('keydown', trapListener);
    _stack.push({ el, prevFocus, trapListener });

    /* Focus first focusable element */
    requestAnimationFrame(() => {
      const first = el.querySelector(FOCUSABLE);
      if (first) first.focus();
    });

    const label = el.getAttribute('aria-label') || el.querySelector('[role="heading"]')?.textContent || 'dialog';
    _announce(label + ' opened');
    window.NxBus?.emit('modalOpen', { id: modalId });
  }

  /* ── Close ──────────────────────────────────────────────────────────────── */
  function close(modalId) {
    const el = typeof modalId === 'string' ? document.getElementById(modalId) : modalId;
    const idx = _stack.findIndex(m => m.el === el);
    if (idx < 0) return;

    const { prevFocus, trapListener } = _stack.splice(idx, 1)[0];

    if (el) {
      el.removeEventListener('keydown', trapListener);
      el.setAttribute('aria-hidden', 'true');
    }

    /* Restore focus */
    if (prevFocus && typeof prevFocus.focus === 'function') {
      prevFocus.focus();
    }

    _announce('Dialog closed');
    window.NxBus?.emit('modalClose', { id: modalId });
  }

  /* ── Augment existing openSettings / closeSettings ──────────────────────── */
  function _patchSettings() {
    const backdrop = document.getElementById('settingsBackdrop');

    const _origOpen  = window.openSettings;
    const _origClose = window.closeSettings;

    if (typeof _origOpen === 'function') {
      window.openSettings = function(tab) {
        _origOpen(tab);
        if (backdrop) open(backdrop);
      };
    }

    if (typeof _origClose === 'function') {
      window.closeSettings = function() {
        _origClose();
        if (backdrop) close(backdrop);
      };
    }
  }

  /* ── Augment drawer (p55) ────────────────────────────────────────────────── */
  function _patchDrawer() {
    const _origOpen  = window.p55OpenPanel;
    const _origClose = window.p55ClosePanel;

    const drawerId = 'p57Drawer';

    if (typeof _origOpen === 'function') {
      window.p55OpenPanel = function() {
        _origOpen();
        const drawer = document.getElementById(drawerId);
        if (drawer) {
          drawer.setAttribute('role', 'dialog');
          drawer.setAttribute('aria-modal', 'true');
          drawer.setAttribute('aria-label', 'Detail panel');
          open(drawer);
        }
      };
    }

    if (typeof _origClose === 'function') {
      window.p55ClosePanel = function() {
        _origClose();
        const drawer = document.getElementById(drawerId);
        if (drawer) close(drawer);
      };
    }
  }

  /* ── Ensure all modals have correct initial ARIA ─────────────────────────── */
  function _auditModals() {
    document.querySelectorAll('[role="dialog"], .nx-modal, #settingsBackdrop, #uncertaintyModal').forEach(el => {
      if (!el.getAttribute('aria-hidden') && el.style.display === 'none') {
        el.setAttribute('aria-hidden', 'true');
      }
      if (!el.getAttribute('role')) el.setAttribute('role', 'dialog');
      if (!el.getAttribute('aria-modal')) el.setAttribute('aria-modal', 'true');
    });
  }

  /* ── Public API ─────────────────────────────────────────────────────────── */
  window.NxModal = { open, close };

  /* ── Init ───────────────────────────────────────────────────────────────── */
  function _init() {
    _auditModals();
    _patchSettings();
    _patchDrawer();
    console.log('[NxModal] Modal system ready. Modals audited:', _stack.length);
  }

  if (typeof window.NX_LOAD_TASKS !== 'undefined') {
    window.NX_LOAD_TASKS.push(_init);
  } else {
    document.addEventListener('DOMContentLoaded', _init);
  }

})();
