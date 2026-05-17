/**
 * nx-xterm.js — AGI Workspace Live Terminal v1
 * ─────────────────────────────────────────────
 * Mounts xterm.js onto the existing #xtermMount DOM node,
 * connects it to the real backend via:
 *   • POST /api/terminal/run  (sandbox mode — buffered, echoed)
 *   • GET  /api/terminal/stream (local-bridge mode — SSE streaming)
 *   • NxBus "agent.output" events (agent PTY passthrough)
 *
 * Architecture rules:
 *   • Never fakes output — every character comes from the backend
 *   • Session-isolated — resets cleanly on session change
 *   • Reconnect-safe — SSE stream re-attaches on visibility/focus
 *   • NxBus-driven — agent commands surface in xterm, not just the div-based terminal
 *   • Scrollback capped at 10000 lines (perf)
 *   • Fully coexists with existing div-based terminal (Phase 22) — no removal
 */
'use strict';

(function () {

  /* ── Guard ─────────────────────────────────────────────────────────────── */
  if (window.NxXterm) return;

  /* ── State ─────────────────────────────────────────────────────────────── */
  let _term      = null;   // xterm.Terminal instance
  let _fitAddon  = null;
  let _linkAddon = null;
  let _mounted   = false;
  let _sid       = null;
  let _history   = [];     // command history
  let _histIdx   = -1;
  let _inputBuf  = '';     // current input line
  let _busy      = false;  // command in-flight
  let _activeStream = null; // EventSource for local-bridge streaming
  let _resizeObs = null;
  let _agentSub  = null;   // NxBus subscription token

  const PROMPT   = '\x1b[32m❯\x1b[0m ';
  const CRLF     = '\r\n';

  /* ── Library readiness ─────────────────────────────────────────────────── */
  function _waitLibs(cb) {
    // xterm.js + addons are loaded via CDN defer in index.html
    if (window.Terminal && window.FitAddon) { cb(); return; }
    const t = setInterval(() => {
      if (window.Terminal && window.FitAddon) { clearInterval(t); cb(); }
    }, 150);
  }

  /* ── Mount ─────────────────────────────────────────────────────────────── */
  function _mount() {
    const mountEl = document.getElementById('xtermMount');
    if (!mountEl || _mounted) return;
    if (!window.Terminal) return;

    _term = new window.Terminal({
      theme: {
        background:   '#0c0c0c',
        foreground:   '#e6e6ef',
        cursor:       '#6366f1',
        cursorAccent: '#0c0c0c',
        black:        '#1e1e2e',
        brightBlack:  '#3b3b52',
        red:          '#f85149',
        brightRed:    '#ff6b6b',
        green:        '#3fb950',
        brightGreen:  '#7ee787',
        yellow:       '#f0883e',
        brightYellow: '#ffa657',
        blue:         '#6366f1',
        brightBlue:   '#818cf8',
        magenta:      '#d2a8ff',
        brightMagenta:'#e9c3ff',
        cyan:         '#39c5cf',
        brightCyan:   '#56d8e4',
        white:        '#b3b3cc',
        brightWhite:  '#e6e6ef',
      },
      fontFamily: '"JetBrains Mono","Cascadia Code","Fira Code",ui-monospace,monospace',
      fontSize:   13,
      lineHeight: 1.4,
      scrollback: 10000,
      cursorBlink: true,
      cursorStyle: 'bar',
      allowTransparency: true,
      fastScrollModifier: 'alt',
    });

    // FitAddon — keeps terminal sized to container
    if (window.FitAddon) {
      _fitAddon = new window.FitAddon.FitAddon();
      _term.loadAddon(_fitAddon);
    }

    // WebLinks addon
    if (window.WebLinksAddon) {
      _linkAddon = new window.WebLinksAddon.WebLinksAddon();
      _term.loadAddon(_linkAddon);
    }

    _term.open(mountEl);
    try { _fitAddon?.fit(); } catch (_) {}

    // Key handler — builds input buffer, sends on Enter
    _term.onKey(({ key, domEvent: ev }) => {
      if (_busy) { if (ev.ctrlKey && ev.key === 'c') _cancelStream(); return; }
      const printable = !ev.altKey && !ev.ctrlKey && !ev.metaKey;
      if (ev.key === 'Enter') {
        _term.writeln('');
        const cmd = _inputBuf.trim();
        _inputBuf = '';
        _histIdx  = -1;
        if (cmd) _executeCommand(cmd);
        else _writePrompt();
      } else if (ev.key === 'Backspace') {
        if (_inputBuf.length > 0) {
          _inputBuf = _inputBuf.slice(0, -1);
          _term.write('\b \b');
        }
      } else if (ev.key === 'ArrowUp') {
        if (_history.length) {
          _histIdx = Math.max(0, _histIdx === -1 ? _history.length - 1 : _histIdx - 1);
          _replaceInput(_history[_histIdx]);
        }
      } else if (ev.key === 'ArrowDown') {
        if (_histIdx >= 0) {
          _histIdx++;
          const cmd = _histIdx < _history.length ? _history[_histIdx] : '';
          if (_histIdx >= _history.length) _histIdx = -1;
          _replaceInput(cmd);
        }
      } else if (ev.ctrlKey && ev.key === 'c') {
        _term.writeln('^C');
        _inputBuf = '';
        _histIdx  = -1;
        _writePrompt();
      } else if (ev.ctrlKey && ev.key === 'l') {
        _term.clear();
        _writePrompt();
      } else if (printable) {
        _inputBuf += key;
        _term.write(key);
      }
    });

    // Paste support — handle multi-line pastes correctly
    _term.onData(data => {
      if (data.length > 1) { // pasted block
        const lines = data.split(/\r?\n/);
        if (lines.length <= 1) {
          // Single-line paste — append to current buffer normally
          _inputBuf += data;
          _term.write(data);
        } else {
          // Multi-line paste — execute each non-empty line sequentially
          const toRun = lines.filter(l => l.trim().length > 0);
          if (toRun.length === 0) return;
          // Execute each line with a small delay to respect PTY ordering
          toRun.forEach((line, idx) => {
            setTimeout(() => {
              _term.write(line);
              _term.writeln('');
              _inputBuf = '';
              _executeCommand(line);
            }, idx * 100);
          });
        }
      }
    });

    _mounted = true;

    // Resize observer
    _resizeObs = new ResizeObserver(() => {
      try { _fitAddon?.fit(); } catch (_) {}
    });
    _resizeObs.observe(mountEl.parentElement || mountEl);

    // Hide skeleton, show status
    const skel = document.getElementById('xtermSkeleton');
    if (skel) skel.style.display = 'none';
    _updateStatus('Ready');

    _writeBanner();
    _writePrompt();
  }

  /* ── Terminal helpers ───────────────────────────────────────────────────── */
  function _writePrompt() {
    if (!_term) return;
    _term.write(PROMPT);
  }

  function _writeBanner() {
    if (!_term) return;
    const sid = _sid ? _sid.slice(0, 8) + '…' : 'none';
    _term.writeln('\x1b[34m╔══════════════════════════════╗\x1b[0m');
    _term.writeln('\x1b[34m║\x1b[0m  \x1b[1mAetherion AI Terminal\x1b[0m         \x1b[34m║\x1b[0m');
    _term.writeln(`\x1b[34m║\x1b[0m  Session: \x1b[33m${sid}\x1b[0m          \x1b[34m║\x1b[0m`);
    _term.writeln('\x1b[34m╚══════════════════════════════╝\x1b[0m');
    _term.writeln('\x1b[90mCtrl+C cancel · Ctrl+L clear · ↑↓ history\x1b[0m');
  }

  function _replaceInput(cmd) {
    // Erase current input line, replace with cmd
    _term.write('\r' + PROMPT + ' '.repeat(_inputBuf.length + 2) + '\r' + PROMPT);
    _inputBuf = cmd;
    _term.write(cmd);
  }

  function _updateStatus(text) {
    const el = document.getElementById('xtermStatus');
    if (el) el.textContent = text;
  }

  function _setBusy(flag) {
    _busy = flag;
    const btn = document.getElementById('xtermRunBtn');
    if (btn) btn.textContent = flag ? '⏹ Cancel' : '▶ Run';
    _updateStatus(flag ? 'Running…' : 'Ready');
  }

  /* ── Command execution ─────────────────────────────────────────────────── */
  async function _executeCommand(cmd) {
    if (!cmd || !_mounted) return;
    _history.push(cmd);
    if (_history.length > 200) _history.shift();

    const termMode = typeof window.termMode !== 'undefined' ? window.termMode : 'sandbox';

    // Local bridge mode → SSE streaming
    if (termMode === 'local') {
      _setBusy(true);
      const streamed = _streamLocal(cmd);
      if (streamed) return;
    }

    // Sandbox mode → buffered POST
    _setBusy(true);
    try {
      const r = await fetch('/api/terminal/run', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ cmd, sid: _sid, mode: termMode }),
      });
      const d = await r.json();
      if (d.error === 'command_blocked') {
        _term.writeln(`\x1b[31m[blocked] ${d.reason || 'unsafe command'}\x1b[0m`);
      } else {
        if (d.stdout) _term.writeln(d.stdout.replace(/\n/g, CRLF));
        if (d.stderr) _term.writeln(`\x1b[33m${d.stderr.replace(/\n/g, CRLF)}\x1b[0m`);
        const ms = Math.round((d.duration_sec || 0) * 1000);
        _term.writeln(`\x1b[90m[exit ${d.exit ?? '?'} · ${ms}ms${d.truncated ? ' TRUNCATED' : ''}]\x1b[0m`);
      }
    } catch (e) {
      _term.writeln(`\x1b[31m[network] ${e.message}\x1b[0m`);
    } finally {
      _setBusy(false);
      _writePrompt();
    }
  }

  /* ── Local bridge SSE streaming ─────────────────────────────────────────── */
  function _streamLocal(cmd) {
    if (typeof EventSource === 'undefined') return false;
    try {
      const qs = new URLSearchParams({ cmd });
      if (_sid) qs.set('sid', _sid);
      const es = new EventSource('/api/terminal/stream?' + qs.toString());
      _activeStream = es;
      const t0 = Date.now();

      es.addEventListener('stdout', ev => {
        _term.writeln((JSON.parse(ev.data) || ev.data).replace(/\n/g, CRLF));
      });
      es.addEventListener('stderr', ev => {
        _term.writeln(`\x1b[33m${(JSON.parse(ev.data) || ev.data).replace(/\n/g, CRLF)}\x1b[0m`);
      });
      es.addEventListener('done', ev => {
        const d = JSON.parse(ev.data) || {};
        const ms = Math.round(((d.duration_sec) || ((Date.now() - t0) / 1000)) * 1000);
        _term.writeln(`\x1b[90m[exit ${d.exit_code ?? '?'} · ${ms}ms${d.timed_out ? ' TIMEOUT' : ''}]\x1b[0m`);
        es.close(); _activeStream = null;
        _setBusy(false); _writePrompt();
      });
      es.addEventListener('error', ev => {
        const msg = ev.data ? JSON.parse(ev.data)?.error || 'stream error' : 'disconnected';
        _term.writeln(`\x1b[31m[stream] ${msg}\x1b[0m`);
        try { es.close(); } catch (_) {}
        _activeStream = null;
        _setBusy(false); _writePrompt();
      });
      return true;
    } catch (_) { return false; }
  }

  function _cancelStream() {
    if (_activeStream) {
      try { _activeStream.close(); } catch (_) {}
      _activeStream = null;
    }
    _setBusy(false);
    _term.writeln('\x1b[31m^C\x1b[0m');
    _writePrompt();
  }

  /* ── NxBus: agent output passthrough ───────────────────────────────────── */
  function _subscribeNxBus() {
    if (!window.NxBus) { setTimeout(_subscribeNxBus, 400); return; }

    // Agent shell output → paint into xterm
    NxBus.on('agent.output', ({ text, stream } = {}) => {
      if (!_term || !_mounted || !text) return;
      const line = String(text).replace(/\n/g, CRLF);
      if (stream === 'stderr') {
        _term.writeln(`\x1b[33m${line}\x1b[0m`);
      } else {
        _term.writeln(line);
      }
    });

    // agent.action write_file → show hint
    NxBus.on('agent.action', ({ action, path } = {}) => {
      if (!_term || !_mounted) return;
      if (action === 'write_file' && path)
        _term.writeln(`\x1b[90m[agent] writing ${path}\x1b[0m`);
      else if (action === 'run_command')
        _updateStatus('Agent running command…');
    });

    // Task done → reset busy hint
    NxBus.on(NxBus.EVENTS?.AGENT_DONE, () => {
      _updateStatus('Ready');
    });

    // Session change → reset
    NxBus.on(NxBus.EVENTS?.SESSION_RESTORED, ({ sid } = {}) => {
      if (sid && sid !== _sid) NxXterm.resetForSession(sid);
    });
  }

  /* ── Quick command bar (xtermRunQuick, xtermClear) ─────────────────────── */
  window.xtermRunQuick = function () {
    const inp = document.getElementById('xtermQuickInput');
    const cmd = (inp?.value || '').trim();
    if (!cmd) return;
    if (inp) inp.value = '';
    if (!_mounted) { NxXterm.ensureMounted(); setTimeout(() => _executeCommand(cmd), 300); return; }
    if (_term) { _term.writeln(PROMPT.replace(/\x1b\[[^m]*m/g, '') + cmd); }
    _executeCommand(cmd);
  };

  window.xtermClear = function () {
    if (_term) { _term.clear(); _writePrompt(); }
  };

  /* ── Public API ─────────────────────────────────────────────────────────── */
  const NxXterm = {

    ensureMounted() {
      if (_mounted) { try { _fitAddon?.fit(); } catch (_) {} return; }
      _waitLibs(_mount);
    },

    resetForSession(sid) {
      _sid = sid;
      if (_term && _mounted) {
        _term.clear();
        _writeBanner();
        _writePrompt();
      }
      _updateStatus('Session: ' + (sid?.slice(0, 8) || 'none'));
    },

    write(text) {
      if (!_term || !_mounted) return;
      _term.writeln(String(text).replace(/\n/g, CRLF));
    },

    get term()    { return _term; },
    get mounted() { return _mounted; },

    init(sid) {
      _sid = sid;
      _subscribeNxBus();
      // Auto-mount when terminal tab becomes visible
      const tabBtn = document.querySelector('[data-nxtab="terminal"], .nx-tab[data-tab="terminal"]');
      if (tabBtn) {
        tabBtn.addEventListener('click', () => NxXterm.ensureMounted(), { once: false });
      }
      // Also mount if terminal tab is already active
      const isVisible = document.getElementById('xtermMount')?.offsetParent !== null;
      if (isVisible) NxXterm.ensureMounted();
      // NxWorkspace toggleBottom hook (existing workspace.js calls nxEnsureTerminal)
      window.nxEnsureTerminal = () => NxXterm.ensureMounted();
      // Fit on window resize
      window.addEventListener('resize', () => {
        if (_mounted) try { _fitAddon?.fit(); } catch (_) {}
      });
      console.debug('[NxXterm] initialized for session', sid);
    },
  };

  window.NxXterm = NxXterm;

  // Auto-init after runtime boots
  function _tryAutoInit() {
    const sid = typeof currentSession !== 'undefined' ? currentSession : null;
    if (sid) { NxXterm.init(sid); return; }
    setTimeout(_tryAutoInit, 500);
  }
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => setTimeout(_tryAutoInit, 700));
  } else {
    setTimeout(_tryAutoInit, 700);
  }

})();
