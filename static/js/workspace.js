/**
 * workspace.js â€” Nexora Dockable Workspace Controller  v2
 * =========================================================
 * Single canonical controller for all panel/tab layout state.
 *
 * Architecture:
 *   - WS_PANELS: registry of all panels with metadata
 *   - NxWorkspace: singleton controller (window.NxWorkspace)
 *   - Persists to localStorage under key 'nx_workspace_v2'
 *   - More â–¾ menu reflects closed secondary panels
 *   - Panels: resizable (drag handle), closable, restorable
 *   - Docking zones: left | center | right | bottom
 *   - Keyboard: Ctrl+Shift+E (left), Ctrl+Shift+I (right)
 */

'use strict';

(function () {

  // â”€â”€â”€ Panel Registry â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
  const WS_PANELS = {
    left: {
      id: 'left', label: 'AI Thinking', zone: 'left',
      closable: true, defaultW: 240, minW: 0, maxW: 480, domId: 'nxLeft',
    },
    right: {
      id: 'right', label: 'Inspector', zone: 'right',
      closable: true, defaultW: 290, minW: 0, maxW: 480, domId: 'nxRight',
    },
    // â”€â”€ Center tabs (secondary â€” closable) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    logs:      { id: 'logs',      label: 'Logs',         zone: 'tab', closable: false, icon: 'ðŸ“‹' },
    preview:   { id: 'preview',   label: 'Preview',      zone: 'tab', closable: true,  icon: 'ðŸ‘'  },
    code:      { id: 'code',      label: 'Code',         zone: 'tab', closable: true,  icon: 'ðŸ“„' },
    chat:      { id: 'chat',      label: 'Chat',         zone: 'tab', closable: false, icon: 'ðŸ’¬' },
    live:      { id: 'live',      label: 'Live',         zone: 'tab', closable: true,  icon: 'âš¡' },
    terminal:  { id: 'terminal',  label: 'Terminal',     zone: 'tab', closable: true,  icon: 'ðŸ’»' },
    metrics:   { id: 'metrics',   label: 'Metrics',      zone: 'tab', closable: true,  icon: 'ðŸ“Š' },
    agents:    { id: 'agents',    label: 'Agents',       zone: 'tab', closable: true,  icon: 'ðŸ¤–' },
    timeline:  { id: 'timeline',  label: 'Timeline',     zone: 'tab', closable: true,  icon: 'ðŸ“…' },
    steps:     { id: 'steps',     label: 'Steps',        zone: 'tab', closable: true,  icon: 'ðŸ§' },
    learning:  { id: 'learning',  label: 'Learning',     zone: 'tab', closable: true,  icon: 'ðŸ“Š' },
    goals:     { id: 'goals',     label: 'Goals',        zone: 'tab', closable: true,  icon: 'ðŸŽ¯' },
    graph:     { id: 'graph',     label: 'Exec Graph',   zone: 'tab', closable: true,  icon: 'ðŸ§©' },
    scheduler: { id: 'scheduler', label: 'Scheduler',    zone: 'tab', closable: true,  icon: 'â±'  },
  };

  const STORE_KEY = 'nx_workspace_v2';
  const DEFAULT_STATE = {
    leftW:       240,
    rightW:      290,
    bottomH:     220,
    leftOpen:    false,
    rightOpen:   true,
    bottomOpen:  false,
    closedTabs:  [],
    activeTab:   'logs',
  };

  // â”€â”€â”€ State â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
  let _state = { ...DEFAULT_STATE };
  let _initialized = false;

  // â”€â”€â”€ Persistence â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
  function _load() {
    try {
      const raw = localStorage.getItem(STORE_KEY);
      if (raw) _state = { ...DEFAULT_STATE, ...JSON.parse(raw) };
    } catch (_) {}
  }
  function _save() {
    try { localStorage.setItem(STORE_KEY, JSON.stringify(_state)); } catch (_) {}
  }

  // â”€â”€â”€ DOM helpers â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
  function _el(id) { return document.getElementById(id); }

  function _applyPanelWidths() {
    const root = document.documentElement;
    root.style.setProperty('--bottomH', (_state.bottomOpen ? _state.bottomH : 0) + 'px');
    _updatePanelHeaderBtns();
  }

  // â”€â”€â”€ Panel header Ã— buttons â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
  function _updatePanelHeaderBtns() {
    // Left panel collapse button label
    const leftColBtn = _el('nxLeft')?.querySelector('.nx-tiny-btn[onclick*="nxToggleLeft"]');
    if (leftColBtn) leftColBtn.textContent = _state.leftOpen ? 'â—‚' : 'â–¸';

    // Right panel header â€” inject Ã— button if missing
    const rightHdr = _el('nxRight')?.querySelector('.nx-panel-hdr .nx-panel-actions');
    if (rightHdr && !rightHdr.querySelector('.nx-ws-close-btn')) {
      const btn = document.createElement('button');
      btn.className = 'nx-tiny-btn nx-ws-close-btn';
      btn.title = 'Close panel (Ctrl+Shift+I to reopen)';
      btn.innerHTML = 'âœ•';
      btn.addEventListener('click', () => NxWorkspace.toggleRight());
      rightHdr.insertBefore(btn, rightHdr.firstChild);
    }
  }

  // â”€â”€â”€ Bottom dock â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
  function _ensureBottomDock() {
    if (_el('nxBottomDock')) return;
    const body = _el('nxBody') || document.querySelector('.nx-body');
    if (!body) return;

    // Dock container
    const dock = document.createElement('div');
    dock.id = 'nxBottomDock';
    dock.style.cssText = `
      display: flex;
      flex-direction: column;
      background: var(--panel, #111118);
      border-top: 1px solid var(--panel-border, #1e1e2e);
      flex-shrink: 0;
      overflow: hidden;
      height: var(--bottomH, 0px);
      transition: height .18s ease;
    `;

    // Drag handle (top)
    const handle = document.createElement('div');
    handle.id = 'nxDivBottom';
    handle.style.cssText = `
      height: 4px;
      cursor: row-resize;
      background: transparent;
      flex-shrink: 0;
    `;
    handle.title = 'Drag to resize';
    handle.addEventListener('mouseenter', () => { handle.style.background = 'var(--accent)'; });
    handle.addEventListener('mouseleave', () => { handle.style.background = 'transparent'; });
    dock.appendChild(handle);

    // Dock header
    const hdr = document.createElement('div');
    hdr.style.cssText = `
      display: flex;
      align-items: center;
      gap: 8px;
      padding: 0 12px;
      height: 32px;
      border-bottom: 1px solid var(--panel-border);
      flex-shrink: 0;
    `;
    hdr.innerHTML = `
      <span style="font-size:11px;font-weight:600;color:var(--text-muted);text-transform:uppercase;letter-spacing:.06em;flex:1">
        Panel Dock
      </span>
      <button class="nx-tiny-btn" onclick="NxWorkspace.toggleBottom()" title="Close dock">âœ•</button>
    `;
    dock.appendChild(hdr);

    // Dock content area â€” reuse existing terminal/logs from tab content
    const content = document.createElement('div');
    content.id = 'nxBottomContent';
    content.style.cssText = 'flex:1;overflow:auto;';
    dock.appendChild(content);

    body.appendChild(dock);
    _setupBottomDragHandle(handle);
  }

  function _setupBottomDragHandle(handle) {
    handle.addEventListener('mousedown', e => {
      e.preventDefault();
      const startY  = e.clientY;
      const startH  = _state.bottomH;
      handle.style.background = 'var(--accent)';
      document.body.classList.add('nx-resizing-row');

      const onMove = me => {
        const delta = startY - me.clientY;          // drag up = increase height
        _state.bottomH = Math.max(60, Math.min(600, startH + delta));
        _state.bottomOpen = _state.bottomH > 40;
        _applyPanelWidths();
        if (window.NX) window.NX.bottomH = _state.bottomH;
      };
      const onUp = () => {
        handle.style.background = 'transparent';
        document.body.classList.remove('nx-resizing-row');
        document.removeEventListener('mousemove', onMove);
        document.removeEventListener('mouseup',   onUp);
        _save();
      };
      document.addEventListener('mousemove', onMove);
      document.addEventListener('mouseup',   onUp);
    });
  }

  // â”€â”€â”€ Tab visibility management â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
  function _isTabClosed(id) { return _state.closedTabs.includes(id); }

  function _applyTabVisibility() {
    const tabBar = _el('nxTabBar');
    if (!tabBar) return;

    // Show/hide primary tab buttons
    tabBar.querySelectorAll('.nx-tab:not(.secondary)').forEach(btn => {
      const tid = btn.dataset.nxtab;
      if (!tid) return;
      const panel = WS_PANELS[tid];
      if (!panel || panel.zone !== 'tab') return;
      const hidden = panel.closable && _isTabClosed(tid);
      btn.style.display = hidden ? 'none' : '';
    });

    // Add close (Ã—) buttons to closable primary tabs if not already present
    tabBar.querySelectorAll('.nx-tab:not(.secondary)').forEach(btn => {
      const tid = btn.dataset.nxtab;
      if (!tid) return;
      const panel = WS_PANELS[tid];
      if (!panel || !panel.closable || btn.querySelector('.nx-tab-close')) return;
      if (btn.style.display === 'none') return;
      const x = document.createElement('span');
      x.className = 'nx-tab-close';
      x.title = 'Close panel (restore from More â–¾)';
      x.textContent = 'Ã—';
      x.addEventListener('click', e => { e.stopPropagation(); NxWorkspace.closeTab(tid); });
      btn.appendChild(x);
    });

    _rebuildMoreMenu();
  }

  function _rebuildMoreMenu() {
    const dd = _el('nxMoreDropdown');
    if (!dd) return;

    // Remove all injected "restore" items
    dd.querySelectorAll('[data-ws-restore]').forEach(el => el.remove());

    const closed = _state.closedTabs.filter(id => WS_PANELS[id]);
    if (closed.length > 0) {
      const sep = document.createElement('div');
      sep.dataset.wsRestore = '1';
      sep.style.cssText = 'border-top:1px solid var(--panel-border,#1e1e2e);margin:3px 0;padding:4px 10px;font-size:9px;color:var(--text-muted,#7878a0);text-transform:uppercase;letter-spacing:.06em;pointer-events:none';
      sep.textContent = 'Restore panel';
      dd.appendChild(sep);

      closed.forEach(id => {
        const p = WS_PANELS[id];
        const item = document.createElement('div');
        item.className = 'nx-more-item';
        item.dataset.wsRestore = '1';
        item.dataset.nxtab = id;
        item.innerHTML = `<span style="opacity:.6;margin-right:4px">${p.icon || ''}</span>${p.label} <span style="float:right;font-size:10px;color:var(--text-muted,#7878a0)">â†© restore</span>`;
        item.addEventListener('click', () => {
          NxWorkspace.openTab(id);
          if (typeof nxCloseMore === 'function') nxCloseMore();
        });
        dd.appendChild(item);
      });
    }

    // â”€â”€ Workspace panel controls section â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    // Ensure the existing Reset Layout item is always present (it might not be
    // if the More dropdown was dynamically generated). Only inject once.
    if (!dd.querySelector('[data-ws-panel-controls]')) {
      const ctrlSep = document.createElement('div');
      ctrlSep.dataset.wsPanelControls = '1';
      ctrlSep.style.cssText = 'border-top:1px solid var(--panel-border,#1e1e2e);margin:3px 0;padding:4px 10px;font-size:9px;color:var(--text-muted,#7878a0);text-transform:uppercase;letter-spacing:.06em;pointer-events:none';
      ctrlSep.textContent = 'Workspace';
      dd.appendChild(ctrlSep);

      const mkItem = (label, fn, title) => {
        const el = document.createElement('div');
        el.className = 'nx-more-item';
        el.dataset.wsPanelControls = '1';
        el.title = title || '';
        el.innerHTML = label;
        el.addEventListener('click', () => { fn(); if (typeof nxCloseMore === 'function') nxCloseMore(); });
        dd.appendChild(el);
        return el;
      };

      mkItem('â—‚ AI Thinking panel', () => NxWorkspace.toggleLeft(),  'Ctrl+Shift+E');
      mkItem('â–¸ Inspector panel',   () => NxWorkspace.toggleRight(), 'Ctrl+Shift+I');
      mkItem('â–² Bottom dock',        () => NxWorkspace.toggleBottom(),'');
      mkItem('â†º Reset layout',       () => NxWorkspace.resetLayout(), '');
    }
  }

  // â”€â”€â”€ Left/Right panel drag handles â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
  function _setupSplitGrid() {
    if (typeof Split !== 'function') {
      console.warn('[NxWorkspace] Split.js not found');
      return;
    }

    const totalW = window.innerWidth || 1200;
    let lw = _state.leftOpen ? ((_state.leftW || 240) / totalW * 100) : 0;
    let rw = _state.rightOpen ? ((_state.rightW || 290) / totalW * 100) : 0;
    let cw = 100 - lw - rw;

    window.nxSplit = Split(['#nxLeft', '#nxCenter', '#nxRight'], {
      sizes: [lw, cw, rw],
      minSize: [0, 300, 0],
      gutterSize: 4,
      snapOffset: 0,
      onDragEnd: function(sizes) {
        _state.leftOpen = sizes[0] > 1;
        _state.rightOpen = sizes[2] > 1;
        _state.leftW = (sizes[0] / 100) * window.innerWidth;
        _state.rightW = (sizes[2] / 100) * window.innerWidth;
        if (window.NX) {
          window.NX.leftW = _state.leftW;
          window.NX.rightW = _state.rightW;
          window.NX.leftOpen = _state.leftOpen;
          window.NX.rightOpen = _state.rightOpen;
        }
        _save();
        _updatePanelHeaderBtns();
        window.dispatchEvent(new Event('resize'));
      }
    });
  }

  // â”€â”€â”€ Public API â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
  const NxWorkspace = {

    init() {
      if (_initialized) return;
      _initialized = true;
      _load();

      // Sync from legacy NX global if present
      if (window.NX) {
        if (Number.isFinite(window.NX.leftW))           _state.leftW    = window.NX.leftW;
        if (Number.isFinite(window.NX.rightW))          _state.rightW   = window.NX.rightW;
        if (typeof window.NX.leftOpen  === 'boolean')   _state.leftOpen  = window.NX.leftOpen;
        if (typeof window.NX.rightOpen === 'boolean')   _state.rightOpen = window.NX.rightOpen;
      }

      _applyPanelWidths();
      _applyTabVisibility();
      _setupSplitGrid();
      _ensureBottomDock();
      _injectStyles();

      console.log('[NxWorkspace v2] Initialized. State:', JSON.stringify(_state));
    },

    // â”€â”€ Panel width toggles â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    toggleLeft() {
      _state.leftOpen = !_state.leftOpen;
      _state.leftW = _state.leftOpen ? Math.max(_state.leftW || 240, 120) : 0;
      if (window.NX) { window.NX.leftW = _state.leftW; window.NX.leftOpen = _state.leftOpen; }
      
      if (window.nxSplit) {
        const totalW = window.innerWidth || 1200;
        let lw = _state.leftOpen ? (_state.leftW / totalW * 100) : 0;
        let sizes = window.nxSplit.getSizes();
        window.nxSplit.setSizes([lw, 100 - lw - sizes[2], sizes[2]]);
      }
      
      _applyPanelWidths();
      _save();
      window.dispatchEvent(new Event('resize'));
    },

    toggleRight() {
      _state.rightOpen = !_state.rightOpen;
      _state.rightW = _state.rightOpen ? Math.max(_state.rightW || 290, 120) : 0;
      if (window.NX) { window.NX.rightW = _state.rightW; window.NX.rightOpen = _state.rightOpen; }
      
      if (window.nxSplit) {
        const totalW = window.innerWidth || 1200;
        let rw = _state.rightOpen ? (_state.rightW / totalW * 100) : 0;
        let sizes = window.nxSplit.getSizes();
        window.nxSplit.setSizes([sizes[0], 100 - sizes[0] - rw, rw]);
      }
      
      _applyPanelWidths();
      _save();
      window.dispatchEvent(new Event('resize'));
    },

    toggleBottom() {
      _state.bottomOpen = !_state.bottomOpen;
      _state.bottomH    = _state.bottomOpen ? Math.max(_state.bottomH || 220, 60) : 0;
      if (window.NX) window.NX.bottomH = _state.bottomH;
      _applyPanelWidths();
      _save();
      if (_state.bottomOpen && typeof nxEnsureTerminal === 'function') nxEnsureTerminal();
    },

    setLeftWidth(w) {
      _state.leftW    = w;
      _state.leftOpen = w > 20;
      if (window.NX) { window.NX.leftW = w; window.NX.leftOpen = _state.leftOpen; }
      
      if (window.nxSplit) {
        const totalW = window.innerWidth || 1200;
        let lw = _state.leftOpen ? (_state.leftW / totalW * 100) : 0;
        let sizes = window.nxSplit.getSizes();
        window.nxSplit.setSizes([lw, 100 - lw - sizes[2], sizes[2]]);
      }
      
      _applyPanelWidths();
      _save();
      window.dispatchEvent(new Event('resize'));
    },

    setRightWidth(w) {
      _state.rightW    = w;
      _state.rightOpen = w > 20;
      if (window.NX) { window.NX.rightW = w; window.NX.rightOpen = _state.rightOpen; }
      
      if (window.nxSplit) {
        const totalW = window.innerWidth || 1200;
        let rw = _state.rightOpen ? (_state.rightW / totalW * 100) : 0;
        let sizes = window.nxSplit.getSizes();
        window.nxSplit.setSizes([sizes[0], 100 - sizes[0] - rw, rw]);
      }
      
      _applyPanelWidths();
      _save();
      window.dispatchEvent(new Event('resize'));
    },

    // â”€â”€ Tab open / close â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    closeTab(id) {
      const panel = WS_PANELS[id];
      if (!panel || !panel.closable) return;
      if (!_state.closedTabs.includes(id)) _state.closedTabs.push(id);

      if (window.NX && window.NX.activeTab === id) {
        const next = _findNextOpenTab(id);
        if (typeof nxSetTab === 'function') nxSetTab(next);
      }
      _applyTabVisibility();
      _save();
      if (typeof nxToast === 'function') nxToast(`${panel.label} closed â€” restore from More â–¾`);
    },

    openTab(id) {
      const panel = WS_PANELS[id];
      if (!panel) return;
      _state.closedTabs = _state.closedTabs.filter(t => t !== id);
      _applyTabVisibility();
      _save();
      if (typeof nxSetTab === 'function') {
        nxSetTab(id);
        if (id === 'learning'  && typeof p15LoadDashboard === 'function') p15LoadDashboard();
        if (id === 'goals'     && typeof p16InitGoals     === 'function') p16InitGoals();
        if (id === 'graph'     && typeof p17InitGraph     === 'function') p17InitGraph();
        if (id === 'scheduler' && typeof p18InitScheduler === 'function') p18InitScheduler();
      }
    },

    // â”€â”€ Reset to default layout â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    resetLayout() {
      _state = { ...DEFAULT_STATE };
      if (window.NX) {
        window.NX.leftW   = _state.leftW;    window.NX.rightW  = _state.rightW;
        window.NX.leftOpen = _state.leftOpen; window.NX.rightOpen = _state.rightOpen;
      }
      _applyPanelWidths();
      _applyTabVisibility();
      _save();
      if (typeof nxSetTab    === 'function') nxSetTab('logs');
      if (typeof nxToast     === 'function') nxToast('Layout reset to default');
    },

    // â”€â”€ Query helpers â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    isTabOpen(id)  { return !_state.closedTabs.includes(id); },
    getState()     { return { ..._state }; },
    refreshMoreMenu() { _rebuildMoreMenu(); },
  };

  // â”€â”€â”€ Internal helpers â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
  function _findNextOpenTab(closedId) {
    for (const id of ['logs', 'chat', 'preview', 'code', 'terminal']) {
      if (id !== closedId && !_state.closedTabs.includes(id)) return id;
    }
    return 'logs';
  }

  function _injectStyles() {
    if (_el('nxWsStyles')) return;
    const s = document.createElement('style');
    s.id = 'nxWsStyles';
    s.textContent = `
      /* â”€â”€ Tab close button â”€â”€ */
      .nx-tab-close {
        display: inline-flex; align-items: center; justify-content: center;
        width: 14px; height: 14px; border-radius: 3px; margin-left: 4px;
        font-size: 12px; line-height: 1; color: var(--text-muted, #7878a0);
        opacity: 0; transition: opacity .15s, background .15s;
        cursor: pointer; vertical-align: middle;
      }
      .nx-tab:hover .nx-tab-close,
      .nx-tab.active .nx-tab-close { opacity: 1; }
      .nx-tab-close:hover { background: rgba(239,68,68,.2); color: var(--red, #ef4444); }

      /* â”€â”€ Resize cursor during drag â”€â”€ */
      body.nx-resizing     * { user-select: none !important; cursor: col-resize !important; }
      body.nx-resizing-row * { user-select: none !important; cursor: row-resize !important; }

      /* â”€â”€ More menu restore section â”€â”€ */
      [data-ws-restore].nx-more-item { color: var(--text-muted, #7878a0); font-style: italic; }
      [data-ws-restore].nx-more-item:hover {
        background: var(--hover, rgba(255,255,255,.05));
        color: var(--accent, #6366f1); font-style: normal;
      }

      /* â”€â”€ Smooth panel width transitions (not during drag) â”€â”€ */
      :root:not(.nx-resizing) #nxLeft,
      :root:not(.nx-resizing) #nxRight { transition: width .18s ease; }

      /* â”€â”€ Bottom dock â”€â”€ */
      #nxBottomDock { max-height: 60vh; }

      /* â”€â”€ Panel header Ã— close button â”€â”€ */
      .nx-ws-close-btn { margin-right: 2px; }
      .nx-ws-close-btn:hover { background: rgba(239,68,68,.18) !important; color: var(--red, #ef4444) !important; }
    `;
    document.head.appendChild(s);
  }

  // â”€â”€â”€ Boot â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
  window.NxWorkspace = NxWorkspace;

  if (Array.isArray(window.NX_BOOT_TASKS)) {
    window.NX_BOOT_TASKS.push(() => NxWorkspace.init());
  } else {
    if (document.readyState === 'loading') {
      document.addEventListener('DOMContentLoaded', () => NxWorkspace.init());
    } else {
      NxWorkspace.init();
    }
  }

  // â”€â”€â”€ Patch legacy global toggle functions â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
  if (Array.isArray(window.NX_BOOT_TASKS)) {
    window.NX_BOOT_TASKS.push(() => {
      window.nxToggleLeft    = () => NxWorkspace.toggleLeft();
      window.nxToggleRight   = () => NxWorkspace.toggleRight();
      window.nxToggleBottom  = () => NxWorkspace.toggleBottom();
      window.nxApplyLayout   = () => {
        if (window.NX) {
          _state.leftW      = window.NX.leftW    || 0;
          _state.rightW     = window.NX.rightW   || 0;
          _state.leftOpen   = window.NX.leftOpen  ?? _state.leftW  > 20;
          _state.rightOpen  = window.NX.rightOpen ?? _state.rightW > 20;
        }
        _applyPanelWidths();
      };
      window.p57UpdateLayout = (col, w) => {
        if (col === 'left') NxWorkspace.setLeftWidth(w);
        else                NxWorkspace.setRightWidth(w);
      };
      window.nxCloseTab    = id => NxWorkspace.closeTab(id);
      window.nxOpenTab     = id => NxWorkspace.openTab(id);
      window.nxResetLayout = () => NxWorkspace.resetLayout();
    });
  }

})();

