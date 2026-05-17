/* ═══════════════════════════════════════════════════════════════════
     PHASE 33 — Real-Time AI Execution Visualization
     Drives: pipeline bar, live code stream, live terminal stream.
     Data source: existing /api/session/<sid>/stream SSE (no backend
     changes needed). Hooks into ingestLogRow() to intercept every log
     row the moment it arrives.
     ═══════════════════════════════════════════════════════════════════ */
  (function () {
    'use strict';

    /* ── Phase detection patterns ───────────────────────────────────── */
    const PHASE_PATTERNS = {
      planning: [
        /\[STAGE\]\s*(plan|think|analy|design|understand|decompos)/i,
        /\bTHINK(?:ING)?\b/,
        /▶\s*Starting task/i,
        /\bplanning\b/i,
        /\banalyzing\b/i,
        /\bbreaking\s+down\b/i,
      ],
      coding: [
        /\[STAGE\]\s*(cod|impl|generat|writ|build|creat)/i,
        /\[STEP\s+\d/i,
        /Writing\s+(?:file|to)/i,
        /Creating\s+(?:file)/i,
        /\[EXECUTE\]/i,
        /\bimplementing\b/i,
        /\bgenerating\b/i,
      ],
      debugging: [
        /\[STAGE\]\s*(debug|test|fix|verif|retry|patch)/i,
        /\[RETRY/i,
        /\[VALIDATION\]/i,
        /\[FINAL CHECK\]/i,
        /\[FALLBACK\]/i,
        /\[ERROR\]/i,
        /\bdebugging\b/i,
        /\bfixing\b/i,
        /\bretrying\b/i,
      ],
      done: [
        /\[Task finished/i,
        /exit=\d+\s+status=success/i,
        /\bTask completed successfully\b/i,
        /✅.*(?:done|complete|success)/i,
      ],
    };

    /* ── Code line detection ───────────────────────────────────────── */
    const CODE_LINE_RE = [
      /^(def |class |import |from |async def |export |export default )/,
      /^(function |const |let |var |return |if\s*\(|for\s*\(|while\s*\()/,
      /^(\s{4}|\t)[\w"'$({[\-@]/,
      /^(#!\/|#\s|\/\/|\/\*|\*\s|\*\/)/,
      /^[{}()\[\]]/,
      /^<\/?[a-zA-Z]/,
      /```/,
      /^\s*(public|private|protected|static|class|interface|struct|fn |impl )/,
      /^(SELECT|INSERT|UPDATE|DELETE|CREATE|ALTER|DROP)\b/i,
    ];
    function _isCodeLine(text) {
      return CODE_LINE_RE.some(re => re.test(text));
    }

    /* ── Terminal line detection ─────────────────────────────────────── */
    const TERM_CMD_RE = /^(\$\s+|>\s+|Running:\s+|Executing:\s+|CMD:\s+|>>\s+)/;
    const TERM_SHELL_RE = /^(pip\s+|npm\s+|node\s+|python\s+|python3\s+|bash\s+|sh\s+|git\s+|mkdir\s+|cd\s+|ls\s+|cat\s+|echo\s+|curl\s+|wget\s+|make\s+|cargo\s+|go\s+run\s+|apt\s+|sudo\s+)/i;
    function _isTermLine(text) {
      return TERM_CMD_RE.test(text) || TERM_SHELL_RE.test(text);
    }

    /* ── File write detection ────────────────────────────────────────── */
    const FILE_WRITE_RE = /(?:Writing|Creating|Saving|Wrote)\s+(?:file\s+)?['"]?([^\s'"]+\.\w+)['"]?/i;
    const FILE_PATH_RE = /(?:^|\s)(['"]?)([/\w.-]+\.\w+)\1(?:\s|$)/;

    /* ── State ───────────────────────────────────────────────────────── */
    const S = {
      phase: 'idle',    // current execution phase
      lineCount: 0,
      codeCount: 0,
      cmdCount: 0,
      lastFile: '',
      codeBuffer: [],       // queued code spans not yet flushed
      termBuffer: [],       // queued terminal spans not yet flushed
      flushTimer: null,
      active: false,    // true while a session is running
      initDone: false,
    };

    /* ── DOM helpers ─────────────────────────────────────────────────── */
    function _qs(id) { return document.getElementById(id); }
    function _esc(s) { return String(s || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;'); }
    function _setVal(id, v) { const n = _qs(id); if (n) n.textContent = v; }

    /* ── Pipeline bar update (both Logs tab mini-bar + Live tab bar) ─── */
    const PHASE_ORDER = ['planning', 'coding', 'debugging', 'done'];

    function _setPipeline(phase) {
      if (phase === S.phase && phase !== 'idle') return;
      S.phase = phase;

      const phaseIdx = PHASE_ORDER.indexOf(phase);

      // Update both the Logs tab mini-pipeline and the Live tab pipeline
      [
        { prefix: 'nlp-', phases: PHASE_ORDER },
        { prefix: 'nls-', phases: PHASE_ORDER },
      ].forEach(({ prefix }) => {
        PHASE_ORDER.forEach((p, i) => {
          const el = _qs(prefix + p);
          if (!el) return;
          el.classList.remove('active', 'done', 'error');
          if (phase === 'error' && i <= phaseIdx) {
            el.classList.add(i === phaseIdx ? 'error' : 'done');
          } else if (i < phaseIdx) {
            el.classList.add('done');
          } else if (i === phaseIdx) {
            el.classList.add('active');
          }
        });
      });

      _setVal('nxLiveStageVal', phase === 'idle' ? '—' : phase);

      // Show Logs tab pipeline bar when active
      const logsPipe = _qs('nxLogsPipeline');
      if (logsPipe) {
        logsPipe.style.display = (phase !== 'idle') ? 'flex' : 'none';
      }
    }

    /* ── Detect phase from a log row ─────────────────────────────────── */
    function _detectPhase(text) {
      for (const [phase, patterns] of Object.entries(PHASE_PATTERNS)) {
        if (patterns.some(re => re.test(text))) return phase;
      }
      return null;
    }

    /* ── Stream code line ────────────────────────────────────────────── */
    let _lastFileLabel = '';
    function _appendCode(text, file) {
      if (file && file !== _lastFileLabel) {
        _lastFileLabel = file;
        S.codeBuffer.push(`<span class="nx-code-file">// ${_esc(file)}</span>`);
        const lbl = _qs('nxCodeFileLabel');
        if (lbl) lbl.textContent = file;
      }
      S.codeBuffer.push(`<span class="nx-code-chunk">${_esc(text)}</span>\n`);
      S.codeCount++;

      // Activate code stream dot
      const dot = _qs('nxCodeStreamDot');
      if (dot) dot.classList.add('active');
      _scheduleFlush();
    }

    /* ── Stream terminal line ────────────────────────────────────────── */
    function _termClass(text, level) {
      if (level === 'error' || /\[error\]/i.test(text) || /\btraceback\b/i.test(text)) return 'nx-term-err';
      if (level === 'success' || /✅/.test(text)) return 'nx-term-ok';
      if (level === 'validation') return 'nx-term-warn';
      if (level === 'system') return 'nx-term-system';
      if (level === 'info') return 'nx-term-info';
      if (TERM_CMD_RE.test(text)) return 'nx-term-cmd';
      if (/warning|warn/i.test(text)) return 'nx-term-warn';
      return '';
    }

    function _appendTerm(text, level) {
      const cls = _termClass(text, level);
      S.termBuffer.push(`<span class="nx-term-line ${cls}">${_esc(text)}</span>`);
      if (TERM_CMD_RE.test(text) || TERM_SHELL_RE.test(text)) S.cmdCount++;
      const dot = _qs('nxTermStreamDot');
      if (dot) dot.classList.add('active');
      _scheduleFlush();
    }

    /* ── Batched DOM flush (max 60fps) ─────────────────────────────────*/
    function _scheduleFlush() {
      if (S.flushTimer) return;
      S.flushTimer = requestAnimationFrame(_flush);
    }

    function _flush() {
      S.flushTimer = null;

      if (S.codeBuffer.length) {
        const area = _qs('nxLiveCodeArea');
        if (area) {
          if (area.querySelector('span[style]')) area.innerHTML = '';
          const html = S.codeBuffer.join('');
          area.insertAdjacentHTML('beforeend', html);
          const as = _qs('nxCodeAutoScroll');
          if (!as || as.checked) area.scrollTop = area.scrollHeight;
        }
        S.codeBuffer = [];
      }

      if (S.termBuffer.length) {
        const area = _qs('nxLiveTermArea');
        if (area) {
          if (area.querySelector('span[style]')) area.innerHTML = '';
          area.insertAdjacentHTML('beforeend', S.termBuffer.join(''));
          const as = _qs('nxTermAutoScroll');
          if (!as || as.checked) area.scrollTop = area.scrollHeight;
        }
        S.termBuffer = [];
      }

      // Update counters
      _setVal('nxLiveLineCount', S.lineCount);
      _setVal('nxLiveCodeCount', S.codeCount);
      _setVal('nxLiveCmdCount', S.cmdCount);
    }

    /* ── Process a single log row (called from ingestLogRow hook) ─────── */
    function _onRow(row) {
      if (!row || typeof row.text !== 'string') return;
      const text = row.text;
      const level = row.level || 'log';
      S.lineCount++;

      // Phase detection (but not from the initial "starting" message alone)
      const detectedPhase = _detectPhase(text);
      if (detectedPhase) {
        // Phase transitions are sticky — don't go backwards except to done
        const cur = PHASE_ORDER.indexOf(S.phase);
        const nxt = PHASE_ORDER.indexOf(detectedPhase);
        if (detectedPhase === 'done' || nxt >= cur) {
          _setPipeline(detectedPhase);
        }
      }

      // Check for file being written
      let currentFile = S.lastFile;
      const fm = FILE_WRITE_RE.exec(text);
      if (fm) currentFile = S.lastFile = fm[1];

      // Classify line: code | terminal | general
      if (level === 'system' || level === 'info') {
        // System lines go to terminal view as informational
        _appendTerm(text, level);
      } else if (level === 'error') {
        _appendTerm(text, level);
      } else if (level === 'success' || level === 'validation') {
        _appendTerm(text, level);
      } else if (_isCodeLine(text)) {
        _appendCode(text, currentFile);
      } else if (_isTermLine(text)) {
        _appendTerm(text, level);
      } else {
        // Generic log lines: short ones look like terminal output,
        // longer ones with code smell → code pane
        if (text.length < 120 && !text.startsWith(' ')) {
          _appendTerm(text, level);
        } else {
          _appendCode(text, currentFile);
        }
      }
    }

    /* ── Session lifecycle hooks ─────────────────────────────────────── */
    function _onSessionStart(sid) {
      S.active = true;
      S.phase = 'idle';
      S.lineCount = 0;
      S.codeCount = 0;
      S.cmdCount = 0;
      S.lastFile = '';
      _lastFileLabel = '';
      S.codeBuffer = [];
      S.termBuffer = [];

      // Reset pipeline to idle (all dots neutral)
      PHASE_ORDER.forEach(p => {
        ['nlp-', 'nls-'].forEach(pfx => {
          const el = _qs(pfx + p);
          if (el) el.classList.remove('active', 'done', 'error');
        });
      });

      // Clear live panels and show placeholders
      const codeArea = _qs('nxLiveCodeArea');
      if (codeArea) codeArea.innerHTML = '<span style="color:var(--muted);font-size:0.75rem">Starting…</span>';
      const termArea = _qs('nxLiveTermArea');
      if (termArea) termArea.innerHTML = '<span class="nx-term-line nx-term-system">Starting session…</span>';

      // Reset stat labels
      _setVal('nxLiveLineCount', '0');
      _setVal('nxLiveCodeCount', '0');
      _setVal('nxLiveCmdCount', '0');
      _setVal('nxLiveStageVal', 'starting…');

      // Reset header dots
      const cdot = _qs('nxCodeStreamDot');
      const tdot = _qs('nxTermStreamDot');
      if (cdot) cdot.classList.remove('active');
      if (tdot) tdot.classList.remove('active');

      // Pulse the Live tab button dot
      const liveDot = _qs('nxLiveDot');
      if (liveDot) liveDot.style.display = '';

      // Show conn status
      _setVal('nxLiveConnStatus', 'Connected — receiving live data');

      // Show logs pipeline bar
      const logsPipe = _qs('nxLogsPipeline');
      if (logsPipe) logsPipe.style.display = 'flex';

      // Auto-switch to Live tab if Logs tab is active
      if (typeof NX !== 'undefined' && NX.activeTab === 'logs') {
        nxSetTab('live');
      }
    }

    function _onSessionEnd(status) {
      S.active = false;

      if (status === 'success') {
        _setPipeline('done');
      } else if (status === 'failed') {
        S.phase = 'error';
        ['nlp-', 'nls-'].forEach(pfx => {
          const el = _qs(pfx + 'debugging');
          if (el) { el.classList.remove('active'); el.classList.add('error'); }
        });
      }

      const liveDot = _qs('nxLiveDot');
      if (liveDot) liveDot.style.display = 'none';

      // Fade the stream dots
      const cdot = _qs('nxCodeStreamDot');
      const tdot = _qs('nxTermStreamDot');
      if (cdot) cdot.classList.remove('active');
      if (tdot) tdot.classList.remove('active');

      _setVal('nxLiveConnStatus', `Session ended (${status || 'done'})`);

      // Append done marker to terminal
      const doneMsg = status === 'success'
        ? '<span class="nx-term-line nx-term-ok">✅ Task completed successfully</span>'
        : `<span class="nx-term-line nx-term-err">❌ Session ended: ${_esc(status || '')}</span>`;
      const termArea = _qs('nxLiveTermArea');
      if (termArea) {
        termArea.insertAdjacentHTML('beforeend', doneMsg);
        termArea.scrollTop = termArea.scrollHeight;
      }
    }

    /* ── Hook into ingestLogRow (monkey-patch, idempotent) ─────────────*/
    function _hookIngestLogRow() {
      if (typeof ingestLogRow !== 'function') {
        setTimeout(_hookIngestLogRow, 200);
        return;
      }
      if (ingestLogRow._nxExecHooked) return;
      const _orig = ingestLogRow;
      window.ingestLogRow = function (row, area) {
        _orig(row, area);
        try { NxExecVis.onRow(row); } catch (_) { }
      };
      window.ingestLogRow._nxExecHooked = true;
    }

    /* ── Watch session state changes (poll, since selectSession calls ─── */
    /* loadStatus which updates stStatus element) */
    let _lastSessionState = null;
    function _pollSessionState() {
      const statusEl = document.getElementById('stStatus');
      const status = statusEl ? statusEl.textContent.trim().toLowerCase() : '';

      if (status === 'running' && _lastSessionState !== 'running') {
        _lastSessionState = 'running';
        const sid = (typeof currentSession !== 'undefined') ? currentSession : null;
        if (sid) NxExecVis.onSessionStart(sid);

      } else if (status && status !== 'running' && status !== 'no session' && _lastSessionState === 'running') {
        _lastSessionState = status;
        NxExecVis.onSessionEnd(status);

      } else if (status && status !== 'running') {
        _lastSessionState = status;
      }
    }

    /* ── Public API ──────────────────────────────────────────────────── */
    window.NxExecVis = {
      onRow: _onRow,
      onSessionStart: _onSessionStart,
      onSessionEnd: _onSessionEnd,
      clearLive() {
        const c = _qs('nxLiveCodeArea');
        const t = _qs('nxLiveTermArea');
        if (c) c.innerHTML = '<span style="color:var(--muted);font-size:0.75rem">Cleared.</span>';
        if (t) t.innerHTML = '<span class="nx-term-line nx-term-system">Cleared.</span>';
        S.codeCount = 0;
        S.cmdCount = 0;
        S.lineCount = 0;
        _lastFileLabel = '';
        _flush();
      },
    };

    /* ── Init ─────────────────────────────────────────────────────────── */
    window.NX_LOAD_TASKS.push( () => {
      _hookIngestLogRow();
      setInterval(_pollSessionState, 600);
      console.debug('[Phase 33] Real-Time AI Execution Visualization active.');
    });

    // nxSetTab already handles the .active class which triggers display:flex from CSS.
    // No wrapper needed — the Live tab inherits flex-direction:column from nx-tab-content.

  })();
