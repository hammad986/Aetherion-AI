/* ════════════════════════════════════════════════════════════════════════
   nx-z55.js — Phase Z55: Live Operational Workspace + Execution Immersion
   Makes Aetherion AI feel alive, operational, intelligent, and trustworthy
   during real usage. Subscribes to Z54's nx:exec:* DOM events — no second
   SSE connections, no extra polling, no new observers beyond one.
   ════════════════════════════════════════════════════════════════════════ */
(function () {
  'use strict';

  const $ = id => document.getElementById(id);
  const qs = (sel, root) => (root || document).querySelector(sel);
  const qsa = (sel, root) => Array.from((root || document).querySelectorAll(sel));
  const set = (id, val) => { const e = $(id); if (e) e.textContent = val; };

  /* ═══════════════════════════════════════════════════════════════════
     Z55A — LIVE EXECUTION PRESENCE CARD
     Injected between activity bar and idle hero. Shown during execution.
     Provides visible execution narrative, stage, timeline, elapsed time.
     ═══════════════════════════════════════════════════════════════════ */

  let _z55ElapsedTick = null;
  let _z55StoryCount = 0;
  let _z55FileCount = 0;
  let _z55CmdCount = 0;

  function z55InjectExecCard() {
    if ($('z55ExecCard')) return;
    const hero = $('nxIdleHero');
    if (!hero) return;

    const card = document.createElement('div');
    card.id = 'z55ExecCard';
    card.className = 'z55-exec-card';
    card.setAttribute('role', 'status');
    card.setAttribute('aria-live', 'polite');
    card.style.display = 'none';
    card.innerHTML = `
      <div class="z55-ec-header">
        <span class="z55-ec-pulse"></span>
        <span class="z55-ec-stage-badge" id="z55EcStage">Initializing</span>
        <span class="z55-ec-sep">·</span>
        <span class="z55-ec-elapsed" id="z55EcElapsed">0s</span>
        <div style="flex:1"></div>
        <button class="z55-ec-stop-btn"
          onclick="if(typeof stopSession==='function')stopSession();else document.dispatchEvent(new Event('nx:stop'))"
          title="Stop execution">■ Stop</button>
      </div>
      <div class="z55-ec-narrative" id="z55EcNarrative">
        Analyzing task and forming execution plan…
      </div>
      <div class="z55-ec-timeline" id="z55EcTimeline"></div>
      <div class="z55-ec-counters" id="z55EcCounters" style="display:none">
        <span class="z55-ec-counter" id="z55EcFiles">0 files</span>
        <span class="z55-ec-counter-sep">·</span>
        <span class="z55-ec-counter" id="z55EcCmds">0 commands</span>
      </div>`;

    hero.parentNode.insertBefore(card, hero);
  }

  function z55ShowExecCard() {
    const card = $('z55ExecCard');
    const hero = $('nxIdleHero');
    if (card) {
      card.style.display = '';
      card.className = 'z55-exec-card running';
    }
    if (hero) hero.style.display = 'none';

    // Reset state
    _z55StoryCount = 0;
    _z55FileCount = 0;
    _z55CmdCount = 0;
    set('z55EcStage', 'Planning');
    set('z55EcNarrative', 'Analyzing task and forming execution plan…');
    set('z55EcFiles', '0 files');
    set('z55EcCmds', '0 commands');
    const tl = $('z55EcTimeline');
    if (tl) tl.innerHTML = '';
    const ctrs = $('z55EcCounters');
    if (ctrs) ctrs.style.display = 'none';

    // Elapsed timer
    clearInterval(_z55ElapsedTick);
    const t0 = Date.now();
    _z55ElapsedTick = setInterval(() => {
      const s = Math.floor((Date.now() - t0) / 1000);
      set('z55EcElapsed', s < 60 ? s + 's' : Math.floor(s / 60) + 'm ' + (s % 60) + 's');
    }, 1000);
  }

  function z55HideExecCard(state) {
    clearInterval(_z55ElapsedTick);
    const card = $('z55ExecCard');
    const hero = $('nxIdleHero');

    if (!card) return;

    if (state === 'complete') {
      card.className = 'z55-exec-card done';
      set('z55EcStage', 'Complete');
      const parts = [];
      if (_z55FileCount) parts.push(_z55FileCount + ' file' + (_z55FileCount !== 1 ? 's' : '') + ' written');
      if (_z55CmdCount) parts.push(_z55CmdCount + ' command' + (_z55CmdCount !== 1 ? 's' : '') + ' run');
      set('z55EcNarrative', parts.length
        ? 'Task completed successfully — ' + parts.join(', ') + '.'
        : 'Task completed successfully.');
      setTimeout(() => {
        card.style.display = 'none';
        card.className = 'z55-exec-card';
        if (hero) hero.style.display = '';
      }, 3800);
    } else if (state === 'failed') {
      card.className = 'z55-exec-card failed';
      set('z55EcStage', 'Failed');
      set('z55EcNarrative', 'Task encountered an error. Review the Output tab for details.');
      setTimeout(() => {
        card.style.display = 'none';
        card.className = 'z55-exec-card';
        if (hero) hero.style.display = '';
      }, 5000);
    } else {
      card.style.display = 'none';
      if (hero) hero.style.display = '';
    }
  }

  /* ═══════════════════════════════════════════════════════════════════
     Z55C — EXECUTION STORYTELLING
     SSE event → human-readable agent narrative
     ═══════════════════════════════════════════════════════════════════ */

  // Rotating thought phrases when no specific content
  const THINK_PHRASES = [
    'Analyzing the task requirements…',
    'Reasoning through the approach…',
    'Reviewing the codebase structure…',
    'Planning the implementation steps…',
    'Evaluating the best strategy…',
    'Considering dependencies and edge cases…',
    'Designing the solution architecture…',
    'Breaking the task into executable steps…',
  ];

  function z55Narrate(data) {
    if (!data) return;
    const type = String(data.type || '').toLowerCase();
    const content = String(data.text || data.content || data.action || data.result || data.message || '').trim();
    const tool = String(data.tool || data.tool_name || '').toLowerCase();
    const path = String(data.path || data.file || data.filename || '').trim();

    let phrase = '';
    let timelineItem = null;
    let stageLabel = null;

    // ── Thought / Planning ─────────────────────────────────────────────
    if (type === 'thought' || type === 'planning') {
      stageLabel = 'Planning';
      phrase = content
        ? z55Trunc(content, 140)
        : THINK_PHRASES[_z55StoryCount % THINK_PHRASES.length];
    }

    // ── File Write ─────────────────────────────────────────────────────
    else if (type === 'file_write' || (type === 'action' && (path || tool.includes('write')))) {
      stageLabel = 'Coding';
      const fname = (path || content).split('/').filter(Boolean).pop() || 'a file';
      phrase = 'Writing ' + fname + '…';
      timelineItem = { icon: '📄', text: path || fname };
      _z55FileCount++;
      z55UpdateCounters();
    }

    // ── Action / Tool Call ─────────────────────────────────────────────
    else if (type === 'action' || type === 'tool_call') {
      stageLabel = 'Executing';
      if (tool.includes('shell') || tool.includes('run') || tool.includes('exec') || tool.includes('bash')) {
        phrase = content ? 'Running: ' + z55Trunc(content, 80) : 'Executing a shell command…';
        timelineItem = { icon: '⚡', text: z55Trunc(content || tool, 72) };
        _z55CmdCount++;
        z55UpdateCounters();
      } else if (tool.includes('read') || tool.includes('view')) {
        phrase = 'Reading ' + (path ? path.split('/').pop() : 'file contents') + '…';
      } else if (tool.includes('search') || tool.includes('grep') || tool.includes('find')) {
        phrase = 'Searching the codebase…';
        timelineItem = { icon: '🔍', text: z55Trunc(content || 'Search', 72) };
      } else if (tool.includes('write') || tool.includes('create')) {
        phrase = 'Creating ' + (path ? path.split('/').pop() : 'a file') + '…';
        timelineItem = { icon: '📄', text: path || 'New file' };
        _z55FileCount++;
        z55UpdateCounters();
      } else if (tool.includes('install') || tool.includes('pip') || tool.includes('npm')) {
        phrase = 'Installing dependencies…';
        timelineItem = { icon: '📦', text: z55Trunc(content || tool, 72) };
        _z55CmdCount++;
        z55UpdateCounters();
      } else if (content) {
        phrase = z55Trunc(content, 140);
        timelineItem = { icon: '🔧', text: z55Trunc(content, 72) };
      }
    }

    // ── Result ─────────────────────────────────────────────────────────
    else if (type === 'result') {
      stageLabel = 'Verifying';
      phrase = content ? z55Trunc(content, 140) : 'Step completed. Continuing…';
      if (data.status === 'success' || data.status === 'ok') {
        timelineItem = { icon: '✓', text: phrase.slice(0, 72), cls: 'ok' };
        stageLabel = 'Done';
      }
    }

    // ── Error ──────────────────────────────────────────────────────────
    else if (type === 'error_event' || type === 'error') {
      stageLabel = 'Debugging';
      phrase = content
        ? 'Encountered an issue: ' + z55Trunc(content, 100)
        : 'Encountered an error — attempting recovery…';
      timelineItem = { icon: '⚠', text: z55Trunc(content || 'Error', 72), cls: 'err' };
    }

    // Apply to UI
    if (phrase) {
      set('z55EcNarrative', phrase);
    }
    if (stageLabel) {
      set('z55EcStage', stageLabel);
    }
    if (timelineItem) {
      z55AddTimelineItem(timelineItem);
    }

    _z55StoryCount++;
  }

  function z55AddTimelineItem({ icon, text, cls }) {
    const tl = $('z55EcTimeline');
    if (!tl) return;
    // Keep only last 4 items
    const existing = tl.querySelectorAll('.z55-tl-row');
    if (existing.length >= 4) existing[0].remove();

    const row = document.createElement('div');
    row.className = 'z55-tl-row' + (cls ? ' ' + cls : '');
    row.innerHTML = `<span class="z55-tl-icon">${icon}</span><span class="z55-tl-text">${z55esc(text)}</span>`;
    tl.appendChild(row);
  }

  function z55UpdateCounters() {
    const ctrs = $('z55EcCounters');
    if (ctrs && (_z55FileCount > 0 || _z55CmdCount > 0)) {
      ctrs.style.display = 'flex';
      set('z55EcFiles', _z55FileCount + ' file' + (_z55FileCount !== 1 ? 's' : ''));
      set('z55EcCmds', _z55CmdCount + ' command' + (_z55CmdCount !== 1 ? 's' : ''));
    }
  }

  /* ═══════════════════════════════════════════════════════════════════
     Z55A — SUBSCRIBE TO Z54 EVENTS
     Clean pub/sub — no second SSE connection, no fetch wrapping.
     ═══════════════════════════════════════════════════════════════════ */

  function z55WireExecEvents() {
    document.addEventListener('nx:exec:start', e => {
      z55ShowExecCard();
    });

    document.addEventListener('nx:exec:sse', e => {
      z55Narrate(e.detail);
    });

    document.addEventListener('nx:exec:end', e => {
      const state = e.detail?.state || 'idle';
      z55HideExecCard(state);
      // Refresh capabilities card after execution
      setTimeout(z55LoadCapabilities, 1000);
    });
  }

  /* ═══════════════════════════════════════════════════════════════════
     Z55B — CENTER WORKSPACE IMMERSION
     Add "System Ready" capabilities card to idle hero.
     ═══════════════════════════════════════════════════════════════════ */

  function z55EnrichIdleHero() {
    const hero = $('nxIdleHero');
    if (!hero || hero.dataset.z55enriched) return;
    hero.dataset.z55enriched = '1';

    // Insert capabilities card before the recent-runs section
    const recentSection = hero.querySelector('.nx-iw-section');
    if (!recentSection) return;

    const card = document.createElement('div');
    card.className = 'z55-caps-card';
    card.id = 'z55CapsCard';
    card.innerHTML = `
      <div class="z55-caps-header">
        <span class="z55-caps-title">System Ready</span>
        <span class="z55-caps-dot" id="z55CapsDot"></span>
        <span class="z55-caps-status" id="z55CapsStatus">Checking…</span>
      </div>
      <div class="z55-caps-grid">
        <div class="z55-cap-tile">
          <span class="z55-cap-icon">🤖</span>
          <span class="z55-cap-name">Model</span>
          <span class="z55-cap-val" id="z55CapModel">—</span>
        </div>
        <div class="z55-cap-tile">
          <span class="z55-cap-icon">🔧</span>
          <span class="z55-cap-name">Tools</span>
          <span class="z55-cap-val" id="z55CapTools">—</span>
        </div>
        <div class="z55-cap-tile">
          <span class="z55-cap-icon">🧠</span>
          <span class="z55-cap-name">Memory</span>
          <span class="z55-cap-val" id="z55CapMem">—</span>
        </div>
        <div class="z55-cap-tile">
          <span class="z55-cap-icon">📊</span>
          <span class="z55-cap-name">Sessions</span>
          <span class="z55-cap-val" id="z55CapSess">—</span>
        </div>
      </div>`;

    hero.insertBefore(card, recentSection);
    z55LoadCapabilities();
  }

  async function z55LoadCapabilities() {
    const statusEl = $('z55CapsStatus');
    const dotEl = $('z55CapsDot');
    try {
      const [mr, tr] = await Promise.all([
        fetch('/api/system/metrics'),
        fetch('/api/tools').catch(() => null),
      ]);

      if (mr.ok) {
        const md = await mr.json();
        const providers = md.providers || [];
        const avail = providers.find(p => p.available);

        if (avail) {
          const name = avail.model || avail.provider || '—';
          set('z55CapModel', name.length > 14 ? name.slice(0, 12) + '…' : name);
          if (statusEl) statusEl.textContent = 'Ready';
          if (dotEl) { dotEl.dataset.state = 'ready'; }
        } else {
          set('z55CapModel', 'None');
          if (statusEl) statusEl.textContent = 'Configure API';
          if (dotEl) { dotEl.dataset.state = 'warn'; }
        }

        const sess = md.sessions?.total ?? md.session_count ?? 0;
        set('z55CapSess', sess || '0');

        const sys = md.system || {};
        const memFree = sys.mem_used_pct != null ? (100 - sys.mem_used_pct).toFixed(0) + '% free' : 'OK';
        set('z55CapMem', memFree);
      }

      // Tool count
      if (tr && tr.ok) {
        const td = await tr.json();
        const count = td.total || td.count || (Array.isArray(td) ? td.length : null) || td.tools?.length;
        set('z55CapTools', count ? count + '' : 'Active');
      } else {
        set('z55CapTools', 'Active');
      }
    } catch (_) {
      if (statusEl) statusEl.textContent = 'Unavailable';
      if (dotEl) dotEl.dataset.state = 'err';
    }
  }

  /* ═══════════════════════════════════════════════════════════════════
     Z55D — REAL CHAT IMMERSION
     Upgrade chat activity messages to agent-voiced conversational style.
     ═══════════════════════════════════════════════════════════════════ */

  function z55UpgradeChatStyle() {
    // Inject CSS overrides for more conversational chat appearance
    if ($('z55-chat-style')) return;
    const style = document.createElement('style');
    style.id = 'z55-chat-style';
    style.textContent = `
      /* Agent messages: warmer, more readable */
      .z54-msg.agent .z54-msg-body {
        background: rgba(255,255,255,.04);
        border-color: rgba(255,255,255,.07);
        line-height: 1.6;
      }

      /* Live activity entries: subtle, italic agent voice */
      .z54-msg.sys.live {
        position: relative;
        padding-left: 10px;
      }
      .z54-msg.sys.live::before {
        content: '';
        position: absolute;
        left: 0;
        top: 4px;
        bottom: 4px;
        width: 2px;
        background: rgba(56,139,253,.35);
        border-radius: 2px;
      }
      .z54-msg.sys.live .z54-msg-body {
        background: transparent;
        border: none;
        color: var(--text-dim, rgba(255,255,255,.4));
        font-style: italic;
        font-size: 10.5px;
        padding: 2px 4px;
      }
      .z54-msg.sys.live .z54-msg-label { display: none; }

      /* User messages: cleaner pill */
      .z54-msg.user .z54-msg-body {
        border-bottom-right-radius: 3px;
      }

      /* System messages: subtle info boxes */
      .z54-msg.sys:not(.live) .z54-msg-body {
        border-radius: 4px;
      }`;
    document.head.appendChild(style);
  }

  /* ═══════════════════════════════════════════════════════════════════
     Z55E — HISTORY + SESSION MATURITY
     Add search and time-grouped sections to history panel.
     Uses a MutationObserver on the panel content — only one, fires once.
     ═══════════════════════════════════════════════════════════════════ */

  function z55WatchHistoryPanel() {
    const panelContent = $('nxPanelContent-history');
    if (!panelContent) return;

    const obs = new MutationObserver(() => {
      if ($('z54HistList') && !$('z55HistSearch')) {
        obs.disconnect();
        z55InjectHistorySearch();
        z55InjectTimeGroups();
      }
    });
    obs.observe(panelContent, { childList: true, subtree: true });
  }

  function z55InjectHistorySearch() {
    const toolbar = qs('.z54-hist-toolbar');
    if (!toolbar || $('z55HistSearch')) return;

    const searchWrap = document.createElement('div');
    searchWrap.className = 'z55-hist-search-wrap';
    searchWrap.innerHTML = `
      <input id="z55HistSearch" class="z55-hist-search"
        placeholder="Search sessions…" autocomplete="off" spellcheck="false" />`;
    toolbar.after(searchWrap);

    const input = $('z55HistSearch');
    if (!input) return;

    input.addEventListener('input', function () {
      const q = this.value.toLowerCase().trim();
      qsa('.z54-hist-item').forEach(item => {
        const text = (item.querySelector('.z54-hist-task')?.textContent || '').toLowerCase();
        item.style.display = q && !text.includes(q) ? 'none' : '';
      });
      // Hide/show time-group headers
      qsa('.z55-time-group').forEach(grp => {
        const visible = Array.from(grp.querySelectorAll('.z54-hist-item'))
          .some(i => i.style.display !== 'none');
        grp.style.display = visible ? '' : 'none';
      });
    });
  }

  function z55InjectTimeGroups() {
    // Wait for history list to have items, then group them by time
    const list = $('z54HistList');
    if (!list) return;

    // Watch for history items to be rendered
    const listObs = new MutationObserver(() => {
      const items = list.querySelectorAll('.z54-hist-item');
      if (items.length > 0) {
        listObs.disconnect();
        z55GroupHistoryByTime(list);
      }
    });
    listObs.observe(list, { childList: true });
  }

  // Override z54RefreshHistory to add time grouping after render
  function z55HookHistoryRender() {
    const origRefresh = window.z54RefreshHistory;
    if (typeof origRefresh !== 'function' || window._z55HistHooked) return;
    window._z55HistHooked = true;
    window.z54RefreshHistory = async function () {
      await origRefresh.apply(this, arguments);
      // After Z54 renders, apply time grouping
      setTimeout(() => z55GroupHistoryByTime($('z54HistList')), 50);
    };

    const origFilter = window.z54HistFilter;
    if (typeof origFilter === 'function') {
      window.z54HistFilter = function (btn, filter) {
        origFilter.call(this, btn, filter);
        setTimeout(() => z55GroupHistoryByTime($('z54HistList')), 50);
      };
    }
  }

  function z55GroupHistoryByTime(list) {
    if (!list) return;
    const items = Array.from(list.querySelectorAll('.z54-hist-item'));
    if (!items.length) return;
    // Items already sorted newest-first from Z54
    // Read relative meta from each item
    const now = Date.now() / 1000;
    const groups = [
      { key: 'today',    label: 'Today',      items: [] },
      { key: 'yest',     label: 'Yesterday',  items: [] },
      { key: 'week',     label: 'This Week',  items: [] },
      { key: 'older',    label: 'Older',      items: [] },
    ];

    items.forEach(item => {
      // Try to parse relative time from meta text
      const meta = item.querySelector('.z54-hist-meta')?.textContent || '';
      const group = z55TimeGroup(meta);
      groups.find(g => g.key === group)?.items.push(item);
    });

    // Clear and re-render with group headers
    list.innerHTML = '';
    groups.forEach(g => {
      if (!g.items.length) return;
      const header = document.createElement('div');
      header.className = 'z55-time-group';
      header.innerHTML = `<div class="z55-time-group-label">${g.label}</div>`;
      g.items.forEach(item => header.appendChild(item));
      list.appendChild(header);
    });
  }

  function z55TimeGroup(meta) {
    const m = meta.toLowerCase();
    if (m.includes('just now') || m.match(/\d+s ago/) || (m.match(/(\d+)m ago/) && parseInt(m) <= 60)) {
      return 'today';
    }
    if (m.includes('h ago')) {
      const hrs = parseInt(m.match(/(\d+)h ago/)?.[1] || 0);
      return hrs < 24 ? 'today' : hrs < 48 ? 'yest' : 'week';
    }
    if (m.includes('d ago')) {
      const days = parseInt(m.match(/(\d+)d ago/)?.[1] || 0);
      return days <= 1 ? 'yest' : days <= 7 ? 'week' : 'older';
    }
    if (m.match(/\d+m ago/)) {
      return 'today';
    }
    return 'older';
  }

  /* ═══════════════════════════════════════════════════════════════════
     Z55F — PRODUCT CALMNESS
     Filter low-value toasts. Suppress chatter.
     ═══════════════════════════════════════════════════════════════════ */

  const TOAST_NOISE = [
    /^plan mode:/i,
    /^mode:/i,
    /^scope:/i,
    /^reconnect/i,
    /loaded$/i,
    /initialized$/i,
    /^theme:/i,
    /provider switched/i,
    /^sse connected/i,
    /^heartbeat/i,
    /token.{0,20}refresh/i,
  ];

  function z55FilterToasts() {
    const prev = window.toast;
    if (typeof prev !== 'function' || window._z55ToastFiltered) return;
    window._z55ToastFiltered = true;
    window.toast = function (msg, type, dur) {
      const s = String(msg || '');
      for (const p of TOAST_NOISE) {
        if (p.test(s)) return;
      }
      return prev.call(this, msg, type, dur);
    };
  }

  /* ═══════════════════════════════════════════════════════════════════
     Z55G — REALISM AUDIT
     Fix misleading static/hardcoded elements.
     ═══════════════════════════════════════════════════════════════════ */

  function z55RealismAudit() {
    z55FixTargetIndicator();
    z55FixRunDot();
    z55FixInlineRec();
  }

  function z55FixTargetIndicator() {
    // "Target: Local Shell" row in exec toolbar — hardcoded green dot always on
    // Find the element with background:#3fb950 in toolbar
    const toolbar = qs('.nx-exec-toolbar');
    if (!toolbar) return;
    // Walk spans in toolbar to find the static green dot
    toolbar.querySelectorAll('span').forEach(el => {
      const style = el.getAttribute('style') || '';
      if (style.includes('3fb950') && style.includes('border-radius:50%')) {
        if (el.dataset.z55fixed) return;
        el.dataset.z55fixed = '1';
        // Default: dimmer when idle
        el.style.opacity = '0.55';
        el.style.transition = 'opacity .3s, box-shadow .3s';
        // Light up when running
        document.addEventListener('nx:exec:start', () => {
          el.style.opacity = '1';
          el.style.boxShadow = '0 0 6px #3fb950';
        });
        document.addEventListener('nx:exec:end', () => {
          el.style.opacity = '0.55';
          el.style.boxShadow = '0 0 4px #3fb950';
        });
      }
    });
  }

  function z55FixRunDot() {
    const runDot = $('nxRunDot');
    if (!runDot || runDot.dataset.z55fixed) return;
    runDot.dataset.z55fixed = '1';
    runDot.style.display = 'none';
    document.addEventListener('nx:exec:start', () => { runDot.style.display = ''; });
    document.addEventListener('nx:exec:end', () => { runDot.style.display = 'none'; });
  }

  function z55FixInlineRec() {
    // p6InlineRec: only show when there's an actual recommendation text
    const rec = $('p6InlineRec');
    const recProv = $('p6IrProv');
    if (!rec || !recProv) return;
    // Hide if empty on load
    if (!recProv.textContent.trim()) rec.style.display = 'none';
    // Watch for content
    new MutationObserver(() => {
      if (!recProv.textContent.trim()) rec.style.display = 'none';
    }).observe(recProv, { childList: true, characterData: true, subtree: true });
  }

  /* ═══════════════════════════════════════════════════════════════════
     UTILITIES
     ═══════════════════════════════════════════════════════════════════ */

  function z55Trunc(s, len) {
    s = String(s || '').trim();
    return s.length > len ? s.slice(0, len) + '…' : s;
  }

  function z55esc(s) {
    return String(s || '')
      .replace(/&/g, '&amp;').replace(/</g, '&lt;')
      .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  }

  /* ═══════════════════════════════════════════════════════════════════
     BOOT
     ═══════════════════════════════════════════════════════════════════ */

  function z55Boot() {
    z55InjectExecCard();
    z55EnrichIdleHero();
    z55WireExecEvents();
    z55UpgradeChatStyle();
    z55WatchHistoryPanel();
    z55HookHistoryRender();
    z55FilterToasts();
    z55RealismAudit();
    console.debug('[Phase Z55] Live Operational Workspace + Execution Immersion active.');
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', z55Boot);
  } else {
    setTimeout(z55Boot, 350); // after Z54 (250ms)
  }

  window._z55 = {
    showExecCard:   z55ShowExecCard,
    hideExecCard:   z55HideExecCard,
    narrate:        z55Narrate,
    loadCaps:       z55LoadCapabilities,
    groupHistory:   z55GroupHistoryByTime,
  };

})();