/* ═══════════════════════════════════════════════════════════════════════════
   NxPresets — Workspace layout presets
   ═══════════════════════════════════════════════════════════════════════════ */
(function () {
  'use strict';

  // ── Preset definitions ────────────────────────────────────────────────────
  const PRESETS = {
    builder: {
      id: 'builder', label: 'Builder', icon: '🏗',
      leftOpen: false, rightOpen: true,  rightW: 290, bottomOpen: false,
      activeTab: 'code',
      closedTabs: ['steps', 'learning', 'goals', 'graph', 'scheduler'],
    },
    debug: {
      id: 'debug', label: 'Debug', icon: '🐛',
      leftOpen: true,  leftW: 220, rightOpen: true, rightW: 290, bottomOpen: true, bottomH: 200,
      activeTab: 'logs',
      closedTabs: ['preview', 'learning', 'goals', 'graph', 'scheduler'],
    },
    minimal: {
      id: 'minimal', label: 'Minimal', icon: '◻',
      leftOpen: false, rightOpen: false, bottomOpen: false,
      activeTab: 'logs',
      closedTabs: ['steps', 'learning', 'goals', 'graph', 'scheduler', 'metrics', 'agents', 'timeline'],
    },
    research: {
      id: 'research', label: 'Research', icon: '🔬',
      leftOpen: true, leftW: 260, rightOpen: true, rightW: 290, bottomOpen: false,
      activeTab: 'chat',
      closedTabs: ['code', 'preview', 'graph', 'scheduler'],
    },
  };

  const PRESET_KEY = 'nx_preset_v1';

  function applyPreset(id) {
    const p = PRESETS[id];
    if (!p || !window.NxWorkspace) return;

    // Transition class for smooth multi-panel animation
    document.body.classList.add('nx-preset-transitioning');

    const ws = window.NxWorkspace;
    const state = ws.getState();

    // Apply panel sizes
    if (p.leftOpen !== undefined) {
      if (p.leftOpen) ws.setLeftWidth(p.leftW || state.leftW || 240);
      else ws.setLeftWidth(0);
    }
    if (p.rightOpen !== undefined) {
      if (p.rightOpen) ws.setRightWidth(p.rightW || state.rightW || 290);
      else ws.setRightWidth(0);
    }
    if (p.bottomOpen !== undefined) {
      const s = ws.getState();
      if (p.bottomOpen !== s.bottomOpen) ws.toggleBottom();
      if (p.bottomOpen && p.bottomH) {
        document.documentElement.style.setProperty('--bottomH', p.bottomH + 'px');
      }
    }

    // Restore all tabs first, then close preset's closed list
    const allClosable = ['preview','code','live','terminal','metrics','agents','timeline','steps','learning','goals','graph','scheduler'];
    allClosable.forEach(tid => {
      if (!ws.isTabOpen(tid)) ws.openTab(tid);
    });
    (p.closedTabs || []).forEach(tid => ws.closeTab(tid));

    // Switch tab
    if (p.activeTab && typeof nxSetTab === 'function') nxSetTab(p.activeTab);

    // Store active preset
    try { localStorage.setItem(PRESET_KEY, id); } catch(_) {}

    // Update preset bar visual
    document.querySelectorAll('.nx-preset-btn').forEach(btn => {
      btn.classList.toggle('active', btn.dataset.preset === id);
    });

    setTimeout(() => document.body.classList.remove('nx-preset-transitioning'), 500);

    if (typeof nxToast === 'function') nxToast(`${p.icon} ${p.label} mode`);
  }

  function _injectPresetBar() {
    const tabBar = document.getElementById('nxTabBar');
    if (!tabBar || document.getElementById('nxPresetBar')) return;

    const bar = document.createElement('div');
    bar.id = 'nxPresetBar';
    bar.className = 'nx-preset-bar';
    bar.style.cssText = 'margin-left:auto;display:flex;align-items:center;gap:4px;padding-right:4px;';

    Object.values(PRESETS).forEach(p => {
      const btn = document.createElement('button');
      btn.className = 'nx-preset-btn';
      btn.dataset.preset = p.id;
      btn.title = `${p.label} workspace`;
      btn.innerHTML = `${p.icon} ${p.label}`;
      btn.addEventListener('click', () => applyPreset(p.id));
      bar.appendChild(btn);
    });

    // Restore last active
    try {
      const last = localStorage.getItem(PRESET_KEY);
      if (last) {
        const btn = bar.querySelector(`[data-preset="${last}"]`);
        if (btn) btn.classList.add('active');
      }
    } catch(_) {}

    tabBar.appendChild(bar);
  }

  // Expose globally
  window.NxPresets = { apply: applyPreset, PRESETS };
  window.nxApplyPreset = applyPreset;

  if (Array.isArray(window.NX_BOOT_TASKS)) {
    window.NX_BOOT_TASKS.push(_injectPresetBar);
  } else {
    document.addEventListener('DOMContentLoaded', _injectPresetBar);
  }
})();


