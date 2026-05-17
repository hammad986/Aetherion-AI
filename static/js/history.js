(function () {
    'use strict';

    /* ═══════════════════════════════════════════════════════════════
       PHASE 15 — Learning Dashboard
       ═══════════════════════════════════════════════════════════════ */

    let _p15Loaded = false;
    let _p15AutoTimer = null;

    window.p15LoadDashboard = async function () {
      _p15Loaded = true;
      await Promise.all([_p15LoadMetrics(), _p15LoadStrategies(), _p15LoadFailures(), _p15LoadTimeline()]);
    };

    async function _p15LoadMetrics() {
      try {
        const r = await fetch('/api/dashboard/metrics');
        const d = await r.json();
        if (!d.ok) return;

        const sr = document.getElementById('p15SuccessRate');
        const rr = document.getElementById('p15RetryRate');
        const al = document.getElementById('p15AvgLatency');
        const tt = document.getElementById('p15TotalTasks');
        if (sr) sr.textContent = d.success_rate + '%';
        if (rr) rr.textContent = d.retry_rate + '%';
        if (al) al.textContent = d.avg_latency + 's';
        if (tt) tt.textContent = d.total;

        // Color-code success rate
        if (sr) sr.style.color = d.success_rate >= 80 ? '#4ade80' : d.success_rate >= 50 ? '#fbbf24' : '#f87171';

        // Render sparkline
        const spark = document.getElementById('p15Sparkline');
        if (spark && d.points && d.points.length) {
          const maxH = 44;
          spark.innerHTML = d.points.map((p, i) => {
            const color = p.ok ? '#4ade80' : p.status === 'stopped' ? '#fbbf24' : '#f87171';
            const h = Math.max(6, maxH - (p.retries * 8));
            const title = `${p.task || 'Task'} — ${p.status}${p.retries ? ' (' + p.retries + ' retries)' : ''}`;
            return `<div title="${title.replace(/"/g, "'")}" style="flex:1;max-width:16px;height:${h}px;background:${color};border-radius:2px 2px 0 0;opacity:0.85;cursor:default;min-width:4px"></div>`;
          }).join('');
        }
      } catch (e) { }
    }

    async function _p15LoadStrategies() {
      const el = document.getElementById('p15Strategies');
      if (!el) return;
      try {
        const r = await fetch('/api/learning/insights');
        const d = await r.json();
        const strats = d.strategies || [];
        if (!strats.length) {
          el.innerHTML = '<div style="color:var(--muted);font-size:11px">No strategy data yet. Run tasks to populate.</div>';
          return;
        }
        el.innerHTML = strats.map(s => {
          const pct = s.win_rate || 0;
          const barW = Math.max(2, pct);
          const color = pct >= 70 ? '#4ade80' : pct >= 40 ? '#fbbf24' : '#f87171';
          return `<div style="display:flex;align-items:center;gap:8px;margin-bottom:8px">
                <div style="width:80px;font-size:11px;opacity:.8;flex-shrink:0">${s.strategy}</div>
                <div style="flex:1;height:8px;background:var(--surface);border-radius:4px;overflow:hidden;border:1px solid var(--border)">
                    <div style="width:${barW}%;height:100%;background:${color};border-radius:4px;transition:width .6s ease"></div>
                </div>
                <div style="font-size:11px;font-weight:600;color:${color};width:52px;text-align:right">${pct}%</div>
                <div style="font-size:10px;color:var(--muted);width:50px;text-align:right">${s.attempts} runs</div>
            </div>`;
        }).join('');
      } catch (e) { if (el) el.innerHTML = '<div style="color:#f87171;font-size:11px">Error loading strategies.</div>'; }
    }

    async function _p15LoadFailures() {
      const el = document.getElementById('p15Failures');
      if (!el) return;
      try {
        const r = await fetch('/api/dashboard/failure-analysis');
        const d = await r.json();
        const pats = d.patterns || [];
        if (!pats.length) {
          el.innerHTML = '<div style="color:#4ade80;font-size:11px">✅ No significant failure patterns detected.</div>';
          return;
        }
        const maxCount = Math.max(...pats.map(p => p.count));
        el.innerHTML = pats.map(p => {
          const barW = Math.max(4, Math.round(p.count / maxCount * 100));
          return `<div style="margin-bottom:10px">
                <div style="display:flex;align-items:center;gap:8px;margin-bottom:3px">
                    <span style="font-size:11px;font-weight:600;width:120px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${p.category}</span>
                    <div style="flex:1;height:6px;background:var(--surface);border-radius:3px;overflow:hidden;border:1px solid var(--border)">
                        <div style="width:${barW}%;height:100%;background:#f87171;border-radius:3px"></div>
                    </div>
                    <span style="font-size:11px;color:#f87171;font-weight:600">${p.count}×</span>
                </div>
                ${p.examples && p.examples[0] ? `<div style="font-size:9px;color:var(--muted);margin-left:4px;opacity:.7">e.g. ${p.examples[0]}</div>` : ''}
            </div>`;
        }).join('');
      } catch (e) { if (el) el.innerHTML = '<div style="color:#f87171;font-size:11px">Error loading failure analysis.</div>'; }
    }

    async function _p15LoadTimeline() {
      const el = document.getElementById('p15Timeline');
      if (!el) return;
      try {
        const r = await fetch('/api/dashboard/timeline');
        const d = await r.json();
        const events = d.events || [];
        if (!events.length) {
          el.innerHTML = '<div style="color:var(--muted);font-size:11px">No learning events yet. Complete tasks to build the timeline.</div>';
          return;
        }
        el.innerHTML = events.map(ev => {
          const dt = ev.ts ? new Date(ev.ts * 1000).toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' }) : '';
          const isReflect = ev.type === 'reflection';
          const icon = isReflect ? (ev.ok ? '🧠' : '⚠️') : (ev.ok ? '✅' : '❌');
          const borderColor = ev.ok ? '#4ade80' : '#f87171';
          let detail = '';
          if (isReflect && ev.insight) detail = `<div style="font-size:9px;color:var(--muted);margin-top:2px;line-height:1.4">${ev.insight.substring(0, 120)}</div>`;
          return `<div style="display:flex;gap:8px;margin-bottom:8px;padding-bottom:8px;border-bottom:1px solid var(--border)">
                <div style="font-size:14px;flex-shrink:0;margin-top:1px">${icon}</div>
                <div style="flex:1;min-width:0">
                    <div style="display:flex;align-items:center;gap:6px">
                        <span style="font-size:11px;font-weight:500;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${ev.task || 'Task'}</span>
                        <span style="font-size:9px;color:var(--muted);flex-shrink:0">${dt}</span>
                    </div>
                    <span style="font-size:9px;background:${borderColor}22;color:${borderColor};border-radius:8px;padding:1px 5px">${isReflect ? 'Learning' : ev.status || 'session'}</span>
                    ${detail}
                </div>
            </div>`;
        }).join('');
      } catch (e) { if (el) el.innerHTML = '<div style="color:#f87171;font-size:11px">Error loading timeline.</div>'; }
    }

    // Auto-refresh every 45s when the learning tab is active
    _p15AutoTimer = setInterval(() => {
      const tab = document.getElementById('nxTab-learning');
      if (tab && !tab.classList.contains('hidden') && tab.style.display !== 'none') {
        p15LoadDashboard();
      }
    }, 45000);

    console.debug('[Phase 15] Learning Dashboard active — metrics, timeline, failure analysis ready.');


    /* ═══════════════════════════════════════════════════════════════
       PHASE 16 — Autonomous Goal Mode
       ═══════════════════════════════════════════════════════════════ */

    let _p16CurrentChainId = null;
    let _p16PollTimer = null;

    window.p16InitGoals = function () {
      p16LoadChains();
    };

    window.p16SubmitGoal = async function () {
      const input = document.getElementById('p16GoalInput');
      const btn = document.getElementById('p16SubmitBtn');
      const priEl = document.getElementById('p16GoalPriority');
      const goal = (input ? input.value.trim() : '');
      if (!goal) {
        if (typeof toast === 'function') toast('Please enter a goal.', 'err');
        return;
      }
      if (btn) { btn.disabled = true; btn.textContent = '⏳ Decomposing…'; }
      const badge = document.getElementById('p16StatusBadge');
      if (badge) { badge.textContent = 'Decomposing…'; badge.style.color = '#fbbf24'; }

      try {
        const importance = priEl ? parseInt(priEl.value) : 7;
        const r = await fetch('/api/goals/decompose', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ goal, importance, auto_run: true })
        });
        const d = await r.json();
        if (!d.ok) throw new Error(d.error || 'Decomposition failed');

        _p16CurrentChainId = d.chain_id;
        p16RenderBreakdown(d);
        if (badge) { badge.textContent = 'Running'; badge.style.color = '#4ade80'; }
        if (typeof toast === 'function') toast(`Goal decomposed into ${d.task_count} tasks`, 'ok');

        // Start polling progress
        _p16StartPoll(d.chain_id);
        p16LoadChains();
      } catch (e) {
        if (typeof toast === 'function') toast(`Error: ${e.message}`, 'err');
        if (badge) { badge.textContent = 'Error'; badge.style.color = '#f87171'; }
      } finally {
        if (btn) { btn.disabled = false; btn.textContent = '🎯 Decompose & Execute'; }
      }
    };

    window.p16RenderBreakdown = function (d) {
      const card = document.getElementById('p16BreakdownCard');
      const list = document.getElementById('p16TaskList');
      const label = document.getElementById('p16GoalLabel');
      const tc = document.getElementById('p16TaskCount');
      const pcard = document.getElementById('p16ProgressCard');
      if (!card) return;

      card.style.display = 'block';
      if (pcard) pcard.style.display = 'block';
      if (label) label.textContent = `"${(d.goal || '').substring(0, 100)}"`;
      if (tc) tc.textContent = `${d.task_count} tasks`;

      const tasks = d.tasks || [];
      if (list) {
        list.innerHTML = tasks.map((t, i) => {
          const status = t.status || 'pending';
          const statusIcon = status === 'completed' ? '✅' : status === 'running' ? '⏳' : status === 'failed' ? '❌' : '⏸';
          const statusColor = status === 'completed' ? '#4ade80' : status === 'running' ? '#fbbf24' : status === 'failed' ? '#f87171' : 'var(--muted)';
          return `<div id="p16Task-${t.id || i}" style="display:flex;align-items:flex-start;gap:8px;padding:7px 0;border-bottom:1px solid var(--border)">
                <span style="font-size:13px;margin-top:1px">${statusIcon}</span>
                <div style="flex:1;min-width:0">
                    <div style="font-size:11px;font-weight:500;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${(t.goal || t.task || 'Task ' + (i + 1)).substring(0, 80)}</div>
                    <span style="font-size:9px;color:${statusColor}">${status}</span>
                    ${t.attempts > 0 ? `<span style="font-size:9px;color:#fbbf24;margin-left:6px">${t.attempts} attempt${t.attempts > 1 ? 's' : ''}</span>` : ''}
                </div>
            </div>`;
        }).join('') || '<div style="color:var(--muted);font-size:11px;padding:8px 0">No tasks generated.</div>';
      }
      p16UpdateProgress({ done: 0, pending: d.task_count, failed: 0, total: d.task_count, pct_complete: 0 });
    };

    window.p16UpdateProgress = function (d) {
      const bar = document.getElementById('p16ProgressBar');
      const pct = document.getElementById('p16ProgressPct');
      const done = document.getElementById('p16DoneCount');
      const pend = document.getElementById('p16PendingCount');
      const fail = document.getElementById('p16FailedCount');
      if (bar) bar.style.width = (d.pct_complete || 0) + '%';
      if (pct) pct.textContent = (d.pct_complete || 0) + '%';
      if (done) done.textContent = d.done || 0;
      if (pend) pend.textContent = d.pending || 0;
      if (fail) fail.textContent = d.failed || 0;
    };

    window.p16RunNext = async function () {
      if (!_p16CurrentChainId) return;
      const btn = document.getElementById('p16RunNextBtn');
      if (btn) { btn.disabled = true; btn.textContent = '⏳ Running…'; }
      try {
        const r = await fetch(`/api/chains/${_p16CurrentChainId}/run-next`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ config: { mode: 'managed', priority: 'smart' } })
        });
        const d = await r.json();
        if (d.ok === false) throw new Error(d.error);
        if (typeof toast === 'function') toast('Next task started', 'ok');
        setTimeout(() => p16PollChain(_p16CurrentChainId), 3000);
      } catch (e) {
        if (typeof toast === 'function') toast(`Error: ${e.message}`, 'err');
      } finally {
        if (btn) { btn.disabled = false; btn.textContent = '▶ Run Next Task'; }
      }
    };

    window.p16CancelGoal = function () {
      _p16StopPoll();
      _p16CurrentChainId = null;
      const card = document.getElementById('p16BreakdownCard');
      const pcard = document.getElementById('p16ProgressCard');
      if (card) card.style.display = 'none';
      if (pcard) pcard.style.display = 'none';
      const badge = document.getElementById('p16StatusBadge');
      if (badge) { badge.textContent = 'Idle'; badge.style.color = 'var(--muted)'; }
    };

    window.p16PollChain = async function (chainId) {
      if (!chainId) return;
      try {
        const r = await fetch(`/api/goals/chain/${chainId}`);
        const d = await r.json();
        if (!d.ok) return;
        p16UpdateProgress(d);

        // Re-render task list with updated statuses
        const list = document.getElementById('p16TaskList');
        if (list && d.tasks) {
          list.innerHTML = d.tasks.map((t, i) => {
            const status = t.status || 'pending';
            const statusIcon = status === 'completed' ? '✅' : status === 'running' ? '⏳' : status === 'failed' ? '❌' : '⏸';
            const statusColor = status === 'completed' ? '#4ade80' : status === 'running' ? '#fbbf24' : status === 'failed' ? '#f87171' : 'var(--muted)';
            return `<div style="display:flex;align-items:flex-start;gap:8px;padding:7px 0;border-bottom:1px solid var(--border)">
                    <span style="font-size:13px;margin-top:1px">${statusIcon}</span>
                    <div style="flex:1;min-width:0">
                        <div style="font-size:11px;font-weight:500;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${(t.goal || 'Task ' + (i + 1)).substring(0, 80)}</div>
                        <span style="font-size:9px;color:${statusColor}">${status}</span>
                        ${t.attempts > 0 ? `<span style="font-size:9px;color:#fbbf24;margin-left:6px">${t.attempts}× attempted</span>` : ''}
                        ${t.last_error ? `<div style="font-size:9px;color:#f87171;margin-top:2px">${t.last_error.substring(0, 80)}</div>` : ''}
                    </div>
                </div>`;
          }).join('');
        }

        // Stop polling if goal is complete
        const badge = document.getElementById('p16StatusBadge');
        if (d.status === 'completed') {
          _p16StopPoll();
          if (badge) { badge.textContent = '✅ Complete'; badge.style.color = '#4ade80'; }
          if (typeof toast === 'function') toast('Goal completed!', 'ok');
        } else if (d.status === 'failed') {
          _p16StopPoll();
          if (badge) { badge.textContent = '❌ Failed'; badge.style.color = '#f87171'; }
        } else {
          if (badge) { badge.textContent = `Running (${d.pct_complete}%)`; badge.style.color = '#fbbf24'; }
        }
      } catch (e) { }
    };

    window.p16LoadChains = async function () {
      const el = document.getElementById('p16ChainList');
      if (!el) return;
      try {
        const r = await fetch('/api/chains');
        const d = await r.json();
        const chains = Array.isArray(d) ? d : (d.chains || []);
        if (!chains.length) {
          el.innerHTML = '<div style="color:var(--muted);font-size:11px">No active goal chains yet.</div>';
          return;
        }
        el.innerHTML = chains.slice(0, 10).map(ch => {
          const chain = ch.chain || ch;
          const cid = chain.id || ch.id;
          const goal = (chain.goal || 'Unknown goal').substring(0, 60);
          const status = chain.status || 'unknown';
          const statusColor = status === 'completed' ? '#4ade80' : status === 'failed' ? '#f87171' : '#fbbf24';
          const taskCount = (ch.tasks || []).length;
          return `<div style="display:flex;align-items:center;gap:8px;padding:7px 0;border-bottom:1px solid var(--border);cursor:pointer" onclick="p16LoadChainDetail(${cid})">
                <div style="flex:1;min-width:0">
                    <div style="font-size:11px;font-weight:500;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${goal}</div>
                    <div style="font-size:9px;margin-top:2px">
                        <span style="color:${statusColor}">${status}</span>
                        ${taskCount ? `<span style="color:var(--muted);margin-left:6px">${taskCount} tasks</span>` : ''}
                        <span style="color:var(--muted);margin-left:6px">Chain #${cid}</span>
                    </div>
                </div>
                <button class="nx-tiny-btn" onclick="event.stopPropagation();p16LoadChainDetail(${cid})" title="View detail">→</button>
            </div>`;
        }).join('');
      } catch (e) {
        el.innerHTML = '<div style="color:#f87171;font-size:11px">Chain runner unavailable.</div>';
      }
    };

    window.p16LoadChainDetail = async function (cid) {
      _p16CurrentChainId = cid;
      const r = await fetch(`/api/goals/chain/${cid}`);
      const d = await r.json();
      if (!d.ok) return;
      p16RenderBreakdown({ goal: d.goal, task_count: d.total, tasks: d.tasks });
      p16UpdateProgress(d);
      const card = document.getElementById('p16BreakdownCard');
      const pcard = document.getElementById('p16ProgressCard');
      if (card) card.style.display = 'block';
      if (pcard) pcard.style.display = 'block';
      _p16StartPoll(cid);
    };

    function _p16StartPoll(chainId) {
      _p16StopPoll();
      _p16PollTimer = setInterval(() => p16PollChain(chainId), 5000);
    }
    function _p16StopPoll() {
      if (_p16PollTimer) { clearInterval(_p16PollTimer); _p16PollTimer = null; }
    }

    console.debug('[Phase 16] Autonomous Goal-Driven AI active — goal decomposition + execution ready.');


    /* ═══════════════════════════════════════════════════════════════
       PHASE 17 — TASK GRAPH VISUALIZATION + EXECUTION ENGINE
       ═══════════════════════════════════════════════════════════════ */

    (function () {

      const NODE_R = 22;
      const COL_X = 180;
      const ROW_Y = 80;
      const PAD = 60;

      const STATUS_COLORS = {
        pending: '#6b7280',
        running: '#3b82f6',
        completed: '#22c55e',
        failed: '#ef4444',
        skipped: '#a855f7',
      };

      let _p17ChainId = null;
      let _p17Graph = null;   // { nodes, edges, nodeMap, positions }
      let _p17PollTimer = null;
      let _p17Selected = null;   // selected node id

      // Canvas transform state
      let _cx = 0, _cy = 0, _cz = 1;
      let _dragging = false, _dragStartX = 0, _dragStartY = 0, _dragCx = 0, _dragCy = 0;

      // ── Boot ────────────────────────────────────────────────────────
      window.p17InitGraph = function () {
        _p17SetupCanvas();
        p17LoadChainOptions();
        const sel = document.getElementById('p17ChainSelect');
        if (sel) sel.addEventListener('change', () => {
          const v = parseInt(sel.value);
          if (v) p17OpenChain(v);
        });
      };

      // ── Chain picker ────────────────────────────────────────────────
      window.p17LoadChainOptions = async function () {
        const sel = document.getElementById('p17ChainSelect');
        if (!sel) return;
        try {
          const r = await fetch('/api/chains');
          const d = await r.json();
          const chains = Array.isArray(d) ? d : (d.chains || []);
          const prev = sel.value;
          sel.innerHTML = '<option value="">— Select a goal chain —</option>';
          chains.slice(0, 30).forEach(ch => {
            const chain = ch.chain || ch;
            const cid = chain.id || ch.id;
            const goal = (chain.goal || 'Chain ' + cid).substring(0, 50);
            const st = chain.status || '?';
            const opt = document.createElement('option');
            opt.value = cid;
            opt.textContent = `#${cid} [${st}] ${goal}`;
            if (String(cid) === String(prev)) opt.selected = true;
            sel.appendChild(opt);
          });
          if (_p17ChainId && !sel.value) {
            sel.value = String(_p17ChainId);
          }
        } catch (e) { }
      };

      // ── Open chain ──────────────────────────────────────────────────
      window.p17OpenChain = async function (cid) {
        _p17ChainId = cid;
        _p17StopPoll();
        const emp = document.getElementById('p17Empty');
        if (emp) emp.style.display = 'none';
        await p17RefreshGraph();
        _p17PollTimer = setInterval(p17RefreshGraph, 4000);
      };

      // ── Refresh graph data ──────────────────────────────────────────
      window.p17RefreshGraph = async function () {
        if (!_p17ChainId) return;
        const btn = document.getElementById('p17RefreshBtn');
        if (btn) btn.textContent = '⟳';
        try {
          const r = await fetch(`/api/goal/graph/${_p17ChainId}`);
          const d = await r.json();
          if (!d.ok) return;
          _p17BuildGraph(d);
          _p17DrawGraph();

          const badge = document.getElementById('p17StatusBadge');
          const prog = d.progress || {};
          const pct = prog.pct_complete || 0;
          const statusColors = { completed: '#22c55e', failed: '#ef4444', running: '#3b82f6' };
          if (badge) {
            badge.textContent = `${d.status} ${pct}%`;
            badge.style.color = statusColors[d.status] || '#fbbf24';
          }
          const nc = document.getElementById('p17NodeCount');
          if (nc) nc.textContent = `${d.nodes.length} nodes · ${d.edges.length} edges`;

          if (d.status === 'completed' || d.status === 'failed') _p17StopPoll();
        } catch (e) { }
      };

      // ── Build graph layout (DAG layering) ───────────────────────────
      function _p17BuildGraph(data) {
        const nodes = data.nodes || [];
        const edges = data.edges || [];
        const nodeMap = {};
        nodes.forEach(n => { nodeMap[n.id] = n; });

        // Build adjacency + in-degree
        const inDeg = {};
        const adjOut = {};
        nodes.forEach(n => { inDeg[n.id] = 0; adjOut[n.id] = []; });
        edges.forEach(e => {
          if (nodeMap[e.from] && nodeMap[e.to]) {
            adjOut[e.from].push(e.to);
            inDeg[e.to]++;
          }
        });

        // Kahn topological sort → assign level
        const level = {};
        const queue = nodes.filter(n => inDeg[n.id] === 0).map(n => n.id);
        const visited = new Set(queue);
        queue.forEach(id => { level[id] = 0; });
        let qi = 0;
        while (qi < queue.length) {
          const cur = queue[qi++];
          (adjOut[cur] || []).forEach(nxt => {
            level[nxt] = Math.max(level[nxt] || 0, (level[cur] || 0) + 1);
            if (!visited.has(nxt)) {
              visited.add(nxt);
              queue.push(nxt);
            }
          });
        }
        // Fallback level for unvisited (cycles / disconnected)
        nodes.forEach(n => { if (level[n.id] == null) level[n.id] = 0; });

        // Group by level, sort within level by id
        const byLevel = {};
        nodes.forEach(n => {
          const lv = level[n.id];
          if (!byLevel[lv]) byLevel[lv] = [];
          byLevel[lv].push(n.id);
        });

        // Assign (x, y) positions
        const positions = {};
        Object.keys(byLevel).sort((a, b) => a - b).forEach(lv => {
          const ids = byLevel[lv];
          ids.forEach((id, row) => {
            positions[id] = {
              x: PAD + parseInt(lv) * COL_X,
              y: PAD + row * ROW_Y,
            };
          });
        });

        _p17Graph = { nodes, edges, nodeMap, positions, data };
      }

      // ── Canvas setup ─────────────────────────────────────────────────
      function _p17SetupCanvas() {
        const canvas = document.getElementById('p17Canvas');
        if (!canvas || canvas._p17Ready) return;
        canvas._p17Ready = true;

        function _resize() {
          const wrap = document.getElementById('p17CanvasWrap');
          if (!wrap) return;
          canvas.width = wrap.clientWidth;
          canvas.height = wrap.clientHeight;
          _p17DrawGraph();
        }
        window.addEventListener('resize', _resize);
        _resize();

        // Zoom
        canvas.addEventListener('wheel', e => {
          e.preventDefault();
          const rect = canvas.getBoundingClientRect();
          const mx = e.clientX - rect.left;
          const my = e.clientY - rect.top;
          const delta = e.deltaY < 0 ? 1.1 : 0.91;
          _cx = mx - (mx - _cx) * delta;
          _cy = my - (my - _cy) * delta;
          _cz = Math.min(Math.max(_cz * delta, 0.15), 4);
          _p17DrawGraph();
        }, { passive: false });

        // Pan
        canvas.addEventListener('mousedown', e => {
          _dragging = true;
          _dragStartX = e.clientX; _dragStartY = e.clientY;
          _dragCx = _cx; _dragCy = _cy;
          canvas.style.cursor = 'grabbing';
        });
        window.addEventListener('mouseup', () => {
          _dragging = false;
          if (canvas) canvas.style.cursor = 'grab';
        });
        window.addEventListener('mousemove', e => {
          if (_dragging) {
            _cx = _dragCx + (e.clientX - _dragStartX);
            _cy = _dragCy + (e.clientY - _dragStartY);
            _p17DrawGraph();
          } else {
            _p17HandleHover(e, canvas);
          }
        });

        // Click
        canvas.addEventListener('click', e => {
          if (Math.abs(e.clientX - _dragStartX) > 5) return;
          const node = _p17NodeAtEvent(e, canvas);
          if (node) p17ShowDetail(node);
          else p17CloseDetail();
        });

        // Touch pan/zoom
        let _touches = [];
        canvas.addEventListener('touchstart', e => { _touches = [...e.touches]; }, { passive: true });
        canvas.addEventListener('touchmove', e => {
          e.preventDefault();
          if (e.touches.length === 1 && _touches.length === 1) {
            const dx = e.touches[0].clientX - _touches[0].clientX;
            const dy = e.touches[0].clientY - _touches[0].clientY;
            _cx += dx; _cy += dy;
          } else if (e.touches.length === 2 && _touches.length === 2) {
            const d0 = Math.hypot(_touches[0].clientX - _touches[1].clientX, _touches[0].clientY - _touches[1].clientY);
            const d1 = Math.hypot(e.touches[0].clientX - e.touches[1].clientX, e.touches[0].clientY - e.touches[1].clientY);
            const scale = d1 / d0;
            _cz = Math.min(Math.max(_cz * scale, 0.15), 4);
          }
          _touches = [...e.touches];
          _p17DrawGraph();
        }, { passive: false });
      }

      // ── Draw ─────────────────────────────────────────────────────────
      function _p17DrawGraph() {
        const canvas = document.getElementById('p17Canvas');
        if (!canvas) return;
        const ctx = canvas.getContext('2d');
        const W = canvas.width, H = canvas.height;
        ctx.clearRect(0, 0, W, H);

        if (!_p17Graph) return;
        const { nodes, edges, nodeMap, positions } = _p17Graph;

        ctx.save();
        ctx.translate(_cx, _cy);
        ctx.scale(_cz, _cz);

        // Draw edges
        edges.forEach(e => {
          const fp = positions[e.from];
          const tp = positions[e.to];
          if (!fp || !tp) return;
          const fromNode = nodeMap[e.from];
          const toNode = nodeMap[e.to];

          // Color edge based on statuses
          const edgeColor = fromNode && fromNode.status === 'completed' ? '#22c55e44' :
            fromNode && fromNode.status === 'running' ? '#3b82f644' : '#ffffff18';
          ctx.strokeStyle = edgeColor;
          ctx.lineWidth = 1.5 / _cz;
          ctx.setLineDash([]);
          ctx.beginPath();

          // Bezier from right of from-node to left of to-node
          const x1 = fp.x + NODE_R, y1 = fp.y;
          const x2 = tp.x - NODE_R, y2 = tp.y;
          const cx1 = x1 + (x2 - x1) * 0.5, cy1 = y1;
          const cx2 = x1 + (x2 - x1) * 0.5, cy2 = y2;
          ctx.moveTo(x1, y1);
          ctx.bezierCurveTo(cx1, cy1, cx2, cy2, x2, y2);
          ctx.stroke();

          // Arrow head
          const angle = Math.atan2(y2 - cy2, x2 - cx2);
          const aLen = 8 / _cz;
          ctx.beginPath();
          ctx.moveTo(x2, y2);
          ctx.lineTo(x2 - aLen * Math.cos(angle - 0.4), y2 - aLen * Math.sin(angle - 0.4));
          ctx.lineTo(x2 - aLen * Math.cos(angle + 0.4), y2 - aLen * Math.sin(angle + 0.4));
          ctx.closePath();
          ctx.fillStyle = edgeColor;
          ctx.fill();
        });

        // Draw nodes
        nodes.forEach(n => {
          const p = positions[n.id];
          if (!p) return;
          const isSelected = _p17Selected === n.id;
          const color = n.is_bottleneck && n.status !== 'completed' ? '#f97316' :
            (STATUS_COLORS[n.status] || '#6b7280');

          // Pulse ring for running nodes
          if (n.status === 'running') {
            const t = (Date.now() % 1500) / 1500;
            const pulseR = NODE_R + 8 * t;
            ctx.beginPath();
            ctx.arc(p.x, p.y, pulseR, 0, Math.PI * 2);
            ctx.strokeStyle = `rgba(59,130,246,${0.5 * (1 - t)})`;
            ctx.lineWidth = 2 / _cz;
            ctx.stroke();
          }

          // Node shadow
          ctx.save();
          ctx.shadowColor = color;
          ctx.shadowBlur = isSelected ? 18 : 8;

          // Fill
          ctx.beginPath();
          ctx.arc(p.x, p.y, NODE_R, 0, Math.PI * 2);
          const grad = ctx.createRadialGradient(p.x - NODE_R * 0.3, p.y - NODE_R * 0.3, 2, p.x, p.y, NODE_R);
          grad.addColorStop(0, _lighten(color, 0.3));
          grad.addColorStop(1, color);
          ctx.fillStyle = grad;
          ctx.fill();
          ctx.restore();

          // Border
          ctx.beginPath();
          ctx.arc(p.x, p.y, NODE_R, 0, Math.PI * 2);
          ctx.strokeStyle = isSelected ? '#fff' : _lighten(color, 0.5);
          ctx.lineWidth = isSelected ? 2.5 / _cz : 1.2 / _cz;
          ctx.stroke();

          // Status icon inside node
          const icon = { pending: '⏸', running: '▶', completed: '✓', failed: '✕', skipped: '↷' }[n.status] || '?';
          ctx.fillStyle = '#fff';
          ctx.font = `bold ${14 / _cz}px sans-serif`;
          ctx.textAlign = 'center';
          ctx.textBaseline = 'middle';
          ctx.fillText(icon, p.x, p.y);

          // Task number badge
          ctx.fillStyle = '#ffffffbb';
          ctx.font = `${9 / _cz}px sans-serif`;
          ctx.fillText(`#${n.id}`, p.x, p.y + NODE_R + 10 / _cz);

          // Task label below
          const label = n.name.length > 26 ? n.name.substring(0, 24) + '…' : n.name;
          ctx.fillStyle = '#ffffffcc';
          ctx.font = `${9.5 / _cz}px sans-serif`;
          ctx.fillText(label, p.x, p.y + NODE_R + 22 / _cz);

          // Retries badge
          if (n.retries > 0) {
            const bx = p.x + NODE_R - 6 / _cz;
            const by = p.y - NODE_R + 6 / _cz;
            const br = 8 / _cz;
            ctx.beginPath();
            ctx.arc(bx, by, br, 0, Math.PI * 2);
            ctx.fillStyle = '#fbbf24';
            ctx.fill();
            ctx.fillStyle = '#000';
            ctx.font = `bold ${8 / _cz}px sans-serif`;
            ctx.fillText(n.retries, bx, by);
          }
        });

        ctx.restore();

        // Schedule next frame if any node is running (for pulse animation)
        const hasRunning = _p17Graph && _p17Graph.nodes.some(n => n.status === 'running');
        if (hasRunning) requestAnimationFrame(_p17DrawGraph);
      }

      function _lighten(hex, amt) {
        const c = parseInt(hex.slice(1), 16);
        const r = Math.min(255, ((c >> 16) & 0xff) + Math.round(amt * 80));
        const g = Math.min(255, ((c >> 8) & 0xff) + Math.round(amt * 80));
        const b = Math.min(255, (c & 0xff) + Math.round(amt * 80));
        return `rgb(${r},${g},${b})`;
      }

      // ── Hit test ─────────────────────────────────────────────────────
      function _p17NodeAtEvent(e, canvas) {
        if (!_p17Graph) return null;
        const rect = canvas.getBoundingClientRect();
        const mx = (e.clientX - rect.left - _cx) / _cz;
        const my = (e.clientY - rect.top - _cy) / _cz;
        const { nodes, positions } = _p17Graph;
        for (const n of nodes) {
          const p = positions[n.id];
          if (!p) continue;
          const dx = mx - p.x, dy = my - p.y;
          if (dx * dx + dy * dy <= NODE_R * NODE_R) return n;
        }
        return null;
      }

      // ── Hover tooltip ────────────────────────────────────────────────
      function _p17HandleHover(e, canvas) {
        const tip = document.getElementById('p17Tooltip');
        if (!tip || !_p17Graph) return;
        const node = _p17NodeAtEvent(e, canvas);
        if (node) {
          const rect = canvas.getBoundingClientRect();
          tip.style.display = 'block';
          tip.style.left = (e.clientX - rect.left + 14) + 'px';
          tip.style.top = (e.clientY - rect.top - 10) + 'px';
          tip.innerHTML = `
            <div style="font-weight:600;margin-bottom:4px;color:#e2e8f0">${node.name}</div>
            <div style="color:${STATUS_COLORS[node.status] || '#aaa'};font-size:10px;margin-bottom:3px">● ${node.status.toUpperCase()}</div>
            ${node.retries > 0 ? `<div style="color:#fbbf24;font-size:10px">⚠ ${node.retries} attempt${node.retries > 1 ? 's' : ''}</div>` : ''}
            ${node.is_bottleneck ? '<div style="color:#f97316;font-size:10px">🔴 Bottleneck detected</div>' : ''}
            <div style="color:#64748b;font-size:9px;margin-top:3px">Priority: ${node.priority} · Importance: ${node.importance}</div>
            <div style="color:#64748b;font-size:9px">Click to inspect</div>`;
          canvas.style.cursor = 'pointer';
        } else {
          tip.style.display = 'none';
          if (!_dragging) canvas.style.cursor = 'grab';
        }
      }

      // ── Node detail panel ────────────────────────────────────────────
      window.p17ShowDetail = function (node) {
        _p17Selected = node.id;
        _p17DrawGraph();

        const panel = document.getElementById('p17Detail');
        const title = document.getElementById('p17DetailTitle');
        const body = document.getElementById('p17DetailBody');
        const acts = document.getElementById('p17DetailActions');
        if (!panel) return;

        panel.style.width = '260px';
        if (title) title.textContent = `Task #${node.id}`;

        const statusColor = STATUS_COLORS[node.status] || '#aaa';
        if (body) body.innerHTML = `
        <div style="margin-bottom:10px">
            <div style="font-size:10px;color:var(--muted);margin-bottom:3px">TASK GOAL</div>
            <div style="font-size:11px;line-height:1.5;color:var(--text)">${node.name}</div>
        </div>
        <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-bottom:10px">
            <div style="background:var(--surface-2,#0f172a);border-radius:5px;padding:7px;text-align:center">
                <div style="font-size:1.1rem;font-weight:700;color:${statusColor}">${node.status}</div>
                <div style="font-size:9px;color:var(--muted)">Status</div>
            </div>
            <div style="background:var(--surface-2,#0f172a);border-radius:5px;padding:7px;text-align:center">
                <div style="font-size:1.1rem;font-weight:700;color:#fbbf24">${node.retries}</div>
                <div style="font-size:9px;color:var(--muted)">Attempts</div>
            </div>
            <div style="background:var(--surface-2,#0f172a);border-radius:5px;padding:7px;text-align:center">
                <div style="font-size:1.1rem;font-weight:700;color:#818cf8">${node.priority}</div>
                <div style="font-size:9px;color:var(--muted)">Priority</div>
            </div>
            <div style="background:var(--surface-2,#0f172a);border-radius:5px;padding:7px;text-align:center">
                <div style="font-size:1.1rem;font-weight:700;color:#38bdf8">${node.importance}</div>
                <div style="font-size:9px;color:var(--muted)">Importance</div>
            </div>
        </div>
        ${node.is_bottleneck ? '<div style="background:#f9731620;border:1px solid #f9731640;border-radius:5px;padding:6px;font-size:10px;color:#f97316;margin-bottom:8px">🔴 Bottleneck — multiple retries with unresolved state</div>' : ''}
        ${node.last_error ? `<div style="background:#ef444420;border:1px solid #ef444440;border-radius:5px;padding:6px;font-size:10px;color:#f87171;margin-bottom:8px;word-break:break-word"><b>Last Error:</b><br>${node.last_error}</div>` : ''}`;

        if (acts) {
          const canRetry = ['failed', 'pending'].includes(node.status);
          const canSkip = ['pending', 'running'].includes(node.status);
          acts.innerHTML = `
            ${canRetry ? `<button class="btn primary" style="font-size:11px" onclick="p17RetryTask(${node.id})">🔄 Retry Task</button>` : ''}
            ${canSkip ? `<button class="btn" style="font-size:11px;color:#f87171" onclick="p17SkipTask(${node.id})">⏭ Skip Task</button>` : ''}
            <button class="btn tiny" style="font-size:10px;color:var(--muted)" onclick="p17CloseDetail()">Close</button>`;
        }
      };

      window.p17CloseDetail = function () {
        _p17Selected = null;
        _p17DrawGraph();
        const panel = document.getElementById('p17Detail');
        if (panel) panel.style.width = '0';
      };

      // ── Actions ──────────────────────────────────────────────────────
      window.p17RetryTask = async function (tid) {
        if (!_p17ChainId) return;
        try {
          const r = await fetch(`/api/goal/graph/${_p17ChainId}/task/${tid}/retry`, { method: 'POST' });
          const d = await r.json();
          if (d.ok) {
            if (typeof toast === 'function') toast('Task reset to pending', 'ok');
            await p17RefreshGraph();
            const n = _p17Graph && _p17Graph.nodeMap && _p17Graph.nodeMap[tid];
            if (n) p17ShowDetail(n);
          } else {
            if (typeof toast === 'function') toast(d.error || 'Retry failed', 'err');
          }
        } catch (e) {
          if (typeof toast === 'function') toast('Network error', 'err');
        }
      };

      window.p17SkipTask = async function (tid) {
        if (!_p17ChainId) return;
        try {
          const r = await fetch(`/api/goal/graph/${_p17ChainId}/task/${tid}/skip`, { method: 'POST' });
          const d = await r.json();
          if (d.ok) {
            if (typeof toast === 'function') toast('Task skipped', 'ok');
            await p17RefreshGraph();
            const n = _p17Graph && _p17Graph.nodeMap && _p17Graph.nodeMap[tid];
            if (n) p17ShowDetail(n);
          } else {
            if (typeof toast === 'function') toast(d.error || 'Skip failed', 'err');
          }
        } catch (e) {
          if (typeof toast === 'function') toast('Network error', 'err');
        }
      };

      // ── Fit to screen ────────────────────────────────────────────────
      window.p17FitGraph = function () {
        if (!_p17Graph || !_p17Graph.nodes.length) return;
        const canvas = document.getElementById('p17Canvas');
        if (!canvas) return;
        const { positions, nodes } = _p17Graph;
        let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
        nodes.forEach(n => {
          const p = positions[n.id];
          if (!p) return;
          minX = Math.min(minX, p.x - NODE_R - 40);
          minY = Math.min(minY, p.y - NODE_R - 40);
          maxX = Math.max(maxX, p.x + NODE_R + 40);
          maxY = Math.max(maxY, p.y + NODE_R + 40);
        });
        const W = canvas.width, H = canvas.height;
        const gW = maxX - minX, gH = maxY - minY;
        const scale = Math.min(W / gW, H / gH, 2);
        _cz = scale;
        _cx = (W - gW * scale) / 2 - minX * scale;
        _cy = (H - gH * scale) / 2 - minY * scale;
        _p17DrawGraph();
      };

      // ── Poll helpers ─────────────────────────────────────────────────
      function _p17StopPoll() {
        if (_p17PollTimer) { clearInterval(_p17PollTimer); _p17PollTimer = null; }
      }

      // ── Link from Goal Mode chains list ─────────────────────────────
      // Extend p16LoadChains to add a "Graph" button to each chain entry.
      const _orig_p16LoadChainDetail = window.p16LoadChainDetail;
      window.p16LoadChainDetail = async function (cid) {
        if (_orig_p16LoadChainDetail) await _orig_p16LoadChainDetail(cid);
        // Also switch tab to graph and load it
        const graphBtn = document.createElement('button');
        graphBtn.className = 'btn tiny';
        graphBtn.textContent = '🧩 View Graph';
        graphBtn.style.cssText = 'margin-top:8px;width:100%';
        graphBtn.onclick = () => {
          nxSetTab('graph');
          p17OpenChain(cid);
          p17LoadChainOptions().then(() => {
            const sel = document.getElementById('p17ChainSelect');
            if (sel) sel.value = String(cid);
          });
        };
        const card = document.getElementById('p16BreakdownCard');
        if (card && !card.querySelector('._p17GraphBtn')) {
          graphBtn.classList.add('_p17GraphBtn');
          card.appendChild(graphBtn);
        }
      };

      console.debug('[Phase 17] Task Graph Visualization + Execution Engine active.');

    })();


    /* ═══════════════════════════════════════════════════════════════
       PHASE 18 — BACKGROUND AUTONOMOUS AGENTS + TASK SCHEDULER
       ═══════════════════════════════════════════════════════════════ */

    (function () {

      let _p18PollTimer = null;

      // ── Schedule hint text ────────────────────────────────────────────
      const SCHED_HINTS = {
        once: 'One-time task — runs once at the specified time (or immediately if blank). Format: 2025-01-15T09:00',
        interval: 'Repeating task — enter interval in minutes (e.g. 60 = every hour, 1440 = every day).',
        daily: 'Daily task — enter time in HH:MM format (UTC), e.g. 09:00 for 9am UTC each day.',
      };
      const SCHED_LABELS = {
        once: 'Run at (ISO datetime or blank for now)',
        interval: 'Interval in minutes (e.g. 60)',
        daily: 'Time of day HH:MM UTC (e.g. 09:00)',
      };

      window.p18UpdateScheduleHint = function () {
        const t = (document.getElementById('p18FSched') || {}).value || 'once';
        const hint = document.getElementById('p18FHint');
        const label = document.getElementById('p18FValLabel');
        const inp = document.getElementById('p18FVal');
        if (hint) hint.textContent = SCHED_HINTS[t] || '';
        if (label) label.textContent = SCHED_LABELS[t] || 'Value';
        if (inp) {
          const ph = { once: 'e.g. 2025-06-01T09:00 or blank', interval: 'e.g. 60', daily: 'e.g. 09:00' };
          inp.placeholder = ph[t] || '';
        }
      };

      // ── Init ─────────────────────────────────────────────────────────
      window.p18InitScheduler = function () {
        p18LoadAll();
        if (!_p18PollTimer) {
          _p18PollTimer = setInterval(p18LoadAll, 10000);
        }
      };

      window.p18LoadAll = async function () {
        await Promise.all([p18LoadStatus(), p18LoadTasks(), p18LoadHistory()]);
      };

      // ── Status ────────────────────────────────────────────────────────
      window.p18LoadStatus = async function () {
        try {
          const r = await fetch('/api/scheduler/status');
          const d = await r.json();
          if (!d.ok) return;
          const set = (id, v) => { const el = document.getElementById(id); if (el) el.textContent = v; };
          set('p18StatTotal', d.total_tasks);
          set('p18StatEnabled', d.enabled_tasks);
          set('p18StatRunning', d.running_count);
          set('p18StatNext', d.next_run_human || '—');
          const badge = document.getElementById('p18EngineBadge');
          if (badge) {
            badge.textContent = d.running_count > 0 ? `● ${d.running_count} running` : '● Active';
            badge.style.color = d.running_count > 0 ? '#fbbf24' : '#4ade80';
          }
        } catch (e) { }
      };

      // ── Task list ─────────────────────────────────────────────────────
      window.p18LoadTasks = async function () {
        const el = document.getElementById('p18TaskList');
        if (!el) return;
        try {
          const r = await fetch('/api/scheduler/tasks');
          const d = await r.json();
          if (!d.ok) { el.innerHTML = '<div style="color:#f87171;font-size:11px;padding:8px 0">Failed to load tasks.</div>'; return; }
          const tasks = d.tasks || [];
          const badge = document.getElementById('p18TaskCountBadge');
          if (badge) badge.textContent = tasks.length;
          if (!tasks.length) {
            el.innerHTML = '<div style="color:var(--muted);font-size:11px;padding:12px 0;text-align:center">No scheduled tasks yet. Click <b>+ New Task</b> to create one.</div>';
            return;
          }
          el.innerHTML = tasks.map(t => _p18TaskRow(t)).join('');
        } catch (e) {
          el.innerHTML = '<div style="color:#f87171;font-size:11px;padding:8px 0">Scheduler unavailable.</div>';
        }
      };

      function _p18TaskRow(t) {
        const statusColors = { idle: '#6b7280', running: '#3b82f6', completed: '#22c55e', failed: '#ef4444', paused: '#a855f7' };
        const typeIcons = { prompt: '🤖', goal: '🎯', analysis: '🔍' };
        const schedIcons = { once: '🔂', interval: '🔁', daily: '📅' };
        const sc = statusColors[t.status] || '#6b7280';
        const dis = !t.enabled;
        const schedLabel = t.schedule_type === 'interval' ? `every ${t.schedule_value} min` :
          t.schedule_type === 'daily' ? `daily ${t.schedule_value} UTC` : 'once';

        return `<div style="display:flex;align-items:flex-start;gap:8px;padding:10px 0;border-bottom:1px solid var(--border);opacity:${dis ? '0.55' : '1'}">
        <div style="flex:1;min-width:0">
            <div style="display:flex;align-items:center;gap:6px;flex-wrap:wrap">
                <span style="font-size:12px">${typeIcons[t.task_type] || '🤖'}</span>
                <span style="font-size:11px;font-weight:600;color:var(--text)">${_esc(t.name)}</span>
                <span style="font-size:9px;padding:1px 6px;border-radius:8px;background:${sc}22;color:${sc}">${t.status}</span>
                ${dis ? '<span style="font-size:9px;color:var(--muted);background:var(--surface);border:1px solid var(--border);border-radius:6px;padding:1px 5px">disabled</span>' : ''}
            </div>
            <div style="font-size:10px;color:var(--muted);margin-top:3px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${_esc((t.prompt || '').substring(0, 70))}</div>
            <div style="display:flex;gap:10px;margin-top:4px;font-size:9px;flex-wrap:wrap">
                <span style="color:#fb923c">${schedIcons[t.schedule_type] || '🔂'} ${schedLabel}</span>
                <span style="color:var(--muted)">Next: ${t.next_run_human || '—'}</span>
                <span style="color:var(--muted)">Runs: ${t.run_count} · Fails: ${t.fail_count}</span>
                ${t.last_error ? `<span style="color:#f87171">⚠ ${_esc(t.last_error.substring(0, 40))}</span>` : ''}
            </div>
        </div>
        <div style="display:flex;flex-direction:column;gap:4px;align-items:flex-end;flex-shrink:0">
            <div style="display:flex;gap:4px">
                <button class="btn tiny" title="${t.enabled ? 'Disable' : 'Enable'}" onclick="p18ToggleTask('${t.id}',this)">${t.enabled ? '⏸' : '▶'}</button>
                <button class="btn tiny" title="Run now" onclick="p18RunNow('${t.id}',this)">▶▶</button>
                <button class="btn tiny" style="color:#f87171" title="Delete" onclick="p18DeleteTask('${t.id}',this)">🗑</button>
            </div>
            <div style="font-size:9px;color:var(--muted)">Last: ${t.last_run_human || '—'}</div>
        </div>
    </div>`;
      }

      // ── History ───────────────────────────────────────────────────────
      window.p18LoadHistory = async function () {
        const el = document.getElementById('p18HistoryList');
        if (!el) return;
        try {
          const r = await fetch('/api/scheduler/history?limit=30');
          const d = await r.json();
          if (!d.ok) return;
          const hist = d.history || [];
          if (!hist.length) {
            el.innerHTML = '<div style="color:var(--muted);font-size:11px;padding:8px 0">No runs yet.</div>';
            return;
          }
          el.innerHTML = hist.map(h => {
            const sc = h.status === 'success' ? '#22c55e' : '#ef4444';
            const icon = h.status === 'success' ? '✅' : '❌';
            const dt = h.run_at ? new Date(h.run_at * 1000).toLocaleString() : '—';
            const dur = h.duration ? `${h.duration.toFixed(1)}s` : '';
            return `<div style="display:flex;align-items:flex-start;gap:8px;padding:6px 0;border-bottom:1px solid var(--border)">
                <span style="font-size:11px;margin-top:1px">${icon}</span>
                <div style="flex:1;min-width:0">
                    <div style="font-size:10px;font-weight:500;color:var(--text)">${_esc((h.task_name || h.task_id || '').substring(0, 50))}</div>
                    <div style="font-size:9px;color:var(--muted)">${dt}${dur ? ' · ' + dur : ''}</div>
                    ${h.error ? `<div style="font-size:9px;color:#f87171;margin-top:1px">${_esc(h.error.substring(0, 60))}</div>` : ''}
                </div>
                <span style="font-size:9px;color:${sc};flex-shrink:0">${h.status}</span>
            </div>`;
          }).join('');
        } catch (e) { }
      };

      // ── Create form ───────────────────────────────────────────────────
      window.p18ShowCreateForm = function () {
        const c = document.getElementById('p18CreateCard');
        if (c) { c.style.display = 'block'; c.scrollIntoView({ behavior: 'smooth', block: 'nearest' }); }
      };
      window.p18HideCreateForm = function () {
        const c = document.getElementById('p18CreateCard');
        if (c) c.style.display = 'none';
        ['p18FName', 'p18FPrompt', 'p18FVal'].forEach(id => { const el = document.getElementById(id); if (el) el.value = ''; });
      };

      window.p18CreateTask = async function () {
        const btn = document.getElementById('p18CreateBtn');
        const name = (document.getElementById('p18FName') || {}).value?.trim() || 'Unnamed Task';
        const type = (document.getElementById('p18FType') || {}).value || 'prompt';
        const prompt = (document.getElementById('p18FPrompt') || {}).value?.trim() || '';
        const sched = (document.getElementById('p18FSched') || {}).value || 'once';
        const val = (document.getElementById('p18FVal') || {}).value?.trim() || '';

        if (!prompt) { if (typeof toast === 'function') toast('Prompt is required', 'err'); return; }
        if (btn) { btn.disabled = true; btn.textContent = '⏳ Creating…'; }
        try {
          const r = await fetch('/api/scheduler/tasks', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ name, task_type: type, prompt, schedule_type: sched, schedule_value: val }),
          });
          const d = await r.json();
          if (!d.ok) throw new Error(d.error || 'Create failed');
          if (typeof toast === 'function') toast('Task scheduled!', 'ok');
          p18HideCreateForm();
          await p18LoadAll();
        } catch (e) {
          if (typeof toast === 'function') toast(`Error: ${e.message}`, 'err');
        } finally {
          if (btn) { btn.disabled = false; btn.textContent = '✓ Create Task'; }
        }
      };

      // ── Task actions ──────────────────────────────────────────────────
      window.p18ToggleTask = async function (tid, btn) {
        if (btn) btn.disabled = true;
        try {
          const r = await fetch(`/api/scheduler/tasks/${tid}/toggle`, { method: 'POST' });
          const d = await r.json();
          if (d.ok) {
            if (typeof toast === 'function') toast(d.enabled ? 'Task enabled' : 'Task disabled', 'ok');
            await p18LoadTasks();
          }
        } catch (e) { } finally {
          if (btn) btn.disabled = false;
        }
      };

      window.p18RunNow = async function (tid, btn) {
        if (btn) { btn.disabled = true; btn.textContent = '⏳'; }
        try {
          const r = await fetch(`/api/scheduler/tasks/${tid}/run-now`, { method: 'POST' });
          const d = await r.json();
          if (d.ok) {
            if (typeof toast === 'function') toast('Task triggered!', 'ok');
            setTimeout(p18LoadAll, 2000);
          } else {
            if (typeof toast === 'function') toast(d.error || 'Failed', 'err');
          }
        } catch (e) {
          if (typeof toast === 'function') toast('Network error', 'err');
        } finally {
          if (btn) { btn.disabled = false; btn.textContent = '▶▶'; }
        }
      };

      window.p18DeleteTask = async function (tid, btn) {
        if (!confirm('Delete this scheduled task?')) return;
        if (btn) btn.disabled = true;
        try {
          const r = await fetch(`/api/scheduler/tasks/${tid}`, { method: 'DELETE' });
          const d = await r.json();
          if (d.ok) {
            if (typeof toast === 'function') toast('Task deleted', 'ok');
            await p18LoadAll();
          }
        } catch (e) { } finally {
          if (btn) btn.disabled = false;
        }
      };

      // ── Helpers ───────────────────────────────────────────────────────
      function _esc(s) {
        return String(s || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
      }

      console.debug('[Phase 18] Background Autonomous Agents + Task Scheduler active.');

    })();

  })();
