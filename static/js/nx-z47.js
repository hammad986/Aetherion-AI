/**
 * nx-z47.js — Phase Z47: Operational File Workspace + Command Flow Realization
 * ══════════════════════════════════════════════════════════════════════════════
 *
 * Z47A — Inline file preview (uses /api/file/<sid>?path=<rel>)
 * Z47B — File tab system (open files remembered, tabs in tab bar)
 * Z47C — Command palette extension (operational commands, recent history, context-aware)
 * Z47D — Execution dock (always-visible bottom bar, live task + state)
 * Z47E — Artifact intelligence (metadata, linkage display in Files panel)
 * Z47F — Workspace continuity (localStorage save/restore)
 * Z47G — UX realism audit
 *
 * Rules:
 *   ✗ No external libraries
 *   ✗ No fake data or dummy interactions
 *   ✗ No UI freezing — preview truncated at 50KB
 *   ✓ All content from real APIs
 *   ✓ RAF-batched DOM writes
 *   ✓ Graceful degradation on API failure
 */
'use strict';

(function () {
  if (window._z47) return;

  /* ── Utilities ───────────────────────────────────────────────────── */
  const $ = id => document.getElementById(id);
  const esc = s => String(s ?? '').replace(/[<>&"']/g, c =>
    ({ '<': '&lt;', '>': '&gt;', '&': '&amp;', '"': '&quot;', "'": '&#39;' }[c]));
  const _raf = fn => requestAnimationFrame(fn);
  const _ago = ts => {
    const sec = Math.floor((Date.now() - (typeof ts === 'number' && ts < 1e12 ? ts * 1000 : ts)) / 1000);
    if (sec < 60)    return `${sec}s ago`;
    if (sec < 3600)  return `${Math.floor(sec / 60)}m ago`;
    if (sec < 86400) return `${Math.floor(sec / 3600)}h ago`;
    return `${Math.floor(sec / 86400)}d ago`;
  };
  const _sizeStr = b => !b ? '' : b < 1024 ? `${b}B` : b < 1048576 ? `${(b/1024).toFixed(0)}K` : `${(b/1048576).toFixed(1)}M`;
  const _activeSid = () => (window.NX?.activeSid) || (typeof currentSession !== 'undefined' ? currentSession : null);

  const PREVIEW_MAX_CHARS = 50_000;  // 50KB display limit

  /* ══════════════════════════════════════════════════════════════════
     Z47F — WORKSPACE STATE (localStorage persistence)
     ══════════════════════════════════════════════════════════════════ */

  const STATE_KEY = 'nx_ws_state_v1';
  let _wsState = { openTabs: [], activeTab: null, panelOpen: null, lastSid: null, recentCmds: [] };

  function _loadState() {
    try {
      const s = localStorage.getItem(STATE_KEY);
      if (s) _wsState = { ..._wsState, ...JSON.parse(s) };
    } catch {}
  }

  function _saveState() {
    try { localStorage.setItem(STATE_KEY, JSON.stringify(_wsState)); } catch {}
  }

  function _persistOpenTabs() {
    _wsState.openTabs = _openFileTabs.map(t => ({ path: t.path, name: t.name, sid: t.sid }));
    _wsState.activeTab = _activeFileTabPath;
    _saveState();
  }

  function _persistPanel(name) {
    _wsState.panelOpen = name || null;
    _saveState();
  }

  function _persistSession(sid) {
    if (sid) { _wsState.lastSid = sid; _saveState(); }
  }

  function _addRecentCmd(label, icon) {
    const rec = { label, icon, ts: Date.now() };
    _wsState.recentCmds = [rec, ..._wsState.recentCmds.filter(r => r.label !== label)].slice(0, 8);
    _saveState();
  }

  /* ══════════════════════════════════════════════════════════════════
     Z47A — FILE PREVIEW PANEL
     ══════════════════════════════════════════════════════════════════ */

  let _previewState = { path: null, sid: null }; // currently previewed file

  function _openFilePreview(path, sid) {
    const el = $('nxPanelContent-files');
    if (!el) return;

    sid = sid || _activeSid();
    if (!sid) {
      _showPreviewError(el, 'No active session', 'Open or create a session first to preview files.', path, sid);
      return;
    }

    _previewState = { path, sid };
    _addFileTab(path, sid);
    _renderPreviewLoading(el, path);

    fetch(`/api/file/${encodeURIComponent(sid)}?path=${encodeURIComponent(path)}`)
      .then(r => r.json())
      .then(data => _raf(() => _paintFilePreview(el, data, path, sid)))
      .catch(err => _raf(() => _showPreviewError(el, 'Failed to load file', String(err), path, sid)));
  }

  function _renderPreviewLoading(el, path) {
    el.innerHTML = `
      <div class="z47-preview-wrap">
        <div class="z47-preview-breadcrumb">
          <button class="z47-preview-back" onclick="window._z47.backToFiles()">← Files</button>
          <span>›</span>
          <span class="z47-preview-filename">${esc(path.split('/').pop())}</span>
        </div>
        <div class="z46-loading">Loading preview…</div>
      </div>`;
  }

  function _paintFilePreview(el, data, path, sid) {
    if (!data.ok) {
      let msg = data.error === 'too_large'
        ? `File is too large to preview (${_sizeStr(data.size)}). Download it instead.`
        : `Could not read file: ${data.error}`;
      _showPreviewError(el, 'Preview unavailable', msg, path, sid);
      return;
    }

    const fname    = path.split('/').pop();
    const ext      = (data.ext || '').replace('.', '');
    const content  = data.content || '';
    const isTrunc  = content.length > PREVIEW_MAX_CHARS;
    const display  = isTrunc ? content.slice(0, PREVIEW_MAX_CHARS) : content;

    const downloadUrl = `/api/download/${encodeURIComponent(sid)}/${encodeURIComponent(path)}`;

    let rendered = '';
    if (ext === 'md' || ext === 'markdown') {
      rendered = `<div class="z47-md-wrap">${_renderMarkdown(display)}</div>`;
    } else if (ext === 'json') {
      rendered = _renderJson(display);
    } else if (['py','js','ts','jsx','tsx','html','htm','css','scss','sh','bash','yaml','yml','toml','ini','cfg','rb','go','rs','java','cpp','c','h','php','sql'].includes(ext)) {
      rendered = _renderCode(display, ext);
    } else if (['log','txt','csv','env','gitignore'].includes(ext) || data.encoding === 'text') {
      rendered = `<div class="z47-text-wrap">${esc(display)}</div>`;
    } else if (data.encoding === 'base64') {
      rendered = `<div class="z47-preview-error">
        <div class="z47-preview-error-icon">📦</div>
        <div class="z47-preview-error-title">Binary file</div>
        <div class="z47-preview-error-body">This file is binary and cannot be previewed inline.</div>
      </div>`;
    } else {
      rendered = `<div class="z47-text-wrap">${esc(display)}</div>`;
    }

    const truncBanner = isTrunc ? `
      <div class="z47-preview-truncated">
        ⚠ Preview truncated — showing first ${_sizeStr(PREVIEW_MAX_CHARS)} of ${_sizeStr(data.size)}.
        <a class="z47-preview-download" href="${esc(downloadUrl)}" download="${esc(fname)}">Download full file</a>
      </div>` : '';

    el.innerHTML = `
      <div class="z47-preview-wrap">
        <div class="z47-preview-breadcrumb">
          <button class="z47-preview-back" onclick="window._z47.backToFiles()">← Files</button>
          <span>›</span>
          <span class="z47-preview-filename" title="${esc(path)}">${esc(fname)}</span>
          <span class="z47-preview-size">${_sizeStr(data.size)}</span>
          <a class="z47-preview-download" href="${esc(downloadUrl)}" download="${esc(fname)}">↓ Download</a>
        </div>
        ${truncBanner}
        <div class="z47-preview-content">${rendered}</div>
      </div>`;
  }

  function _showPreviewError(el, title, body, path, sid) {
    const fname = (path || '').split('/').pop();
    const downloadUrl = (sid && path) ? `/api/download/${encodeURIComponent(sid)}/${encodeURIComponent(path)}` : null;
    el.innerHTML = `
      <div class="z47-preview-wrap">
        <div class="z47-preview-breadcrumb">
          <button class="z47-preview-back" onclick="window._z47.backToFiles()">← Files</button>
          <span>›</span>
          <span class="z47-preview-filename">${esc(fname)}</span>
        </div>
        <div class="z47-preview-error">
          <div class="z47-preview-error-icon">⚠</div>
          <div class="z47-preview-error-title">${esc(title)}</div>
          <div class="z47-preview-error-body">${esc(body)}</div>
          ${downloadUrl ? `<a class="z47-preview-download" href="${esc(downloadUrl)}" download="${esc(fname)}">↓ Download instead</a>` : ''}
        </div>
      </div>`;
  }

  function _backToFiles() {
    _previewState = { path: null, sid: null };
    if (window._z46?.refreshFiles) window._z46.refreshFiles();
  }

  /* ── Markdown renderer ──────────────────────────────────────────── */
  function _renderMarkdown(src) {
    // Block-level elements
    let html = '';
    const lines = src.split('\n');
    let inPre = false, preLang = '', preBuf = '';
    let inList = false, listBuf = '';

    const _flushList = () => {
      if (!inList) return;
      html += `<ul>${listBuf}</ul>`;
      inList = false; listBuf = '';
    };
    const _inline = t => t
      .replace(/`([^`]+)`/g, '<code>$1</code>')
      .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
      .replace(/\*([^*]+)\*/g, '<em>$1</em>')
      .replace(/~~([^~]+)~~/g, '<del>$1</del>')
      .replace(/\[([^\]]+)\]\(([^)]+)\)/g, '<a href="$2" rel="noopener">$1</a>');

    for (let i = 0; i < lines.length; i++) {
      const raw = lines[i];
      const line = esc(raw);  // HTML-escape first

      // Fenced code block
      if (raw.startsWith('```')) {
        if (!inPre) {
          _flushList();
          inPre = true; preLang = raw.slice(3).trim(); preBuf = '';
        } else {
          html += `<pre><code class="z47-lang-${esc(preLang)}">${preBuf}</code></pre>`;
          inPre = false;
        }
        continue;
      }
      if (inPre) { preBuf += esc(raw) + '\n'; continue; }

      if (raw === '---' || raw === '***' || raw === '___') { _flushList(); html += '<hr>'; continue; }
      if (/^#{1} /.test(raw))   { _flushList(); html += `<h1>${_inline(esc(raw.slice(2)))}</h1>`; continue; }
      if (/^#{2} /.test(raw))   { _flushList(); html += `<h2>${_inline(esc(raw.slice(3)))}</h2>`; continue; }
      if (/^#{3} /.test(raw))   { _flushList(); html += `<h3>${_inline(esc(raw.slice(4)))}</h3>`; continue; }
      if (/^#{4,6} /.test(raw)) { _flushList(); html += `<h4>${_inline(esc(raw.replace(/^#+\s*/, '')))}</h4>`; continue; }
      if (/^>\s/.test(raw))     { _flushList(); html += `<blockquote>${_inline(esc(raw.slice(2)))}</blockquote>`; continue; }
      if (/^[-*+]\s/.test(raw)) { inList = true; listBuf += `<li>${_inline(esc(raw.slice(2)))}</li>`; continue; }
      if (/^\d+\.\s/.test(raw)) { _flushList(); html += `<li>${_inline(esc(raw.replace(/^\d+\.\s/, '')))}</li>`; continue; }
      if (raw.trim() === '')    { _flushList(); html += '<p></p>'; continue; }
      _flushList();
      html += `<p>${_inline(line)}</p>`;
    }
    _flushList();
    return html;
  }

  /* ── JSON renderer ──────────────────────────────────────────────── */
  function _renderJson(src) {
    try {
      const parsed = JSON.parse(src);
      const pretty = JSON.stringify(parsed, null, 2);
      const colored = pretty
        .replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')
        .replace(/("(\\u[a-zA-Z0-9]{4}|\\[^u]|[^\\"])*"(\s*:)?|\b(true|false|null)\b|-?\d+\.?\d*([eE][+\-]?\d+)?)/g, m => {
          if (/^"/.test(m)) {
            if (/:$/.test(m)) return `<span class="z47-json-key">${m}</span>`;
            return `<span class="z47-json-str">${m}</span>`;
          }
          if (/true|false/.test(m)) return `<span class="z47-json-bool">${m}</span>`;
          if (/null/.test(m))       return `<span class="z47-json-null">${m}</span>`;
          return `<span class="z47-json-num">${m}</span>`;
        })
        .replace(/[{}\[\]:,]/g, p => `<span class="z47-json-punct">${p}</span>`);
      return `<div class="z47-json-wrap">${colored}</div>`;
    } catch {
      return `<div class="z47-text-wrap">${esc(src)}</div>`;
    }
  }

  /* ── Code renderer (escaped + line numbers) ─────────────────────── */
  function _renderCode(src, ext) {
    const lines = esc(src).split('\n');
    const numbered = lines.map((l, i) =>
      `<span class="z47-ln">${i + 1}</span>${l}`
    ).join('\n');
    return `<div class="z47-code-block">${numbered}</div>`;
  }

  /* ══════════════════════════════════════════════════════════════════
     Z47B — FILE TAB SYSTEM
     ══════════════════════════════════════════════════════════════════ */

  let _openFileTabs = [];      // [{ path, name, sid }]
  let _activeFileTabPath = null;
  let _fileTabsEl = null;

  function _mountFileTabsEl() {
    const tabActions = $('nxTabActions');
    if (!tabActions || $('z47FileTabsWrap')) return;

    const wrap = document.createElement('div');
    wrap.id = 'z47FileTabsWrap';
    wrap.style.cssText = 'display:flex;align-items:center;gap:4px;';

    const divider = document.createElement('div');
    divider.className = 'z47-tab-divider';

    const tabs = document.createElement('div');
    tabs.id = 'z47FileTabs';
    tabs.className = 'z47-file-tabs';

    wrap.appendChild(divider);
    wrap.appendChild(tabs);
    tabActions.appendChild(wrap);
    _fileTabsEl = tabs;
  }

  function _addFileTab(path, sid) {
    const name = path.split('/').pop();
    const existing = _openFileTabs.findIndex(t => t.path === path && t.sid === sid);
    if (existing === -1) {
      _openFileTabs.push({ path, name, sid });
    }
    _activeFileTabPath = path;
    _renderFileTabs();
    _persistOpenTabs();

    // Also open the Files panel if not already open
    const panel = $('nxPanel-files');
    if (panel && panel.style.display !== 'flex') {
      if (window.nxTogglePanel) nxTogglePanel('files');
    }
  }

  function _closeFileTab(path) {
    _openFileTabs = _openFileTabs.filter(t => t.path !== path);
    if (_activeFileTabPath === path) {
      _activeFileTabPath = _openFileTabs.length > 0 ? _openFileTabs[_openFileTabs.length - 1].path : null;
      if (_activeFileTabPath) {
        const tab = _openFileTabs.find(t => t.path === _activeFileTabPath);
        if (tab) _openFilePreview(tab.path, tab.sid);
        else if (window._z46?.refreshFiles) window._z46.refreshFiles();
      } else {
        if (window._z46?.refreshFiles) window._z46.refreshFiles();
      }
    }
    _renderFileTabs();
    _persistOpenTabs();
  }

  function _switchToTab(path) {
    const tab = _openFileTabs.find(t => t.path === path);
    if (!tab) return;
    _activeFileTabPath = path;
    _openFilePreview(tab.path, tab.sid);
    _renderFileTabs();
  }

  function _renderFileTabs() {
    if (!_fileTabsEl) _mountFileTabsEl();
    if (!_fileTabsEl) return;

    const wrap = $('z47FileTabsWrap');
    if (!_openFileTabs.length) {
      if (wrap) wrap.style.display = 'none';
      return;
    }
    if (wrap) wrap.style.display = 'flex';

    _fileTabsEl.innerHTML = _openFileTabs.map(t =>
      `<button class="z47-file-tab${t.path === _activeFileTabPath ? ' active' : ''}"
        onclick="window._z47.switchTab(${JSON.stringify(t.path)})"
        title="${esc(t.path)}">
        <span class="z47-file-tab-name">${esc(t.name)}</span>
        <span class="z47-file-tab-close" onclick="event.stopPropagation();window._z47.closeTab(${JSON.stringify(t.path)})">×</span>
      </button>`
    ).join('');
  }

  /* ── Restore file tabs from saved state ────────────────────────── */
  function _restoreFileTabs() {
    const saved = _wsState.openTabs || [];
    if (!saved.length) return;

    const sid = _activeSid() || _wsState.lastSid;
    // Only restore tabs for the current session
    _openFileTabs = saved.filter(t => !sid || t.sid === sid);
    _activeFileTabPath = _wsState.activeTab;
    _renderFileTabs();

    if (_wsState.panelOpen) {
      setTimeout(() => {
        const p = $('nxPanel-' + _wsState.panelOpen);
        if (!p) return;
        if (window.nxTogglePanel) nxTogglePanel(_wsState.panelOpen);
        if (_wsState.panelOpen === 'files' && _activeFileTabPath) {
          const tab = _openFileTabs.find(t => t.path === _activeFileTabPath);
          if (tab) setTimeout(() => _openFilePreview(tab.path, tab.sid), 300);
        }
      }, 800);
      _showRestoreToast();
    }
  }

  function _showRestoreToast() {
    let toast = $('z47RestoreToast');
    if (!toast) {
      toast = document.createElement('div');
      toast.id = 'z47RestoreToast';
      toast.className = 'z47-restore-toast';
      toast.textContent = 'Workspace state restored';
      document.body.appendChild(toast);
    }
    setTimeout(() => toast.classList.add('visible'), 100);
    setTimeout(() => toast.classList.remove('visible'), 2500);
  }

  /* ══════════════════════════════════════════════════════════════════
     Z47C — COMMAND PALETTE EXTENSION
     ══════════════════════════════════════════════════════════════════ */

  const _isExecuting = () => document.body.dataset.nxExec === 'running';
  const _isHitl      = () => document.body.dataset.nxState === 'hitl';
  const _isFailed    = () => document.body.dataset.nxState === 'failed';
  const _hasSession  = () => !!_activeSid();
  const _hasReplay   = () => !!window._z30?.inReplayMode?.();

  const _Z47_COMMANDS = [
    // Execution
    { icon: '⏮', label: 'Replay Last Mission',         section: 'Execution', condition: _hasSession,
      action: () => {
        const sid = _activeSid() || _wsState.lastSid;
        if (sid && window.NxBus) NxBus.emit('dag.replay.start', { sid });
      }},
    { icon: '⏸', label: 'Pause Mission',               section: 'Execution', condition: _isExecuting,
      action: () => typeof hitlPause === 'function' && hitlPause() },
    { icon: '▶', label: 'Resume Mission',               section: 'Execution', condition: _isHitl,
      action: () => typeof hitlResume === 'function' && hitlResume() },
    { icon: '↺', label: 'Recover Mission',              section: 'Execution', condition: _isFailed,
      action: () => typeof hitlRetry === 'function' && hitlRetry() },
    { icon: '⏹', label: 'Stop Agent',                  section: 'Execution', condition: _isExecuting,
      action: () => typeof stopSession === 'function' && stopSession() },
    { icon: '⏺', label: 'Jump to HITL Queue',          section: 'Execution',
      action: () => { window.nxSetTab?.('govern'); nxClosePalette?.(); }},

    // Navigation
    { icon: '📂', label: 'Open Files Panel',            section: 'Navigation',
      action: () => window.nxTogglePanel?.('files') },
    { icon: '📅', label: 'Open Session History',        section: 'Navigation',
      action: () => window.nxTogglePanel?.('history') },
    { icon: '⚙',  label: 'Open Settings Panel',        section: 'Navigation',
      action: () => window.nxTogglePanel?.('settings') },
    { icon: '🔍', label: 'Search Logs',                 section: 'Navigation',
      action: () => {
        window.nxSetTab?.('logs');
        setTimeout(() => $('nxLogFilter')?.focus(), 200);
      }},
    { icon: '📋', label: 'View Logs',                   section: 'Navigation',
      action: () => window.nxSetTab?.('logs') },
    { icon: '📄', label: 'Code Editor',                 section: 'Navigation',
      action: () => window.nxSetTab?.('code') },
    { icon: '💻', label: 'Terminal',                    section: 'Navigation',
      action: () => window.nxSetTab?.('terminal') },
    { icon: '🔭', label: 'Operator Intelligence',       section: 'Navigation',
      action: () => { window.nxSetTab?.('intel'); document.dispatchEvent(new Event('nx:tab:intel')); }},
    { icon: '🏛', label: 'Governance Panel',            section: 'Navigation',
      action: () => { window.nxSetTab?.('govern'); document.dispatchEvent(new Event('nx:tab:govern')); }},

    // Artifacts & Files
    { icon: '📦', label: 'Open Latest Artifact',        section: 'Artifacts',
      action: async () => {
        try {
          const r = await fetch('/api/artifacts/list');
          const d = r.ok ? await r.json() : null;
          const arts = d?.artifacts || d || [];
          if (arts.length && arts[0].name) {
            window.nxTogglePanel?.('files');
          }
        } catch {}
      }},
    { icon: '⬇', label: 'Export Forensic Bundle',      section: 'Artifacts', condition: _hasSession,
      action: () => {
        const sid = _activeSid();
        if (sid) window.open(`/api/z31/export/${encodeURIComponent(sid)}`, '_blank');
      }},
    { icon: '⬇', label: 'Download Session Log',        section: 'Artifacts', condition: _hasSession,
      action: () => {
        const sid = _activeSid();
        if (sid) window.open(`/api/logs?format=text&sid=${encodeURIComponent(sid)}`, '_blank');
      }},

    // Workspace
    { icon: '✕', label: 'Clear Open File Tabs',         section: 'Workspace',
      action: () => { _openFileTabs = []; _activeFileTabPath = null; _renderFileTabs(); _persistOpenTabs(); }},
    { icon: '◻', label: 'Minimal Mode',                 section: 'Workspace',
      action: () => typeof nxApplyPreset === 'function' && nxApplyPreset('minimal') },
    { icon: '🏗', label: 'Builder Mode',                 section: 'Workspace',
      action: () => typeof nxApplyPreset === 'function' && nxApplyPreset('builder') },
    { icon: '🐛', label: 'Debug Mode',                   section: 'Workspace',
      action: () => typeof nxApplyPreset === 'function' && nxApplyPreset('debug') },
    { icon: '↺', label: 'Reset Layout',                  section: 'Workspace',
      action: () => window.NxWorkspace?.resetLayout() },
  ];

  function _registerPaletteItems() {
    const waitPalette = (cb) => {
      if (window._NxPalette?.register) { cb(); return; }
      const t = setInterval(() => {
        if (window._NxPalette?.register) { clearInterval(t); cb(); }
      }, 100);
    };

    waitPalette(() => {
      _Z47_COMMANDS.forEach(cmd => {
        window._NxPalette.register({
          icon: cmd.icon, label: cmd.label,
          section: cmd.section, hint: cmd.hint || '',
          condition: cmd.condition,
          action: () => {
            _addRecentCmd(cmd.label, cmd.icon);
            cmd.action();
          },
        });
      });

      // Patch palette open to show recent commands
      _hookPaletteForRecents();
    });
  }

  function _hookPaletteForRecents() {
    const origOpen = window.nxOpenPalette;
    if (!origOpen) return;

    window.nxOpenPalette = function (...args) {
      origOpen.apply(this, args);
      // After palette opens, inject recent commands section
      setTimeout(_injectRecentCommands, 20);
    };
  }

  function _injectRecentCommands() {
    const list = $('nxPaletteList');
    const input = $('nxPaletteInput');
    if (!list || !input || input.value.trim()) return;  // Only when no query

    const recents = _wsState.recentCmds || [];
    if (!recents.length) return;

    const recentHtml = `
      <div class="nx-palette-section">Recent</div>
      ${recents.slice(0, 5).map(r => `
        <div class="nx-palette-recent" onclick="window._z47.runRecentCmd(${JSON.stringify(r.label)})">
          <span class="nx-palette-recent-icon">${esc(r.icon || '▶')}</span>
          <span class="nx-palette-recent-label">${esc(r.label)}</span>
          <span class="nx-palette-recent-ago">${_ago(r.ts)}</span>
        </div>`).join('')}
    `;
    list.insertAdjacentHTML('afterbegin', recentHtml);
  }

  function _runRecentCmd(label) {
    const cmd = _Z47_COMMANDS.find(c => c.label === label);
    if (cmd) { _addRecentCmd(cmd.label, cmd.icon); cmd.action(); }
    window.nxForcePaletteClose?.();
  }

  /* ══════════════════════════════════════════════════════════════════
     Z47D — EXECUTION DOCK
     Populates #nxDock with live state.
     ══════════════════════════════════════════════════════════════════ */

  let _dockTimer = null;
  let _taskStart = null;
  let _lastTask  = '';
  let _lastSessionInfo = null;

  function _mountDock() {
    const dock = $('nxDock');
    if (!dock || $('z47DockInner')) return;

    dock.innerHTML = `
      <div id="z47DockInner" style="display:flex;align-items:center;gap:10px;width:100%;min-width:0;">

        <!-- Status dot (always visible, color = state) -->
        <span class="z47-dock-dot" id="z47DockDot"></span>

        <!-- Idle state text -->
        <span class="z47-dock-slot z47-dock-idle" id="z47DockIdle">
          <span id="z47DockIdleText" style="font-size:11px;color:#6B7280">Ready</span>
        </span>

        <!-- Running state -->
        <span class="z47-dock-slot z47-dock-running" id="z47DockRunning" style="display:none">
          <span class="z47-dock-task" id="z47DockTask">Executing…</span>
          <span class="z47-dock-timer" id="z47DockTimer">0:00</span>
        </span>

        <!-- HITL wait state -->
        <span class="z47-dock-hitl" id="z47DockHitl">
          <span style="font-size:11px">⏸</span>
          <span>Awaiting approval</span>
          <button class="z47-dock-btn z47-dock-btn-primary" onclick="if(typeof hitlResume==='function')hitlResume()">Resume</button>
        </span>

        <!-- Completion state -->
        <span class="z47-dock-done" id="z47DockDone">
          <span>✓ Done</span>
          <span class="z47-dock-timer" id="z47DockDoneTime"></span>
          <button class="z47-dock-btn z47-dock-btn-green" id="z47DockReplayBtn"
            onclick="window._z47.replayLast()">⏮ Replay</button>
        </span>

        <!-- Failure state -->
        <span class="z47-dock-failed" id="z47DockFailed">
          <span>✗ Failed</span>
          <button class="z47-dock-btn z47-dock-btn-danger" onclick="window._z47.inspectFailure()">Inspect</button>
          <button class="z47-dock-btn" onclick="if(typeof hitlRetry==='function')hitlRetry()">Retry</button>
        </span>

        <span class="z47-dock-spacer"></span>

        <!-- Right meta chips -->
        <span class="z47-dock-meta" id="z47DockMeta"></span>

        <!-- Running controls (shown when executing) -->
        <span id="z47DockRunControls" style="display:none;gap:5px;align-items:center;flex-shrink:0;">
          <button class="z47-dock-btn" onclick="if(typeof hitlPause==='function')hitlPause()">⏸ Pause</button>
          <button class="z47-dock-btn z47-dock-btn-danger" onclick="if(typeof stopSession==='function')stopSession()">■ Stop</button>
        </span>

        <!-- Replay quick-chip (shown when has last session) -->
        <span class="z47-dock-replay" id="z47DockReplayChip" style="display:none"
          onclick="window._z47.replayLast()" title="Replay last session">
          ⏮ <span id="z47DockReplaySid"></span>
        </span>

        <!-- Version indicator -->
        <span style="font-size:9px;color:#374151;flex-shrink:0">Z47</span>
      </div>`;

    _updateDockMeta();
    _pollDockQueue();
  }

  function _dockSetIdle(info) {
    const idleEl = $('z47DockIdle');
    const runEl  = $('z47DockRunning');
    const runCtrl = $('z47DockRunControls');
    if (idleEl)   idleEl.style.display = '';
    if (runEl)    runEl.style.display = 'none';
    if (runCtrl)  runCtrl.style.display = 'none';
    _stopDockTimer();

    const idleText = $('z47DockIdleText');
    if (!idleText) return;
    if (info) {
      idleText.textContent = `Ready · ${info}`;
    } else {
      idleText.textContent = 'Ready';
    }
  }

  function _dockSetRunning(task) {
    const idleEl = $('z47DockIdle');
    const runEl  = $('z47DockRunning');
    const runCtrl = $('z47DockRunControls');
    if (idleEl)   idleEl.style.display = 'none';
    if (runEl)    runEl.style.display = '';
    if (runCtrl)  runCtrl.style.display = 'flex';
    _lastTask = task || 'Executing…';
    const taskEl = $('z47DockTask');
    if (taskEl) taskEl.textContent = _lastTask;
    _taskStart = Date.now();
    _startDockTimer();
    _hideDockReplayChip();
  }

  function _dockSetComplete(duration) {
    _stopDockTimer();
    const runCtrl = $('z47DockRunControls');
    if (runCtrl) runCtrl.style.display = 'none';
    const doneTime = $('z47DockDoneTime');
    if (doneTime && duration) doneTime.textContent = `· ${_fmtDuration(duration)}`;
  }

  function _startDockTimer() {
    _stopDockTimer();
    _dockTimer = setInterval(() => {
      const el = $('z47DockTimer');
      if (el && _taskStart) el.textContent = _fmtDuration(Date.now() - _taskStart);
    }, 1000);
  }

  function _stopDockTimer() {
    if (_dockTimer) { clearInterval(_dockTimer); _dockTimer = null; }
  }

  function _fmtDuration(ms) {
    const s = Math.floor(ms / 1000);
    const m = Math.floor(s / 60);
    const h = Math.floor(m / 60);
    if (h > 0) return `${h}:${String(m % 60).padStart(2,'0')}:${String(s % 60).padStart(2,'0')}`;
    return `${m}:${String(s % 60).padStart(2,'0')}`;
  }

  function _updateDockMeta() {
    const meta = $('z47DockMeta');
    if (!meta) return;
    fetch('/api/queue').then(r => r.ok ? r.json() : null).then(d => {
      if (!d) return;
      const q = d.queue_length || d.length || 0;
      meta.innerHTML = q > 0
        ? `<span class="z47-dock-chip warn">Queue: ${q}</span>`
        : `<span class="z47-dock-chip">Queue: 0</span>`;
    }).catch(() => {});
  }

  function _pollDockQueue() {
    setInterval(_updateDockMeta, 8000);
  }

  function _showDockReplayChip(sid) {
    const chip = $('z47DockReplayChip');
    const sidEl = $('z47DockReplaySid');
    if (!chip || !sid) return;
    chip.style.display = '';
    if (sidEl) sidEl.textContent = sid.slice(0, 8) + '…';
  }

  function _hideDockReplayChip() {
    const chip = $('z47DockReplayChip');
    if (chip) chip.style.display = 'none';
  }

  function _replayLast() {
    const sid = _activeSid() || _wsState.lastSid;
    if (!sid) return;
    if (window.NxBus) NxBus.emit('dag.replay.start', { sid });
  }

  function _inspectFailure() {
    window.nxSetTab?.('govern');
    document.dispatchEvent(new Event('nx:tab:govern'));
  }

  /* ── Dock queue polling ─────────────────────────────────────────── */
  let _dockQueuePoll = null;

  /* ══════════════════════════════════════════════════════════════════
     Z47E — ARTIFACT INTELLIGENCE
     Enhanced artifact display with metadata in files panel.
     ══════════════════════════════════════════════════════════════════ */

  function _enrichArtifactSection(el) {
    fetch('/api/artifacts/list')
      .then(r => r.ok ? r.json() : null)
      .then(data => {
        if (!data) return;
        const arts = data.artifacts || data || [];
        if (!Array.isArray(arts) || !arts.length) return;
        _raf(() => {
          // Find existing artifact section and replace with enriched version
          const existing = el.querySelector('.z46-section:last-child');
          const artSection = document.createElement('div');
          artSection.className = 'z46-section';
          artSection.innerHTML = `
            <div class="z46-section-label">Artifacts</div>
            ${arts.slice(0, 15).map(a => {
              const linked = a.session_id || a.sid || '';
              const type   = a.type || a.kind || a.format || 'artifact';
              const age    = a.created_at ? _ago(a.created_at) : '';
              return `<div class="z47-artifact-row"
                onclick="window._z47.openArtifact(${JSON.stringify(a.id || a.name || '')})">
                <div class="z47-artifact-header">
                  <span class="z46-file-icon">📦</span>
                  <span class="z47-artifact-name">${esc(a.name || a.id || 'artifact')}</span>
                  <span class="z47-artifact-type-badge">${esc(type)}</span>
                </div>
                <div class="z47-artifact-meta">
                  ${age ? `<span>${esc(age)}</span>` : ''}
                  ${a.size ? `<span>${_sizeStr(a.size)}</span>` : ''}
                  ${linked ? `<span class="z47-artifact-linkage">⛓ ${esc(linked.slice(0,8))}</span>` : ''}
                </div>
              </div>`;
            }).join('')}`;
          // Replace or append
          el.appendChild(artSection);
        });
      })
      .catch(() => {});
  }

  function _openArtifact(id) {
    // Open artifact panel or navigate to it
    window.nxSetTab?.('code');
  }

  /* ══════════════════════════════════════════════════════════════════
     Z47G — UX REALISM AUDIT
     Patch Files panel to open preview on file click.
     ══════════════════════════════════════════════════════════════════ */

  function _patchFilesPanelForPreview() {
    // Intercept clicks on z46-file-row elements that are not directories
    document.addEventListener('click', (e) => {
      const row = e.target.closest('.z46-file-row:not(.z46-dir)');
      if (!row) return;

      // Don't intercept download links
      if (row.tagName === 'A' && row.hasAttribute('download')) return;

      const el = $('nxPanelContent-files');
      if (!el || !el.contains(row)) return;

      // Get the path from the href or title
      const href   = row.getAttribute('href') || '';
      const title  = row.getAttribute('title') || '';
      const fname  = row.querySelector('.z46-file-name')?.textContent || '';

      // Extract path from download URL: /api/download/{sid}/{path}
      const m = href.match(/\/api\/download\/[^/]+\/(.+)/);
      if (m) {
        e.preventDefault();
        const path = decodeURIComponent(m[1]);
        const sid  = _activeSid();
        const ext  = path.split('.').pop().toLowerCase();
        // Previewable extensions
        const PREVIEWABLE = ['txt','md','markdown','json','py','js','ts','jsx','tsx','html','htm','css','scss','sh','bash','yaml','yml','toml','ini','cfg','log','env','rb','go','rs','java','cpp','c','h','php','sql','csv','gitignore','lock'];
        if (PREVIEWABLE.includes(ext)) {
          _openFilePreview(path, sid);
        }
        // For non-previewable, let the download proceed (don't prevent default on non-text)
      }
    });
  }

  /* ══════════════════════════════════════════════════════════════════
     NXBUS EVENT WIRING
     ══════════════════════════════════════════════════════════════════ */

  function _wireEvents() {
    if (!window.NxBus) return;

    NxBus.on('session.start', (d) => {
      _dockSetRunning(d?.task || d?.prompt || 'Mission in progress');
      _persistSession(d?.sid || _activeSid());
      document.body.dataset.nxExec = 'running';
    }, { owner: 'z47' });

    NxBus.on('session.started', (d) => {
      _dockSetRunning(d?.task || d?.prompt || 'Mission in progress');
      _persistSession(d?.sid || _activeSid());
      document.body.dataset.nxExec = 'running';
    }, { owner: 'z47' });

    NxBus.on('session.done', (d) => {
      const duration = _taskStart ? Date.now() - _taskStart : null;
      document.body.dataset.nxExec = 'idle';
      _dockSetComplete(duration);
      const sid = d?.sid || _activeSid();
      if (sid) { _persistSession(sid); _showDockReplayChip(sid); }
    }, { owner: 'z47' });

    NxBus.on('prompt.submit', (d) => {
      if (d?.prompt) _dockSetRunning(d.prompt);
    }, { owner: 'z47' });

    NxBus.on('dag.replay.start', () => {
      _dockSetRunning('Replay in progress');
      document.body.dataset.nxExec = 'running';
    }, { owner: 'z47' });

    // Patch nxTogglePanel to persist panel state
    const origToggle = window.nxTogglePanel;
    if (origToggle) {
      window.nxTogglePanel = function (name) {
        origToggle.apply(this, arguments);
        setTimeout(() => {
          const p = $('nxPanel-' + name);
          const isOpen = p?.style.display === 'flex';
          _persistPanel(isOpen ? name : null);
        }, 100);
      };
    }
  }

  /* ══════════════════════════════════════════════════════════════════
     BOOTSTRAP
     ══════════════════════════════════════════════════════════════════ */

  function _init() {
    _loadState();
    _mountDock();
    _mountFileTabsEl();
    _patchFilesPanelForPreview();
    _registerPaletteItems();
    _restoreFileTabs();

    // Wire NxBus events
    const waitBus = () => {
      if (window.NxBus) { _wireEvents(); return; }
      setTimeout(waitBus, 150);
    };
    waitBus();

    // Restore last session info in dock
    if (_wsState.lastSid) {
      _dockSetIdle(`Last: ${_wsState.lastSid.slice(0,8)}…`);
    }

    console.log('[Phase Z47] Operational file workspace active.');
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => setTimeout(_init, 700));
  } else {
    setTimeout(_init, 700);
  }

  /* ── Public API ──────────────────────────────────────────────────── */
  window._z47 = {
    version:         'Z47',
    openPreview:     _openFilePreview,
    backToFiles:     _backToFiles,
    addFileTab:      _addFileTab,
    closeTab:        _closeFileTab,
    switchTab:       _switchToTab,
    replayLast:      _replayLast,
    inspectFailure:  _inspectFailure,
    openArtifact:    _openArtifact,
    runRecentCmd:    _runRecentCmd,
    enrichArtifacts: _enrichArtifactSection,
    getDockState:    () => ({ timer: _dockTimer, task: _lastTask }),
  };
})();