/* ═══════════════════════════════════════════════════════════════════════════
   NxEmptyStates — Actionable empty panel states
   ═══════════════════════════════════════════════════════════════════════════ */
(function () {
  'use strict';

  const EMPTY_STATES = {
    logs: {
      icon: '🤖', title: 'No agent activity yet',
      sub: 'Run a task and the AI agent\'s live output will appear here.',
      actions: [
        { label: '▶ Run Agent', fn: () => typeof nxRunOrStop === 'function' && nxRunOrStop(), primary: true },
        { label: 'Open Chat', fn: () => typeof nxSetTab === 'function' && nxSetTab('chat') },
      ],
    },
    preview: {
      icon: '👁', title: 'Nothing to preview yet',
      sub: 'Build a web app and the live preview will appear here.',
      actions: [
        { label: 'Switch to Logs', fn: () => typeof nxSetTab === 'function' && nxSetTab('logs') },
        { label: 'Open Code', fn: () => typeof nxSetTab === 'function' && nxSetTab('code'), primary: true },
      ],
    },
    code: {
      icon: '📄', title: 'No file open',
      sub: 'Run a task to generate code, or open a file from the Inspector.',
      actions: [
        { label: '▶ Run Agent', fn: () => typeof nxRunOrStop === 'function' && nxRunOrStop(), primary: true },
        { label: 'Open Inspector', fn: () => window.NxWorkspace && NxWorkspace.toggleRight() },
      ],
    },
    terminal: {
      icon: '💻', title: 'Terminal not started',
      sub: 'The terminal will launch when your agent runs a command.',
      actions: [
        { label: '▶ Run Agent', fn: () => typeof nxRunOrStop === 'function' && nxRunOrStop(), primary: true },
      ],
    },
  };

  function renderEmptyState(tabId, containerEl) {
    const def = EMPTY_STATES[tabId];
    if (!def || !containerEl) return false;

    // Only render if the container is visually empty
    if (containerEl.children.length > 0 &&
        !containerEl.querySelector('.nx-empty-state')) return false;
    if (containerEl.querySelector('.nx-empty-state')) return true;

    const el = document.createElement('div');
    el.className = 'nx-empty-state';
    el.innerHTML = `
      <div class="nx-empty-icon">${def.icon}</div>
      <div class="nx-empty-title">${def.title}</div>
      <div class="nx-empty-sub">${def.sub}</div>
      <div class="nx-empty-actions">
        ${def.actions.map((a, i) =>
          `<button class="nx-empty-btn${a.primary ? '' : ' secondary'}" data-empty-action="${tabId}-${i}">${a.label}</button>`
        ).join('')}
      </div>`;

    def.actions.forEach((a, i) => {
      const btn = el.querySelector(`[data-empty-action="${tabId}-${i}"]`);
      if (btn) btn.addEventListener('click', a.fn);
    });

    containerEl.appendChild(el);
    return true;
  }

  window.NxEmptyStates = { render: renderEmptyState, EMPTY_STATES };
})();


