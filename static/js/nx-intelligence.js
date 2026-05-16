/**
 * nx-intelligence.js — Nexora Operational Intelligence v1.1
 * ═══════════════════════════════════════════════════════════════════
 * Phase R: Analytics, forensics, feedback.
 * Phase S: Cross-session reliability memory, operator trust calibration,
 *          semantic execution quality scoring.
 * ═══════════════════════════════════════════════════════════════════
 */
(function () {
  'use strict';

  const $ = id => document.getElementById(id);
  const STORAGE_KEY_FAILURES = 'nx_failures_v1';

  /* ══════════════════════════════════════════════════════════════════
     1. SESSION FORENSICS & RESOURCE PROFILING EXPORT
     ══════════════════════════════════════════════════════════════════ */
  function exportForensics() {
    const snap = window.NxDiag ? NxDiag.snapshot() : {};
    const analytics = window.NxAnalytics ? window.NxAnalytics.report() : [];
    
    // Gather timeline DOM state (anonymized/structural)
    const chunks = Array.from(document.querySelectorAll('.nx-exec-chunk')).map(ch => ({
      kind: ch.dataset.kind,
      label: ch.querySelector('.nx-kind-label')?.textContent,
      contentLength: ch.querySelector('.nx-chunk-content')?.textContent.length || 0,
      hasError: !!ch.querySelector('.nx-chunk-error')
    }));

    const qualityScore = NxFailureIntel.calculateExecutionQuality(chunks, snap);
    const envInfo = {
      userAgent: navigator.userAgent,
      screenSize: `${window.innerWidth}x${window.innerHeight}`,
      memoryLimit: performance?.memory?.jsHeapSizeLimit || 'unknown',
      hardwareConcurrency: navigator.hardwareConcurrency || 'unknown'
    };

    const data = {
      timestamp: new Date().toISOString(),
      agent_version: 'Nexora v0.9-beta',
      beta_cohort: 'cohort-1-internal',
      environment: envInfo,
      execution_quality: qualityScore,
      metrics: snap.metrics || {},
      mission: snap.missionState || {},
      trust: snap.trustState || {},
      analytics: analytics,
      cross_session_failures: NxFailureIntel.getReport(),
      timeline_chunks: chunks,
      bus_history: snap.busHistory || []
    };

    const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `nx_session_forensics_${Date.now()}.json`;
    a.click();
    URL.revokeObjectURL(url);
  }

  /* ══════════════════════════════════════════════════════════════════
     2. CROSS-SESSION FAILURE INTELLIGENCE & TRUST CALIBRATION
     — Persists to localStorage to remember flaky files/tools.
     ══════════════════════════════════════════════════════════════════ */
  const NxFailureIntel = (() => {
    function _load() {
      try { return JSON.parse(localStorage.getItem(STORAGE_KEY_FAILURES) || '{"files":{},"tools":{},"escalations":0,"overrides":0}'); }
      catch(_) { return {files:{},tools:{},escalations:0,overrides:0}; }
    }
    function _save(d) {
      try { localStorage.setItem(STORAGE_KEY_FAILURES, JSON.stringify(d)); } catch(_) {}
    }

    function recordFailure(type, target) {
      const d = _load();
      if (!d[type]) d[type] = {};
      
      if (typeof d[type] === 'number') {
        d[type]++;
      } else {
        d[type][target] = (d[type][target] || 0) + 1;
        // Flag flaky (3+)
        if (d[type][target] >= 3 && window.NxClarity?.analytics) {
          window.NxClarity.analytics.track('flaky_detected', `${type}:${target}`);
        }
      }
      _save(d);
    }

    // Phase S Execution Quality Scoring (0-100)
    function calculateExecutionQuality(chunks, snap) {
      let score = 100;
      let notes = [];
      
      const errorChunks = chunks.filter(c => c.hasError || c.kind === 'recovery').length;
      if (errorChunks > 0) {
        score -= (errorChunks * 10);
        notes.push(`${errorChunks} recovery/error events detected (-${errorChunks*10})`);
      }

      const hitlChunks = chunks.filter(c => c.kind === 'escalation').length;
      if (hitlChunks > 1) {
        score -= (hitlChunks * 5);
        notes.push(`High operator burden: ${hitlChunks} escalations (-${hitlChunks*5})`);
      }

      const d = _load();
      if (d.overrides > 0) {
        score -= 5;
        notes.push(`Trust calibration penalty: past overrides detected (-5)`);
      }

      if (snap && snap.missionState && snap.missionState.phase === 'completed') {
        notes.push('Mission reached completed state (+0)');
      } else {
        score -= 40;
        notes.push('Mission failed or abandoned (-40)');
      }

      return {
        score: Math.max(0, score),
        semantic_notes: notes
      };
    }

    return { record: recordFailure, getReport: _load, calculateExecutionQuality };
  })();

  /* ══════════════════════════════════════════════════════════════════
     3. OPERATOR FEEDBACK SYSTEM
     ══════════════════════════════════════════════════════════════════ */
  function _injectFeedbackUI(container) {
    if (container.querySelector('.nx-feedback-widget')) return;

    const widget = document.createElement('div');
    widget.className = 'nx-feedback-widget';
    widget.innerHTML = `
      <div class="nx-fw-title">Semantic Execution Quality</div>
      <div class="nx-fw-buttons">
        <button class="nx-fw-btn" data-fb="useful">✓ Fully Solved</button>
        <button class="nx-fw-btn" data-fb="incorrect">✗ Incorrect Outcome</button>
        <button class="nx-fw-btn" data-fb="slow">⏱ Overly Complex</button>
        <button class="nx-fw-btn" data-fb="confusing">? Confusing Logs</button>
      </div>
    `;

    widget.querySelectorAll('.nx-fw-btn').forEach(b => {
      b.onclick = () => {
        const val = b.dataset.fb;
        if (window.NxClarity && window.NxClarity.analytics) {
          window.NxClarity.analytics.track('semantic_feedback', val);
        }
        widget.innerHTML = `<div class="nx-fw-title" style="color:#3fb950">Semantic quality recorded.</div>`;
      };
    });

    container.appendChild(widget);
  }

  /* ══════════════════════════════════════════════════════════════════
     4. BUS WIRING
     ══════════════════════════════════════════════════════════════════ */
  function _wire() {
    if (!window.NxBus) return;
    const E = NxBus.EVENTS;

    // Track failures & trust overrides
    NxBus.on(E.STREAM_ERROR, () => NxFailureIntel.record('tools', 'stream'), { owner: 'nx-intel' });
    NxBus.on('nx:hitl:required', () => NxFailureIntel.record('escalations', null), { owner: 'nx-intel' });
    
    // Intercept UI clicks on HITL Reject as "Overrides" (Trust calibration)
    document.addEventListener('click', (e) => {
      if (e.target && e.target.textContent && e.target.textContent.includes('Cancel action')) {
         NxFailureIntel.record('overrides', null);
         if (window.NxClarity?.analytics) window.NxClarity.analytics.track('trust_override');
      }
    });

    // Inject feedback on mission completion
    NxBus.on(E.AGENT_DONE, () => {
      const execStream = $('nxExecutionStream') || $('nxTab-logs');
      if (execStream) _injectFeedbackUI(execStream);
    }, { owner: 'nx-intel' });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => setTimeout(_wire, 1000));
  } else {
    setTimeout(_wire, 1000);
  }

  // Expose API
  window.NxIntel = {
    exportForensics,
    failures: NxFailureIntel
  };

})();
