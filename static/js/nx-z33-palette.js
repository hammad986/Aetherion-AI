/**
 * nx-z33-palette.js — Phase Z33D Runtime-Aware Command Palette Extension
 * ═══════════════════════════════════════════════════════════════════════
 * Extends the existing _NxPalette registry with:
 *   - Runtime-adaptive suggestions (session state, replay mode, instability)
 *   - Operator shortcuts (replay jump, forensic export, compression inspect,
 *     skill recall, HITL resume)
 *   - Semantic session / DAG node / failure / skill search
 *
 * Runs AFTER nx-command-palette.js. Uses _NxPalette.register().
 *
 * Rules:
 *  - Palette items are contextual: only available when their condition is met.
 *  - No items appear if their backend is unreachable.
 *  - Semantic search uses prefix match + tag match — no fuzzy AI calls.
 *  - All state from NxBus or real API responses.
 */
'use strict';

(function () {
  if (window._z33palette) return;

  /* Wait for _NxPalette to be ready */
  function _waitForPalette(cb) {
    if (window._NxPalette?.register) { cb(); return; }
    const t = setInterval(() => {
      if (window._NxPalette?.register) { clearInterval(t); cb(); }
    }, 80);
  }

  /* ── Session state accessors ──────────────────────────────────────── */
  const _isExecuting = () => !!(window.currentSession && window._z33ux?.isExecuting !== false);
  const _hasReplay   = () => !!window._z30?.inReplayMode?.();
  const _hasSession  = () => !!window.currentSession;

  /* ── Runtime-aware command sections ──────────────────────────────── */
  const STATIC_ITEMS = [
    // Replay shortcuts
    { icon: '⏮', label: 'Replay: Jump to Start',
      hint: '', section: 'Replay',
      condition: _hasSession,
      action: () => window._z30?.replayStart() },
    { icon: '⏹', label: 'Replay: Exit Replay Mode',
      hint: '', section: 'Replay',
      condition: _hasReplay,
      action: () => window._z30?.replayStop() },

    // Forensic export
    { icon: '⬇', label: 'Export Forensic Bundle',
      hint: '', section: 'Forensics',
      condition: _hasSession,
      action: () => {
        const sid = window.currentSession;
        if (sid) window.open(`/api/z31/export/${encodeURIComponent(sid)}`, '_blank');
      }},
    { icon: '📂', label: 'Open Session History',
      hint: '', section: 'Forensics',
      action: () => {
        window.nxSetTab?.('live');
        setTimeout(() => window._z31forensics?.togglePanel(), 100);
      }},

    // Z32 compression + confidence
    { icon: '↯', label: 'Force Context Compression',
      hint: '', section: 'Runtime',
      condition: _hasSession,
      action: () => window._z32?.forceCompress() },
    { icon: '◉', label: 'Check Semantic Confidence',
      hint: '', section: 'Runtime',
      condition: _hasSession,
      action: () => { window._z32?.forceConfidence(); window.nxSetTab?.('live'); }},
    { icon: '⬡', label: 'Run Failure Intelligence Report',
      hint: '', section: 'Runtime',
      condition: _hasSession,
      action: () => { window._z32?.forceIntel(); window.nxSetTab?.('live'); }},

    // Skill recall
    { icon: '🧠', label: 'Open Skill Memory Panel',
      hint: '', section: 'Skills',
      action: () => {
        window.nxSetTab?.('live');
        setTimeout(() => window._z32?.toggleSkills(), 100);
      }},

    // Timeline
    { icon: '⏱', label: 'Expand Execution Timeline',
      hint: '', section: 'Live',
      action: () => {
        window.nxSetTab?.('live');
        setTimeout(() => window._z33timeline?.toggle(), 100);
      }},

    // HITL
    { icon: '⏸', label: 'Review HITL Escalations',
      hint: '', section: 'Governance',
      action: () => window.nxOpenPanel?.('hitl') || window.nxSetTab?.('agents') },
  ];

  /* ── Dynamic session search ───────────────────────────────────────── */
  let _sessionCache = [];
  let _skillCache   = [];
  let _cacheTs      = 0;

  async function _refreshCache() {
    if (Date.now() - _cacheTs < 30_000) return;
    _cacheTs = Date.now();
    try {
      const [sr, skr] = await Promise.allSettled([
        fetch('/api/z31/sessions?limit=10').then(r => r.json()),
        fetch('/api/z32/skills?limit=10').then(r => r.json()),
      ]);
      _sessionCache = sr.status === 'fulfilled' ? (sr.value.sessions || []) : [];
      _skillCache   = skr.status === 'fulfilled' ? (skr.value.skills || []) : [];
    } catch { /* non-fatal */ }
  }

  function _dynamicItems(query) {
    if (!query || query.length < 2) return [];
    const q = query.toLowerCase();
    const items = [];

    _sessionCache.forEach(s => {
      const sid = s.session_id || '';
      if (sid.toLowerCase().includes(q) || (s.integrity_verdict || '').toLowerCase().includes(q)) {
        items.push({
          icon: '📁', label: `Session: ${sid.slice(0,12)}… (${s.integrity_verdict || '?'})`,
          section: 'Sessions', hint: '',
          action: () => {
            window._z31forensics?.loadSession(sid);
            window.nxSetTab?.('live');
          }
        });
      }
    });

    _skillCache.forEach(sk => {
      if ((sk.name || '').toLowerCase().includes(q)) {
        items.push({
          icon: '🧠', label: `Skill: ${sk.name}`,
          section: 'Skills', hint: `${Math.round((sk.validation_rate || 0) * 100)}%`,
          action: () => {
            window.nxSetTab?.('live');
            setTimeout(() => window._z32?.toggleSkills(), 100);
          }
        });
      }
    });

    return items;
  }

  /* ── Runtime state banner in palette ─────────────────────────────── */
  function _injectStateBanner() {
    const palette = document.querySelector('.nx-palette');
    if (!palette || document.getElementById('z33PaletteStateBanner')) return;

    const banner = document.createElement('div');
    banner.id = 'z33PaletteStateBanner';
    banner.className = 'nx-palette-state-banner';
    banner.style.display = 'none';

    const inputEl = palette.querySelector('.nx-palette-input');
    if (inputEl) inputEl.after(banner);
    else palette.prepend(banner);
  }

  function _updateStateBanner() {
    const banner = document.getElementById('z33PaletteStateBanner');
    if (!banner) return;
    if (_isExecuting()) {
      banner.innerHTML = `<span class="nx-palette-state-dot"></span> Session active — runtime-aware suggestions shown`;
      banner.style.display = 'flex';
    } else if (_hasReplay()) {
      banner.innerHTML = `<span class="nx-palette-state-dot" style="background:#a78bfa"></span> Replay mode active`;
      banner.style.display = 'flex';
    } else {
      banner.style.display = 'none';
    }
  }

  /* ── Register with _NxPalette ─────────────────────────────────────── */
  function _registerItems() {
    STATIC_ITEMS.forEach(item => {
      window._NxPalette.register({
        icon:    item.icon,
        label:   item.label,
        hint:    item.hint || '',
        section: item.section,
        condition: item.condition,
        action:  item.action,
      });
    });

    // Register dynamic search provider if supported
    if (typeof window._NxPalette.registerSearchProvider === 'function') {
      window._NxPalette.registerSearchProvider(_dynamicItems);
    }
  }

  /* ── Hook palette open/close ──────────────────────────────────────── */
  function _hookPalette() {
    const origOpen  = window.nxOpenPalette;
    const origClose = window.nxClosePalette;

    if (origOpen) {
      window.nxOpenPalette = function (...args) {
        origOpen.apply(this, args);
        _injectStateBanner();
        _updateStateBanner();
        _refreshCache();
      };
    }

    if (origClose) {
      window.nxClosePalette = function (...args) {
        origClose.apply(this, args);
      };
    }
  }

  /* ── Init ────────────────────────────────────────────────────────── */
  window._z33palette = { refresh: _refreshCache };

  _waitForPalette(() => {
    _registerItems();
    _hookPalette();
    _refreshCache();
    console.debug('[Phase Z33] Runtime-aware command palette extension active.');
  });
})();
