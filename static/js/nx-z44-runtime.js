/**
 * nx-z44-runtime.js — Phase Z44: Unified Cognitive Runtime + Execution Storytelling
 * ═══════════════════════════════════════════════════════════════════════════════════
 * Responsibilities:
 *   1. Unified 9-state runtime state machine → body[data-nx-state]
 *   2. Mission narrative strip — reads log stream for operational narrative
 *   3. Inspector advisory section — state-aware operator guidance
 *   4. Session age awareness → #nxSessionCard[data-session-age]
 *   5. Runtime storytelling feed — last N meaningful log events in inspector
 *
 * State detection strategy (all observation-based, no polling):
 *   - MutationObserver on #runBtn (class: is-running)
 *   - MutationObserver on #stStatus (textContent → backend status)
 *   - MutationObserver on #nxHitlStrip (display → HITL sub-state)
 *   - MutationObserver on #nxErrorCard (display → failed state)
 *   - MutationObserver on #logArea (childList → mission narrative)
 *
 * All mounted elements are non-destructive additions to existing structure.
 * ═══════════════════════════════════════════════════════════════════════════════════
 */
(function () {
  'use strict';

  /* ── Constants ─────────────────────────────────────────────────────── */

  const STATES = {
    IDLE:        'idle',
    RUNNING:     'running',
    QUEUED:      'queued',
    PAUSED:      'paused',
    HITL:        'hitl',
    RECOVERY:    'recovery',
    STABILIZING: 'stabilizing',
    FAILED:      'failed',
    REPLAY:      'replay',
  };

  // State → topbar/status accent color (CSS custom property set on :root)
  const STATE_COLORS = {
    idle:        '#3A3D48',
    running:     '#0079F2',
    queued:      '#525566',
    paused:      '#C28A00',
    hitl:        '#7C5DB7',
    recovery:    '#D97706',
    stabilizing: '#16A34A',
    failed:      '#C0392B',
    replay:      '#0891B2',
  };

  // State → advisory message (null = no advisory shown)
  const ADVISORIES = {
    idle:        null,
    running:     null,
    queued:      'Task queued — execution will begin when resources are available.',
    paused:      'Execution paused. Resume when ready or inject a correction below.',
    hitl:        'Agent is waiting for your input. Review the request and respond.',
    recovery:    'Recovery in progress. Monitoring for stabilization.',
    stabilizing: 'System is stabilizing after a disruption. No action required.',
    failed:      'Execution ended with an error. Review logs and retry or adjust the task.',
    replay:      'Replay mode active. Timeline is showing historical execution.',
  };

  // State → statusbar label
  const STATE_LABELS = {
    idle:        'Idle',
    running:     'Running',
    queued:      'Queued',
    paused:      'Paused',
    hitl:        'Awaiting Input',
    recovery:    'Recovering',
    stabilizing: 'Stabilizing',
    failed:      'Failed',
    replay:      'Replay',
  };

  // Log lines to skip for narrative (noise patterns)
  const NARRATIVE_SKIP = [
    /^\s*─+\s*$/,
    /heartbeat/i,
    /^ping$/i,
    /^\[poll\]/i,
    /^\[tick\]/i,
    /\[\d+ms\]/,
    /^DEBUG/,
    /^---/,
    /^===+/,
  ];

  // Log lines worth showing in narrative (signal patterns)
  const NARRATIVE_SIGNAL = [
    /writing|creating|updating|modifying/i,
    /executing|running|calling/i,
    /analyzing|inspecting|reading/i,
    /planning|deciding|routing/i,
    /fixing|patching|correcting/i,
    /testing|validating|verifying/i,
    /completed|finished|done/i,
    /error|failed|exception/i,
    /recovered|retrying|fallback/i,
    /stage|step|phase/i,
    /installing|building|compiling/i,
  ];

  /* ── State ─────────────────────────────────────────────────────────── */

  let _currentState = STATES.IDLE;
  let _storyLines = [];     // last N meaningful narrative lines
  const STORY_MAX = 5;      // max lines to keep in storytelling feed
  let _sessionStart = null; // Date.now() when session became active
  let _mounted = false;
  let _advisoryEl = null;
  let _missionEl  = null;
  let _storyEl    = null;

  /* ── Utility ───────────────────────────────────────────────────────── */

  function $id(id) { return document.getElementById(id); }
  function esc(s) {
    const d = document.createElement('div');
    d.textContent = s;
    return d.innerHTML;
  }

  /* ── State Classification ──────────────────────────────────────────── */

  function classifyState() {
    const runBtn      = $id('runBtn');
    const statusPill  = $id('stStatus');
    const hitlStrip   = $id('nxHitlStrip');
    const errorCard   = $id('nxErrorCard');

    const isRunning   = runBtn && runBtn.classList.contains('is-running');
    const statusText  = (statusPill && statusPill.textContent || '').toLowerCase().trim();
    const hitlVisible = hitlStrip && hitlStrip.style.display !== 'none' && hitlStrip.style.display !== '';
    const errVisible  = errorCard && errorCard.style.display !== 'none' && errorCard.style.display !== '';

    // HITL takes priority over generic running
    if (isRunning && hitlVisible) return STATES.HITL;

    if (isRunning) {
      if (statusText.includes('recover')) return STATES.RECOVERY;
      if (statusText.includes('stabiliz')) return STATES.STABILIZING;
      if (statusText.includes('paus')) return STATES.PAUSED;
      return STATES.RUNNING;
    }

    // Not running
    if (statusText.includes('replay')) return STATES.REPLAY;
    if (statusText.includes('queue')) return STATES.QUEUED;
    if (statusText.includes('error') || statusText.includes('fail') || errVisible) {
      return STATES.FAILED;
    }
    if (statusText.includes('stabiliz')) return STATES.STABILIZING;

    return STATES.IDLE;
  }

  /* ── State Application ─────────────────────────────────────────────── */

  function applyState(state) {
    if (state === _currentState) return;
    _currentState = state;

    // Primary attribute (Z44)
    document.body.dataset.nxState = state;

    // Backward-compat Z43 exec attribute
    document.body.dataset.nxExec = (state === STATES.RUNNING || state === STATES.HITL ||
                                    state === STATES.RECOVERY || state === STATES.STABILIZING)
      ? 'running' : 'idle';

    // CSS color token for state accent
    document.documentElement.style.setProperty('--z44-state-color', STATE_COLORS[state] || '#3A3D48');

    // Update advisory
    updateAdvisory(state);

    // Track session start time
    if (state === STATES.RUNNING && !_sessionStart) {
      _sessionStart = Date.now();
    } else if (state === STATES.IDLE || state === STATES.FAILED) {
      updateSessionAge();
      _sessionStart = null;
    }
  }

  /* ── Observers ─────────────────────────────────────────────────────── */
  /* Z56: Consolidated — one shared MutationObserver for all state-bearing
     elements instead of four separate instances. Reduces active observer
     count by 3 (was watchRunBtn + watchStatusPill + watchHitlStrip +
     watchErrorCard = 4; now 1). Each element keeps its own observe() call
     with the exact same options as before.                                */

  let _stateObserver = null;

  function watchStateElements(runBtn, statusPill, hitlStrip, errorCard) {
    if (_stateObserver) _stateObserver.disconnect();
    _stateObserver = new MutationObserver(() => applyState(classifyState()));
    if (runBtn)     _stateObserver.observe(runBtn,     { attributes: true, attributeFilter: ['class'] });
    if (statusPill) _stateObserver.observe(statusPill, { childList: true, characterData: true, subtree: true });
    if (hitlStrip)  _stateObserver.observe(hitlStrip,  { attributes: true, attributeFilter: ['style'] });
    if (errorCard)  _stateObserver.observe(errorCard,  { attributes: true, attributeFilter: ['style'] });
  }

  function watchLogArea(el) {
    const mo = new MutationObserver((mutations) => {
      for (const m of mutations) {
        for (const node of m.addedNodes) {
          if (node.nodeType === 1 && node.classList.contains('log-line')) {
            processLogLine(node.textContent || '');
          }
        }
      }
    });
    mo.observe(el, { childList: true });
  }

  /* ── Mission Narrative ─────────────────────────────────────────────── */

  function isNarrativeSignal(text) {
    if (!text || text.length < 5) return false;
    for (const re of NARRATIVE_SKIP) { if (re.test(text)) return false; }
    for (const re of NARRATIVE_SIGNAL) { if (re.test(text)) return true; }
    return false;
  }

  function processLogLine(text) {
    text = text.replace(/^\[[\w:]+\]\s*/, '').trim(); // strip level prefix
    if (!isNarrativeSignal(text)) return;

    // Update mission strip (last line only)
    if (_missionEl) {
      const txt = _missionEl.querySelector('.nx-mission-text');
      if (txt) {
        txt.textContent = text.length > 120 ? text.slice(0, 117) + '…' : text;
      }
    }

    // Accumulate into storytelling feed
    _storyLines.push({ text, ts: Date.now() });
    if (_storyLines.length > STORY_MAX) _storyLines.shift();
    renderStoryFeed();
  }

  /* ── Session Age ───────────────────────────────────────────────────── */

  function updateSessionAge() {
    const card = $id('nxSessionCard');
    if (!card) return;
    const sbSess = $id('nxSbSess');
    const sessText = sbSess ? sbSess.textContent : '';

    // Simple heuristic from statusbar session text — if it contains elapsed info
    // we can derive age. Otherwise default to 'active'.
    const elapsed = _sessionStart ? Math.floor((Date.now() - _sessionStart) / 60000) : 0;

    let age = 'idle';
    if (_sessionStart) {
      if (elapsed < 5)  age = 'fresh';
      else if (elapsed < 30) age = 'active';
      else age = 'long';
    }
    card.dataset.sessionAge = age;
  }

  // Refresh session age every 2 minutes during active run
  setInterval(() => {
    if (_currentState === STATES.RUNNING) updateSessionAge();
  }, 120000);

  /* ── DOM Mounting ──────────────────────────────────────────────────── */

  function mountMissionStrip() {
    if ($id('nx-mission-strip')) return;

    const strip = document.createElement('div');
    strip.id = 'nx-mission-strip';
    strip.className = 'nx-mission-strip';
    strip.innerHTML = `
      <span class="nx-mission-icon" aria-hidden="true"></span>
      <span class="nx-mission-text">Ready.</span>
      <span class="nx-mission-state"></span>
    `;
    _missionEl = strip;

    // Insert between composer and tab bar
    const tabBar = $id('nxTabBar');
    if (tabBar && tabBar.parentElement) {
      tabBar.parentElement.insertBefore(strip, tabBar);
    }
  }

  function mountAdvisory() {
    if ($id('nx-advisory')) return;

    const adv = document.createElement('div');
    adv.id = 'nx-advisory';
    adv.className = 'nx-advisory';
    adv.style.display = 'none';
    adv.innerHTML = `
      <span class="nx-advisory-icon" aria-hidden="true">◈</span>
      <span class="nx-advisory-text"></span>
    `;
    _advisoryEl = adv;

    // Inject at top of inspector right panel body
    const rightBody = $id('nxRightBody');
    if (rightBody && rightBody.firstChild) {
      rightBody.insertBefore(adv, rightBody.firstChild);
    } else if (rightBody) {
      rightBody.appendChild(adv);
    }
  }

  function mountStoryFeed() {
    if ($id('nx-story-section')) return;

    const sec = document.createElement('div');
    sec.id = 'nx-story-section';
    sec.className = 'nx-inspector-section nx-story-section';
    sec.innerHTML = `
      <div class="nx-insp-label">Execution Narrative</div>
      <div class="nx-story-feed" id="nx-story-feed">
        <div class="nx-story-empty">No activity yet.</div>
      </div>
    `;
    _storyEl = sec;

    // Append to right body — at end, before download section
    const rightBody = $id('nxRightBody');
    const dlSec = $id('nxDownloadSection');
    if (rightBody && dlSec) {
      rightBody.insertBefore(sec, dlSec);
    } else if (rightBody) {
      rightBody.appendChild(sec);
    }
  }

  /* ── Advisory Rendering ────────────────────────────────────────────── */

  function updateAdvisory(state) {
    if (!_advisoryEl) return;
    const msg = ADVISORIES[state];
    if (!msg) {
      _advisoryEl.style.display = 'none';
      return;
    }
    const txt = _advisoryEl.querySelector('.nx-advisory-text');
    if (txt) txt.textContent = msg;
    _advisoryEl.dataset.nxAdvisoryState = state;
    _advisoryEl.style.display = 'flex';

    // Update mission strip state label
    if (_missionEl) {
      const stLabel = _missionEl.querySelector('.nx-mission-state');
      if (stLabel) {
        stLabel.textContent = STATE_LABELS[state] || '';
        stLabel.dataset.state = state;
      }
    }
  }

  /* ── Storytelling Feed Rendering ───────────────────────────────────── */

  function renderStoryFeed() {
    const feed = $id('nx-story-feed');
    if (!feed) return;

    if (!_storyLines.length) {
      feed.innerHTML = '<div class="nx-story-empty">No activity yet.</div>';
      return;
    }

    feed.innerHTML = _storyLines.slice().reverse().map(({ text, ts }) => {
      const age = Math.floor((Date.now() - ts) / 1000);
      const ageStr = age < 60 ? `${age}s ago` : `${Math.floor(age / 60)}m ago`;
      return `<div class="nx-story-line">
        <span class="nx-story-text">${esc(text)}</span>
        <span class="nx-story-age">${esc(ageStr)}</span>
      </div>`;
    }).join('');
  }

  // Refresh story timestamps every 30s
  setInterval(renderStoryFeed, 30000);

  /* ── Bootstrap ─────────────────────────────────────────────────────── */

  function attachAll() {
    const runBtn    = $id('runBtn');
    const stStatus  = $id('stStatus');
    const hitlStrip = $id('nxHitlStrip');
    const errorCard = $id('nxErrorCard');
    const logArea   = $id('logArea');

    watchStateElements(runBtn, stStatus, hitlStrip, errorCard);
    if (logArea) watchLogArea(logArea);

    // Initial state sync
    applyState(classifyState());

    // Mount UI surfaces
    mountMissionStrip();
    mountAdvisory();
    mountStoryFeed();

    _mounted = true;
  }

  function init() {
    // Wait for the key elements to exist (they're inside the main closure)
    const requiredIds = ['runBtn', 'stStatus', 'nxRightBody', 'logArea'];
    const allReady = () => requiredIds.every(id => !!$id(id));

    if (allReady()) { attachAll(); return; }

    const observer = new MutationObserver(() => {
      if (allReady()) { observer.disconnect(); attachAll(); }
    });
    observer.observe(document.body, { childList: true, subtree: true });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    // Small delay to let the main runtime JS finish setting up its closure
    setTimeout(init, 200);
  }

  // Expose minimal public API for other scripts
  window.nxZ44 = {
    getState: () => _currentState,
    setState: (s) => { if (STATES[s.toUpperCase()]) applyState(s); },
    STATES,
  };
})();
