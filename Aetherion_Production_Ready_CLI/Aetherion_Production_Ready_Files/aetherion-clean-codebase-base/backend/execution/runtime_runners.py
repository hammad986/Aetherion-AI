from opentelemetry import trace
import re
ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")
from execution_engine import *

tracer = trace.get_tracer(__name__)

def run_session(sid):
    with tracer.start_as_current_span("run_session") as span:
        span.set_attribute("session_id", sid)
        sess = db_session(sid)
        if not sess: return

        cfg = json.loads(sess.get("config_json") or "{}") or default_managed_config()
    mode = cfg.get("mode", "managed")

    # Phase 8 — pick entry point based on the thinking_mode flag.
    # ON  → orchestrator.py (THINK→PLAN→EXECUTE→VERIFY→REFLECT→ADAPT,
    #       multi-strategy retries, automatic decomposition)
    # OFF → main.py (raw single-shot agent, original behaviour)
    # Both entry points accept the same `--model` flag, so a forced
    # provider chosen via Settings → Force Model is honoured in either
    # mode. The orchestrator forwards it to Agent(force_model=…).
    thinking_mode = bool(cfg.get("thinking_mode", True))
    if thinking_mode:
        cmd = [sys.executable, "-u", "agent_state_machine.py"]
        if sess["model"]:
            cmd += ["--model", sess["model"]]
        cmd += ["--session-id", str(sid)]
        cmd.append(sess["task"])
    else:
        cmd = [sys.executable, "-u", "main.py"]
        if sess["model"]:
            cmd += ["--model", sess["model"]]
        cmd.append(sess["task"])

    started = time.time()
    execution_id = f"exec_{sid}_{int(started * 1000)}"
    try:
        from services.checkpoint_service import CheckpointService
        CheckpointService.start_checkpoint(sid, sess.get("task", ""), started)
    except Exception:
        logger.debug("[checkpoint] save failed (non-critical)", exc_info=True)
    try:
        from execution.truth_kernel import get_ledger
        get_ledger().record_task_started(
            sid,
            execution_id,
            task=sess.get("task"),
            source="execution_engine.run_session",
        )
    except Exception:
        logger.error("Swallowed exception intercepted", exc_info=True)
    db_update_session(sid, status="running", started_at=started, stage="starting",
                      step=None, current_model=None, retry_count=0,
                      error_category=None, validation=None, result=None,
                      success=None, exit_code=None, files_json=None)

    # Phase 7.0 — each session writes into its own folder so files never
    # collide and the UI can list/serve/zip them per session.
    ws_dir = session_workspace(sid)
    ws_before = snapshot_workspace(ws_dir)
    seq = 0

    # ── Phase 6: per-session usage tracking (estimated, never authoritative) ──
    # Tokens are approximated as bytes/4 of agent output; calls are counted from
    # [ROUTE] markers. Recorded per current model so the UI can show a breakdown.
    usage = {
        "calls": 0,
        "tokens_in_est": max(1, len(sess["task"]) // 4),
        "tokens_out_est": 0,
        "by_model": {},     # {model: {"calls": n, "tokens_out_est": n}}
        "started_at": started,
    }
    state = {"current_model": None}

    def _flush_usage():
        db_update_session(sid, usage_json=json.dumps(usage))

    def emit(level, text):
        nonlocal seq
        seq += 1
        ts = time.time()
        db_insert_log(sid, seq, ts, level, text)
        running["seq"] = seq
        _nx_redis.set_running(sid, seq=seq)  # Phase Z7: sync seq to Redis

    def track_usage_from_line(line):
        m = ROUTE_RE.search(line)
        if m:
            usage["calls"] += 1
            target = m.group(1).strip()
            mm = MODEL_RE.search(target)
            model = (mm.group(1) if mm else target.split()[0]).lower()[:32]
            state["current_model"] = model
            slot = usage["by_model"].setdefault(
                model, {"calls": 0, "tokens_out_est": 0})
            slot["calls"] += 1
            return
        # Approximate output tokens from any non-marker content while a model is active
        if state["current_model"] and not line.startswith(("[", "▶", "  Forced")):
            n = max(1, len(line) // 4)
            usage["tokens_out_est"] += n
            slot = usage["by_model"].setdefault(
                state["current_model"], {"calls": 0, "tokens_out_est": 0})
            slot["tokens_out_est"] += n

    # BYOK is the only execution mode. Log provider configuration.
    order = " -> ".join(cfg.get("fallback_order") or [])
    provs = ", ".join(cfg.get("providers") or [])
    if provs:
        emit("info", f"[BYOK] Providers: {provs}")
        emit("info", f"[BYOK] Fallback order: {order}")
    else:
        emit("warning", "[BYOK] No providers configured. Add API keys in Settings to execute tasks.")
    emit("system", f"▶ Starting task: {sess['task']}")
    if sess["model"]:
        emit("system", f"  Forced model: {sess['model']}")

    proc_env = env_for_session(cfg)
    # config.py honors $WORKSPACE_DIR — point the agent at this session's
    # isolated folder. tools.py reads config.WORKSPACE_DIR at __init__ time
    # so this picks up cleanly without modifying any agent file.
    proc_env["WORKSPACE_DIR"] = ws_dir
    proc_env["AETHERION_SESSION_ID"] = str(sid)
    proc_env["AETHERION_EXECUTION_ID"] = execution_id

    try:
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace", bufsize=1, cwd=BASE_DIR,
            env={**proc_env, "PYTHONUNBUFFERED": "1"},
            preexec_fn=os.setsid if os.name != "nt" else None,
        )
        running["proc"] = proc
        usage_flush_at = time.time()
        for raw in proc.stdout:
            line = ANSI_RE.sub("", raw.rstrip("\n"))
            if not line: continue
            
            # Phase 28.1 - Strip noisy logs
            if "%|" in line or line.startswith("Batches: ") or "onnx.tar.gz" in line or "it/s]" in line:
                continue

            # Phase 15: SSE reasoning stream interception
            if line.startswith("[ORCHESTRATOR] ") or line.startswith("[AGENT_RECORD] "):
                try:
                    import json as _json
                    payload = _json.loads(line.split(" ", 1)[1])
                    from event_bus import global_event_bus
                    global_event_bus.publish(sid, payload.get("kind", "unknown"), payload)
                    from streaming.sse_manager import SSEManager
                    SSEManager.broadcast_to_session(sid, payload.get("kind", "unknown"), payload)
                    # Wire infra event bus (Redis-backed telemetry + cluster fanout)
                    try:
                        from infra.event_bus import get_event_bus
                        get_event_bus().publish_to_session(
                            session_id=sid,
                            event_type=payload.get("kind", "unknown"),
                            payload=payload,
                        )
                    except Exception:
                        pass  # Redis unavailable — SSE path above is unaffected
                except Exception:
                    pass

            level = classify(line)
            emit(level, line)
            update_state_from_line(sid, line)
            parse_decisions(line, sid, time.time())
            track_usage_from_line(line)
            # Flush usage to DB at most every 1s to avoid hammering writes
            if time.time() - usage_flush_at > 1.0:
                _flush_usage()
                usage_flush_at = time.time()
        proc.wait()
        exit_code = proc.returncode
    except Exception as e:
        emit("error", f"[EXCEPTION] {e}")
        exit_code = -1
    finally:
        # Reliability fix: explicitly terminate the agent subprocess if it is
        # still alive. This prevents zombie/orphaned processes when Gunicorn
        # recycles a worker mid-execution (SIGTERM to worker does not propagate
        # to the subprocess on all platforms).
        try:
            if proc and proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except Exception:
                    proc.kill()  # force-kill if it ignores SIGTERM
        except Exception:
            logger.error("Swallowed exception intercepted", exc_info=True)
        running["proc"] = None
        files = diff_workspace(ws_before, ws_dir)
        try:
            from execution.truth_kernel import get_ledger
            ledger = get_ledger()
            ledger.record_tool_result(
                sid,
                execution_id,
                "agent_process",
                exit_code == 0,
                exit_code=exit_code,
                source="execution_engine.run_session",
            )
            truth = ledger.finalize_task(
                sid,
                execution_id,
                exit_code == 0,
                reason=f"agent_process_exit_{exit_code}",
                require_verifier=True,
                source="execution_engine.run_session",
            )
            success = 1 if truth.status == "succeeded" else 0
            status = "success" if success else (
                "stopped" if exit_code in (-15, -9, 143, 137) else "failed")
        except Exception:
            logger.error("Swallowed exception intercepted", exc_info=True)
            success = 0
            status = "stopped" if exit_code in (-15, -9, 143, 137) else "failed"
        finished = time.time()
        try:
            from services.checkpoint_service import CheckpointService
            if success:
                CheckpointService.mark_checkpoint_complete(sid)
            else:
                CheckpointService.mark_checkpoint_failed(sid, exit_code, status)
        except Exception:
            logger.debug("[checkpoint] mark failed (non-critical)", exc_info=True)
        # Phase 12 — record AI response in chat history
        try:
            result_text = (db_session(sid) or {}).get("result") or ""
            if status == "success":
                ai_reply = result_text or f"Task completed successfully in {usage['elapsed_seconds']}s."
            elif status == "stopped":
                ai_reply = "Task was stopped."
            else:
                ai_reply = result_text or f"Task encountered an error (exit code {exit_code})."
            db_chat_add(sid, "assistant", ai_reply,
                        meta={"status": status, "elapsed": usage["elapsed_seconds"],
                              "files": files[:10]})
        except Exception:
            logger.error("Swallowed exception intercepted", exc_info=True)
        # ── Real-Time Notification: task done/failed ─────────────────────────
        try:
            from streaming.sse_manager import SSEManager
            if status == "success":
                SSEManager.broadcast_to_session(sid, "task.completed", {"status": "success", "ai_reply": ai_reply})
                logger.info(f"[run_session] Broadcasted task.completed for {sid}")
            else:
                SSEManager.broadcast_to_session(sid, "task.failed", {"error": ai_reply})
                logger.info(f"[run_session] Broadcasted task.failed for {sid}")
        except Exception as e:
            logger.error("SSE Notification failed", exc_info=True)
            
        try:
            if _NOTIF_AVAILABLE:
                _n_uid = cfg.get("_notify_user_id", "")
                if _n_uid:
                    _task_label = (sess.get("task") or "")[:120]
                    if status == "success":
                        _notif.notify_task_complete(_n_uid, _task_label, sid=sid)
                    elif status == "failed":
                        _notif.notify_task_failed(_n_uid, _task_label,
                                                  reason=f"exit code {exit_code}", sid=sid)
        except Exception:
            logger.error("Swallowed exception intercepted", exc_info=True)
        # Phase 14 — async self-improvement: reflect on the completed task
        try:
            if get_setting("p14_learning_enabled", True) and status != "stopped":
                _task_str = (cur.get("task") or "")[:500]
                _hist_str = result_text[:2000] if result_text else ""
                _success = bool(success)
                def _p14_reflect(_task=_task_str, _hist=_hist_str, _ok=_success):
                    try:
                        from evolution_engine import get_evolution_engine
                        get_evolution_engine().reflect_on_task(_task, _ok, _hist)
                        logger.info(f"[Phase 14] Reflection stored for task: {_task[:60]}")
                    except Exception as e:
                        logger.debug(f"[Phase 14] Reflect error: {e}")
                t = threading.Thread(target=_p14_reflect, daemon=True)
                t.start()
        except Exception:
            logger.error("Swallowed exception intercepted", exc_info=True)
        # Phase 17 — if this session is part of a chain, mark the
        # parent chain task done now that the subprocess has finished.
        # The chain row's status auto-rolls-up inside mark_task_done.
        chain_task_id = cur.get("chain_task_id")
        chain_id      = cur.get("chain_id")
        if chain_task_id is not None:
            try:
                runner = _get_chain_runner()
                if runner is not None:
                    # ── Phase 18 idempotency: only act on tasks that
                    # are still mid-flight. If the row is already in
                    # a terminal status (completed / failed / skipped)
                    # then a previous hook run already handled it —
                    # firing again would re-emit events, double-bump
                    # the replan budget, and stamp over the existing
                    # last_error. Bail early. ──
                    cur_task = runner.memory.get_task(
                        int(chain_task_id))
                    if cur_task and cur_task.get("status") not in (
                            "completed", "failed", "skipped"):
                        # ── Phase 18 retry-aware terminal handling
                        # for the subprocess path. Mirrors run_next:
                        # on retryable failure flip back to pending so
                        # the next /run-next call uses the augmented
                        # goal; on terminal failure mark done + try to
                        # replan. ──
                        attempts_used = int(cur_task.get("attempts", 0)
                                            or 0)
                        max_attempts = runner.max_attempts_per_task
                        retry_reason = (f"session={sid} status={status} "
                                        f"exit={exit_code}")
                        if (not success
                                and attempts_used < max_attempts):
                            # Retryable — flip back to pending so the
                            # next /run-next call picks it up with the
                            # augmented goal. If reset_ok is False
                            # another writer already moved the row off
                            # `running`; emit a progress event and let
                            # whoever owns the row finish — the new
                            # status-conditional mark_task_done makes
                            # that path idempotent.
                            reset_ok = runner.memory.reset_task_for_retry(
                                int(chain_task_id),
                                last_error=retry_reason[:500])
                            runner._emit("task_progress", {
                                "chain_id": chain_id,
                                "task_id": chain_task_id,
                                "status": ("pending_retry" if reset_ok
                                           else "race_lost"),
                                "attempt": attempts_used,
                                "last_error": retry_reason[:200],
                            })
                        else:
                            runner.memory.mark_task_done(
                                int(chain_task_id),
                                ok=bool(success), reason=retry_reason)
                            runner._emit("task_completed", {
                                "chain_id": chain_id,
                                "task_id": chain_task_id,
                                "ok": bool(success),
                                "reason": f"session {sid} "
                                          f"{status}"[:200],
                            })
                            if not success:
                                try:
                                    runner.maybe_replan_after_failure(
                                        int(chain_id),
                                        int(chain_task_id))
                                except Exception as e:
                                    emit("warn",
                                         f"[chain-hook] replan "
                                         f"failed: {e}")
                            # ── Phase 19 — chain rollup hook for the
                            # goal engine. mark_task_done already
                            # rolled the chain up; if the chain is
                            # now in a terminal state we (a) emit
                            # `goal_completed` for system_generated
                            # chains so the UI can mark them done,
                            # and (b) ask the engine whether a new
                            # goal cycle should run. The engine has
                            # its own enabled/cooldown/cap/loop
                            # gates — this call is fire-and-forget. ──
                            try:
                                chain_row = runner.memory.get_chain(
                                    int(chain_id))
                                chain_meta = (chain_row or {}).get(
                                    "chain", {})
                                cstatus = chain_meta.get("status")
                                # Phase 19 — authoritative lookup: the
                                # `system_generated` flag is now
                                # surfaced directly by get_chain so the
                                # loop-guard works regardless of how
                                # old the chain is. (Earlier revisions
                                # scanned list_chains(limit=200) which
                                # silently misclassified old chains.)
                                sys_gen = bool(
                                    chain_meta.get("system_generated",
                                                    False))
                                if cstatus in ("completed", "failed"):
                                    if sys_gen:
                                        _emit_goal_event(
                                            "goal_completed",
                                            int(chain_id),
                                            {"status": cstatus,
                                             "via_session": sid})
                                    # Trigger a new analysis cycle.
                                    # The engine refuses if the parent
                                    # chain was system_generated, so
                                    # there's no recursive spawn.
                                    _maybe_trigger_goal_engine_async(
                                        reason=("chain_failed"
                                                if cstatus == "failed"
                                                else "chain_completed"),
                                        parent_chain_id=int(chain_id),
                                        parent_system_generated=sys_gen,
                                    )
                            except Exception as ge:
                                emit("warn",
                                     f"[goal-engine] post-chain "
                                     f"trigger failed: {ge}")
            except Exception as e:
                emit("warn", f"[chain-hook] mark_task_done failed: {e}")
        return bool(success)

def p7_execute_pipeline(sid: str, task: str, log_text: str,
                         plan_mode: str, enabled_agents: dict) -> None:
    """Run the multi-agent pipeline in a background thread."""
    with _p7_lock:
        pipeline = _P7_PIPELINES.get(sid)
        if not pipeline:
            return
        pipeline["stage"] = "running"
        pipeline["started_at"] = time.time()

    # Extract code snippets from log (lines that look like code)
    code_snippets = [ln for ln in log_text.split("\n")
                     if any(h in ln for h in ["def ", "class ", "import ", "function", "const ", "var "])]

    for agent_id in _P7_PIPELINE_ORDER:
        if agent_id not in _P7_AGENTS:
            continue
        # Check user toggle
        if not enabled_agents.get(agent_id, True):
            with _p7_lock:
                for ag in _P7_PIPELINES[sid]["agents"]:
                    if ag["id"] == agent_id:
                        ag["status"] = "skipped"
                        ag["skipped_reason"] = "disabled"
            continue
        # Check smart triggering
        should_run = p7_should_trigger(agent_id, task, log_text, plan_mode)
        if not should_run:
            with _p7_lock:
                for ag in _P7_PIPELINES[sid]["agents"]:
                    if ag["id"] == agent_id:
                        ag["status"] = "skipped"
                        ag["skipped_reason"] = "not triggered"
            continue
        # Mark as running
        with _p7_lock:
            for ag in _P7_PIPELINES[sid]["agents"]:
                if ag["id"] == agent_id:
                    ag["status"] = "running"
                    ag["started_at"] = time.time()
        # Run the agent
        try:
            t0 = time.time()
            if agent_id == "reviewer":
                result = p7_run_reviewer(task, log_text, code_snippets)
            elif agent_id == "debugger":
                result = p7_run_debugger(task, log_text)
            elif agent_id == "tester":
                result = p7_run_tester(task, log_text, code_snippets)
            elif agent_id == "security":
                result = p7_run_security(task, log_text, code_snippets)
            elif agent_id == "optimizer":
                result = p7_run_optimizer(task, log_text, code_snippets)
            else:
                result = {}
            elapsed = round(time.time() - t0, 2)
            with _p7_lock:
                for ag in _P7_PIPELINES[sid]["agents"]:
                    if ag["id"] == agent_id:
                        ag["status"]   = "done"
                        ag["result"]   = result
                        ag["elapsed"]  = elapsed
        except Exception as exc:
            with _p7_lock:
                for ag in _P7_PIPELINES[sid]["agents"]:
                    if ag["id"] == agent_id:
                        ag["status"] = "error"
                        ag["error"]  = str(exc)[:200]
        # Small pause between agents so the UI can show sequential progress
        time.sleep(0.4)

    with _p7_lock:
        if sid in _P7_PIPELINES:
            _P7_PIPELINES[sid]["stage"] = "done"
            _P7_PIPELINES[sid]["finished_at"] = time.time()

@app.route("/api/stream/<sid>")
def api_realtime_stream(sid):
    """
    Realtime SSE stream for a session — consumes the SSEManager subscriber queue.

    Event types emitted:
      agent.task_start    — task accepted, work beginning
      agent.think         — LLM reasoning token (thought field)
      agent.action        — tool call about to execute
      agent.tool_success  — tool executed successfully
      file.modified       — agent mutated a file (triggers Monaco sync)
      agent.task_complete — task finished (status: done | timeout)
      task.started        — worker thread began
      task.completed      — worker thread finished
      task.failed         — worker thread errored
      hitl.required       — human approval needed (future)
      rollback.triggered  — state rollback occurred (future)
      heartbeat           — keep-alive ping every 15 s
    """
    # ── Feature-flag guard ─────────────────────────────────────────────────────
    if os.getenv("AETHERION_REALTIME_V1", "").lower() != "true":
        return jsonify({
            "ok": False,
            "error": "AETHERION_REALTIME_V1 flag is not enabled",
            "hint": "Set AETHERION_REALTIME_V1=true in your .env to activate realtime streaming.",
        }), 501

    if not sid:
        return jsonify({"ok": False, "error": "session_id required"}), 400

    # Register this HTTP connection as an SSE subscriber for the session.
    try:
        from streaming.sse_manager import SSEManager
    except ImportError:
        return jsonify({"ok": False, "error": "SSEManager unavailable"}), 503

    client = SSEManager.register_client(session_id=sid)
    
    try:
        from streaming.sse_redis import RedisSSEBridge
        RedisSSEBridge.subscribe_session(sid)
    except Exception as e:
        logger.warning(f"Failed to subscribe to Redis SSE bridge: {e}")
    
    try:
        from workspace_watcher import WorkspaceWatcher
        WorkspaceWatcher.start_for_session(sid, session_workspace(sid))
    except Exception as _we:
        logger.warning(f"Failed to start workspace watcher: {_we}")

    _SSE_HEADERS = {
        "Content-Type":      "text/event-stream",
        "Cache-Control":     "no-cache, no-transform",
        "X-Accel-Buffering": "no",   # nginx: disable proxy buffering
        "Connection":        "keep-alive",
    }

    def _generate():
        """
        Generator consumed by Flask's stream_with_context.
        Blocks on client.queue with a 15-second timeout to emit heartbeats.
        Terminates cleanly on:
          - queue sentinel (None) — session completed or server shutdown
          - GeneratorExit         — browser disconnected / tab closed
        Guarantees SSEManager.remove_client() is always called for cleanup.
        """
        import queue as _queue
        try:
            while True:
                try:
                    event = client.queue.get(timeout=15)
                except _queue.Empty:
                    # No events for 15 s — emit a comment heartbeat.
                    # SSE comments (lines starting with ":") are safe no-ops
                    # for EventSource but prevent proxy/load-balancer timeouts.
                    yield ": heartbeat\n\n"
                    continue

                if event is None:
                    # Sentinel: task finished or server is shutting down.
                    yield "event: done\ndata: {\"status\": \"session_closed\"}\n\n"
                    return

                # event is an SSEEvent — encode and stream.
                yield event.encode()

        except GeneratorExit:
            # Browser closed the connection — clean up silently.
            pass
        finally:
            # Always deregister — prevents queue memory leaks.
            SSEManager.remove_client(client.client_id)
            try:
                from workspace_watcher import WorkspaceWatcher
                WorkspaceWatcher.stop_for_session(sid)
            except Exception:
                pass

    return Response(
        stream_with_context(_generate()),
        headers=_SSE_HEADERS,
    )