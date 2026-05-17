async function loadEvolutionDashboard() {
    const btn = $('evoRefreshBtn');
    if (btn) { btn.disabled = true; btn.textContent = '⟳ Loading…'; }
    try {
      const [ev_res, gov_res] = await Promise.all([
        api('GET', '/api/evolution/stats'),
        api('GET', '/api/governance/status')
      ]);
      const data = ev_res.data || {};
      const gov = gov_res.data || {};

      const sEl = $('evoStrategies');
      if (sEl) {
        sEl.innerHTML = (data.strategies || []).map(s =>
          `<div style="display:flex;justify-content:space-between;padding:8px;border-bottom:1px solid var(--border)">
                    <strong>${escapeHtml(s.strategy)}</strong>
                    <span>Win: <span style="color:${s.win_rate > 50 ? '#3fb950' : '#f85149'}">${s.win_rate}%</span> (${s.successes}/${s.attempts}) | Score: ${s.score}</span>
                </div>`
        ).join('') || '<div style="color:var(--muted)">No data yet.</div>';
      }

      const pEl = $('evoPrompts');
      if (pEl) {
        pEl.innerHTML = (data.prompts || []).map(p =>
          `<div style="display:flex;justify-content:space-between;padding:8px;border-bottom:1px solid var(--border)">
                    <strong>${escapeHtml(p.id)}</strong>
                    <span>v${p.version} (Qual: ${p.quality})</span>
                </div>`
        ).join('') || '<div style="color:var(--muted)">No data yet.</div>';
      }

      const rEl = $('evoReflections');
      if (rEl) {
        rEl.innerHTML = (data.reflections || []).map(r =>
          `<div style="padding:8px;border-bottom:1px solid var(--border);margin-bottom:8px">
                    <div style="font-size:0.8rem;color:var(--muted);margin-bottom:4px">Task: ${escapeHtml(r.task.substring(0, 80))}...</div>
                    <div style="color:${r.success ? '#3fb950' : '#f85149'};font-weight:bold;margin-bottom:4px">${r.success ? 'Success' : 'Failure'}</div>
                    <div style="font-size:0.85rem"><em>Meta:</em> ${escapeHtml(r.meta)}</div>
                </div>`
        ).join('') || '<div style="color:var(--muted)">No data yet.</div>';
      }

      const ptEl = $('evoPatches');
      if (ptEl) {
        ptEl.innerHTML = (data.patches || []).map(p =>
          `<div style="padding:10px;background:#d2992222;border:1px solid #d29922;border-radius:6px;margin-bottom:8px">
                    <div style="font-weight:bold;color:#d29922">${escapeHtml(p.title)}</div>
                    <div style="font-size:0.8rem;margin:4px 0">Target: <code>${escapeHtml(p.file)}</code></div>
                    <button style="background:#238636;color:white;border:none;padding:4px 8px;border-radius:4px;cursor:pointer;font-size:0.75rem" onclick="applySystemPatch('${p.id}')">Approve & Apply</button>
                </div>`
        ).join('') || '<div style="color:var(--muted)">No pending proposals.</div>';
      }
    } catch (e) { console.error(e); }
    finally {
      if (btn) { btn.disabled = false; btn.textContent = '⟳ Refresh'; }
      renderGovernance(gov);
    }
  }

  window.applySystemPatch = async function (pid) {
    if (!confirm('Apply this code modification to the system?')) return;
    try {
      const res = await api('POST', '/api/governance/patch/apply', { id: pid });
      if (res.ok) alert('Patch applied successfully. Restart recommended.');
      else alert('Failed to apply patch.');
      loadEvolutionDashboard();
    } catch (e) { console.error(e); }
  };

  function renderGovernance(gov) {
    const eEl = $('govEvals');
    if (eEl) {
      eEl.innerHTML = (gov.evaluations || []).map(e =>
        `<div style="margin-bottom:8px;padding:6px;background:var(--bg);border-radius:4px">
                <div style="font-weight:bold;color:#3fb950">#${e.patch_id} Score: ${e.score}</div>
                <div style="font-size:0.75rem;color:var(--muted)">${escapeHtml(e.issues || 'No issues found.')}</div>
            </div>`
      ).join('') || 'No evaluations yet.';
    }
    const aEl = $('govAudit');
    if (aEl) {
      aEl.innerHTML = (gov.audit_logs || []).map(l =>
        `<div style="font-size:0.8rem;padding:4px 0;border-bottom:1px solid var(--border);display:flex;gap:10px">
                <span style="color:var(--muted);white-space:nowrap">${new Date(l.ts * 1000).toLocaleTimeString()}</span>
                <strong style="color:#d29922;min-width:100px">${escapeHtml(l.event)}</strong>
                <span>${escapeHtml(l.desc)} ➔ <span style="color:var(--muted)">${escapeHtml(l.res)}</span></span>
            </div>`
      ).join('') || 'Log is empty.';
    }
  }

  // ── Phase 39: Autonomous Worker ────────────────────────────────────────────
  let _workerPollTimer = null;

  function startWorkerPoll() {
    stopWorkerPoll();
    _workerPollTimer = setInterval(loadWorkerDashboard, 5000);
  }
  function stopWorkerPoll() {
    if (_workerPollTimer) { clearInterval(_workerPollTimer); _workerPollTimer = null; }
  }

  async function workerControl(action) {
    try {
      await api('POST', `/api/worker/${action}`);
      loadWorkerDashboard();
    } catch (e) { console.error('[Worker]', e); }
  }

  function quickGoal(title) {
    const el = $('wGoalTitle');
    if (el) { el.value = title; }
  }

  async function addWorkerGoal() {
    const title = ($('wGoalTitle') || {}).value || '';
    if (!title.trim()) { alert('Goal title required'); return; }
    const body = {
      title: title.trim(),
      description: ($('wGoalDesc') || {}).value || '',
      priority: parseInt(($('wGoalPriority') || {}).value || '5'),
      max_iterations: parseInt(($('wGoalMaxIter') || {}).value || '20'),
    };
    try {
      const res = await api('POST', '/api/worker/goals/add', body);
      if (res.ok) {
        ($('wGoalTitle') || {}).value = '';
        ($('wGoalDesc') || {}).value = '';
        loadWorkerDashboard();
      } else { alert('Failed: ' + (res.error || 'unknown')); }
    } catch (e) { alert('Error: ' + e); }
  }

  async function workerGoalAction(action, id) {
    try {
      await api('POST', `/api/worker/goals/${action}`, { id });
      loadWorkerDashboard();
    } catch (e) { console.error(e); }
  }

  function _statusColor(status) {
    return { queued: '#58a6ff', running: '#d29922', completed: '#3fb950', failed: '#f85149', paused: '#8b949e' }[status] || '#8b949e';
  }

  async function loadWorkerDashboard() {
    try {
      const [wRes, tRes] = await Promise.all([
        api('GET', '/api/worker/status'),
        api('GET', '/api/worker/tools'),
      ]);
      const d = wRes.data || {};
      const tools = tRes.data || [];

      // Status badge
      const badge = $('workerStatusBadge');
      if (badge) {
        const running = d.running;
        badge.textContent = running ? '● Running' : '● Stopped';
        badge.style.background = running ? '#1a3a1a' : '#21262d';
        badge.style.color = running ? '#3fb950' : '#8b949e';
      }

      // Stat cards
      const counts = d.counts || {};
      ['Queued', 'Running', 'Completed', 'Failed'].forEach(k => {
        const el = $('wCount' + k);
        if (el) el.textContent = counts[k.toLowerCase()] || 0;
      });

      // Goals list
      const gl = $('wGoalsList');
      if (gl) {
        const goals = d.goals || [];
        if (!goals.length) { gl.innerHTML = '<div style="color:var(--muted);text-align:center;padding:20px">No goals yet. Add one above!</div>'; }
        else {
          gl.innerHTML = goals.map(g => {
            const sc = _statusColor(g.status);
            const prog = g.max_iterations > 0 ? Math.min(100, Math.round((g.iterations / g.max_iterations) * 100)) : 0;
            const isCurrent = g.id === d.current_goal;
            return `<div style="margin-bottom:10px;padding:10px;border-radius:6px;border:1px solid ${sc}33;background:${sc}0a;${isCurrent ? 'border-left:3px solid ' + sc : ''}">
                        <div style="display:flex;justify-content:space-between;align-items:flex-start">
                            <div style="flex:1;min-width:0">
                                <div style="font-weight:bold;font-size:0.85rem;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${escapeHtml(g.title)}</div>
                                <div style="display:flex;gap:8px;margin-top:4px;align-items:center">
                                    <span style="background:${sc}22;color:${sc};padding:1px 6px;border-radius:10px;font-size:0.7rem">${g.status}</span>
                                    <span style="font-size:0.7rem;color:var(--muted)">p=${g.priority} | iter:${g.iterations}/${g.max_iterations}</span>
                                    ${isCurrent ? '<span style="color:#d29922;font-size:0.7rem;font-weight:bold">⚡ ACTIVE</span>' : ''}
                                </div>
                                ${g.status === 'running' ? `<div style="margin-top:6px;background:var(--border);border-radius:4px;height:3px"><div style="width:${prog}%;background:${sc};height:3px;border-radius:4px;transition:width 0.3s"></div></div>` : ''}
                                ${g.error ? `<div style="font-size:0.72rem;color:#f85149;margin-top:4px">✗ ${escapeHtml(g.error.substring(0, 100))}</div>` : ''}
                                ${g.result && g.status === 'completed' ? `<div style="font-size:0.72rem;color:#3fb950;margin-top:4px">✓ Done</div>` : ''}
                            </div>
                            <div style="display:flex;flex-direction:column;gap:4px;margin-left:8px">
                                ${g.status === 'queued' ? `<button onclick="workerGoalAction('pause','${g.id}')" style="background:#21262d;color:var(--text);border:1px solid var(--border);padding:3px 8px;border-radius:4px;cursor:pointer;font-size:0.7rem">⏸</button>` : ''}
                                ${g.status === 'paused' ? `<button onclick="workerGoalAction('resume','${g.id}')" style="background:#21262d;color:var(--text);border:1px solid var(--border);padding:3px 8px;border-radius:4px;cursor:pointer;font-size:0.7rem">▶</button>` : ''}
                                <button onclick="workerGoalAction('delete','${g.id}')" style="background:#21262d;color:#f85149;border:1px solid #f8514933;padding:3px 8px;border-radius:4px;cursor:pointer;font-size:0.7rem">🗑</button>
                            </div>
                        </div>
                    </div>`;
          }).join('');
        }
      }

      // Task log
      const tl = $('wTaskLog');
      if (tl) {
        const tasks = d.recent_tasks || [];
        if (!tasks.length) { tl.innerHTML = '<span style="color:var(--muted)">No tasks executed yet.</span>'; }
        else {
          tl.innerHTML = tasks.map(t => {
            const icon = t.success ? '<span style="color:#3fb950">✓</span>' : '<span style="color:#f85149">✗</span>';
            const ts = new Date(t.ts * 1000).toLocaleTimeString();
            return `<div style="padding:3px 0;border-bottom:1px solid var(--border)">${icon} <span style="color:var(--muted)">${ts}</span> [<span style="color:#58a6ff">${escapeHtml(t.tool)}</span>] ${escapeHtml((t.step || '').substring(0, 80))}</div>`;
          }).join('');
        }
      }

      // Tools
      const toolEl = $('wToolsList');
      if (toolEl) {
        toolEl.innerHTML = tools.map(t =>
          `<div style="padding:10px;background:var(--bg);border:1px solid var(--border);border-radius:6px;min-width:160px;flex:1">
                    <div style="font-weight:bold;color:#58a6ff;font-size:0.85rem">🛠 ${escapeHtml(t.name)}</div>
                    <div style="font-size:0.72rem;color:var(--muted);margin-top:4px">${escapeHtml(t.description)}</div>
                </div>`
        ).join('');
      }

    } catch (e) { console.error('[Worker UI]', e); }
  }

  // ── Phase 40: AI Team Dashboard ───────────────────────────────────────────
  let _teamPollTimer = null;

  function startTeamPoll() { stopTeamPoll(); _teamPollTimer = setInterval(loadTeamDashboard, 6000); }
  function stopTeamPoll() { if (_teamPollTimer) { clearInterval(_teamPollTimer); _teamPollTimer = null; } }

  async function teamControl(action) {
    try { await api('POST', `/api/team/${action}`); loadTeamDashboard(); }
    catch (e) { console.error('[Team]', e); }
  }

  async function workerControl2(action, role) {
    try { await api('POST', `/api/team/worker/${action}`, { role }); loadTeamDashboard(); }
    catch (e) { console.error('[Team worker]', e); }
  }

  function setTeamGoal(title) { const el = $('teamGlobalTitle'); if (el) el.value = title; }

  async function addGlobalGoal() {
    const title = ($('teamGlobalTitle') || {}).value || '';
    if (!title.trim()) { alert('Goal title required'); return; }
    try {
      const res = await api('POST', '/api/team/goals/global', {
        title: title.trim(),
        description: ($('teamGlobalDesc') || {}).value || ''
      });
      if (res.ok) { ($('teamGlobalTitle') || {}).value = ''; ($('teamGlobalDesc') || {}).value = ''; loadTeamDashboard(); }
      else alert('Failed: ' + (res.error || 'unknown'));
    } catch (e) { alert('Error: ' + e); }
  }

  async function assignWorkerGoal() {
    const role = ($('teamAssignRole') || {}).value || '';
    const title = ($('teamAssignTitle') || {}).value || '';
    if (!title.trim()) { alert('Task title required'); return; }
    try {
      const res = await api('POST', '/api/team/goals/assign', {
        role, title: title.trim(), priority: parseInt(($('teamAssignPriority') || {}).value || '5')
      });
      if (res.ok) { ($('teamAssignTitle') || {}).value = ''; loadTeamDashboard(); }
      else alert('Failed: ' + (res.error || 'unknown'));
    } catch (e) { alert('Error: ' + e); }
  }

  async function clearTeamMessages() {
    try { await api('POST', '/api/team/messages/clear'); loadTeamDashboard(); }
    catch (e) { console.error(e); }
  }

  async function loadTeamDashboard() {
    try {
      const res = await api('GET', '/api/team/status');
      const d = res.data || {};
      const workers = d.workers || {};
      const ROLE_META = {
        manager: { emoji: '🧠', color: '#d29922' },
        research: { emoji: '🔍', color: '#58a6ff' },
        coding: { emoji: '💻', color: '#3fb950' },
        deployment: { emoji: '🚀', color: '#a371f7' },
      };

      // Worker cards
      const cardsEl = $('teamWorkerCards');
      if (cardsEl) {
        cardsEl.innerHTML = Object.entries(workers).map(([role, w]) => {
          const meta = ROLE_META[role] || { emoji: '🤖', color: '#8b949e' };
          const col = w.running ? meta.color : '#8b949e';
          const qd = w.counts || {};
          return `<div style="background:var(--bg-card);border:2px solid ${col}33;border-top:3px solid ${col};border-radius:8px;padding:14px">
                    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px">
                        <div>
                            <div style="font-size:1.2rem">${meta.emoji} <strong>${escapeHtml(w.label || role)}</strong></div>
                            <div style="font-size:0.7rem;color:${col};font-weight:bold">${w.running ? '● RUNNING' : '○ IDLE'}</div>
                        </div>
                        <div style="display:flex;gap:4px">
                            ${w.running
              ? `<button onclick="workerControl2('stop','${role}')" style="background:#da363333;color:#f85149;border:1px solid #da363355;padding:3px 8px;border-radius:4px;cursor:pointer;font-size:0.7rem">⏹</button>`
              : `<button onclick="workerControl2('start','${role}')" style="background:#23863633;color:#3fb950;border:1px solid #23863655;padding:3px 8px;border-radius:4px;cursor:pointer;font-size:0.7rem">▶</button>`
            }
                        </div>
                    </div>
                    <div style="display:grid;grid-template-columns:1fr 1fr;gap:4px;font-size:0.72rem">
                        <div style="background:var(--bg);padding:4px;border-radius:4px;text-align:center">
                            <div style="font-weight:bold;color:#58a6ff">${qd.queued || 0}</div>
                            <div style="color:var(--muted)">queued</div>
                        </div>
                        <div style="background:var(--bg);padding:4px;border-radius:4px;text-align:center">
                            <div style="font-weight:bold;color:#3fb950">${qd.completed || 0}</div>
                            <div style="color:var(--muted)">done</div>
                        </div>
                    </div>
                    ${w.current_goal ? `<div style="margin-top:6px;font-size:0.7rem;color:#d29922;background:#d2992211;padding:3px 6px;border-radius:4px">⚡ Working...</div>` : ''}
                    ${(w.recent_tasks || []).length ? `<div style="margin-top:6px;font-size:0.68rem;color:var(--muted);overflow:hidden;text-overflow:ellipsis;white-space:nowrap">Last: ${escapeHtml((w.recent_tasks[0] || {}).step || '')}</div>` : ''}
                </div>`;
        }).join('');
      }

      // Global goals
      const ggEl = $('teamGlobalGoals');
      if (ggEl) {
        const goals = d.global_goals || [];
        if (!goals.length) {
          ggEl.innerHTML = '<div style="color:var(--muted);text-align:center;padding:16px">No global goals yet. Add one above!</div>';
        } else {
          const STATUS_COLORS = { pending: '#8b949e', delegating: '#d29922', in_progress: '#58a6ff', completed: '#3fb950', failed: '#f85149' };
          ggEl.innerHTML = goals.map(g => {
            const sc = STATUS_COLORS[g.status] || '#8b949e';
            // find sub-goals
            const subs = (d.sub_goals || []).filter(s => s.global_goal_id === g.id);
            const doneCount = subs.filter(s => s.status === 'completed').length;
            const pct = subs.length ? Math.round(doneCount / subs.length * 100) : 0;
            return `<div style="margin-bottom:12px;padding:12px;border:1px solid ${sc}44;border-left:4px solid ${sc};border-radius:6px;background:${sc}08">
                        <div style="display:flex;justify-content:space-between;align-items:flex-start">
                            <div style="flex:1">
                                <strong>${escapeHtml(g.title)}</strong>
                                <span style="margin-left:8px;background:${sc}22;color:${sc};padding:1px 8px;border-radius:10px;font-size:0.7rem">${g.status}</span>
                            </div>
                            <span style="font-size:0.7rem;color:var(--muted)">${new Date((g.created_at || 0) * 1000).toLocaleTimeString()}</span>
                        </div>
                        ${subs.length ? `<div style="margin-top:8px">
                            <div style="display:flex;align-items:center;gap:8px">
                                <div style="flex:1;background:var(--border);border-radius:4px;height:4px">
                                    <div style="width:${pct}%;background:${sc};height:4px;border-radius:4px;transition:width 0.5s"></div>
                                </div>
                                <span style="font-size:0.7rem;color:var(--muted)">${doneCount}/${subs.length} tasks</span>
                            </div>
                            <div style="display:flex;flex-wrap:wrap;gap:4px;margin-top:6px">
                                ${subs.map(s => {
              const ssc = STATUS_COLORS[s.status] || '#8b949e';
              const re = ROLE_META[s.role] || {};
              return `<div style="font-size:0.68rem;padding:2px 8px;background:${ssc}18;border:1px solid ${ssc}44;border-radius:10px;color:${ssc}">${re.emoji || ''} ${escapeHtml(s.role)}: ${escapeHtml(s.title.substring(0, 40))}</div>`;
            }).join('')}
                            </div>
                        </div>` : ''}
                    </div>`;
          }).join('');
        }
      }

      // Sub-goals
      const sgEl = $('teamSubGoals');
      if (sgEl) {
        const subs = d.sub_goals || [];
        if (!subs.length) { sgEl.innerHTML = '<div style="color:var(--muted)">No sub-goals yet.</div>'; }
        else {
          const SC = { pending: '#8b949e', delegated: '#d29922', completed: '#3fb950', failed: '#f85149' };
          sgEl.innerHTML = subs.map(s => {
            const col = SC[s.status] || '#8b949e';
            const meta = ROLE_META[s.role] || { emoji: '🤖' };
            return `<div style="padding:6px;border-bottom:1px solid var(--border);font-size:0.8rem;display:flex;gap:8px;align-items:center">
                        <span style="color:${col};font-size:0.7rem">${s.status === 'completed' ? '✓' : s.status === 'failed' ? '✗' : '○'}</span>
                        <span>${meta.emoji}</span>
                        <span style="flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${escapeHtml(s.title)}</span>
                        <span style="background:${col}22;color:${col};padding:1px 6px;border-radius:10px;font-size:0.65rem">${s.status}</span>
                    </div>`;
          }).join('');
        }
      }

      // Messages
      const msgEl = $('teamMessages');
      if (msgEl) {
        const msgs = d.messages || [];
        if (!msgs.length) { msgEl.innerHTML = '<span style="color:var(--muted)">No messages yet.</span>'; }
        else {
          msgEl.innerHTML = msgs.map(m => {
            const fromMeta = ROLE_META[m.from] || { emoji: '?', color: '#8b949e' };
            const toMeta = m.to ? (ROLE_META[m.to] || { emoji: '?' }) : { emoji: '📢' };
            const ts = new Date((m.ts || 0) * 1000).toLocaleTimeString();
            return `<div style="padding:3px 0;border-bottom:1px solid var(--border)22">
                        <span style="color:var(--muted)">${ts}</span>
                        <span style="color:${fromMeta.color};font-weight:bold"> ${fromMeta.emoji}${escapeHtml(m.from)}</span>
                        <span style="color:var(--muted)"> → </span>
                        <span>${m.to ? toMeta.emoji + escapeHtml(m.to) : '📢 all'}</span>
                        <span style="color:var(--muted)"> [${escapeHtml(m.subject)}]</span>
                    </div>`;
          }).join('');
        }
      }

      // Shared memory
      const memEl = $('teamSharedMemory');
      if (memEl) {
        const mem = d.shared_memory || {};
        const keys = Object.keys(mem);
        if (!keys.length) { memEl.innerHTML = '<span style="color:var(--muted)">Memory empty.</span>'; }
        else {
          memEl.innerHTML = keys.slice(-20).map(k => {
            const v = typeof mem[k] === 'object' ? JSON.stringify(mem[k]).substring(0, 100) : String(mem[k]).substring(0, 100);
            return `<div style="padding:4px;border-bottom:1px solid var(--border);display:flex;gap:12px">
                        <span style="color:#58a6ff;font-weight:bold;min-width:160px;overflow:hidden;text-overflow:ellipsis">${escapeHtml(k)}</span>
                        <span style="color:var(--muted)">${escapeHtml(v)}</span>
                    </div>`;
          }).join('');
        }
      }
    } catch (e) { console.error('[Team UI]', e); }
  }

  // ── Phase 41: Projects & Outputs Dashboard ────────────────────────────────
  let _projectsPollTimer = null;

  function startProjectsPoll() { stopProjectsPoll(); _projectsPollTimer = setInterval(loadProjectsDashboard, 5000); }
  function stopProjectsPoll() { if (_projectsPollTimer) { clearInterval(_projectsPollTimer); _projectsPollTimer = null; } }

  function setProject(name, goal, type) {
    const ne = $('pName'); if (ne) ne.value = name;
    const ge = $('pGoal'); if (ge) ge.value = goal;
    const te = $('pType'); if (te) te.value = type;
  }

  async function submitProject() {
    const name = ($('pName') || {}).value || '';
    const goal = ($('pGoal') || {}).value || '';
    if (!name.trim() || !goal.trim()) { alert('Name and goal required'); return; }
    const btn = document.querySelector('[onclick="submitProject()"]');
    if (btn) { btn.disabled = true; btn.textContent = '⏳ Building...'; }
    try {
      const res = await api('POST', '/api/projects/submit', {
        name: name.trim(), goal: goal.trim(),
        type: ($('pType') || {}).value || 'website',
        platform: ($('pPlatform') || {}).value || 'local',
        max_retries: parseInt(($('pRetries') || {}).value || '2'),
      });
      if (res.ok) {
        ($('pName') || {}).value = '';
        ($('pGoal') || {}).value = '';
        loadProjectsDashboard();
      } else { alert('Failed: ' + (res.error || 'unknown')); }
    } catch (e) { alert('Error: ' + e); }
    finally { if (btn) { btn.disabled = false; btn.textContent = '🚀 Build Project'; } }
  }

  async function deployArtifact(aid) {
    const platform = prompt('Deploy to platform? (vercel / netlify / railway / render):', 'vercel');
    if (!platform) return;
    try {
      const res = await api('POST', '/api/deploy/trigger', { platform, artifact_id: aid });
      if (res.ok && res.data && res.data.live_url) {
        alert('✅ Deployed! URL: ' + res.data.live_url);
      } else {
        alert('Deploy failed: ' + (res.data && res.data.error || 'unknown'));
      }
      loadProjectsDashboard();
    } catch (e) { alert('Error: ' + e); }
  }

  async function viewArtifactFiles(aid) {
    try {
      const res = await api('GET', `/api/artifacts/${aid}/files`);
      const files = res.data || {};
      const keys = Object.keys(files);
      if (!keys.length) { alert('No files found.'); return; }
      let msg = keys.map(k => `📄 ${k} (${(files[k] || '').length} chars)`).join('\n');
      alert('Artifact files:\n' + msg);
    } catch (e) { alert('Error: ' + e); }
  }

  async function loadProjectsDashboard() {
    try {
      const [statsRes, projRes, artRes, platRes] = await Promise.all([
        api('GET', '/api/projects/stats'),
        api('GET', '/api/projects/list'),
        api('GET', '/api/artifacts/list' + (($('pArtFilter') || {}).value ? '?status=' + $('pArtFilter').value : '')),
        api('GET', '/api/deploy/platforms'),
      ]);

      // Stats
      const ps = statsRes.projects || {};
      const as_ = statsRes.artifacts || {};
      const _sv = (id, v) => { const e = $(id); if (e) e.textContent = v; };
      _sv('pStatTotal', ps.total || 0);
      _sv('pStatRunning', ps.running || 0);
      _sv('pStatCompleted', ps.completed || 0);
      _sv('pStatFailed', ps.failed || 0);
      _sv('pStatSuccess', (ps.success_rate || 0) + '%');

      // Platforms
      const platEl = $('pPlatforms');
      if (platEl) {
        const platforms = platRes.data || [];
        platEl.innerHTML = platforms.map(p => {
          const icons = { vercel: '▲', netlify: '🌀', railway: '🚂', render: '🎯', github_actions: '⚙️' };
          const col = p.configured ? '#3fb950' : '#f85149';
          return `<div style="display:flex;align-items:center;justify-content:space-between;padding:8px;border-radius:6px;border:1px solid ${col}33;background:${col}08">
                    <div style="display:flex;align-items:center;gap:8px">
                        <span style="font-size:1.1rem">${icons[p.platform] || '🚀'}</span>
                        <span style="font-weight:600;text-transform:capitalize">${p.platform}</span>
                    </div>
                    <span style="font-size:0.75rem;padding:2px 8px;border-radius:10px;background:${col}22;color:${col}">
                        ${p.configured ? '✓ Configured' : '✗ Token Missing'}
                    </span>
                </div>`;
        }).join('');
      }

      // Projects list
      const projEl = $('pProjectsList');
      if (projEl) {
        const projects = projRes.data || [];
        const SC = {
          queued: '#8b949e', planning: '#58a6ff', building: '#d29922', validating: '#a371f7',
          deploying: '#d29922', completed: '#3fb950', failed: '#f85149'
        };
        const TI = { website: '🌐', api: '🔌', report: '📄', saas: '🚀' };
        if (!projects.length) {
          projEl.innerHTML = '<div style="color:var(--muted);text-align:center;padding:20px">No projects yet. Launch one above!</div>';
        } else {
          projEl.innerHTML = `<div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(340px,1fr));gap:12px">` +
            projects.map(p => {
              const sc = SC[p.status] || '#8b949e';
              const ti = TI[p.type] || '📦';
              const isRunning = !['completed', 'failed'].includes(p.status);
              const logs = (p.log || []);
              const lastLog = logs.length ? logs[logs.length - 1].msg : '';
              return `<div style="border:1px solid ${sc}33;border-top:3px solid ${sc};border-radius:8px;padding:14px;background:${sc}06">
                            <div style="display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:8px">
                                <div>
                                    <div style="font-weight:bold">${ti} ${escapeHtml(p.name)}</div>
                                    <div style="font-size:0.7rem;color:var(--muted)">${p.type} · retry ${p.retries}</div>
                                </div>
                                <span style="background:${sc}22;color:${sc};padding:2px 8px;border-radius:10px;font-size:0.7rem;font-weight:bold">${p.status}</span>
                            </div>
                            ${isRunning ? `<div style="margin-bottom:8px">
                                <div style="height:3px;background:var(--border);border-radius:2px;overflow:hidden">
                                    <div style="height:3px;background:${sc};border-radius:2px;animation:pulsebar 1.5s ease-in-out infinite"></div>
                                </div>
                            </div>` : ''}
                            ${lastLog ? `<div style="font-size:0.72rem;color:var(--muted);margin-bottom:8px;font-style:italic">${escapeHtml(lastLog.substring(0, 100))}</div>` : ''}
                            ${p.live_url ? `<div style="margin-bottom:6px">
                                <a href="${escapeHtml(p.live_url)}" target="_blank" style="color:#58a6ff;font-size:0.8rem;text-decoration:none">🔗 ${escapeHtml(p.live_url.substring(0, 50))}</a>
                            </div>` : ''}
                            ${p.error ? `<div style="font-size:0.72rem;color:#f85149;margin-top:4px">⚠ ${escapeHtml(p.error.substring(0, 120))}</div>` : ''}
                            ${p.artifact_id ? `<div style="margin-top:8px;display:flex;gap:6px">
                                <button onclick="viewArtifactFiles('${p.artifact_id}')" style="flex:1;background:#21262d;color:var(--text);border:1px solid var(--border);padding:4px;border-radius:4px;cursor:pointer;font-size:0.72rem">📄 Files</button>
                                <button onclick="deployArtifact('${p.artifact_id}')" style="flex:1;background:#21262d;color:#58a6ff;border:1px solid #58a6ff44;padding:4px;border-radius:4px;cursor:pointer;font-size:0.72rem">🚀 Deploy</button>
                            </div>` : ''}
                        </div>`;
            }).join('') + '</div>';
        }
      }

      // Artifacts
      const artEl = $('pArtifactsList');
      if (artEl) {
        const artifacts = artRes.data || [];
        if (!artifacts.length) {
          artEl.innerHTML = '<div style="color:var(--muted);text-align:center;padding:16px">No artifacts yet.</div>';
        } else {
          const SC2 = { draft: '#8b949e', ready: '#d29922', deployed: '#3fb950', failed: '#f85149' };
          artEl.innerHTML = `<table style="width:100%;border-collapse:collapse;font-size:0.82rem">
                    <thead><tr style="border-bottom:1px solid var(--border);color:var(--muted);text-align:left">
                        <th style="padding:8px">Name</th><th>Type</th><th>Status</th>
                        <th>Platform</th><th>Version</th><th>Live URL</th><th>Actions</th>
                    </tr></thead>
                    <tbody>` +
            artifacts.map(a => {
              const sc = SC2[a.status] || '#8b949e';
              const TI2 = { website: '🌐', api: '🔌', report: '📄', saas: '🚀', code: '💻' };
              return `<tr style="border-bottom:1px solid var(--border)22">
                            <td style="padding:8px;font-weight:600">${TI2[a.type] || '📦'} ${escapeHtml(a.name)}</td>
                            <td style="padding:8px;color:var(--muted)">${a.type}</td>
                            <td style="padding:8px"><span style="background:${sc}22;color:${sc};padding:1px 8px;border-radius:10px">${a.status}</span></td>
                            <td style="padding:8px;color:var(--muted)">${a.platform || '—'}</td>
                            <td style="padding:8px;text-align:center">v${a.version}</td>
                            <td style="padding:8px;max-width:200px;overflow:hidden;text-overflow:ellipsis">
                                ${a.live_url ? `<a href="${escapeHtml(a.live_url)}" target="_blank" style="color:#58a6ff;font-size:0.78rem">${escapeHtml(a.live_url.substring(0, 40))}...</a>` : '<span style="color:var(--muted)">—</span>'}
                            </td>
                            <td style="padding:8px;white-space:nowrap">
                                <button onclick="viewArtifactFiles('${a.id}')" style="background:#21262d;color:var(--text);border:1px solid var(--border);padding:3px 8px;border-radius:4px;cursor:pointer;font-size:0.7rem;margin-right:4px">📄</button>
                                <button onclick="deployArtifact('${a.id}')" style="background:#21262d;color:#58a6ff;border:1px solid #58a6ff44;padding:3px 8px;border-radius:4px;cursor:pointer;font-size:0.7rem">🚀</button>
                            </td>
                        </tr>`;
            }).join('') + '</tbody></table>';
        }
      }

    } catch (e) { console.error('[Projects UI]', e); }
  }

  /* ================================================================
     PHASE 4 — INTELLIGENCE & PERSONALIZATION LAYER
     ================================================================ */
  (function () {
    'use strict';

    /* ── STEP 5: Theme System ──────────────────────────────────────── */
    const THEME_KEY = 'p4_theme';

    function p4ApplyTheme(theme) {
      if (theme === 'light') {
        document.body.classList.add('light-theme');
        const btn = document.getElementById('p4ThemeBtn');
        if (btn) btn.textContent = '☀️';
      } else {
        document.body.classList.remove('light-theme');
        const btn = document.getElementById('p4ThemeBtn');
        if (btn) btn.textContent = '🌙';
      }
      localStorage.setItem(THEME_KEY, theme);
    }

    window.p4ToggleTheme = function () {
      const current = localStorage.getItem(THEME_KEY) || 'dark';
      p4ApplyTheme(current === 'dark' ? 'light' : 'dark');
    };

    // Restore saved theme on load
    (function () {
      const saved = localStorage.getItem(THEME_KEY) || 'dark';
      p4ApplyTheme(saved);
    })();


    /* ── STEP 4: Real-Time Token / Cost Tracker ───────────────────── */
    let p4TokenPollTimer = null;
    let p4TotalTokens = 0;
    let p4TotalCost = 0;

    async function p4UpdateTokenTracker() {
      try {
        // Pull from active session stats if available
        const sid = (typeof currentSession !== 'undefined') ? currentSession : null;
        if (!sid) {
          // Try costs totals
          const r = await fetch('/api/costs/totals?hours=1');
          if (!r.ok) return;
          const d = await r.json();
          if (d.ok && d.totals) {
            const t = d.totals;
            p4TotalTokens = (t.tokens_in_est || 0) + (t.tokens_out_est || 0);
            p4TotalCost = t.cost_usd || 0;
          }
        } else {
          const r = await fetch(`/api/session/${sid}`);
          if (!r.ok) return;
          const d = await r.json();
          const u = d.usage || d.resource_usage || null;
          if (u) {
            p4TotalTokens = (u.tokens_in_est || 0) + (u.tokens_out_est || 0);
            p4TotalCost = u.cost_usd || 0;
          }
        }
        const pill = document.getElementById('p4TokenPill');
        const tkEl = document.getElementById('p4TkCount');
        const costEl = document.getElementById('p4CostVal');
        if (pill) {
          if (p4TotalTokens > 0 || p4TotalCost > 0) {
            pill.style.display = 'flex';
            if (tkEl) tkEl.textContent = p4TotalTokens.toLocaleString();
            if (costEl) costEl.textContent = p4TotalCost.toFixed(4);
          } else {
            pill.style.display = 'none';
          }
        }
      } catch (e) { }
    }

    // Poll every 5s while page is visible
    function p4StartTokenPoll() {
      if (p4TokenPollTimer) clearInterval(p4TokenPollTimer);
      p4TokenPollTimer = setInterval(() => {
        if (!document.hidden) p4UpdateTokenTracker();
      }, 5000);
      p4UpdateTokenTracker();
    }
    window.NX_LOAD_TASKS.push( p4StartTokenPoll);


    /* ── STEP 1: Session History ──────────────────────────────────── */
    const P4_SESS_COLORS = {
      done: '#3fb950', running: '#f0883e', failed: '#f85149',
      stopped: '#d29922', queued: '#8b949e'
    };

    window.p4RefreshSessionHistory = async function () {
      const list = document.getElementById('p4SessList');
      if (!list) return;
      try {
        const r = await fetch('/api/sessions');
        if (!r.ok) return;
        const d = await r.json();
        const sessions = (d.sessions || []).slice(0, 8);
        if (!sessions.length) {
          list.innerHTML = '<div class="p4-sess-empty">No sessions yet</div>';
          return;
        }
        list.innerHTML = sessions.map(s => {
          const name = (s.task || s.project_name || s.sid || '(no task)').slice(0, 48);
          const ts = s.created_at ? new Date(s.created_at * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) : '';
          const st = s.status || 'queued';
          const col = P4_SESS_COLORS[st] || '#8b949e';
          const sid = s.sid || s.session_id || s.id || '';
          return `<div class="p4-sess-item" onclick="p4RestoreSession('${escHTML(sid)}')" title="Click to restore session">
                <div class="p4-si-name">${escHTML(name)}</div>
                <div class="p4-si-meta">
                  <span class="p4-si-dot" style="background:${col}"></span>
                  <span>${escHTML(st)}</span>
                  ${ts ? `<span style="margin-left:auto">${escHTML(ts)}</span>` : ''}
                </div>
            </div>`;
        }).join('');
      } catch (e) { }
    };

    function escHTML(s) {
      return (s || '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
    }

    window.p4RestoreSession = function (sid) {
      if (!sid) return;
      if (typeof selectSession === 'function') {
        selectSession(sid);
        // Switch to Logs tab to show session output
        if (typeof nxSetTab === 'function') nxSetTab('logs');
        else if (typeof setActiveTab === 'function') setActiveTab('logs');
      }
      // Show toast
      if (typeof toast === 'function') toast('Session restored', 'ok');
      else if (typeof nxToast === 'function') nxToast('Session restored');
    };

    // Refresh session history every 15s and on page load
    window.NX_LOAD_TASKS.push( () => {
      p4RefreshSessionHistory();
      setInterval(p4RefreshSessionHistory, 15000);
    });


    /* ── STEP 2: Prompt Templates System ─────────────────────────── */
    const P4_TEMPLATES = {
      build: [
        'Build a Flask REST API with CRUD endpoints and SQLite',
        'Create a full-stack todo app with login',
        'Build a real-time chat app using WebSockets',
        'Create a landing page for a SaaS product',
        'Build a file upload system with preview',
        'Create a data visualization dashboard with charts',
      ],
      fix: [
        'Fix all bugs and errors in this project',
        'Debug the failing tests and make them pass',
        'Fix the import errors and missing dependencies',
        'Resolve all syntax errors in the codebase',
        'Fix the authentication flow that is broken',
      ],
      api: [
        'Build a REST API with JWT authentication',
        'Create API endpoints for user management (CRUD)',
        'Add rate limiting and input validation to all API routes',
        'Build a webhook handler with retry logic',
        'Create a GraphQL API with resolvers',
      ],
      test: [
        'Write comprehensive unit tests for all functions',
        'Create integration tests for the API endpoints',
        'Add pytest fixtures and mock external calls',
        'Generate test data and seed scripts',
        'Set up CI/CD pipeline with automated testing',
      ],
    };

    let p4CurrentCat = 'build';

    function p4RenderTplChips() {
      const container = document.getElementById('p4TplChips');
      const savedContainer = document.getElementById('p4CustomSaved');
      if (!container) return;

      if (p4CurrentCat === 'saved') {
        container.innerHTML = '';
        p4RenderSavedTemplates();
        return;
      }

      const items = P4_TEMPLATES[p4CurrentCat] || [];
      container.innerHTML = items.map(t =>
        `<button class="nx-example-chip" onclick="nxSetTask(${JSON.stringify(t)})">${escHTML(t.length > 32 ? t.slice(0, 32) + '…' : t)}</button>`
      ).join('');
      if (savedContainer) p4RenderSavedTemplates();
    }

    function p4RenderSavedTemplates() {
      const container = document.getElementById('p4CustomSaved');
      if (!container) return;
      const saved = p4GetSavedTemplates();
      if (!saved.length) {
        if (p4CurrentCat === 'saved') {
          const chipsEl = document.getElementById('p4TplChips');
          if (chipsEl) chipsEl.innerHTML = '<span style="color:#8b949e;font-size:0.76rem;font-style:italic">No saved templates yet</span>';
        }
        container.innerHTML = '';
        return;
      }
      const html = saved.map((t, i) =>
        `<div class="p4-custom-chip" onclick="nxSetTask(${JSON.stringify(t)})">
           <span>${escHTML(t.length > 28 ? t.slice(0, 28) + '…' : t)}</span>
           <span class="p4-del" onclick="event.stopPropagation();p4DeleteSavedTemplate(${i})" title="Delete">×</span>
         </div>`
      ).join('');
      if (p4CurrentCat === 'saved') {
        const chipsEl = document.getElementById('p4TplChips');
        if (chipsEl) chipsEl.innerHTML = html;
        container.innerHTML = '';
      } else {
        container.innerHTML = html;
      }
    }

    function p4GetSavedTemplates() {
      try { return JSON.parse(localStorage.getItem('p4_saved_tpls') || '[]'); } catch { return []; }
    }

    window.p4SaveCustomTemplate = function () {
      const input = document.getElementById('p4CustomTplInput');
      const val = (input ? input.value : '').trim();
      if (!val) {
        // Save current task input content
        const ta = document.getElementById('taskInput');
        const task = ta ? ta.value.trim() : '';
        if (!task) { if (typeof toast === 'function') toast('Enter a task to save', 'err'); return; }
        const saved = p4GetSavedTemplates();
        if (!saved.includes(task)) { saved.unshift(task); localStorage.setItem('p4_saved_tpls', JSON.stringify(saved.slice(0, 20))); }
        if (typeof toast === 'function') toast('✅ Template saved', 'ok');
        p4RenderSavedTemplates();
        return;
      }
      const saved = p4GetSavedTemplates();
      if (!saved.includes(val)) { saved.unshift(val); localStorage.setItem('p4_saved_tpls', JSON.stringify(saved.slice(0, 20))); }
      if (input) input.value = '';
      if (typeof toast === 'function') toast('✅ Template saved', 'ok');
      p4RenderSavedTemplates();
    };

    window.p4DeleteSavedTemplate = function (idx) {
      const saved = p4GetSavedTemplates();
      saved.splice(idx, 1);
      localStorage.setItem('p4_saved_tpls', JSON.stringify(saved));
      p4RenderSavedTemplates();
    };

    window.p4SetTplCat = function (cat, el) {
      p4CurrentCat = cat;
      document.querySelectorAll('.p4-tpl-cat').forEach(b => b.classList.remove('active'));
      if (el) el.classList.add('active');
      p4RenderTplChips();
    };

    window.NX_LOAD_TASKS.push( () => {
      p4RenderTplChips();
      p4RenderSavedTemplates();
    });


    /* ── STEP 3: AI Suggestions Engine ───────────────────────────── */
    const P4_SUGGESTIONS = {
      flask: ['Build a Flask REST API', 'Create a Flask login system', 'Add Flask-SQLAlchemy models'],
      python: ['Write a Python script to', 'Create a Python CLI tool', 'Build a Python data processor'],
      react: ['Build a React dashboard', 'Create a React component library', 'Add state management with Zustand'],
      fix: ['Fix all errors in the codebase', 'Debug the failing tests', 'Resolve import errors'],
      test: ['Write unit tests for all functions', 'Create pytest integration tests', 'Generate mock data for testing'],
      api: ['Build a REST API with JWT auth', 'Create CRUD endpoints for users', 'Add rate limiting to all routes'],
      build: ['Build a full-stack web app', 'Create a landing page', 'Build a real-time chat app'],
      create: ['Create a user authentication system', 'Create a dashboard with charts', 'Create a file upload handler'],
      optimize: ['Optimize database queries', 'Refactor and clean up the codebase', 'Improve API response times'],
    };

    const P4_IDLE_SUGGESTIONS = [
      'Build a Flask REST API with authentication',
      'Create a real-time dashboard with WebSocket updates',
      'Write a web scraper with BeautifulSoup',
      'Build a CLI tool with argument parsing',
      'Create a CRUD app with SQLite database',
      'Generate unit tests for the existing codebase',
      'Build a file converter utility',
      'Create a REST API client with retry logic',
    ];

    function p4GetSuggestions(text) {
      if (!text || text.length < 2) return [];
      const lower = text.toLowerCase();
      let matches = [];
      for (const [key, items] of Object.entries(P4_SUGGESTIONS)) {
        if (lower.includes(key)) {
          matches = matches.concat(items.filter(s => !s.toLowerCase().startsWith(lower.slice(0, 8))));
        }
      }
      // Deduplicate and limit
      return [...new Set(matches)].slice(0, 5);
    }

    window.p4OnTaskInput = function (val) {
      const box = document.getElementById('p4SuggestBox');
      if (!box) return;
      const sugs = p4GetSuggestions(val);
      if (!sugs.length || !val.trim()) { box.classList.remove('open'); return; }
      box.innerHTML = '<div class="p4-suggest-label">Suggestions</div>' +
        sugs.map(s => `<div class="p4-suggest-item" onmousedown="event.preventDefault();nxSetTask(${JSON.stringify(s)});p4CloseSuggest()">${escHTML(s)}</div>`).join('');
      box.classList.add('open');
    };

    window.p4CloseSuggest = function () {
      const box = document.getElementById('p4SuggestBox');
      if (box) box.classList.remove('open');
    };

    // Show idle suggestions when no task is running
    function p4UpdateIdleSuggestions() {
      const el = document.getElementById('p4IdleSuggest');
      const chips = document.getElementById('p4IdleChips');
      if (!el || !chips) return;
      const isIdle = !currentSession ||
        (typeof nxSetGlobalStatus !== 'undefined' && document.getElementById('nxGlobalStatus')?.textContent === 'Idle');
      if (isIdle) {
        const shuffled = P4_IDLE_SUGGESTIONS.sort(() => Math.random() - 0.5).slice(0, 3);
        chips.innerHTML = shuffled.map(s =>
          `<button class="nx-example-chip" onclick="nxSetTask(${JSON.stringify(s)})">${escHTML(s.length > 32 ? s.slice(0, 32) + '…' : s)}</button>`
        ).join('');
        el.style.display = 'block';
      } else {
        el.style.display = 'none';
      }
    }
    window.NX_LOAD_TASKS.push( () => {
      p4UpdateIdleSuggestions();
      setInterval(p4UpdateIdleSuggestions, 20000);
    });


    /* ── STEP 7: Personalization ──────────────────────────────────── */
    const P4_PREFS_KEY = 'p4_prefs';

    function p4GetPrefs() {
      try { return JSON.parse(localStorage.getItem(P4_PREFS_KEY) || '{}'); } catch { return {}; }
    }
    function p4SavePref(key, val) {
      const p = p4GetPrefs();
      p[key] = val;
      localStorage.setItem(P4_PREFS_KEY, JSON.stringify(p));
    }

    // Remember + restore last plan mode
    window.NX_LOAD_TASKS.push( () => {
      const prefs = p4GetPrefs();
      // Restore last plan mode
      if (prefs.planMode && typeof nxSetPlan === 'function') {
        setTimeout(() => nxSetPlan(prefs.planMode, true), 500);
      }
      // Hook into plan changes to save preference
      const planOpts = document.querySelectorAll('.nx-plan-option');
      planOpts.forEach(opt => {
        opt.addEventListener('click', () => {
          const mode = opt.querySelector('.nx-plan-name')?.textContent?.toLowerCase()?.trim();
          if (mode) p4SavePref('planMode', mode);
        });
      });

      // Restore last session
      if (prefs.lastSession && typeof selectSession === 'function') {
        setTimeout(() => {
          if (!currentSession) selectSession(prefs.lastSession);
        }, 800);
      }
    });

    // Intercept session selection to save last session
    const _p4OrigSelectSession = window.selectSession;
    window.selectSession = function (sid) {
      if (_p4OrigSelectSession) _p4OrigSelectSession.call(this, sid);
      if (sid) {
        p4SavePref('lastSession', sid);
        // Update token tracker after session switch
        setTimeout(p4UpdateTokenTracker, 1000);
      }
    };

    // Remember last model (watch modelSelect changes)
    document.addEventListener('change', function (e) {
      if (e.target && e.target.id === 'modelSelect') {
        p4SavePref('lastModel', e.target.value);
      }
    });
    window.NX_LOAD_TASKS.push( () => {
      const prefs = p4GetPrefs();
      if (prefs.lastModel) {
        const ms = document.getElementById('modelSelect');
        if (ms && prefs.lastModel) {
          // Retry after config loads
          setTimeout(() => {
            if (ms.value !== prefs.lastModel) {
              const opt = ms.querySelector(`option[value="${prefs.lastModel}"]`);
              if (opt) ms.value = prefs.lastModel;
            }
          }, 1500);
        }
      }
    });


    /* ── STEP 6: UX Intelligence ──────────────────────────────────── */
    // Smooth auto-scroll for logArea — respects the existing autoScroll checkbox
    (function p4EnhanceAutoScroll() {
      window.NX_LOAD_TASKS.push( () => {
        const logArea = document.getElementById('logArea');
        if (!logArea) return;
        const obs = new MutationObserver(() => {
          const cb = document.getElementById('autoScroll');
          if (cb && cb.checked) {
            logArea.scrollTo({ top: logArea.scrollHeight, behavior: 'smooth' });
          }
        });
        obs.observe(logArea, { childList: true, subtree: true });
      });
    })();

    // Highlight active plan step in logs with a subtle border
    (function p4HighlightCurrentStep() {
      const origIngestLogRow = window.ingestLogRow;
      if (!origIngestLogRow) return;
      window.ingestLogRow = function (e, area) {
        origIngestLogRow.call(this, e, area);
        // Mark last log line with a subtle highlight if it's a step
        if (e && e.text && (e.text.includes('Step') || e.text.includes('Executing'))) {
          if (!area) return;
          const last = area.lastElementChild;
          if (last && last.classList.contains('log-line')) {
            last.style.borderLeft = '2px solid #388bfd44';
            last.style.paddingLeft = '6px';
            // Remove highlight after 2s
            setTimeout(() => { last.style.borderLeft = ''; last.style.paddingLeft = ''; }, 2000);
          }
        }
      };
    })();

    console.debug('[Phase 4] Intelligence & Personalization Layer active.');
  })();

  /* ================================================================
     PHASE 5 — BYOK MULTI-PROVIDER SYSTEM (Production-Grade)
     ================================================================ */
  (function () {
    'use strict';

    /* -- Provider catalog cache ---------------------------------------- */
    let p5Providers = [];          // full list from /api/providers
    let p5ActiveProvider = 'auto'; // currently selected provider id
    let p5ProviderMap = {};        // id -> provider object

    const P5_CAT_ORDER = ['core', 'high_value', 'open', 'multimodal'];
    const P5_CAT_IDS = { core: 'p5ByokCore', high_value: 'p5ByokHighValue', open: 'p5ByokOpen', multimodal: 'p5ByokMultimodal' };
    const P5_SPEED_COLORS = { fastest: '#3fb950', fast: '#58a6ff', balanced: '#d29922', slow: '#8b949e', variable: '#8b949e' };
    const P5_DOT_COLORS = { available: '#3fb950', byok: '#f0883e', unavailable: '#30363d' };


    /* -- Load providers ------------------------------------------------- */
    async function p5LoadProviders() {
      try {
        const r = await fetch('/api/providers');
        if (!r.ok) return;
        const d = await r.json();
        p5Providers = d.providers || [];
        p5ProviderMap = {};
        p5Providers.forEach(p => { p5ProviderMap[p.id] = p; });
        p5RenderProvMenu();
        p5SyncBadge();
      } catch (e) { }
    }

    /* -- Provider dot color -------------------------------------------- */
    function p5DotColor(prov) {
      if (!prov) return '#30363d';
      if (prov.has_platform_key) return '#3fb950';
      if (prov.has_byok_key) return '#f0883e';
      if (!prov.needs_key) return '#3fb950';
      return '#30363d';
    }

    /* -- Render the header provider dropdown menu ----------------------- */
    function p5RenderProvMenu() {
      const list = document.getElementById('p5ProvMenuList');
      if (!list) return;

      // AUTO option first
      let html = `<div class="p5-prov-row${p5ActiveProvider === 'auto' ? ' active' : ''}" onclick="p5SelectProvider('auto',event)">
        <span class="p5-r-dot" style="background:#58a6ff"></span>
        <span class="p5-r-label">AUTO — intelligent routing</span>
        <span class="p5-r-speed" style="color:#58a6ff">smart</span>
    </div>`;

      // Group by category
      const groups = {};
      p5Providers.forEach(p => {
        if (!groups[p.category]) groups[p.category] = [];
        groups[p.category].push(p);
      });

      const catLabels = { core: '🔵 Core', high_value: '⚡ High Value', open: '🌐 Open', multimodal: '🎨 Multimodal' };
      for (const cat of P5_CAT_ORDER) {
        const items = groups[cat] || [];
        if (!items.length) continue;
        html += `<div class="p5-prov-cat">${catLabels[cat] || cat}</div>`;
        items.forEach(p => {
          const dot = p5DotColor(p);
          const caps = (p.caps || []).slice(0, 2).map(c =>
            `<span class="p5-cap-tag ${c}">${c}</span>`).join('');
          const avail = p.available ? '' : ' style="opacity:0.45"';
          html += `<div class="p5-prov-row${p5ActiveProvider === p.id ? ' active' : ''}" onclick="p5SelectProvider('${p.id}',event)"${avail}>
                <span class="p5-r-dot" style="background:${dot}"></span>
                <span class="p5-r-label">${p.label}</span>
                <span class="p5-r-caps">${caps}</span>
                <span class="p5-r-speed" style="color:${P5_SPEED_COLORS[p.speed] || '#8b949e'}">${p.speed || ''}</span>
            </div>`;
        });
      }
      list.innerHTML = html;
    }

    /* -- Toggle provider dropdown --------------------------------------- */
    window.p5ToggleProvMenu = function (e) {
      e.stopPropagation();
      const menu = document.getElementById('p5ProvMenu');
      if (!menu) return;
      const isOpen = menu.classList.contains('open');
      // close all open menus first
      document.querySelectorAll('.p5-prov-menu.open').forEach(m => m.classList.remove('open'));
      if (!isOpen) menu.classList.add('open');
    };
    document.addEventListener('click', () => {
      document.querySelectorAll('.p5-prov-menu.open').forEach(m => m.classList.remove('open'));
    });

    /* -- Select a provider --------------------------------------------- */
    window.p5SelectProvider = function (pid, e) {
      if (e) e.stopPropagation();
      p5ActiveProvider = pid;
      // Sync the Force Model dropdown in settings
      const ms = document.getElementById('modelSelect');
      if (ms) ms.value = pid === 'auto' ? '' : pid;
      // Save to localStorage for session persistence
      localStorage.setItem('p5_active_provider', pid);
      p5SyncBadge();
      p5RenderProvMenu();
      document.querySelectorAll('.p5-prov-menu.open').forEach(m => m.classList.remove('open'));
      // Update routing display
      p5UpdateRoutingInfo();
      if (typeof toast === 'function') toast(`Provider: ${pid === 'auto' ? 'AUTO' : (p5ProviderMap[pid]?.label || pid)}`, 'ok');
    };

    /* -- Sync the header badge ------------------------------------------ */
    function p5SyncBadge() {
      const nameEl = document.getElementById('p5ProvName');
      const dotEl = document.getElementById('p5ProvDot');
      if (!nameEl || !dotEl) return;
      if (p5ActiveProvider === 'auto') {
        nameEl.textContent = 'AUTO';
        dotEl.style.background = '#58a6ff';
      } else {
        const prov = p5ProviderMap[p5ActiveProvider];
        nameEl.textContent = prov ? prov.label.split(' ')[0] : p5ActiveProvider.toUpperCase();
        dotEl.style.background = prov ? p5DotColor(prov) : '#30363d';
      }
    }

    /* -- Restore saved provider on load --------------------------------- */
    window.NX_LOAD_TASKS.push( () => {
      const saved = localStorage.getItem('p5_active_provider') || 'auto';
      p5ActiveProvider = saved;
      const ms = document.getElementById('modelSelect');
      if (ms) ms.value = saved === 'auto' ? '' : saved;
      p5LoadProviders();
      setTimeout(p5UpdateRoutingInfo, 2000);
    });

    /* -- Live routing info in inspector --------------------------------- */
    async function p5UpdateRoutingInfo() {
      try {
        // Get current plan mode from the badge
        const planBadge = document.querySelector('.nx-plan-badge, .nx-plan-name');
        const plan = planBadge ? planBadge.textContent.toLowerCase().trim() : 'pro';
        const r = await fetch(`/api/p5/routing?plan=${encodeURIComponent(plan)}`);
        if (!r.ok) return;
        const d = await r.json();

        // Inspector routing block
        const ri = document.getElementById('p5RouteInfo');
        const riProv = document.getElementById('p5RiProv');
        const riChain = document.getElementById('p5RiChain');
        if (ri && d.ok) {
          ri.style.display = 'block';
          if (riProv) {
            const label = p5ProviderMap[d.recommended]?.label || d.recommended;
            riProv.textContent = p5ActiveProvider === 'auto' ? label : (p5ProviderMap[p5ActiveProvider]?.label || p5ActiveProvider);
          }
          if (riChain && d.fallback_chain?.length) {
            const labels = d.fallback_chain.slice(0, 4).map(p => p5ProviderMap[p]?.label?.split(' ')[0] || p);
            riChain.textContent = 'Fallback: ' + labels.join(' → ');
          }
        }

        // Settings: routing recommendation
        const recProv = document.getElementById('p5RecProv');
        const recChain = document.getElementById('p5RecChain');
        const recBox = document.getElementById('p5RoutingRec');
        if (recBox && d.ok) {
          recBox.style.display = 'block';
          if (recProv) recProv.textContent = p5ProviderMap[d.recommended]?.label || d.recommended;
          if (recChain && d.fallback_chain?.length) {
            const labels = d.fallback_chain.slice(0, 4).map(p => p5ProviderMap[p]?.label?.split(' ')[0] || p);
            recChain.textContent = 'Chain: ' + labels.join(' → ');
          }
        }
      } catch (e) { }
    }

    /* -- BYOK Settings panel: render by category ----------------------- */
    window.p5RenderByokPanel = function (cfg) {
      // cfg = {api_keys: {}, api_keys_masked: {}, api_keys_set: {}}
      const keysSet = cfg.api_keys_set || {};
      const keysMasked = cfg.api_keys_masked || {};

      const groups = {};
      p5Providers.forEach(p => {
        if (!p.needs_key) return; // skip ollama (no key needed)
        if (!groups[p.category]) groups[p.category] = [];
        groups[p.category].push(p);
      });

      for (const cat of P5_CAT_ORDER) {
        const el = document.getElementById(P5_CAT_IDS[cat]);
        if (!el) continue;
        const items = groups[cat] || [];
        if (!items.length) { el.style.display = 'none'; continue; }
        el.style.display = '';
        el.innerHTML = items.map(p => {
          const isSet = keysSet[p.id] || false;
          const masked = keysMasked[p.id] || '';
          const capHtml = (p.caps || []).slice(0, 3).map(c =>
            `<span class="p5-cap-tag ${c}">${c}</span>`).join('');
          const dotClass = isSet ? 'green' : 'grey';
          return `<div style="margin-bottom:10px">
                <div class="p5-byok-row" style="grid-template-columns:auto 1fr">
                  <div class="p5-byok-label">
                    <span class="p5-status-dot ${dotClass}"></span>
                    <span>${p.label}</span>
                    <small style="color:#8b949e;margin-left:2px">${p.speed || ''}</small>
                  </div>
                  <div class="p5-byok-caps">${capHtml}</div>
                </div>
                <div style="display:flex;gap:5px;align-items:center">
                  <input class="p5-byok-input${isSet ? ' has-key' : ''}"
                         id="key_${p.id}"
                         type="password"
                         placeholder="${isSet ? masked : ('Enter ' + p.label + ' API key…')}"
                         autocomplete="off" spellcheck="false">
                  ${isSet ? `<button class="btn tiny" onclick="p5ClearKey('${p.id}')" title="Remove key" style="white-space:nowrap">✕ Clear</button>` : ''}
                </div>
                <div style="font-size:0.68rem;color:#8b949e;margin-top:2px">
                  ${p.models && p.models.length ? 'Models: ' + p.models.slice(0, 2).join(', ') : ''}
                </div>
            </div>`;
        }).join('');
      }
      // Show/hide category headers based on content
      document.querySelectorAll('.p5-byok-cat-header').forEach(h => {
        const next = h.nextElementSibling;
        if (next && next.innerHTML.trim() === '') h.style.display = 'none';
      });
    };

    /* -- Hook into existing renderByokProviders to also run Phase 5 ---- */
    const _p5OrigRenderByok = window.renderByokProviders;
    window.renderByokProviders = function (cfg) {
      if (_p5OrigRenderByok) _p5OrigRenderByok.call(this, cfg);
      if (p5Providers.length) p5RenderByokPanel(cfg);
      setTimeout(() => { if (p5Providers.length) p5RenderByokPanel(cfg); }, 500);
      // show routing rec
      document.getElementById('p5RoutingRec') && setTimeout(p5UpdateRoutingInfo, 300);
    };

    /* -- Clear a single key --------------------------------------------- */
    window.p5ClearKey = async function (pid) {
      try {
        const r = await fetch(`/api/key/${pid}`, { method: 'DELETE' });
        if (!r.ok) return;
        if (typeof toast === 'function') toast(`${p5ProviderMap[pid]?.label || pid} key removed`, 'ok');
        if (typeof refreshConfig === 'function') refreshConfig(true);
        await p5LoadProviders();
      } catch (e) { }
    };

    /* -- Collect BYOK keys from Phase 5 inputs -------------------------- */
    const _p5OrigGetByokKeys = window.getByokApiKeys;
    window.getByokApiKeys = function () {
      const keys = _p5OrigGetByokKeys ? _p5OrigGetByokKeys.call(this) : {};
      // Phase 5: also collect from p5-byok-input fields
      document.querySelectorAll('.p5-byok-input[id^="key_"]').forEach(inp => {
        const pid = inp.id.replace('key_', '');
        if (inp.value && inp.value !== '•'.repeat(8)) {
          keys[pid] = inp.value;
        }
      });
      return keys;
    };

    /* -- Test all configured keys --------------------------------------- */
    window.p5TestAllKeys = async function () {
      const btn = document.querySelector('.p5-test-btn');
      if (btn) btn.disabled = true;
      if (typeof toast === 'function') toast('Testing keys…', 'ok');
      try {
        const r = await fetch('/api/providers');
        if (!r.ok) throw new Error('fetch failed');
        const d = await r.json();
        const results = (d.providers || []).filter(p => p.has_byok_key || p.has_platform_key);
        const msg = results.length
          ? `${results.length} provider(s) configured: ${results.map(p => p.label).join(', ')}`
          : 'No keys configured yet.';
        if (typeof toast === 'function') toast(msg, 'ok');
        await p5LoadProviders();
      } catch (e) {
        if (typeof toast === 'function') toast('Error checking keys', 'err');
      }
    };

    /* -- Monitor session logs for failover events ----------------------- */
    function p5MonitorFailover() {
      // Hook into the existing log ingestion to detect failover messages
      const origIngest = window.ingestLogRow;
      if (!origIngest) return;
      window.ingestLogRow = function (e, area) {
        origIngest.call(this, e, area);
        if (!e || !e.text) return;
        const text = e.text;
        // Detect failover pattern from router.py logs
        const m = text.match(/\[FAILOVER\]\s+(\w+)\s+→\s+(\w+)|switching.*from\s+(\w+).*to\s+(\w+)/i);
        if (m) {
          const from = m[1] || m[3];
          const to = m[2] || m[4];
          p5ShowFailover(from, to, 'provider unavailable');
        }
        // Track current model from log lines
        const pm = text.match(/\[MODEL\]\s+Using\s+(.+?)(?:\s+\(|$)/i);
        if (pm) {
          const provName = pm[1].toLowerCase().trim();
          // Try to match to known provider
          const matched = p5Providers.find(p =>
            p.label.toLowerCase().includes(provName) || p.id === provName
          );
          if (matched && matched.id !== p5ActiveProvider) {
            p5ActiveProvider = matched.id;
            p5SyncBadge();
            p5RenderProvMenu();
          }
        }
      };
    }
    window.NX_LOAD_TASKS.push( p5MonitorFailover);

    /* -- Show failover status bar --------------------------------------- */
    window.p5ShowFailover = function (from, to, reason) {
      const bar = document.getElementById('p5FailoverBar');
      const fromEl = document.getElementById('p5FbFrom');
      const toEl = document.getElementById('p5FbTo');
      const rsEl = document.getElementById('p5FbReason');
      if (!bar) return;
      if (fromEl) fromEl.textContent = p5ProviderMap[from]?.label || from;
      if (toEl) toEl.textContent = p5ProviderMap[to]?.label || to;
      if (rsEl) rsEl.textContent = reason ? `(${reason})` : '';
      bar.classList.add('visible');
      setTimeout(() => bar.classList.remove('visible'), 12000);
    };

    /* -- Refresh providers every 60s ------------------------------------ */
    setInterval(p5LoadProviders, 60000);

    /* -- Also load after settings open (to populate BYOK categories) --- */
    const _p5OrigOpen = window.openSettings;
    window.openSettings = function (tab) {
      if (_p5OrigOpen) _p5OrigOpen.call(this, tab);
      if (p5Providers.length === 0) p5LoadProviders();
      setTimeout(p5UpdateRoutingInfo, 400);
    };

    console.debug('[Phase 5] BYOK Multi-Provider System active. Providers:', Object.keys({}).length || 'loading…');
  })();

  /* ================================================================
     PHASE 6 — DECISION INTELLIGENCE LAYER
     ================================================================ */
  (function () {
    'use strict';

    /* ── State ──────────────────────────────────────────────────────── */
    let p6Priority = localStorage.getItem('p6_priority') || 'fast';
    let p6LockedProv = localStorage.getItem('p6_locked_prov') || 'auto';
    let p6IntelData = null;   // cached from /api/p6/performance
    let p6RecTimer = null;   // debounce timer for task recommendation
    let p6LastRecProv = null;   // last recommended provider id

    const P6_PRIO_DESC = {
      cheap: 'Optimizes for lowest cost — free and budget providers preferred.',
      fast: 'Optimizes for lowest latency — fastest providers preferred.',
      smart: 'Optimizes for highest quality — strongest models preferred.',
    };
    const P6_COST_TIERS = ['free', 'lowest', 'low', 'medium', 'high'];
    const P6_QUAL_TIERS = ['variable', 'good', 'high', 'highest'];
    const P6_LATENCY_MAX = 3000; // ms, used for bar width scaling


    /* ── Priority Control ───────────────────────────────────────────── */
    window.p6SetPriority = async function (prio, btn) {
      p6Priority = prio;
      localStorage.setItem('p6_priority', prio);
      // Save to backend
      try { await fetch('/api/p6/priority', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ priority: prio }) }); } catch { }
      // Update UI
      document.querySelectorAll('.p6-prio-btn').forEach(b => b.classList.remove('active'));
      if (btn) btn.classList.add('active');
      else {
        const bEl = document.querySelector(`.p6-prio-btn.${prio}`);
        if (bEl) bEl.classList.add('active');
      }
      const labelEl = document.getElementById('p6PrioLabel');
      const descEl = document.getElementById('p6PrioDesc');
      if (labelEl) labelEl.textContent = prio;
      if (descEl) descEl.innerHTML = `Currently using <b>${prio}</b> routing — ${P6_PRIO_DESC[prio]}`;
      // Re-score current task
      const ti = document.getElementById('taskInput');
      if (ti && ti.value.trim().length > 5) p6OnTaskType(ti.value);
    };

    // Restore saved priority
    window.NX_LOAD_TASKS.push( () => {
      setTimeout(() => p6SetPriority(p6Priority, null), 600);
      p6BuildLockRow();
    });


    /* ── Provider Lock Control ──────────────────────────────────────── */
    window.p6LockProvider = function (pid) {
      p6LockedProv = pid;
      localStorage.setItem('p6_locked_prov', pid);
      // Sync with Phase 5 provider selector
      if (typeof p5SelectProvider === 'function') p5SelectProvider(pid, null);
      p6BuildLockRow();
      const desc = document.getElementById('p6LockDesc');
      if (desc) {
        if (pid === 'auto') {
          desc.innerHTML = 'No lock — the router chooses the best provider automatically.';
        } else {
          const label = (typeof p5ProviderMap !== 'undefined' ? p5ProviderMap[pid]?.label : null) || pid;
          desc.innerHTML = `🔒 Locked to <b>${label}</b> — all tasks will use this provider.`;
        }
      }
    };

    function p6BuildLockRow() {
      const row = document.getElementById('p6LockRow');
      if (!row) return;
      // Only build once providers are loaded
      const providers = (typeof p5Providers !== 'undefined') ? p5Providers : [];
      const chatProviders = providers.filter(p => (p.caps || []).includes('coding') || (p.caps || []).includes('thinking'));
      let html = `<button class="p6-lock-btn${p6LockedProv === 'auto' ? ' locked' : ''}" onclick="p6LockProvider('auto')" id="p6Lock_auto">🔓 Auto</button>`;
      chatProviders.forEach(p => {
        const dot = p.available ? '🟢' : '⚫';
        html += `<button class="p6-lock-btn${p6LockedProv === p.id ? ' locked' : ''}" onclick="p6LockProvider('${p.id}')" id="p6Lock_${p.id}">${dot} ${p.label.split(' ')[0]}</button>`;
      });
      row.innerHTML = html;
    }

    // Rebuild lock row once Phase 5 providers are loaded
    window.NX_LOAD_TASKS.push( () => {
      const waitForProviders = setInterval(() => {
        if (typeof p5Providers !== 'undefined' && p5Providers.length > 0) {
          clearInterval(waitForProviders);
          p6BuildLockRow();
        }
      }, 500);
    });


    /* ── Model Intelligence Table ───────────────────────────────────── */
    window.p6LoadIntelPanel = async function () {
      const container = document.getElementById('p6IntelTable');
      if (!container) return;
      container.innerHTML = '<div class="empty" style="padding:8px 0">Loading…</div>';
      try {
        const r = await fetch('/api/p6/performance');
        if (!r.ok) throw new Error('fetch failed');
        const d = await r.json();
        p6IntelData = d.performance;
        const badges = d.badges || {};
        const providers = (typeof p5Providers !== 'undefined') ? p5Providers : [];

        // Build table rows sorted by score (based on current priority)
        const rows = providers.map(p => {
          const perf = d.performance[p.id] || {};
          return { p, perf };
        }).filter(({ p }) => (p.caps || []).some(c => ['coding', 'thinking', 'debugging'].includes(c)));

        // Sort by priority
        const costRank = { free: 0, lowest: 1, low: 2, medium: 3, high: 4 };
        rows.sort((a, b) => {
          if (p6Priority === 'cheap') return costRank[a.perf.cost_tier || 'medium'] - costRank[b.perf.cost_tier || 'medium'];
          if (p6Priority === 'fast') return (a.perf.latency_est || 9999) - (b.perf.latency_est || 9999);
          if (p6Priority === 'smart') {
            const qr = { variable: 0, good: 1, high: 2, highest: 3 };
            return (qr[b.perf.quality_tier || 'good'] || 0) - (qr[a.perf.quality_tier || 'good'] || 0);
          }
          return 0;
        });

        const tbody = rows.map(({ p, perf }, i) => {
          const latMs = perf.avg_latency_ms || perf.latency_est || 1500;
          const barW = Math.max(4, Math.round(60 * Math.min(latMs, P6_LATENCY_MAX) / P6_LATENCY_MAX));
          const barCol = latMs < 400 ? '#3fb950' : latMs < 900 ? '#d29922' : '#f85149';
          const hasData = (perf.calls || 0) > 0;
          const sr = perf.success_rate != null ? `${perf.success_rate}%` : '—';
          const pBadges = (badges[p.id] || []).map(b => `<span class="p6-badge">${b}</span>`).join('');
          const winner = i === 0 ? ' class="p6-compare-winner"' : '';
          const capHtml = (p.caps || []).slice(0, 2).map(c => `<span class="p5-cap-tag ${c}">${c}</span>`).join('');
          const lockBtn = `<button class="p6-lock-btn${p6LockedProv === p.id ? ' locked' : ''}" onclick="p6LockProvider('${p.id}')" title="Lock to this provider">${p6LockedProv === p.id ? '🔒' : '🔓'}</button>`;
          return `<tr${winner}>
                <td style="font-weight:${hasData ? '600' : '400'}">${p.label}${pBadges ? '<br>' + pBadges : ''}</td>
                <td><div class="p6-latency-bar-wrap">
                    <div class="p6-latency-bar" style="width:${barW}px;background:${barCol}"></div>
                    <span style="font-size:0.68rem;color:#8b949e">${hasData ? Math.round(latMs) : latMs + 'ms*'}</span>
                </div></td>
                <td><span class="p6-tier-pill ${perf.cost_tier || 'medium'}">${perf.cost_tier || '?'}</span></td>
                <td><span class="p6-qual-pill ${perf.quality_tier || 'good'}">${perf.quality_tier || '?'}</span></td>
                <td>${capHtml}</td>
                <td style="color:${sr === '—' ? '#8b949e' : '#3fb950'}">${sr}</td>
                <td>${lockBtn}</td>
            </tr>`;
        }).join('');

        container.innerHTML = `<table class="p6-intel-table">
            <thead><tr>
                <th>Provider</th><th>Latency${p6Priority === 'fast' ? ' ★' : ''}</th>
                <th>Cost${p6Priority === 'cheap' ? ' ★' : ''}</th>
                <th>Quality${p6Priority === 'smart' ? ' ★' : ''}</th>
                <th>Capabilities</th><th>Success</th><th>Lock</th>
            </tr></thead>
            <tbody>${tbody}</tbody>
        </table>
        <div style="font-size:0.65rem;color:#8b949e;margin-top:6px">* Static estimate. Run tasks to collect real measurements. ★ = sorted by current priority.</div>`;
      } catch (e) {
        container.innerHTML = '<div class="empty" style="padding:8px 0;color:var(--red)">Failed to load. Check connection.</div>';
      }
    };


    /* ── Performance Badges Panel ───────────────────────────────────── */
    window.p6LoadPerfBadges = async function () {
      const el = document.getElementById('p6PerfBadges');
      if (!el) return;
      try {
        const r = await fetch('/api/p6/performance');
        if (!r.ok) throw new Error();
        const d = await r.json();
        const badges = d.badges || {};
        const perf = d.performance || {};
        // Providers with any calls
        const active = Object.entries(perf).filter(([, p]) => (p.calls || 0) > 0);
        if (!active.length) {
          el.innerHTML = '<div class="empty" style="padding:8px 0">No runtime data yet.</div>';
          return;
        }
        el.innerHTML = active.map(([pid, p]) => {
          const label = (typeof p5ProviderMap !== 'undefined') ? (p5ProviderMap[pid]?.label || pid) : pid;
          const bs = (badges[pid] || []).map(b => `<span class="p6-badge">${b}</span>`).join('');
          return `<div style="display:flex;align-items:center;gap:8px;margin-bottom:6px;padding:5px 0;border-bottom:1px solid #21262d">
                <span style="font-size:0.78rem;color:#e6edf3;font-weight:500;min-width:100px">${label}</span>
                <span style="font-size:0.7rem;color:#8b949e">${p.calls} calls · ${p.success_rate != null ? p.success_rate + '% ok' : '—'} · ${p.avg_latency_ms ? Math.round(p.avg_latency_ms) + 'ms avg' : '—'}</span>
                ${bs}
            </div>`;
        }).join('');
      } catch (e) {
        el.innerHTML = '<div class="empty" style="padding:8px 0">Unable to load.</div>';
      }
    };


    /* ── Smart Task Recommendation Engine ───────────────────────────── */
    let p6LastRec = null;  // full rec response cached

    window.p6OnTaskType = function (text) {
      const rec = document.getElementById('p6InlineRec');
      if (!rec) return;
      if (!text || text.trim().length < 8) {
        rec.classList.remove('visible');
        p6LastRec = null;
        return;
      }
      clearTimeout(p6RecTimer);
      p6RecTimer = setTimeout(() => p6FetchRecommendation(text), 600);
    };

    async function p6FetchRecommendation(text) {
      try {
        const planBadge = document.querySelector('.nx-plan-name, .nx-plan-badge');
        const plan = planBadge ? planBadge.textContent.toLowerCase().trim() : 'pro';
        const r = await fetch('/api/p6/recommend', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ task: text, plan: plan, priority: p6Priority })
        });
        if (!r.ok) return;
        const d = await r.json();
        if (!d.ok || !d.top) return;
        p6LastRec = d;
        const top = d.top;
        p6LastRecProv = top.id;
        // Show inline rec bar
        const rec = document.getElementById('p6InlineRec');
        const prov = document.getElementById('p6IrProv');
        const rsn = document.getElementById('p6IrReason');
        if (rec && prov) {
          prov.textContent = top.label;
          if (rsn) rsn.textContent = top.reasons.length ? '— ' + top.reasons[0] : '';
          rec.classList.add('visible');
        }
      } catch (e) { }
    }

    window.p6ApplyRecommendation = function () {
      if (!p6LastRecProv) return;
      if (typeof p5SelectProvider === 'function') p5SelectProvider(p6LastRecProv, null);
      const rec = document.getElementById('p6InlineRec');
      if (rec) rec.classList.remove('visible');
      if (typeof toast === 'function') toast(`Provider set to ${p6LastRec?.top?.label || p6LastRecProv}`, 'ok');
    };


    /* ── AUTO Mode upgrade — pre-execution recommendation ──────────── */
    // Hook into the run workflow to pick the best provider if locked to AUTO
    const _p6OrigRun = window.nxRunOrStop;
    window.nxRunOrStop = async function () {
      const taskInput = document.getElementById('taskInput');
      if (taskInput && taskInput.value.trim() && p6LockedProv === 'auto' && typeof p5SelectProvider === 'function') {
        // Fetch best provider for this task in background, then run
        try {
          const planBadge = document.querySelector('.nx-plan-name, .nx-plan-badge');
          const plan = planBadge ? planBadge.textContent.toLowerCase().trim() : 'pro';
          const r = await fetch('/api/p6/recommend', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ task: taskInput.value.trim(), plan, priority: p6Priority })
          });
          if (r.ok) {
            const d = await r.json();
            if (d.ok && d.top && d.top.id !== 'local') {
              p5SelectProvider(d.top.id, null);
              console.debug(`[P6 AUTO] Pre-selected ${d.top.label} for task (score: ${d.top.score})`);
            }
          }
        } catch (e) { }
      }
      if (_p6OrigRun) _p6OrigRun.call(this);
    };


    /* ── Performance recording after session completion ─────────────── */
    // Hook into session status updates to record performance data
    const _p6OrigPollSession = window.pollSessionStatus;
    if (_p6OrigPollSession) {
      let _p6SessionStart = {};
      const _p6OrigPollOrig = _p6OrigPollSession;
      window.pollSessionStatus = function (sid) {
        if (!_p6SessionStart[sid]) _p6SessionStart[sid] = Date.now();
        _p6OrigPollOrig.call(this, sid);
      };
    }

    // Monitor session completions to record perf
    window.NX_LOAD_TASKS.push( () => {
      // Watch for status badge changes
      const statusBadge = document.getElementById('nxGlobalStatus');
      if (statusBadge) {
        const obs = new MutationObserver((muts) => {
          for (const m of muts) {
            const text = statusBadge.textContent.trim().toLowerCase();
            if (text === 'done' || text === 'complete' || text === 'success') {
              // Record a success for the active provider
              const activeProv = (typeof p5ActiveProvider !== 'undefined') ? p5ActiveProvider : 'auto';
              if (activeProv && activeProv !== 'auto') {
                fetch('/api/p6/perf/record', {
                  method: 'POST', headers: { 'Content-Type': 'application/json' },
                  body: JSON.stringify({ provider: activeProv, latency_ms: 800, success: true })
                }).catch(() => { });
              }
            } else if (text === 'failed' || text === 'error') {
              const activeProv = (typeof p5ActiveProvider !== 'undefined') ? p5ActiveProvider : 'auto';
              if (activeProv && activeProv !== 'auto') {
                fetch('/api/p6/perf/record', {
                  method: 'POST', headers: { 'Content-Type': 'application/json' },
                  body: JSON.stringify({ provider: activeProv, latency_ms: 0, success: false })
                }).catch(() => { });
              }
            }
          }
        });
        obs.observe(statusBadge, { childList: true, characterData: true, subtree: true });
      }
    });


    /* ── Open Intelligence tab when settings open ───────────────────── */
    const _p6OrigOpen = window.openSettings;
    window.openSettings = function (tab) {
      if (_p6OrigOpen) _p6OrigOpen.call(this, tab);
      if (tab === 'intelligence') {
        setTimeout(() => {
          p6LoadIntelPanel();
          p6LoadPerfBadges();
          p6SetPriority(p6Priority, null);
        }, 200);
      }
    };

    // Also hook into tab switching
    const _p6OrigSwitch = window.switchSettingsTab;
    window.switchSettingsTab = function (tab) {
      if (_p6OrigSwitch) _p6OrigSwitch.call(this, tab);
      if (tab === 'intelligence') {
        setTimeout(() => {
          p6LoadIntelPanel();
          p6LoadPerfBadges();
          p6SetPriority(p6Priority, null);
        }, 150);
      }
    };

    console.debug('[Phase 6] Decision Intelligence Layer active. Priority:', p6Priority);
  })();

  /* ================================================================
     PHASE 7 — STRUCTURED AGENT SYSTEM
     5 specialist agents · plan-gated · smart-triggered · collapsible
     ================================================================ */
  (function () {
    'use strict';

    /* ── State ──────────────────────────────────────────────────────── */
    let p7Agents = [];          // from /api/p7/agents
    let p7Config = null;        // from /api/p7/config
    let p7ActiveSid = null;        // current session id being tracked
    let p7PollTimer = null;        // polling interval handle
    let p7Collapsed = false;       // panel collapsed state
    let p7LastLogText = '';          // cached log text for manual run
    let p7LastTask = '';          // cached task text
    let p7LastPlanMode = 'elite';     // cached plan mode

    const P7_PLAN_LABELS = {
      lite: 'Lite — Agents disabled',
      pro: 'Pro — Optional agents',
      elite: 'Elite — Full pipeline',
    };

    /* ── Utility ────────────────────────────────────────────────────── */
    function p7El(id) { return document.getElementById(id); }

    function p7SetStage(msg, color) {
      const el = p7El('p7StageLabel');
      if (!el) return;
      el.textContent = msg;
      el.style.color = color || '';
    }

    /* ── Collapse ───────────────────────────────────────────────────── */
    window.p7ToggleBody = function () {
      p7Collapsed = !p7Collapsed;
      const body = p7El('p7Body');
      const icon = p7El('p7CollapseIcon');
      if (body) body.style.display = p7Collapsed ? 'none' : '';
      if (icon) icon.textContent = p7Collapsed ? '▾' : '▴';
    };

    /* ── Load agents list from backend ─────────────────────────────── */
    async function p7LoadAgents() {
      try {
        const r = await fetch('/api/p7/agents');
        const d = await r.json();
        if (d.ok) p7Agents = d.agents;
      } catch (e) { }
    }

    /* ── Load user config ───────────────────────────────────────────── */
    async function p7LoadConfig() {
      try {
        const r = await fetch('/api/p7/config');
        const d = await r.json();
        if (d.ok) {
          p7Config = d.config;
          const mt = p7El('p7MasterToggle');
          if (mt) mt.checked = p7Config.enabled !== false;
        }
      } catch (e) { }
    }

    /* ── Save master toggle ─────────────────────────────────────────── */
    window.p7SaveMasterToggle = async function (enabled) {
      try {
        await fetch('/api/p7/config', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ enabled }),
        });
        if (p7Config) p7Config.enabled = enabled;
        p7SetStage(enabled ? 'Agents enabled' : 'Agents disabled');
      } catch (e) { }
    };

    /* ── Save individual agent toggle ───────────────────────────────── */
    window.p7SaveAgentToggle = async function (agentId, enabled) {
      if (!p7Config) return;
      const toggles = Object.assign({}, p7Config.toggles || {}, { [agentId]: enabled });
      try {
        await fetch('/api/p7/config', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ toggles }),
        });
        if (p7Config) p7Config.toggles = toggles;
      } catch (e) { }
    };

    /* ── Render agent list ──────────────────────────────────────────── */
    function p7RenderAgents(pipelineAgents) {
      const list = p7El('p7AgentList');
      if (!list) return;
      // Merge agents from pipeline state (if any) with config
      const source = pipelineAgents || p7Agents.map(a => ({ ...a, status: 'pending' }));
      const toggles = (p7Config && p7Config.toggles) || {};
      list.innerHTML = source.map(ag => {
        const isEnabled = toggles[ag.id] !== false;
        const st = ag.status || 'pending';
        const rowClass = `p7-agent-row p7-${st}`;
        const statusText = {
          pending: 'Waiting…',
          running: 'Running…',
          done: ag.result?.summary || 'Complete',
          skipped: ag.skipped_reason === 'disabled' ? 'Disabled' : 'Not triggered',
          error: ag.error || 'Error',
        }[st] || st;

        const elapsedTag = ag.elapsed ? ` <span style="color:var(--text-dim);font-size:9px">${ag.elapsed}s</span>` : '';

        let resultHtml = '';
        if (st === 'done' && ag.result) {
          resultHtml = p7BuildResultHtml(ag.id, ag.result);
        }

        return `<div class="${rowClass}" id="p7Row-${ag.id}">
  <div class="p7-agent-progress"></div>
  <span class="p7-agent-icon">${ag.icon || '🤖'}</span>
  <div class="p7-agent-body">
    <div class="p7-agent-name">${ag.name}${elapsedTag}</div>
    <div class="p7-agent-status" id="p7Status-${ag.id}">${statusText}</div>
    ${resultHtml ? `<div class="p7-result-card visible" id="p7Result-${ag.id}">${resultHtml}</div>` : ''}
  </div>
  <label class="p7-toggle-switch p7-agent-toggle" title="Toggle this agent">
    <input type="checkbox" ${isEnabled ? 'checked' : ''}
      onchange="p7SaveAgentToggle('${ag.id}', this.checked)">
    <span class="p7-toggle-track"></span>
  </label>
</div>`;
      }).join('');
    }

    /* ── Build result HTML per agent type ───────────────────────────── */
    function p7BuildResultHtml(agentId, result) {
      if (!result) return '';
      const summary = `<div class="p7-result-summary">${escHtml(result.summary || '')}</div>`;

      if (agentId === 'reviewer') {
        const grade = result.grade || '?';
        const color = grade === 'A' ? '#4caf50' : grade === 'B' ? '#6c8ebf' : grade === 'C' ? '#d6a94a' : '#bf4c4c';
        const findings = (result.findings || []).slice(0, 3).map(f =>
          `<div class="p7-result-item"><span class="p7-sev-medium">WARN</span><span>${escHtml(f.message)}</span></div>`).join('');
        const positive = (result.positive || []).slice(0, 2).map(p =>
          `<div class="p7-result-item"><span class="p7-sev-ok">✓</span><span>${escHtml(p)}</span></div>`).join('');
        return `${summary}<div style="text-align:center;margin:4px 0">
          <span style="font-size:22px;font-weight:700;color:${color}">${grade}</span>
          <span style="font-size:10px;color:var(--text-dim)"> / ${result.score}/100</span>
        </div>${findings}${positive}`;
      }
      if (agentId === 'debugger') {
        const errs = (result.errors || []).slice(0, 3).map(e =>
          `<div class="p7-result-item"><span class="p7-sev-high">ERR</span><span>${escHtml(e.explanation)}</span></div>`).join('');
        const fixes = (result.fixes || []).slice(0, 2).map(f =>
          `<div class="p7-result-item"><span class="p7-sev-ok">FIX</span><span>${escHtml(f)}</span></div>`).join('');
        return `${summary}${errs}${fixes}`;
      }
      if (agentId === 'tester') {
        const tests = (result.tests || []).slice(0, 3).map(t =>
          `<div class="p7-result-item"><span class="p7-sev-low">TEST</span><span>${escHtml(t.suggestion)}</span></div>`).join('');
        return `${summary}${tests}`;
      }
      if (agentId === 'security') {
        const riskColor = result.risk_level === 'Clean' ? '#4caf50' : result.risk_level === 'Low' ? '#6c8ebf' : result.risk_level === 'Medium' ? '#d6a94a' : '#bf4c4c';
        const findings = (result.findings || []).slice(0, 4).map(f => {
          const sev = f.severity === 'HIGH' ? 'p7-sev-high' : f.severity === 'MEDIUM' ? 'p7-sev-medium' : 'p7-sev-low';
          return `<div class="p7-result-item"><span class="${sev}">${f.severity}</span><span>${escHtml(f.message)}</span></div>`;
        }).join('');
        return `${summary}<div style="font-size:11px;font-weight:700;color:${riskColor};margin-bottom:4px">Risk: ${result.risk_level}</div>${findings}`;
      }
      if (agentId === 'optimizer') {
        const suggs = (result.suggestions || []).slice(0, 3).map(s =>
          `<div class="p7-result-item"><span class="p7-sev-low">OPT</span><span>${escHtml(s.suggestion)}</span></div>`).join('');
        return `${summary}${suggs}`;
      }
      return summary;
    }

    function escHtml(s) {
      return String(s || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
    }

    /* ── Poll pipeline status ───────────────────────────────────────── */
    async function p7PollStatus(sid) {
      try {
        const r = await fetch(`/api/p7/pipeline/status/${sid}`);
        if (!r.ok) return;
        const d = await r.json();
        if (!d.ok) return;
        const state = d.state;
        p7RenderAgents(state.agents);
        const doneCount = state.agents.filter(a => a.status === 'done').length;
        const runningCount = state.agents.filter(a => a.status === 'running').length;
        const total = state.agents.filter(a => a.status !== 'skipped').length;

        if (state.stage === 'running') {
          p7SetStage(`Pipeline running — ${doneCount}/${total} complete`, 'var(--accent)');
        } else if (state.stage === 'done') {
          const elapsed = state.finished_at && state.created_at
            ? `${(state.finished_at - state.created_at).toFixed(1)}s`
            : '';
          p7SetStage(`Pipeline complete ${elapsed ? `in ${elapsed}` : ''}`, 'var(--green, #4caf50)');
          clearInterval(p7PollTimer);
          p7PollTimer = null;
          const btn = p7El('p7RunBtn');
          if (btn) { btn.disabled = false; btn.textContent = '↺ Re-run Pipeline'; }
        }
      } catch (e) { }
    }

    /* ── Start polling ──────────────────────────────────────────────── */
    function p7StartPolling(sid) {
      if (p7PollTimer) clearInterval(p7PollTimer);
      p7PollTimer = setInterval(() => p7PollStatus(sid), 800);
      // First poll immediately
      p7PollStatus(sid);
    }

    /* ── Trigger pipeline (called after session completes) ──────────── */
    async function p7TriggerPipeline(sid, task, planMode, logText) {
      if (!sid || planMode === 'lite') return;
      const mt = p7El('p7MasterToggle');
      if (mt && !mt.checked) return;
      p7ActiveSid = sid;
      p7LastTask = task;
      p7LastPlanMode = planMode;
      p7LastLogText = logText;

      const btn = p7El('p7RunBtn');
      if (btn) { btn.disabled = true; btn.textContent = '⏳ Running…'; }
      p7SetStage('Starting pipeline…', 'var(--accent)');

      try {
        const r = await fetch('/api/p7/pipeline/run', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ sid, task, plan_mode: planMode, log_text: logText }),
        });
        const d = await r.json();
        if (d.ok) {
          p7RenderAgents(d.state.agents);
          p7StartPolling(sid);
        } else {
          p7SetStage(d.error || 'Pipeline unavailable');
          if (btn) { btn.disabled = false; btn.textContent = '▶ Run Agent Pipeline'; }
        }
      } catch (e) {
        p7SetStage('Pipeline error — check console');
        if (btn) { btn.disabled = false; btn.textContent = '▶ Run Agent Pipeline'; }
      }
    }

    /* ── Manual run button ──────────────────────────────────────────── */
    window.p7ManualRun = function () {
      if (!p7ActiveSid) {
        p7SetStage('No session available — run a task first');
        return;
      }
      p7TriggerPipeline(p7ActiveSid, p7LastTask, p7LastPlanMode, p7LastLogText);
    };

    /* ── Update panel for plan mode ─────────────────────────────────── */
    function p7UpdateForPlan(planMode) {
      p7LastPlanMode = planMode;
      const label = p7El('p7PlanLabel');
      if (label) label.textContent = P7_PLAN_LABELS[planMode] || planMode;
      const btn = p7El('p7RunBtn');
      if (btn) {
        if (planMode === 'lite') {
          btn.disabled = true;
          btn.title = 'Agents require Pro or Elite plan';
        } else if (p7ActiveSid) {
          btn.disabled = false;
        }
      }
    }

    /* ── Hook into session completion ───────────────────────────────── */
    // Watch status badge for completion, then grab logs + trigger pipeline
    function p7MonitorSessions() {
      const statusBadge = document.getElementById('nxGlobalStatus') ||
        document.getElementById('stStatus');
      if (!statusBadge) return;

      const obs = new MutationObserver(async () => {
        const text = statusBadge.textContent.trim().toLowerCase();
        if (text !== 'done' && text !== 'complete' && text !== 'success') return;

        // Get active session id
        const sid = (typeof currentSessionId !== 'undefined' && currentSessionId) ||
          (typeof nxCurrentSid !== 'undefined' && nxCurrentSid);
        if (!sid || sid === p7ActiveSid) return;  // avoid double-trigger

        // Get plan mode
        const planBadge = document.getElementById('nxPlanBadge') ||
          document.getElementById('planModeSelect') ||
          document.querySelector('[data-plan]');
        const planMode = (planBadge?.dataset?.plan || planBadge?.value ||
          localStorage.getItem('p4_prefs') && JSON.parse(localStorage.getItem('p4_prefs') || '{}').plan_mode ||
          'elite').toLowerCase();

        if (planMode === 'lite') return;

        // Grab log text from the log area
        const logArea = document.getElementById('logArea') ||
          document.getElementById('nxLogs') ||
          document.querySelector('.nx-log-area');
        const logText = logArea ? logArea.innerText || logArea.textContent : '';

        // Get task text
        const taskInput = document.getElementById('taskInput') ||
          document.querySelector('textarea[name="task"]');
        const task = taskInput ? taskInput.value : '';

        p7TriggerPipeline(sid, task, planMode, logText);
      });
      obs.observe(statusBadge, { childList: true, characterData: true, subtree: true });
    }

    /* ── Hook into plan mode selector ───────────────────────────────── */
    function p7WatchPlanMode() {
      // Try to watch a plan selector if present
      const possibleSelectors = ['planModeSelect', 'nxPlanSelect', 'p3PlanMode'];
      for (const id of possibleSelectors) {
        const el = document.getElementById(id);
        if (el) {
          el.addEventListener('change', () => p7UpdateForPlan(el.value));
        }
      }
      // Also watch localStorage-based plan changes via custom event
      window.addEventListener('p7PlanChanged', e => {
        if (e.detail?.plan) p7UpdateForPlan(e.detail.plan);
      });
    }

    /* ── Session hook: enable Run button when a session becomes active ─ */
    function p7WatchSessionSelect() {
      // Override selectSession if it exists
      const origSelect = window.selectSession;
      window.selectSession = function (sid, ...rest) {
        if (origSelect) origSelect.call(this, sid, ...rest);
        if (sid) {
          p7ActiveSid = sid;
          const btn = p7El('p7RunBtn');
          const mt = p7El('p7MasterToggle');
          if (btn && mt?.checked && p7LastPlanMode !== 'lite') {
            btn.disabled = false;
            btn.textContent = '▶ Run Agent Pipeline';
          }
        }
      };
    }

    /* ── Expose trigger for external hooks ──────────────────────────── */
    window.p7TriggerPipeline = p7TriggerPipeline;
    window.p7UpdateForPlan = p7UpdateForPlan;

    /* ── Initialise ─────────────────────────────────────────────────── */
    window.NX_LOAD_TASKS.push( async function () {
      await Promise.all([p7LoadAgents(), p7LoadConfig()]);
      p7RenderAgents(null);  // render skeleton with pending states
      p7UpdateForPlan(p7LastPlanMode);
      p7MonitorSessions();
      p7WatchPlanMode();
      p7WatchSessionSelect();
      console.debug('[Phase 7] Structured Agent System active. Agents:', p7Agents.map(a => a.id));
    });

  })();

  /* ================================================================
     PHASE 8 — MONETIZATION & ACCESS CONTROL UI
     Subscription badge · Upgrade modal · Usage indicators · Coupons
     ================================================================ */
  (function () {
    'use strict';

    /* ── State ──────────────────────────────────────────────────────── */
    let p8State = null;   // from /api/plan/info
    let p8PollTimer = null;

    const P8_COLORS = { free: '#4caf50', pro: '#388bfd', elite: '#bc8cff' };
    const P8_ICONS = { free: '🆓', pro: '⭐', elite: '👑' };

    /* ── Utility ────────────────────────────────────────────────────── */
    function p8El(id) { return document.getElementById(id); }
    function p8Esc(s) { return String(s || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;'); }

    /* ── Load plan info ─────────────────────────────────────────────── */
    async function p8Load() {
      try {
        const r = await fetch('/api/plan/info');
        const d = await r.json();
        if (!d.ok) return;
        p8State = d;
        p8UpdateBadge(d);
        p8UpdateInspectorUsage(d);
        p8UpdateSettingsPanel(d);
        p36UpdateInspectorBilling(d);
      } catch (e) { }
    }

    function p36UpdateInspectorBilling(d) {
      const wrap = p8El('p36InspBilling');
      const cont = p8El('p36InspBillingContent');
      if (!wrap || !cont) return;
      const plan = d.plan || 'free';
      if (plan === 'free') {
        wrap.style.display = 'block';
        cont.innerHTML = `<span class="p36-sub-chip free">Free</span> <a onclick="p8OpenUpgradeModal()" style="cursor:pointer;color:#bc8cff;font-size:10px;margin-left:4px">Upgrade ↗</a>`;
      } else {
        wrap.style.display = 'block';
        const planName = d.meta?.name || plan;
        const expiry = d.expires ? d.expires : 'Lifetime';
        const chipCls = 'active';
        cont.innerHTML = `<div><span class="p36-sub-chip ${chipCls}">${p8Esc(planName)}</span></div><div style="margin-top:3px;color:var(--text-muted)">Expires: ${p8Esc(expiry)}</div><div style="margin-top:3px"><a onclick="p8OpenUpgradeModal()" style="cursor:pointer;color:#bc8cff;font-size:10px">Manage ↗</a></div>`;
      }
    }

    /* ── Update header badge ─────────────────────────────────────────── */
    function p8UpdateBadge(d) {
      const badge = p8El('p8SubBadge');
      const icon = p8El('p8SubIcon');
      const name = p8El('p8SubName');
      const expEl = p8El('p8SubExpiry');
      if (!badge) return;
      const plan = d.plan;
      badge.className = `p8-sub-badge p8-${plan}`;
      if (icon) icon.textContent = P8_ICONS[plan] || '';
      if (name) name.textContent = d.meta?.name || plan.charAt(0).toUpperCase() + plan.slice(1);
      if (expEl) {
        if (d.expires && plan !== 'free') {
          expEl.style.display = 'inline';
          expEl.textContent = `· ${d.expires}`;
        } else {
          expEl.style.display = 'none';
        }
      }
      if (d.expires) {
        badge.title = `${d.meta?.name} plan — expires ${d.expires}. Click to manage.`;
      } else {
        badge.title = `${d.meta?.name} plan. Click to upgrade.`;
      }
    }

    /* ── Inspector usage mini-block ─────────────────────────────────── */
    function p8UpdateInspectorUsage(d) {
      const wrap = p8El('p8InspUsage');
      const content = p8El('p8InspUsageContent');
      if (!wrap || !content) return;

      const plan = d.plan;
      const usage = d.usage || {};
      const pd = usage.pro_daily || {};
      const em = usage.elite_monthly || {};
      const pdLim = pd.limit;
      const emLim = em.limit;

      // Only show if there are finite limits
      if (pdLim === null && emLim === null && plan !== 'free') {
        wrap.style.display = 'none';
        return;
      }
      wrap.style.display = '';

      let html = '';

      if (pdLim !== null && pdLim !== undefined) {
        const pct = Math.min(100, Math.round((pd.count || 0) / pdLim * 100));
        const cls = pct >= 100 ? 'p8-usage-full' : pct >= 80 ? 'p8-usage-warn' : 'p8-usage-ok';
        html += `<div class="p8-usage-row">
            <span class="p8-usage-label">Pro runs today</span>
            <span class="p8-usage-val">${pd.count || 0}/${pdLim}</span>
        </div>
        <div class="p8-usage-bar-wrap"><div class="p8-usage-bar-fill ${cls}" style="width:${pct}%"></div></div>`;
      } else if (plan !== 'free' && pdLim === null) {
        html += `<div class="p8-usage-row">
            <span class="p8-usage-label">Pro runs</span>
            <span class="p8-usage-val" style="color:#4caf50">∞ Unlimited</span>
        </div>`;
      }

      if (emLim === 0) {
        html += `<div class="p8-usage-row" style="margin-top:4px">
            <span class="p8-usage-label">Elite runs</span>
            <span class="p8-usage-val" style="color:#bf4c4c">🔒 Locked</span>
        </div>`;
      } else if (emLim !== null && emLim !== undefined) {
        const pct = Math.min(100, Math.round((em.count || 0) / emLim * 100));
        const cls = pct >= 100 ? 'p8-usage-full' : pct >= 80 ? 'p8-usage-warn' : 'p8-usage-ok';
        html += `<div class="p8-usage-row" style="margin-top:4px">
            <span class="p8-usage-label">Elite / month</span>
            <span class="p8-usage-val">${em.count || 0}/${emLim}</span>
        </div>
        <div class="p8-usage-bar-wrap"><div class="p8-usage-bar-fill ${cls}" style="width:${pct}%"></div></div>`;
      } else if (plan === 'elite' && emLim === null) {
        html += `<div class="p8-usage-row" style="margin-top:4px">
            <span class="p8-usage-label">Elite runs</span>
            <span class="p8-usage-val" style="color:#4caf50">∞ Unlimited</span>
        </div>`;
      }

      content.innerHTML = html;
    }

    /* ── Settings Plan tab ──────────────────────────────────────────── */
    function p8UpdateSettingsPanel(d) {
      const badge = p8El('p8SettingsPlanBadge');
      const expiry = p8El('p8SettingsPlanExpiry');
      const usage = p8El('p8SettingsUsage');
      const bpTog = p8El('p8ByokPriorityToggle');

      if (badge) {
        badge.textContent = `${P8_ICONS[d.plan] || ''} ${d.meta?.name || d.plan} — ${d.meta?.price || ''}`;
        badge.style.color = P8_COLORS[d.plan] || 'var(--accent)';
      }
      if (expiry) {
        expiry.textContent = d.expires ? `Expires: ${d.expires}` :
          d.coupon ? `Active coupon: ${d.coupon}` : '';
      }
      if (bpTog) {
        bpTog.checked = !!d.byok_priority;
        // Disable BYOK Priority toggle for free plan
        bpTog.disabled = d.plan === 'free';
        bpTog.parentElement.style.opacity = d.plan === 'free' ? '0.5' : '1';
      }
      if (usage) {
        const pd = d.usage?.pro_daily || {};
        const em = d.usage?.elite_monthly || {};
        const pdL = pd.limit;
        const emL = em.limit;
        let html = '';
        html += `<div class="p8-usage-row"><span class="p8-usage-label">Pro runs today</span>
            <span class="p8-usage-val">${pdL === null ? '∞ Unlimited' : `${pd.count || 0} / ${pdL}`}</span></div>`;
        if (pdL !== null && pdL !== undefined) {
          const pct = Math.min(100, Math.round((pd.count || 0) / pdL * 100));
          const cls = pct >= 100 ? 'p8-usage-full' : pct >= 80 ? 'p8-usage-warn' : 'p8-usage-ok';
          html += `<div class="p8-usage-bar-wrap"><div class="p8-usage-bar-fill ${cls}" style="width:${pct}%"></div></div>`;
        }
        html += `<div class="p8-usage-row" style="margin-top:8px"><span class="p8-usage-label">Elite runs this month</span>
            <span class="p8-usage-val">${emL === 0 ? '🔒 Locked' : emL === null ? '∞ Unlimited' : `${em.count || 0} / ${emL}`}</span></div>`;
        if (emL && emL !== null) {
          const pct = Math.min(100, Math.round((em.count || 0) / emL * 100));
          const cls = pct >= 100 ? 'p8-usage-full' : pct >= 80 ? 'p8-usage-warn' : 'p8-usage-ok';
          html += `<div class="p8-usage-bar-wrap"><div class="p8-usage-bar-fill ${cls}" style="width:${pct}%"></div></div>`;
        }
        usage.innerHTML = html;
      }
    }

    /* ── Open upgrade modal ─────────────────────────────────────────── */
    window.p8OpenUpgradeModal = async function () {
      const modal = p8El('p8UpgradeModal');
      if (!modal) return;
      modal.classList.add('open');
      p8El('p8CouponMsg') && (p8El('p8CouponMsg').textContent = '');
      p8El('p8CouponInput') && (p8El('p8CouponInput').value = '');
      p36ShowPayMsg('', true);
      await p8Load();
      await p36LoadBillingPlans();
      p8RenderPlanCards();
      p8RenderModalUsage();
      await p36LoadBillingInfo();
    };

    window.p8CloseUpgradeModal = function (e) {
      if (e && e.target !== p8El('p8UpgradeModal')) return;
      const modal = p8El('p8UpgradeModal');
      if (modal) modal.classList.remove('open');
    };

    /* ── Render plan cards in modal ──────────────────────────────────── */
    /* ── Phase 36: Billing cycle state ────────────────────────────────── */
    let p36Cycle = 'monthly';
    let p36Plans = {};   // from /api/payments/plans
    let p36RazKey = '';

    const P36_PRICING = {
      pro: { monthly: '₹20/month', yearly: '₹200/year' },
      elite: { monthly: '₹50/month', yearly: '₹500/year' },
      free: { monthly: 'Free', yearly: 'Free' },
    };

    window.p36SetCycle = function (cycle) {
      p36Cycle = cycle;
      ['monthly', 'yearly'].forEach(c => {
        const el = p8El('p36Cycle' + c.charAt(0).toUpperCase() + c.slice(1));
        if (el) el.classList.toggle('active', c === cycle);
      });
      p8RenderPlanCards();
    };

    async function p36LoadBillingPlans() {
      try {
        const r = await fetch('/api/payments/plans');
        const d = await r.json();
        if (d.ok) {
          p36Plans = d.plans || {};
          p36RazKey = d.razorpay_key_id || '';
        }
      } catch (e) { }
    }

    async function p36LoadBillingInfo() {
      try {
        const r = await fetch('/api/billing/info');
        const d = await r.json();
        if (!d.ok) return;
        p36RenderSubStatus(d.subscription);
        p36RenderInvoices(d.invoices || []);
        p36RenderWebhookStatus(d.razorpay_enabled);
      } catch (e) { }
    }

    function p36RenderWebhookStatus(enabled) {
      const statusEl = p8El('p36WebhookStatus');
      const urlEl = p8El('p36WebhookUrl');
      const guide = p8El('p36SetupGuide');

      const webhookUrl = `${location.protocol}//${location.host}/api/payments/webhook`;
      if (urlEl) urlEl.textContent = webhookUrl;

      if (statusEl) {
        if (enabled) {
          statusEl.textContent = '● Connected';
          statusEl.style.background = 'rgba(63,185,80,.12)';
          statusEl.style.color = '#3fb950';
          statusEl.style.border = '1px solid rgba(63,185,80,.3)';
          if (guide) guide.style.borderColor = 'rgba(63,185,80,.2)';
        } else {
          statusEl.textContent = '○ Not Connected';
          statusEl.style.background = 'rgba(248,81,73,.1)';
          statusEl.style.color = '#f85149';
          statusEl.style.border = '1px solid rgba(248,81,73,.25)';
        }
      }
    }

    function p36RenderSubStatus(sub) {
      const wrap = p8El('p36SubStatus');
      const cont = p8El('p36SubStatusContent');
      if (!wrap || !cont) return;
      if (!sub || sub.status !== 'active') { wrap.style.display = 'none'; return; }
      wrap.style.display = 'block';
      const expiry = sub.expiry_date ? sub.expiry_date.slice(0, 10) : '—';
      const planName = sub.plan ? (sub.plan.charAt(0).toUpperCase() + sub.plan.slice(1)) : '—';
      cont.innerHTML = `
        <div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap">
          <span class="p36-sub-chip active">● ACTIVE</span>
          <span style="font-size:12px;font-weight:700;color:var(--text)">${p8Esc(planName)} · ${p8Esc(sub.billing_cycle || 'monthly')}</span>
          <span style="font-size:11px;color:var(--text-muted)">Expires: ${p8Esc(expiry)}</span>
        </div>`;
    }

    function p36RenderInvoices(invoices) {
      const el = p8El('p36InvoiceList');
      if (!el) return;
      if (!invoices || invoices.length === 0) {
        el.innerHTML = '<span style="font-size:12px;color:var(--text-muted)">No invoices yet.</span>';
        return;
      }
      el.innerHTML = invoices.map(inv => {
        const plan = inv.plan ? (inv.plan.charAt(0).toUpperCase() + inv.plan.slice(1)) : '—';
        const date = inv.issued_at ? inv.issued_at.slice(0, 10) : '—';
        const amt = inv.amount ? `₹${Math.round(inv.amount / 100)}` : '—';
        return `<div class="p36-inv-row">
          <div>
            <div class="p36-inv-plan">${p8Esc(plan)} · ${p8Esc(amt)}</div>
            <div class="p36-inv-date">${p8Esc(date)}</div>
          </div>
          <a class="p36-inv-dl" href="/api/invoice/${p8Esc(inv.id)}" target="_blank" title="Download Invoice">⬇ Download</a>
        </div>`;
      }).join('');
    }

    function p36ShowPayMsg(msg, ok) {
      const el = p8El('p36PayMsg');
      if (!el) return;
      el.style.display = msg ? 'block' : 'none';
      el.style.background = ok ? 'rgba(63,185,80,.15)' : 'rgba(248,81,73,.15)';
      el.style.color = ok ? '#3fb950' : '#f85149';
      el.style.border = `1px solid ${ok ? 'rgba(63,185,80,.3)' : 'rgba(248,81,73,.3)'}`;
      el.textContent = msg;
    }

    /* ── Phase 36: Real Razorpay payment ─────────────────────────────── */
    window.p36StartPayment = async function (plan, cycle) {
      if (!p36RazKey) {
        p36ShowPayMsg('⚠ Payment gateway not configured yet. Add RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET in Secrets.', false);
        return;
      }
      p36ShowPayMsg('Creating order…', null);
      let order;
      try {
        const r = await fetch('/api/payments/create-order', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ plan, billing_cycle: cycle }),
        });
        order = await r.json();
        if (!order.ok) {
          p36ShowPayMsg(`⚠ ${order.error || 'Order creation failed'}`, false);
          return;
        }
      } catch (e) {
        p36ShowPayMsg('⚠ Network error. Try again.', false);
        return;
      }

      p36ShowPayMsg('Opening payment…', null);

      const options = {
        key: p36RazKey,
        amount: order.amount,
        currency: order.currency,
        name: 'Aetherion AI Platform',
        description: `${order.plan_name} Plan (${order.billing_cycle})`,
        order_id: order.razorpay_order_id,
        theme: { color: '#bc8cff' },
        handler: async function (resp) {
          p36ShowPayMsg('Verifying payment…', null);
          try {
            const vr = await fetch('/api/payments/verify', {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({
                razorpay_order_id: resp.razorpay_order_id,
                razorpay_payment_id: resp.razorpay_payment_id,
                razorpay_signature: resp.razorpay_signature,
                plan,
                billing_cycle: cycle,
                amount: order.amount,
              }),
            });
            const vd = await vr.json();
            if (vd.ok) {
              p36ShowPayMsg(`✓ ${vd.message || 'Plan activated!'}`, true);
              await p8Load();
              p8RenderPlanCards();
              p8RenderModalUsage();
              await p36LoadBillingInfo();
              setTimeout(() => p36ShowPayMsg('', true), 5000);
            } else {
              p36ShowPayMsg(`⚠ Verification failed: ${vd.error}`, false);
            }
          } catch (e) {
            p36ShowPayMsg('⚠ Verification error. Contact support.', false);
          }
        },
        modal: {
          ondismiss: () => p36ShowPayMsg('', true),
        },
      };

      if (window.Razorpay) {
        const rzp = new window.Razorpay(options);
        rzp.on('payment.failed', e => p36ShowPayMsg(`⚠ Payment failed: ${e.error?.description || 'Unknown error'}`, false));
        rzp.open();
      } else {
        p36ShowPayMsg('⚠ Razorpay checkout not loaded. Check your internet connection.', false);
      }
    };

    function p8RenderPlanCards() {
      if (!p8State) return;
      const grid = p8El('p8PlansGrid');
      if (!grid) return;
      const current = p8State.plan;
      const plans = p8State.all_plans || {};
      const order = ['free', 'pro', 'elite'];

      grid.innerHTML = order.map(pid => {
        const p = plans[pid] || {};
        const isActive = pid === current;
        const color = P8_COLORS[pid] || '#888';
        const features = (p.features || []).map(f =>
          `<li>${p8Esc(f)}</li>`).join('');
        const activeCls = isActive ? 'p8-active' : '';

        // INR pricing for paid plans
        const priceLine = pid === 'free'
          ? p8Esc(p.price || '$0 / month')
          : (P36_PRICING[pid]?.[p36Cycle] || p8Esc(p.price || ''));

        let btnHtml;
        if (isActive) {
          btnHtml = `<button class="p8-plan-btn p8-current" style="color:${color};border-color:${color}" disabled>Current Plan</button>`;
        } else if (pid === 'free') {
          btnHtml = `<button class="p8-plan-btn" style="color:${color};border-color:${color};background:${color}1a" onclick="p8SelectPlan('free')">Downgrade to Free</button>`;
        } else {
          btnHtml = `<button class="p36-pay-btn" style="background:${color};color:#fff" onclick="p36StartPayment('${pid}','${p36Cycle}')">💳 Pay ${P36_PRICING[pid]?.[p36Cycle] || ''}</button>`;
        }

        return `<div class="p8-plan-card ${activeCls}" style="border-color:${isActive ? color : ''};${isActive ? `box-shadow:0 0 0 1px ${color}33` : ''}" >
            <div class="p8-plan-icon">${p.icon || ''}</div>
            <div class="p8-plan-name" style="color:${color}">${p8Esc(p.name || pid)}</div>
            <div class="p8-plan-price" style="color:${color}">${priceLine}</div>
            <ul class="p8-plan-features">${features}</ul>
            ${btnHtml}
        </div>`;
      }).join('');
    }

    function p8RenderModalUsage() {
      if (!p8State) return;
      const el = p8El('p8ModalUsage');
      if (!el) return;
      const d = p8State;
      const pd = d.usage?.pro_daily || {};
      const em = d.usage?.elite_monthly || {};
      const pdL = pd.limit;
      const emL = em.limit;
      el.innerHTML = `<div style="display:grid;grid-template-columns:1fr 1fr;gap:10px">
        <div style="background:var(--panel);border-radius:8px;padding:10px;border:1px solid var(--panel-border)">
            <div style="font-size:10px;color:var(--text-dim);margin-bottom:4px">PRO RUNS TODAY</div>
            <div style="font-size:18px;font-weight:800;color:#388bfd">
                ${pdL === null ? '∞' : `${pd.count || 0}<span style="font-size:12px;color:var(--text-dim)">/${pdL}</span>`}
            </div>
            ${pdL !== null && pdL !== undefined ? `<div class="p8-usage-bar-wrap" style="margin-top:4px"><div class="p8-usage-bar-fill p8-usage-ok" style="width:${Math.min(100, Math.round((pd.count || 0) / pdL * 100))}%"></div></div>` : ''}
        </div>
        <div style="background:var(--panel);border-radius:8px;padding:10px;border:1px solid var(--panel-border)">
            <div style="font-size:10px;color:var(--text-dim);margin-bottom:4px">ELITE RUNS THIS MONTH</div>
            <div style="font-size:18px;font-weight:800;color:#bc8cff">
                ${emL === 0 ? '🔒' : emL === null ? '∞' : `${em.count || 0}<span style="font-size:12px;color:var(--text-dim)">/${emL}</span>`}
            </div>
            ${emL && emL !== null ? `<div class="p8-usage-bar-wrap" style="margin-top:4px"><div class="p8-usage-bar-fill p8-usage-ok" style="width:${Math.min(100, Math.round((em.count || 0) / emL * 100))}%"></div></div>` : ''}
        </div>
    </div>`;
    }

    /* ── Select plan (demo — no payment) ─────────────────────────────── */
    window.p8SelectPlan = async function (plan) {
      try {
        const r = await fetch('/api/plan/set', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ plan }),
        });
        const d = await r.json();
        if (d.ok) {
          await p8Load();
          p8RenderPlanCards();
          p8RenderModalUsage();
          p8ShowCouponMsg(p8El('p8CouponMsg'),
            `✓ Switched to ${d.meta?.name} plan`, true);
          setTimeout(() => {
            const modal = p8El('p8UpgradeModal');
            if (modal) modal.classList.remove('open');
          }, 1200);
        }
      } catch (e) { }
    };

    /* ── Apply coupon (modal) ─────────────────────────────────────────── */
    window.p8ApplyCoupon = async function () {
      const input = p8El('p8CouponInput');
      const msgEl = p8El('p8CouponMsg');
      if (!input) return;
      const code = input.value.trim().toUpperCase();
      if (!code) { p8ShowCouponMsg(msgEl, 'Enter a coupon code first', false); return; }
      await p8SendCoupon(code, msgEl, input);
    };

    /* ── Apply coupon (settings tab) ─────────────────────────────────── */
    window.p8ApplyCouponSettings = async function () {
      const input = p8El('p8SettingsCouponInput');
      const msgEl = p8El('p8SettingsCouponMsg');
      if (!input) return;
      const code = input.value.trim().toUpperCase();
      if (!code) { p8ShowCouponMsg(msgEl, 'Enter a coupon code first', false); return; }
      await p8SendCoupon(code, msgEl, input);
    };

    async function p8SendCoupon(code, msgEl, inputEl) {
      p8ShowCouponMsg(msgEl, 'Checking…', null);
      try {
        const r = await fetch('/api/plan/apply-coupon', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ code }),
        });
        const d = await r.json();
        if (d.ok) {
          p8ShowCouponMsg(msgEl,
            `✓ ${d.note} unlocked! ${d.expires ? `Expires ${d.expires}` : 'Lifetime access'}`, true);
          if (inputEl) inputEl.value = '';
          await p8Load();
          p8RenderPlanCards();
          p8RenderModalUsage();
        } else {
          p8ShowCouponMsg(msgEl, `✗ ${d.error}`, false);
        }
      } catch (e) {
        p8ShowCouponMsg(msgEl, 'Error applying coupon — try again', false);
      }
    }

    function p8ShowCouponMsg(el, msg, ok) {
      if (!el) return;
      el.textContent = msg;
      el.className = `p8-coupon-msg ${ok === true ? 'p8-coupon-ok' : ok === false ? 'p8-coupon-err' : ''}`;
    }

    /* ── BYOK Priority toggle ─────────────────────────────────────────── */
    window.p8ToggleByokPriority = async function (enabled) {
      try {
        const r = await fetch('/api/plan/byok-priority', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ enabled }),
        });
        const d = await r.json();
        if (!d.ok) {
          const tog = p8El('p8ByokPriorityToggle');
          if (tog) tog.checked = !enabled; // revert
        } else {
          if (p8State) p8State.byok_priority = d.byok_priority;
        }
      } catch (e) { }
    };

    /* ── Hook into task submission to show gate errors ──────────────── */
    // Intercept the response from /api/queue-task and show upgrade prompt
    const _p8OrigQueueTask = window.queueTask || window.nxRunOrStop;
    document.addEventListener('p8PlanGate', function (e) {
      const reason = e.detail?.reason || 'Upgrade required';
      // Show a nice gate notification
      const errBanner = document.getElementById('p57-error-banner');
      const errMsg = document.getElementById('p57-error-msg');
      if (errBanner && errMsg) {
        errMsg.textContent = reason;
        errBanner.style.display = 'flex';
        // Replace "Fix with AI" with "Upgrade Plan"
        const fixBtn = errBanner.querySelector('button');
        if (fixBtn) {
          const orig = fixBtn.textContent;
          fixBtn.textContent = '🚀 Upgrade Plan';
          fixBtn.onclick = () => { p8OpenUpgradeModal(); errBanner.style.display = 'none'; };
          setTimeout(() => {
            fixBtn.textContent = orig;
            fixBtn.onclick = () => p57FixError?.();
          }, 8000);
        }
      }
    });

    /* ── Hook settings tab open ──────────────────────────────────────── */
    const _p8OrigSwitch = window.switchSettingsTab;
    window.switchSettingsTab = function (tab) {
      if (_p8OrigSwitch) _p8OrigSwitch.call(this, tab);
      if (tab === 'plan') {
        p8Load(); // Refresh when opening plan tab
      }
    };

    /* ── Poll usage every 60s ────────────────────────────────────────── */
    function p8StartPoll() {
      if (p8PollTimer) clearInterval(p8PollTimer);
      p8PollTimer = setInterval(p8Load, 60000);
    }

    /* ── Initialise ──────────────────────────────────────────────────── */
    window.NX_LOAD_TASKS.push( function () {
      p8Load();
      p8StartPoll();
      console.debug('[Phase 8] Monetization & Access Control Layer active.');
    });

  })();

  /* ================================================================
     PHASE 9 — MODEL INTELLIGENCE ROUTING SYSTEM
     Role-based routing · Live Inspector · Fallback log · Plan sync
     ================================================================ */
  (function () {
    'use strict';

    /* ── State ──────────────────────────────────────────────────────── */
    let p9State = null;   // last /api/p9/routing response
    let p9PollTimer = null;
    let p9PlanMode = 'lite'; // mirrors current plan/exec mode

    const P9_ROLE_LABELS = { planning: 'Plan', coding: 'Code', debug: 'Debug' };
    const P9_ROLE_ORDER = ['planning', 'coding', 'debug'];

    /* ── Helpers ────────────────────────────────────────────────────── */
    function p9El(id) { return document.getElementById(id); }

    function p9Esc(s) {
      return String(s || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
    }

    function p9CurrentPlan() {
      // Read plan selector value or fall back to p8State
      const sel = document.getElementById('nxPlanSelect') ||
        document.getElementById('nxExecMode') ||
        document.querySelector('[data-plan-selector]');
      if (sel && sel.value) return sel.value.toLowerCase();
      if (window.p8State) return window.p8State.plan || 'lite';
      return 'lite';
    }

    /* ── Load routing from API ──────────────────────────────────────── */
    async function p9Load(planMode) {
      try {
        const plan = planMode || p9CurrentPlan();
        const r = await fetch(`/api/p9/routing?plan_mode=${encodeURIComponent(plan)}`);
        const d = await r.json();
        if (!d.ok) return;
        p9State = d;
        p9PlanMode = d.plan_mode;
        p9RenderInspector(d);
      } catch (e) { }
    }

    /* ── Render inspector routing rows ─────────────────────────────── */
    function p9RenderInspector(d) {
      const rowsEl = p9El('p9RouteRows');
      const modeTag = p9El('p9PlanModeTag');
      const dot = p9El('p9StatusDot');
      if (!rowsEl) return;

      if (modeTag) modeTag.textContent = d.plan_mode.toUpperCase();

      const routes = d.routes || {};
      const anyFallback = P9_ROLE_ORDER.some(r => routes[r]?.fallback_used);
      if (dot) {
        dot.style.background = anyFallback ? '#f59e0b' : '#4caf50';
        dot.title = anyFallback ? 'Some providers using fallback' : 'All primary providers active';
      }

      let html = '';
      P9_ROLE_ORDER.forEach(role => {
        const info = routes[role];
        if (!info) return;
        const label = P9_ROLE_LABELS[role] || role;
        const color = d.provider_meta?.[info.provider]?.color || '#888';
        const fbBadge = info.fallback_used
          ? `<span class="p9-fallback-badge" title="Using fallback — primary provider unavailable">FB</span>`
          : '';
        const modelShort = info.model_label || (info.model || '').split('/').pop() || '—';
        const title = `${info.provider_name} · ${info.model}\nContext: ${(info.context_limit || 0).toLocaleString()} tokens`;

        html += `<div class="p9-role-row" title="${p9Esc(title)}">
            <span class="p9-role-label">${p9Esc(label)}</span>
            <span class="p9-model-tag" style="color:${color}">${p9Esc(modelShort)}</span>
            <span style="display:flex;align-items:center;gap:3px">
                ${fbBadge}
                <span class="p9-provider-dot" style="background:${color}" title="${p9Esc(info.provider_name)}"></span>
            </span>
        </div>`;
      });
      rowsEl.innerHTML = html || '<div style="color:var(--muted);font-size:10px;padding:4px 0">No routing data</div>';
    }

    /* ── React to plan/exec-mode changes ────────────────────────────── */
    function p9HookPlanSelector() {
      const selectors = [
        document.getElementById('nxPlanSelect'),
        document.getElementById('nxExecMode'),
        document.querySelector('select[name="plan_mode"]'),
        document.querySelector('[data-plan-selector]'),
      ].filter(Boolean);

      selectors.forEach(el => {
        el.addEventListener('change', () => {
          const val = el.value?.toLowerCase() || 'lite';
          if (val !== p9PlanMode) {
            p9PlanMode = val;
            p9Load(val);
          }
        });
      });
    }

    /* ── Hook into task start events to log "which model was used" ──── */
    // The task start event is dispatched by the main app; we listen and
    // append a routing note to the output log.
    function p9HookTaskEvents() {
      document.addEventListener('nxTaskStart', function (e) {
        const plan = e?.detail?.plan_mode || p9CurrentPlan();
        p9Load(plan);
      });
      document.addEventListener('nxTaskDone', function (e) {
        p9Load();
      });
    }

    /* ── Expose routing state for other phases / console ─────────────── */
    window.p9GetRoutes = () => p9State?.routes || {};
    window.p9LoadRouting = p9Load;
    window.p9GetPlanMode = () => p9PlanMode;

    /* ── Poll every 30s ─────────────────────────────────────────────── */
    function p9StartPoll() {
      if (p9PollTimer) clearInterval(p9PollTimer);
      p9PollTimer = setInterval(() => p9Load(p9CurrentPlan()), 30000);
    }

    /* ── Init ───────────────────────────────────────────────────────── */
    window.NX_LOAD_TASKS.push( function () {
      p9Load(p9CurrentPlan());
      p9StartPoll();
      p9HookPlanSelector();
      p9HookTaskEvents();
      console.debug('[Phase 9] Model Intelligence Routing System active.');
    });

  })();

  /* ═══════════════════════════════════════════════════════════════════
     PHASE 10 — Agent Intelligence & Memory System (UI)
     ═══════════════════════════════════════════════════════════════════ */
  (function () {
    'use strict';

    const _p10 = id => document.getElementById(id);

    /* ── Grade helper ──────────────────────────────────────────────── */
    function _p10Grade(score) {
      if (score >= 90) return { grade: 'A', color: '#4caf50' };
      if (score >= 75) return { grade: 'B', color: '#8bc34a' };
      if (score >= 60) return { grade: 'C', color: '#f59e0b' };
      if (score >= 40) return { grade: 'D', color: '#ff9800' };
      return { grade: 'F', color: '#f87171' };
    }

    /* ── Render intelligence score in Inspector ─────────────────────── */
    function p10RenderScore(data) {
      const gradeEl = _p10('p10Grade');
      const rateEl = _p10('p10SuccessRate');
      const callsEl = _p10('p10Calls');
      if (!gradeEl) return;

      const score = data.score ?? 0;
      const g = _p10Grade(score);
      gradeEl.textContent = g.grade;
      gradeEl.style.color = g.color;
      gradeEl.style.borderColor = g.color + '66';

      if (rateEl) rateEl.textContent = data.success_rate != null
        ? (data.success_rate * 100).toFixed(0) + '%' : '—';
      if (callsEl) callsEl.textContent = data.total_calls ?? 0;
    }

    /* ── Render recent memory items in Inspector ────────────────────── */
    function p10RenderMemory(items) {
      const wrap = _p10('p10MemItems');
      if (!wrap) return;
      if (!items || !items.length) {
        wrap.innerHTML = '<div style="font-size:10px;color:var(--muted)">No tasks recorded yet.</div>';
        return;
      }
      wrap.innerHTML = items.slice(0, 5).map(it => {
        const cls = it.success === false ? 'fail' : (it.success === true ? 'success' : '');
        const icon = it.success === false ? '✗' : (it.success === true ? '✓' : '·');
        const txt = (it.task || it.summary || '').substring(0, 70);
        return `<div class="p10-mem-item ${cls}">${icon} ${txt}</div>`;
      }).join('');
    }

    /* ── Fetch + render cycle ───────────────────────────────────────── */
    async function p10Refresh() {
      try {
        const [scoreR, memR] = await Promise.all([
          fetch('/api/agent/score'),
          fetch('/api/memory/recent?limit=5'),
        ]);
        const scoreD = await scoreR.json();
        const memD = await memR.json();
        if (scoreD.ok !== false) {
          const s = scoreD.score || scoreD;
          p10RenderScore({
            score: Math.round((s.success_rate ?? s.quality_score ?? 1) * 100),
            success_rate: s.success_rate ?? s.quality_score,
            total_calls: s.calls ?? s.session_count ?? 0,
          });
        }
        const items = memD.session_stm || memD.tasks || memD.recent || [];
        p10RenderMemory(items);
      } catch (e) { }
    }

    /* ── Hook into task completion events ──────────────────────────── */
    document.addEventListener('nxTaskDone', p10Refresh);

    /* ── Init ───────────────────────────────────────────────────────── */
    let _p10Timer = null;
    window.NX_LOAD_TASKS.push( function () {
      p10Refresh();
      _p10Timer = setInterval(p10Refresh, 45000);
      console.debug('[Phase 10] Agent Intelligence & Memory System active.');
    });

    window.p10Refresh = p10Refresh;

  })();

  /* ═══════════════════════════════════════════════════════════════════
     PHASE 11 — Multi-Agent Collaboration System (UI)
     ═══════════════════════════════════════════════════════════════════ */
  (function () {
    'use strict';

    const _p11 = id => document.getElementById(id);

    let _p11ActiveSid = null;
    let _p11Polling = null;

    /* ── Append a line to the team log area ────────────────────────── */
    function p11Log(agent, msg) {
      const area = _p11('p11LogArea');
      if (!area) return;
      const now = new Date();
      const ts = now.toLocaleTimeString('en-US', { hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit' });
      const entry = document.createElement('div');
      entry.className = 'p11-log-entry';
      entry.innerHTML = `<span class="p11-log-ts">${ts}</span><span class="p11-log-agent ${(agent || '').toLowerCase()}">${agent}</span><span class="p11-log-msg">${msg}</span>`;
      area.appendChild(entry);
      area.scrollTop = area.scrollHeight;
    }

    /* ── Update an agent card's status/task ────────────────────────── */
    function p11SetCard(agent, status, task) {
      const nameUp = agent.charAt(0).toUpperCase() + agent.slice(1);
      const statEl = _p11('p11Status' + nameUp);
      const taskEl = _p11('p11Task' + nameUp);
      const card = _p11('p11Card' + nameUp);
      if (statEl) { statEl.textContent = status; statEl.className = 'p11-agent-status ' + status; }
      if (taskEl) taskEl.textContent = task || '—';
      if (card) { card.className = 'p11-agent-card ' + (status !== 'idle' ? status : ''); }
    }

    /* ── Set team status badge ──────────────────────────────────────── */
    function p11SetStatus(status, text) {
      const el = _p11('p11TeamStatus');
      if (!el) return;
      el.textContent = text || status;
      el.className = 'p11-status-badge ' + status;
    }

    /* ── Reset all agent cards to idle ─────────────────────────────── */
    function p11ResetCards() {
      ['manager', 'research', 'coding', 'debug'].forEach(a => p11SetCard(a, 'idle', '—'));
    }

    /* ── Clear log area ─────────────────────────────────────────────── */
    function p11ClearLog() {
      const area = _p11('p11LogArea');
      if (area) area.innerHTML = '';
    }

    /* ── Poll for team run status ───────────────────────────────────── */
    async function p11PollStatus(sid) {
      try {
        const r = await fetch(`/api/p11/team/status?sid=${encodeURIComponent(sid)}`);
        const d = await r.json();
        if (!d.ok) return;

        const s = d.status;
        p11SetStatus(s, s.charAt(0).toUpperCase() + s.slice(1));

        const steps = d.steps || [];
        steps.forEach(st => {
          p11SetCard(st.agent, st.status, st.summary || st.task || '');
        });

        if (d.log_lines && d.log_lines.length) {
          p11ClearLog();
          d.log_lines.forEach(l => p11Log(l.agent || 'System', l.message || l.msg || ''));
        }

        if (s === 'done' || s === 'failed' || s === 'stopped') {
          clearInterval(_p11Polling);
          _p11Polling = null;
          const runBtn = _p11('p11RunBtn');
          const cancelBtn = _p11('p11CancelBtn');
          if (runBtn) runBtn.disabled = false;
          if (cancelBtn) cancelBtn.disabled = true;
          if (s === 'done') {
            p11Log('System', '✅ Team run complete.');
            p11SetStatus('done', 'Done');
          } else if (s === 'failed') {
            p11Log('System', '❌ Team run failed.');
            p11SetStatus('failed', 'Failed');
          } else {
            p11Log('System', '⚠ Run stopped.');
            p11SetStatus('paused', 'Stopped');
          }
        }
      } catch (e) {
        p11Log('System', 'Poll error: ' + e.message);
      }
    }

    /* ── Start a team run ───────────────────────────────────────────── */
    window.p11RunTeam = async function () {
      const task = (_p11('p11TaskInput')?.value || '').trim();
      if (!task) {
        alert('Please describe a task for the AI team.');
        return;
      }
      const sid = (typeof currentSession !== 'undefined' && currentSession) || '';
      if (!sid) {
        alert('Please create or select a session first.');
        return;
      }
      const runBtn = _p11('p11RunBtn');
      const cancelBtn = _p11('p11CancelBtn');
      if (runBtn) runBtn.disabled = true;
      if (cancelBtn) cancelBtn.disabled = false;

      _p11ActiveSid = sid;
      p11ClearLog();
      p11ResetCards();
      p11SetStatus('running', 'Running');
      p11Log('System', `▶ Starting team run for session [${sid.substring(0, 8)}]…`);
      p11Log('System', `Task: "${task.substring(0, 80)}"`);

      try {
        const r = await fetch('/api/p11/team/run', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ task, sid }),
        });
        const d = await r.json();
        if (!d.ok) throw new Error(d.error || 'Run failed to start');
        p11Log('System', `Status: ${d.status}`);
        _p11Polling = setInterval(() => p11PollStatus(sid), 2000);
      } catch (e) {
        p11Log('System', '❌ ' + e.message);
        p11SetStatus('failed', 'Error');
        if (runBtn) runBtn.disabled = false;
        if (cancelBtn) cancelBtn.disabled = true;
      }
    };

    /* ── Cancel the active run ──────────────────────────────────────── */
    window.p11CancelTeam = async function () {
      if (!_p11ActiveSid) return;
      try {
        await fetch('/api/p11/team/pause', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ sid: _p11ActiveSid }),
        });
        p11Log('System', '⚠ Pause/cancel request sent…');
      } catch (e) { }
    };

    /* ── Patch setActiveTab to show/hide the aiteam + support panels ─── */
    const _p11_origSetTab = window.setActiveTab;
    window.setActiveTab = function (tab) {
      if (typeof _p11_origSetTab === 'function') _p11_origSetTab(tab);
      const el = document.getElementById('tabAiteam');
      if (el) {
        if (tab === 'aiteam') el.classList.remove('hidden');
        else el.classList.add('hidden');
      }
      const supEl = document.getElementById('tabSupport');
      if (supEl) {
        if (tab === 'support') {
          supEl.classList.remove('hidden');
          nxSupportLoadTickets();
        } else {
          supEl.classList.add('hidden');
        }
      }
    };

    /* ── Init ───────────────────────────────────────────────────────── */
    window.NX_LOAD_TASKS.push( function () {
      console.debug('[Phase 11] Multi-Agent Collaboration System active.');
    });

  })();
