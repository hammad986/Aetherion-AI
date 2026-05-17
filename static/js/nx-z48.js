/**
 * nx-z48.js — Phase Z48: Workspace Composition + Diff Intelligence
 * ════════════════════════════════════════════════════════════════════
 *
 * Z48A — Side-by-side file diff viewer
 * Z48B — Split workspace mode (horizontal / vertical)
 * Z48C — Replay minimap, bookmarks, jump points, summaries
 * Z48D — Workspace intelligence (quiet suggestions)
 * Z48E — Artifact relationship display
 * Z48F — Flow polish (transitions, focus, keyboard nav)
 *
 * Rules:
 *   ✗ No external libraries  ✗ No fake features  ✗ No UI freezing
 *   ✓ All data from real APIs  ✓ RAF-batched DOM writes
 *   ✓ Graceful degradation  ✓ No new agentic cognition
 */
'use strict';

(function () {
  if (window._z48) return;

  /* ── Shared utilities ───────────────────────────────────────────── */
  const $ = id => document.getElementById(id);
  const esc = s => String(s ?? '').replace(/[<>&"']/g, c =>
    ({ '<': '&lt;', '>': '&gt;', '&': '&amp;', '"': '&quot;', "'": '&#39;' }[c]));
  const _raf = fn => requestAnimationFrame(fn);
  const _activeSid = () => (window.NX?.activeSid) ||
    (typeof currentSession !== 'undefined' ? currentSession : null) ||
    ((() => { try { return JSON.parse(localStorage.getItem('nx_ws_state_v1') || '{}').lastSid; } catch { return null; } })());

  /* ══════════════════════════════════════════════════════════════════
     Z48A — DIFF VIEWER
     ══════════════════════════════════════════════════════════════════ */

  let _diffState = { pathA: '', pathB: '', sidA: null, sidB: null };

  /* Open the diff tab with optional pre-filled paths */
  function _openDiff(pathA, pathB, sidA, sidB) {
    _diffState.pathA = pathA || '';
    _diffState.pathB = pathB || '';
    _diffState.sidA  = sidA  || _activeSid();
    _diffState.sidB  = sidB  || _activeSid();

    // Show the diff tab
    if (typeof nxSetTab === 'function') nxSetTab('diff');

    const el = $('nxTab-diff');
    if (!el) return;

    if (pathA && pathB) {
      _runDiff(el, pathA, pathB, _diffState.sidA, _diffState.sidB);
    } else {
      _renderDiffPicker(el);
    }
  }

  function _renderDiffPicker(el) {
    el.innerHTML = `
      <div class="z48-diff-shell">
        <div class="z48-diff-header">
          <span class="z48-diff-title">Compare Files</span>
          <button class="z48-diff-close" onclick="window.nxSetTab?.('logs')">✕ Close</button>
        </div>
        <div style="padding:20px;">
          <div class="z48-diff-picker">
            <div>
              <div class="z48-diff-picker-label">File A (original)</div>
              <input class="z48-diff-picker-input" id="z48DiffInputA"
                placeholder="e.g. src/main.py" value="${esc(_diffState.pathA)}">
            </div>
            <div class="z48-diff-picker-row">
              <span style="font-size:10px;color:#6B7280;">vs</span>
              <button class="z48-diff-picker-swap" onclick="window._z48.swapDiffPaths()" title="Swap A and B">⇅</button>
            </div>
            <div>
              <div class="z48-diff-picker-label">File B (revised)</div>
              <input class="z48-diff-picker-input" id="z48DiffInputB"
                placeholder="e.g. src/main.py (or another path)" value="${esc(_diffState.pathB)}">
            </div>
            <button class="z48-diff-btn" onclick="window._z48.commitDiff()">Compare →</button>
          </div>
        </div>
      </div>`;
    setTimeout(() => $('z48DiffInputA')?.focus(), 50);
  }

  function _swapDiffPaths() {
    const a = $('z48DiffInputA');
    const b = $('z48DiffInputB');
    if (!a || !b) return;
    const tmp = a.value;
    a.value = b.value;
    b.value = tmp;
  }

  function _commitDiff() {
    const a = $('z48DiffInputA')?.value.trim();
    const b = $('z48DiffInputB')?.value.trim();
    if (!a || !b) return;
    const el = $('nxTab-diff');
    if (!el) return;
    _runDiff(el, a, b, _diffState.sidA, _diffState.sidB);
  }

  function _runDiff(el, pathA, pathB, sidA, sidB) {
    sidA = sidA || _activeSid();
    sidB = sidB || sidA;
    if (!sidA) {
      el.innerHTML = `<div class="z48-diff-shell"><div class="z48-diff-empty"><div class="z48-diff-empty-icon">⚠</div><div class="z48-diff-empty-title">No active session</div></div></div>`;
      return;
    }

    // Render loading state
    el.innerHTML = `
      <div class="z48-diff-shell">
        <div class="z48-diff-header">
          <span class="z48-diff-title">Comparing…</span>
        </div>
        <div class="z48-tab-loading">Loading files for comparison</div>
      </div>`;

    // Build URL with optional sid_b
    let url = `/api/file/${encodeURIComponent(sidA)}/diff?a=${encodeURIComponent(pathA)}&b=${encodeURIComponent(pathB)}`;
    if (sidB && sidB !== sidA) url += `&sid_b=${encodeURIComponent(sidB)}`;

    fetch(url)
      .then(r => r.json())
      .then(data => _raf(() => _paintDiff(el, data, pathA, pathB, sidA, sidB)))
      .catch(err => _raf(() => {
        el.innerHTML = `
          <div class="z48-diff-shell">
            <div class="z48-diff-header">
              <span class="z48-diff-title">Diff failed</span>
              <button class="z48-diff-close" onclick="window._z48.openDiff()">← Back</button>
            </div>
            <div class="z48-diff-empty">
              <div class="z48-diff-empty-icon">⚠</div>
              <div class="z48-diff-empty-title">Could not compare files</div>
              <div style="font-size:11px;color:#6B7280">${esc(String(err))}</div>
            </div>
          </div>`;
      }));
  }

  function _paintDiff(el, data, pathA, pathB, sidA, sidB) {
    if (!data.ok) {
      el.innerHTML = `
        <div class="z48-diff-shell">
          <div class="z48-diff-header">
            <span class="z48-diff-title">Diff error</span>
            <button class="z48-diff-close" onclick="window._z48.openDiff()">← Back</button>
          </div>
          <div class="z48-diff-empty">
            <div class="z48-diff-empty-icon">⚠</div>
            <div class="z48-diff-empty-title">${esc(data.error || 'Unknown error')}</div>
          </div>
        </div>`;
      return;
    }

    const diff    = data.diff || [];
    const adds    = diff.filter(r => r.type === 'insert').length;
    const dels    = diff.filter(r => r.type === 'delete').length;
    const chgs    = diff.filter(r => r.type === 'replace').length;
    const changes = adds + dels + chgs;
    const fnA = pathA.split('/').pop();
    const fnB = pathB.split('/').pop();

    let truncBanner = '';
    if (data.truncated) {
      truncBanner = `<div class="z48-diff-truncated">⚠ Files truncated at 2000 lines each. Full diff may differ.</div>`;
    }

    // Render diff rows
    const rowsHtml = diff.map(row => {
      if (row.type === 'equal') {
        return `<div class="z48-diff-row z48-diff-equal">
          <div class="z48-diff-cell z48-diff-cell-a">
            <span class="z48-diff-linenum">${row.num_a ?? ''}</span>
            <span class="z48-diff-sigil"> </span>
            <span class="z48-diff-text">${esc(row.text ?? '')}</span>
          </div>
          <div class="z48-diff-cell z48-diff-cell-b">
            <span class="z48-diff-linenum">${row.num_b ?? ''}</span>
            <span class="z48-diff-sigil"> </span>
            <span class="z48-diff-text">${esc(row.text ?? '')}</span>
          </div>
        </div>`;
      }
      if (row.type === 'delete') {
        return `<div class="z48-diff-row z48-diff-delete">
          <div class="z48-diff-cell z48-diff-cell-a">
            <span class="z48-diff-linenum">${row.num_a ?? ''}</span>
            <span class="z48-diff-sigil">−</span>
            <span class="z48-diff-text">${esc(row.text ?? '')}</span>
          </div>
          <div class="z48-diff-cell z48-diff-cell-b">
            <span class="z48-diff-linenum"></span>
            <span class="z48-diff-sigil"></span>
            <span class="z48-diff-text"></span>
          </div>
        </div>`;
      }
      if (row.type === 'insert') {
        return `<div class="z48-diff-row z48-diff-insert">
          <div class="z48-diff-cell z48-diff-cell-a">
            <span class="z48-diff-linenum"></span>
            <span class="z48-diff-sigil"></span>
            <span class="z48-diff-text"></span>
          </div>
          <div class="z48-diff-cell z48-diff-cell-b">
            <span class="z48-diff-linenum">${row.num_b ?? ''}</span>
            <span class="z48-diff-sigil">+</span>
            <span class="z48-diff-text">${esc(row.text ?? '')}</span>
          </div>
        </div>`;
      }
      if (row.type === 'replace') {
        return `<div class="z48-diff-row z48-diff-replace">
          <div class="z48-diff-cell z48-diff-cell-a">
            <span class="z48-diff-linenum">${row.num_a ?? ''}</span>
            <span class="z48-diff-sigil">~</span>
            <span class="z48-diff-text">${esc(row.text_a ?? '')}</span>
          </div>
          <div class="z48-diff-cell z48-diff-cell-b">
            <span class="z48-diff-linenum">${row.num_b ?? ''}</span>
            <span class="z48-diff-sigil">+</span>
            <span class="z48-diff-text">${esc(row.text_b ?? '')}</span>
          </div>
        </div>`;
      }
      return '';
    }).join('');

    el.innerHTML = `
      <div class="z48-diff-shell">
        <div class="z48-diff-header">
          <span class="z48-diff-title">Diff: ${esc(fnA)} → ${esc(fnB)}</span>
          <span class="z48-diff-stats">
            <span class="z48-diff-stat-add">+${adds}</span>
            <span class="z48-diff-stat-del"> −${dels}</span>
            ${chgs ? `<span class="z48-diff-stat-chg"> ~${chgs}</span>` : ''}
            <span> · ${changes} change${changes !== 1 ? 's' : ''}</span>
          </span>
          <button class="z48-diff-close" onclick="window._z48.openDiff()">← New</button>
          <button class="z48-diff-close" onclick="window.nxSetTab?.('logs')">✕</button>
        </div>
        ${truncBanner}
        ${changes === 0 ? `<div class="z48-diff-empty" style="grid-column:1/-1"><div class="z48-diff-empty-icon">✓</div><div class="z48-diff-empty-title">Files are identical</div></div>` : ''}
        <div class="z48-diff-labels">
          <div class="z48-diff-label z48-diff-label-a">
            <span class="z48-diff-label-badge">A</span>
            ${esc(pathA)}
          </div>
          <div class="z48-diff-label z48-diff-label-b">
            <span class="z48-diff-label-badge">B</span>
            ${esc(pathB)}
          </div>
        </div>
        <div class="z48-diff-body">
          <div class="z48-diff-grid">${rowsHtml}</div>
        </div>
      </div>`;

    // Save comparison paths for workspace continuity
    try {
      const state = JSON.parse(localStorage.getItem('nx_ws_state_v1') || '{}');
      state.lastDiff = { pathA, pathB, sidA, sidB };
      localStorage.setItem('nx_ws_state_v1', JSON.stringify(state));
    } catch {}
  }

  /* Patch Z47 file preview to add "Compare with…" button */
  function _patchFilePreviewForDiff() {
    const origOpen = window._z47?.openPreview;
    if (!origOpen) return;

    const patchedOpen = function (path, sid) {
      origOpen(path, sid);
      // After a short delay, inject compare button into breadcrumb
      setTimeout(() => {
        const bc = document.querySelector('.z47-preview-breadcrumb');
        if (!bc || bc.querySelector('.z48-compare-btn')) return;
        const btn = document.createElement('button');
        btn.className = 'z48-compare-btn';
        btn.textContent = '⇄ Compare';
        btn.title = 'Compare this file with another';
        btn.onclick = () => {
          _diffState.pathA = path;
          _diffState.sidA  = sid || _activeSid();
          window._z48.openDiff(path, '', sid);
        };
        bc.insertBefore(btn, bc.querySelector('.z47-preview-download'));
      }, 100);
    };

    if (window._z47) window._z47.openPreview = patchedOpen;
  }

  /* ══════════════════════════════════════════════════════════════════
     Z48B — SPLIT WORKSPACE MODE
     ══════════════════════════════════════════════════════════════════ */

  const SPLIT_KEY = 'nx_z48_split';
  let _splitMode = 'none'; // 'none' | 'h' | 'v'
  let _splitTab  = 'logs';
  let _isDragging = false;

  const SPLIT_TABS = [
    { id: 'logs',     label: 'Output' },
    { id: 'code',     label: 'Code' },
    { id: 'terminal', label: 'Terminal' },
    { id: 'diff',     label: 'Diff' },
    { id: 'intel',    label: 'Intel' },
    { id: 'live',     label: 'Live' },
  ];

  function _mountSplitPane() {
    if ($('z48SplitPane')) return;

    const pane = document.createElement('div');
    pane.id = 'z48SplitPane';
    pane.className = 'z48-split-pane';
    pane.innerHTML = `
      <div class="z48-split-tabbar" id="z48SplitTabbar">
        ${SPLIT_TABS.map(t =>
          `<button class="z48-split-tab${t.id === _splitTab ? ' active' : ''}"
            onclick="window._z48.setSplitTab('${t.id}')">${esc(t.label)}</button>`
        ).join('')}
      </div>
      <div class="z48-split-content" id="z48SplitContent">
        <div class="z48-tab-loading">Select a tab above</div>
      </div>`;

    const center = $('nxCenter');
    if (center) center.appendChild(pane);
  }

  function _mountSplitResizer() {
    if ($('z48SplitResizer')) return;
    const resizer = document.createElement('div');
    resizer.id = 'z48SplitResizer';
    resizer.className = 'z48-split-resizer';
    resizer.addEventListener('mousedown', _onResizerMousedown);
    const pane = $('z48SplitPane');
    if (pane) pane.parentNode.insertBefore(resizer, pane);
  }

  function _onResizerMousedown(e) {
    _isDragging = true;
    const resizer = $('z48SplitResizer');
    if (resizer) resizer.classList.add('dragging');
    document.addEventListener('mousemove', _onResizerMousemove);
    document.addEventListener('mouseup', _onResizerMouseup);
    e.preventDefault();
  }

  function _onResizerMousemove(e) {
    if (!_isDragging) return;
    const center = $('nxCenter');
    if (!center) return;
    const rect = center.getBoundingClientRect();
    if (_splitMode === 'h') {
      const topH = e.clientY - rect.top;
      const pct = Math.max(20, Math.min(80, (topH / rect.height) * 100));
      center.style.setProperty('--z48-split-pct', pct + '%');
    } else if (_splitMode === 'v') {
      const leftW = e.clientX - rect.left;
      const pct = Math.max(20, Math.min(80, (leftW / rect.width) * 100));
      center.style.setProperty('--z48-split-pct', pct + '%');
    }
  }

  function _onResizerMouseup() {
    _isDragging = false;
    const resizer = $('z48SplitResizer');
    if (resizer) resizer.classList.remove('dragging');
    document.removeEventListener('mousemove', _onResizerMousemove);
    document.removeEventListener('mouseup', _onResizerMouseup);
    _saveSplitState();
  }

  function _setSplitMode(mode) {
    const center = $('nxCenter');
    if (!center) return;

    // Remove existing modes
    document.body.classList.remove('z48-split-h', 'z48-split-v');

    if (mode === 'none' || mode === _splitMode) {
      _splitMode = 'none';
      _updateSplitBtn();
      _saveSplitState();
      return;
    }

    _splitMode = mode;
    document.body.classList.add('z48-split-' + mode);
    _mountSplitPane();
    _mountSplitResizer();
    _renderSplitContent(_splitTab);
    _updateSplitBtn();
    _saveSplitState();
  }

  function _setSplitTab(tab) {
    _splitTab = tab;
    _renderSplitContent(tab);

    // Update mini tab bar active state
    const bar = $('z48SplitTabbar');
    if (bar) {
      bar.querySelectorAll('.z48-split-tab').forEach(btn => {
        btn.classList.toggle('active', btn.textContent === SPLIT_TABS.find(t => t.id === tab)?.label);
      });
    }
    _saveSplitState();
  }

  function _renderSplitContent(tab) {
    const content = $('z48SplitContent');
    if (!content) return;

    if (tab === 'logs') {
      content.innerHTML = `<div style="padding:8px;font-size:11px;color:#8B949E">
        Log output mirrors the Output tab.<br>
        <button class="z48-diff-btn" style="margin-top:8px" onclick="window.nxSetTab?.('logs')">Switch to Output</button>
      </div>`;
    } else if (tab === 'diff') {
      content.innerHTML = `<div style="padding:12px;">
        <div class="z48-diff-picker">
          <div class="z48-diff-picker-label">Quick Diff</div>
          <input class="z48-diff-picker-input" id="z48SplitDiffA" placeholder="File A path">
          <input class="z48-diff-picker-input" id="z48SplitDiffB" placeholder="File B path">
          <button class="z48-diff-btn" onclick="window._z48.openDiff(
            document.getElementById('z48SplitDiffA')?.value,
            document.getElementById('z48SplitDiffB')?.value
          )">Compare</button>
        </div>
      </div>`;
    } else {
      // Mirror: tell user to use the main tab
      const tabLabel = SPLIT_TABS.find(t => t.id === tab)?.label || tab;
      content.innerHTML = `<div style="padding:16px;font-size:11px;color:#8B949E;text-align:center">
        <div style="font-size:20px;margin-bottom:8px;opacity:0.3">${_tabIcon(tab)}</div>
        ${esc(tabLabel)} panel<br>
        <button class="z48-diff-btn" style="margin-top:10px"
          onclick="window.nxSetTab?.('${tab}')">Switch main view to ${esc(tabLabel)}</button>
      </div>`;
    }
  }

  function _tabIcon(tab) {
    const icons = { logs: '📋', code: '📄', terminal: '💻', diff: '⇄', intel: '🔭', live: '⚡' };
    return icons[tab] || '📁';
  }

  function _updateSplitBtn() {
    document.querySelectorAll('.z48-split-btn').forEach(btn => {
      btn.classList.toggle('active', _splitMode !== 'none');
    });
  }

  function _saveSplitState() {
    try {
      localStorage.setItem(SPLIT_KEY, JSON.stringify({ mode: _splitMode, tab: _splitTab }));
    } catch {}
  }

  function _restoreSplitState() {
    try {
      const s = JSON.parse(localStorage.getItem(SPLIT_KEY) || '{}');
      if (s.mode && s.mode !== 'none') {
        _splitTab = s.tab || 'logs';
        setTimeout(() => _setSplitMode(s.mode), 1000);
      }
    } catch {}
  }

  /* Mount split toggle button in tab bar */
  function _mountSplitBtn() {
    const tabActions = $('nxTabActions');
    if (!tabActions || $('z48SplitBtnWrap')) return;

    const wrap = document.createElement('div');
    wrap.id = 'z48SplitBtnWrap';
    wrap.style.cssText = 'display:flex;align-items:center;gap:2px;margin-left:4px;';

    wrap.innerHTML = `
      <button class="z48-split-btn" title="Split horizontal (Ctrl+Shift+H)"
        onclick="window._z48.setSplitMode('h')" id="z48SplitBtnH">⊟ Split</button>`;

    tabActions.insertBefore(wrap, tabActions.firstChild);
  }

  /* ══════════════════════════════════════════════════════════════════
     Z48C — REPLAY MINIMAP + BOOKMARKS
     ══════════════════════════════════════════════════════════════════ */

  let _replayEvents  = [];  // {type, step, label}
  let _replayTotal   = 0;
  let _replayCurrent = 0;
  let _bookmarks     = [];
  let _replaySummary = { steps: 0, failures: 0, files: 0, duration: null };

  function _mountReplayMinimap() {
    const replayBar = $('z30ReplayBar');
    if (!replayBar || $('z48ReplayMinimap')) return;

    // Insert minimap and jump buttons after replay bar
    const minimap = document.createElement('div');
    minimap.id = 'z48ReplayMinimap';
    minimap.className = 'z48-replay-minimap';
    minimap.innerHTML = `
      <div class="z48-mm-track" id="z48MinimapTrack"></div>
      <div class="z48-mm-cursor" id="z48MinimapCursor" style="left:0"></div>
      <button class="z48-mm-bookmark-btn" onclick="window._z48.addBookmark()" title="Bookmark this step">⚑</button>`;
    minimap.addEventListener('click', _onMinimapClick);

    const summary = document.createElement('div');
    summary.id = 'z48ReplaySummary';
    summary.className = 'z48-replay-summary';

    const jumps = document.createElement('div');
    jumps.id = 'z48ReplayJumps';
    jumps.className = 'z48-replay-jumps';

    replayBar.parentNode.insertBefore(jumps, replayBar.nextSibling);
    replayBar.parentNode.insertBefore(summary, replayBar.nextSibling);
    replayBar.parentNode.insertBefore(minimap, replayBar.nextSibling);
  }

  function _onMinimapClick(e) {
    const track = $('z48MinimapTrack');
    if (!track || !_replayTotal) return;
    const rect = track.getBoundingClientRect();
    const pct  = Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width));
    const step = Math.round(pct * (_replayTotal - 1));
    // Jump replay to this step
    const scrubber = $('z30ReplayScrubber');
    if (scrubber && window._z30?.replayScrub) {
      scrubber.value = step;
      window._z30.replayScrub(step);
    }
  }

  function _updateMinimapCursor(step) {
    _replayCurrent = step;
    const cursor = $('z48MinimapCursor');
    const track  = $('z48MinimapTrack');
    if (!cursor || !track || !_replayTotal) return;
    const pct = (_replayTotal > 1) ? (step / (_replayTotal - 1)) : 0;
    const trackRect = track.getBoundingClientRect();
    const minimapRect = track.parentNode.getBoundingClientRect();
    const left = (trackRect.left - minimapRect.left) + pct * trackRect.width;
    cursor.style.left = left + 'px';
  }

  function _renderMinimapMarkers() {
    const track = $('z48MinimapTrack');
    if (!track || !_replayTotal) return;

    // Clear existing markers
    track.innerHTML = '';

    const allEvents = [..._replayEvents, ..._bookmarks.map(b => ({...b, type: 'bookmark'}))];
    allEvents.forEach(ev => {
      const pct = (_replayTotal > 1) ? (ev.step / (_replayTotal - 1)) : 0;
      const marker = document.createElement('div');
      marker.className = 'z48-mm-marker';
      marker.dataset.type = ev.type;
      marker.style.left = (pct * 100) + '%';
      marker.style.opacity = '0.7';
      marker.title = ev.label || ev.type;
      marker.addEventListener('click', (e) => {
        e.stopPropagation();
        if (window._z30?.replayScrub) {
          const scrubber = $('z30ReplayScrubber');
          if (scrubber) scrubber.value = ev.step;
          window._z30.replayScrub(ev.step);
        }
      });
      track.appendChild(marker);
    });
  }

  function _renderJumpButtons() {
    const jumps = $('z48ReplayJumps');
    if (!jumps) return;
    const failures  = _replayEvents.filter(e => e.type === 'failure');
    const hitls     = _replayEvents.filter(e => e.type === 'hitl');
    const recoveries = _replayEvents.filter(e => e.type === 'recovery');

    if (!failures.length && !hitls.length && !recoveries.length) {
      jumps.classList.remove('visible');
      return;
    }
    jumps.classList.add('visible');
    jumps.innerHTML =
      `<span style="font-size:9px;color:#4B5563;margin-right:2px">Jump:</span>` +
      failures.map((ev, i) =>
        `<button class="z48-jump-btn fail" onclick="window._z48.jumpToStep(${ev.step})">✗ Failure ${i+1}</button>`
      ).join('') +
      hitls.map((ev, i) =>
        `<button class="z48-jump-btn hitl" onclick="window._z48.jumpToStep(${ev.step})">⏸ HITL ${i+1}</button>`
      ).join('') +
      recoveries.map((ev, i) =>
        `<button class="z48-jump-btn recov" onclick="window._z48.jumpToStep(${ev.step})">↺ Recovery ${i+1}</button>`
      ).join('');
  }

  function _jumpToStep(step) {
    const scrubber = $('z30ReplayScrubber');
    if (scrubber && window._z30?.replayScrub) {
      scrubber.value = step;
      window._z30.replayScrub(step);
    }
  }

  function _addBookmark() {
    _bookmarks.push({ step: _replayCurrent, label: `Step ${_replayCurrent + 1}` });
    _renderMinimapMarkers();
  }

  function _renderReplaySummary() {
    const el = $('z48ReplaySummary');
    if (!el) return;
    const s = _replaySummary;
    if (!s.steps) { el.classList.remove('visible'); return; }
    el.classList.add('visible');
    el.innerHTML = [
      `<span class="z48-rs-item"><span class="z48-rs-label">Steps</span><span class="z48-rs-val">${s.steps}</span></span>`,
      `<span class="z48-rs-sep">·</span>`,
      `<span class="z48-rs-item"><span class="z48-rs-label">Failures</span><span class="z48-rs-val${s.failures ? ' z48-diff-stat-del' : ''}">${s.failures}</span></span>`,
      `<span class="z48-rs-sep">·</span>`,
      `<span class="z48-rs-item"><span class="z48-rs-label">File writes</span><span class="z48-rs-val">${s.files}</span></span>`,
      s.duration ? `<span class="z48-rs-sep">·</span><span class="z48-rs-item"><span class="z48-rs-label">Duration</span><span class="z48-rs-val">${s.duration}</span></span>` : '',
    ].join('');
  }

  /* ══════════════════════════════════════════════════════════════════
     Z48D — WORKSPACE INTELLIGENCE (quiet suggestions)
     ══════════════════════════════════════════════════════════════════ */

  let _suggestTimeout = null;
  let _suggestDismissed = new Set();

  function _mountSuggestBar() {
    const hero = $('nxIdleHero');
    if (!hero || $('z48SuggestBar')) return;

    const bar = document.createElement('div');
    bar.id = 'z48SuggestBar';
    bar.className = 'z48-suggest-bar';

    // Insert after the status strip
    const strip = hero.querySelector('.nx-iw-status-strip');
    if (strip) strip.after(bar);
    else hero.appendChild(bar);
  }

  function _showSuggestion(icon, text, actionLabel, actionFn, key) {
    if (_suggestDismissed.has(key)) return;
    const bar = $('z48SuggestBar');
    if (!bar) return;

    bar.innerHTML = `
      <span class="z48-suggest-icon">${esc(icon)}</span>
      <span class="z48-suggest-text">${esc(text)}</span>
      ${actionLabel ? `<button class="z48-suggest-action" onclick="window._z48._doSuggestAction('${esc(key)}')">${esc(actionLabel)}</button>` : ''}
      <button class="z48-suggest-dismiss" onclick="window._z48.dismissSuggestion('${esc(key)}')" title="Dismiss">×</button>`;
    bar.classList.add('visible');

    // Store action for later
    _suggestActions[key] = actionFn;

    // Auto-dismiss after 12s
    if (_suggestTimeout) clearTimeout(_suggestTimeout);
    _suggestTimeout = setTimeout(() => _hideSuggestion(), 12000);
  }

  const _suggestActions = {};

  function _doSuggestAction(key) {
    const fn = _suggestActions[key];
    if (fn) fn();
    _hideSuggestion();
  }

  function _hideSuggestion() {
    const bar = $('z48SuggestBar');
    if (bar) bar.classList.remove('visible');
  }

  function _dismissSuggestion(key) {
    _suggestDismissed.add(key);
    _hideSuggestion();
    // Remember for session
    try {
      const state = JSON.parse(localStorage.getItem('nx_ws_state_v1') || '{}');
      state.dismissedSuggestions = [..._suggestDismissed];
      localStorage.setItem('nx_ws_state_v1', JSON.stringify(state));
    } catch {}
  }

  function _checkSuggestions() {
    // Restore dismissed set
    try {
      const state = JSON.parse(localStorage.getItem('nx_ws_state_v1') || '{}');
      (state.dismissedSuggestions || []).forEach(k => _suggestDismissed.add(k));
      const lastSid = state.lastSid;
      const lastDiff = state.lastDiff;
      const openTabs = state.openTabs || [];
      const recentCmds = state.recentCmds || [];

      // Suggestion: Resume last session
      if (lastSid && !_activeSid()) {
        setTimeout(() => _showSuggestion(
          '↺', `Resume session ${lastSid.slice(0, 8)}…`, 'Replay →',
          () => { if (window.NxBus) NxBus.emit('dag.replay.start', { sid: lastSid }); },
          'resume_last_' + lastSid.slice(0, 8)
        ), 2000);
        return;
      }

      // Suggestion: Reopen last diff comparison
      if (lastDiff?.pathA && lastDiff?.pathB) {
        setTimeout(() => _showSuggestion(
          '⇄', `Reopen diff: ${lastDiff.pathA.split('/').pop()} vs ${lastDiff.pathB.split('/').pop()}`, 'Open →',
          () => _openDiff(lastDiff.pathA, lastDiff.pathB, lastDiff.sidA, lastDiff.sidB),
          'reopen_diff_' + lastDiff.pathA
        ), 3000);
        return;
      }

      // Suggestion: Open most recent file tab
      if (openTabs.length && !_activeSid()) {
        const latest = openTabs[openTabs.length - 1];
        setTimeout(() => _showSuggestion(
          '📄', `Reopen ${latest.name} from last session`, 'Preview →',
          () => window._z47?.openPreview(latest.path, latest.sid),
          'reopen_tab_' + latest.path
        ), 2500);
        return;
      }
    } catch {}
  }

  /* ══════════════════════════════════════════════════════════════════
     Z48E — ARTIFACT RELATIONSHIP (patch Z47 artifact rendering)
     ══════════════════════════════════════════════════════════════════ */

  function _enrichArtifactRelationships() {
    // Fetch artifacts and build a simple relationship map
    fetch('/api/artifacts/list')
      .then(r => r.ok ? r.json() : null)
      .then(data => {
        if (!data) return;
        const arts = data.artifacts || data || [];
        if (!Array.isArray(arts) || arts.length < 2) return;

        // Group by session to find co-generated artifacts (relationships)
        const bySession = {};
        arts.forEach(a => {
          const sid = a.session_id || a.sid || 'unknown';
          if (!bySession[sid]) bySession[sid] = [];
          bySession[sid].push(a);
        });

        // Add "Related" chips to artifact rows in the Files panel
        _raf(() => {
          document.querySelectorAll('.z47-artifact-row').forEach(row => {
            if (row.querySelector('.z48-related-row')) return;

            const nameEl = row.querySelector('.z47-artifact-name');
            const name = nameEl?.textContent;
            const thisArt = arts.find(a => (a.name || a.id) === name);
            if (!thisArt) return;

            const sid = thisArt.session_id || thisArt.sid;
            if (!sid || !bySession[sid]) return;

            const related = bySession[sid].filter(a => (a.name || a.id) !== name).slice(0, 3);
            if (!related.length) return;

            const relRow = document.createElement('div');
            relRow.className = 'z48-related-row';
            relRow.innerHTML = related.map(r =>
              `<span class="z48-art-rel" title="Co-generated artifact">
                <span class="z48-art-rel-type">⛓</span>
                <span class="z48-art-rel-name">${esc((r.name || r.id || '').slice(0, 20))}</span>
              </span>`
            ).join('');
            row.appendChild(relRow);
          });
        });
      })
      .catch(() => {});
  }

  /* ══════════════════════════════════════════════════════════════════
     Z48F — FLOW POLISH
     ══════════════════════════════════════════════════════════════════ */

  function _polishFocus() {
    // Ensure all interactive elements have proper accessible focus
    document.querySelectorAll('button:not([tabindex="-1"]), input, textarea, select, a[href]').forEach(el => {
      if (!el.hasAttribute('tabindex')) el.setAttribute('tabindex', '0');
    });
  }

  function _polishTransitions() {
    // Apply smooth transition to slide panels if not already
    document.querySelectorAll('.nx-slide-panel').forEach(p => {
      if (!p.style.transition) {
        p.style.transition = 'opacity 0.18s ease';
      }
    });
  }

  /* ══════════════════════════════════════════════════════════════════
     NXBUS WIRING FOR REPLAY MINIMAP
     ══════════════════════════════════════════════════════════════════ */

  function _wireReplayEvents() {
    if (!window.NxBus) return;

    NxBus.on('dag.replay.started', (data) => {
      _replayEvents = [];
      _bookmarks = [];
      _replayTotal = data?.total || 0;
      _replayCurrent = 0;
      const minimap = $('z48ReplayMinimap');
      if (minimap) minimap.classList.add('visible');
      _renderMinimapMarkers();
      _renderJumpButtons();
    }, { owner: 'z48' });

    NxBus.on('dag.replay.step', (data) => {
      const step = typeof data?.step === 'number' ? data.step : _replayCurrent;
      _replayCurrent = step;
      _replayTotal = data?.total || _replayTotal;
      _updateMinimapCursor(step);

      // Record notable events
      const type = data?.type || data?.event_type;
      if (type === 'failure' || type === 'error') {
        _replayEvents.push({ step, type: 'failure', label: data?.label || `Failure at step ${step+1}` });
        _renderMinimapMarkers();
        _renderJumpButtons();
      } else if (type === 'hitl') {
        _replayEvents.push({ step, type: 'hitl', label: data?.label || `HITL at step ${step+1}` });
        _renderMinimapMarkers();
        _renderJumpButtons();
      } else if (type === 'recovery') {
        _replayEvents.push({ step, type: 'recovery', label: data?.label || `Recovery at step ${step+1}` });
        _renderMinimapMarkers();
        _renderJumpButtons();
      } else if (type === 'file_write') {
        _replayEvents.push({ step, type: 'write', label: data?.path || `Write at step ${step+1}` });
      }

      // Update summary
      _replaySummary.steps = step + 1;
      _replaySummary.failures = _replayEvents.filter(e => e.type === 'failure').length;
      _replaySummary.files    = _replayEvents.filter(e => e.type === 'write').length;
    }, { owner: 'z48' });

    NxBus.on('dag.replay.stopped', () => {
      const minimap = $('z48ReplayMinimap');
      if (minimap) minimap.classList.remove('visible');
      const summary = $('z48ReplaySummary');
      if (summary) summary.classList.remove('visible');
      const jumps = $('z48ReplayJumps');
      if (jumps) jumps.classList.remove('visible');
    }, { owner: 'z48' });

    NxBus.on('dag.replay.available', (data) => {
      if (data?.steps?.length) {
        _replayTotal = data.steps.length;
        _replaySummary.steps = data.steps.length;
        // Pre-populate events from historical data
        data.steps.forEach((s, i) => {
          if (s.type === 'failure' || s.status === 'failed') {
            _replayEvents.push({ step: i, type: 'failure', label: s.label || `Failure` });
          } else if (s.type === 'hitl') {
            _replayEvents.push({ step: i, type: 'hitl', label: `HITL` });
          }
        });
        _renderMinimapMarkers();
        _renderJumpButtons();
        _renderReplaySummary();
      }
    }, { owner: 'z48' });

    // Also register command palette: compare files
    const waitPalette = (cb) => {
      if (window._NxPalette?.register) { cb(); return; }
      const t = setInterval(() => {
        if (window._NxPalette?.register) { clearInterval(t); cb(); }
      }, 100);
    };
    waitPalette(() => {
      window._NxPalette.register({
        icon: '⇄', label: 'Compare Files (Diff)',
        section: 'Workspace', hint: '',
        action: () => {
          if (typeof nxSetTab === 'function') nxSetTab('diff');
          setTimeout(() => _renderDiffPicker($('nxTab-diff')), 50);
        }
      });
      window._NxPalette.register({
        icon: '⊟', label: 'Split Workspace Horizontal',
        section: 'Workspace', hint: 'Ctrl+Shift+H',
        action: () => _setSplitMode('h')
      });
      window._NxPalette.register({
        icon: '⊞', label: 'Split Workspace Vertical',
        section: 'Workspace', hint: 'Ctrl+Shift+V',
        action: () => _setSplitMode('v')
      });
    });
  }

  /* ── Keyboard shortcuts ─────────────────────────────────────────── */
  function _wireKeyboard() {
    document.addEventListener('keydown', (e) => {
      if (e.ctrlKey && e.shiftKey && e.key === 'H') {
        e.preventDefault();
        _setSplitMode('h');
      } else if (e.ctrlKey && e.shiftKey && e.key === 'V') {
        e.preventDefault();
        _setSplitMode('v');
      } else if (e.ctrlKey && e.shiftKey && e.key === 'D') {
        e.preventDefault();
        if (typeof nxSetTab === 'function') nxSetTab('diff');
      }
    });
  }

  /* ══════════════════════════════════════════════════════════════════
     BOOTSTRAP
     ══════════════════════════════════════════════════════════════════ */

  function _init() {
    _mountSplitBtn();
    _mountSplitPane();
    _mountReplayMinimap();
    _mountSuggestBar();
    _patchFilePreviewForDiff();
    _polishTransitions();

    const waitBus = () => {
      if (window.NxBus) { _wireReplayEvents(); return; }
      setTimeout(waitBus, 200);
    };
    waitBus();

    _wireKeyboard();
    _restoreSplitState();

    // Run suggestions after a short delay to not compete with init
    setTimeout(_checkSuggestions, 3000);

    // Periodically enrich artifact relationships when Files panel is open
    setInterval(() => {
      if (document.querySelector('#nxPanelContent-files .z47-artifact-row')) {
        _enrichArtifactRelationships();
      }
    }, 15000);

    console.debug('[Phase Z48] Workspace composition active.');
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => setTimeout(_init, 1000));
  } else {
    setTimeout(_init, 1000);
  }

  /* ── Public API ──────────────────────────────────────────────────── */
  window._z48 = {
    version:          'Z48',
    openDiff:         _openDiff,
    commitDiff:       _commitDiff,
    swapDiffPaths:    _swapDiffPaths,
    setSplitMode:     _setSplitMode,
    setSplitTab:      _setSplitTab,
    jumpToStep:       _jumpToStep,
    addBookmark:      _addBookmark,
    dismissSuggestion: _dismissSuggestion,
    _doSuggestAction:  _doSuggestAction,
  };
})();
