/* ─── UX INTELLIGENCE & TRUST LAYER — Phase UX-IT ─────────────────────── */
  (function () {
    'use strict';

    /* ── Utilities ────────────────────────────────────────────────────────────── */
    function $id(id) { return document.getElementById(id); }
    function esc(s) {
      return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
    }
    function fmtSecs(s) {
      s = Math.floor(s);
      const m = Math.floor(s / 60), sec = s % 60;
      return m > 0 ? `${m} min ${sec}s` : `${sec}s`;
    }
    function fmtAgo(ts) {
      const d = (Date.now() - ts) / 1000;
      if (d < 5) return 'just now';
      if (d < 60) return `${Math.floor(d)}s ago`;
      if (d < 3600) return `${Math.floor(d / 60)} min ago`;
      return 'a while ago';
    }
    function nowTime() {
      return new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
    }

    /* ── Plan mode ────────────────────────────────────────────────────────────── */
    function getPlanMode() {
      const el = $id('nxActivePlanMode');
      if (!el) return 'pro';
      const t = el.textContent.toLowerCase();
      if (t.includes('lite')) return 'lite';
      if (t.includes('elite')) return 'elite';
      return 'pro';
    }
    function isProOrElite() { return getPlanMode() !== 'lite'; }

    /* ══════════════════════════════════════════════════════════════════════════
       DOM INJECTION
       ══════════════════════════════════════════════════════════════════════════ */
    function injectDOM() {
      /* File toast container */
      if (!$id('uxit-file-toasts')) {
        const t = document.createElement('div');
        t.id = 'uxit-file-toasts';
        document.body.appendChild(t);
      }

      const leftBody = $id('nxLeftBody');
      if (!leftBody) return;

      /* Thinking banner */
      if (!$id('uxit-thinking-banner')) {
        const banner = document.createElement('div');
        banner.id = 'uxit-thinking-banner';
        banner.innerHTML = `
      <div class="uxit-think-phase">
        <div class="uxit-dot"></div>
        <span id="uxit-think-msg">Analyzing your request…</span>
      </div>
      <div class="uxit-think-timer" id="uxit-think-timer">Thinking for 0.0s</div>
      <div class="uxit-think-steps" id="uxit-think-steps">
        <div class="uxit-think-step" id="uxit-step-analyze"><span class="uxit-step-icon">○</span>Analyzing request</div>
        <div class="uxit-think-step" id="uxit-step-plan"><span class="uxit-step-icon">○</span>Planning execution steps</div>
        <div class="uxit-think-step" id="uxit-step-env"><span class="uxit-step-icon">○</span>Preparing environment</div>
      </div>`;
        leftBody.insertBefore(banner, leftBody.firstChild);
      }

      /* Contextual status */
      if (!$id('uxit-ctx-status')) {
        const cs = document.createElement('div');
        cs.id = 'uxit-ctx-status';
        leftBody.insertBefore(cs, leftBody.firstChild);
      }

      /* Execution timer */
      if (!$id('uxit-exec-timer')) {
        const tmr = document.createElement('div');
        tmr.id = 'uxit-exec-timer';
        tmr.innerHTML = `<div class="uxit-timer-row">
      <span class="uxit-timer-worked" id="uxit-timer-worked">—</span>
      <span class="uxit-timer-last"   id="uxit-timer-last">Ready</span>
    </div>`;
        leftBody.insertBefore(tmr, leftBody.firstChild);
      }

      /* Activity Timeline */
      if (!$id('uxit-timeline-panel')) {
        const tl = document.createElement('div');
        tl.id = 'uxit-timeline-panel';
        tl.innerHTML = `
      <div class="uxit-tl-header">
        Activity Timeline
        <span onclick="UXIT.clearTimeline()" title="Clear">✕</span>
      </div>
      <div class="uxit-tl-list" id="uxit-tl-list">
        <div class="uxit-tl-empty" id="uxit-tl-empty">No activity yet.</div>
      </div>`;
        leftBody.appendChild(tl);
      }
    }

    /* ══════════════════════════════════════════════════════════════════════════
       PART 1 — AI THINKING VISIBILITY (Pro / Elite only)
       ══════════════════════════════════════════════════════════════════════════ */
    let _thinkStart = null, _thinkTimer = null, _thinkPhase = 0;
    const THINK_PHASES = [
      { id: 'uxit-step-analyze', msg: 'Analyzing your request…' },
      { id: 'uxit-step-plan', msg: 'Planning execution steps…' },
      { id: 'uxit-step-env', msg: 'Preparing environment…' },
    ];

    function startThinkingPhase() {
      if (!isProOrElite()) return;
      const banner = $id('uxit-thinking-banner');
      if (!banner) return;
      banner.style.display = 'block';
      _thinkStart = Date.now();
      _thinkPhase = 0;
      _updateThinkStep(0);
      clearInterval(_thinkTimer);
      _thinkTimer = setInterval(() => {
        const elapsed = (Date.now() - _thinkStart) / 1000;
        const timerEl = $id('uxit-think-timer');
        if (timerEl) timerEl.textContent = `Thinking for ${elapsed.toFixed(1)}s`;
        if (_thinkPhase === 0 && elapsed > 0.8) _updateThinkStep(1);
        if (_thinkPhase === 1 && elapsed > 2.2) _updateThinkStep(2);
      }, 100);
    }

    function _updateThinkStep(idx) {
      _thinkPhase = idx;
      THINK_PHASES.forEach((p, i) => {
        const el = $id(p.id);
        if (!el) return;
        const icon = el.querySelector('.uxit-step-icon');
        if (i < idx) { el.className = 'uxit-think-step done'; if (icon) icon.textContent = '✓'; }
        if (i === idx) { el.className = 'uxit-think-step active'; if (icon) icon.textContent = '›'; }
        if (i > idx) { el.className = 'uxit-think-step'; if (icon) icon.textContent = '○'; }
      });
      const msgEl = $id('uxit-think-msg');
      if (msgEl && THINK_PHASES[idx]) msgEl.textContent = THINK_PHASES[idx].msg;
    }

    function stopThinkingPhase() {
      clearInterval(_thinkTimer); _thinkTimer = null;
      const banner = $id('uxit-thinking-banner');
      if (!banner) return;
      THINK_PHASES.forEach(p => {
        const el = $id(p.id);
        if (el) { el.className = 'uxit-think-step done'; const ic = el.querySelector('.uxit-step-icon'); if (ic) ic.textContent = '✓'; }
      });
      setTimeout(() => { if (banner) banner.style.display = 'none'; }, 900);
    }

    /* ══════════════════════════════════════════════════════════════════════════
       PART 3 — EXECUTION TIMER
       ══════════════════════════════════════════════════════════════════════════ */
    let _execStart = null, _execStop = null, _execRunning = false;
    let _lastActionTime = null, _execTickTimer = null;

    function startExecTimer() {
      _execStart = Date.now(); _execStop = null; _execRunning = true; _lastActionTime = Date.now();
      const el = $id('uxit-exec-timer');
      if (el) { el.style.display = 'block'; el.classList.add('active'); }
      clearInterval(_execTickTimer);
      _execTickTimer = setInterval(_tickExec, 500);
    }
    function stopExecTimer() {
      _execRunning = false; _execStop = Date.now();
      const el = $id('uxit-exec-timer');
      if (el) el.classList.remove('active');
      clearInterval(_execTickTimer);
      _tickExec();
    }
    function _tickExec() {
      const we = $id('uxit-timer-worked'), le = $id('uxit-timer-last');
      if (!we || !le) return;
      if (_execStart) {
        const dur = ((_execRunning ? Date.now() : _execStop || Date.now()) - _execStart) / 1000;
        we.textContent = `Worked for ${fmtSecs(dur)}`;
      }
      if (_lastActionTime) le.textContent = `Last action: ${fmtAgo(_lastActionTime)}`;
    }

    /* ══════════════════════════════════════════════════════════════════════════
       PART 4 — ACTIVITY TIMELINE
       ══════════════════════════════════════════════════════════════════════════ */
    const _timeline = [];
    const MAX_TL = 50;

    function tlAdd(type, msg) {
      const last = _timeline[_timeline.length - 1];
      if (last && last.msg === msg && (Date.now() - last.ts) < 2000) return;
      _timeline.push({ type, msg, ts: Date.now(), time: nowTime() });
      if (_timeline.length > MAX_TL) _timeline.shift();
      _lastActionTime = Date.now();
      _renderTimeline();
    }

    function _renderTimeline() {
      const list = $id('uxit-tl-list');
      if (!list) return;
      list.innerHTML = _timeline.slice().reverse().map((e, i) => {
        const isFirst = (i === 0);
        const dotCls = isFirst && _execRunning ? 'active' : e.type;
        return `<div class="uxit-tl-entry" onclick="UXIT.tlJump(${_timeline.length - 1 - i})" title="${esc(e.msg)}">
      <div class="uxit-tl-spine">
        <div class="uxit-tl-dot ${dotCls}"></div>
        ${!isFirst ? '<div class="uxit-tl-line"></div>' : ''}
      </div>
      <div class="uxit-tl-body">
        <div class="uxit-tl-msg">${esc(e.msg)}</div>
        <div class="uxit-tl-ts">${e.time}</div>
      </div>
    </div>`;
      }).join('') || '<div class="uxit-tl-empty">No activity yet.</div>';
    }

    /* ══════════════════════════════════════════════════════════════════════════
       PART 5 — CONTEXTUAL STATUS TEXT
       ══════════════════════════════════════════════════════════════════════════ */
    function setCtxStatus(msg, cls) {
      const el = $id('uxit-ctx-status');
      if (!el) return;
      el.textContent = msg;
      el.className = cls || '';
      el.style.display = msg ? 'block' : 'none';
    }

    const STATUS_PATTERNS = [
      [/writing file[:\s]+([^\s\]]+)/i, m => [`Writing ${m[1]}…`, '']],
      [/created? file[:\s]+([^\s\]]+)/i, m => [`Created ${m[1]}`, '']],
      [/updated? file[:\s]+([^\s\]]+)/i, m => [`Updated ${m[1]}`, '']],
      [/running command[:\s]+(.+)/i, m => [`Running: ${m[1].slice(0, 50)}`, '']],
      [/\$ (.+)/, m => [`$ ${m[1].slice(0, 50)}`, '']],
      [/planning/i, () => ['AI is planning…', '']],
      [/generating|writing code/i, () => ['Writing code…', '']],
      [/running server|starting server/i, () => ['Running server…', '']],
      [/fix(ing)?.*error/i, () => ['Fixing error…', 'error']],
      [/error|exception|traceback/i, () => ['Error detected', 'error']],
      [/success|complete|done|finished/i, () => ['Task complete ✓', 'success']],
      [/install(ing)?/i, () => ['Installing dependencies…', '']],
      [/test(ing)?/i, () => ['Running tests…', '']],
      [/deploy(ing)?/i, () => ['Deploying…', '']],
    ];

    function _parseStatus(text) {
      if (!text) return null;
      for (const [re, fn] of STATUS_PATTERNS) {
        const m = text.match(re);
        if (m) return fn(m);
      }
      return null;
    }

    /* ══════════════════════════════════════════════════════════════════════════
       PART 6 — FILE ACTION FEEDBACK
       ══════════════════════════════════════════════════════════════════════════ */
    const _shownFiles = new Set();
    const FILE_PATTERNS = [
      [/\[WRITE\]\s*(.+)/i, 'updated'],
      [/\[CREATE\]\s*(.+)/i, 'created'],
      [/wrote file[:\s]+([^\s]+)/i, 'updated'],
      [/created? file[:\s]+([^\s\]]+)/i, 'created'],
      [/writing[:\s]+([^\s\]]+\.\w{1,6})/i, 'updated'],
      [/saved[:\s]+([^\s\]]+\.\w{1,6})/i, 'updated'],
      [/new file[:\s]+([^\s\]]+)/i, 'created'],
    ];

    function _parseFileAction(text) {
      if (!text) return null;
      for (const [re, action] of FILE_PATTERNS) {
        const m = text.match(re);
        if (m && m[1]) {
          const fname = m[1].trim().replace(/['"]/g, '');
          if (fname.length > 0 && fname.length < 120) return { fname, action };
        }
      }
      return null;
    }

    function showFileToast(filename, action) {
      const key = `${action}:${filename}`;
      if (_shownFiles.has(key)) return;
      _shownFiles.add(key);
      setTimeout(() => _shownFiles.delete(key), 6000);
      const container = $id('uxit-file-toasts');
      if (!container) return;
      const toast = document.createElement('div');
      toast.className = 'uxit-file-toast';
      const shortName = filename.split('/').pop() || filename;
      toast.innerHTML = `
    <span class="uxit-toast-badge ${action}">${action}</span>
    <span class="uxit-toast-name" title="${esc(filename)}">${esc(shortName)}</span>
    <span class="uxit-toast-open" onclick="UXIT.openFile('${esc(filename)}')" title="Open in Files tab">↗ Open</span>`;
      container.appendChild(toast);
      setTimeout(() => { try { container.removeChild(toast); } catch (_) { } }, 3200);
    }

    function openFile(path) {
      if (typeof window.nxSetTab === 'function') window.nxSetTab('files');
      if (typeof window.openFilePath !== 'undefined') window.openFilePath = path;
      if (typeof window.loadFilesTree === 'function') setTimeout(window.loadFilesTree, 50);
    }

    /* ══════════════════════════════════════════════════════════════════════════
       TIMELINE CLASSIFICATION
       ══════════════════════════════════════════════════════════════════════════ */
    function _classifyLog(text) {
      if (!text) return null;
      const fa = _parseFileAction(text);
      if (fa) return ['file', `${fa.action === 'created' ? 'Created' : 'Wrote'} ${fa.fname}`];
      if (/planning|plan\s|decompos|steps:/i.test(text)) return ['plan', 'Planning: ' + text.slice(0, 55)];
      if (/\$ |\brun\b|executing|command|install|npm|python|bash/i.test(text))
        return ['cmd', text.replace(/^\[.*?\]\s*/, '').slice(0, 55)];
      if (/error|exception|traceback|failed|fix/i.test(text)) return ['error', text.replace(/^\[.*?\]\s*/, '').slice(0, 55)];
      if (/success|complete|done|finished|✓/i.test(text)) return ['success', text.replace(/^\[.*?\]\s*/, '').slice(0, 55)];
      return null;
    }

    /* ══════════════════════════════════════════════════════════════════════════
       MONKEY-PATCH: ingestLogRow
       ══════════════════════════════════════════════════════════════════════════ */
    let _patchedIngest = false;
    function patchIngestLogRow() {
      if (_patchedIngest || typeof window.ingestLogRow !== 'function') {
        if (!_patchedIngest) setTimeout(patchIngestLogRow, 300);
        return;
      }
      _patchedIngest = true;
      const orig = window.ingestLogRow;
      window.ingestLogRow = function (e, area) {
        orig.call(this, e, area);
        const text = (e && e.text) || '';
        if (!text) return;
        _lastActionTime = Date.now();

        // Part 5: contextual status
        if (_execRunning) {
          const ps = _parseStatus(text);
          if (ps) setCtxStatus(ps[0], ps[1]);
        }
        // Part 4: timeline
        const cl = _classifyLog(text);
        if (cl) tlAdd(cl[0], cl[1]);
        // Part 6: file toasts
        const fa = _parseFileAction(text);
        if (fa) showFileToast(fa.fname, fa.action);
        // Part 7: pre-display terminal command in Live pane
        const cmdM = text.match(/^\$\s+(.+)/);
        if (cmdM) {
          const area2 = $id('nxLiveTermArea');
          if (area2) {
            const pre = document.createElement('div');
            pre.className = 'uxit-term-cmd-pre'; pre.textContent = cmdM[1];
            area2.appendChild(pre);
            if ($id('nxTermAutoScroll') && $id('nxTermAutoScroll').checked) area2.scrollTop = area2.scrollHeight;
          }
        }
      };
    }

    /* ══════════════════════════════════════════════════════════════════════════
       MONKEY-PATCH: nxSetGlobalStatus
       ══════════════════════════════════════════════════════════════════════════ */
    let _patchedGlobalStatus = false;
    function patchNxSetGlobalStatus() {
      if (_patchedGlobalStatus || typeof window.nxSetGlobalStatus !== 'function') {
        if (!_patchedGlobalStatus) setTimeout(patchNxSetGlobalStatus, 300);
        return;
      }
      _patchedGlobalStatus = true;
      const orig = window.nxSetGlobalStatus;
      window.nxSetGlobalStatus = function (status) {
        orig.call(this, status);
        if (status === 'running') {
          // Part 1
          if (isProOrElite()) startThinkingPhase();
          else { const b = $id('uxit-thinking-banner'); if (b) b.style.display = 'none'; }
          // Part 3
          startExecTimer();
          // Part 5
          setCtxStatus('AI is thinking…');
          // Part 4
          tlAdd('plan', 'Session started');
          // Part 8: beacon pulse
          const pulse = $id('nxThinkPulse');
          if (pulse) pulse.classList.add('uxit-live-pulse');
        } else {
          stopThinkingPhase();
          stopExecTimer();
          const isErr = (status === 'error');
          setCtxStatus(isErr ? 'Error encountered' : 'Task complete ✓', isErr ? 'error' : 'success');
          tlAdd(isErr ? 'error' : 'success', isErr ? 'Error encountered' : 'Execution complete ✓');
          setTimeout(() => setCtxStatus('', ''), 4000);
          const pulse = $id('nxThinkPulse');
          if (pulse) pulse.classList.remove('uxit-live-pulse');
          // Part 10: generate docs on completion
          setTimeout(_generateDoc, 500);
        }
        updateModeBadge();
      };
    }

    /* ══════════════════════════════════════════════════════════════════════════
       MONKEY-PATCH: syncRunningIndicators (stage-level status)
       ══════════════════════════════════════════════════════════════════════════ */
    let _patchedSync = false;
    function patchSyncRunning() {
      if (_patchedSync || typeof window.syncRunningIndicators !== 'function') {
        if (!_patchedSync) setTimeout(patchSyncRunning, 400);
        return;
      }
      _patchedSync = true;
      const orig = window.syncRunningIndicators;
      window.syncRunningIndicators = function (s, model) {
        orig.call(this, s, model);
        _execRunning = !!s.is_running;
        if (s.is_running && s.stage) {
          const stage = s.stage.toLowerCase();
          if (stage.includes('plan')) setCtxStatus('AI is planning…');
          else if (stage.includes('code')) setCtxStatus('Writing code…');
          else if (stage.includes('debug')) setCtxStatus('Fixing error…');
          else if (stage.includes('review')) setCtxStatus('Reviewing code…');
          else if (stage.includes('test')) setCtxStatus('Running tests…');
          else if (stage.includes('deploy')) setCtxStatus('Deploying…');
        }
      };
    }

    /* ══════════════════════════════════════════════════════════════════════════
       MONKEY-PATCH: appendLogLine (micro-animation)
       ══════════════════════════════════════════════════════════════════════════ */
    let _patchedAppend = false;
    function patchAppendLogLine() {
      if (_patchedAppend || typeof window.appendLogLine !== 'function') {
        if (!_patchedAppend) setTimeout(patchAppendLogLine, 400);
        return;
      }
      _patchedAppend = true;
      const orig = window.appendLogLine;
      window.appendLogLine = function (area, e) {
        orig.call(this, area, e);
        const last = area && area.lastElementChild;
        if (last && !last.classList.contains('uxit-log-new')) last.classList.add('uxit-log-new');
      };
    }

    /* ══════════════════════════════════════════════════════════════════════════
       PART 2 — SMART MESSAGE COLLAPSE
       ══════════════════════════════════════════════════════════════════════════ */
    let _collapseTimer = null, _lastCollapseN = 0;

    function _scheduleCollapse() {
      clearTimeout(_collapseTimer);
      _collapseTimer = setTimeout(_doCollapse, 1800);
    }
    function _doCollapse() {
      const area = $id('logArea');
      if (!area) return;
      const lines = area.querySelectorAll('.log-line:not(.uxit-collapsed-block)');
      if (lines.length < 25) return;
      if (area.querySelector('.uxit-collapse-bar')) return;
      const n = lines.length - 10;
      if (n <= 0 || n === _lastCollapseN) return;
      _lastCollapseN = n;
      for (let i = 0; i < n; i++) lines[i].classList.add('uxit-collapsed-block');
      const bar = document.createElement('div');
      bar.className = 'uxit-collapse-bar';
      let expanded = false;
      function _updateBar() {
        bar.innerHTML = expanded
          ? `<span>▾ Hide previous steps</span><span class="uxit-cb-count">${n}</span>`
          : `<span>▸ Show previous steps</span><span class="uxit-cb-count">${n}</span>`;
      }
      _updateBar();
      bar.onclick = () => {
        expanded = !expanded;
        area.querySelectorAll('.uxit-collapsed-block').forEach(el => { el.style.display = expanded ? '' : ''; });
        _updateBar();
        if (!expanded) area.scrollTop = area.scrollHeight;
      };
      const firstVis = lines[n];
      if (firstVis) area.insertBefore(bar, firstVis);
    }
    function _initLogObserver() {
      const area = $id('logArea');
      if (!area) { setTimeout(_initLogObserver, 500); return; }
      new MutationObserver(_scheduleCollapse).observe(area, { childList: true });
    }

    /* ══════════════════════════════════════════════════════════════════════════
       PART 9 — MODE BADGE
       ══════════════════════════════════════════════════════════════════════════ */
    function updateModeBadge() {
      const old = $id('uxit-mode-badge');
      if (old) old.remove();
      const planEl = $id('nxActivePlanMode');
      if (!planEl) return;
      const mode = getPlanMode();
      const badge = document.createElement('span');
      badge.id = 'uxit-mode-badge';
      badge.className = mode;
      badge.textContent = mode.toUpperCase();
      planEl.parentNode.insertBefore(badge, planEl.nextSibling);
    }

    /* ══════════════════════════════════════════════════════════════════════════
       PART 10 — AUTO DOCUMENTATION
       ══════════════════════════════════════════════════════════════════════════ */
    function _generateDoc() {
      if (!_timeline.length) return;
      const sid = (typeof currentSession !== 'undefined' ? currentSession : 'unknown');
      const lines = [
        '# System Execution Log',
        '',
        `**Generated:** ${new Date().toISOString()}`,
        `**Session:** ${sid}`,
        `**Plan Mode:** ${getPlanMode()}`,
        '',
        '## Activity Timeline',
        '',
        ..._timeline.map(e => `- **[${e.time}]** \`${e.type.toUpperCase()}\` — ${e.msg}`),
        '',
        '## Summary',
        `- Total events: ${_timeline.length}`,
        _execStart ? `- Execution duration: ${fmtSecs(((_execStop || Date.now()) - _execStart) / 1000)}` : '',
        '',
        '---',
        '*Auto-generated by UX Intelligence Layer — Phase UX-IT*',
      ].filter(l => l !== undefined).join('\n');

      fetch('/api/write-doc', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ path: 'docs/system_execution_log.md', content: lines })
      }).catch(() => {
        try { sessionStorage.setItem('uxit_exec_log', lines); } catch (_) { }
      });
    }

    /* ══════════════════════════════════════════════════════════════════════════
       PUBLIC API
       ══════════════════════════════════════════════════════════════════════════ */
    window.UXIT = {
      clearTimeline: () => { _timeline.length = 0; _renderTimeline(); },
      tlJump(idx) {
        const area = $id('logArea');
        if (!area || idx < 0) return;
        const lines = area.querySelectorAll('.log-line');
        const target = lines[Math.min(Math.floor(idx * lines.length / Math.max(_timeline.length, 1)), lines.length - 1)];
        if (target) target.scrollIntoView({ behavior: 'smooth', block: 'center' });
      },
      openFile,
      getLog: () => sessionStorage.getItem('uxit_exec_log') || '(no log)',
      generateLog: _generateDoc,
    };

    /* ══════════════════════════════════════════════════════════════════════════
       BOOT
       ══════════════════════════════════════════════════════════════════════════ */
    function boot() {
      injectDOM();
      patchIngestLogRow();
      patchNxSetGlobalStatus();
      patchSyncRunning();
      patchAppendLogLine();
      _initLogObserver();
      updateModeBadge();

      // Watch plan mode changes
      const planEl = $id('nxActivePlanMode');
      if (planEl) new MutationObserver(updateModeBadge).observe(planEl, { childList: true, characterData: true, subtree: true });

      // Auto-generate doc on page unload
      window.addEventListener('beforeunload', _generateDoc);
      // Periodic doc gen while running
      setInterval(() => { if (_execRunning && _timeline.length > 0) _generateDoc(); }, 60000);

      console.debug('[UX-IT] ready');
    }

    window.NX_BOOT_TASKS.push(boot);

  })();
