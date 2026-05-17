/**
 * nx-diagnostics.js — Aetherion Operator Diagnostics Panel v1
 * ═══════════════════════════════════════════════════════════════════
 * Hidden by default. Operator-only access.
 * Toggle: Ctrl+Shift+D
 *
 * Exposes:
 *  - Active NxBus listener counts
 *  - Monaco model count
 *  - ResizeObserver count
 *  - SSE reconnect counter + state
 *  - NxMission current state
 *  - NxTrust confidence
 *  - DOM node counts (inspector, timeline)
 *  - Memory estimate (performance.memory if available)
 *  - NxBus event history (last 20)
 * ═══════════════════════════════════════════════════════════════════
 */
(function () {
  'use strict';

  const $ = id => document.getElementById(id);

  let _panel    = null;
  let _ticker   = null;
  let _visible  = false;

  /* ══════════════════════════════════════════════════════════════════
     PANEL CONSTRUCTION
     ══════════════════════════════════════════════════════════════════ */
  function _buildPanel() {
    if (_panel && _panel.isConnected) return _panel;

    _panel = document.createElement('div');
    _panel.id = 'nxDiagPanel';
    _panel.className = 'nx-diag-panel';
    _panel.innerHTML = `
      <div class="nx-diag-header">
        <span>DIAGNOSTICS</span>
        <span class="nx-diag-subtitle">operator view · Ctrl+Shift+D</span>
        <button class="nx-diag-close" id="nxDiagClose">close</button>
      </div>
      <div class="nx-diag-grid" id="nxDiagGrid"></div>
      <div class="nx-diag-section">
        <div class="nx-diag-label">BUS HISTORY (last 10)</div>
        <div class="nx-diag-history" id="nxDiagHistory"></div>
      </div>
    `;

    document.body.appendChild(_panel);
    $('nxDiagClose').onclick = _hide;
    return _panel;
  }

  /* ══════════════════════════════════════════════════════════════════
     METRICS COLLECTION
     ══════════════════════════════════════════════════════════════════ */
  function _collect() {
    const metrics = {};

    /* NxBus */ 
    if (window.NxBus) {
      const counts = NxBus.listenerCounts();
      metrics['Bus listeners'] = Object.values(counts).reduce((a,b) => a+b, 0);
      metrics['Bus events tracked'] = Object.keys(counts).length;
    }

    /* Monaco */
    try {
      metrics['Monaco models'] = window.monaco?.editor?.getModels()?.length ?? '—';
      metrics['Monaco active tab'] = window.NxMonaco?.getActiveTab()?.split('/').pop() ?? '—';
    } catch (_) { metrics['Monaco models'] = '?'; }

    /* ResizeObserver */
    metrics['ResizeObservers'] = window.NxHardening?.observerCount() ?? '—';

    /* SSE */
    if (window._NX_SSE_STATE !== undefined) {
      metrics['SSE state'] = window._NX_SSE_STATE;
    } else {
      const strip = $('nxLiveConnStatus');
      metrics['SSE state'] = strip?.textContent?.trim() ?? '—';
    }

    /* Mission */
    if (window.NxMission) {
      metrics['Mission phase'] = NxMission.getPhase();
      metrics['Mission objective'] = (NxMission.getObjective() || '—').slice(0, 30);
      const cont = NxMission.getContinuity();
      metrics['Recovery count'] = cont.successfulRecoveries;
      metrics['Escalations'] = cont.escalationCauses.length;
    }

    /* Trust */
    if (window.NxTrust) {
      metrics['Confidence'] = Math.round(NxTrust.getConf() * 100) + '%';
      const mem = NxTrust.memory();
      metrics['Flaky files'] = Object.keys(mem.flakyFiles).length;
      metrics['Retry loops'] = Object.keys(mem.retryLoops).length;
    }

    /* DOM node counts */
    metrics['Inspector nodes'] = $('nxInspectorContent')?.children.length ?? 0;
    metrics['Timeline chunks'] = document.querySelectorAll('.nx-exec-chunk').length;
    metrics['Transient files'] = document.querySelectorAll('.nx-transient-file').length;

    /* Memory */
    if (performance.memory) {
      const mb = v => Math.round(v / 1048576) + 'MB';
      metrics['JS heap used']  = mb(performance.memory.usedJSHeapSize);
      metrics['JS heap total'] = mb(performance.memory.totalJSHeapSize);
      metrics['JS heap limit'] = mb(performance.memory.jsHeapSizeLimit);
    } else {
      metrics['Memory API'] = 'unavailable';
    }

    /* NxChunker */
    if (window.NxChunker) {
      metrics['Active chunks'] = NxChunker.getGroupCount ? NxChunker.getGroupCount() : '—';
    }

    return metrics;
  }

  /* ══════════════════════════════════════════════════════════════════
     RENDER
     ══════════════════════════════════════════════════════════════════ */
  function _render() {
    if (!_visible) return;
    const metrics = _collect();
    const grid    = $('nxDiagGrid');
    const hist    = $('nxDiagHistory');
    if (!grid || !hist) return;

    grid.innerHTML = Object.entries(metrics).map(([k, v]) => `
      <div class="nx-diag-row">
        <span class="nx-diag-key">${k}</span>
        <span class="nx-diag-val">${v}</span>
      </div>
    `).join('');

    /* Bus history */
    if (window.NxBus) {
      const h = NxBus.history(10).reverse();
      hist.innerHTML = h.map(e => {
        const age = Math.round((Date.now() - e.at) / 1000);
        const evt = e.event.replace('nx:','').slice(0,24);
        return `<div class="nx-diag-hist-row"><span style="color:#484f58">${age}s</span> ${evt}</div>`;
      }).join('');
    }
  }

  /* ══════════════════════════════════════════════════════════════════
     FAILURE FORENSICS SNAPSHOT
     ══════════════════════════════════════════════════════════════════ */
  function _captureForensics() {
    const snap = {
      timestamp:      new Date().toISOString(),
      metrics:        _collect(),
      busHistory:     window.NxBus ? NxBus.history(30) : [],
      missionState:   window.NxMission ? {
        phase:     NxMission.getPhase(),
        objective: NxMission.getObjective(),
        continuity: NxMission.getContinuity(),
      } : null,
      trustState:     window.NxTrust ? {
        confidence: NxTrust.getConf(),
        memory:     NxTrust.memory(),
      } : null,
      domState: {
        inspectorNodes: $('nxInspectorContent')?.children.length,
        chunks:         document.querySelectorAll('.nx-exec-chunk').length,
        missionCard:    !!$('nxMissionCard'),
        hitlCard:       !!document.querySelector('.nx-hitl-card'),
      },
    };

    // Write to sessionStorage for retrieval
    try {
      sessionStorage.setItem('nx_forensics_last', JSON.stringify(snap));
    } catch (_) {}

    // Also print to console as a structured report
    console.group('[NxForensics] Failure Snapshot');
    console.log('Phase:', snap.missionState?.phase);
    console.log('Confidence:', snap.trustState?.confidence);
    console.log('Inspector nodes:', snap.domState.inspectorNodes);
    console.log('Full snapshot:', snap);
    console.groupEnd();

    return snap;
  }

  /* ══════════════════════════════════════════════════════════════════
     SHOW / HIDE
     ══════════════════════════════════════════════════════════════════ */
  function _show() {
    _buildPanel();
    _visible = true;
    _panel.style.display = 'flex';
    _render();
    clearInterval(_ticker);
    _ticker = setInterval(_render, 1500);
  }

  function _hide() {
    _visible = false;
    if (_panel) _panel.style.display = 'none';
    clearInterval(_ticker);
  }

  function _toggle() {
    _visible ? _hide() : _show();
  }

  /* ══════════════════════════════════════════════════════════════════
     KEYBOARD
     ══════════════════════════════════════════════════════════════════ */
  document.addEventListener('keydown', (e) => {
    if ((e.ctrlKey || e.metaKey) && e.shiftKey && e.key === 'D') {
      e.preventDefault();
      _toggle();
    }
  });

  /* ══════════════════════════════════════════════════════════════════
     BUS WIRING — capture forensics on error/escalation
     ══════════════════════════════════════════════════════════════════ */
  function _wire() {
    if (!window.NxBus) { setTimeout(_wire, 200); return; }

    NxBus.on('nx:stream:error',  () => _captureForensics(), { owner: 'nx-diagnostics' });
    NxBus.on('nx:agent:error',   () => _captureForensics(), { owner: 'nx-diagnostics' });
    NxBus.on('nx:hitl:required', () => _captureForensics(), { owner: 'nx-diagnostics' });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => setTimeout(_wire, 900));
  } else {
    setTimeout(_wire, 900);
  }

  /* Public API */
  window.NxDiag = {
    show:            _show,
    hide:            _hide,
    toggle:          _toggle,
    snapshot:        _captureForensics,
    lastForensics:   () => {
      try { return JSON.parse(sessionStorage.getItem('nx_forensics_last')); } catch (_) { return null; }
    },
  };

})();
