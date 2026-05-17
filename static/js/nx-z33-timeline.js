/**
 * nx-z33-timeline.js — Phase Z33B Execution Timeline Dock
 * ════════════════════════════════════════════════════════
 * Expandable bottom dock in the Live tab. Records and groups:
 *   - DAG node completions / errors
 *   - Retry events
 *   - Replanning events (from Z32C)
 *   - Confidence drops (from Z32B)
 *   - HITL interruptions
 *   - Compression events (from Z32A)
 *   - Recovery transitions
 *
 * Semantic grouping: ≥3 consecutive events of the same type
 * are collapsed into a single group row.
 *
 * Rules:
 *  - All events sourced from NxBus — no polling.
 *  - DOM writes are RAF-batched, auto-scroll is opt-in.
 *  - Max 500 events retained per session.
 *  - Timeline cleared on new session start.
 */
'use strict';

(function () {
  if (window._z33timeline) return;

  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s ?? '').replace(/[<>&"]/g, c =>
    ({ '<': '&lt;', '>': '&gt;', '&': '&amp;', '"': '&quot;' }[c]));

  /* ── State ──────────────────────────────────────────────────────── */
  const S = {
    events:     [],
    maxEvents:  500,
    expanded:   false,
    autoScroll: true,
    counts: { retry: 0, replan: 0, hitl: 0, done: 0, error: 0, conf_drop: 0 },
    renderPending: false,
  };

  /* ── Event type registry ────────────────────────────────────────── */
  const TYPES = {
    'node-done':    { dot: 'node-done',  label: (e) => `✓ ${e.nodeId || 'Node'} completed`, badge: null },
    'node-error':   { dot: 'node-error', label: (e) => `✕ ${e.nodeId || 'Node'} failed: ${e.msg || ''}`, badge: null },
    'retry':        { dot: 'retry',      label: (e) => `↺ Retry #${e.n || ''}: ${e.nodeId || ''}`, badge: 'retry' },
    'replan':       { dot: 'replan',     label: (e) => `⬡ Replan [${e.trigger || ''}] → ${e.action || ''}`, badge: 'replan' },
    'hitl':         { dot: 'hitl',       label: (e) => `⏸ HITL: ${e.msg || 'Awaiting approval'}`, badge: 'hitl' },
    'conf-drop':    { dot: 'conf-drop',  label: (e) => `◉ Confidence → ${e.pct || '?'}% (${e.level || ''})`, badge: 'conf' },
    'compress':     { dot: 'compress',   label: (e) => `↯ Context compressed [${e.trigger || ''}] −${e.saved || 0} tok`, badge: null },
    'recovery':     { dot: 'recovery',   label: (e) => `⟳ Recovery: ${e.msg || ''}`, badge: null },
    'session-start':{ dot: null,         label: (e) => `▶ Session started: ${e.sid || ''}`, badge: null },
    'session-done': { dot: null,         label: (e) => `■ Session ended`, badge: null },
  };

  /* ── Core: add event ─────────────────────────────────────────────── */
  function _addEvent(type, data = {}) {
    const def = TYPES[type];
    if (!def) return;

    S.events.push({
      type,
      data,
      ts: Date.now(),
      label: def.label(data),
      dot:   def.dot,
      badge: def.badge,
    });

    // Count for badges
    if (type === 'retry')     S.counts.retry++;
    if (type === 'replan')    S.counts.replan++;
    if (type === 'hitl')      S.counts.hitl++;
    if (type === 'node-done') S.counts.done++;
    if (type === 'node-error')S.counts.error++;
    if (type === 'conf-drop') S.counts.conf_drop++;

    // Trim
    if (S.events.length > S.maxEvents) S.events.splice(0, S.events.length - S.maxEvents);

    _scheduleRender();
  }

  function _clearEvents() {
    S.events = [];
    S.counts = { retry: 0, replan: 0, hitl: 0, done: 0, error: 0, conf_drop: 0 };
    _scheduleRender();
  }

  /* ── Semantic grouping ──────────────────────────────────────────── */
  function _groupEvents(events) {
    const rows = [];
    let i = 0;
    while (i < events.length) {
      const ev = events[i];
      // Look ahead for consecutive same-type events
      let j = i + 1;
      while (j < events.length && events[j].type === ev.type) j++;
      const count = j - i;

      if (count >= 3 && ev.type !== 'session-start' && ev.type !== 'session-done') {
        rows.push({ group: true, type: ev.type, count, dot: ev.dot, badge: ev.badge,
          label: `${_typeName(ev.type)} ×${count}`, ts: ev.ts });
        i = j;
      } else {
        rows.push({ group: false, ...ev });
        i++;
      }
    }
    return rows;
  }

  function _typeName(type) {
    return { 'node-done': 'Node completed', 'node-error': 'Node error',
      retry: 'Retry', replan: 'Replan', hitl: 'HITL', 'conf-drop': 'Confidence drop',
      compress: 'Compression', recovery: 'Recovery' }[type] || type;
  }

  /* ── Header badge summary ───────────────────────────────────────── */
  function _renderHeader() {
    const count = $('z33TlCount');
    const badges = $('z33TlBadges');
    if (count) count.textContent = S.events.length;
    if (!badges) return;

    const parts = [];
    if (S.counts.retry  > 0) parts.push(`<span class="z33-tl-badge retry">↺${S.counts.retry}</span>`);
    if (S.counts.replan > 0) parts.push(`<span class="z33-tl-badge replan">⬡${S.counts.replan}</span>`);
    if (S.counts.hitl   > 0) parts.push(`<span class="z33-tl-badge hitl">⏸${S.counts.hitl}</span>`);
    if (S.counts.conf_drop > 0) parts.push(`<span class="z33-tl-badge conf">◉${S.counts.conf_drop}</span>`);
    badges.innerHTML = parts.join('');
  }

  /* ── Render ──────────────────────────────────────────────────────── */
  function _scheduleRender() {
    if (S.renderPending) return;
    S.renderPending = true;
    requestAnimationFrame(() => {
      S.renderPending = false;
      _render();
    });
  }

  function _render() {
    _renderHeader();

    if (!S.expanded) return;
    const body = $('z33TlBody');
    if (!body) return;

    const rows = _groupEvents(S.events.slice(-200)); // show last 200 grouped
    const wasAtBottom = body.scrollHeight - body.scrollTop <= body.clientHeight + 10;

    body.innerHTML = rows.map(row => {
      const ts = _formatTs(row.ts);
      if (row.group) {
        return `<div class="z33-tl-group">
          ${row.dot ? `<span class="z33-tl-dot ${row.dot}"></span>` : ''}
          <span class="z33-tl-ts">${esc(ts)}</span>
          <span class="z33-tl-text muted">${esc(row.label)}</span>
          <span class="z33-tl-group-count">×${row.count}</span>
        </div>`;
      }
      return `<div class="z33-tl-event">
        <span class="z33-tl-ts">${esc(ts)}</span>
        ${row.dot ? `<span class="z33-tl-dot ${row.dot}"></span>` : '<span style="width:6px;flex-shrink:0"></span>'}
        <span class="z33-tl-text">${esc(row.label)}</span>
      </div>`;
    }).join('');

    if (S.autoScroll && wasAtBottom) body.scrollTop = body.scrollHeight;
  }

  function _formatTs(ms) {
    if (!ms) return '';
    const d = new Date(ms);
    return `${String(d.getMinutes()).padStart(2,'0')}:${String(d.getSeconds()).padStart(2,'0')}`;
  }

  /* ── Toggle expand / collapse ───────────────────────────────────── */
  function _toggle() {
    S.expanded = !S.expanded;
    const dock = $('z33TimelineDock');
    if (dock) dock.classList.toggle('expanded', S.expanded);
    if (S.expanded) _render();
  }

  /* ── NxBus wiring ───────────────────────────────────────────────── */
  function _wireNxBus() {
    if (!window.NxBus) { setTimeout(_wireNxBus, 200); return; }

    NxBus.on('session.started', (e) => {
      _clearEvents();
      _addEvent('session-start', { sid: e?.sid || e?.session_id || '' });
    }, { owner: 'z33tl' });

    NxBus.on('session.done',  () => _addEvent('session-done', {}), { owner: 'z33tl' });
    NxBus.on('session.error', () => _addEvent('node-error',   { msg: 'Session error' }), { owner: 'z33tl' });

    // DAG node events
    NxBus.on('dag.node.done',  (e) => _addEvent('node-done',  { nodeId: e?.id || e?.node_id }), { owner: 'z33tl' });
    NxBus.on('dag.node.error', (e) => _addEvent('node-error', { nodeId: e?.id, msg: e?.error || '' }), { owner: 'z33tl' });

    // Retry events from logs
    NxBus.on('agent.log_row', (e) => {
      const t = (e?.text || '').toLowerCase();
      if (/retry\s*#?\d+/i.test(e?.text || '')) {
        const m = (e.text || '').match(/retry\s*#?(\d+)/i);
        _addEvent('retry', { n: m?.[1] || '', nodeId: '' });
      }
    }, { owner: 'z33tl' });

    // Z32C replanning
    NxBus.on('dag.replan.triggered', (e) => {
      _addEvent('replan', { trigger: e?.plan?.trigger || '', action: e?.plan?.action || '' });
    }, { owner: 'z33tl' });

    // Z32B confidence drops
    NxBus.on('z32.confidence.update', (e) => {
      if (e?.level === 'LOW') {
        _addEvent('conf-drop', { pct: e?.pct || 0, level: 'LOW' });
      }
    }, { owner: 'z33tl' });

    // Z32A compression
    NxBus.on('z32.context.compressed', (e) => {
      _addEvent('compress', { trigger: e?.trigger || '', saved: e?.tokens_saved || 0 });
    }, { owner: 'z33tl' });

    // HITL
    NxBus.on('hitl.escalation', (e) => {
      _addEvent('hitl', { msg: e?.reason || 'Awaiting approval' });
    }, { owner: 'z33tl' });
  }

  /* ── Page unload cleanup ────────────────────────────────────────── */
  window.addEventListener('beforeunload', () => {
    // Nothing to clean — no timers, NxBus handles GC
  });

  /* ── Public API ──────────────────────────────────────────────────── */
  window._z33timeline = {
    toggle: _toggle,
    addEvent: _addEvent,
    clear: _clearEvents,
    setAutoScroll: (v) => { S.autoScroll = v; },
  };

  /* ── Init ────────────────────────────────────────────────────────── */
  function _init() {
    _wireNxBus();
    _renderHeader();
    console.debug('[Phase Z33] Execution timeline dock active.');
  }

  if (window.NX_LOAD_TASKS) {
    window.NX_LOAD_TASKS.push(_init);
  } else if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => setTimeout(_init, 500));
  } else {
    setTimeout(_init, 500);
  }
})();
