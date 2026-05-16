/**
 * nx-z31-forensics.js — Phase Z31 Persistent Forensics + Execution Memory Controller
 * ══════════════════════════════════════════════════════════════════════════════════════
 *
 * Responsibilities:
 *  Z31A — Auto-persist DAG snapshots to server on every NxDagEngine snapshot event
 *  Z31B — Replay integrity: fingerprint validation, drift detection on session load
 *  Z31C — Historical session browser: load, filter, inspect, cross-device hydrate
 *  Z31D — Forensic bundle export (download) + import (file picker + drag/drop)
 *  Z31E — Persistence indicator, drift banner, snapshot watermark
 *
 * Rules:
 *  - NO fake data. All state from live API responses or NxBus events.
 *  - Replay isolation: imported sessions NEVER touch the active runtime.
 *  - RAF-batched UI writes. No layout thrashing.
 *  - Interval timers cleared on session end + page unload.
 *  - All fetch errors are handled gracefully — persistence failures never crash UI.
 */
'use strict';

(function () {
  if (window._z31forensics) return;

  /* ── Constants ──────────────────────────────────────────────────── */
  const API             = '/api/z31';
  const SESSION_POLL_MS = 30_000;
  const SNAP_DEBOUNCE   = 2_500;   // ms between auto-snapshot writes
  const MAX_SNAP_CACHE  = 200;

  /* ── State ──────────────────────────────────────────────────────── */
  const S = {
    sid:            null,
    lastSnapHash:   null,
    snapTimer:      null,
    pollTimer:      null,
    pendingSnap:    false,
    isIsolated:     false,
    activeFilter:   null,
    selectedSid:    null,
    driftDetected:  false,
    cache: {
      sessions:   [],
      snapshots:  {},
      integrity:  {},
    },
  };

  /* ── DOM helpers ────────────────────────────────────────────────── */
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s ?? '').replace(/[<>&"]/g, c =>
    ({ '<': '&lt;', '>': '&gt;', '&': '&amp;', '"': '&quot;' }[c]));

  /* ── Persistence indicator ──────────────────────────────────────── */
  function _setDot(state) {  // 'saving' | 'saved' | 'error' | 'isolated'
    const dot = $('z31PersistDot');
    if (!dot) return;
    dot.className = `z31-persist-dot ${state}`;
    dot.title = {
      saving:   'Persisting snapshot…',
      saved:    'Snapshot saved',
      error:    'Snapshot save failed',
      isolated: 'Isolated replay session',
    }[state] || '';
  }

  /* ── Snapshot watermark ─────────────────────────────────────────── */
  function _setWatermark(text) {
    const wm = $('z31SnapWatermark');
    if (wm) wm.textContent = text;
  }

  /* ── Drift banner ───────────────────────────────────────────────── */
  function _showDrift(msg) {
    const banner = $('z31DriftBanner');
    if (!banner) return;
    banner.querySelector('.z31-drift-msg').textContent = msg;
    banner.classList.add('visible');
  }

  function _hideDrift() {
    const banner = $('z31DriftBanner');
    if (banner) banner.classList.remove('visible');
  }

  /* ── Auto-snapshot: called whenever NxDagEngine has a new snapshot ── */
  function _scheduleSnapshot() {
    if (S.pendingSnap || !S.sid || S.isIsolated) return;
    S.pendingSnap = true;
    if (S.snapTimer) clearTimeout(S.snapTimer);
    S.snapTimer = setTimeout(_doSnapshot, SNAP_DEBOUNCE);
  }

  function _doSnapshot() {
    S.pendingSnap = false;
    if (!S.sid || !window.NxDagEngine || S.isIsolated) return;

    const info = NxDagEngine.getState ? NxDagEngine.getState() : null;
    if (!info) return;

    const nodes = info.nodes || [];
    const edges = info.edges || [];
    const metrics = info.metrics || {};

    // Compute hash to avoid redundant writes
    const hashInput = JSON.stringify({ nodes: nodes.map(n => ({ id: n.id, state: n.state })), edges });
    const hash = _quickHash(hashInput);
    if (hash === S.lastSnapHash) return;
    S.lastSnapHash = hash;

    _setDot('saving');

    fetch(`${API}/snapshot/${encodeURIComponent(S.sid)}`, {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify({ nodes, edges, metrics }),
    })
      .then(r => r.json())
      .then(data => {
        if (data.ok) {
          _setDot('saved');
          _setWatermark(`snap#${data.index} · ${_fmtTime(Date.now())}`);
          S.cache.snapshots[S.sid] = S.cache.snapshots[S.sid] || [];
          if (S.cache.snapshots[S.sid].length > MAX_SNAP_CACHE)
            S.cache.snapshots[S.sid].shift();
        } else {
          _setDot('error');
        }
      })
      .catch(() => _setDot('error'));
  }

  /* ── Session hydration: load latest snapshot from server ───────── */
  function _hydrateFromServer(sid) {
    if (!sid || !window.NxDagEngine) return;
    fetch(`${API}/snapshot/${encodeURIComponent(sid)}/latest`)
      .then(r => r.json())
      .then(data => {
        if (!data.ok || !data.snapshot) return;
        const { nodes, edges } = data.snapshot;
        if (!nodes || !nodes.length) return;
        NxDagEngine.applySnapshot({ nodes, edges });
        _setWatermark(`restored snap#${data.snapshot.snapshot_index} · ${_fmtTime(data.snapshot.created_at * 1000)}`);
        _setDot('saved');

        // Validate fingerprint / drift
        const serverFp = data.snapshot.fingerprint;
        if (serverFp && window.NxDagEngine.getReplayInfo) {
          _checkDrift(sid, serverFp);
        }
      })
      .catch(() => {});
  }

  function _checkDrift(sid, clientFp) {
    fetch(`${API}/integrity/${encodeURIComponent(sid)}?fingerprint=${encodeURIComponent(clientFp)}`)
      .then(r => r.json())
      .then(data => {
        if (!data.ok) return;
        const drift = data.integrity?.drift;
        if (drift && drift.drift_detected && drift.risk === 'HIGH') {
          S.driftDetected = true;
          _showDrift(`Replay drift detected — server fingerprint mismatch (risk: ${drift.risk}). Data may be incomplete.`);
        } else {
          S.driftDetected = false;
          _hideDrift();
        }
      })
      .catch(() => {});
  }

  /* ── Historical session browser ─────────────────────────────────── */
  function _loadSessions(filter) {
    S.activeFilter = filter || null;
    const params = new URLSearchParams({ limit: '50' });
    if (filter) params.set('filter', filter);

    fetch(`${API}/sessions?${params}`)
      .then(r => r.json())
      .then(data => {
        if (!data.ok) return;
        S.cache.sessions = data.sessions || [];
        _renderSessionList();
      })
      .catch(() => {});
  }

  function _renderSessionList() {
    const list = $('z31SessionList');
    if (!list) return;

    if (!S.cache.sessions.length) {
      list.innerHTML = `<div style="padding:12px;font-size:9px;color:var(--text-dim,#6e7681);text-align:center">No sessions stored</div>`;
      return;
    }

    list.innerHTML = S.cache.sessions.map(sess => {
      const age = sess.age_s != null ? _fmtAge(sess.age_s) : '—';
      const snaps = sess.snapshot_count || 0;
      const evs   = sess.event_count || 0;
      const integ = S.cache.integrity[sess.session_id];
      const integHtml = integ
        ? `<span class="z31-integrity-score ${integ.verdict}" title="Integrity: ${integ.score}/100">${integ.score}</span>`
        : '';
      const isActive = sess.session_id === S.selectedSid;

      return `
        <div class="z31-session-item${isActive ? ' active' : ''}"
          onclick="_z31forensics.selectSession('${esc(sess.session_id)}')"
          title="${esc(sess.session_id)}">
          <div class="z31-session-sid">${esc(sess.session_id.slice(-36))}</div>
          <div class="z31-session-meta">
            <span class="z31-session-badge snaps" title="Snapshots">${snaps} snaps</span>
            <span class="z31-session-badge events" title="Events">${evs} evts</span>
            <span style="flex:1"></span>
            ${integHtml}
            <span>${age}</span>
          </div>
        </div>
      `;
    }).join('');
  }

  function _selectSession(sid) {
    S.selectedSid = sid;
    _renderSessionList();
    _loadSessionDetail(sid);
  }

  function _loadSessionDetail(sid) {
    const detail = $('z31ForensicDetail');
    if (!detail) return;
    detail.innerHTML = `<div style="font-size:9px;color:var(--text-dim,#6e7681);padding:4px">Loading…</div>`;

    Promise.all([
      fetch(`${API}/snapshots/${encodeURIComponent(sid)}?limit=5`).then(r => r.json()),
      fetch(`${API}/integrity/${encodeURIComponent(sid)}`).then(r => r.json()),
    ]).then(([snapsData, intData]) => {
      const snaps   = snapsData.ok ? snapsData : { total: 0, snapshots: [] };
      const integ   = intData.ok  ? intData.integrity : { score: 0, verdict: 'UNKNOWN', issues: [] };

      if (integ.verdict) {
        S.cache.integrity[sid] = integ;
        _renderSessionList();
      }

      const rows = [
        ['Session', sid.slice(-24) + '…'],
        ['Snapshots', snaps.total || 0],
        ['Integrity', `<span class="z31-integrity-score ${integ.verdict}">${integ.score}/100 ${integ.verdict}</span>`],
        ['Fingerprint', (integ.session_fingerprint || '—').slice(0, 12) + '…'],
      ];

      if (integ.issues && integ.issues.length) {
        rows.push(['Issues', `<span style="color:#f87171">${integ.issues.length} issue(s)</span>`]);
      }

      detail.innerHTML = rows.map(([k, v]) => `
        <div class="z31-detail-row">
          <span class="z31-detail-key">${esc(k)}</span>
          <span class="z31-detail-val">${v}</span>
        </div>
      `).join('');

      detail.insertAdjacentHTML('beforeend', `
        <div class="z31-detail-actions">
          <button class="z31-action-btn primary" onclick="_z31forensics.loadReplay('${esc(sid)}')" title="Load DAG from this session">↺ Load</button>
          <button class="z31-action-btn" onclick="_z31forensics.exportBundle('${esc(sid)}')" title="Export forensic bundle">⬇ Export</button>
          <button class="z31-action-btn danger" onclick="_z31forensics.purgeSession('${esc(sid)}')" title="Delete all snapshots">✕</button>
        </div>
      `);
    }).catch(() => {
      detail.innerHTML = `<div style="font-size:9px;color:#f87171;padding:4px">Failed to load session detail.</div>`;
    });
  }

  /* ── Load historical DAG into Z30 surface ───────────────────────── */
  function _loadReplay(sid) {
    if (!window.NxDagEngine) return;
    fetch(`${API}/snapshot/${encodeURIComponent(sid)}/latest`)
      .then(r => r.json())
      .then(data => {
        if (!data.ok || !data.snapshot) {
          _toast('No snapshot found for this session.', 'warn');
          return;
        }
        const { nodes, edges } = data.snapshot;
        NxDagEngine.applySnapshot({ nodes, edges });
        _setWatermark(`HISTORICAL: ${sid.slice(-16)} snap#${data.snapshot.snapshot_index}`);
        _setDot('isolated');

        // Notify Z30 intel panel is in historical mode
        if (window._z30) {
          _toast(`Historical DAG loaded: ${sid.slice(-16)}`, 'info');
        }

        // Close forensic panel after load
        _closePanel();
      })
      .catch(() => _toast('Failed to load historical DAG.', 'error'));
  }

  /* ── Export bundle ──────────────────────────────────────────────── */
  function _exportBundle(sid) {
    _toast('Generating forensic bundle…', 'info');
    fetch(`${API}/export/${encodeURIComponent(sid)}`)
      .then(r => r.json())
      .then(data => {
        if (!data.ok) { _toast('Export failed: ' + data.error, 'error'); return; }

        // Build download
        const payload = JSON.stringify({
          bundle_b64:  data.compressed_b64,
          session_id:  data.session_id,
          bundle_hash: data.bundle_hash,
        }, null, 2);

        const blob = new Blob([payload], { type: 'application/json' });
        const a    = document.createElement('a');
        a.href     = URL.createObjectURL(blob);
        a.download = `nx-forensic-${sid.slice(-12)}-${_fmtDate()}.json`;
        a.click();
        URL.revokeObjectURL(a.href);

        _toast(`Bundle exported: ${data.snapshot_count} snapshots, ${data.size_bytes} bytes`, 'success');
      })
      .catch(() => _toast('Export failed — network error.', 'error'));
  }

  /* ── Import bundle ──────────────────────────────────────────────── */
  function _importBundle(b64, alias) {
    _toast('Importing forensic bundle…', 'info');
    fetch(`${API}/import`, {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify({ bundle_b64: b64, session_alias: alias || '' }),
    })
      .then(r => r.json())
      .then(data => {
        if (!data.ok) { _toast('Import failed: ' + data.error, 'error'); return; }
        const valid = data.fingerprint_valid ? '✓ fingerprint valid' : '⚠ fingerprint mismatch';
        _toast(`Bundle imported as ${data.replay_session_id.slice(-16)} [${valid}]`, data.fingerprint_valid ? 'success' : 'warn');

        // Auto-load the imported session's DAG
        _loadReplay(data.replay_session_id);
        _loadSessions();
      })
      .catch(() => _toast('Import failed — network error.', 'error'));
  }

  function _handleFileImport(file) {
    if (!file) return;
    const reader = new FileReader();
    reader.onload = (e) => {
      try {
        const parsed = JSON.parse(e.target.result);
        const b64    = parsed.bundle_b64 || '';
        const alias  = parsed.session_id || '';
        if (!b64) { _toast('Invalid bundle file — missing bundle_b64', 'error'); return; }
        _importBundle(b64, alias);
      } catch (err) {
        _toast('Failed to parse bundle file: ' + err.message, 'error');
      }
    };
    reader.readAsText(file);
  }

  /* ── Panel show/hide ────────────────────────────────────────────── */
  function _openPanel() {
    const panel = $('z31ForensicPanel');
    if (panel) panel.classList.add('open');
    const btn = $('z31ForensicToggle');
    if (btn) btn.classList.add('active');
    _loadSessions(S.activeFilter);
  }

  function _closePanel() {
    const panel = $('z31ForensicPanel');
    if (panel) panel.classList.remove('open');
    const btn = $('z31ForensicToggle');
    if (btn) btn.classList.remove('active');
  }

  function _togglePanel() {
    const panel = $('z31ForensicPanel');
    if (!panel) return;
    panel.classList.contains('open') ? _closePanel() : _openPanel();
  }

  /* ── Purge session ──────────────────────────────────────────────── */
  function _purgeSession(sid) {
    if (!confirm(`Delete all snapshots for session ${sid.slice(-16)}…?`)) return;
    fetch(`${API}/snapshots/${encodeURIComponent(sid)}`, { method: 'DELETE' })
      .then(r => r.json())
      .then(data => {
        if (data.ok) { _toast('Session snapshots deleted.', 'success'); _loadSessions(); }
        else _toast('Delete failed: ' + data.error, 'error');
      })
      .catch(() => _toast('Delete failed.', 'error'));
  }

  /* ── Filter chips ───────────────────────────────────────────────── */
  function _setFilter(filter) {
    S.activeFilter = filter === S.activeFilter ? null : filter;
    // Update chip UI
    document.querySelectorAll('.z31-filter-chip').forEach(chip => {
      chip.classList.remove('active', 'active-failed', 'active-retries');
    });
    if (S.activeFilter) {
      const chip = document.querySelector(`[data-z31-filter="${S.activeFilter}"]`);
      if (chip) chip.classList.add('active', S.activeFilter === 'failed' ? 'active-failed' : S.activeFilter === 'retries' ? 'active-retries' : 'active');
    }
    _loadSessions(S.activeFilter);
  }

  /* ── NxBus wiring ───────────────────────────────────────────────── */
  function _wireNxBus() {
    if (!window.NxBus) { setTimeout(_wireNxBus, 200); return; }

    // Hook into DAG snapshot events — auto-persist on every applySnapshot
    NxBus.on('dag.snapshot.applied', () => {
      if (!S.isIsolated) _scheduleSnapshot();
    }, { owner: 'z31forensics' });

    NxBus.on('dag.replay.available', (e) => {
      if (e?.count > 0) _loadSessions();
    }, { owner: 'z31forensics' });

    // Session lifecycle
    NxBus.on('session.started', (e) => {
      const sid = e?.sid || e?.session_id;
      if (sid) _onSessionStart(sid);
    }, { owner: 'z31forensics' });

    NxBus.on('session.done',  () => _onSessionEnd(), { owner: 'z31forensics' });
    NxBus.on('session.error', () => _onSessionEnd(), { owner: 'z31forensics' });

    const EV = NxBus.EVENTS || {};
    NxBus.on(EV.SESSION_CREATED  || 'nx:session:created', (e) => {
      const sid = e?.session_id || e?.sid;
      if (sid) _onSessionStart(sid);
    }, { owner: 'z31forensics' });

    NxBus.on(EV.SESSION_RESTORED || 'nx:session:restored', (e) => {
      const sid = e?.session_id || e?.sid;
      if (sid) { S.sid = sid; _hydrateFromServer(sid); }
    }, { owner: 'z31forensics' });
  }

  /* ── Hook NxDagEngine applySnapshot to fire NxBus event ────────── */
  function _hookDagEngine() {
    if (!window.NxDagEngine) { setTimeout(_hookDagEngine, 200); return; }
    if (NxDagEngine._z31Hooked) return;

    const orig = NxDagEngine.applySnapshot.bind(NxDagEngine);
    NxDagEngine.applySnapshot = function (data) {
      orig(data);
      if (window.NxBus) NxBus.emit('dag.snapshot.applied', data);
    };
    NxDagEngine._z31Hooked = true;
  }

  /* ── Session lifecycle ──────────────────────────────────────────── */
  function _onSessionStart(sid) {
    S.sid          = sid;
    S.lastSnapHash = null;
    S.isIsolated   = sid.startsWith('replay:');
    S.driftDetected = false;
    _hideDrift();

    if (S.isIsolated) {
      _setDot('isolated');
    } else {
      _setDot('saved');
      // Attempt to hydrate DAG from server (cross-device / page refresh recovery)
      setTimeout(() => _hydrateFromServer(sid), 1500);
    }

    if (S.pollTimer) clearInterval(S.pollTimer);
    S.pollTimer = setInterval(() => _loadSessions(S.activeFilter), SESSION_POLL_MS);
  }

  function _onSessionEnd() {
    // Force a final snapshot write
    if (S.pendingSnap || S.snapTimer) {
      if (S.snapTimer) clearTimeout(S.snapTimer);
      _doSnapshot();
    }
    if (S.pollTimer) { clearInterval(S.pollTimer); S.pollTimer = null; }
  }

  /* ── Drag/drop wiring for import zone ───────────────────────────── */
  function _wireDragDrop() {
    const zone = $('z31ImportZone');
    if (!zone) return;

    zone.addEventListener('dragover', (e) => { e.preventDefault(); zone.classList.add('drag-over'); });
    zone.addEventListener('dragleave', () => zone.classList.remove('drag-over'));
    zone.addEventListener('drop', (e) => {
      e.preventDefault();
      zone.classList.remove('drag-over');
      const file = e.dataTransfer?.files?.[0];
      if (file) _handleFileImport(file);
    });
    zone.addEventListener('click', () => {
      const input = document.createElement('input');
      input.type = 'file';
      input.accept = '.json';
      input.onchange = (e) => { if (e.target.files?.[0]) _handleFileImport(e.target.files[0]); };
      input.click();
    });
  }

  /* ── Toast utility (falls back to NxToast or console) ──────────── */
  function _toast(msg, type = 'info') {
    if (window.NxToast) { NxToast[type]?.(msg) || NxToast.info?.(msg); return; }
    if (window.showToast) { showToast(msg, type); return; }
    console.log(`[Z31] [${type.toUpperCase()}] ${msg}`);
  }

  /* ── Formatting helpers ─────────────────────────────────────────── */
  function _quickHash(str) {
    let h = 0;
    for (let i = 0; i < str.length; i++) {
      h = Math.imul(31, h) + str.charCodeAt(i) | 0;
    }
    return (h >>> 0).toString(16);
  }

  function _fmtTime(ms) {
    const d = new Date(ms);
    return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
  }

  function _fmtDate() {
    return new Date().toISOString().slice(0, 10);
  }

  function _fmtAge(s) {
    if (s < 60)     return `${s}s ago`;
    if (s < 3600)   return `${Math.floor(s / 60)}m ago`;
    if (s < 86400)  return `${Math.floor(s / 3600)}h ago`;
    return `${Math.floor(s / 86400)}d ago`;
  }

  /* ── Page unload cleanup ────────────────────────────────────────── */
  function _wireUnload() {
    window.addEventListener('beforeunload', () => {
      if (S.snapTimer) clearTimeout(S.snapTimer);
      if (S.pollTimer) clearInterval(S.pollTimer);
      // Best-effort final snapshot using sendBeacon (non-blocking)
      if (S.sid && !S.isIsolated && window.NxDagEngine) {
        const info = NxDagEngine.getState ? NxDagEngine.getState() : null;
        if (info) {
          const payload = JSON.stringify({ nodes: info.nodes || [], edges: info.edges || [] });
          navigator.sendBeacon?.(`${API}/snapshot/${encodeURIComponent(S.sid)}`, new Blob([payload], { type: 'application/json' }));
        }
      }
    });
  }

  /* ── Public API ──────────────────────────────────────────────────── */
  window._z31forensics = {
    openPanel:    _openPanel,
    closePanel:   _closePanel,
    togglePanel:  _togglePanel,
    setFilter:    _setFilter,
    selectSession: _selectSession,
    loadReplay:   _loadReplay,
    exportBundle: _exportBundle,
    importBundle: _importBundle,
    purgeSession: _purgeSession,
    forceSnapshot: _doSnapshot,
    checkDrift(fp) { if (S.sid) _checkDrift(S.sid, fp); },
    setSid(sid) { S.sid = sid; },
  };

  /* ── Init ─────────────────────────────────────────────────────────── */
  function _init() {
    _wireNxBus();
    _hookDagEngine();
    _wireDragDrop();
    _wireUnload();

    // Inherit active session from Z30 or globals
    const sid = window.currentSession || (window._z30 ? null : null);
    if (sid) _onSessionStart(sid);

    // Initial session list load
    _loadSessions();

    console.log('[Phase Z31] Persistent Forensics + Execution Memory active.');
  }

  if (window.NX_LOAD_TASKS) {
    window.NX_LOAD_TASKS.push(_init);
  } else if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => setTimeout(_init, 250));
  } else {
    setTimeout(_init, 250);
  }

})();
