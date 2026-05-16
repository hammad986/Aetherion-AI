// ════════════════════════════════════════════════════════════════════
// NX — Advanced UI Runtime (replaces p57 bridge)
// Wires legacy content into new panel/tab slots
// ════════════════════════════════════════════════════════════════════

    // Ensure we are using the unified global state from boot.js
    const NX = window.NX;

    // ── Slot map: nxTabId → legacy element ID ──
    const NX_TAB_MAP = {
      logs: 'tabLogs',
      preview: 'tabPreview',
      code: 'tabFiles',
      terminal: 'tabTerminal',
      metrics: 'tabObservability',
      agents: 'tabAgents',
      timeline: 'tabTimeline',
      steps: 'tabSteps',
    };

    // ── Tab actions ──
    const NX_TAB_ACTIONS = {
      logs: `<label style="display:flex;align-items:center;gap:4px;font-size:11px;color:var(--text-muted);cursor:pointer"><input type="checkbox" id="quietMode" checked> Quiet</label>
         <label style="display:flex;align-items:center;gap:4px;font-size:11px;color:var(--text-muted);cursor:pointer"><input type="checkbox" id="autoScroll" checked> Auto-scroll</label>
         <button class="nx-tiny-btn" onclick="clearLogView()">Clear</button>`,
      preview: `<span style="font-size:11px;color:var(--text-muted)" id="previewHint">Live preview</span>
            <button class="nx-tiny-btn" onclick="reloadPreview()">Reload</button>
            <button class="nx-tiny-btn" onclick="openPreviewWindow()">Open ↗</button>`,
      code: `<span style="font-size:11px;color:var(--text-muted)" id="filesCount">0 files</span>
         <button class="nx-tiny-btn" id="fsBtnNewFile" onclick="fsCreateFile()" disabled>+ File</button>
         <button class="nx-tiny-btn" id="fsBtnNewFolder" onclick="fsCreateFolder()" disabled>+ Folder</button>
         <button class="nx-tiny-btn" id="fsBtnRename" onclick="fsRename()" disabled>Rename</button>
         <button class="nx-tiny-btn" id="fsBtnDelete" onclick="fsDelete()" disabled>Delete</button>
         <button class="nx-tiny-btn" onclick="loadFilesTree()">Refresh</button>
         <button class="nx-tiny-btn" onclick="downloadProject()">Download .zip</button>`,
      terminal: `<span style="font-size:11px;color:var(--text-muted)" id="terminalCwd">cwd: workspace</span>
             <span id="terminalModeBadge" style="font-size:10px;padding:2px 7px;border-radius:8px;background:var(--blue-dim);color:var(--blue);border:1px solid rgba(59,130,246,0.3)">sandbox</span>
             <button class="nx-tiny-btn" onclick="terminalToggleSettings()">Settings</button>
             <button class="nx-tiny-btn" onclick="terminalClear()">Clear</button>
             <button class="nx-tiny-btn" onclick="terminalLoadHistory()">History</button>`,
      metrics: `<button class="nx-tiny-btn" onclick="nxRefreshMetrics()">Refresh</button>`,
      agents: `<button class="nx-tiny-btn" onclick="loadAgentsState()">Refresh</button>`,
      timeline: `<button class="nx-tiny-btn" onclick="loadTimeline()">Refresh</button>`,
      steps: `<button class="nx-tiny-btn" onclick="loadStepTrace()">Refresh</button>`,
      live: `<span style="font-size:11px;color:var(--text-muted)">Real-time execution view</span>
          <button class="nx-tiny-btn" onclick="NxExecVis.clearLive()">Clear</button>
          <button class="nx-tiny-btn" onclick="nxSetTab('logs')">← Logs</button>`,
    };

    let uiInitialized = false;
    const NX_LAYOUT_STORE = 'layoutSizes';
    const nxFlag = (name) => !!(window.NX && typeof window.NX.hasDebugFlag === 'function' && window.NX.hasDebugFlag(name));
    const nxStep = (name) => console.log(`[NX:UI] ${name}`);

    function nxReadLayoutStore() {
      try { return JSON.parse(localStorage.getItem(NX_LAYOUT_STORE) || '{}'); } catch { return {}; }
    }

    function nxWriteLayoutStore(patch) {
      const next = { ...nxReadLayoutStore(), ...patch };
      try { localStorage.setItem(NX_LAYOUT_STORE, JSON.stringify(next)); } catch (_) { }
    }

    function nxHydrateLayoutState() {
      if (nxFlag('layoutrestore')) return;
      const stored = nxReadLayoutStore();
      if (Number.isFinite(stored.leftW)) {
        NX.leftW = stored.leftW;
        NX.leftOpen = stored.leftW > 20;
      }
      if (Number.isFinite(stored.rightW)) {
        NX.rightW = stored.rightW;
        NX.rightOpen = stored.rightW > 20;
      }
      if (typeof window.NX?.logLayoutDiagnostic === 'function') {
        window.NX.logLayoutDiagnostic({
          source: 'ui-hydrate',
          leftW: NX.leftW,
          rightW: NX.rightW,
        });
      }
    }

    function nxInit() {
      if (uiInitialized) return;
      uiInitialized = true;
      nxStep('init:start');
      // 1. Move legacy content into center tab slots
      if (!nxFlag('ui_tabs')) {
        nxStep('init:tabs');
        for (const [tabId, legacyId] of Object.entries(NX_TAB_MAP)) {
          const slot = document.getElementById('nxTab-' + tabId);
          const legacy = document.getElementById(legacyId);
          if (slot && legacy) {
            legacy.classList.remove('hidden');
            legacy.style.display = 'flex';
            legacy.style.height = '100%';
            legacy.style.overflow = 'auto';
            slot.appendChild(legacy);
          }
        }
      }

      // 2. Move thinking elements into left panel
      const el = (id) => document.getElementById(id);
      if (!nxFlag('ui_leftpanel')) {
        nxStep('init:left-panel');
        const thSlot = document.getElementById('nxThoughtSlot');
        const decSlot = document.getElementById('nxDecisionSlot');
        const recSlot = document.getElementById('nxRecallSlot');
        if (el('rsListThoughts') && thSlot) thSlot.appendChild(el('rsListThoughts'));
        if (el('rsListDecisions') && decSlot) decSlot.appendChild(el('rsListDecisions'));
        if (el('rsListRecall') && recSlot) recSlot.appendChild(el('rsListRecall'));
      }

      // 3. Move inspector elements into right panel
      if (!nxFlag('ui_rightpanel')) {
        nxStep('init:right-panel');
        const decSlot2 = document.getElementById('nxDecisionSlot2');
        const outSlot = document.getElementById('nxOutputSlot');
        if (el('decisionList') && decSlot2) {
          decSlot2.innerHTML = '';
          decSlot2.appendChild(el('decisionList'));
        }
        if (el('outResult') && outSlot) {
          outSlot.innerHTML = '';
          outSlot.appendChild(el('outResult'));
        }
      }

      // 4. Move HITL panel
      const hitlEl = el('hitlPanel');
      if (hitlEl) {
        hitlEl.style.display = 'none'; // managed by nxUpdateHitl
      }

      // 5. Defer non-critical startup tasks to NX_LOAD_TASKS
      if (!nxFlag('ui_background')) {
        nxStep('init:background-enqueue');
        window.NX_LOAD_TASKS.push(nxInitBackgroundTasks);
      }

      // 6. Keyboard shortcuts
      if (!nxFlag('ui_keydown')) {
        nxStep('init:keydown');
        document.addEventListener('keydown', nxKeydown);
      }

      // 7. Set initial tab actions
      nxStep('init:tab-actions');
      nxUpdateTabActions('logs');

      // 8. Drag handles
      if (!nxFlag('ui_drag')) {
        nxStep('init:drag');
        nxSetupDrag();
      }

      // 9. Apply initial layout (left panel collapsed)
      if (!nxFlag('layoutrestore')) {
        nxStep('init:layout');
        nxHydrateLayoutState();
        nxApplyLayout();
      }

      // 10. Auto-focus the command bar so user can type immediately
      if (!nxFlag('ui_autofocus')) {
        nxStep('init:autofocus');
        setTimeout(() => {
          const inp = document.getElementById('taskInput');
          if (inp) inp.focus();
        }, 120);
      }

      // 11. Set initial plan mode UI (silent — no toast on load)
      if (!nxFlag('ui_planinit')) {
        nxStep('init:plan');
        nxSetPlan(NX.planMode, true);
      }

      nxStep('init:end');
      console.log('[NX] Advanced UI (Critical) initialized');
    }

    // Deferred non-critical tasks
    function nxInitBackgroundTasks() {
        console.log('[NX] Initializing background tasks...');
        // Start metric polling
        nxStartMetrics();
        // Load initial state
        nxLoadProviders();
        nxLoadSessions();
        // Queue display
        setInterval(nxPollQueue, 3000);
        // Rotating placeholder text in command bar
        nxStartPlaceholderRotation();
        // Start with metrics collapsed
        nxSetMetricsExpanded(false);
        // Watch decisions/output slots
        nxWatchInspectorSlots();
        // Drag & drop on command bar
        nxInitDragDrop();
        console.log('[NX] Background tasks initialized');
    }

    // ── Rotating placeholder text ──
    function nxStartPlaceholderRotation() {
      const prompts = [
        'Describe what you want to build... (Ctrl+Enter to run)',
        'Build a Flask REST API with user auth...',
        'Create a React dashboard with live charts...',
        'Fix the bug on line 42 of app.py...',
        'Add file upload to my existing API...',
        'Write unit tests for the auth module...',
        'Refactor Python files to use async/await...',
        'Build a real-time chat feature with WebSockets...',
      ];
      let idx = 0;
      const inp = document.getElementById('taskInput');
      if (!inp) return;
      setInterval(() => {
        if (document.activeElement === inp || inp.value) return;
        idx = (idx + 1) % prompts.length;
        inp.placeholder = prompts[idx];
      }, 3500);
    }

    // ── Tab switching ──
    function nxSetTab(id) {
      // Hide idle hero when any tab is selected
      nxHideHero();

      // Hide all tab contents
      document.querySelectorAll('.nx-tab-content').forEach(el => el.classList.remove('active'));
      // Deactivate all tab buttons (primary + secondary)
      document.querySelectorAll('.nx-tab').forEach(btn => btn.classList.remove('active'));

      // Show target content
      const content = document.getElementById('nxTab-' + id);
      const btn = document.querySelector(`.nx-tab:not(.secondary)[data-nxtab="${id}"]`) ||
        document.querySelector(`[data-nxtab="${id}"]`);
      if (content) content.classList.add('active');
      if (btn) btn.classList.add('active');
      NX.activeTab = id;

      // Update "More" dropdown active states
      document.querySelectorAll('.nx-more-item').forEach(item => {
        item.classList.toggle('active', item.dataset.nxtab === id);
      });

      nxUpdateTabActions(id);

      // Tab-specific side effects
      if (id === 'code') { if(typeof setActiveTab==='function')setActiveTab('files'); if(typeof loadFilesTree==='function')loadFilesTree(); }
      if (id === 'preview') if(typeof setActiveTab==='function')setActiveTab('preview');
      if (id === 'agents') if(typeof loadAgentsState==='function')loadAgentsState();
      if (id === 'timeline') if(typeof loadTimeline==='function')loadTimeline();
      if (id === 'steps') if(typeof loadStepTrace==='function')loadStepTrace();
      if (id === 'metrics') nxRefreshMetrics();
      if (id === 'terminal') nxEnsureTerminal();
    }

    // ── Idle hero helpers ──
    function nxHideHero() {
      const hero = document.getElementById('nxIdleHero');
      if (hero) hero.classList.add('hidden');
    }
    function nxShowHero() {
      const hero = document.getElementById('nxIdleHero');
      if (hero) hero.classList.remove('hidden');
      // Deactivate tab contents when hero is shown
      document.querySelectorAll('.nx-tab-content').forEach(el => el.classList.remove('active'));
    }

    // ── More dropdown ──
    function nxToggleMore(e) {
      e.stopPropagation();
      const btn = document.getElementById('nxMoreBtn');
      const dd = document.getElementById('nxMoreDropdown');
      if (!dd) {
        if (typeof window.NX?.logInteractionFailure === 'function') {
          window.NX.logInteractionFailure('more-menu-missing', {});
        }
        return;
      }
      const open = dd.classList.toggle('open');
      if (btn) {
        btn.classList.toggle('open', open);
        btn.setAttribute('aria-expanded', open ? 'true' : 'false');
      }
      if (open) {
        const close = (ev) => { if (!dd.contains(ev.target)) { nxCloseMore(); document.removeEventListener('click', close); } };
        setTimeout(() => document.addEventListener('click', close), 0);
      }
    }
    function nxCloseMore() {
      const btn = document.getElementById('nxMoreBtn');
      const dd = document.getElementById('nxMoreDropdown');
      if (btn) {
        btn.classList.remove('open');
        btn.setAttribute('aria-expanded', 'false');
      }
      if (dd) dd.classList.remove('open');
    }

    function nxUpdateTabActions(id) {
      const bar = document.getElementById('nxTabActions');
      if (bar) bar.innerHTML = NX_TAB_ACTIONS[id] || '';
    }

    // ── Panel toggles ──
    function nxToggleLeft() {
      NX.leftOpen = !NX.leftOpen;
      NX.leftW = NX.leftOpen ? 240 : 0;
      nxApplyLayout();
    }
    function nxToggleRight() {
      NX.rightOpen = !NX.rightOpen;
      NX.rightW = NX.rightOpen ? 290 : 0;
      nxApplyLayout();
    }
    function nxToggleBottom() {
      NX.bottomOpen = !NX.bottomOpen;
      const b = document.getElementById('nxBottom');
      const hint = document.getElementById('nxBottomHint');
      if (b) b.classList.toggle('open', NX.bottomOpen);
      if (hint) hint.textContent = NX.bottomOpen ? 'Click to collapse' : 'Click to expand';
      if (NX.bottomOpen) nxEnsureTerminal();
    }

    let layoutUpdateRequested = false;
    function nxApplyLayout() {
      if (layoutUpdateRequested) return;
      layoutUpdateRequested = true;
      requestAnimationFrame(() => {
        document.documentElement.style.setProperty('--leftW', (NX.leftW || 0) + 'px');
        document.documentElement.style.setProperty('--rightW', (NX.rightW || 0) + 'px');
        nxWriteLayoutStore({
          leftW: NX.leftW || 0,
          rightW: NX.rightW || 0,
        });
        layoutUpdateRequested = false;
      });
    }

    // ── Drag handles ──
    function nxSetupDrag() {
      const handles = [
        { el: 'nxDivLeft', var: 'leftW', rev: false, min: 0, max: 480 },
        { el: 'nxDivRight', var: 'rightW', rev: true, min: 0, max: 480 },
      ];
      handles.forEach(h => {
        const el = document.getElementById(h.el);
        if (!el) return;
        el.addEventListener('mousedown', (e) => {
          e.preventDefault();
          const startX = e.clientX;
          const startW = NX[h.var];
          el.classList.add('dragging');
          document.body.classList.add('nx-resizing');
          const onMove = (me) => {
            let delta = me.clientX - startX;
            if (h.rev) delta = -delta;
            NX[h.var] = Math.max(h.min, Math.min(h.max, startW + delta));
            if (h.var === 'leftW') NX.leftOpen = NX.leftW > 20;
            if (h.var === 'rightW') NX.rightOpen = NX.rightW > 20;
            nxApplyLayout();
          };
          const onUp = () => {
            el.classList.remove('dragging');
            document.body.classList.remove('nx-resizing');
            document.removeEventListener('mousemove', onMove);
            document.removeEventListener('mouseup', onUp);
          };
          document.addEventListener('mousemove', onMove);
          document.addEventListener('mouseup', onUp);
        });
      });
    }

    // ── Live metrics ──
    async function nxRefreshMetrics() {
      try {
        const [hr, mr] = await Promise.all([
          fetch('/api/health'),
          fetch('/api/system/metrics'),
        ]);
        const set = (id, val) => { const e = document.getElementById(id); if (e) e.textContent = val; };
        const setW = (id, w) => { const e = document.getElementById(id); if (e) e.style.width = Math.min(100, w) + '%'; };

        if (hr.ok) {
          const hd = await hr.json();
          const sys = hd.system || {};
          const cpu = sys.cpu_pct || 0;
          const mem = sys.mem_used_pct || 0;
          set('nxMetCpu', cpu.toFixed(1) + '%');
          set('nxMetMem', mem.toFixed(1) + '%');
          setW('nxMetCpuBar', cpu);
          setW('nxMetMemBar', mem);

          const sess = hd.sessions || {};
          set('nxMetSess', (sess.total || 0) + ' (' + (sess.running || 0) + ' live)');

          set('nxMetUptime', sys.mem_used_gb ? sys.mem_used_gb.toFixed(1) + ' GB' : '—');
        }

        if (mr.ok) {
          const md = await mr.json();
          const m = md.metrics || {};
          const calls = m.total_calls || 0;
          const fallbacks = m.fallbacks || 0;
          set('nxTokenCount', calls + (fallbacks ? ' (' + fallbacks + ' fb)' : ''));

          const providers = md.providers || [];
          const hasAvailable = providers.some(p => p.available);
          const dot = document.getElementById('nxModelDot');
          const sbDot = document.getElementById('nxSbDot');
          const color = hasAvailable ? 'var(--green)' : 'var(--yellow)';
          if (dot) dot.style.background = color;
          if (sbDot) sbDot.style.background = color;
        }
      } catch (e) { }
    }

    function nxStartMetrics() {
      if (nxFlag('metrics')) return;
      nxRefreshMetrics();
      NX.metricTimer = setInterval(nxRefreshMetrics, 8000);
    }

    // ── Provider/model loading ──
    async function nxLoadProviders() {
      try {
        const [cr, mr] = await Promise.all([
          fetch('/api/get-config'),
          fetch('/api/system/metrics'),
        ]);

        if (cr.ok) {
          const raw = await cr.json();
          const cfg = raw.config || raw;
          const mode = cfg.mode || 'managed';
          const fallback = (cfg.fallback_order || [])[0] || '—';
          const keysSet = Object.keys(cfg.api_keys_set || {});
          const activeProv = keysSet.length ? keysSet[0] : fallback;

          const set = (id, val) => { const e = document.getElementById(id); if (e) e.textContent = val; };
          set('nxInspProvider', activeProv);
          set('nxInspMode', mode === 'byok' ? 'BYOK — your keys' : 'Managed — platform keys');
          set('nxSbMode', mode === 'byok' ? 'BYOK' : 'Managed');
        }

        if (mr.ok) {
          const md = await mr.json();
          const providers = md.providers || [];
          const available = providers.filter(p => p.available);
          const first = available[0] || providers[0];
          const model = first ? first.model : '—';
          const prov = first ? first.provider : '—';
          const truncModel = model.length > 22 ? model.slice(0, 20) + '…' : model;

          ['nxModelName', 'nxSbModel'].forEach(id => {
            const e = document.getElementById(id);
            if (e) e.textContent = truncModel;
          });
          const inspModel = document.getElementById('nxInspModel');
          if (inspModel) inspModel.textContent = model;
          if (!document.getElementById('nxInspProvider').textContent || document.getElementById('nxInspProvider').textContent === '—') {
            const inspP = document.getElementById('nxInspProvider');
            if (inspP) inspP.textContent = prov;
          }
        }
      } catch (e) { }
    }

    // ── Session polling ──
    async function nxLoadSessions() {
      try {
        const r = await fetch('/api/sessions');
        if (!r.ok) return;
        const d = await r.json();
        const count = Array.isArray(d) ? d.length : 0;
        ['nxSessCount', 'nxMetSess'].forEach(id => {
          const e = document.getElementById(id);
          if (e) e.textContent = count;
        });

        if (count > 0 && !NX.activeSid) {
          const last = d[d.length - 1];
          if (last && last.sid) nxUpdateSessionCard(last);
        }
      } catch (e) { }
    }

    function nxUpdateSessionCard(sess) {
      if (!sess) return;
      NX.activeSid = sess.sid;
      const set = (id, val) => { const e = document.getElementById(id); if (e) e.textContent = val; };
      set('nxCurSid', sess.sid ? sess.sid.slice(-8) : '—');
      set('nxCurProject', sess.project_name || sess.task_preview || '—');
      set('nxCurStatus', sess.status || 'Idle');

      const dl = document.getElementById('nxDownloadSection');
      const dlBtn = document.getElementById('nxDownloadBtn');
      if (dl) dl.style.display = 'block';
      if (dlBtn) dlBtn.onclick = () => { if(typeof downloadProject==='function') downloadProject(); };

      nxSetGlobalStatus(sess.status || 'idle');
    }

    function nxSetGlobalStatus(status) {
      const badge = document.getElementById('nxGlobalStatus');
      const sb = document.getElementById('nxSbStatus');
      const sessEl = document.getElementById('nxCurStatus');
      const runBtn = document.getElementById('runBtn');
      const runLabel = document.getElementById('runBtnLabel');
      const dot = document.getElementById('nxRunDot');
      const thinkPulse = document.getElementById('nxThinkPulse');
      const logPulse = document.getElementById('nxLogPulse');

      const label = status === 'running' ? 'Running' : status === 'error' ? 'Error' : 'Idle';
      const cls = status === 'running' ? 'running' : status === 'error' ? 'error' : '';

      if (badge) { badge.textContent = label; badge.className = 'nx-status-badge ' + cls; }
      if (sb) sb.textContent = label;
      if (sessEl) sessEl.textContent = label;
      if (dot) dot.style.display = status === 'running' ? 'block' : 'none';
      if (thinkPulse) thinkPulse.style.display = status === 'running' ? 'block' : 'none';
      if (logPulse) logPulse.style.display = status === 'running' ? 'block' : 'none';
      if (runBtn) runBtn.classList.toggle('running', status === 'running');
      if (runLabel) runLabel.textContent = status === 'running' ? '■ Stop' : '▶ Run';

      const hitlStrip = document.getElementById('nxHitlStrip');
      if (hitlStrip) hitlStrip.style.display = status === 'running' ? 'block' : 'none';

      const prev = NX.lastStatus;
      NX.lastStatus = status;

      if (status === 'running' && prev !== 'running') {
        if (!NX.leftOpen) {
          NX.leftOpen = true;
          NX.leftW = 240;
          nxApplyLayout();
        }
        nxHideHero();
        nxSetTab('logs');
        nxClearLogsBanner();
        if (NX.idleCollapseTimer) { clearTimeout(NX.idleCollapseTimer); NX.idleCollapseTimer = null; }
        nxSetMetricsExpanded(true);
        const errCard = document.getElementById('nxErrorCard');
        if (errCard) errCard.style.display = 'none';
      }

      if (status === 'error' && prev !== 'error') {
        const right = document.getElementById('nxRight');
        if (right) { right.classList.add('error-pulse'); setTimeout(() => right.classList.remove('error-pulse'), 1200); }
        nxHideHero();
        if (NX.activeTab !== 'logs') nxSetTab('logs');
        nxShowLogsBanner('error');
        const errCard = document.getElementById('nxErrorCard');
        if (errCard) errCard.style.display = 'block';
      }

      if (status === 'idle' && prev === 'running') {
        nxShowLogsBanner('success');
        setTimeout(nxClearLogsBanner, 5000);
        NX.idleCollapseTimer = setTimeout(() => {
          NX.leftOpen = false;
          NX.leftW = 0;
          nxApplyLayout();
        }, 4000);
        setTimeout(() => {
          if (NX.lastStatus === 'idle') nxSetMetricsExpanded(false);
        }, 6000);
      }

      if (status === 'idle' && prev === 'idle') {
        nxSetMetricsExpanded(false);
      }
    }

    let NX_metricsExpanded = true;
    function nxToggleMetrics() {
      NX_metricsExpanded = !NX_metricsExpanded;
      nxSetMetricsExpanded(NX_metricsExpanded);
    }
    function nxSetMetricsExpanded(expanded) {
      NX_metricsExpanded = expanded;
      const body = document.getElementById('nxMetricsBody');
      const icon = document.getElementById('nxMetricsToggleIcon');
      if (body) { body.classList.toggle('expanded', expanded); body.classList.toggle('collapsed', !expanded); }
      if (icon) icon.textContent = expanded ? '▴' : '▾';
    }

    function nxShowLogsBanner(type) {
      const wrap = document.getElementById('nxLogsBanner');
      if (!wrap) return;
      if (type === 'success') {
        wrap.innerHTML = `<div class="nx-success-flash">
      <span class="nx-sf-icon">✓</span>
      <span>Task completed successfully</span>
      <button class="nx-sf-close" onclick="nxClearLogsBanner()">✕</button>
    </div>`;
      } else if (type === 'error') {
        wrap.innerHTML = `<div class="nx-error-highlight-bar">
      <span>⚠ Task encountered an error — check logs below</span>
      <button class="nx-fix-cta" onclick="p57FixError()">Fix with AI</button>
    </div>`;
      }
      wrap.style.display = 'block';
    }
    function nxClearLogsBanner() {
      const wrap = document.getElementById('nxLogsBanner');
      if (wrap) { wrap.innerHTML = ''; wrap.style.display = 'none'; }
    }

    function nxWatchInspectorSlots() {
      if (nxFlag('inspector')) return;
      const checkSlot = (slotId, sectionId) => {
        const slot = document.getElementById(slotId);
        const section = document.getElementById(sectionId);
        if (!slot || !section) return;
        const obs = new MutationObserver(() => {
          const hasContent = slot.textContent.trim().length > 0 &&
            slot.textContent.trim() !== 'No decisions yet.' &&
            slot.textContent.trim() !== 'Nothing yet.';
          section.style.display = hasContent ? 'block' : 'none';
        });
        obs.observe(slot, { childList: true, subtree: true, characterData: true });
      };
      checkSlot('nxDecisionSlot2', 'nxDecisionsSection');
      checkSlot('nxOutputSlot', 'nxOutputSection');
    }

    function nxRunOrStop() {
      const sid = NX.activeSid || (typeof currentSession !== 'undefined' ? currentSession : null);
      if (NX.lastStatus === 'running' && sid) {
        // Use the full stopSession() from runtime.js when available (it also refreshes queue/sessions).
        // Fall back to a bare POST when runtime hasn't loaded yet.
        if (typeof stopSession === 'function') {
          stopSession();
        } else {
          fetch('/api/session/' + sid + '/stop', { method: 'POST' })
            .then(() => { nxSetGlobalStatus('idle'); })
            .catch(() => {});
        }
      } else {
        if (typeof window.NX?.markFirstInteraction === 'function') {
          window.NX.markFirstInteraction();
        }
        nxQueueTask();
      }
    }

    const NX_PLANS = {
      lite: { label: '⚡ Lite', cls: 'lite', color: '#3fb950', icon: '🟢' },
      pro: { label: '🔷 Pro', cls: 'pro', color: '#388bfd', icon: '🔷' },
      elite: { label: '⚡ Elite', cls: 'elite', color: '#bc8cff', icon: '💜' },
    };

    function nxSetPlan(mode, silent) {
      if (!NX_PLANS[mode]) return;
      NX.planMode = mode;
      const p = NX_PLANS[mode];
      const badge = document.getElementById('nxPlanBadge');
      const label = document.getElementById('nxPlanLabel');
      if (badge) { badge.className = 'nx-plan-badge ' + p.cls; }
      if (label) { label.textContent = p.label; }
      ['lite', 'pro', 'elite'].forEach(m => {
        const el = document.getElementById('nxPlanCheck-' + m);
        if (el) el.style.display = m === mode ? '' : 'none';
      });
      document.querySelectorAll('.nx-plan-option').forEach((el, i) => {
        const modes = ['lite', 'pro', 'elite'];
        el.classList.toggle('active', modes[i] === mode);
      });
      const planEl = document.getElementById('nxActivePlanMode');
      if (planEl) { planEl.textContent = p.label; planEl.style.color = p.color; }
      nxClosePlanDropdown();
      if (!silent) toast('Plan mode: ' + p.label.replace(/[⚡🔷💜🔷]/g, '').trim(), 'ok');
    }

    function nxTogglePlanDropdown() {
      const dd = document.getElementById('nxPlanDropdown');
      if (!dd) return;
      NX.planDropdownOpen = !NX.planDropdownOpen;
      dd.classList.toggle('open', NX.planDropdownOpen);
      if (NX.planDropdownOpen) { nxClosePlusMenu(); }
    }
    function nxClosePlanDropdown() {
      const dd = document.getElementById('nxPlanDropdown');
      if (dd) dd.classList.remove('open');
      NX.planDropdownOpen = false;
    }

    function nxTogglePlusMenu() {
      const menu = document.getElementById('nxPlusMenu');
      if (!menu) return;
      NX.plusMenuOpen = !NX.plusMenuOpen;
      menu.classList.toggle('open', NX.plusMenuOpen);
      if (NX.plusMenuOpen) { nxClosePlanDropdown(); }
    }
    function nxClosePlusMenu() {
      const menu = document.getElementById('nxPlusMenu');
      if (menu) menu.classList.remove('open');
      NX.plusMenuOpen = false;
    }
    function nxPlusMenu_file() {
      nxClosePlusMenu();
      const el = document.getElementById('nxFileInput');
      if (el) el.click();
    }
    function nxPlusMenu_image() {
      nxClosePlusMenu();
      const el = document.getElementById('nxImageInput');
      if (el) el.click();
    }
    function nxPlusMenu_folder() {
      nxClosePlusMenu();
      const el = document.getElementById('nxFolderInput');
      if (el) el.click();
    }

    function nxOpenGithubModal() {
      nxClosePlusMenu();
      const ov = document.getElementById('nxGhOverlay');
      if (ov) { ov.classList.add('open'); }
      const inp = document.getElementById('nxGhUrl');
      if (inp) { inp.value = ''; inp.focus(); }
      const prog = document.getElementById('nxGhProgress');
      if (prog) prog.classList.remove('visible');
    }
    function nxCloseGithubModal(ev) {
      if (ev && ev.target !== document.getElementById('nxGhOverlay')) return;
      const ov = document.getElementById('nxGhOverlay');
      if (ov) ov.classList.remove('open');
    }
    async function nxImportGithub() {
      const inp = document.getElementById('nxGhUrl');
      const prog = document.getElementById('nxGhProgress');
      const url = (inp ? inp.value : '').trim();
      if (!url) { toast('Enter a GitHub repo URL', 'err'); return; }
      if (prog) { prog.textContent = '⟳ Cloning repository…'; prog.classList.add('visible'); }
      try {
        const r = await fetch('/api/github/import', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ repo_url: url })
        });
        const d = await r.json();
        if (!r.ok || d.error) {
          if (prog) prog.textContent = '✗ ' + (d.error || 'Import failed');
          toast('GitHub import failed: ' + (d.error || 'unknown'), 'err');
          return;
        }
        NX.ghRepo = { url, name: d.repo_name || url.split('/').pop(), data: d };
        nxAddContextBadge('repo', '🔗 ' + (d.repo_name || 'repo'), url);
        const ta = document.getElementById('taskInput');
        if (ta && !ta.value.trim()) {
          ta.value = 'Analyze the repository at ' + url + ' and help me understand its structure.';
        }
        if (typeof _uploadedContext !== 'undefined') {
          _uploadedContext.push({
            filename: d.repo_name || 'repo',
            context: 'GitHub Repository: ' + url + '\n' + (d.completion_plan || '').slice(0, 1000),
            type: 'repo'
          });
        }
        const ov = document.getElementById('nxGhOverlay');
        if (ov) ov.classList.remove('open');
        toast('✅ Repo imported: ' + (d.repo_name || url), 'ok');
      } catch (e) {
        if (prog) prog.textContent = '✗ Network error';
        toast('GitHub import error: ' + e, 'err');
      }
    }

    function nxAddContextBadge(type, label, detail) {
      const container = document.getElementById('nxCtxBadges');
      const bar = document.getElementById('nxContextBar');
      if (!container || !bar) return;
      const id = 'nxCtx-' + type + '-' + Date.now();
      const badge = document.createElement('div');
      badge.className = 'nx-ctx-badge';
      badge.id = id;
      badge.innerHTML = `<span class="nx-ctx-badge-icon">${label.split(' ')[0]}</span>
    <span>${label.replace(/^[\S]+\s/, '')}</span>
    <button class="nx-ctx-badge-remove" onclick="nxRemoveContextBadge('${id}', '${type}')" title="Remove">×</button>`;
      badge.title = detail || label;
      container.appendChild(badge);
      bar.classList.add('visible');
    }
    function nxRemoveContextBadge(id, type) {
      const el = document.getElementById(id);
      if (el) el.remove();
      if (type === 'repo') { NX.ghRepo = null; }
      if (typeof _uploadedContext !== 'undefined' && type === 'repo') {
        _uploadedContext = _uploadedContext.filter(u => u.type !== 'repo');
      }
      const container = document.getElementById('nxCtxBadges');
      const bar = document.getElementById('nxContextBar');
      if (container && bar && !container.children.length) {
        bar.classList.remove('visible');
      }
    }
    function nxSyncContextBadgesFromChips() {
      const container = document.getElementById('nxCtxBadges');
      const bar = document.getElementById('nxContextBar');
      if (!container || !bar) return;
      Array.from(container.children).forEach(el => {
        if (!el.id.includes('-repo-')) el.remove();
      });
      if (typeof _uploadedContext !== 'undefined') {
        _uploadedContext.forEach((u, i) => {
          if (u.type === 'repo') return;
          const icon = u.type === 'image' ? '🖼' : '📄';
          const badge = document.createElement('div');
          badge.className = 'nx-ctx-badge';
          badge.innerHTML = `<span class="nx-ctx-badge-icon">${icon}</span>
        <span>${u.filename}</span>
        <button class="nx-ctx-badge-remove" onclick="nxRemoveCtxFile(${i})" title="Remove">×</button>`;
          container.appendChild(badge);
        });
        bar.classList.toggle('visible', _uploadedContext.length > 0 || !!NX.ghRepo);
      }
    }
    function nxRemoveCtxFile(idx) {
      if (typeof _uploadedContext !== 'undefined') {
        _uploadedContext.splice(idx, 1);
        if(typeof renderUploadChips === 'function') renderUploadChips();
        nxSyncContextBadgesFromChips();
      }
    }

    function nxInitDragDrop() {
      const wrap = document.getElementById('nxCmdWrap');
      if (!wrap) return;
      wrap.addEventListener('dragover', e => {
        e.preventDefault();
        wrap.classList.add('drag-over');
      });
      wrap.addEventListener('dragleave', e => {
        if (!wrap.contains(e.relatedTarget)) wrap.classList.remove('drag-over');
      });
      wrap.addEventListener('drop', async e => {
        e.preventDefault();
        wrap.classList.remove('drag-over');
        const files = Array.from(e.dataTransfer.files);
        if (!files.length) return;
        for (const file of files.slice(0, 5)) {
          const dt = new DataTransfer();
          dt.items.add(file);
          const fakeInput = { files: dt.files };
          if(typeof handleFileUpload === 'function') await handleFileUpload(fakeInput);
        }
        nxSyncContextBadgesFromChips();
      });
    }

    async function nxHandleFolderUpload(input) {
      const files = Array.from(input.files || []).slice(0, 20);
      if (!files.length) return;
      toast(`Uploading ${files.length} file(s)…`, 'ok');
      let count = 0;
      for (const file of files) {
        const dt = new DataTransfer();
        dt.items.add(file);
        if(typeof handleFileUpload === 'function') await handleFileUpload({ files: dt.files });
        count++;
      }
      nxSyncContextBadgesFromChips();
      toast(`✅ ${count} file(s) attached`, 'ok');
      input.value = '';
    }

    function nxQueueTask() {
      const task = (document.getElementById('taskInput') || {}).value || '';
      if (!task.trim()) { toast('Please describe a task first.', 'err'); return; }
      const model = document.getElementById('modelSelect') ? document.getElementById('modelSelect').value : '';
      if (typeof _uploadedContext !== 'undefined' && _uploadedContext.length > 0) {
        const ta = document.getElementById('taskInput');
        const ctx = _uploadedContext.map(u => u.context).join('\n\n');
        if (ta && ctx) ta.value = ctx + '\n\n' + ta.value;
        _uploadedContext = [];
        if (typeof renderUploadChips === 'function') renderUploadChips();
        nxSyncContextBadgesFromChips();
      }
      const taskFinal = (document.getElementById('taskInput') || {}).value || '';
      fetch('/api/queue-task', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ task: taskFinal, model: model || null, plan_mode: NX.planMode })
      }).then(async r => {
        const d = await r.json().catch(() => ({ ok: false, error: 'Server error' }));
        // ── 403 = plan restriction (Elite/managed quota locked) ────────────────
        if (r.status === 403 || (d.error && /elite|locked|plan|upgrade|limit/i.test(d.error))) {
          nxShowPlanLockedToast(d.error);
          return;
        }
        // ── 429 = rate limited ─────────────────────────────────────────────────
        if (r.status === 429) {
          toast('⏳ Rate limited — please wait a moment.', 'warn'); return;
        }
        if (!d.ok) { toast(d.error || 'Failed to queue task.', 'err'); return; }
        const inp = document.getElementById('taskInput');
        if (inp) inp.value = '';
        NX.activeSid = d.session_id;
        nxSetGlobalStatus('running');
        if (typeof selectSession === 'function') selectSession(d.session_id);
        if (typeof loadSessions === 'function') loadSessions();
        if (typeof loadQueue === 'function') loadQueue();
        const planEl = document.getElementById('nxActivePlanMode');
        const p = NX_PLANS[NX.planMode] || NX_PLANS.elite;
        if (planEl) { planEl.textContent = p.label; planEl.style.color = p.color; }
      }).catch(e => toast('Network error — is the server running? ' + e, 'err'));
    }

    // ── Plan-locked CTA toast ─────────────────────────────────────────────────
    function nxShowPlanLockedToast(serverMsg) {
      // Remove any existing plan-locked toast to avoid stacking
      const existing = document.getElementById('nxPlanLockedToast');
      if (existing) existing.remove();

      const msg = serverMsg && serverMsg.length < 120 ? serverMsg
        : 'Elite runs are locked on your current plan.';

      const t = document.createElement('div');
      t.id = 'nxPlanLockedToast';
      t.style.cssText = [
        'position:fixed;bottom:24px;left:50%;transform:translateX(-50%)',
        'background:#1c1c2e;border:1px solid rgba(188,140,255,.45)',
        'border-radius:10px;padding:12px 18px',
        'display:flex;align-items:center;gap:14px',
        'box-shadow:0 8px 32px rgba(0,0,0,.6)',
        'z-index:99999;max-width:480px;min-width:280px',
        'animation:nxSlideUp .22s ease',
      ].join(';');

      t.innerHTML = `
        <span style="font-size:20px">🔒</span>
        <div style="flex:1">
          <div style="font-size:13px;font-weight:600;color:#e6edf3;margin-bottom:2px">Plan restriction</div>
          <div style="font-size:11px;color:#8b949e;line-height:1.4">${msg}</div>
        </div>
        <button onclick="if(typeof p8OpenUpgradeModal==='function')p8OpenUpgradeModal();document.getElementById('nxPlanLockedToast')?.remove();"
          style="background:linear-gradient(135deg,#bc8cff,#8b5cf6);color:#fff;border:none;border-radius:7px;padding:7px 14px;font-size:12px;font-weight:700;cursor:pointer;white-space:nowrap;font-family:inherit">Upgrade ↗</button>
        <button onclick="this.parentElement.remove()"
          style="background:none;border:none;color:#8b949e;cursor:pointer;font-size:16px;padding:0 2px;line-height:1">✕</button>
      `;
      document.body.appendChild(t);
      // Auto-dismiss after 8s
      setTimeout(() => t.remove(), 8000);
    }

    document.addEventListener('click', e => {
      const planSel = document.getElementById('nxPlanSelector');
      const plusWrap = document.getElementById('nxPlusBtn') && document.getElementById('nxPlusBtn').parentElement;
      if (planSel && !planSel.contains(e.target)) nxClosePlanDropdown();
      if (plusWrap && !plusWrap.contains(e.target)) nxClosePlusMenu();
    });

    async function nxPollQueue() {
      try {
        const r = await fetch('/api/queue');
        if (!r.ok) return;
        const d = await r.json();
        const q = d.queue || [];
        const qc = document.getElementById('nxQueueCount');
        if (qc) qc.textContent = q.length;

        const hasRunning = q.some(t => t.status === 'running');
        const hasError   = q.some(t => t.status === 'error' || t.status === 'failed');
        if (hasRunning) {
          nxSetGlobalStatus('running');
        } else if (hasError) {
          nxSetGlobalStatus('error');
        } else if (q.length > 0 && q.every(t => t.status === 'done' || t.status === 'completed')) {
          nxSetGlobalStatus('idle');
        }
        if(typeof nxLoadSessions === 'function') nxLoadSessions();
      } catch (e) { }
    }

    function nxEnsureTerminal() {
      try {
        const termEl = document.getElementById('tabTerminal');
        if (termEl && typeof terminalInit === 'function') {
        }
      } catch (e) { }
    }

    const NX_PALETTE_ITEMS = [
      { icon: '▶', label: 'Run Task', hint: 'Ctrl+Enter', action: () => { if(typeof nxQueueTask === 'function') nxQueueTask(); } },
      { icon: '📋', label: 'View Logs', hint: '', action: () => nxSetTab('logs') },
      { icon: '👁', label: 'Preview App', hint: '', action: () => nxSetTab('preview') },
      { icon: '📁', label: 'Code Editor', hint: '', action: () => nxSetTab('code') },
      { icon: '💻', label: 'Terminal', hint: '', action: () => nxSetTab('terminal') },
      { icon: '📊', label: 'Metrics', hint: '', action: () => nxSetTab('metrics') },
      { icon: '🤖', label: 'Agent State', hint: '', action: () => nxSetTab('agents') },
      { icon: '📅', label: 'Timeline', hint: '', action: () => nxSetTab('timeline') },
      { icon: '🧐', label: 'Step Trace', hint: '', action: () => nxSetTab('steps') },
      { icon: '⚙', label: 'Settings', hint: '', action: () => { if(typeof openSettings === 'function') openSettings(); } },
      { icon: '📂', label: 'Sessions', hint: '', action: () => nxOpenPanel('sessions') },
      { icon: '🧹', label: 'Clear Memory', hint: '', action: () => { if(typeof clearAgentMemory === 'function') clearAgentMemory(); } },
      { icon: '💾', label: 'Save File', hint: 'Ctrl+S', action: () => typeof saveCurrentFile !== 'undefined' && saveCurrentFile() },
      { icon: '⬇', label: 'Download Project', hint: '', action: () => { if(typeof downloadProject === 'function') downloadProject(); } },
    ];

    let nxPaletteSelected = 0;
    let nxPaletteFiltered = [...NX_PALETTE_ITEMS];

    function nxOpenPalette() {
      const backdrop = document.getElementById('nxPalette');
      const input = document.getElementById('nxPaletteInput');
      if (backdrop) backdrop.classList.add('open');
      if (input) { input.value = ''; input.focus(); }
      nxPaletteSelected = 0;
      nxRenderPalette('');
    }

    function nxClosePalette(e) {
      if (e && e.target !== document.getElementById('nxPalette')) return;
      document.getElementById('nxPalette').classList.remove('open');
    }

    function nxRenderPalette(q) {
      const list = document.getElementById('nxPaletteList');
      if (!list) return;
      nxPaletteFiltered = q
        ? NX_PALETTE_ITEMS.filter(i => i.label.toLowerCase().includes(q.toLowerCase()))
        : NX_PALETTE_ITEMS;
      if (!nxPaletteFiltered.length) {
        list.innerHTML = '<div class="nx-palette-empty">No commands found</div>';
        return;
      }
      list.innerHTML = nxPaletteFiltered.map((item, i) =>
        `<div class="nx-palette-item${i === nxPaletteSelected ? ' selected' : ''}" onclick="nxRunPaletteItem(${i})">
      <span class="nx-palette-item-icon">${item.icon}</span>
      <span class="nx-palette-item-label">${item.label}</span>
      ${item.hint ? `<span class="nx-palette-item-hint"><kbd class="nx-kbd">${item.hint}</kbd></span>` : ''}
    </div>`
      ).join('');
    }

    function nxRunPaletteItem(i) {
      document.getElementById('nxPalette').classList.remove('open');
      nxPaletteFiltered[i]?.action?.();
    }

    window.NX_BOOT_TASKS.push( () => {
      const pinput = document.getElementById('nxPaletteInput');
      if (pinput) {
        pinput.addEventListener('input', e => { nxPaletteSelected = 0; nxRenderPalette(e.target.value); });
        pinput.addEventListener('keydown', e => {
          if (e.key === 'ArrowDown') { e.preventDefault(); nxPaletteSelected = Math.min(nxPaletteSelected + 1, nxPaletteFiltered.length - 1); nxRenderPalette(pinput.value); }
          else if (e.key === 'ArrowUp') { e.preventDefault(); nxPaletteSelected = Math.max(0, nxPaletteSelected - 1); nxRenderPalette(pinput.value); }
          else if (e.key === 'Enter') { e.preventDefault(); e.stopPropagation(); nxRunPaletteItem(nxPaletteSelected); }
          else if (e.key === 'Escape') { document.getElementById('nxPalette').classList.remove('open'); }
        });
      }
    });

    function nxKeydown(e) {
      // Ctrl+Enter — run task (use canonical nxQueueTask, fall back to legacy queueTask)
      if (e.ctrlKey && e.key === 'Enter') {
        e.preventDefault();
        nxRunOrStop();
        return;
      }
      if (e.ctrlKey && e.key === 'k') { e.preventDefault(); nxOpenPalette(); return; }
      if (e.ctrlKey && e.key === ',') { e.preventDefault(); if (typeof openSettings === 'function') openSettings(); return; }
      if (e.ctrlKey && e.key === 's' && NX.activeTab === 'code') { e.preventDefault(); if (typeof saveCurrentFile !== 'undefined') saveCurrentFile(); return; }
      // Ctrl+Shift+E = toggle left (AI Thinking), Ctrl+Shift+I = toggle right (Inspector)
      if (e.ctrlKey && e.shiftKey && e.key === 'E') { e.preventDefault(); if (typeof NxWorkspace !== 'undefined') NxWorkspace.toggleLeft(); else nxToggleLeft(); return; }
      if (e.ctrlKey && e.shiftKey && e.key === 'I') { e.preventDefault(); if (typeof NxWorkspace !== 'undefined') NxWorkspace.toggleRight(); else nxToggleRight(); return; }
      if (e.key === 'Escape') {
        const pal = document.getElementById('nxPalette');
        if (pal) pal.classList.remove('open');
        nxCloseMore();
        // Only call p55ClosePanel if the drawer actually exists
        const drawer = document.getElementById('nxWorkspaceDrawer');
        if (drawer && drawer.classList.contains('open')) nxWsDrawerClose();
      }
    }

    function nxOpenPanel(panel) {
      // Route panel requests: sessions → Settings modal sessions tab,
      // everything else → Settings modal at the relevant tab.
      if (typeof openSettings === 'function') {
        if (panel === 'sessions') {
          openSettings('sessions');
        } else if (panel === 'settings' || !panel) {
          openSettings();
        } else {
          openSettings(panel);
        }
      }
    }

    function nxSetTask(text) {
      const inp = document.getElementById('taskInput');
      if (inp) {
        inp.value = text;
        inp.focus();
        inp.setSelectionRange(text.length, text.length);
      }
      nxHideHero();
    }

    function nxToast(msg, dur = 2500) {
      const t = document.createElement('div');
      t.className = 'nx-toast'; t.textContent = msg;
      document.body.appendChild(t);
      setTimeout(() => t.remove(), dur);
    }

    function p57SetView(v) { if (v === 'preview') nxSetTab('preview'); else nxSetTab('code'); }
    function p57UpdateLayout(col, w) { if (col === 'left') { NX.leftW = w; nxApplyLayout(); } else { NX.rightW = w; nxApplyLayout(); } }
    function p55OpenPanel() { document.getElementById('p57Drawer').classList.add('open'); document.getElementById('p57-overlay').style.display = 'block'; }
    function p55ClosePanel() { document.getElementById('p57Drawer').classList.remove('open'); document.getElementById('p57-overlay').style.display = 'none'; }
    function p57OpenDetail(t, id) {
      const m = document.getElementById('p57-detail-modal'), b = document.getElementById('p57-detail-body');
      document.getElementById('p57-detail-title').textContent = t;
      const el = document.getElementById(id);
      if (el) { b.innerHTML = ''; b.appendChild(el); el.style.display = 'block'; }
      m.style.display = 'flex';
    }
    function p57CloseDetail() { document.getElementById('p57-detail-modal').style.display = 'none'; }
    function p57FixError() {
      const msg = document.getElementById('nxErrorMsg')?.textContent
               || document.getElementById('p57-error-msg')?.textContent
               || '';
      if (!msg) return;
      const inp = document.getElementById('taskInput');
      if (inp) { inp.value = 'Fix this error: ' + msg.slice(0, 200); inp.focus(); }
      // Use the canonical run path — never the stale queueTask alias
      nxQueueTask();
    }

    // NOTE: The MutationObserver on runBtnLabel has been intentionally removed.
    // It created a status feedback loop: nxSetGlobalStatus updates the label text,
    // the observer fires, calls nxSetGlobalStatus again → infinite cycle.
    // Status is now driven exclusively by nxSetGlobalStatus() calls from nxQueueTask
    // and stopSession, which is the canonical single source of truth.

    window.nxSetTab = nxSetTab;
    window.nxSwitchTab = nxSetTab;
    window.nxHideHero = nxHideHero;
    window.nxShowHero = nxShowHero;
    window.nxToggleMore = nxToggleMore;
    window.nxCloseMore = nxCloseMore;
    window.nxToggleLeft = nxToggleLeft;
    window.nxToggleRight = nxToggleRight;
    window.nxToggleBottom = nxToggleBottom;
    window.nxApplyLayout = nxApplyLayout;
    window.nxRunOrStop = nxRunOrStop;
    window.nxSetPlan = nxSetPlan;
    window.nxTogglePlanDropdown = nxTogglePlanDropdown;
    window.nxClosePlanDropdown = nxClosePlanDropdown;
    window.nxTogglePlusMenu = nxTogglePlusMenu;
    window.nxClosePlusMenu = nxClosePlusMenu;
    window.nxPlusMenu_file = nxPlusMenu_file;
    window.nxPlusMenu_image = nxPlusMenu_image;
    window.nxPlusMenu_folder = nxPlusMenu_folder;
    window.nxOpenGithubModal = nxOpenGithubModal;
    window.nxCloseGithubModal = nxCloseGithubModal;
    window.nxQueueTask = nxQueueTask;
    window.nxRunTask = nxQueueTask;
    window.nxEnsureTerminal = nxEnsureTerminal;
    window.nxOpenPalette = nxOpenPalette;
    window.nxClosePalette = nxClosePalette;
    window.nxOpenPanel = nxOpenPanel;
    window.nxToast = nxToast;
    window.nxSetTask = nxSetTask;

    window.p57SetView = p57SetView;
    window.p57UpdateLayout = p57UpdateLayout;
    window.p55OpenPanel = p55OpenPanel;
    window.p55ClosePanel = p55ClosePanel;
    window.p57OpenDetail = p57OpenDetail;
    window.p57CloseDetail = p57CloseDetail;
    window.p57FixError = p57FixError;

    window.NX_BOOT_TASKS.push(nxInit);
