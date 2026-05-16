/**
 * nx-monaco.js — AGI Workspace Monaco Layer v1
 * ─────────────────────────────────────────────
 * Wraps the existing window.CodeEditor (Phase 20.6) with:
 *   • Multi-tab management (open, close, dirty tracking per file)
 *   • File conflict detection (agent writes to open file → banner)
 *   • NxBus-driven file.modified sync
 *   • Session-aware tab restoration from localStorage
 *   • Controlled reload prompts (never silently overwrites)
 *
 * Zero modification to runtime.js or web_app.py.
 * Communicates exclusively via NxBus + existing CodeEditor API.
 */
'use strict';

(function () {

  /* ── Guard ─────────────────────────────────────────────────────────────── */
  if (window.NxMonaco) return;

  /* ── Constants ─────────────────────────────────────────────────────────── */
  const STORE_KEY  = 'nx_monaco_tabs_v1';
  const MAX_TABS   = 10;
  const ICONS = {
    '.py':'🐍','.js':'📜','.ts':'📘','.jsx':'⚛','.tsx':'⚛',
    '.html':'🌐','.css':'🎨','.json':'📦','.md':'📝','.sh':'⚙',
    '.yml':'📋','.yaml':'📋','.sql':'🗄','.go':'🐹','.rs':'🦀',
    '.rb':'💎','.php':'🐘','.java':'☕','.c':'🔧','.cpp':'🔧',
  };

  /* ── State ─────────────────────────────────────────────────────────────── */
  const _tabs = new Map(); // path → { path, content, mtime, dirty, version, conflicted }
  let _activeTab   = null; // path of currently displayed tab
  let _sid         = null; // active session id
  let _mounted     = false;

  /* ── Storage ────────────────────────────────────────────────────────────── */
  function _persist() {
    try {
      const payload = { sid: _sid, active: _activeTab, paths: [..._tabs.keys()] };
      localStorage.setItem(STORE_KEY, JSON.stringify(payload));
    } catch (_) {}
  }

  function _restore(sid) {
    try {
      const raw = JSON.parse(localStorage.getItem(STORE_KEY) || '{}');
      if (raw.sid !== sid) return [];
      return { paths: raw.paths || [], active: raw.active };
    } catch (_) { return { paths: [], active: null }; }
  }

  /* ── Tab bar DOM ────────────────────────────────────────────────────────── */
  function _ensureTabBar() {
    if (document.getElementById('nxMonacoTabBar')) return;
    const host = document.getElementById('fileViewerBody')?.closest('[id]')?.parentElement
              || document.querySelector('.nx-code-panel, #tabCode, #nxTab-code, .tab-pane[data-tab="code"]');
    if (!host) return;

    const bar = document.createElement('div');
    bar.id = 'nxMonacoTabBar';
    bar.style.cssText = [
      'display:flex;align-items:center;overflow-x:auto;background:var(--nds-bg)',
      'border-bottom:1px solid var(--nds-surface-4)',
      'height:var(--nds-tabbar-h);flex-shrink:0;gap:0;scrollbar-width:none',
    ].join(';');
    host.insertBefore(bar, host.firstChild);

    // Conflict banner (hidden initially)
    const banner = document.createElement('div');
    banner.id = 'nxConflictBanner';
    banner.style.cssText = [
      'display:none;align-items:center;gap:8px;padding:5px 12px',
      'background:rgba(246,185,59,.12);border-bottom:1px solid rgba(246,185,59,.3)',
      'font-size:11px;color:#f6b93b;flex-shrink:0',
    ].join(';');
    banner.innerHTML = `<span id="nxConflictMsg">⚠ File modified externally</span>
      <button onclick="NxMonaco.acceptReload()" style="margin-left:auto;padding:2px 8px;
        border:1px solid #f6b93b;background:none;color:#f6b93b;border-radius:4px;cursor:pointer;font-size:11px">
        Reload</button>
      <button onclick="NxMonaco.dismissConflict()" style="padding:2px 8px;border:none;
        background:none;color:var(--text-muted,#7878a0);cursor:pointer;font-size:11px">Keep mine</button>`;
    host.insertBefore(banner, bar.nextSibling);

    _mounted = true;
  }

  function _iconFor(path) {
    const ext = (path.match(/(\.[^.]+)$/) || [])[1] || '';
    return ICONS[ext] || '📄';
  }

  function _renderTabBar() {
    const bar = document.getElementById('nxMonacoTabBar');
    if (!bar) return;
    bar.innerHTML = '';
    for (const [path, tab] of _tabs) {
      const name = path.split('/').pop();
      const active = path === _activeTab;
      const el = document.createElement('div');
      el.className = 'nx-mt' + (active ? ' nx-mt-active' : '') + (tab.dirty ? ' nx-mt-dirty' : '') + (tab.conflicted ? ' nx-mt-conflict' : '');
      el.dataset.path = path;
      el.title = path + (tab.dirty ? ' (unsaved)' : '') + (tab.conflicted ? ' (conflict!)' : '');
      el.style.cssText = [
        'display:flex;align-items:center;gap:6px;padding:0 12px',
        'height:var(--nds-tabbar-h);font-size:12px;font-family:var(--nds-font);cursor:pointer;white-space:nowrap;border-right:1px solid var(--nds-surface-4)',
        'flex-shrink:0;user-select:none;transition:background var(--nds-dur-2)',
        active ? 'background:var(--nds-surface-2);border-top:1px solid var(--nds-accent);color:var(--nds-text-hi)'
               : 'color:var(--nds-text-lo);background:transparent',
      ].join(';');
      el.innerHTML = `<span style="font-size:10px">${_iconFor(path)}</span>
        <span>${_escHtml(name)}</span>
        ${tab.dirty ? '<span style="color:var(--accent,#6366f1);font-size:8px;margin-left:1px">●</span>' : ''}
        ${tab.conflicted ? '<span title="External modification" style="color:#f6b93b">⚡</span>' : ''}
        <span class="nx-mt-close" data-close="${_escHtml(path)}"
          style="margin-left:4px;opacity:0;font-size:11px;padding:0 2px;border-radius:2px;
                 transition:opacity .12s;color:var(--text-muted,#7878a0)">✕</span>`;
      el.addEventListener('click', (e) => {
        if (e.target.dataset.close) { NxMonaco.closeTab(e.target.dataset.close); return; }
        NxMonaco.activateTab(path);
      });
      el.addEventListener('mouseenter', () => { el.querySelector('.nx-mt-close').style.opacity = '1'; });
      el.addEventListener('mouseleave', () => { el.querySelector('.nx-mt-close').style.opacity = '0'; });
      bar.appendChild(el);
    }
  }

  function _escHtml(s) {
    return String(s ?? '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
  }

  /* ── Conflict banner ────────────────────────────────────────────────────── */
  let _pendingReloadPath = null;
  let _pendingReloadContent = null;

  function _showConflict(path, newContent, agentVersion) {
    _pendingReloadPath    = path;
    _pendingReloadContent = newContent;
    const tab = _tabs.get(path);
    if (tab) { tab.conflicted = true; _renderTabBar(); }
    const banner = document.getElementById('nxConflictBanner');
    const msg    = document.getElementById('nxConflictMsg');
    if (banner) {
      if (msg) msg.textContent = `⚠ Agent modified "${path.split('/').pop()}" — you have unsaved edits`;
      banner.style.display = 'flex';
    }
    if (window.NxBus) NxBus.emit('nx:editor:conflict', { path, agentVersion });
  }

  function _hideConflict() {
    const banner = document.getElementById('nxConflictBanner');
    if (banner) banner.style.display = 'none';
    if (_pendingReloadPath) {
      const tab = _tabs.get(_pendingReloadPath);
      if (tab) { tab.conflicted = false; _renderTabBar(); }
    }
    _pendingReloadPath = _pendingReloadContent = null;
  }

  /* ── Core tab operations ────────────────────────────────────────────────── */
  async function _activateTab(path) {
    if (!_tabs.has(path)) return;
    const tab = _tabs.get(path);
    _activeTab = path;
    _persist();
    _renderTabBar();

    // Check dirty guard on CodeEditor before switching content
    if (window.CodeEditor) {
      const curPath = window.CodeEditor.getCurrentPath?.();
      if (curPath && curPath !== path && window.CodeEditor.isDirty?.()) {
        const cur = _tabs.get(curPath);
        if (cur) {
          cur.dirty = true;
          _renderTabBar();
        }
      }
    }

    // Switch Monaco to this file's content
    if (window.CodeEditor && tab.content !== undefined) {
      const ext = (path.match(/(\.[^.]+)$/) || [])[1] || '';
      await window.CodeEditor.loadFile(path, tab.content, ext);
      // Re-sync dirty state from editor
      if (typeof nxSetTab === 'function') nxSetTab('code');
    }

    // Highlight in file tree
    document.querySelectorAll('.ft-row.active').forEach(r => r.classList.remove('active'));
    const row = document.querySelector(`.ft-file[data-path="${CSS.escape(path)}"]`);
    if (row) row.classList.add('active');

    // Restore cursor/scroll from persisted state
    _restoreViewState(path);
  }

  async function _openTab(path, content, mtime) {
    _ensureTabBar();
    if (_tabs.size >= MAX_TABS && !_tabs.has(path)) {
      // Evict oldest clean tab
      for (const [p, t] of _tabs) {
        if (!t.dirty) { _tabs.delete(p); break; }
      }
    }
    const existing = _tabs.get(path);
    if (existing) {
      // Already open — if content is newer and file is not dirty, silently update buffer
      if (content !== undefined && mtime && (!existing.mtime || mtime > existing.mtime)) {
        if (!existing.dirty) {
          existing.content = content;
          existing.mtime   = mtime;
          if (path === _activeTab && window.CodeEditor) {
            const ext = (path.match(/(\.[^.]+)$/) || [])[1] || '';
            await window.CodeEditor.loadFile(path, content, ext);
          }
        } else {
          // Dirty + external update → conflict
          existing.pendingContent = content;
          _showConflict(path, content, mtime);
        }
      }
    } else {
      _tabs.set(path, { path, content: content ?? '', mtime: mtime ?? 0, dirty: false, conflicted: false });
    }
    _persist();
    await _activateTab(path);
  }

  function _closeTab(path) {
    const tab = _tabs.get(path);
    if (!tab) return;
    if (tab.dirty) {
      if (!confirm(`"${path.split('/').pop()}" has unsaved changes. Close anyway?`)) return;
    }
    _saveViewState(path);
    _tabs.delete(path);
    if (_activeTab === path) {
      const keys = [..._tabs.keys()];
      _activeTab = keys[keys.length - 1] || null;
      if (_activeTab) _activateTab(_activeTab);
      else if (window.CodeEditor) window.CodeEditor.reset();
    }
    _persist();
    _renderTabBar();
  }

  /* ── View state (cursor/scroll) persistence ────────────────────────────── */
  const _viewState = new Map(); // path → Monaco viewState

  function _saveViewState(path) {
    if (!path || !window.CodeEditor?.editor) return;
    try { _viewState.set(path, window.CodeEditor.editor.saveViewState()); } catch (_) {}
  }

  function _restoreViewState(path) {
    if (!path || !window.CodeEditor?.editor || !_viewState.has(path)) return;
    try { window.CodeEditor.editor.restoreViewState(_viewState.get(path)); } catch (_) {}
  }

  /* ── Dirty tracking from Monaco onDidChangeModelContent ────────────────── */
  function _hookMonacoDirtyTracking() {
    // Poll until Monaco editor instance is available, then hook
    const _poll = setInterval(() => {
      const ed = window.CodeEditor?.editor;
      if (!ed) return;
      clearInterval(_poll);
      ed.onDidChangeModelContent(() => {
        const p = _activeTab;
        if (!p) return;
        const tab = _tabs.get(p);
        if (!tab) return;
        const wasDirty = tab.dirty;
        tab.dirty = window.CodeEditor.isDirty?.() ?? true;
        if (tab.dirty !== wasDirty) _renderTabBar();
      });
    }, 500);
  }

  /* ── NxBus subscriptions — file.modified sync ──────────────────────────── */
  function _subscribeNxBus() {
    if (!window.NxBus) { setTimeout(_subscribeNxBus, 400); return; }

    // SSE file.modified event → check conflict / refresh
    NxBus.on('file.modified', async ({ path, mtime, session_id } = {}) => {
      if (!path || (session_id && session_id !== _sid)) return;
      const tab = _tabs.get(path);
      if (!tab) {
        // Not open — just refresh the tree
        if (typeof refreshFileTree === 'function') refreshFileTree();
        return;
      }
      // Fetch new content
      if (!_sid) return;
      try {
        const r = await fetch(`/api/file/${_sid}?path=${encodeURIComponent(path)}`);
        const d = await r.json();
        if (!d.ok || d.encoding !== 'text') return;
        if (tab.dirty) {
          tab.pendingContent = d.content;
          _showConflict(path, d.content, mtime || Date.now());
        } else {
          tab.content = d.content;
          tab.mtime   = mtime || Date.now();
          if (path === _activeTab && window.CodeEditor) {
            const ext = (path.match(/(\.[^.]+)$/) || [])[1] || '';
            await window.CodeEditor.loadFile(path, d.content, ext);
          }
          _markStale(path, false);
        }
      } catch (_) {}
    });

    // Session lifecycle — reset tabs on new session
    NxBus.on(NxBus.EVENTS?.SESSION_RESTORED, ({ sid } = {}) => {
      if (sid && sid !== _sid) {
        NxMonaco.resetForSession(sid);
      }
    });
    NxBus.on(NxBus.EVENTS?.SESSION_CREATED, ({ sid } = {}) => {
      NxMonaco.resetForSession(sid);
    });

    // Reflect agent output events as read-only indicators
    NxBus.on('agent.action', ({ action, path: aPath } = {}) => {
      if (action === 'write_file' && aPath) _markStale(aPath, true);
    });
  }

  function _markStale(path, stale) {
    const tab = _tabs.get(path);
    if (!tab) return;
    // For now surface via tree highlighting; extend if needed
    const row = document.querySelector(`.ft-file[data-path="${CSS.escape(path)}"]`);
    if (row) {
      row.style.opacity = stale ? '0.6' : '';
      row.title = stale ? `${path} (agent is writing…)` : path;
    }
  }

  /* ── Public API ─────────────────────────────────────────────────────────── */
  const NxMonaco = {

    async openFile(path, content, mtime) {
      await _openTab(path, content, mtime);
    },

    activateTab(path) {
      _saveViewState(_activeTab);
      return _activateTab(path);
    },

    closeTab(path) {
      _closeTab(path);
    },

    /** Called by Ctrl+S or toolbar Save */
    async saveActive() {
      const path = _activeTab;
      const tab  = _tabs.get(path);
      if (!tab || !_sid) return;
      const content = window.CodeEditor?.editor?.getValue() ?? tab.content;
      try {
        const r = await fetch(`/api/save-file/${_sid}`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ path, content }),
        });
        const d = await r.json();
        if (d.ok) {
          tab.content = content;
          tab.dirty   = false;
          tab.mtime   = d.saved_at || Date.now();
          _renderTabBar();
          if (window.NxBus) NxBus.emit('nx:editor:saved', { path, sid: _sid });
        } else {
          console.error('[NxMonaco] save failed', d);
        }
      } catch (e) { console.error('[NxMonaco] save error', e); }
    },

    acceptReload() {
      if (!_pendingReloadPath || !_pendingReloadContent) { _hideConflict(); return; }
      const path    = _pendingReloadPath;
      const content = _pendingReloadContent;
      const tab     = _tabs.get(path);
      if (tab) { tab.content = content; tab.dirty = false; tab.conflicted = false; }
      _hideConflict();
      if (path === _activeTab && window.CodeEditor) {
        const ext = (path.match(/(\.[^.]+)$/) || [])[1] || '';
        window.CodeEditor.loadFile(path, content, ext);
      }
      _renderTabBar();
    },

    dismissConflict() {
      _hideConflict();
    },

    /** Reset all tabs for a new session */
    resetForSession(sid) {
      _tabs.clear();
      _activeTab = null;
      _sid = sid;
      _viewState.clear();
      _persist();
      _renderTabBar();
      if (window.CodeEditor) window.CodeEditor.reset();
      // Restore previously open tabs from localStorage
      const saved = _restore(sid);
      if (saved.paths?.length) {
        // Re-open first tab immediately; rest lazily (avoiding N parallel fetches)
        const first = saved.paths[0];
        fetch(`/api/file/${sid}?path=${encodeURIComponent(first)}`)
          .then(r => r.json())
          .then(d => { if (d.ok && d.encoding === 'text') NxMonaco.openFile(first, d.content, d.mtime); })
          .catch(() => {});
      }
    },

    init(sid) {
      _sid = sid;
      _ensureTabBar();
      _hookMonacoDirtyTracking();
      _subscribeNxBus();
      _injectStyles();
      // Patch openFileFromTree to go through NxMonaco
      _patchFileTreeOpen();
      // Ctrl+S global save
      document.addEventListener('keydown', e => {
        if ((e.ctrlKey || e.metaKey) && e.key === 's') {
          e.preventDefault();
          NxMonaco.saveActive();
        }
      }, { capture: true });
      console.debug('[NxMonaco] initialized for session', sid);
    },

    getActiveTab()   { return _activeTab; },
    getAllTabs()      { return [..._tabs.values()]; },
    isDirtyTab(path) { return _tabs.get(path)?.dirty ?? false; },
    get editor()     { return window.CodeEditor?.editor ?? null; },
  };

  /* ── Patch openFileFromTree ──────────────────────────────────────────────── */
  function _patchFileTreeOpen() {
    const _poll = setInterval(() => {
      if (typeof window.openFileFromTree !== 'function') return;
      clearInterval(_poll);
      const _orig = window.openFileFromTree;
      window.openFileFromTree = async function (path) {
        // Check if already in tabs (use cached content)
        const tab = _tabs.get(path);
        if (tab) { NxMonaco.activateTab(path); return; }
        // Let runtime.js do the fetch, then steal the result
        await _orig.apply(this, arguments);
        // After runtime.js loads, grab the content from CodeEditor buffer
        const content = window.CodeEditor?.editor?.getValue() ?? '';
        if (!_tabs.has(path)) {
          _tabs.set(path, { path, content, mtime: Date.now(), dirty: false, conflicted: false });
          _activeTab = path;
          _persist();
          _renderTabBar();
        }
      };
    }, 400);
  }

  /* ── Styles ─────────────────────────────────────────────────────────────── */
  function _injectStyles() {
    if (document.getElementById('nxMonacoStyles')) return;
    const s = document.createElement('style');
    s.id = 'nxMonacoStyles';
    s.textContent = `
      #nxMonacoTabBar::-webkit-scrollbar { display:none; }
      .nx-mt { transition: background .12s, border-color .12s; }
      .nx-mt:hover { background: var(--hover,rgba(255,255,255,.04)) !important; }
      .nx-mt-active { position:relative; }
      .nx-mt-dirty .nx-mt-close { color: var(--accent,#6366f1) !important; }
      .nx-mt-conflict { background: rgba(246,185,59,.06) !important; }
      #nxConflictBanner button:hover { opacity:.8; }
    `;
    document.head.appendChild(s);
  }

  /* ── Boot ───────────────────────────────────────────────────────────────── */
  window.NxMonaco = NxMonaco;

  // Auto-init when a session becomes active
  function _tryAutoInit() {
    const sid = typeof currentSession !== 'undefined' ? currentSession : null;
    if (sid) { NxMonaco.init(sid); return; }
    setTimeout(_tryAutoInit, 500);
  }
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => setTimeout(_tryAutoInit, 600));
  } else {
    setTimeout(_tryAutoInit, 600);
  }

})();
