(function () {
    'use strict';

    /* ═══════════════════════════════════════════════════════════════
       PHASE 13 — Context Compression UI
       ═══════════════════════════════════════════════════════════════ */

    let _p13SummaryVisible = false;
    let _p13CurrentSummary = null;

    window.p13CheckSummary = async function (sid) {
      if (!sid) return;
      try {
        const r = await fetch(`/api/chat/${sid}/summary`);
        const d = await r.json();
        const bar = document.getElementById('p13ContextBar');
        const badge = document.getElementById('p13Badge');
        const toggle = document.getElementById('p13SummaryToggle');
        const box = document.getElementById('p13SummaryBox');
        if (!bar) return;

        if (d.ok && d.has_summary) {
          _p13CurrentSummary = d.summary;
          bar.style.display = 'flex';
          bar.style.flexDirection = 'column';
          badge.style.display = 'inline-block';
          toggle.style.display = 'inline';
          if (box && _p13SummaryVisible) box.textContent = d.summary;
        } else if (d.ok && d.message_count >= 8) {
          // Getting close — show compressing indicator
          bar.style.display = 'flex';
          badge.style.display = 'none';
          toggle.style.display = 'none';
        } else {
          bar.style.display = 'none';
        }
      } catch (e) {
        // Silent — non-critical
      }
    };

    window.p13ToggleSummary = function () {
      _p13SummaryVisible = !_p13SummaryVisible;
      const box = document.getElementById('p13SummaryBox');
      const toggle = document.getElementById('p13SummaryToggle');
      if (!box) return;
      if (_p13SummaryVisible) {
        box.textContent = _p13CurrentSummary || 'No summary yet.';
        box.style.display = 'block';
        if (toggle) toggle.textContent = 'Hide summary';
      } else {
        box.style.display = 'none';
        if (toggle) toggle.textContent = 'View summary';
      }
    };

    // Hook into p12LoadChat to also check summary state
    const _p13_origLoadChat = window.p12LoadChat;
    window.p12LoadChat = async function (sid) {
      if (typeof _p13_origLoadChat === 'function') await _p13_origLoadChat(sid);
      p13CheckSummary(sid);
    };

    console.log('[Phase 13] Context Compression active — 3-tier memory enabled.');


    /* ═══════════════════════════════════════════════════════════════
       PHASE 14 — Self-Improving AI UI
       ═══════════════════════════════════════════════════════════════ */

    let _p14InsightsOpen = false;

    window.p14ToggleInsights = function () {
      _p14InsightsOpen = !_p14InsightsOpen;
      const body = document.getElementById('p14InsightsBody');
      const icon = document.getElementById('p14InsightsToggleIcon');
      if (!body) return;
      body.style.display = _p14InsightsOpen ? 'block' : 'none';
      if (icon) icon.textContent = _p14InsightsOpen ? '▾' : '▸';
      if (_p14InsightsOpen) p14LoadInsights();
    };

    window.p14LoadInsights = async function () {
      const el = document.getElementById('p14InsightsContent');
      if (!el) return;
      try {
        const r = await fetch('/api/learning/insights');
        const d = await r.json();
        if (!d.ok) { el.innerHTML = '<div style="color:#f87171">Failed to load insights.</div>'; return; }

        const reflections = d.reflections || [];
        const strategies = d.strategies || [];

        let html = '';

        // Strategy win rates
        if (strategies.length) {
          html += '<div style="font-weight:600;margin-bottom:4px;color:var(--text)">Strategy Performance</div>';
          strategies.forEach(s => {
            const pct = s.win_rate || 0;
            const color = pct >= 70 ? '#4ade80' : pct >= 40 ? '#fbbf24' : '#f87171';
            html += `<div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:3px">
                    <span style="opacity:.8">${s.strategy}</span>
                    <span style="color:${color};font-weight:600">${pct}% (${s.attempts} runs)</span>
                </div>`;
          });
        }

        // Recent reflections
        if (reflections.length) {
          html += '<div style="font-weight:600;margin:8px 0 4px;color:var(--text)">Recent Learnings</div>';
          reflections.slice(0, 3).forEach(ref => {
            const icon = ref.success ? '✅' : '⚠️';
            const taskShort = (ref.task || 'Task').substring(0, 50);
            const meta = (ref.meta || '').substring(0, 120);
            html += `<div style="margin-bottom:6px;padding:5px 7px;background:var(--surface);border-radius:5px;border-left:2px solid ${ref.success ? '#4ade80' : '#f87171'}">
                    <div style="font-weight:500;margin-bottom:2px">${icon} ${taskShort}${taskShort.length === 50 ? '…' : ''}</div>
                    ${meta ? `<div style="opacity:.7;font-size:9px;line-height:1.4">${meta}</div>` : ''}
                </div>`;
          });
          if (d.total_reflections > 3) {
            html += `<div style="opacity:.5;font-size:9px;margin-top:4px">+${d.total_reflections - 3} more reflections stored</div>`;
          }
        }

        if (!html) {
          html = '<div style="opacity:.5">No learnings yet. Run tasks to build intelligence.</div>';
        }

        // Status badges
        html += `<div style="margin-top:8px;display:flex;gap:5px;flex-wrap:wrap">
            <span style="background:${d.learning_enabled ? '#4ade8022' : '#f8717122'};color:${d.learning_enabled ? '#4ade80' : '#f87171'};border-radius:10px;padding:1px 7px;font-size:9px">
                Learning: ${d.learning_enabled ? 'ON' : 'OFF'}
            </span>
            <span style="background:${d.auto_optimize ? '#60a5fa22' : '#6b728022'};color:${d.auto_optimize ? '#60a5fa' : '#9ca3af'};border-radius:10px;padding:1px 7px;font-size:9px">
                Auto-optimize: ${d.auto_optimize ? 'ON' : 'OFF'}
            </span>
        </div>`;

        el.innerHTML = html;
      } catch (e) {
        if (el) el.innerHTML = `<div style="color:#f87171">Error: ${e.message}</div>`;
      }
    };

    window.p14ResetLearning = async function () {
      if (!confirm('Reset all learning memory? This will clear all reflections, strategy stats, and prompt optimizations.')) return;
      try {
        const r = await fetch('/api/learning/reset', { method: 'POST' });
        const d = await r.json();
        if (typeof toast === 'function') toast(d.message || 'Learning memory reset.', d.ok ? 'ok' : 'err');
        if (d.ok) p14LoadInsights();
      } catch (e) {
        if (typeof toast === 'function') toast('Reset failed.', 'err');
      }
    };

    window.p14SaveSettings = async function () {
      const learnEl = document.getElementById('p14LearningToggle');
      const autoEl = document.getElementById('p14AutoOptToggle');
      if (!learnEl && !autoEl) return;
      try {
        await fetch('/api/learning/settings', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            learning_enabled: learnEl ? learnEl.checked : true,
            auto_optimize: autoEl ? autoEl.checked : true,
          })
        });
        if (typeof toast === 'function') toast('Learning settings saved.', 'ok');
      } catch (e) { }
    };

    // Load settings state when Intelligence tab opens
    function p14LoadSettingsState() {
      fetch('/api/learning/insights').then(r => r.json()).then(d => {
        const le = document.getElementById('p14LearningToggle');
        const ao = document.getElementById('p14AutoOptToggle');
        if (le) le.checked = d.learning_enabled !== false;
        if (ao) ao.checked = d.auto_optimize !== false;
      }).catch(() => { });
    }

    // Hook into settings tab switch to load state
    const _p14_origSwitchSettingsTab = window.switchSettingsTab;
    window.switchSettingsTab = function (tab) {
      if (typeof _p14_origSwitchSettingsTab === 'function') _p14_origSwitchSettingsTab(tab);
      if (tab === 'intelligence') {
        p14LoadSettingsState();
      }
    };

    // Auto-refresh insights every 60s when Inspector is visible
    setInterval(() => {
      if (_p14InsightsOpen) p14LoadInsights();
    }, 60000);

    console.log('[Phase 14] Self-Improving AI active — learning engine connected.');

  })();
