/**
 * nx-z33-ux.js — Phase Z33 Operational Workspace Controller
 * ═══════════════════════════════════════════════════════════
 * Z33A — Idle state manager: surfaces Z32 signals, replay resume,
 *         pending approvals, recent forensic alerts, runtime pulse
 * Z33B — Execution timeline dock integration
 * Z33C — Sidebar workspace memory surface
 *
 * Rules:
 *  - No fake data. All state from live API or NxBus.
 *  - All animations are calm and slow (≥ 2.4s cycles).
 *  - No neon effects, no gradients on data, no marketing UI.
 *  - DOM writes are RAF-batched.
 *  - Timers cleared on page unload.
 */
'use strict';

(function () {
  if (window._z33ux) return;

  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s ?? '').replace(/[<>&"]/g, c =>
    ({ '<': '&lt;', '>': '&gt;', '&': '&amp;', '"': '&quot;' }[c]));

  /* ── State ──────────────────────────────────────────────────────── */
  const S = {
    isExecuting:  false,
    replayActive: false,
    lastSessionId: null,
    lastConfidence: null,
    lastPressure:   null,
    idleRefreshTimer: null,
    pulseState: 'idle', // idle | active | degraded | critical
  };

  /* ═══════════════════════════════════════════════════════════════
     Z33A — Runtime Pulse
     ═══════════════════════════════════════════════════════════════ */

  function _setPulse(state, label) {
    const el = $('z33RuntimePulse');
    if (!el) return;
    S.pulseState = state;
    el.className = `z33-runtime-pulse ${state}`;
    const lbl = el.querySelector('.z33-pulse-label');
    if (lbl) lbl.textContent = label || _pulseLabel(state);
  }

  function _pulseLabel(state) {
    return { idle: 'Idle', active: 'Active', degraded: 'Degraded', critical: 'Critical' }[state] || 'Unknown';
  }

  function _syncPulseFromZ32() {
    if (!window._z32) return;
    const pressure = S.lastPressure;
    if (!pressure) return;
    const lvl = pressure.pressure_level || 'NOMINAL';
    if      (lvl === 'CRITICAL') _setPulse('critical', 'Critical');
    else if (lvl === 'HIGH')     _setPulse('degraded', 'Pressure');
    else if (S.isExecuting)      _setPulse('active',   'Running');
    else                         _setPulse('idle',     'Ready');
  }

  /* ═══════════════════════════════════════════════════════════════
     Z33A — Idle Hero Enhancement
     ═══════════════════════════════════════════════════════════════ */

  function _refreshIdleHero() {
    _injectIdleSignals();
    _injectReplayResume();
    _injectApprovals();
    _syncIdleStatusStrip();
  }

  function _injectIdleSignals() {
    const container = $('z33IdleSignals');
    if (!container) return;

    const signals = [];

    // Z32 confidence signal
    if (S.lastConfidence !== null) {
      const pct = Math.round(S.lastConfidence * 100);
      const cls = pct >= 75 ? 'ok' : pct >= 45 ? '' : 'warn';
      signals.push({ cls, icon: '◉', text: `Confidence ${pct}%`, action: () => window.nxSetTab?.('live') });
    }

    // Z32 pressure
    if (S.lastPressure) {
      const lvl = S.lastPressure.pressure_level;
      if (lvl !== 'NOMINAL') {
        const cls = lvl === 'CRITICAL' ? 'error' : lvl === 'HIGH' ? 'warn' : '';
        signals.push({ cls, icon: '⬡', text: `Pressure ${lvl}`, action: () => window.nxSetTab?.('live') });
      }
    }

    // Recent Z31 sessions with failures
    _fetchRecentAlerts().then(alerts => {
      alerts.forEach(a => signals.push(a));
      requestAnimationFrame(() => {
        if (!container) return;
        if (!signals.length) { container.innerHTML = ''; return; }
        container.innerHTML = signals.map(s =>
          `<span class="z33-idle-signal ${s.cls || ''}" title="${esc(s.text)}"
            onclick="${s.onclick || ''}" style="cursor:${s.action ? 'pointer' : 'default'}">
            ${esc(s.icon)} ${esc(s.text)}
          </span>`
        ).join('');
        // Wire click handlers that couldn't be inlined
        signals.forEach((s, i) => {
          if (!s.action) return;
          const el = container.children[i];
          if (el) el.addEventListener('click', s.action);
        });
      });
    });
  }

  async function _fetchRecentAlerts() {
    try {
      const r = await fetch('/api/z31/sessions?limit=5');
      const d = await r.json();
      const alerts = [];
      (d.sessions || []).forEach(sess => {
        if ((sess.integrity_verdict === 'CORRUPT' || sess.integrity_verdict === 'DEGRADED') && alerts.length < 2) {
          alerts.push({
            cls: 'warn',
            icon: '⚠',
            text: `Session ${(sess.session_id || '').slice(0, 8)}… ${sess.integrity_verdict}`,
          });
        }
      });
      return alerts;
    } catch {
      return [];
    }
  }

  function _injectReplayResume() {
    const el = $('z33ReplayResume');
    if (!el) return;

    // Look for last forensic session
    fetch('/api/z31/sessions?limit=1')
      .then(r => r.json())
      .then(d => {
        const sess = (d.sessions || [])[0];
        if (!sess) return;
        const ts = sess.latest_ts ? new Date(sess.latest_ts * 1000).toLocaleTimeString() : '';
        const sid = sess.session_id || '';
        requestAnimationFrame(() => {
          const sidEl = el.querySelector('.z33-replay-resume-sid');
          const metaEl = el.querySelector('.z33-replay-resume-meta');
          if (sidEl)  sidEl.textContent  = sid.slice(0, 12) + '…';
          if (metaEl) metaEl.textContent = `${sess.snapshot_count || 0} snapshots · ${ts}`;
          el.classList.add('visible');
          el.onclick = () => {
            if (window._z31forensics) {
              _z31forensics.loadSession(sid);
              window.nxSetTab?.('live');
            }
          };
        });
      })
      .catch(() => {});
  }

  function _injectApprovals() {
    const el = $('z33ApprovalsRow');
    if (!el) return;
    // Check HITL queue
    fetch('/api/hitl/pending').then(r => r.json()).then(d => {
      const count = (d.items || []).length || d.count || 0;
      if (count > 0) {
        requestAnimationFrame(() => {
          el.innerHTML = `⚠ ${count} pending approval${count !== 1 ? 's' : ''} — <a href="#" style="color:inherit" onclick="window.nxOpenPanel?.('hitl')">Review</a>`;
          el.classList.add('visible');
        });
      }
    }).catch(() => {});
  }

  function _syncIdleStatusStrip() {
    // Confidence
    const confEl = $('nxIdleConf');
    if (confEl && S.lastConfidence !== null) {
      const pct = Math.round(S.lastConfidence * 100);
      confEl.textContent = `${pct}%`;
      confEl.style.color = pct >= 75 ? 'var(--clr-ok)' : pct >= 45 ? 'var(--clr-warn)' : 'var(--clr-error)';
    }

    // Context pressure
    const ctxEl = $('nxIdleCtx');
    if (ctxEl && S.lastPressure) {
      const pct = Math.round((S.lastPressure.context_pressure || 0) * 100);
      ctxEl.textContent = `${pct}%`;
      ctxEl.style.color = pct > 70 ? 'var(--clr-warn)' : 'var(--clr-ok)';
    }
  }

  /* ═══════════════════════════════════════════════════════════════
     Z33C — Sidebar Workspace Memory Surface
     ═══════════════════════════════════════════════════════════════ */

  function _loadSidebarMemory() {
    const list = $('z33SidebarMemoryList');
    if (!list) return;

    const items = [];

    // Fetch skills
    fetch('/api/z32/skills?limit=3')
      .then(r => r.json())
      .then(d => {
        (d.skills || []).forEach(sk => items.push({
          type:  'skill',
          label: sk.name.replace('Workflow:', '').split('-').slice(0,2).join('→'),
          meta:  `${Math.round((sk.validation_rate || 0) * 100)}%`,
          action: () => window._z32?.toggleSkills(),
        }));
      })
      .catch(() => {})
      .finally(() => {
        // Fetch unstable sessions
        fetch('/api/z31/sessions?filter=instability&limit=2')
          .then(r => r.json())
          .then(d => {
            (d.sessions || []).forEach(s => items.push({
              type:  'unstable',
              label: (s.session_id || '').slice(0, 10) + '…',
              meta:  s.integrity_verdict || 'DEGRADED',
              action: () => { window._z31forensics?.loadSession(s.session_id); window.nxSetTab?.('live'); },
            }));
          })
          .catch(() => {})
          .finally(() => _renderSidebarMemory(list, items));
      });
  }

  function _renderSidebarMemory(container, items) {
    if (!items.length) {
      container.innerHTML = `<div class="z33-sm-item">
        <span class="z33-sm-item-label" style="color:var(--clr-muted)">No memory items yet</span>
      </div>`;
      return;
    }
    requestAnimationFrame(() => {
      container.innerHTML = items.map((item, i) => `
        <div class="z33-sm-item" data-z33-mem="${i}">
          <span class="z33-sm-item-dot ${item.type}"></span>
          <span class="z33-sm-item-label">${esc(item.label)}</span>
          <span class="z33-sm-item-meta">${esc(item.meta)}</span>
        </div>
      `).join('');
      items.forEach((item, i) => {
        if (!item.action) return;
        const el = container.querySelector(`[data-z33-mem="${i}"]`);
        if (el) el.addEventListener('click', item.action);
      });
    });
  }

  /* ═══════════════════════════════════════════════════════════════
     NxBus wiring
     ═══════════════════════════════════════════════════════════════ */

  function _wireNxBus() {
    if (!window.NxBus) { setTimeout(_wireNxBus, 200); return; }

    NxBus.on('session.started', () => {
      S.isExecuting = true;
      _setPulse('active', 'Running');
    }, { owner: 'z33ux' });

    NxBus.on('session.done', () => {
      S.isExecuting = false;
      _setPulse('idle', 'Ready');
      _refreshIdleHero();
      _loadSidebarMemory();
    }, { owner: 'z33ux' });

    NxBus.on('session.error', () => {
      S.isExecuting = false;
      _setPulse('degraded', 'Error');
      _refreshIdleHero();
    }, { owner: 'z33ux' });

    // Ingest Z32 pressure/confidence updates
    NxBus.on('z32.pressure.update', (d) => {
      S.lastPressure = d;
      _syncPulseFromZ32();
      _syncIdleStatusStrip();
    }, { owner: 'z33ux' });

    NxBus.on('z32.confidence.update', (d) => {
      S.lastConfidence = d?.score ?? null;
      _syncIdleStatusStrip();
    }, { owner: 'z33ux' });
  }

  /* ── Init ────────────────────────────────────────────────────────── */
  function _init() {
    _wireNxBus();
    _refreshIdleHero();
    _loadSidebarMemory();

    // Idle refresh every 45s — no-op if executing
    S.idleRefreshTimer = setInterval(() => {
      if (!S.isExecuting) _refreshIdleHero();
    }, 45_000);

    window.addEventListener('beforeunload', () => {
      if (S.idleRefreshTimer) clearInterval(S.idleRefreshTimer);
    });

    console.debug('[Phase Z33] Operational Workspace UX Controller active.');
  }

  window._z33ux = { refreshIdle: _refreshIdleHero, loadMemory: _loadSidebarMemory };

  if (window.NX_LOAD_TASKS) {
    window.NX_LOAD_TASKS.push(_init);
  } else if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => setTimeout(_init, 400));
  } else {
    setTimeout(_init, 400);
  }
})();
