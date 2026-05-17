/* ══════════════════════════════════════════════════════════════════
     NEXORA — Customer Support System
     Full ticket lifecycle, chat-style conversation, AI auto-tag
     ══════════════════════════════════════════════════════════════════ */
  (function () {
    'use strict';

    let _nxCurrentTicketId = null;

    // ── Status / Priority display helpers ───────────────────────────────────────
    const STATUS_COLORS = {
      open: { bg: '#58a6ff22', color: '#58a6ff' },
      in_progress: { bg: '#d2992222', color: '#d29922' },
      resolved: { bg: '#3fb95022', color: '#3fb950' },
      closed: { bg: '#8b949e22', color: '#8b949e' },
    };
    const PRIORITY_COLORS = {
      high: { bg: '#f8514922', color: '#f85149' },
      medium: { bg: '#d2992222', color: '#d29922' },
      low: { bg: '#3fb95022', color: '#3fb950' },
    };

    function _statusBadge(status) {
      const c = STATUS_COLORS[status] || STATUS_COLORS.open;
      return `<span style="background:${c.bg};color:${c.color};padding:2px 8px;border-radius:10px;font-size:10px;font-weight:700;text-transform:uppercase">${status.replace('_', ' ')}</span>`;
    }
    function _priorityBadge(priority) {
      const c = PRIORITY_COLORS[priority] || PRIORITY_COLORS.medium;
      return `<span style="background:${c.bg};color:${c.color};padding:2px 8px;border-radius:10px;font-size:10px;font-weight:700;text-transform:uppercase">${priority}</span>`;
    }
    function _tagBadge(tag) {
      const tags = { billing: '#bc8cff', bug: '#f85149', feature: '#3fb950', ai_error: '#d29922', general: '#58a6ff' };
      const color = tags[tag] || '#8b949e';
      return `<span style="background:${color}22;color:${color};padding:1px 6px;border-radius:8px;font-size:9px">${tag.replace('_', ' ')}</span>`;
    }
    function _relTime(iso) {
      if (!iso) return '';
      const diff = (Date.now() - new Date(iso)) / 1000;
      if (diff < 60) return 'just now';
      if (diff < 3600) return Math.floor(diff / 60) + 'm ago';
      if (diff < 86400) return Math.floor(diff / 3600) + 'h ago';
      return Math.floor(diff / 86400) + 'd ago';
    }
    function _esc(s) {
      return String(s || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
    }

    // ── Load ticket list ─────────────────────────────────────────────────────────
    async function nxSupportLoadTickets() {
      const statusFilter = (document.getElementById('nxSupportStatusFilter') || {}).value || '';
      const priorityFilter = (document.getElementById('nxSupportPriorityFilter') || {}).value || '';
      const listEl = document.getElementById('nxSupportTicketList');
      if (!listEl) return;
      listEl.innerHTML = '<div style="color:var(--muted);font-size:11px;text-align:center;padding:20px">Loading…</div>';

      let url = '/api/support/tickets?limit=50';
      if (statusFilter) url += '&status=' + encodeURIComponent(statusFilter);
      if (priorityFilter) url += '&priority=' + encodeURIComponent(priorityFilter);

      try {
        const resp = await fetch(url);
        const data = await resp.json();
        if (!data.ok || !data.tickets.length) {
          listEl.innerHTML = '<div style="color:var(--muted);font-size:11px;text-align:center;padding:20px">No tickets found.<br><button class="btn tiny" onclick="nxSupportNewTicket()" style="margin-top:8px;background:#bc8cff;color:#0d1117;border:none">Create your first ticket</button></div>';
          // Update badge
          const badge = document.getElementById('supportBadge');
          if (badge) { badge.style.display = 'none'; }
          return;
        }
        // Update badge
        const openCount = data.tickets.filter(t => t.status === 'open').length;
        const badge = document.getElementById('supportBadge');
        if (badge) {
          badge.textContent = openCount;
          badge.style.display = openCount ? '' : 'none';
        }
        listEl.innerHTML = data.tickets.map(t => `
            <div onclick="nxSupportOpenTicket('${_esc(t.id)}')"
                 style="border-radius:6px;padding:9px 10px;margin-bottom:4px;cursor:pointer;border:1px solid ${_nxCurrentTicketId === t.id ? '#bc8cff' : 'var(--border)'};background:${_nxCurrentTicketId === t.id ? '#bc8cff11' : 'var(--bg2)'};transition:all .15s"
                 id="nxTicketItem-${_esc(t.id)}">
              <div style="display:flex;align-items:center;gap:6px;margin-bottom:4px">
                ${_statusBadge(t.status)}
                ${_tagBadge(t.tag || 'general')}
                <span style="font-size:9px;color:var(--muted);margin-left:auto">${_relTime(t.updated_at)}</span>
              </div>
              <div style="font-size:12px;font-weight:600;color:var(--text);white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${_esc(t.subject)}</div>
              <div style="font-size:10px;color:var(--muted);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;margin-top:2px">${_esc((t.message || '').slice(0, 60))}…</div>
            </div>
        `).join('');
      } catch (e) {
        listEl.innerHTML = '<div style="color:#f85149;font-size:11px;text-align:center;padding:20px">Failed to load tickets</div>';
      }
    }
    window.nxSupportLoadTickets = nxSupportLoadTickets;

    // ── Open ticket ──────────────────────────────────────────────────────────────
    async function nxSupportOpenTicket(ticketId) {
      _nxCurrentTicketId = ticketId;
      // Hide form, show detail
      const formEl = document.getElementById('nxSupportNewForm');
      const placeholderEl = document.getElementById('nxSupportPlaceholder');
      const headerEl = document.getElementById('nxTicketHeader');
      const msgEl = document.getElementById('nxTicketMessages');
      const replyEl = document.getElementById('nxTicketReplyArea');
      if (formEl) formEl.style.display = 'none';
      if (placeholderEl) placeholderEl.style.display = 'none';
      if (headerEl) headerEl.style.display = '';
      if (msgEl) msgEl.innerHTML = '<div style="color:var(--muted);font-size:11px;text-align:center;padding:20px">Loading…</div>';
      if (replyEl) replyEl.style.display = '';

      // Update ticket list highlight
      document.querySelectorAll('[id^="nxTicketItem-"]').forEach(el => {
        el.style.border = '1px solid var(--border)';
        el.style.background = 'var(--bg2)';
      });
      const activeItem = document.getElementById('nxTicketItem-' + ticketId);
      if (activeItem) {
        activeItem.style.border = '1px solid #bc8cff';
        activeItem.style.background = '#bc8cff11';
      }

      try {
        const resp = await fetch('/api/support/ticket/' + encodeURIComponent(ticketId));
        const data = await resp.json();
        if (!data.ok) {
          if (msgEl) msgEl.innerHTML = '<div style="color:#f85149;font-size:11px;padding:20px">Failed to load ticket</div>';
          return;
        }
        const t = data.ticket;
        // Header
        const titleEl = document.getElementById('nxTicketTitle');
        const metaEl = document.getElementById('nxTicketMeta');
        const statusBadgeEl = document.getElementById('nxTicketStatusBadge');
        const priorityBadgeEl = document.getElementById('nxTicketPriorityBadge');
        if (titleEl) titleEl.textContent = t.subject;
        if (metaEl) metaEl.innerHTML = `#${t.id.slice(0, 8).toUpperCase()} · ${_tagBadge(t.tag || 'general')} · Created ${_relTime(t.created_at)}`;
        if (statusBadgeEl) {
          const c = STATUS_COLORS[t.status] || STATUS_COLORS.open;
          statusBadgeEl.style.background = c.bg;
          statusBadgeEl.style.color = c.color;
          statusBadgeEl.textContent = t.status.replace('_', ' ').toUpperCase();
        }
        if (priorityBadgeEl) {
          const c = PRIORITY_COLORS[t.priority] || PRIORITY_COLORS.medium;
          priorityBadgeEl.style.background = c.bg;
          priorityBadgeEl.style.color = c.color;
          priorityBadgeEl.textContent = t.priority.toUpperCase();
        }

        // Messages (chat-style)
        if (msgEl) {
          msgEl.innerHTML = (t.messages || []).map(m => {
            const isAdmin = m.sender === 'admin';
            return `<div style="display:flex;flex-direction:column;align-items:${isAdmin ? 'flex-start' : 'flex-end'}">
                  <div style="max-width:75%;background:${isAdmin ? 'var(--bg2)' : '#bc8cff22'};border:1px solid ${isAdmin ? 'var(--border)' : '#bc8cff44'};border-radius:10px;padding:8px 12px">
                    <div style="font-size:10px;color:${isAdmin ? '#58a6ff' : '#bc8cff'};font-weight:700;margin-bottom:4px">${isAdmin ? 'Support Team' : 'You'}</div>
                    <div style="font-size:13px;color:var(--text);white-space:pre-wrap">${_esc(m.message)}</div>
                    <div style="font-size:9px;color:var(--muted);margin-top:4px;text-align:right">${_relTime(m.created_at)}</div>
                  </div>
                </div>`;
          }).join('') || '<div style="color:var(--muted);font-size:11px;text-align:center;padding:20px">No messages yet</div>';
          msgEl.scrollTop = msgEl.scrollHeight;
        }
      } catch (e) {
        if (msgEl) msgEl.innerHTML = '<div style="color:#f85149;font-size:11px;padding:20px">Error loading ticket</div>';
      }
    }
    window.nxSupportOpenTicket = nxSupportOpenTicket;

    // ── New ticket form ──────────────────────────────────────────────────────────
    function nxSupportNewTicket() {
      _nxCurrentTicketId = null;
      const formEl = document.getElementById('nxSupportNewForm');
      const placeholderEl = document.getElementById('nxSupportPlaceholder');
      const headerEl = document.getElementById('nxTicketHeader');
      const msgEl = document.getElementById('nxTicketMessages');
      const replyEl = document.getElementById('nxTicketReplyArea');
      if (formEl) { formEl.style.display = 'flex'; }
      if (placeholderEl) placeholderEl.style.display = 'none';
      if (headerEl) headerEl.style.display = 'none';
      if (msgEl) msgEl.innerHTML = '';
      if (replyEl) replyEl.style.display = 'none';
      // Clear form
      ['nxTicketSubject', 'nxTicketMessage', 'nxTicketEmail'].forEach(id => {
        const el = document.getElementById(id);
        if (el) el.value = '';
      });
      const msgFeedback = document.getElementById('nxTicketFormMsg');
      if (msgFeedback) msgFeedback.style.display = 'none';
    }
    window.nxSupportNewTicket = nxSupportNewTicket;

    function nxSupportCancelNew() {
      const formEl = document.getElementById('nxSupportNewForm');
      const placeholderEl = document.getElementById('nxSupportPlaceholder');
      if (formEl) formEl.style.display = 'none';
      if (placeholderEl) placeholderEl.style.display = '';
    }
    window.nxSupportCancelNew = nxSupportCancelNew;

    // ── Submit ticket ────────────────────────────────────────────────────────────
    async function nxSupportSubmitTicket() {
      const subject = (document.getElementById('nxTicketSubject') || {}).value || '';
      const message = (document.getElementById('nxTicketMessage') || {}).value || '';
      const priority = (document.getElementById('nxTicketPriority') || {}).value || 'medium';
      const email = (document.getElementById('nxTicketEmail') || {}).value || '';

      if (!subject.trim() || !message.trim()) {
        _nxSupportMsg('Subject and message are required', '#f85149');
        return;
      }

      try {
        const resp = await fetch('/api/support/ticket', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ subject, message, priority, user_email: email }),
        });
        const data = await resp.json();
        if (!data.ok) {
          _nxSupportMsg(data.error || 'Failed to create ticket', '#f85149');
          return;
        }
        _nxSupportMsg('✓ Ticket created! We\'ll respond within 24 hours.', '#3fb950');
        await nxSupportLoadTickets();
        setTimeout(() => {
          if (data.ticket && data.ticket.id) nxSupportOpenTicket(data.ticket.id);
        }, 600);
      } catch (e) {
        _nxSupportMsg('Network error. Please try again.', '#f85149');
      }
    }
    window.nxSupportSubmitTicket = nxSupportSubmitTicket;

    function _nxSupportMsg(text, color) {
      const el = document.getElementById('nxTicketFormMsg');
      if (!el) return;
      el.textContent = text;
      el.style.color = color || '#3fb950';
      el.style.display = '';
    }

    // ── Send reply ───────────────────────────────────────────────────────────────
    async function nxSupportSendReply() {
      if (!_nxCurrentTicketId) return;
      const inputEl = document.getElementById('nxTicketReplyInput');
      const message = (inputEl || {}).value || '';
      if (!message.trim()) return;

      try {
        const resp = await fetch('/api/support/ticket/' + encodeURIComponent(_nxCurrentTicketId) + '/reply', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ message, sender: 'user' }),
        });
        const data = await resp.json();
        if (data.ok) {
          if (inputEl) inputEl.value = '';
          await nxSupportOpenTicket(_nxCurrentTicketId);
        }
      } catch (e) { }
    }
    window.nxSupportSendReply = nxSupportSendReply;

    // ── Update status ────────────────────────────────────────────────────────────
    async function nxTicketSetStatus(status) {
      if (!_nxCurrentTicketId) return;
      try {
        const resp = await fetch('/api/support/ticket/' + encodeURIComponent(_nxCurrentTicketId) + '/status', {
          method: 'PATCH',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ status }),
        });
        const data = await resp.json();
        if (data.ok) {
          await Promise.all([
            nxSupportLoadTickets(),
            nxSupportOpenTicket(_nxCurrentTicketId),
          ]);
        }
      } catch (e) { }
    }
    window.nxTicketSetStatus = nxTicketSetStatus;

    // ── Auto-load when support tab is active ─────────────────────────────────────
    window.NX_LOAD_TASKS.push( function () {
      console.debug('[Support] ready');
      // Load tickets in background for badge count
      setTimeout(nxSupportLoadTickets, 2000);
    });

  })();
