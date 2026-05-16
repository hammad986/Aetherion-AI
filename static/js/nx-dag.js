/**
 * nx-dag.js — Aetherion SVG DAG Execution Engine v2
 * ══════════════════════════════════════════════════════════════════
 * Replaces the previous linear list visualization with a realtime
 * SVG-based branching DAG renderer driven by live NxBus events.
 *
 * Features:
 *   - SVG rendering with branching, parallel tracks, dependencies
 *   - 6 node states: pending, running, done, error, blocked, skipped
 *   - Retry branching: shows retry nodes branched from error nodes
 *   - Semantic confidence badges per node
 *   - Zoom + pan (wheel + drag)
 *   - Replay mode (frozen snapshot with step-through)
 *   - Realtime updates via NxBus 'agent.dag_update' + 'nx:dag:update'
 *   - Zero layout thrashing: RAF-batched SVG mutations only
 *
 * NO FAKE DATA. All state derives from NxBus events.
 */
'use strict';

(function () {

  if (window.NxDagEngine) return;

  /* ── Layout Constants ──────────────────────────────────────────── */
  const NODE_W      = 160;
  const NODE_H      = 36;
  const NODE_RX     = 5;
  const COL_GAP     = 56;   // horizontal spacing between stages
  const ROW_GAP     = 18;   // vertical spacing between parallel nodes
  const PAD         = 20;   // canvas padding
  const FONT_SIZE   = 11;
  const BADGE_R     = 7;    // confidence badge radius

  /* ── State Colour Map ──────────────────────────────────────────── */
  const STATE_COLORS = {
    pending:  { fill: 'var(--nds-surface-2)', stroke: 'var(--nds-surface-5)', text: 'var(--nds-text-lo)' },
    running:  { fill: 'var(--nds-accent-subtle)', stroke: 'var(--nds-accent)', text: 'var(--nds-text-hi)' },
    done:     { fill: 'rgba(0,145,24,0.08)',  stroke: 'var(--nds-green)', text: 'var(--nds-green)' },
    error:    { fill: 'rgba(229,34,34,0.06)', stroke: 'var(--nds-red)',  text: 'var(--nds-red)' },
    blocked:  { fill: 'rgba(150,125,0,0.06)', stroke: 'var(--nds-yellow)', text: 'var(--nds-yellow)' },
    skipped:  { fill: 'var(--nds-surface-1)', stroke: 'var(--nds-surface-4)', text: 'var(--nds-text-dim)' },
  };

  /* ── Internal State ────────────────────────────────────────────── */
  let _nodes       = [];   // [{id, label, state, stage, is_critical, retries, dur_ms, confidence, col, row}]
  let _edges       = [];   // [{from, to}]
  let _layout      = null; // {w, h, nodePos: Map<id, {cx,cy}>}
  let _rafPending  = false;
  let _container   = null; // HTMLElement
  let _svg         = null; // SVGElement
  let _replayMode  = false;
  let _replaySnaps = [];   // snapshots for step-through
  let _replayIdx   = 0;
  let _currentSid  = null; // active session ID for localStorage keying

  /* ── Replay Persistence ─────────────────────────────────────────── */
  const REPLAY_VERSION    = 2;
  const REPLAY_LS_PREFIX  = 'nx_dag_replay:';
  const REPLAY_MAX_SNAPS  = 200;
  const REPLAY_MAX_LS_KB  = 1024; // 1 MB guard

  function _lsKey(sid) {
    return REPLAY_LS_PREFIX + (sid || 'global');
  }

  /** Validate a replay payload — guards against corruption. */
  function _validateReplayPayload(obj) {
    if (!obj || typeof obj !== 'object') return false;
    if (obj.version !== REPLAY_VERSION)  return false;
    if (!Array.isArray(obj.snaps))       return false;
    // Each snap must have nodes/edges arrays
    for (const s of obj.snaps.slice(-5)) {  // spot-check last 5
      if (!Array.isArray(s.nodes) || !Array.isArray(s.edges)) return false;
    }
    return true;
  }

  /** Persist current replay buffer to localStorage for the active session. */
  function _persistReplay(sid) {
    sid = sid || _currentSid;
    if (!sid || !_replaySnaps.length) return;
    try {
      const payload = JSON.stringify({
        version:  REPLAY_VERSION,
        sid:      sid,
        saved_at: Date.now(),
        snaps:    _replaySnaps,
      });
      // Size guard — skip if > REPLAY_MAX_LS_KB KB to avoid quota errors
      if (payload.length > REPLAY_MAX_LS_KB * 1024) {
        // Store only last 50 snaps
        const trimmed = _replaySnaps.slice(-50);
        const small = JSON.stringify({ version: REPLAY_VERSION, sid, saved_at: Date.now(), snaps: trimmed });
        localStorage.setItem(_lsKey(sid), small);
      } else {
        localStorage.setItem(_lsKey(sid), payload);
      }
    } catch (_) { /* quota exceeded — silently skip */ }
  }

  /** Load replay buffer from localStorage for a session. Returns true on success. */
  function _loadReplay(sid) {
    sid = sid || _currentSid;
    if (!sid) return false;
    try {
      const raw = localStorage.getItem(_lsKey(sid));
      if (!raw) return false;
      const obj = JSON.parse(raw);
      if (!_validateReplayPayload(obj)) {
        console.warn('[DAG Replay] Corrupt replay data for session', sid, '— discarding.');
        localStorage.removeItem(_lsKey(sid));
        return false;
      }
      _replaySnaps = obj.snaps;
      _replayIdx   = _replaySnaps.length - 1;
      console.info(`[DAG Replay] Loaded ${_replaySnaps.length} snapshots for session ${sid}`);
      return true;
    } catch (e) {
      console.warn('[DAG Replay] Load failed:', e);
      return false;
    }
  }

  /** Export replay as a downloadable JSON string. */
  function replayExport(sid) {
    sid = sid || _currentSid;
    return JSON.stringify({
      version:    REPLAY_VERSION,
      sid:        sid,
      exported_at: Date.now(),
      snaps:      _replaySnaps,
    }, null, 2);
  }

  /** Import a JSON string produced by replayExport(). Returns snap count or 0 on error. */
  function replayImport(jsonStr) {
    try {
      const obj = JSON.parse(jsonStr);
      if (!_validateReplayPayload(obj)) {
        console.error('[DAG Replay] Import rejected: invalid format or version mismatch.');
        return 0;
      }
      _replaySnaps = obj.snaps.slice(0, REPLAY_MAX_SNAPS);
      _replayIdx   = 0;
      _replayMode  = true;
      _applyReplaySnap();
      console.info(`[DAG Replay] Imported ${_replaySnaps.length} snapshots.`);
      return _replaySnaps.length;
    } catch (e) {
      console.error('[DAG Replay] Import parse error:', e);
      return 0;
    }
  }

  let _zoom  = 1.0;
  let _panX  = 0;
  let _panY  = 0;
  let _dragging = false;
  let _dragStart = null;

  /* ── SVG helpers ───────────────────────────────────────────────── */
  const NS = 'http://www.w3.org/2000/svg';
  function _el(tag, attrs = {}) {
    const e = document.createElementNS(NS, tag);
    for (const [k, v] of Object.entries(attrs)) {
      if (v != null) e.setAttribute(k, v);
    }
    return e;
  }
  function _esc(s) { return String(s ?? '').replace(/[<>&"]/g, c => ({ '<':'&lt;','>':'&gt;','&':'&amp;','"':'&quot;' }[c])); }
  function _truncate(s, n = 22) { s = String(s || ''); return s.length > n ? s.slice(0, n - 1) + '…' : s; }

  /* ── Layout Engine ─────────────────────────────────────────────── */
  /**
   * Assigns {col, row} coordinates to nodes using Sugiyama-lite:
   *  - Group by stage (col index).
   *  - Within a stage, stack in order (row index).
   *  - Retry/error branches get an additional offset col.
   */
  function _computeLayout() {
    if (!_nodes.length) return null;

    /* 1. Assign columns by stage order */
    const stageOrder = [];
    const stageMap   = {};
    for (const n of _nodes) {
      const s = n.stage || 'default';
      if (!stageMap[s]) { stageMap[s] = stageOrder.length; stageOrder.push(s); }
      n._col = stageMap[s];
    }

    /* 2. Within each stage, assign rows sequentially */
    const colRowCount = {};
    for (const n of _nodes) {
      const c = n._col;
      if (colRowCount[c] == null) colRowCount[c] = 0;
      n._row = colRowCount[c]++;
    }

    /* 3. Retry nodes branch one column to the right of their parent */
    for (const n of _nodes) {
      if (n.retries > 0) {
        // Find source error node
        const parent = _nodes.find(p => p.id === (n.parent_id || n.id - 1));
        if (parent) {
          n._col = parent._col + 1;
          n._row = parent._row + 0.5;
          /* push all downstream nodes right */
        }
      }
    }

    /* 4. Convert col/row to pixel coords */
    const pos = new Map();
    let maxX = 0, maxY = 0;
    for (const n of _nodes) {
      const cx = PAD + n._col * (NODE_W + COL_GAP) + NODE_W / 2;
      const cy = PAD + n._row * (NODE_H + ROW_GAP) + NODE_H / 2;
      pos.set(n.id, { cx, cy });
      maxX = Math.max(maxX, cx + NODE_W / 2);
      maxY = Math.max(maxY, cy + NODE_H / 2);
    }

    return { w: maxX + PAD, h: maxY + PAD, nodePos: pos };
  }

  /* ── Render ─────────────────────────────────────────────────────── */
  function _scheduleRender() {
    if (_rafPending || !_container) return;
    _rafPending = true;
    requestAnimationFrame(() => {
      _rafPending = false;
      _render();
    });
  }

  function _render() {
    if (!_container) return;

    if (!_nodes.length) {
      _container.innerHTML = '<div style="padding:16px;font-size:11px;color:var(--nds-text-dim);text-align:center">No tasks running.</div>';
      _svg = null;
      return;
    }

    _layout = _computeLayout();

    /* Create or reuse SVG */
    if (!_svg || !_container.contains(_svg)) {
      _container.innerHTML = '';
      _svg = _el('svg', { id: 'agi-dag-svg' });
      _svg.style.cssText = [
        'display:block',
        'width:100%',
        'height:100%',
        'cursor:grab',
        'user-select:none',
        'font-family:var(--nds-font,inherit)',
        'overflow:hidden',
      ].join(';');
      _container.appendChild(_svg);
      _bindInteraction();
    }

    /* Clear and rebuild contents */
    while (_svg.firstChild) _svg.removeChild(_svg.firstChild);

    /* Viewport group (pan/zoom target) */
    const g = _el('g', { id: 'dag-vp', transform: `translate(${_panX},${_panY}) scale(${_zoom})` });
    _svg.appendChild(g);

    /* ── Render edges ── */
    for (const edge of _edges) {
      const fromPos = _layout.nodePos.get(edge.from);
      const toPos   = _layout.nodePos.get(edge.to);
      if (!fromPos || !toPos) continue;

      const x1 = fromPos.cx + NODE_W / 2;
      const y1 = fromPos.cy;
      const x2 = toPos.cx   - NODE_W / 2;
      const y2 = toPos.cy;

      /* Bezier curve for visual quality */
      const mx = (x1 + x2) / 2;
      const path = _el('path', {
        d:            `M${x1},${y1} C${mx},${y1} ${mx},${y2} ${x2},${y2}`,
        fill:         'none',
        stroke:       'var(--nds-surface-5)',
        'stroke-width': '1.5',
        'stroke-dasharray': _isRetryEdge(edge) ? '4,3' : 'none',
        opacity:      '0.7',
      });
      g.appendChild(path);
    }

    /* ── Render nodes ── */
    for (const node of _nodes) {
      const pos = _layout.nodePos.get(node.id);
      if (!pos) continue;

      const x = pos.cx - NODE_W / 2;
      const y = pos.cy - NODE_H / 2;
      const c = STATE_COLORS[node.state] || STATE_COLORS.pending;
      const isRunning = node.state === 'running';

      /* Node group */
      const ng = _el('g', {
        class:    `dag-node dag-node--${node.state}`,
        'data-id': node.id,
        transform: `translate(${x},${y})`,
        style:    'cursor:pointer',
      });
      ng.addEventListener('click', () => _onNodeClick(node));
      g.appendChild(ng);

      /* Background rect */
      const rect = _el('rect', {
        width:  NODE_W,
        height: NODE_H,
        rx:     NODE_RX,
        fill:   c.fill,
        stroke: c.stroke,
        'stroke-width': isRunning ? '1.5' : '1',
      });
      ng.appendChild(rect);

      /* Running pulse overlay */
      if (isRunning) {
        const pulse = _el('rect', {
          width:  NODE_W,
          height: NODE_H,
          rx:     NODE_RX,
          fill:   'none',
          stroke: c.stroke,
          'stroke-width': '2',
          opacity: '0.4',
        });
        const anim = _el('animate', {
          attributeName: 'opacity',
          values:        '0.4;0;0.4',
          dur:           '1.6s',
          repeatCount:   'indefinite',
        });
        pulse.appendChild(anim);
        ng.appendChild(pulse);
      }

      /* Critical-path indicator (left edge glow) */
      if (node.is_critical) {
        const crit = _el('rect', {
          width:  3,
          height: NODE_H,
          rx:     1,
          fill:   'var(--nds-purple)',
        });
        ng.appendChild(crit);
      }

      /* State dot */
      const dot = _el('circle', {
        cx:   12,
        cy:   NODE_H / 2,
        r:    4,
        fill: c.stroke,
      });
      ng.appendChild(dot);

      /* Label */
      const label = _el('text', {
        x:              22,
        y:              NODE_H / 2 + 1,
        'dominant-baseline': 'middle',
        'font-size':    FONT_SIZE,
        fill:           c.text,
        'text-decoration': node.state === 'skipped' ? 'line-through' : 'none',
      });
      label.textContent = _truncate(node.label, 17);
      ng.appendChild(label);

      /* Duration badge */
      if (node.dur_ms != null && node.dur_ms > 0) {
        const durationText = node.dur_ms < 1000
          ? `${node.dur_ms}ms`
          : `${(node.dur_ms / 1000).toFixed(1)}s`;
        const dt = _el('text', {
          x:    NODE_W - 6,
          y:    10,
          'font-size':   8,
          'text-anchor': 'end',
          fill:          'var(--nds-text-dim)',
          'dominant-baseline': 'middle',
        });
        dt.textContent = durationText;
        ng.appendChild(dt);
      }

      /* Retry badge */
      if (node.retries > 0) {
        const rb = _el('g', { transform: `translate(${NODE_W - 18},${NODE_H - 10})` });
        rb.appendChild(_el('circle', { r: BADGE_R, fill: 'var(--nds-yellow)', opacity: '0.9' }));
        const rt = _el('text', { 'text-anchor': 'middle', 'dominant-baseline': 'middle', 'font-size': 8, fill: '#000', 'font-weight': '700' });
        rt.textContent = `×${node.retries}`;
        rb.appendChild(rt);
        ng.appendChild(rb);
      }

      /* Semantic confidence badge */
      if (node.confidence != null && node.state === 'done') {
        const pct = Math.round(node.confidence * 100);
        const badgeColor = pct >= 75 ? 'var(--nds-green)' : pct >= 45 ? 'var(--nds-yellow)' : 'var(--nds-red)';
        const sb = _el('g', { transform: `translate(${NODE_W - 18},${10})`, opacity: '0.85' });
        sb.appendChild(_el('circle', { r: BADGE_R, fill: badgeColor }));
        const st = _el('text', { 'text-anchor': 'middle', 'dominant-baseline': 'middle', 'font-size': 7, fill: '#fff', 'font-weight': '700' });
        st.textContent = `${pct}%`;
        sb.appendChild(st);
        ng.appendChild(sb);
      }

      /* Verification tick for done+verified nodes */
      if (node.state === 'done' && node.verified) {
        const tick = _el('text', {
          x:    NODE_W - 4,
          y:    NODE_H / 2 + 1,
          'font-size':   10,
          'text-anchor': 'end',
          'dominant-baseline': 'middle',
          fill: 'var(--nds-green)',
        });
        tick.textContent = '✓';
        ng.appendChild(tick);
      }
    }

    /* Update SVG viewBox */
    _svg.setAttribute('viewBox', `0 0 ${_layout.w} ${_layout.h}`);
  }

  /* ── Interaction ────────────────────────────────────────────────── */
  function _bindInteraction() {
    if (!_svg) return;

    /* Zoom: wheel */
    _svg.addEventListener('wheel', (e) => {
      e.preventDefault();
      const delta = e.deltaY > 0 ? 0.9 : 1.1;
      _zoom = Math.min(3, Math.max(0.3, _zoom * delta));
      _scheduleRender();
    }, { passive: false });

    /* Pan: drag */
    _svg.addEventListener('mousedown', (e) => {
      if (e.button !== 0) return;
      _dragging   = true;
      _dragStart  = { x: e.clientX - _panX, y: e.clientY - _panY };
      _svg.style.cursor = 'grabbing';
    });
    window.addEventListener('mousemove', (e) => {
      if (!_dragging) return;
      _panX = e.clientX - _dragStart.x;
      _panY = e.clientY - _dragStart.y;
      const vp = _svg.getElementById('dag-vp');
      if (vp) vp.setAttribute('transform', `translate(${_panX},${_panY}) scale(${_zoom})`);
    });
    window.addEventListener('mouseup', () => {
      _dragging = false;
      if (_svg) _svg.style.cursor = 'grab';
    });
  }

  /* ── Node click → detail tooltip ───────────────────────────────── */
  function _onNodeClick(node) {
    if (window.NxBus) {
      NxBus.emit('dag.node.selected', { node });
    }
    /* Also surface a brief tooltip above the DAG container */
    const el = document.getElementById('agiDagTooltip');
    if (el) {
      const dur = node.dur_ms ? (node.dur_ms < 1000 ? `${node.dur_ms}ms` : `${(node.dur_ms/1000).toFixed(1)}s`) : '—';
      const conf = node.confidence != null ? `${Math.round(node.confidence * 100)}%` : '—';
      el.innerHTML = `<strong>${_esc(node.label)}</strong>&nbsp;<span class="agi-val-chip ${node.state}">${node.state}</span><br><span style="font-size:9px;color:var(--nds-text-dim)">dur:${dur} · conf:${conf} · retries:${node.retries||0}</span>`;
      el.style.display = 'block';
    }
  }

  /* ── Retry edge detection ───────────────────────────────────────── */
  function _isRetryEdge(edge) {
    const to = _nodes.find(n => n.id === edge.to);
    return to && to.retries > 0;
  }

  /* ── Public Data API ────────────────────────────────────────────── */

  /**
   * Mount the DAG engine into a container element.
   * @param {HTMLElement|string} container - element or ID
   */
  function mount(container) {
    if (typeof container === 'string') container = document.getElementById(container);
    if (!container) return;
    _container = container;
    _container.style.cssText = 'position:relative;overflow:hidden;width:100%;height:100%;min-height:120px;';

    /* Inject tooltip bar */
    const tt = document.createElement('div');
    tt.id = 'agiDagTooltip';
    tt.style.cssText = [
      'display:none',
      'position:absolute',
      'bottom:0',
      'left:0',
      'right:0',
      'background:var(--nds-surface-2)',
      'border-top:1px solid var(--nds-surface-4)',
      'padding:6px 10px',
      'font-size:10px',
      'color:var(--nds-text-hi)',
      'z-index:2',
      'pointer-events:none',
    ].join(';');
    _container.appendChild(tt);

    _scheduleRender();
  }

  /**
   * Apply a full DAG state snapshot (from agent.dag_update NxBus event).
   * Payload shape: { nodes: [...], edges: [...] }
   */
  function applySnapshot(payload) {
    if (!payload) return;
    _nodes = (payload.nodes || []).map(n => ({
      id:          n.id        ?? n.index ?? 0,
      label:       n.label     ?? n.step_text ?? `Step ${n.id}`,
      state:       n.state     ?? 'pending',
      stage:       n.stage     ?? 'default',
      is_critical: !!n.is_critical_path,
      retries:     n.retries   ?? 0,
      dur_ms:      n.duration_ms ?? null,
      confidence:  n.semantic_confidence ?? null,
      verified:    n.verified  ?? false,
      parent_id:   n.parent_id ?? null,
    }));
    _edges = (payload.edges || []).map(e => ({ from: e.from_id ?? e.from, to: e.to_id ?? e.to }));

    /* Record snapshot for replay */
    if (!_replayMode) {
      _replaySnaps.push({ nodes: JSON.parse(JSON.stringify(_nodes)), edges: JSON.parse(JSON.stringify(_edges)) });
      if (_replaySnaps.length > REPLAY_MAX_SNAPS) _replaySnaps.shift();
      _replayIdx = _replaySnaps.length - 1;
      /* Persist every 10 snaps to avoid excessive localStorage writes */
      if (_replaySnaps.length % 10 === 0) _persistReplay();
    }

    _scheduleRender();
  }

  /**
   * Upsert a single node (used for incremental updates).
   */
  function upsertNode(id, label, state, opts = {}) {
    const idx = _nodes.findIndex(n => n.id === id);
    if (idx >= 0) {
      Object.assign(_nodes[idx], { label, state, ...opts });
    } else {
      _nodes.push({ id, label, state, stage: opts.stage || 'default', is_critical: false, retries: 0, dur_ms: null, confidence: null, verified: false, ...opts });
    }
    _scheduleRender();
  }

  function clearNodes() {
    _nodes = [];
    _edges = [];
    _layout = null;
    _scheduleRender();
  }

  /* ── Replay controls ────────────────────────────────────────────── */
  function replayStart() {
    if (!_replaySnaps.length) return;
    _replayMode = true;
    _replayIdx  = 0;
    _applyReplaySnap();
  }

  function replayStep(delta = 1) {
    _replayIdx = Math.max(0, Math.min(_replaySnaps.length - 1, _replayIdx + delta));
    _applyReplaySnap();
  }

  function replayStop() {
    _replayMode = false;
    _replayIdx  = _replaySnaps.length - 1;
    if (_replaySnaps[_replayIdx]) {
      const s = _replaySnaps[_replayIdx];
      _nodes = JSON.parse(JSON.stringify(s.nodes));
      _edges = JSON.parse(JSON.stringify(s.edges));
    }
    _scheduleRender();
  }

  function _applyReplaySnap() {
    const snap = _replaySnaps[_replayIdx];
    if (!snap) return;
    _nodes = JSON.parse(JSON.stringify(snap.nodes));
    _edges = JSON.parse(JSON.stringify(snap.edges));
    _scheduleRender();
  }

  function getReplayInfo() {
    return { mode: _replayMode, index: _replayIdx, total: _replaySnaps.length };
  }

  /* ── NxBus wiring ───────────────────────────────────────────────── */
  function _wireEvents() {
    if (!window.NxBus) return;

    /* Full snapshot updates from backend DAG */
    NxBus.on('agent.dag_update', applySnapshot, { owner: 'nx-dag-engine' });
    NxBus.on('nx:dag:update',    applySnapshot, { owner: 'nx-dag-engine' });

    /* Incremental task updates from NxAgiSurface signals */
    NxBus.on('agent.task_start', (e) => {
      if (e) upsertNode(e.id || Date.now(), e.label || 'Task', 'running', { stage: e.stage });
    }, { owner: 'nx-dag-engine' });

    NxBus.on('agent.tool_call', (e) => {
      if (e) upsertNode('tool_' + (e.name || 'tool'), e.name || 'tool', 'running');
    }, { owner: 'nx-dag-engine' });

    NxBus.on('agent.tool_result', (e) => {
      if (e) upsertNode('tool_' + (e.name || 'tool'), e.name || 'tool', e.error ? 'error' : 'done');
    }, { owner: 'nx-dag-engine' });

    NxBus.on('session.done', () => {
      /* Mark all running nodes as done */
      for (const n of _nodes) { if (n.state === 'running') n.state = 'done'; }
      _persistReplay(); // Final persist on session completion
      _scheduleRender();
    }, { owner: 'nx-dag-engine' });

    NxBus.on('session.error', () => {
      for (const n of _nodes) { if (n.state === 'running') n.state = 'error'; }
      _persistReplay();
      _scheduleRender();
    }, { owner: 'nx-dag-engine' });

    NxBus.on('session.idle', clearNodes, { owner: 'nx-dag-engine' });

    /* Session restore — recover replay from localStorage */
    const _onSessionEvent = (d) => {
      const sid = d?.sid || d?.session_id;
      if (!sid) return;
      _currentSid = sid;
      if (_loadReplay(sid)) {
        // Show the replay controls since we have history
        if (window.NxBus) NxBus.emit('dag.replay.available', { sid, count: _replaySnaps.length });
      }
    };
    NxBus.on(NxBus.EVENTS?.SESSION_RESTORED || 'nx:session:restored', _onSessionEvent, { owner: 'nx-dag-engine' });
    NxBus.on(NxBus.EVENTS?.SESSION_CREATED  || 'nx:session:created',  (d) => {
      const sid = d?.sid || d?.session_id;
      if (sid) { _currentSid = sid; _replaySnaps = []; _replayIdx = 0; }
    }, { owner: 'nx-dag-engine' });
  }

  /* ── Auto-mount into AGI surface DAG pane ─────────────────────── */
  function _autoMount() {
    const pane = document.getElementById('agiDagSurface');
    if (pane) mount(pane);
  }

  /* ── Public API ─────────────────────────────────────────────────── */
  window.NxDagEngine = {
    mount,
    applySnapshot,
    upsertNode,
    clearNodes,
    replayStart,
    replayStep,
    replayStop,
    getReplayInfo,
    /** Persist current replay to localStorage */
    replaySave: (sid) => _persistReplay(sid),
    /** Load replay from localStorage — returns snap count */
    replayLoad: (sid) => { _loadReplay(sid); return _replaySnaps.length; },
    /** Export replay as JSON string */
    replayExport,
    /** Import replay from JSON string — returns snap count */
    replayImport,
    /** Set active session ID for localStorage keying */
    setSession: (sid) => { _currentSid = sid; },
  };

  /* Init */
  function _init() {
    _autoMount();
    _wireEvents();
    if (!window.NxBus) {
      const t = setInterval(() => {
        if (window.NxBus) { _wireEvents(); clearInterval(t); }
      }, 200);
    }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => setTimeout(_init, 90));
  } else {
    setTimeout(_init, 90);
  }

})();
