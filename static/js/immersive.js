/* ═══════════════════════════════════════════════════════════════════
     IMMERSIVE AI EXECUTION SYSTEM
     - AI Activity Bar: real-time file write tracking, state, controls
     - "AI editing" banner injected into Code tab on file writes
     - Auto-loads written file into Monaco editor (when on Code tab)
     - Wires Pause/Resume/Stop into activity bar buttons
     - All data from real SSE stream — zero faking
     ═══════════════════════════════════════════════════════════════════ */
  (function () {
    'use strict';

    const FILE_WRITE_RE = /\[FILE_(?:WRITE|EDIT)\]\s*([^\s(]+)/i;

    /* ── State ─────────────────────────────────────────────────────── */
    let _isRunning = false;
    let _stepCount = 0;
    let _lastFile = null;
    let _editBannerTm = null;
    let _editingBanner = null;  // injected banner DOM node

    /* ── DOM helpers ──────────────────────────────────────────────── */
    function _el(id) { return document.getElementById(id); }

    function _abShow() {
      const bar = _el('nxActivityBar');
      if (bar) bar.style.display = 'flex';
      _isRunning = true;
      _abSetState('running');
      _showBtn('nxAbPauseBtn');
      _showBtn('nxAbStopBtn');
      _hideEl('nxAbResumeBtn');
      _showEl('nxAbStepsBadge');
    }

    function _abHide() {
      _isRunning = false;
      setTimeout(() => {
        const bar = _el('nxActivityBar');
        if (bar) bar.style.display = 'none';
        _hideEditBanner();
        _hideEl('nxAbStepsBadge');
      }, 5000);
    }

    function _showEl(id) { const e = _el(id); if (e) e.style.display = ''; }
    function _hideEl(id) { const e = _el(id); if (e) e.style.display = 'none'; }
    function _showBtn(id) { const e = _el(id); if (e) e.style.display = ''; }

    function _abSetState(state) {
      const dot = _el('nxAbDot');
      if (!dot) return;
      dot.className = 'nx-ab-dot';
      const map = {
        coding: 'nx-ab-coding', planning: 'nx-ab-planning',
        debugging: 'nx-ab-debug', paused: 'nx-ab-paused', done: 'nx-ab-done'
      };
      if (map[state]) dot.classList.add(map[state]);
    }

    function _abSetText(txt) {
      const e = _el('nxAbText');
      if (e) e.textContent = txt;
    }

    function _abSetFile(path) {
      const fEl = _el('nxAbFile');
      const oBtn = _el('nxAbFileOpen');
      if (!path) {
        if (fEl) { fEl.style.display = 'none'; fEl.textContent = ''; }
        if (oBtn) oBtn.style.display = 'none';
        return;
      }
      const name = path.split('/').pop() || path;
      if (fEl) { fEl.textContent = name; fEl.title = path; fEl.style.display = ''; }
      if (oBtn) oBtn.style.display = '';
      _lastFile = path;
    }

    /* ── AI Editing Banner (injected into Code tab body) ─────────── */
    function _ensureEditBanner() {
      if (_editingBanner && _editingBanner.parentNode) return;
      _editingBanner = document.createElement('div');
      _editingBanner.id = 'nxAiEditingBanner';
      _editingBanner.innerHTML =
        '<span class="nx-aeb-pulse"></span>' +
        '<span>AI wrote</span>' +
        '<span id="nxAebFile" style="font-family:monospace;font-size:10px;' +
        'background:rgba(139,92,246,.12);border:1px solid rgba(139,92,246,.25);' +
        'border-radius:3px;padding:1px 7px;color:#bc8cff"></span>' +
        '<button onclick="nxAbOpenFile()" style="font-size:10px;background:none;' +
        'border:none;color:#8b5cf6;cursor:pointer;text-decoration:underline;' +
        'text-underline-offset:2px;padding:0 4px">Open ↗</button>' +
        '<div style="flex:1"></div>' +
        '<button onclick="this.closest(\'#nxAiEditingBanner\').style.display=\'none\'" ' +
        'style="background:none;border:none;color:var(--text-dim);cursor:pointer;' +
        'font-size:13px;line-height:1;padding:0 2px">&times;</button>';
    }

    function _showEditBanner(file) {
      _ensureEditBanner();

      /* Inject banner at top of the Code tab content area */
      const codeTab = _el('nxTab-code');
      if (codeTab && !codeTab.contains(_editingBanner)) {
        codeTab.insertBefore(_editingBanner, codeTab.firstChild);
      }

      const fEl = document.getElementById('nxAebFile');
      if (fEl) fEl.textContent = (file || '').split('/').pop() || file || '';

      _editingBanner.style.display = 'flex';
      clearTimeout(_editBannerTm);
      _editBannerTm = setTimeout(() => _hideEditBanner(), 8000);

      /* Flash the Code tab button */
      const codeTabBtn = document.querySelector('[data-nxtab="code"]');
      if (codeTabBtn) {
        codeTabBtn.style.animation = 'nx-tab-file-flash .6s ease';
        setTimeout(() => { codeTabBtn.style.animation = ''; }, 700);
      }
    }

    function _hideEditBanner() {
      if (_editingBanner) _editingBanner.style.display = 'none';
    }

    /* ── Auto-load file into Monaco (silently, no unsaved-change prompt) */
    async function _silentLoadFile(path) {
      if (!path) return;
      /* Only switch if the user is currently on the Code tab — no forced tab switch */
      const codeTabBtn = document.querySelector('.nx-tab.active[data-nxtab="code"]');
      if (!codeTabBtn) return;         /* not on Code tab — don't disrupt */
      try {
        /* Update the shared pointer so the Files pane knows what to highlight */
        if (typeof loadFilesTree === 'function') await loadFilesTree();
        if (typeof openFileFromTree === 'function') {
          /* Skip guard by setting openFilePath to null first (safe — no dirty check) */
          const cur = (typeof openFilePath !== 'undefined') ? openFilePath : null;
          if (cur !== path) {
            window.openFilePath = path;
            await openFileFromTree(path);
          }
        }
      } catch (e) { /* silent */ }
    }

    /* ── Public open-file handler for "Open ↗" buttons ─────────────── */
    window.nxAbOpenFile = async function () {
      if (!_lastFile) return;
      nxSetTab('code');
      try {
        if (typeof loadFilesTree === 'function') await loadFilesTree();
        if (typeof openFileFromTree === 'function') {
          window.openFilePath = _lastFile;
          await openFileFromTree(_lastFile);
        }
      } catch (e) { }
    };

    /* ── Event handlers ──────────────────────────────────────────── */
    function _onRow(row) {
      if (!_isRunning) return;
      const text = row.text || '';

      /* File write event */
      const fwm = FILE_WRITE_RE.exec(text);
      if (fwm) {
        const path = fwm[1];
        _abSetText('AI wrote:');
        _abSetFile(path);
        _abSetState('coding');
        _showEditBanner(path);
        _silentLoadFile(path);
        _stepCount++;
        const sc = _el('nxAbSteps');
        if (sc) sc.textContent = _stepCount;
        return;
      }

      /* Stage / state hints */
      const tl = text.toLowerCase();
      if (tl.includes('[stage] planning') || tl.includes('▶ starting task') || tl.includes('starting task')) {
        _abSetText('AI is planning…'); _abSetFile(null); _abSetState('planning');
      } else if (tl.includes('[stage] cod') || tl.includes('[stage] writ')) {
        _abSetText('AI is coding…'); _abSetState('coding');
      } else if (tl.includes('[stage] debug') || tl.includes('[stage] fix')) {
        _abSetText('AI is debugging…'); _abSetState('debugging');
      } else if (tl.includes('[stage] run') || tl.includes('[stage] execut')) {
        _abSetText('Running commands…'); _abSetState('coding');
      } else if (tl.includes('[stage] done') || tl.includes('[stage] complet') || tl.includes('[final check]')) {
        _abSetText('Completed'); _abSetFile(null); _abSetState('done');
      } else if (tl.includes('[retry')) {
        _abSetText('Retrying step…'); _abSetState('debugging');
      } else if (tl.includes('[error') || text.includes('❌')) {
        _abSetText('Error encountered'); _abSetState('debugging');
      }
    }

    function _onSessionStart(sid) {
      _stepCount = 0; _lastFile = null;
      _abSetText('AI is starting…');
      _abSetFile(null);
      _abSetState('running');
      _abShow();
      const sc = _el('nxAbSteps'); if (sc) sc.textContent = '0';
    }

    function _onSessionEnd(status) {
      const label = {
        completed: '✓ Completed', stopped: '■ Stopped',
        cancelled: '✕ Cancelled', error: '✗ Error'
      }[status] || 'Session ended';
      _abSetText(label);
      _abSetState('done');
      _hideEl('nxAbPauseBtn'); _hideEl('nxAbResumeBtn'); _hideEl('nxAbStopBtn');
      setTimeout(_hideEditBanner, 2000);
      _abHide();
    }

    /* ── Patch hitlSetPaused to sync activity bar pause/resume state ── */
    function _patchHitlSetPaused() {
      const orig = window.hitlSetPaused;
      if (typeof orig !== 'function') return;
      window.hitlSetPaused = function (paused) {
        orig(paused);
        const pb = _el('nxAbPauseBtn'), rb = _el('nxAbResumeBtn');
        if (pb) pb.style.display = paused ? 'none' : '';
        if (rb) rb.style.display = paused ? '' : 'none';
        _abSetState(paused ? 'paused' : 'running');
        _abSetText(paused ? '⏸ Agent paused' : 'AI is working…');
      };
    }

    /* ── Extend NxExecVis public API ─────────────────────────────── */
    function _init() {
      if (!window.NxExecVis) { setTimeout(_init, 150); return; }

      const _origRow = window.NxExecVis.onRow;
      const _origStart = window.NxExecVis.onSessionStart;
      const _origEnd = window.NxExecVis.onSessionEnd;

      window.NxExecVis.onRow = function (row) {
        if (_origRow) _origRow(row);
        try { _onRow(row); } catch (e) { }
      };
      window.NxExecVis.onSessionStart = function (sid) {
        if (_origStart) _origStart(sid);
        try { _onSessionStart(sid); } catch (e) { }
      };
      window.NxExecVis.onSessionEnd = function (status) {
        if (_origEnd) _origEnd(status);
        try { _onSessionEnd(status); } catch (e) { }
      };

      _patchHitlSetPaused();
      console.debug('[Immersive] active');
    }

    window.NX_LOAD_TASKS.push( _init);

  })();