/* ═══════════════════════════════════════════════════════════════════════════
   NxPaletteGroups — Grouped command palette items
   Patches window.NX_PALETTE_ITEMS with grouped structure
   ═══════════════════════════════════════════════════════════════════════════ */
(function () {
  'use strict';

  const GROUPED_ITEMS = [
    // ── Run actions ────────────────────────────────────────────────────────
    { group: 'Run',     icon: '▶', label: 'Run Task',         hint: 'Ctrl+Enter', action: () => typeof nxRunOrStop === 'function' && nxRunOrStop() },
    { group: 'Run',     icon: '⏹', label: 'Stop Agent',       hint: '',           action: () => typeof stopSession === 'function' && stopSession() },
    // ── Panel toggles ──────────────────────────────────────────────────────
    { group: 'Panels',  icon: '◂', label: 'Toggle AI Panel',  hint: 'Ctrl+Shift+E', action: () => window.NxWorkspace && NxWorkspace.toggleLeft() },
    { group: 'Panels',  icon: '▸', label: 'Toggle Inspector', hint: 'Ctrl+Shift+I', action: () => window.NxWorkspace && NxWorkspace.toggleRight() },
    { group: 'Panels',  icon: '▲', label: 'Toggle Dock',      hint: '',           action: () => window.NxWorkspace && NxWorkspace.toggleBottom() },
    // ── Workspace presets ──────────────────────────────────────────────────
    { group: 'Presets', icon: '🏗', label: 'Builder Mode',    hint: '',           action: () => typeof nxApplyPreset === 'function' && nxApplyPreset('builder') },
    { group: 'Presets', icon: '🐛', label: 'Debug Mode',      hint: '',           action: () => typeof nxApplyPreset === 'function' && nxApplyPreset('debug') },
    { group: 'Presets', icon: '◻', label: 'Minimal Mode',    hint: '',           action: () => typeof nxApplyPreset === 'function' && nxApplyPreset('minimal') },
    { group: 'Presets', icon: '🔬', label: 'Research Mode',   hint: '',           action: () => typeof nxApplyPreset === 'function' && nxApplyPreset('research') },
    // ── Tabs ───────────────────────────────────────────────────────────────
    { group: 'Views',   icon: '📋', label: 'View Logs',       hint: '',           action: () => typeof nxSetTab === 'function' && nxSetTab('logs') },
    { group: 'Views',   icon: '👁', label: 'Preview App',     hint: '',           action: () => typeof nxSetTab === 'function' && nxSetTab('preview') },
    { group: 'Views',   icon: '📄', label: 'Code Editor',     hint: '',           action: () => typeof nxSetTab === 'function' && nxSetTab('code') },
    { group: 'Views',   icon: '💻', label: 'Terminal',        hint: '',           action: () => typeof nxSetTab === 'function' && nxSetTab('terminal') },
    { group: 'Views',   icon: '💬', label: 'Chat',            hint: '',           action: () => typeof nxSetTab === 'function' && nxSetTab('chat') },
    { group: 'Views',   icon: '📊', label: 'Metrics',         hint: '',           action: () => typeof nxSetTab === 'function' && nxSetTab('metrics') },
    // ── System ─────────────────────────────────────────────────────────────
    { group: 'System',  icon: '⚙', label: 'Settings',        hint: 'Ctrl+,',     action: () => typeof openSettings === 'function' && openSettings() },
    { group: 'System',  icon: '↺', label: 'Reset Layout',    hint: '',           action: () => window.NxWorkspace && NxWorkspace.resetLayout() },
    { group: 'System',  icon: '💾', label: 'Save File',      hint: 'Ctrl+S',     action: () => typeof saveCurrentFile !== 'undefined' && saveCurrentFile() },
    { group: 'System',  icon: '🧹', label: 'Clear Memory',   hint: '',           action: () => typeof clearAgentMemory === 'function' && clearAgentMemory() },
  ];

  // Override NX_PALETTE_ITEMS once boot tasks run
  function _patchPalette() {
    window.NX_PALETTE_ITEMS = GROUPED_ITEMS;

    // Patch nxRenderPalette to render group headers
    const orig = window.nxRenderPalette;
    window.nxRenderPalette = function(q) {
      const list = document.getElementById('nxPaletteList');
      if (!list) { if (orig) orig(q); return; }

      const filtered = q
        ? GROUPED_ITEMS.filter(i => i.label.toLowerCase().includes(q.toLowerCase()))
        : GROUPED_ITEMS;

      if (!filtered.length) {
        list.innerHTML = '<div class="nx-palette-empty">No commands found</div>';
        window.nxPaletteFiltered = filtered;
        return;
      }

      let html = '';
      let lastGroup = null;
      filtered.forEach((item, i) => {
        if (!q && item.group !== lastGroup) {
          html += `<div class="nx-palette-group">${item.group}</div>`;
          lastGroup = item.group;
        }
        html += `<div class="nx-palette-item${i === (window.nxPaletteSelected||0) ? ' selected' : ''}"
          onclick="nxRunPaletteItem(${i})">
          <span class="nx-palette-item-icon">${item.icon}</span>
          <span class="nx-palette-item-label">${item.label}</span>
          ${item.hint ? `<span class="nx-palette-item-hint"><kbd class="nx-kbd">${item.hint}</kbd></span>` : ''}
        </div>`;
      });

      list.innerHTML = html;
      window.nxPaletteFiltered = filtered;
    };
  }

  if (Array.isArray(window.NX_BOOT_TASKS)) {
    window.NX_BOOT_TASKS.push(_patchPalette);
  } else {
    document.addEventListener('DOMContentLoaded', _patchPalette);
  }

  window.NX_GROUPED_PALETTE = GROUPED_ITEMS;
})();

