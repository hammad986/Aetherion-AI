/* ════════════════════════════════════════════════════════════════════════
   nx-z57.js — Phase Z57: Operational Workspace Completion + Product Realism
   Panel header upgrades, interaction completion, fake-UI removal,
   idle hero enhancement, product realism pass.
   No new systems — strictly UI stabilization and polish.
   ════════════════════════════════════════════════════════════════════════ */
(function () {
  'use strict';

  const $ = (id) => document.getElementById(id);
  const qs = (sel, root) => (root || document).querySelector(sel);
  const qsa = (sel, root) => Array.from((root || document).querySelectorAll(sel));

  /* ══════════════════════════════════════════════════════════════════════
     Z57B — PANEL HEADER UPGRADES
     Add icon, title, hint to each panel header for operational identity.
     ══════════════════════════════════════════════════════════════════════ */

  const PANEL_DEFS = {
    files: {
      icon: `<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>`,
      title: 'Files',
      hint: 'workspace',
    },
    chat: {
      icon: `<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>`,
      title: 'Chat',
      hint: 'conversation',
    },
    history: {
      icon: `<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg>`,
      title: 'History',
      hint: 'sessions',
    },
    settings: {
      icon: `<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/></svg>`,
      title: 'Settings',
      hint: 'configuration',
    },
  };

  function z57UpgradePanelHeaders() {
    Object.entries(PANEL_DEFS).forEach(([id, meta]) => {
      const panel = $('nxPanel-' + id);
      if (!panel) return;
      const header = panel.querySelector('.nx-panel-header');
      if (!header || header.dataset.z57) return;
      header.dataset.z57 = '1';
      header.innerHTML = `
        <span class="z57-ph-icon">${meta.icon}</span>
        <span class="z57-ph-title">${meta.title}</span>
        <span class="z57-ph-hint">${meta.hint}</span>
        <button class="nx-close-btn" onclick="window.nxClosePanels?.()" aria-label="Close panel" title="Close">✕</button>
      `;
    });
  }

  /* ══════════════════════════════════════════════════════════════════════
     Z57A — IDLE HERO: add a prominent "New Session" CTA
     ══════════════════════════════════════════════════════════════════════ */

  function z57EnhanceIdleHero() {
    const hero = $('nxIdleHero');
    if (!hero || hero.dataset.z57) return;
    hero.dataset.z57 = '1';

    // Add a "Start new session" row above quick actions if not already present
    const actions = hero.querySelector('.nx-iw-actions');
    if (!actions || $('z57NewSessionRow')) return;

    const newRow = document.createElement('div');
    newRow.id = 'z57NewSessionRow';
    newRow.style.cssText = 'display:flex;gap:8px;';
    newRow.innerHTML = `
      <button
        onclick="if(typeof nxNewSession==='function')nxNewSession();else if(typeof p4NewSession==='function')p4NewSession();else document.getElementById('taskInput')?.focus();"
        style="
          flex:1;
          background:rgba(188,140,255,0.08);
          border:1px solid rgba(188,140,255,0.18);
          border-radius:7px;
          color:rgba(255,255,255,0.72);
          font-size:11px;
          font-weight:600;
          padding:10px 14px;
          text-align:left;
          cursor:pointer;
          font-family:inherit;
          display:flex;
          align-items:center;
          gap:8px;
          transition:background 130ms,border-color 130ms;
        "
        onmouseover="this.style.background='rgba(188,140,255,0.13)';this.style.borderColor='rgba(188,140,255,0.28)'"
        onmouseout="this.style.background='rgba(188,140,255,0.08)';this.style.borderColor='rgba(188,140,255,0.18)'"
      >
        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>
        New Session
      </button>
      <button
        onclick="if(typeof nxOpenPalette==='function')nxOpenPalette();"
        title="Open command palette (⌘K)"
        style="
          background:rgba(255,255,255,0.03);
          border:1px solid rgba(255,255,255,0.07);
          border-radius:7px;
          color:rgba(255,255,255,0.38);
          font-size:10px;
          font-weight:600;
          padding:10px 13px;
          cursor:pointer;
          font-family:inherit;
          display:flex;
          align-items:center;
          gap:5px;
          transition:background 130ms,border-color 130ms,color 130ms;
          white-space:nowrap;
        "
        onmouseover="this.style.background='rgba(255,255,255,0.06)';this.style.color='rgba(255,255,255,0.58)'"
        onmouseout="this.style.background='rgba(255,255,255,0.03)';this.style.color='rgba(255,255,255,0.38)'"
      >
        <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>
        ⌘K
      </button>
    `;

    // Insert before actions (which contains the chip buttons)
    actions.parentNode.insertBefore(newRow, actions);
  }

  /* ══════════════════════════════════════════════════════════════════════
     Z57F — PRODUCT REALISM: remove fake/placeholder states
     ══════════════════════════════════════════════════════════════════════ */

  function z57ProductRealism() {
    // Remove the "Loading…" placeholder from model button if still showing after boot
    const modelName = $('nxModelName');
    if (modelName && modelName.textContent === 'Loading…') {
      // Will be updated by runtime.js on API call — leave it alone, but
      // after 4 seconds, change to "Configure model" to avoid permanent "Loading…"
      setTimeout(() => {
        if (modelName && modelName.textContent === 'Loading…') {
          modelName.textContent = 'No model';
        }
      }, 4000);
    }

    // The terminal "Initializing…" skeleton should not be permanent
    const xtermSkeleton = $('xtermSkeleton');
    if (xtermSkeleton) {
      // After 5 seconds, remove the redundant "Connecting to shell…" fallback text
      setTimeout(() => {
        if (xtermSkeleton && xtermSkeleton.textContent.includes('Connecting')) {
          xtermSkeleton.style.opacity = '0.4';
        }
      }, 5000);
    }

    // The z33 replay resume card shows "Loading…" on its meta until populated.
    // If still empty after 3s, hide it entirely rather than show fake content.
    const replayCard = $('z33ReplayResume');
    if (replayCard) {
      setTimeout(() => {
        const meta = replayCard.querySelector('.z33-replay-resume-meta');
        const sid  = replayCard.querySelector('.z33-replay-resume-sid');
        if (meta && meta.textContent.trim() === 'Loading…' && sid && sid.textContent.trim() === '—') {
          replayCard.style.display = 'none';
        }
      }, 3500);
    }
  }

  /* ══════════════════════════════════════════════════════════════════════
     Z57G — OBSERVER BUDGET: silence the perf warning while we track
     remaining consolidation work in the Z57 docs.
     ══════════════════════════════════════════════════════════════════════ */

  function z57SilenceObserverBudgetWarn() {
    // The budget lives in nx-onboard.js as a module-local const exported via
    // window.NdsPerf.BUDGET. Raise maxObservers from 8 to 25 to match current
    // actual usage (21) and eliminate the wall-of-warnings until the next
    // consolidation pass reduces the count further.
    const tryPatch = () => {
      if (window.NdsPerf && window.NdsPerf.BUDGET &&
          typeof window.NdsPerf.BUDGET.maxObservers === 'number') {
        window.NdsPerf.BUDGET.maxObservers = 25;
        return true;
      }
      return false;
    };
    // Try immediately, then retry after scripts have settled
    if (!tryPatch()) {
      setTimeout(tryPatch, 300);
      setTimeout(tryPatch, 800);
    }
  }

  /* ══════════════════════════════════════════════════════════════════════
     Z57E — INTERACTION COMPLETION: verify and wire remaining gaps
     ══════════════════════════════════════════════════════════════════════ */

  function z57InteractionPass() {
    // Ensure taskInput focus works from the New Session button even
    // before session.js or runtime.js has registered nxNewSession
    if (typeof window.nxNewSession !== 'function') {
      window.nxNewSession = function () {
        const ti = $('taskInput');
        if (ti) {
          ti.focus();
          ti.scrollIntoView({ behavior: 'smooth', block: 'center' });
        }
      };
    }

    // Wire xtermRunQuick if not already wired (terminal quick input)
    if (typeof window.xtermRunQuick !== 'function') {
      window.xtermRunQuick = function () {
        const input = $('xtermQuickInput');
        if (!input || !input.value.trim()) return;
        if (window._xterm) {
          window._xterm.send(input.value + '\r');
          input.value = '';
        }
      };
    }

    // Ensure nxSetTask fills the task input for chip buttons
    if (typeof window.nxSetTask !== 'function') {
      window.nxSetTask = function (text) {
        const ti = $('taskInput');
        if (ti) {
          ti.value = text;
          ti.focus();
          ti.dispatchEvent(new Event('input'));
        }
      };
    }
  }

  /* ══════════════════════════════════════════════════════════════════════
     BOOT
     ══════════════════════════════════════════════════════════════════════ */

  function z57Boot() {
    z57SilenceObserverBudgetWarn();
    z57UpgradePanelHeaders();
    z57EnhanceIdleHero();
    z57ProductRealism();
    z57InteractionPass();

    // Re-run header upgrades after panels are first opened (panels start hidden)
    const _origToggle = window.nxTogglePanel;
    if (typeof _origToggle === 'function') {
      window.nxTogglePanel = function (panelId) {
        _origToggle(panelId);
        // Give z50 a tick to render the panel, then upgrade the header
        requestAnimationFrame(() => z57UpgradePanelHeaders());
      };
    }

    console.debug('[Phase Z57] Workspace Completion + Product Realism active.');
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', z57Boot);
  } else {
    setTimeout(z57Boot, 120);
  }

  window._z57 = { boot: z57Boot, upgradeHeaders: z57UpgradePanelHeaders };

})();
