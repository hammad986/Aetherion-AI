// Phase 28: Listen for uncertainty state
      // In a real implementation this would trigger off of an SSE event or polling the MCP state endpoint
      const nxFlag = (name) => !!(window.NX && typeof window.NX.hasDebugFlag === 'function' && window.NX.hasDebugFlag(name));
      window.addEventListener('phase28:uncertainty', function (e) {
        const query = e.detail;
        if (!query) return;
        document.getElementById('uncertaintyModal').style.display = 'block';
        document.getElementById('uncertaintyQuestion').textContent = query.question;
        const optsDiv = document.getElementById('uncertaintyOptions');
        optsDiv.innerHTML = '';
        query.options.forEach(opt => {
          const btn = document.createElement('button');
          btn.textContent = opt;
          btn.style.cssText = 'padding:8px; background:var(--bg); color:var(--text); border:1px solid var(--border); border-radius:4px; cursor:pointer; text-align:left;';
          btn.onmouseover = () => btn.style.borderColor = 'var(--accent)';
          btn.onmouseout = () => btn.style.borderColor = 'var(--border)';
          btn.onclick = () => submitUncertainty(opt, query.id);
          optsDiv.appendChild(btn);
        });
        window.currentUncertaintyId = query.id;
      });

      function submitUncertainty(choice, id = window.currentUncertaintyId) {
        if (choice === 'custom') {
          choice = document.getElementById('uncertaintyCustomInput').value;
          if (!choice) return;
        }
        document.getElementById('uncertaintyModal').style.display = 'none';

        // Push response to server MCP Context API endpoint
        if (typeof api === 'function') {
          api('POST', '/api/mcp/human-response', { query_id: id, decision: choice });
        }
      }

      /* ================================================================
         PHASE 31 — VOICE INPUT (Web Speech API)
         ================================================================ */
      let _voiceRecognition = null;
      let _voiceActive = false;
      let _uploadedContext = [];  // [{filename, context, type}]

      function toggleVoice() {
        if (_voiceActive) {
          stopVoice();
        } else {
          startVoice();
        }
      }

      function startVoice() {
        const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
        if (!SpeechRecognition) {
          toast('Voice input requires Chrome or Edge.', 'err');
          return;
        }
        _voiceRecognition = new SpeechRecognition();
        _voiceRecognition.lang = 'en-US';
        _voiceRecognition.continuous = true;
        _voiceRecognition.interimResults = true;

        _voiceRecognition.onresult = (event) => {
          let interim = '', final = '';
          for (let i = event.resultIndex; i < event.results.length; i++) {
            const t = event.results[i][0].transcript;
            if (event.results[i].isFinal) final += t;
            else interim += t;
          }
          const preview = $('voiceTranscriptPreview');
          preview.textContent = final || interim;
          preview.classList.toggle('show', !!(final || interim));
          if (final) {
            const ta = $('taskInput');
            ta.value = (ta.value ? ta.value + ' ' : '') + final.trim();
          }
        };
        _voiceRecognition.onerror = (e) => {
          toast(`Voice error: ${e.error}`, 'err');
          stopVoice();
        };
        _voiceRecognition.onend = () => {
          if (_voiceActive) _voiceRecognition.start();  // re-start for continuous
        };

        _voiceRecognition.start();
        _voiceActive = true;
        $('voiceBtn').classList.add('recording');
        $('voiceBtn').title = 'Click to stop recording';
        toast('Voice recording started 🎤', 'ok');
      }

      function stopVoice() {
        if (_voiceRecognition) {
          _voiceActive = false;
          _voiceRecognition.stop();
          _voiceRecognition = null;
        }
        $('voiceBtn').classList.remove('recording');
        $('voiceBtn').title = 'Voice input (click to start recording)';
        toast('Voice recording stopped', 'ok');
      }

      /* ================================================================
         PHASE 31 — FILE / IMAGE UPLOAD
         ================================================================ */
      async function handleFileUpload(input) {
        const file = input.files[0];
        if (!file) return;
        input.value = '';  // reset so same file can be re-uploaded

        const fd = new FormData();
        fd.append('file', file);

        toast(`Uploading ${file.name}…`, 'ok');
        try {
          const res = await fetch('/api/upload', { method: 'POST', body: fd });
          const data = await res.json();
          if (!data.ok) { toast(`Upload failed: ${data.error}`, 'err'); return; }

          // Store context for injection into next task
          _uploadedContext.push({ filename: file.name, context: data.context, type: data.type });
          renderUploadChips();
          toast(`✔ ${file.name} ready to use`, 'ok');
        } catch (e) {
          toast(`Upload error: ${e.message}`, 'err');
        }
      }

      function renderUploadChips() {
        const container = $('uploadChips');
        if (!container) return;
        container.innerHTML = _uploadedContext.map((u, i) =>
          `<span class="upload-chip">
            <span>${u.type === 'image' ? '🖼️' : '📄'}</span>
            ${escapeHtml(u.filename)}
            <span class="chip-remove" onclick="removeUpload(${i})">×</span>
        </span>`
        ).join('');
        // Sync to Phase 3 context bar
        if (typeof nxSyncContextBadgesFromChips === 'function') nxSyncContextBadgesFromChips();
      }

      function removeUpload(idx) {
        _uploadedContext.splice(idx, 1);
        renderUploadChips();
      }

      function buildTaskWithContext() {
        let task = ($('taskInput').value || '').trim();
        if (_uploadedContext.length > 0) {
          const ctx = _uploadedContext.map(u => u.context).join('\n\n');
          task = `${task}\n\n--- Attached Context ---\n${ctx}`;
        }
        return task;
      }

      /* ================================================================
         PHASE 31 — HUMAN-IN-THE-LOOP (HITL) CONTROLS
         ================================================================ */
      function showHitlPanel(show) {
        const p = $('hitlPanel');
        if (p) p.style.display = show ? 'block' : 'none';
      }

      function hitlSetPaused(paused) {
        const pauseBtn = $('hitlPauseBtn');
        const resumeBtn = $('hitlResumeBtn');
        const dot = $('hitlDot');
        const statusTxt = $('hitlStatusText');
        if (!pauseBtn) return;
        if (paused) {
          pauseBtn.style.display = 'none';
          resumeBtn.style.display = '';
          dot.classList.add('paused');
          statusTxt.textContent = 'Agent paused';
        } else {
          pauseBtn.style.display = '';
          resumeBtn.style.display = 'none';
          dot.classList.remove('paused');
          statusTxt.textContent = 'Agent running';
        }
      }

      async function hitlPause() {
        if (!currentSession) return;
        const r = await api('POST', `/api/session/${currentSession}/pause`);
        if (r.ok) { hitlSetPaused(true); toast('Agent paused', 'ok'); }
        else toast('Could not pause: ' + (r.data.error || '?'), 'err');
      }

      async function hitlResume() {
        if (!currentSession) return;
        const r = await api('POST', `/api/session/${currentSession}/resume`);
        if (r.ok) { hitlSetPaused(false); toast('Agent resumed', 'ok'); }
        else toast('Could not resume: ' + (r.data.error || '?'), 'err');
      }

      async function hitlInject() {
        if (!currentSession) return;
        const instruction = ($('hitlInjectInput').value || '').trim();
        if (!instruction) { toast('Enter an instruction to inject', 'err'); return; }
        const r = await api('POST', `/api/session/${currentSession}/inject`,
          { instruction });
        if (r.ok) {
          $('hitlInjectInput').value = '';
          toast('✔ Instruction injected into agent', 'ok');
        } else {
          toast('Inject failed: ' + (r.data.error || '?'), 'err');
        }
      }

      async function hitlRetry() {
        if (!currentSession) return;
        await hitlInject_direct('Retry the last step with a different approach.');
      }

      async function hitlInject_direct(instruction) {
        if (!currentSession) return;
        await api('POST', `/api/session/${currentSession}/inject`, { instruction });
        toast('↻ Retry injected', 'ok');
      }

      /* ================================================================
         PHASE 31 — OBSERVABILITY DASHBOARD
         ================================================================ */
      async function loadObservability() {
        try {
          const r = await api('GET', '/api/health');
          if (!r.ok || !r.data.ok) { toast('Health endpoint error', 'err'); return; }
          const d = r.data;

          // KPI cards
          setVal('obsTotal', d.sessions.total);
          setVal('obsRunning', d.sessions.running);
          setVal('obsCpu', d.system.cpu_pct != null ? d.system.cpu_pct + '%' : 'N/A');
          setVal('obsMem', d.system.mem_used_gb != null ? d.system.mem_used_gb + ' GB' : 'N/A');
          setVal('obsPython', d.system.python || 'N/A');

          const sr = d.sessions.success_rate_pct;
          setVal('obsSuccessRate', sr + '%');
          setVal('obsSuccessRateBar', sr + '%');
          const fill = $('obsBarFill');
          if (fill) fill.style.width = Math.min(100, sr) + '%';

          // Safety limits
          setVal('obsSafetyIter', d.safety.max_iterations);
          setVal('obsSafetyCmd', d.safety.max_commands);
          setVal('obsSafetyRuntime', d.safety.max_runtime_s + 's');

          // Last updated
          const lu = $('obsLastUpdated');
          if (lu) lu.textContent = 'Updated ' + new Date().toLocaleTimeString();
        } catch (e) {
          toast('Observability error: ' + e.message, 'err');
        }
      }

      // Auto-load observability when switching to that tab
      const _origSetActiveTab = typeof setActiveTab === 'function' ? setActiveTab : null;
      // Hook tab switching to auto-load observability
      window.NX_LOAD_TASKS.push( () => {
        // Auto-load on observability tab click
        const obsTabBtn = document.querySelector('[data-tab="observability"]');
        if (obsTabBtn) {
          obsTabBtn.addEventListener('click', () => {
            setTimeout(loadObservability, 100);
          });
        }
      });

      /* ================================================================
         PHASE 31 — WIRE HITL PANEL INTO SESSION LIFECYCLE
         ================================================================ */
      // Extend session activation to show/hide HITL panel
      const _p31_origActivate = typeof activateSession === 'function' ? activateSession : null;
      function _p31_onSessionChange(sid, status) {
        const isActive = ['queued', 'running'].includes(status);
        showHitlPanel(isActive);
        if (!isActive) hitlSetPaused(false);
      }
      // Patch into the polling cycle (safe, non-breaking)
      const _p31_origUpdateStatus = typeof updateStatus === 'function' ? updateStatus : null;
      if (_p31_origUpdateStatus) {
        const _wrapped = updateStatus;
        // We'll hook via the existing poll interval instead
      }
      // Observe session status changes via MutationObserver on stStatus element
      (function _p31_watchStatus() {
        const el = $('stStatus');
        if (!el) return;
        const mo = new MutationObserver(() => {
          if (nxFlag('mutationobservers')) return;
          const txt = el.textContent.toLowerCase();
          const isActive = txt.includes('running') || txt.includes('queued');
          showHitlPanel(isActive);
        });
        mo.observe(el, { childList: true, characterData: true, subtree: true });
      })();

      // Extend queueTask to inject file context into the prompt
      const _p31_origQueueTask = typeof queueTask === 'function' ? queueTask : null;
      if (typeof queueTask === 'function') {
        const _origQt = queueTask;
        window.queueTask = function () {
          if (_uploadedContext.length > 0) {
            const ta = $('taskInput');
            ta.value = buildTaskWithContext();
            _uploadedContext = [];
            renderUploadChips();
          }
          _origQt.call(this);
        };
      }

      /* ================================================================
         PHASE 32 — STEP TIMELINE
         ================================================================ */
      let _steps = [];   // local cache
      let _stepsLoadTimer = null;

      function clearSteps() {
        _steps = [];
        const tl = $('stepTimeline');
        if (tl) tl.innerHTML = '<div style="padding:30px;text-align:center;color:var(--muted);font-size:0.82rem">Cleared.</div>';
        const badge = $('stepsBadge');
        if (badge) badge.textContent = '';
      }

      async function loadStepTrace() {
        if (!currentSession) return;
        const r = await api('GET', `/api/session/${currentSession}/steps`);
        if (!r.ok || !r.data.ok) return;
        _steps = r.data.steps || [];
        renderStepTimeline();
      }

      function renderStepTimeline() {
        const tl = $('stepTimeline');
        if (!tl) return;
        if (_steps.length === 0) {
          tl.innerHTML = '<div style="padding:30px;text-align:center;color:var(--muted);font-size:0.82rem">No steps yet.</div>';
          return;
        }
        const badge = $('stepsBadge');
        if (badge) badge.textContent = _steps.length;
        tl.innerHTML = _steps.map((s, i) => buildStepCard(s, i)).join('');
      }

      const STEP_ICONS = {
        plan: '📝',
        execute: '⚡',
        verify: '✅',
        reflect: '🧠',
        error: '❌',
      };

      function buildStepCard(step, idx) {
        const phase = (step.phase || 'unknown').toLowerCase();
        const status = step.status || 'done';
        const color = STEP_COLORS[phase] || '#8b949e';
        const icon = STEP_ICONS[phase] || '🔸';
        const detail = step.detail || step.content || step.summary || '';
        const conf = typeof step.confidence === 'number' ? step.confidence : null;
        const tokens = step.tokens || null;
        const elapsed = step.elapsed_ms ? `${(step.elapsed_ms / 1000).toFixed(1)}s` : null;
        const ts = step.ts ? new Date(step.ts * 1000).toLocaleTimeString() : '';

        const metaItems = [
          ts && `<span class="step-meta-item">⏰ ${ts}</span>`,
          elapsed && `<span class="step-meta-item">⏱️ ${elapsed}</span>`,
          conf !== null && `<span class="step-meta-item">📊 ${Math.round(conf * 100)}% confidence</span>`,
          tokens && `<span class="step-meta-item">🔎 ${tokens} tokens</span>`,
        ].filter(Boolean).join('');

        const confBar = conf !== null
          ? `<div class="step-conf-bar" style="width:${Math.round(conf * 100)}%"></div>`
          : '';

        return `
    <div class="step-item" style="--step-color:${color}">
        <div class="step-icon">${icon}</div>
        <div class="step-body">
            <div class="step-header" onclick="toggleStepDetail(${idx})">
                <span class="step-phase">${phase}</span>
                <span class="step-status ${status}">${status}</span>
                <span class="step-expand" id="stepExp${idx}">▼</span>
            </div>
            <div class="step-meta">${metaItems}</div>
            ${confBar}
            ${detail ? `<div class="step-detail" id="stepDet${idx}">${escapeHtml(detail)}</div>` : ''}
        </div>
    </div>`;
      }

      function toggleStepDetail(idx) {
        const det = $(`stepDet${idx}`);
        const exp = $(`stepExp${idx}`);
        if (!det) return;
        det.classList.toggle('open');
        if (exp) exp.textContent = det.classList.contains('open') ? '▲' : '▼';
      }

      // Auto-poll steps when Steps tab is active
      window.NX_LOAD_TASKS.push( () => {
        const stepsBtn = document.querySelector('[data-tab="steps"]');
        if (stepsBtn) {
          stepsBtn.addEventListener('click', () => {
            loadStepTrace();
            clearInterval(_stepsLoadTimer);
            _stepsLoadTimer = setInterval(loadStepTrace, 3000);
          });
        }
      });

      /* ================================================================
         PHASE 32 — SMART MULTIMODAL FILE INTELLIGENCE
         ================================================================ */
      const IMAGE_EXTS = new Set(['.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp']);
      const CODE_EXTS = new Set(['.py', '.js', '.ts', '.jsx', '.tsx', '.html', '.css',
        '.go', '.rs', '.cpp', '.c', '.h', '.java', '.rb', '.sh']);

      // Override handleFileUpload to add smart routing
      const _p31_origHandleFileUpload = typeof handleFileUpload === 'function' ? handleFileUpload : null;
      window.handleFileUpload = async function (input) {
        const file = input.files[0];
        if (!file) return;

        const ext = (file.name.split('.').pop() || '').toLowerCase();
        const isImg = IMAGE_EXTS.has('.' + ext);
        const isCode = CODE_EXTS.has('.' + ext);

        // Standard upload for context injection
        if (_p31_origHandleFileUpload) {
          await _p31_origHandleFileUpload(input);
        }

        // Additionally route to analysis
        if (isImg) {
          await runImageAnalysis(file);
        } else if (isCode) {
          await runCodeAnalysis(file);
        }
      };

      async function runImageAnalysis(file) {
        const panel = $('analysisResultsPane');
        if (panel) {
          panel.innerHTML = `<div class="analysis-panel">
            <div style="display:flex;gap:10px;align-items:center">
                <span class="skel" style="width:60px;height:60px;border-radius:8px"></span>
                <div style="flex:1">
                    <div class="skel" style="height:12px;width:60%;margin-bottom:8px"></div>
                    <div class="skel" style="height:12px;width:40%"></div>
                </div>
            </div>
        </div>`;
        }

        // Switch to analysis tab
        setActiveTab('analysis');

        const fd = new FormData();
        fd.append('file', file);
        try {
          const res = await fetch('/api/analyze-image', { method: 'POST', body: fd });
          const data = await res.json();
          if (!data.ok) { renderAnalysisError(data.error); return; }
          renderImageAnalysis(file.name, data.analysis);
        } catch (e) {
          renderAnalysisError(e.message);
        }
      }

      async function runCodeAnalysis(file) {
        const panel = $('analysisResultsPane');
        if (panel) {
          panel.innerHTML = `<div class="analysis-panel">
            <div style="display:flex;gap:10px;align-items:center;margin-bottom:10px">
                <span style="font-size:2rem">📄</span>
                <div style="flex:1">
                    <div class="skel" style="height:12px;width:55%;margin-bottom:8px"></div>
                    <div class="skel" style="height:12px;width:35%"></div>
                </div>
            </div>
            <div class="skel" style="height:12px;width:80%;margin-bottom:8px"></div>
            <div class="skel" style="height:12px;width:65%"></div>
        </div>`;
        }

        setActiveTab('analysis');
        toast(`🔬 Analyzing ${file.name}…`, 'ok');

        const fd = new FormData();
        fd.append('file', file);
        try {
          const res = await fetch('/api/analyze-code', { method: 'POST', body: fd });
          const data = await res.json();
          if (!data.ok) { renderAnalysisError(data.error); return; }
          renderCodeAnalysis(file.name, data.analysis, data.lines);
        } catch (e) {
          renderAnalysisError(e.message);
        }
      }

      function renderImageAnalysis(filename, a) {
        const panel = $('analysisResultsPane');
        if (!panel) return;
        const insights = (a.insights || []).map(i =>
          `<li style="margin-bottom:4px;font-size:0.8rem;color:var(--text)">${escapeHtml(i)}</li>`
        ).join('');
        panel.innerHTML = `
        <div class="analysis-panel">
            <h4>🖼️ Image Analysis <span class="analysis-badge">${escapeHtml(filename)}</span></h4>
            <div style="font-size:0.82rem;color:var(--text);line-height:1.55;margin-bottom:10px">${escapeHtml(a.description || '')}</div>
            ${insights ? `<ul style="padding-left:18px;margin-bottom:10px">${insights}</ul>` : ''}
            ${a.suggested_task ? `
            <div style="margin-top:10px">
                <div style="font-size:0.68rem;text-transform:uppercase;letter-spacing:0.08em;color:var(--muted);margin-bottom:5px">Suggested Task</div>
                <div style="background:#0a0f17;border:1px solid #2a3140;border-radius:6px;padding:8px 10px;
                            font-size:0.8rem;color:var(--accent);cursor:pointer;"
                     onclick="setTask(this.textContent)" title="Click to use as task">${escapeHtml(a.suggested_task)}</div>
            </div>` : ''}
        </div>`;
        toast('✔ Image analyzed', 'ok');
      }

      function renderCodeAnalysis(filename, a, lines) {
        const panel = $('analysisResultsPane');
        if (!panel) return;
        const issues = (a.issues || []).map(iss => {
          const sev = (iss.severity || 'info').toLowerCase();
          return `<div class="analysis-issue">
            <span class="issue-sev ${sev}">${sev}</span>
            <div>
                <div>${escapeHtml(iss.description || '')}</div>
                ${iss.line ? `<div class="issue-line">Line ${iss.line}</div>` : ''}
                ${iss.fix ? `<div class="issue-fix">💡 ${escapeHtml(iss.fix)}</div>` : ''}
            </div>
        </div>`;
        }).join('');
        const score = a.quality_score || 0;
        panel.innerHTML = `
        <div class="analysis-panel">
            <h4>📄 Code Analysis <span class="analysis-badge">${escapeHtml(filename)}</span></h4>
            <div style="display:flex;align-items:baseline;gap:16px;margin-bottom:12px">
                <span class="quality-score">${score}</span>
                <div>
                    <div style="font-size:0.7rem;color:var(--muted)">Quality Score / 100</div>
                    <div style="font-size:0.72rem;color:var(--muted)">${lines || '?'} lines</div>
                </div>
            </div>
            <div style="font-size:0.82rem;color:var(--text);margin-bottom:10px">${escapeHtml(a.summary || '')}</div>
            ${issues ? `<div style="margin-top:8px">${issues}</div>` : '<div style="color:var(--green);font-size:0.8rem">&#10003; No issues found</div>'}
            ${a.suggested_task ? `
            <div style="margin-top:12px">
                <div style="font-size:0.68rem;text-transform:uppercase;letter-spacing:0.08em;color:var(--muted);margin-bottom:5px">Suggested Task</div>
                <div style="background:#0a0f17;border:1px solid #2a3140;border-radius:6px;padding:8px 10px;
                            font-size:0.8rem;color:var(--accent);cursor:pointer;"
                     onclick="setTask(this.textContent)">${escapeHtml(a.suggested_task)}</div>
            </div>` : ''}
        </div>`;
        toast(`✔ ${filename} analyzed`, 'ok');
      }

      function renderAnalysisError(msg) {
        const panel = $('analysisResultsPane');
        if (!panel) return;
        panel.innerHTML = `<div class="analysis-panel">
        <h4>❌ Analysis Error</h4>
        <div style="color:var(--red);font-size:0.8rem">${escapeHtml(String(msg))}</div>
    </div>`;
      }

      function clearAnalysisResults() {
        const panel = $('analysisResultsPane');
        if (panel) panel.innerHTML = '<div style="padding:30px;text-align:center;color:var(--muted);font-size:0.82rem">Cleared.</div>';
      }

      /* ================================================================
         PHASE 32 — TAB ROUTING (Steps + Analysis + Terminal)
         ================================================================ */
      // Wire Steps and Analysis tabs into setActiveTab
      (function _p32_hookTabs() {
        if (typeof setActiveTab !== 'function') return;
        const _orig = setActiveTab;
        window.setActiveTab = function (tab) {
          _orig.call(this, tab);
          if (tab === 'steps') loadStepTrace();
          if (tab === 'terminal') setTimeout(initXterm, 80);
          // Show/hide per-tab action strips for new tabs
          const strips = ['tabActSteps', 'tabActAnalysis'];
          strips.forEach(id => {
            const el = $(id);
            if (el) el.style.display = 'none';
          });
          if (tab === 'steps') {
            const el = $('tabActSteps'); if (el) el.style.display = '';
          }
          if (tab === 'analysis') {
            const el = $('tabActAnalysis'); if (el) el.style.display = '';
          }
        };
      })();

      // Poll step trace every 3s while a session is running
      setInterval(() => {
        const el = $('stStatus');
        if (!el) return;
        const isRunning = el.textContent.toLowerCase().includes('running');
        if (isRunning && currentSession) {
          loadStepTrace();
        }
      }, 3000);

      /* ================================================================
         PHASE 33 — CROSS-PLATFORM TERMINAL INTELLIGENCE
         ================================================================ */

      // ── Venv status bar ───────────────────────────────────────────────
      let _venvStatusTimer = null;

      function injectVenvStatusBar() {
        const toolbar = $('xtermToolbar');
        if (!toolbar || $('venvStatusBar')) return;
        const bar = document.createElement('div');
        bar.id = 'venvStatusBar';
        bar.style.cssText = [
          'display:flex', 'gap:6px', 'align-items:center',
          'padding:4px 10px', 'border-top:1px solid var(--border)',
          'background:#0a0f17', 'font-size:0.72rem', 'flex-shrink:0',
        ].join(';');
        bar.innerHTML = `
        <span id="venvIcon" style="font-size:0.9rem">⏳</span>
        <span id="venvLabel" style="color:var(--muted)">venv: checking…</span>
        <span id="venvPill" style="
            padding:1px 7px;border-radius:10px;
            background:#1a2030;color:var(--muted);
            font-size:0.65rem;font-weight:600;
        "></span>
        <div style="flex:1"></div>
        <button class="btn tiny" id="venvEnsureBtn"
            onclick="ensureVenv()" title="Create/activate my_env venv">
            🐍 Ensure venv
        </button>
    `;
        toolbar.parentElement.insertBefore(bar, toolbar.nextSibling);
      }

      async function refreshVenvStatus() {
        if (!_xtermTid) return;
        try {
          const r = await api('GET', `/api/pty/${_xtermTid}/venv`);
          if (!r.ok) return;
          const d = r.data;
          const icon = $('venvIcon');
          const label = $('venvLabel');
          const pill = $('venvPill');
          if (!icon) return;
          if (d.venv_ready && d.venv_activated) {
            icon.textContent = '✅';
            label.textContent = `venv: ${d.venv_name} (active)`;
            label.style.color = 'var(--green)';
            pill.textContent = 'ISOLATED';
            pill.style.background = '#1a3a1a';
            pill.style.color = 'var(--green)';
          } else if (d.venv_ready) {
            icon.textContent = '🟡';
            label.textContent = `venv: ${d.venv_name} (found, not activated)`;
            label.style.color = '#d29922';
            pill.textContent = 'NOT ACTIVE';
            pill.style.background = '#3a2a00';
            pill.style.color = '#d29922';
          } else {
            icon.textContent = '⚠️';
            label.textContent = `venv: ${d.venv_name} (missing)`;
            label.style.color = 'var(--red)';
            pill.textContent = 'NO VENV';
            pill.style.background = '#3a1a1a';
            pill.style.color = 'var(--red)';
          }
        } catch (_) { }
      }

      async function ensureVenv() {
        if (!_xtermTid) { toast('Start terminal first', 'warn'); return; }
        const btn = $('venvEnsureBtn');
        if (btn) { btn.disabled = true; btn.textContent = '⏳ Creating…'; }
        try {
          const r = await api('POST', `/api/pty/${_xtermTid}/venv/ensure`);
          if (r.ok && r.data.ok) {
            toast('✅ venv ready and activated', 'ok');
            await refreshVenvStatus();
          } else {
            toast('⚠️ venv ensure failed: ' + (r.data?.error || 'unknown'), 'warn');
          }
        } finally {
          if (btn) { btn.disabled = false; btn.textContent = '🐍 Ensure venv'; }
          renderGovernance(gov);
        }
      }

      // ── Error Intelligence Panel ──────────────────────────────────────
      let _errorCheckTimer = null;

      function injectErrorPanel() {
        const container = $('xtermContainer');
        if (!container || $('termErrorPanel')) return;
        const panel = document.createElement('div');
        panel.id = 'termErrorPanel';
        panel.style.cssText = [
          'display:none', 'position:absolute', 'bottom:0', 'left:0', 'right:0',
          'background:rgba(40,10,10,0.97)', 'border-top:2px solid #f85149',
          'padding:10px 14px', 'z-index:100', 'animation:fadeInUp 0.2s ease',
        ].join(';');
        panel.innerHTML = `
        <div style="display:flex;align-items:center;gap:8px;margin-bottom:6px">
            <span style="font-size:1rem">⚠️</span>
            <strong id="errLabel" style="font-size:0.82rem;color:#f85149"></strong>
            <div style="flex:1"></div>
            <button class="btn tiny" onclick="hideErrorPanel()" style="border-color:#f8514966">✕</button>
        </div>
        <div id="errExcerpt" style="
            font-family:ui-monospace,monospace;font-size:0.74rem;
            color:#ffb3b0;background:#1a0808;border-radius:4px;
            padding:6px 8px;margin-bottom:8px;max-height:60px;
            overflow:auto;white-space:pre-wrap;
        "></div>
        <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">
            <span id="errSuggestion" style="font-size:0.76rem;color:#d29922;flex:1"></span>
            <button class="btn tiny" id="errFixBtn"
                onclick="applyAiFix()"
                style="background:linear-gradient(135deg,#1a3a1a,#0a1f0a);
                       border-color:#3fb950;color:#3fb950;"
                title="Apply AI-generated fix">
                🤖 AI Fix
            </button>
            <button class="btn tiny" onclick="dismissAndClearError()"
                style="border-color:#555">Dismiss</button>
        </div>
    `;
        container.appendChild(panel);
      }

      function showErrorPanel(err) {
        injectErrorPanel();
        const panel = $('termErrorPanel');
        const label = $('errLabel');
        const excerpt = $('errExcerpt');
        const suggestion = $('errSuggestion');
        if (!panel) return;
        if (label) label.textContent = err.label || 'Error detected';
        if (excerpt) excerpt.textContent = err.excerpt || '';
        if (suggestion) suggestion.textContent = '💡 ' + (err.suggestion || '');
        panel.style.display = 'block';
      }

      function hideErrorPanel() {
        const panel = $('termErrorPanel');
        if (panel) panel.style.display = 'none';
      }

      async function dismissAndClearError() {
        hideErrorPanel();
        if (_xtermTid) await api('POST', `/api/pty/${_xtermTid}/clear-error`);
      }

      async function applyAiFix() {
        if (!_xtermTid) return;
        const btn = $('errFixBtn');
        if (btn) { btn.disabled = true; btn.textContent = '⏳ Fixing…'; }
        try {
          const r = await api('POST', `/api/pty/${_xtermTid}/fix`);
          if (r.ok && r.data.ok) {
            if (r.data.fix_command) {
              toast(`🤖 Applying fix: ${r.data.fix_command.slice(0, 60)}…`, 'ok');
              hideErrorPanel();
            } else {
              // No AI command, show static suggestion
              const sug = $('errSuggestion');
              if (sug && r.data.suggestion) {
                sug.textContent = '💡 ' + r.data.suggestion;
                sug.style.color = 'var(--green)';
              }
            }
          } else {
            toast('AI fix unavailable: ' + (r.data?.error || 'unknown'), 'warn');
          }
        } finally {
          if (btn) { btn.disabled = false; btn.textContent = '🤖 AI Fix'; }
          renderGovernance(gov);
        }
      }

      async function pollTerminalErrors() {
        if (!_xtermTid) return;
        try {
          const r = await api('GET', `/api/pty/${_xtermTid}/last-error`);
          if (r.ok && r.data.error) {
            showErrorPanel(r.data.error);
          }
        } catch (_) { }
      }

      // ── Intercept SSE error_detected events ──────────────────────────
      const _p33_origXtermConnect = xtermConnect;
      window.xtermConnect = async function (sid) {
        await _p33_origXtermConnect(sid);
        // Patch the SSE handler after connection
        if (_xtermSSE) {
          const _origOnMsg = _xtermSSE.onmessage;
          _xtermSSE.onmessage = (e) => {
            if (_origOnMsg) _origOnMsg(e);
            try {
              const msg = JSON.parse(e.data);
              if (msg.type === 'error_detected') {
                showErrorPanel(msg);
              }
            } catch (_) { }
          };
        }
        // Inject UI panels after connection
        injectVenvStatusBar();
        injectErrorPanel();
        // Start periodic venv + error polling
        clearInterval(_venvStatusTimer);
        _venvStatusTimer = setInterval(() => {
          refreshVenvStatus();
          pollTerminalErrors();
        }, 4000);
        // Initial check
        setTimeout(refreshVenvStatus, 1500);
      };

      /* ================================================================
         PHASE 33 — SESSION PERSISTENCE (Save / Restore)
         ================================================================ */

      async function saveSession() {
        if (!currentSession) { toast('No active session to save', 'warn'); return; }
        const r = await api('POST', `/api/session/${currentSession}/save`, {
          extra: { task: ($('taskInput') || {}).value || '' }
        });
        if (r.ok && r.data.ok) {
          toast(`✅ Session saved (${r.data.records} decisions)`, 'ok');
        } else {
          toast('⚠️ Save failed', 'warn');
        }
      }

      async function restoreSession(sid) {
        const r = await api('GET', `/api/session/${sid}/restore`);
        if (!r.ok || !r.data.ok) { toast('❌ Restore failed', 'warn'); return; }
        const snap = r.data.snapshot;
        toast(`📂 Restored session ${sid} — ${snap.agent_decisions?.length || 0} decisions`, 'ok');
        // Re-hydrate terminal history if terminal is open
        if (_xterm && snap.terminal_history?.length) {
          _xterm.write(`\r\n\x1b[36m[Session restored — ${snap.terminal_history.length} commands in history]\x1b[0m\r\n`);
        }
        return snap;
      }

      async function loadSavedSessions() {
        const r = await api('GET', '/api/sessions/saved');
        if (!r.ok || !r.data.ok) return [];
        return r.data.snapshots || [];
      }

      // Wire Save button to header
      (function _p33_addSaveBtn() {
        const headerActions = document.querySelector('.header-actions');
        if (!headerActions || $('saveSessionBtn')) return;
        const btn = document.createElement('button');
        btn.id = 'saveSessionBtn';
        btn.className = 'btn';
        btn.title = 'Save current session to disk';
        btn.innerHTML = '💾 Save Session';
        btn.onclick = saveSession;
        btn.style.cssText = 'background:linear-gradient(135deg,#1a2a3a,#0d1f2d);border-color:#388bfd55;color:#79c0ff';
        headerActions.insertBefore(btn, headerActions.firstChild);
      })();

      /* ================================================================
         PHASE 33 — ORCHESTRATOR ADAPTIVE HOOKS (UI)
         ================================================================ */

      // Enrich log entries with strategy/phase labels from orchestrator events
      const _p33_STRATEGY_COLORS = {
        plan: '#388bfd',
        decide: '#bc8cff',
        execute: '#f0883e',
        role: '#58a6ff',
        verify: '#3fb950',
        adapt: '#d29922',
        replan: '#f85149',
        reflect: '#79c0ff',
        guard: '#f85149',
      };

      function renderOrchestratorBadge(kind) {
        const color = _p33_STRATEGY_COLORS[kind] || '#8b949e';
        return `<span style="
        display:inline-block;padding:1px 6px;border-radius:4px;
        font-size:0.62rem;font-weight:700;text-transform:uppercase;
        letter-spacing:0.06em;vertical-align:middle;
        background:${color}22;color:${color};border:1px solid ${color}44;
        margin-right:4px;
    ">${kind}</span>`;
      }

      // Hook into the log rendering to badge orchestrator lines
      (function _p33_hookLog() {
        const origAppendLog = typeof appendLog === 'function' ? appendLog : null;
        if (!origAppendLog) return;
        window.appendLog = function (record, ...rest) {
          // Detect orchestrator records and inject badge
          if (record && typeof record === 'object' && record.kind) {
            const badge = renderOrchestratorBadge(record.kind);
            const div = document.createElement('div');
            div.className = 'log-entry';
            div.style.cssText = 'display:flex;align-items:flex-start;gap:4px;padding:2px 0;';
            div.innerHTML = badge + `<span style="font-size:0.78rem;color:var(--text);flex:1">
                ${escapeHtml(JSON.stringify(record).slice(0, 200))}
            </span>`;
            const logArea = $('logArea');
            if (logArea) { logArea.appendChild(div); }
            return;
          }
          origAppendLog.call(this, record, ...rest);
        };
      })();

      /* ================================================================
         PHASE 33 — PERFORMANCE: THROTTLE STREAMING + MEMORY BOUNDS
         ================================================================ */

      // Throttle xterm writes: batch chunks within 16ms (one frame)
      let _xtermFlushTimer = null;
      let _xtermPendingBuf = '';

      const _p33_origXtermWrite = xtermWrite;
      window.xtermWrite = function (text) {
        _xtermPendingBuf += text;
        if (!_xtermFlushTimer) {
          _xtermFlushTimer = requestAnimationFrame(() => {
            if (_xterm && _xtermPendingBuf) {
              _xterm.write(_xtermPendingBuf);
              _xtermPendingBuf = '';
            }
            _xtermFlushTimer = null;
          });
        }
      };

      // Limit xterm scrollback to prevent memory growth
      const XTERM_MAX_SCROLLBACK = 5000;
      let _xtermLineCount = 0;

      if (typeof _xterm !== 'undefined' && _xterm) {
        _xterm.onLineFeed(() => {
          _xtermLineCount++;
          if (_xtermLineCount > XTERM_MAX_SCROLLBACK + 500) {
            // Trim old lines
            _xterm.clear();
            _xtermLineCount = 0;
          }
        });
      }

      console.debug('[Phase 33] Cross-platform terminal intelligence active.');

      /* ================================================================
         PHASE 34 — INTELLIGENCE DASHBOARD JS
         ================================================================ */

      // ── Tab routing for new panel ─────────────────────────────────────
      const _p34_origSetActiveTab = setActiveTab;
      window.setActiveTab = function (tab) {
        _p34_origSetActiveTab(tab);
        const intPanel = $('tabIntelligence');
        if (!intPanel) return;
        if (tab === 'intelligence') {
          intPanel.classList.remove('hidden');
          loadIntelligenceDashboard();
        } else {
          intPanel.classList.add('hidden');
        }
      };

      // ── Dashboard loader ──────────────────────────────────────────────
      async function loadIntelligenceDashboard() {
        const btn = $('intRefreshBtn');
        if (btn) { btn.disabled = true; btn.textContent = '⟳ Loading…'; }
        try {
          const r = await api('GET', '/api/intelligence/dashboard');
          if (!r.ok || !r.data.ok) return;
          const d = r.data;
          _renderAgents(d.agents || []);
          _renderLtmStats(d.ltm || {});
          _renderCaps(d.capabilities || []);
          // Update badge
          const badge = $('intBadge');
          if (badge) badge.textContent = 'Phase ' + d.phase;
          // Load tools + patterns + messages in parallel
          Promise.all([
            api('GET', '/api/tools/learned'),
            api('GET', '/api/patterns'),
            api('GET', '/api/agents/messages'),
          ]).then(([tr, pr, mr]) => {
            _renderTools((tr.data || {}).tools || []);
            _renderPatterns((pr.data || {}).patterns || []);
            _renderMessages((mr.data || {}).messages || []);
          });
        } catch (e) {
          console.error('[P34] dashboard load error', e);
        } finally {
          if (btn) { btn.disabled = false; btn.textContent = '⟳ Refresh'; }
          renderGovernance(gov);
        }
      }

      // Agent card colors
      const _AGENT_COLORS = {
        planner: { bg: '#1a2a1a', border: '#3fb950', icon: '🗺️' },
        executor: { bg: '#2a1a0a', border: '#f0883e', icon: '⚙️' },
        critic: { bg: '#1a1a2a', border: '#388bfd', icon: '🔍' },
        debugger: { bg: '#2a1a1a', border: '#f85149', icon: '🐛' },
        memory: { bg: '#1a1a2a', border: '#bc8cff', icon: '🧠' },
      };

      function _renderAgents(agents) {
        const el = $('intAgents');
        if (!el) return;
        el.innerHTML = agents.map(a => {
          const c = _AGENT_COLORS[a.name] || { bg: '#1a1a2a', border: '#555', icon: '🤖' };
          return `<div style="
            flex:1;min-width:130px;padding:10px 12px;border-radius:8px;
            background:${c.bg};border:1px solid ${c.border}44;
            display:flex;flex-direction:column;gap:4px;
            animation:fadeInUp 0.3s ease;
        ">
            <div style="font-size:1.2rem">${c.icon}</div>
            <div style="font-weight:700;font-size:0.8rem;color:${c.border};text-transform:capitalize">
                ${a.name}
            </div>
            <div style="font-size:0.7rem;color:var(--muted)">${a.role || ''}</div>
            <div style="font-size:0.65rem;color:${c.border};background:${c.border}22;
                border-radius:4px;padding:1px 5px;width:fit-content">
                ${a.status || 'ready'}
            </div>
        </div>`;
        }).join('');
      }

      function _renderLtmStats(stats) {
        const el = $('intLtmStats');
        if (!el) return;
        const items = [
          { label: 'Memories', value: stats.memories || 0, color: '#388bfd' },
          { label: 'Solved', value: stats.solved || 0, color: '#3fb950' },
          { label: 'Tools', value: stats.tools || 0, color: '#f0883e' },
          { label: 'Patterns', value: stats.patterns || 0, color: '#bc8cff' },
        ];
        el.innerHTML = `<div style="display:grid;grid-template-columns:1fr 1fr;gap:8px">
        ${items.map(it => `<div style="
            padding:8px 10px;border-radius:6px;
            background:${it.color}11;border:1px solid ${it.color}33;
        ">
            <div style="font-size:1.2rem;font-weight:800;color:${it.color}">${it.value}</div>
            <div style="font-size:0.7rem;color:var(--muted)">${it.label}</div>
        </div>`).join('')}
    </div>
    <div style="margin-top:8px;font-size:0.7rem;color:var(--muted)">
        ChromaDB: <span style="color:${stats.chroma ? '#3fb950' : '#f85149'}">
            ${stats.chroma ? '✅ Active' : '⬜ Disabled (SQLite fallback)'}
        </span>
    </div>`;
      }

      function _renderTools(tools) {
        const el = $('intTools');
        if (!el) return;
        if (!tools.length) {
          el.innerHTML = '<span style="color:var(--muted);font-size:0.75rem">No tools learned yet — run tasks to auto-generate tools.</span>';
          return;
        }
        el.innerHTML = tools.slice(0, 20).map(t => `
        <div style="padding:6px 8px;border-bottom:1px solid var(--border);display:flex;align-items:center;gap:6px">
            <span style="font-size:0.9rem">🔧</span>
            <div style="flex:1">
                <div style="color:var(--text);font-weight:600">${escapeHtml(t.name || '')}</div>
                <div style="color:var(--muted);font-size:0.7rem">${escapeHtml((t.description || '').slice(0, 60))}</div>
            </div>
            <span style="font-size:0.65rem;color:#f0883e;background:#f0883e22;
                border-radius:4px;padding:1px 5px">×${t.usage_count || 1}</span>
        </div>`).join('');
      }

      function _renderPatterns(patterns) {
        const el = $('intPatterns');
        if (!el) return;
        if (!patterns.length) {
          el.innerHTML = '<span style="color:var(--muted);font-size:0.75rem">No patterns stored yet.</span>';
          return;
        }
        const colors = ['#bc8cff', '#388bfd', '#3fb950', '#f0883e', '#f85149', '#d29922', '#79c0ff', '#58a6ff'];
        el.innerHTML = `<div style="display:flex;flex-wrap:wrap;gap:6px;padding:4px 0">
        ${patterns.map((p, i) => {
          const c = colors[i % colors.length];
          return `<div style="
                padding:4px 10px;border-radius:12px;font-size:0.72rem;
                background:${c}22;border:1px solid ${c}55;color:${c};
                cursor:default;
            " title="${escapeHtml(p.description || '')}">
                ${escapeHtml(p.pattern || p.name || 'pattern')}
                <span style="opacity:0.6;margin-left:4px">×${p.hits || 1}</span>
            </div>`;
        }).join('')}
    </div>`;
      }

      function _renderMessages(messages) {
        const feed = $('intMsgFeed');
        const count = $('intMsgCount');
        if (!feed) return;
        if (count) count.textContent = messages.length + ' msgs';
        if (!messages.length) {
          feed.innerHTML = '<span style="color:var(--muted)">No agent messages yet.</span>';
          return;
        }
        const roleColors = {
          planner: '#3fb950', executor: '#f0883e', critic: '#388bfd',
          debugger: '#f85149', memory: '#bc8cff'
        };
        feed.innerHTML = messages.slice(-30).reverse().map(m => {
          const from = m.from || '?';
          const to = m.to || '*';
          const kind = m.kind || '?';
          const color = roleColors[from] || '#8b949e';
          const ts = m.ts ? new Date(m.ts * 1000).toLocaleTimeString() : '';
          return `<div style="padding:2px 0;border-bottom:1px solid #ffffff08">
            <span style="color:${color};font-weight:700">[${from}]</span>
            <span style="color:#555"> → </span>
            <span style="color:#8b949e">${to}</span>
            <span style="color:#d29922;margin:0 4px">${kind}</span>
            <span style="color:#555;font-size:0.65rem">${ts}</span>
        </div>`;
        }).join('');
        feed.scrollTop = 0;
      }

      // ── Auto-refresh every 5s when Intelligence tab is active ─────────
      setInterval(() => {
        const intPanel = $('tabIntelligence');
        if (intPanel && !intPanel.classList.contains('hidden')) {
          api('GET', '/api/agents/messages').then(r => {
            _renderMessages((r.data || {}).messages || []);
          });
        }
      }, 5000);

      // ── Wire SSE phase34 events to Intelligence tab ───────────────────
      (function _p34_wireSSE() {
        const _origOnMessage = typeof _handleSSEMessage === 'function'
          ? _handleSSEMessage : null;
        window._handlePhase34Message = function (data) {
          if (data.kind !== 'phase34') return;
          const badge = $('intBadge');
          if (badge && data.event) {
            const eventLabels = {
              'cognitive_team_ready': 'AGI ✅',
              'prior_solution_found': '🧠 Recall Hit',
              'pattern_stored': '🗺️ +Pattern',
            };
            badge.textContent = eventLabels[data.event] || 'Phase 34';
            setTimeout(() => { if (badge) badge.textContent = 'Phase 34'; }, 3000);
          }
          // Auto-refresh if tab is open
          const intPanel = $('tabIntelligence');
          if (intPanel && !intPanel.classList.contains('hidden')) {
            setTimeout(loadIntelligenceDashboard, 500);
          }
        };
      })();

      console.debug('[Phase 34] Semi-AGI Intelligence Dashboard active.');

      /* ================================================================
         PHASE 35 — ENTERPRISE DASHBOARD JS
         ================================================================ */
      const _p35_origSetTab = window.setActiveTab;
      window.setActiveTab = function (tab) {
        _p35_origSetTab(tab);
        const ep = $('tabEnterprise');
        if (!ep) return;
        if (tab === 'enterprise') { ep.classList.remove('hidden'); loadEnterpriseDashboard(); }
        const ev = $('tabEvolution');
        if (ev) {
          if (tab === 'evolution') { ev.classList.remove('hidden'); loadEvolutionDashboard(); }
          else ev.classList.add('hidden');
        }
        const wk = $('tabWorker');
        if (wk) {
          if (tab === 'worker') { wk.classList.remove('hidden'); loadWorkerDashboard(); startWorkerPoll(); }
          else { wk.classList.add('hidden'); stopWorkerPoll(); }
        }
        const pr = $('tabProjects');
        if (pr) {
          if (tab === 'projects') { pr.classList.remove('hidden'); loadProjectsDashboard(); startProjectsPoll(); }
          else { pr.classList.add('hidden'); stopProjectsPoll(); }
        }
        const tm = $('tabTeam');
        if (tm) {
          if (tab === 'team') { tm.classList.remove('hidden'); loadTeamDashboard(); startTeamPoll(); }
          else { tm.classList.add('hidden'); stopTeamPoll(); }
        }
        else ep.classList.add('hidden');
      };

      async function loadEnterpriseDashboard() {
        const btn = $('entRefreshBtn');
        if (btn) { btn.disabled = true; btn.textContent = '⟳ Loading…'; }
        try {
          const [hr, qr, cr, mr, hw] = await Promise.all([
            api('GET', '/api/system/health'),
            api('GET', '/api/queue/snapshot'),
            api('GET', '/api/costs/totals'),
            api('GET', '/api/models/status'),
            api('GET', '/api/hardware/status')
          ]);
          const h = hr.data || {};
          _entHealth(h.system || {}, h.sandbox || {}, hw.data || {});
          _entQueue(qr.data || {});
          _entCosts(cr.data || {});
          _entSandbox(h.sandbox || {});
          _entModels((mr.data || {}).providers || [], hw.data || {});
          _entCaps(h.capabilities || []);
          const b = $('entBadge'); if (b) b.textContent = 'P35 ✓';
        } catch (e) { console.error('[P35]', e); }
        finally {
          if (btn) { btn.disabled = false; btn.textContent = '⟳ Refresh'; }
          renderGovernance(gov);
        }
      }

      function _m(label, value, unit, color) {
        return `<div style="padding:10px 12px;border-radius:8px;background:${color}11;border:1px solid ${color}33;text-align:center">
        <div style="font-size:1.3rem;font-weight:800;color:${color}">${value}${unit}</div>
        <div style="font-size:0.68rem;color:var(--muted);margin-top:2px">${label}</div></div>`;
      }
      function _entHealth(s, sb, hw) {
        const el = $('entHealth'); if (!el) return;
        const lrm = hw.low_resource_mode ? '<div style="grid-column:1/-1;margin-top:8px;padding:4px;background:#d2992222;color:#d29922;border:1px solid #d29922;border-radius:4px;font-size:0.75rem;font-weight:bold;text-align:center">⚠️ LOW RESOURCE MODE ACTIVE</div>' : '';
        let cpu = hw.cpu_pct; if (cpu === undefined) cpu = s.cpu_pct;
        let mem = hw.mem_pct; if (mem === undefined) mem = s.mem_pct;
        el.innerHTML = _m('CPU', cpu ?? '–', '%', '#f0883e') + _m('Memory', mem ?? '–', '%', '#388bfd')
          + _m('Disk', s.disk_pct ?? '–', '%', '#d29922') + _m('Sandbox', sb.docker ? '🐳 Docker' : '🔒 Process', '', '#3fb950')
          + lrm;
      }
      function _entQueue(q) {
        const el = $('entQueue'), badge = $('entQueueBadge'), we = $('entWorkers');
        if (!el) return;
        const by = q.by_status || {};
        const total = Object.values(by).reduce((a, b) => a + b, 0);
        if (badge) badge.textContent = total + ' tasks';
        const cols = [{ s: 'running', c: '#3fb950' }, { s: 'queued', c: '#388bfd' }, { s: 'done', c: '#8b949e' }, { s: 'failed', c: '#f85149' }];
        el.innerHTML = `<div style="display:grid;grid-template-columns:1fr 1fr;gap:4px">
        ${cols.filter(c => by[c.s]).map(c => `<div style="display:flex;justify-content:space-between;padding:4px 6px;border-radius:4px;background:${c.c}11;border:1px solid ${c.c}33">
            <span style="color:${c.c}">${c.s}</span><strong style="color:${c.c}">${by[c.s] || 0}</strong></div>`).join('')}
    </div><div style="margin-top:6px;font-size:0.7rem;color:var(--muted)">${q.n_workers || 0} workers • Redis: ${q.redis ? '✅' : '⬜'}</div>`;
        if (we && q.running) we.innerHTML = q.running.slice(0, 5).map(t =>
          `<div style="font-size:0.7rem;padding:2px 6px;margin-bottom:3px;border-radius:4px;background:#3fb95022;color:#3fb950;border:1px solid #3fb95044;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">▶ ${escapeHtml(t.name || t.id)}</div>`).join('');
      }
      function _entCosts(d) {
        const el = $('entCosts'); if (!el) return;
        const t = d.totals || {};
        el.innerHTML = `<div style="display:grid;grid-template-columns:1fr 1fr;gap:6px">
        ${[{ l: 'Tokens', v: (t.tokens || 0).toLocaleString(), c: '#388bfd' }, { l: 'Cost', v: '$' + (t.cost_usd || 0).toFixed(4), c: '#d29922' },
          { l: 'Runs', v: t.runs || 0, c: '#3fb950' }, { l: 'Over budget', v: t.over_budget || 0, c: '#f85149' }]
            .map(i => `<div style="padding:6px 8px;border-radius:6px;background:${i.c}11;border:1px solid ${i.c}33">
            <div style="font-weight:700;color:${i.c};font-size:0.9rem">${i.v}</div>
            <div style="color:var(--muted);font-size:0.65rem">${i.l}</div></div>`).join('')}
    </div>`;
      }
      function _entSandbox(sb) {
        const el = $('entSandbox'), badge = $('entSandboxBadge'); if (!el) return;
        if (badge) badge.textContent = sb.docker ? 'Docker' : 'Process';
        el.innerHTML = `<div style="display:grid;grid-template-columns:1fr 1fr;gap:6px">
        ${[{ l: 'Backend', v: sb.backend || 'process', c: '#3fb950' }, { l: 'Total', v: sb.total_runs || 0, c: '#388bfd' },
          { l: 'OK', v: sb.ok || 0, c: '#3fb950' }, { l: 'Failed', v: sb.failed || 0, c: '#f85149' }]
            .map(i => `<div style="padding:5px 8px;border-radius:5px;background:${i.c}11;border:1px solid ${i.c}33">
            <div style="font-weight:700;color:${i.c}">${i.v}</div>
            <div style="color:var(--muted);font-size:0.65rem">${i.l}</div></div>`).join('')}
    </div>`;
      }
      window.toggleLocalModels = async function (checked) {
        window._allowLocalModels = checked;
        try {
          await api('POST', '/api/models/allow_local', { allow: checked });
          loadEnterpriseDashboard();
        } catch (e) { console.error('Failed to toggle local models', e); }
      };

      function _entModels(providers, hw) {
        const el = $('entModels'); if (!el) return;
        let warningHtml = '';
        if (hw && hw.ollama_eligible === false) {
          warningHtml = '<div style="margin-bottom:8px;padding:6px;background:#f8514922;color:#f85149;border:1px solid #f85149;border-radius:4px;font-size:0.75rem;font-weight:bold;">⚠️ Low RAM detected. Running local models may crash the system.</div>';
        }
        const toggleHtml = `<div style="margin-bottom:10px;font-size:0.8rem;display:flex;align-items:center;gap:6px">
        <input type="checkbox" id="allowLocalModelsCheckbox" ${window._allowLocalModels ? 'checked' : ''} onchange="toggleLocalModels(this.checked)">
        <label for="allowLocalModelsCheckbox" style="color:var(--text);cursor:pointer">Enable Local Models (Advanced)</label>
    </div>`;

        if (!providers.length) { el.innerHTML = warningHtml + toggleHtml + '<span style="color:var(--muted)">No providers.</span>'; return; }

        const cards = providers.map(p => {
          const c = p.available ? '#3fb950' : '#555';
          return `<div style="flex:1;min-width:120px;padding:8px 10px;border-radius:8px;background:${c}11;border:1px solid ${c}33;opacity:${p.available ? 1 : 0.5}">
            <div style="font-weight:700;font-size:0.75rem;color:${c}">${p.key}</div>
            <div style="font-size:0.65rem;color:var(--muted)">${escapeHtml(p.model || '')}</div>
            <div style="font-size:0.6rem;color:${p.available ? '#3fb950' : '#f85149'};margin-top:4px">${p.available ? '&#9679; ready' : '&#9675; offline'}</div>
        </div>`;
        }).join('');
        el.innerHTML = warningHtml + toggleHtml + `<div style="display:flex;flex-wrap:wrap;gap:8px">${cards}</div>`;
      }
      function _entCaps(caps) {
        const el = $('entCaps'); if (!el) return;
        const cs = ['#f0883e', '#3fb950', '#388bfd', '#bc8cff', '#d29922', '#58a6ff', '#79c0ff', '#f0883e'];
        el.innerHTML = caps.map((c, i) => `<div style="padding:4px 10px;border-radius:12px;font-size:0.72rem;background:${cs[i % cs.length]}22;border:1px solid ${cs[i % cs.length]}55;color:${cs[i % cs.length]}">${c}</div>`).join('');
      }

      async function runEntWorkflow() {
        const btn = $('entRunBtn'), resEl = $('entWorkflowResult');
        const task = ($('entWorkflowTask') || {}).value || '';
        const wf = ($('entWorkflowSel') || {}).value || 'generate_and_test';
        const lang = ($('entLangSel') || {}).value || 'python';
        if (!task.trim()) { alert('Enter a task.'); return; }
        if (btn) { btn.disabled = true; btn.textContent = '⟳ Running…'; }
        if (resEl) { resEl.style.display = 'block'; resEl.innerHTML = '<span style="color:var(--muted)">Running…</span>'; }
        try {
          const r = await api('POST', '/api/workflows/run', { workflow: wf, task, language: lang, budget_usd: 0.10 });
          const res = (r.data || {}).result || r.data || {};
          const ok = res.ok;
          const rows = (res.steps || []).map(s =>
            `<div style="padding:3px 0;border-bottom:1px solid #ffffff08">
                <span style="color:${s.ok ? '#3fb950' : '#f85149'}">${s.ok ? '✓' : '✗'}</span>
                <strong style="color:var(--text);margin-left:4px">${s.name}</strong>
                <span style="color:var(--muted);font-size:0.68rem;margin-left:6px">${s.elapsed_s}s${s.error ? ' — ' + escapeHtml((s.error || '').slice(0, 60)) : ''}</span>
            </div>`).join('');
          const codeHtml = res.code ? `<div style="margin-top:8px;background:#0d1117;border-radius:4px;padding:6px;font-family:monospace;font-size:0.7rem;max-height:120px;overflow-y:auto;color:#e6edf3">${escapeHtml(res.code.slice(0, 800))}</div>` : '';
          if (resEl) resEl.innerHTML = `<div style="color:${ok ? '#3fb950' : '#f85149'};font-weight:700;margin-bottom:6px">${ok ? '✅ PASSED' : '❌ FAILED'} — ${res.elapsed_s}s</div>${rows}${codeHtml}`;
        } catch (e) { if (resEl) resEl.innerHTML = `<span style="color:#f85149">Error: ${escapeHtml(String(e))}</span>`; }
        finally { if (btn) { btn.disabled = false; btn.textContent = '▶ Run'; } }
        renderGovernance(gov);
      }

      setInterval(() => {
        const ep = $('tabEnterprise');
        if (ep && !ep.classList.contains('hidden')) {
          api('GET', '/api/queue/snapshot').then(r => _entQueue(r.data || {}));
          api('GET', '/api/costs/totals').then(r => _entCosts(r.data || {}));
        }
      }, 8000);

      console.debug('[Phase 35] Enterprise Autonomous System Dashboard active.');
