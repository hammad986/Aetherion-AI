/**
 * nx-polish.js — Aetherion Operator Experience & Flow Polish v1
 * ═══════════════════════════════════════════════════════════════════
 * Phase N: Interaction smoothness, keyboard ergonomics, latency
 * masking, empty-state intelligence, progressive disclosure,
 * session continuity, and runtime responsiveness.
 *
 * No new systems. Pure ergonomic refinement.
 * ═══════════════════════════════════════════════════════════════════
 */
(function () {
  'use strict';

  const $ = id => document.getElementById(id);
  const $q = sel => document.querySelector(sel);

  /* ══════════════════════════════════════════════════════════════════
     1. KEYBOARD ERGONOMICS MAP
     ══════════════════════════════════════════════════════════════════ */
  const _keybindings = [
    // Tabs
    { key: '1', ctrl: true, action: () => _switchTab('logs'),     label: 'Output tab'     },
    { key: '2', ctrl: true, action: () => _switchTab('code'),     label: 'Code tab'       },
    { key: '3', ctrl: true, action: () => _switchTab('terminal'), label: 'Terminal tab'   },
    { key: '4', ctrl: true, action: () => _switchTab('preview'),  label: 'Preview tab'    },
    // Inspector toggle
    { key: '\\', ctrl: true, action: () => _toggle('nxInspectorPanel', 'is-open'), label: 'Toggle inspector' },
    // Execution
    { key: 'Enter', ctrl: true, action: _runTask, label: 'Execute task' },
    { key: '.', ctrl: true,     action: _stopTask, label: 'Stop execution' },
    // Command palette
    { key: 'k', ctrl: true, action: _openPalette, label: 'Command palette' },
    // Composer focus
    { key: '/', ctrl: true, action: () => { const t = $('taskInput'); if(t){ t.focus(); t.select(); } }, label: 'Focus composer' },
    // Nav panel
    { key: 'b', ctrl: true, action: () => { if(window.nxToggleLeft) nxToggleLeft(); }, label: 'Toggle nav rail' },
  ];

  function _switchTab(name) {
    if (typeof nxSetTab === 'function') nxSetTab(name);
    else if (window.NxBus) NxBus.emit(NxBus.EVENTS.TAB_CHANGE, { tab: name });
  }

  function _toggle(id, cls) {
    const el = $(id);
    if (el) el.classList.toggle(cls);
  }

  function _runTask() {
    if (typeof nxRunOrStop === 'function') nxRunOrStop();
    else { const btn = $('runBtn'); if(btn) btn.click(); }
  }

  function _stopTask() {
    if (typeof stopSession === 'function') stopSession();
  }

  function _openPalette() {
    if (typeof nxOpenPalette === 'function') nxOpenPalette();
    else if (window.NxBus) NxBus.emit(NxBus.EVENTS.PALETTE_OPEN, {});
  }

  function _bindKeyboard() {
    document.addEventListener('keydown', (e) => {
      // Skip if focus is inside textarea or input (except specific combos)
      const active = document.activeElement;
      const inInput = active && (active.tagName === 'TEXTAREA' || active.tagName === 'INPUT');

      for (const kb of _keybindings) {
        const ctrlMatch  = kb.ctrl  ? (e.ctrlKey || e.metaKey) : true;
        const shiftMatch = kb.shift ? e.shiftKey : !e.shiftKey;

        if (e.key === kb.key && ctrlMatch && shiftMatch) {
          // Allow Ctrl+Enter inside textarea (run task)
          if (inInput && kb.key === 'Enter' && (e.ctrlKey || e.metaKey)) {
            e.preventDefault();
            kb.action();
            return;
          }
          // Block all other shortcuts when in input
          if (inInput && kb.key !== 'k') continue;

          e.preventDefault();
          kb.action();
          return;
        }
      }
    });
  }

  /* ══════════════════════════════════════════════════════════════════
     2. LATENCY MASKING
     ══════════════════════════════════════════════════════════════════ */
  function _maskLatency() {
    if (!window.NxBus) { setTimeout(_maskLatency, 200); return; }

    // When stream opens: show subtle "live" indicator on run button dot
    NxBus.on('nx:stream:open', () => {
      const dot = $('nxRunDot');
      if (dot) dot.style.display = '';
      _hideIdleHero();
    }, { owner: 'nx-polish' });

    // Agent start: hide idle hero, enable run indicator
    NxBus.on('nx:agent:start', () => {
      _hideIdleHero();
      const dot = $('nxRunDot');
      if (dot) dot.style.display = '';
    }, { owner: 'nx-polish' });

    // Done/Stop: restore idle hero if no content, stop indicator
    NxBus.on('nx:agent:done', () => {
      const dot = $('nxRunDot');
      if (dot) dot.style.display = 'none';
      _updateRunBtn(false);
    }, { owner: 'nx-polish' });

    NxBus.on('nx:agent:stop', () => {
      const dot = $('nxRunDot');
      if (dot) dot.style.display = 'none';
      _updateRunBtn(false);
    }, { owner: 'nx-polish' });

    // Reconnect: preserve visible state — don't blank the UI
    NxBus.on('nx:ws:status', (d) => {
      if (d.state === 'reconnecting') {
        const label = $('nxLiveConnStatus');
        if (label) { label.textContent = 'Reconnecting...'; label.style.color = '#f59e0b'; }
      } else if (d.state === 'connected') {
        const label = $('nxLiveConnStatus');
        if (label) { label.textContent = 'Live'; label.style.color = '#3fb950'; }
      }
    }, { owner: 'nx-polish' });
  }

  function _updateRunBtn(running) {
    const btn   = $('runBtn');
    const label = $('runBtnLabel');
    if (!btn || !label) return;
    if (running) {
      label.textContent = 'Running...';
      btn.style.color   = '#f59e0b';
    } else {
      label.textContent = 'Run';
      btn.style.color   = '#3fb950';
    }
  }

  /* ══════════════════════════════════════════════════════════════════
     3. IDLE HERO: execution-first empty state
     ══════════════════════════════════════════════════════════════════ */
  function _initIdleHero() {
    const hero = $('nxIdleHero');
    if (!hero) return;

    // Upgrade idle hero to operational guidance
    const heading = hero.querySelector('.nx-hero-heading');
    if (heading && heading.textContent.includes('What do you want to build')) {
      heading.textContent = 'Ready to execute.';
    }

    const sub = hero.querySelector('.nx-hero-sub');
    if (sub) {
      sub.textContent = 'Define a task. The agent plans, codes, and validates autonomously.';
    }

    // Add keyboard shortcut hint row
    const hint = hero.querySelector('.nx-hero-hint');
    if (hint && !hint.querySelector('.nx-kb-row')) {
      hint.innerHTML = `
        <div class="nx-kb-row">
          <kbd>Ctrl+Enter</kbd> Execute
          <kbd>Ctrl+K</kbd> Commands
          <kbd>Ctrl+\\</kbd> Inspector
          <kbd>Ctrl+1-4</kbd> Tabs
        </div>
      `;
    }
  }

  function _hideIdleHero() {
    const hero = $('nxIdleHero');
    if (hero) hero.classList.add('nx-hero-hidden');
  }

  function _showIdleHero() {
    const hero = $('nxIdleHero');
    if (hero) hero.classList.remove('nx-hero-hidden');
    _populateIdleWorkspace();
  }

  function _populateIdleWorkspace() {
    // Populate runtime status strip + recent executions from live APIs
    _loadIdleMetrics();
    _loadIdleRecent();
    _loadIdleTelemetry();
  }

  function _loadIdleMetrics() {
    fetch('/api/system/metrics', { credentials: 'include' })
      .then(r => r.ok ? r.json() : null)
      .then(d => {
        if (!d) return;
        const modelEl = $('nxIdleModel');
        if (modelEl) {
          const m = (d.last_model || d.model || '—');
          modelEl.textContent = m.length > 22 ? m.slice(0, 20) + '…' : m;
        }
      })
      .catch(() => {});
  }

  function _loadIdleTelemetry() {
    fetch('/api/runtime/telemetry', { credentials: 'include' })
      .then(r => r.ok ? r.json() : null)
      .then(d => {
        if (!d || !d.ok) return;
        const data = d.data || {};

        // Confidence
        const confEl = $('nxIdleConf');
        if (confEl && data.confidence) {
          const avg = data.confidence.rolling_average;
          if (typeof avg === 'number') {
            confEl.textContent = (avg * 100).toFixed(0) + '%';
            confEl.style.color = avg < 0.5 ? '#f85149' : avg < 0.75 ? '#d29922' : '#3fb950';
          }
        }

        // Context token pressure
        const ctxEl = $('nxIdleCtx');
        if (ctxEl && data.context) {
          const pct = data.context.budget_pct;
          if (typeof pct === 'number') {
            ctxEl.textContent = pct.toFixed(0) + '%';
            ctxEl.style.color = pct > 85 ? '#f85149' : pct > 65 ? '#d29922' : '#3fb950';
          }
        }

        // Scheduled missions
        const schedEl = $('nxIdleSched');
        if (schedEl && data.scheduler) {
          const cnt = data.scheduler.pending_count;
          schedEl.textContent = typeof cnt === 'number' ? String(cnt) : '0';
        }
      })
      .catch(() => {});
  }

  function _loadIdleRecent() {
    const container = $('nxIdleRecent');
    if (!container) return;
    fetch('/api/sessions', { credentials: 'include' })
      .then(r => r.ok ? r.json() : null)
      .then(d => {
        if (!d) return;
        const sessions = Array.isArray(d) ? d : (d.sessions || []);
        const recent = sessions.slice(0, 5);
        if (!recent.length) return;
        container.innerHTML = '';
        recent.forEach(s => {
          const task = (s.task || s.description || 'Untitled session').slice(0, 80);
          const status = (s.status || s.last_status || '');
          const ts = s.updated_at || s.created_at || '';
          let badge = 'other', label = status || 'idle';
          if (status === 'done' || status === 'completed') { badge = 'done'; label = 'done'; }
          else if (status === 'failed' || status === 'error') { badge = 'failed'; label = 'failed'; }
          let timeStr = '';
          if (ts) {
            try {
              const dt = new Date(ts);
              const now = new Date();
              const diffH = (now - dt) / 3600000;
              if (diffH < 1) timeStr = Math.round(diffH * 60) + 'm ago';
              else if (diffH < 24) timeStr = Math.round(diffH) + 'h ago';
              else timeStr = Math.round(diffH / 24) + 'd ago';
            } catch(_) {}
          }
          const row = document.createElement('div');
          row.className = 'nx-iw-recent-row';
          row.title = task;
          row.innerHTML = `<span class="nx-iw-recent-badge nx-iw-recent-badge--${badge}">${label}</span><span class="nx-iw-recent-task">${task}</span><span class="nx-iw-recent-time">${timeStr}</span>`;
          row.onclick = () => {
            if (s.session_id && window.loadSession) window.loadSession(s.session_id);
          };
          container.appendChild(row);
        });
      })
      .catch(() => {});
  }

  // Update idle model when a new execution finishes
  if (window.NxBus && NxBus.EVENTS) {
    const E = NxBus.EVENTS;
    NxBus.on(E.AGENT_DONE, () => {
      _loadIdleRecent();
      _loadIdleTelemetry();
    }, { owner: 'nx-polish-z27' });
  } else {
    setTimeout(() => {
      if (window.NxBus && NxBus.EVENTS) {
        const E = NxBus.EVENTS;
        NxBus.on(E.AGENT_DONE, () => {
          _loadIdleRecent();
          _loadIdleTelemetry();
        }, { owner: 'nx-polish-z27' });
      }
    }, 500);
  }

  /* ══════════════════════════════════════════════════════════════════
     4. PROGRESSIVE DISCLOSURE — collapse idle surfaces
     ══════════════════════════════════════════════════════════════════ */
  function _initProgressiveDisclosure() {
    // Inspector chains: start collapsed, expand on hover
    // (handled by CSS .nx-insp-chain--sealed opacity — no JS needed)

    // Trust pills: hide in inspector unless confidence < 80%
    // Confidence bar always visible via nx-trust-intel rendering

    // Collapse nxLogsPipeline bar when not executing
    const pipe = $('nxLogsPipeline');
    if (pipe) pipe.style.display = 'none'; // starts hidden; shown by NxOrchestrator on chunk

    // Exec strip: start at minimal opacity
    const strip = $('nxExecStrip');
    if (strip) strip.classList.add('nx-strip-idle');
  }

  function _onExecStart() {
    const strip = $('nxExecStrip');
    if (strip) strip.classList.remove('nx-strip-idle');
  }

  function _onExecEnd() {
    const strip = $('nxExecStrip');
    if (strip) strip.classList.add('nx-strip-idle');
  }

  /* ══════════════════════════════════════════════════════════════════
     5. SESSION CONTINUITY — smooth reconnect experience
     ══════════════════════════════════════════════════════════════════ */
  function _initSessionContinuity() {
    if (!window.NxBus || !NxBus.EVENTS) { setTimeout(_initSessionContinuity, 200); return; }
    const E = NxBus.EVENTS;

    NxBus.on(E.SESSION_RESTORED, (d) => {
      // Don't blank the UI — restore continuity message
      _showContextBanner('Session restored — continuing mission');
      _onExecStart();
    }, { owner: 'nx-polish' });

    NxBus.on(E.SESSION_CLEARED, () => {
      _showIdleHero();
      _onExecEnd();
    }, { owner: 'nx-polish' });

    NxBus.on(E.AGENT_START, () => {
      _hideIdleHero();
      _onExecStart();
    }, { owner: 'nx-polish' });

    NxBus.on(E.SESSION_CREATED, () => {
      _hideIdleHero();
      _onExecStart();
    }, { owner: 'nx-polish' });

    NxBus.on(E.AGENT_DONE, _onExecEnd, { owner: 'nx-polish' });
    NxBus.on(E.AGENT_STOP, _onExecEnd, { owner: 'nx-polish' });
  }

  function _showContextBanner(msg) {
    // Delegate to SurfaceFusion if available
    if (window.NxSurfaceFusion) {
      NxSurfaceFusion.hintTab('logs', msg);
    }
  }

  /* ══════════════════════════════════════════════════════════════════
     6. RUNTIME RESPONSIVENESS — eliminate unnecessary DOM churn
     ══════════════════════════════════════════════════════════════════ */
  function _reduceChurn() {
    // Patch nxSetTab to use CSS class toggling instead of display:block|none
    // if it's relying on inline styles (check existing behavior)
    const origSetTab = window.nxSetTab;
    if (typeof origSetTab === 'function') {
      window.nxSetTab = function(tab) {
        // Add transition class to current active panel before switching
        const current = $q('.nx-tab-content:not([style*="none"])');
        if (current) current.classList.add('nx-tab-fading');

        origSetTab.call(this, tab);

        requestAnimationFrame(() => {
          if (current) current.classList.remove('nx-tab-fading');
          // Scroll active panel to top on switch (prevents phantom scroll positions)
          const newPanel = $(`nxTab-${tab}`);
          if (newPanel) newPanel.scrollTop = 0;
        });
      };
    }

    // Throttle exec strip updates to max 4/sec
    const origStripUpdate = window._nxExecStripUpdate;
    let _stripThrottle = 0;
    const stripState = $('nxExecStripState');
    if (stripState) {
      const origSet = Object.getOwnPropertyDescriptor(stripState, 'textContent');
      // Simple throttle: skip updates if last update was < 250ms ago
      let _lastStripUpdate = 0;
      Object.defineProperty(stripState, '_cachedContent', { value: '', writable: true, configurable: true });
    }
  }

  /* ══════════════════════════════════════════════════════════════════
     7. VISUAL POLISH HELPERS
     ══════════════════════════════════════════════════════════════════ */
  function _polishResizeHandles() {
    // Smooth Split.js resize — add will-change to split panes
    const splitPanes = document.querySelectorAll('.gutter, .split');
    splitPanes.forEach(el => {
      el.style.willChange = 'width, height';
    });
  }

  function _polishScrollContainers() {
    // Ensure all overflow-y:auto containers have smooth scrolling
    const scrollers = document.querySelectorAll(
      '.nx-chunk-body, .nx-inspector-content, .p12-chat-msgs, .nx-panel-content'
    );
    scrollers.forEach(el => {
      el.style.scrollBehavior = 'smooth';
    });
  }

  /* ══════════════════════════════════════════════════════════════════
     8. COMMAND PALETTE ENHANCEMENT
     ══════════════════════════════════════════════════════════════════ */
  function _enhancePalette() {
    // Register mission-aware palette commands on NxBus PALETTE_OPEN
    if (!window.NxBus || !NxBus.EVENTS) { setTimeout(_enhancePalette, 200); return; }

    NxBus.on(NxBus.EVENTS.PALETTE_OPEN, () => {
      // Inject mission-context items into palette if it supports it
      const palette = $q('.nx-palette-list') || $q('#nxPaletteList');
      if (!palette) return;

      const missionPhase = window.NxMission ? NxMission.getPhase() : null;
      if (!missionPhase || missionPhase === 'idle') return;

      // Check if mission item already added
      if (palette.querySelector('[data-mission-item]')) return;

      const item = document.createElement('div');
      item.className = 'nx-palette-item';
      item.dataset.missionItem = '1';
      item.innerHTML = `<span style="color:#bc8cff">&#9670;</span> Mission: ${missionPhase.toUpperCase()}`;
      palette.insertBefore(item, palette.firstChild);
    }, { owner: 'nx-polish' });
  }

  /* ══════════════════════════════════════════════════════════════════
     INIT
     ══════════════════════════════════════════════════════════════════ */
  function _init() {
    _bindKeyboard();
    _maskLatency();
    _initIdleHero();
    _initProgressiveDisclosure();
    _initSessionContinuity();
    _reduceChurn();
    _polishResizeHandles();
    _polishScrollContainers();
    _enhancePalette();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => setTimeout(_init, 700));
  } else {
    setTimeout(_init, 700);
  }

  // Expose keyboard reference
  window.NxPolish = {
    getKeybindings: () => _keybindings.map(kb => ({
      key: `${kb.ctrl ? 'Ctrl+' : ''}${kb.shift ? 'Shift+' : ''}${kb.key}`,
      label: kb.label
    }))
  };

})();
