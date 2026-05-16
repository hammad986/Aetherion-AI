/**
 * nx-signals.js — Aetherion Canonical Cognition Signal Schema v1
 * ══════════════════════════════════════════════════════════════════
 * Single source of truth for all runtime intelligence event types.
 *
 * GOVERNANCE:
 *   - ALL frontend AGI-native surfaces consume from this schema.
 *   - ALL backend emit_fn calls MUST conform to these payload shapes.
 *   - Never add signals here without a corresponding backend emitter.
 *
 * Signal lifecycle: Backend → SSEManager → NxSSERuntime → NxBus → surfaces
 */
'use strict';

(function () {

  /**
   * SIGNAL TAXONOMY
   * ═══════════════
   * Each entry defines:
   *   bus_event   - NxBus event name (what surfaces listen on)
   *   sse_event   - Raw SSE event type from backend
   *   schema      - Payload field definitions
   *   severity    - info | warn | error | critical
   *   category    - reasoning | orchestration | trust | memory | hitl | resource
   */
  const SIGNALS = Object.freeze({

    /* ── REASONING ─────────────────────────────────────────────── */

    THOUGHT: {
      bus_event: 'agent.thought',
      sse_event: 'agent.think',
      severity:  'info',
      category:  'reasoning',
      schema: {
        thought:     'string  — the agent\'s raw reasoning text',
        step_index:  'number  — which plan step this thought belongs to',
        step_text:   'string  — human-readable step label',
        session_id:  'string  — session correlation ID',
        _seq:        'number  — monotonic event sequence number',
        _sid:        'string  — session ID for dedup',
      },
    },

    DECISION: {
      bus_event: 'agent.decision',
      sse_event: 'agent.action',
      severity:  'info',
      category:  'reasoning',
      schema: {
        tool:        'string  — tool name selected',
        args:        'object  — tool arguments (truncated to 120 chars per value)',
        step_index:  'number',
        step_text:   'string',
        session_id:  'string',
        _seq:        'number',
      },
    },

    PLAN: {
      bus_event:  'agent.plan',
      sse_event:  'agent.task_start',
      severity:   'info',
      category:   'orchestration',
      schema: {
        task:       'string  — user task text (max 200 chars)',
        session_id: 'string',
        _seq:       'number',
      },
    },

    /* ── TOOL EXECUTION ─────────────────────────────────────────── */

    TOOL_SUCCESS: {
      bus_event:  'agent.tool_result',
      sse_event:  'agent.tool_success',
      severity:   'info',
      category:   'orchestration',
      schema: {
        tool:        'string',
        step_index:  'number',
        output:      'string  — first 300 chars of tool output',
        path:        'string? — file path for write/read tools',
        session_id:  'string',
        _seq:        'number',
      },
    },

    FILE_MODIFIED: {
      bus_event:  'file.modified',
      sse_event:  'file.modified',
      severity:   'info',
      category:   'orchestration',
      schema: {
        tool:        'string',
        step_index:  'number',
        path:        'string  — file path',
        output:      'string',
        session_id:  'string',
        _seq:        'number',
      },
    },

    /* ── SEMANTIC VALIDATION ────────────────────────────────────── */

    TRUST_SIGNAL: {
      bus_event:  'nx:trust:signal',
      sse_event:  'agent.trust_signal',
      severity:   'info',
      category:   'trust',
      schema: {
        type:        'string  — action_success | contradiction | verification | assumption',
        verified:    'boolean — has this been semantically verified?',
        confidence:  'number  — 0.0–1.0 confidence score',
        message:     'string  — human-readable signal description',
        step:        'number  — plan step index',
        action:      'string  — tool or agent action that generated this signal',
        session_id:  'string',
        _seq:        'number',
      },
    },

    /* ── EXECUTION DAG ──────────────────────────────────────────── */

    DAG_UPDATE: {
      bus_event:  'nx:dag:update',
      sse_event:  'agent.dag_update',
      severity:   'info',
      category:   'orchestration',
      schema: {
        nodes:      'object[]  — {id, label, state, stage, is_critical_path, retries, duration_ms, semantic_confidence}',
        edges:      'object[]  — {from_id, to_id}',
        active:     'number    — currently active node index',
        progress:   'number    — 0.0–1.0 completion ratio',
        session_id: 'string',
        _seq:       'number',
      },
    },

    MILESTONE_UPDATE: {
      bus_event:  'nx:milestone:update',
      sse_event:  'agent.milestone_update',
      severity:   'info',
      category:   'orchestration',
      schema: {
        completed:  'string[]  — list of achieved milestone names',
        progress:   'number    — 0.0–1.0',
        session_id: 'string',
        _seq:       'number',
      },
    },

    /* ── MEMORY ─────────────────────────────────────────────────── */

    MEMORY_RETRIEVED: {
      bus_event:  'memory.retrieved',
      sse_event:  'agent.memory_retrieved',
      severity:   'info',
      category:   'memory',
      schema: {
        type:        'string  — episodic | semantic | caution | pattern',
        content:     'string  — retrieved memory text',
        trust:       'string  — high | medium | low',
        confidence:  'number  — retrieval relevance score 0.0–1.0',
        why:         'string  — reason this memory was selected',
        staleness_h: 'number  — age in hours',
        source:      'string  — memory store key / path',
        session_id:  'string',
        _seq:        'number',
      },
    },

    ASSUMPTION_DETECTED: {
      bus_event:  'trust.assumption',
      sse_event:  'agent.assumption_detected',
      severity:   'warn',
      category:   'trust',
      schema: {
        text:        'string  — the assumption the agent is making',
        action:      'string  — action that triggered this assumption',
        step_index:  'number',
        session_id:  'string',
        _seq:        'number',
      },
    },

    /* ── HITL / RECOVERY ────────────────────────────────────────── */

    HITL_REQUIRED: {
      bus_event:  'nx:hitl:required',
      sse_event:  'hitl.required',
      severity:   'critical',
      category:   'hitl',
      schema: {
        event_id:    'string  — unique HITL event ID for correlation',
        prompt:      'string  — question or description for operator',
        hitl_type:   'string  — clarification | escalation | approval',
        context:     'object  — {confidence, ambiguities, last_error, task_preview}',
        actions:     'string[]? — allowed operator responses (approve, reject, retry)',
        session_id:  'string',
        _seq:        'number',
      },
    },

    HITL_RESOLVED: {
      bus_event:  'nx:hitl:resolved',
      sse_event:  'hitl.resolved',
      severity:   'info',
      category:   'hitl',
      schema: {
        event_id:  'string',
        action:    'string  — approve | reject | retry',
        session_id:'string',
        _seq:      'number',
      },
    },

    /* ── ORCHESTRATION LIFECYCLE ─────────────────────────────────── */

    TASK_COMPLETE: {
      bus_event:  'session.done',
      sse_event:  'agent.task_complete',
      severity:   'info',
      category:   'orchestration',
      schema: {
        status:          'string  — success | partial | failed',
        confidence:      'number? — final confidence 0.0–1.0',
        completed_steps: 'number?',
        total_steps:     'number?',
        session_id:      'string',
        _seq:            'number',
      },
    },

    TASK_ERROR: {
      bus_event:  'session.error',
      sse_event:  'agent.error',
      severity:   'error',
      category:   'orchestration',
      schema: {
        error:      'string',
        session_id: 'string',
        _seq:       'number',
      },
    },

    TASK_CANCELLED: {
      bus_event:  'session.cancelled',
      sse_event:  'task.cancelled',
      severity:   'warn',
      category:   'orchestration',
      schema: {
        session_id: 'string',
        _seq:       'number',
      },
    },

    /* ── RESOURCE GOVERNANCE ────────────────────────────────────── */

    BUDGET_UPDATE: {
      bus_event:  'budget.update',
      sse_event:  'agent.budget_update',
      severity:   'info',
      category:   'resource',
      schema: {
        tokens:     'number  — total tokens used this session',
        token_max:  'number  — soft limit (configurable)',
        steps:      'number  — plan steps executed',
        step_max:   'number  — MAX_AGENT_LOOPS value',
        cost_usd:   'number? — estimated USD cost if available',
        session_id: 'string',
        _seq:       'number',
      },
    },

    DEGRADED_MODE: {
      bus_event:  'agent.degraded',
      sse_event:  'agent.degraded_mode',
      severity:   'warn',
      category:   'orchestration',
      schema: {
        reason:     'string  — why degraded mode was entered',
        fallback:   'string  — what was substituted',
        session_id: 'string',
        _seq:       'number',
      },
    },
  });

  /**
   * NxSignals.validate(busEvent, payload)
   *
   * Lightweight schema validation. Only active in dev mode.
   * Returns { valid: bool, missing: string[] }
   */
  function validate(busEvent, payload) {
    const sig = Object.values(SIGNALS).find(s => s.bus_event === busEvent);
    if (!sig) return { valid: true, missing: [] };
    const missing = Object.keys(sig.schema).filter(k => {
      if (k.startsWith('_')) return false; // internal fields
      if (sig.schema[k].includes('?')) return false; // optional
      return !(k in payload);
    });
    return { valid: missing.length === 0, missing };
  }

  /**
   * NxSignals.describe(busEvent)
   * Returns the signal descriptor for a given bus event name.
   */
  function describe(busEvent) {
    return Object.values(SIGNALS).find(s => s.bus_event === busEvent) || null;
  }

  /**
   * NxSignals.list()
   * Returns all signal keys.
   */
  function list() { return Object.keys(SIGNALS); }

  window.NxSignals = Object.freeze({ SIGNALS, validate, describe, list });

  /* ── SSE → NxBus Bridge Extension ─────────────────────────────────
   * Extend nx-sse-runtime.js dispatch to route additional events that
   * NxAgiSurface listens for, translating NxBus STREAM_CHUNK shapes
   * into the canonical cognition-surface event names.
   * ─────────────────────────────────────────────────────────────────
   */
  function _wireAgiSurfaceBridge() {
    if (!window.NxBus || !NxBus.EVENTS) { setTimeout(_wireAgiSurfaceBridge, 200); return; }
    const E = NxBus.EVENTS;

    /* Translate nx:stream:chunk {kind:'think'} → agent.thought */
    NxBus.on(E.STREAM_CHUNK, (d) => {
      if (!d) return;
      switch (d.kind) {
        case 'think':
          NxBus.emit('agent.thought', {
            text:       d.thought || d.text || '',
            step_index: d.step_index,
            step_text:  d.step_text,
            session_id: d.session_id,
          });
          break;
        case 'action':
          NxBus.emit('agent.tool_call', {
            name:       d.tool,
            args:       d.args,
            step_index: d.step_index,
            step_text:  d.step_text,
            session_id: d.session_id,
          });
          break;
        case 'tool_success':
          NxBus.emit('agent.tool_result', {
            name:       d.tool,
            output:     d.output,
            path:       d.path,
            step_index: d.step_index,
            session_id: d.session_id,
          });
          break;
        case 'output':
          /* plain output lines — route to cognition stream as plan type */
          if (d.text && d.text.trim()) {
            NxBus.emit('agent.plan', {
              text:       d.text,
              session_id: d.session_id,
            });
          }
          break;
      }
    }, { owner: 'nx-signals-bridge' });

    /* nx:trust:signal → trust.score + trust.validation */
    NxBus.on('nx:trust:signal', (d) => {
      if (!d) return;
      const pct = Math.round((d.confidence ?? 1.0) * 100);
      const level = pct >= 75 ? 'high' : pct >= 40 ? 'medium' : 'low';
      NxBus.emit('trust.score', { pct, level, message: d.message, session_id: d.session_id });

      /* Map signal type to semantic validation checks */
      const checks = [];
      if (d.type === 'action_success')   checks.push({ label: d.action || 'action', status: 'pass' });
      if (d.type === 'verification')     checks.push({ label: 'verify', status: d.verified ? 'pass' : 'warn' });
      if (d.type === 'contradiction')    checks.push({ label: 'consistency', status: 'fail' });
      if (d.type === 'assumption')       checks.push({ label: 'assumption', status: 'warn' });
      if (checks.length) NxBus.emit('trust.validation', { checks });

      /* Surface assumption */
      if (d.type === 'assumption' && d.message) {
        NxBus.emit('trust.assumption', { text: d.message });
      }
    }, { owner: 'nx-signals-bridge' });

    /* nx:dag:update → agent.dag_update (for NxAgiSurface + NxDagEngine) */
    NxBus.on('nx:dag:update', (d) => {
      if (!d) return;
      NxBus.emit('agent.dag_update', d);
    }, { owner: 'nx-signals-bridge' });

    /* nx:hitl:required → hitl.required */
    NxBus.on('nx:hitl:required', (d) => {
      if (!d) return;
      NxBus.emit('hitl.required', {
        reason:  d.prompt || d.reason || 'Agent requires human input.',
        actions: d.actions,
        context: d.context,
      });
    }, { owner: 'nx-signals-bridge' });

    /* nx:hitl:resolved → hitl.resolved */
    NxBus.on('nx:hitl:resolved', (d) => {
      if (!d) return;
      NxBus.emit('hitl.resolved', d);
    }, { owner: 'nx-signals-bridge' });

    /* AGENT_DONE → session.done */
    NxBus.on(E.AGENT_DONE, (d) => {
      NxBus.emit('session.done', d || {});
      if (d?.confidence != null) {
        const pct = Math.round(d.confidence * 100);
        NxBus.emit('trust.score', { pct, level: pct >= 75 ? 'high' : pct >= 40 ? 'medium' : 'low' });
      }
      /* Final budget snapshot from step counts */
      if (d?.completed_steps != null && d?.total_steps != null) {
        NxBus.emit('budget.update', {
          steps:    d.completed_steps,
          step_max: d.total_steps,
          tokens:   0, token_max: 100000,  // token data comes separately
        });
      }
    }, { owner: 'nx-signals-bridge' });

    /* AGENT_STOP → session.idle */
    NxBus.on(E.AGENT_STOP, () => {
      NxBus.emit('session.idle', {});
    }, { owner: 'nx-signals-bridge' });

    /* STREAM_ERROR → session.error */
    NxBus.on(E.STREAM_ERROR, (d) => {
      NxBus.emit('session.error', { message: d?.error || 'Stream error' });
    }, { owner: 'nx-signals-bridge' });

    /* nx:sse:event catch-all for extended signal types */
    NxBus.on('nx:sse:event', (d) => {
      if (!d || !d.type) return;
      switch (d.type) {
        case 'agent.memory_retrieved':
          NxBus.emit('memory.retrieved', d);
          break;
        case 'agent.assumption_detected':
          NxBus.emit('trust.assumption', { text: d.text || d.assumption || '' });
          break;
        case 'agent.budget_update':
          NxBus.emit('budget.update', d);
          break;
        case 'agent.degraded_mode':
          NxBus.emit('agent.degraded', d);
          NxBus.emit('agent.thought', { text: `⚠ Degraded mode: ${d.reason || ''}` });
          break;
      }
    }, { owner: 'nx-signals-bridge' });
  }

  /* Init bridge after DOMContentLoaded (NxBus may not be ready yet) */
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => setTimeout(_wireAgiSurfaceBridge, 50));
  } else {
    setTimeout(_wireAgiSurfaceBridge, 50);
  }

})();
