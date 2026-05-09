(function () {
    'use strict';

    const now = () => (window.performance && typeof performance.now === 'function')
        ? performance.now()
        : Date.now();

    const MAX_LOG = 120;
    const SEARCH = (() => {
        try { return new URLSearchParams(window.location.search || ''); }
        catch { return new URLSearchParams(''); }
    })();

    function parseCsvParam(name) {
        const raw = (SEARCH.get(name) || '').trim();
        if (!raw) return new Set();
        return new Set(raw.split(',').map((part) => part.trim().toLowerCase()).filter(Boolean));
    }

    function pushBounded(list, item) {
        list.push(item);
        if (list.length > MAX_LOG) list.shift();
    }

    function ensureDiagnostics(nx) {
        nx.diagnostics = nx.diagnostics || {
            bootStartedAt: 0,
            bootCompletedAt: 0,
            interactiveAt: 0,
            firstInteractionAt: 0,
            stages: [],
            modules: [],
            errors: [],
            interactions: [],
            api: [],
            layout: [],
            performance: [],
            degradedModules: [],
            missingGlobals: [],
        };
        return nx.diagnostics;
    }

    function recordStage(name, extra) {
        const diag = ensureDiagnostics(window.NX);
        pushBounded(diag.stages, { name, at: now(), ...(extra || {}) });
    }

    function recordError(type, error, context) {
        const diag = ensureDiagnostics(window.NX);
        const entry = {
            type,
            message: error && error.message ? error.message : String(error || 'unknown error'),
            stack: error && error.stack ? String(error.stack) : '',
            at: now(),
            ...(context || {}),
        };
        pushBounded(diag.errors, entry);
        console.error('[NX:RECOVERY]', type, context || {}, error);
    }

    function recordPerformance(name, data) {
        const diag = ensureDiagnostics(window.NX);
        pushBounded(diag.performance, { name, at: now(), ...(data || {}) });
    }

    function noteDegraded(moduleName, reason) {
        const nx = window.NX;
        const diag = ensureDiagnostics(nx);
        if (!diag.degradedModules.some((m) => m.module === moduleName)) {
            pushBounded(diag.degradedModules, { module: moduleName, reason, at: now() });
        }
        nx.state = nx.state === 'interactive' ? 'interactive-degraded' : 'degraded';
    }

    function markFirstInteraction() {
        const diag = ensureDiagnostics(window.NX);
        if (diag.firstInteractionAt) return;
        diag.firstInteractionAt = now();
        recordPerformance('first-interaction', {
            deltaMs: Math.round(diag.firstInteractionAt - (diag.bootStartedAt || diag.firstInteractionAt)),
        });
    }

    window.NX = window.NX || {
        state: 'booting',
        lastStatus: 'idle',
        config: null,
        activeSid: null,
        activeTab: 'logs',
        leftOpen: false,
        rightOpen: true,
        bottomOpen: false,
        leftW: 0,
        rightW: 290,
        metricTimer: null,
        paletteOpen: false,
        idleCollapseTimer: null,
        pollFast: null,
        pollSlow: null,
        planMode: 'elite',
        plusMenuOpen: false,
        planDropdownOpen: false,
        ghRepo: null,
        debugFlags: {
            disable: parseCsvParam('nx_disable'),
            disableSources: parseCsvParam('nx_disable_sources'),
            disableTasks: parseCsvParam('nx_disable_tasks'),
            counters: {
                intervalsScheduled: 0,
                timeoutsScheduled: 0,
                rafScheduled: 0,
                mutationObservers: 0,
                resizeObservers: 0,
                eventSources: 0,
            },
        },
    };

    const nx = window.NX;
    nx.state = nx.state || 'booting';
    nx.recovery = nx.recovery || {};
    nx.markFirstInteraction = markFirstInteraction;
    nx.recordStage = recordStage;
    nx.recordError = recordError;
    nx.recordPerformance = recordPerformance;
    nx.noteDegraded = noteDegraded;
    nx.logInteractionFailure = function (name, data) {
        const diag = ensureDiagnostics(nx);
        pushBounded(diag.interactions, { name, at: now(), ...(data || {}) });
    };
    nx.logApiDiagnostic = function (data) {
        const diag = ensureDiagnostics(nx);
        pushBounded(diag.api, { at: now(), ...(data || {}) });
    };
    nx.logLayoutDiagnostic = function (data) {
        const diag = ensureDiagnostics(nx);
        pushBounded(diag.layout, { at: now(), ...(data || {}) });
    };
    nx.hasDebugFlag = function (name) {
        return !!(name && nx.debugFlags && nx.debugFlags.disable && nx.debugFlags.disable.has(String(name).toLowerCase()));
    };
    window.nxDiagnosticsReport = function () {
        return JSON.stringify(ensureDiagnostics(window.NX), null, 2);
    };

    ensureDiagnostics(nx);

    function sourceFromStack(stack) {
        const lines = String(stack || '').split(/\r?\n/).map((line) => line.trim()).filter(Boolean);
        for (const line of lines) {
            const match = line.match(/(static\/js\/[^:\s)]+\.js)(?::(\d+):(\d+))?/i);
            if (match) return { file: match[1], line: match[2] || '', col: match[3] || '' };
        }
        return { file: 'unknown', line: '', col: '' };
    }

    function tagTask(task, phaseHint) {
        if (typeof task !== 'function') return task;
        if (task.__nxTagged) return task;
        const source = sourceFromStack(new Error().stack);
        Object.defineProperties(task, {
            __nxTagged: { value: true, configurable: true },
            __nxPhaseHint: { value: phaseHint || '', configurable: true },
            __nxSourceFile: { value: source.file, configurable: true },
            __nxSourceLine: { value: source.line, configurable: true },
            __nxTaskLabel: {
                value: task.name || `${source.file}${source.line ? ':' + source.line : ''}`,
                configurable: true,
            },
        });
        return task;
    }

    function patchTaskQueue(queue, phaseHint) {
        const origPush = queue.push.bind(queue);
        queue.push = function (...items) {
            return origPush(...items.map((item) => tagTask(item, phaseHint)));
        };
        for (let i = 0; i < queue.length; i++) queue[i] = tagTask(queue[i], phaseHint);
    }

    window.NX_BOOT_TASKS = window.NX_BOOT_TASKS || [];
    window.NX_LOAD_TASKS = window.NX_LOAD_TASKS || [];
    patchTaskQueue(window.NX_BOOT_TASKS, 'boot');
    patchTaskQueue(window.NX_LOAD_TASKS, 'load');

    (function installRuntimeCounters() {
        const origSetInterval = window.setInterval.bind(window);
        const origSetTimeout = window.setTimeout.bind(window);
        const origRaf = typeof window.requestAnimationFrame === 'function'
            ? window.requestAnimationFrame.bind(window)
            : null;
        const OrigMutationObserver = window.MutationObserver;
        const OrigResizeObserver = window.ResizeObserver;
        const OrigEventSource = window.EventSource;

        window.setInterval = function (fn, delay, ...args) {
            nx.debugFlags.counters.intervalsScheduled += 1;
            if (nx.hasDebugFlag('intervals')) {
                console.warn('[NX:DEBUG] interval blocked', delay);
                return 0;
            }
            return origSetInterval(fn, delay, ...args);
        };
        window.setTimeout = function (fn, delay, ...args) {
            nx.debugFlags.counters.timeoutsScheduled += 1;
            if (nx.hasDebugFlag('timeouts') && Number(delay || 0) >= 100) {
                console.warn('[NX:DEBUG] timeout blocked', delay);
                return 0;
            }
            return origSetTimeout(fn, delay, ...args);
        };
        if (origRaf) {
            window.requestAnimationFrame = function (callback) {
                nx.debugFlags.counters.rafScheduled += 1;
                if (nx.hasDebugFlag('raf')) {
                    console.warn('[NX:DEBUG] requestAnimationFrame blocked');
                    return 0;
                }
                return origRaf(callback);
            };
        }
        if (typeof OrigMutationObserver === 'function') {
            window.MutationObserver = class DebugMutationObserver extends OrigMutationObserver {
                constructor(callback) {
                    nx.debugFlags.counters.mutationObservers += 1;
                    if (nx.hasDebugFlag('mutationobservers')) {
                        console.warn('[NX:DEBUG] MutationObserver blocked');
                        super(() => {});
                        this.__nxBlocked = true;
                        return;
                    }
                    super(callback);
                }
                observe(...args) {
                    if (this.__nxBlocked) return;
                    return super.observe(...args);
                }
            };
        }
        if (typeof OrigResizeObserver === 'function') {
            window.ResizeObserver = class DebugResizeObserver extends OrigResizeObserver {
                constructor(callback) {
                    nx.debugFlags.counters.resizeObservers += 1;
                    if (nx.hasDebugFlag('resizeobservers')) {
                        console.warn('[NX:DEBUG] ResizeObserver blocked');
                        super(() => {});
                        this.__nxBlocked = true;
                        return;
                    }
                    super(callback);
                }
                observe(...args) {
                    if (this.__nxBlocked) return;
                    return super.observe(...args);
                }
            };
        }
        if (typeof OrigEventSource === 'function') {
            window.EventSource = function DebugEventSource(...args) {
                nx.debugFlags.counters.eventSources += 1;
                if (nx.hasDebugFlag('eventsource') || nx.hasDebugFlag('streams')) {
                    console.warn('[NX:DEBUG] EventSource blocked', args[0] || '');
                    return {
                        close() {},
                        addEventListener() {},
                        removeEventListener() {},
                        readyState: 2,
                    };
                }
                return new OrigEventSource(...args);
            };
        }
    })();

    let bootStarted = false;

    async function runTaskList(tasks, phase) {
        for (let i = 0; i < tasks.length; i++) {
            const task = tasks[i];
            if (typeof task !== 'function') continue;
            const taskName = task.__nxTaskLabel || task.name || `${phase}-task-${i}`;
            const taskKey = taskName.toLowerCase();
            const sourceKey = (task.__nxSourceFile || '').toLowerCase();
            if (nx.debugFlags.disableTasks.has(taskKey) || (sourceKey && nx.debugFlags.disableSources.has(sourceKey))) {
                console.warn(`[BOOT] Skipping ${phase} task: ${taskName}`);
                pushBounded(nx.diagnostics.modules, {
                    phase,
                    task: taskName,
                    ok: true,
                    skipped: true,
                    source: task.__nxSourceFile || '',
                });
                continue;
            }
            const t0 = now();
            recordStage(`${phase}:start`, { task: taskName });
            console.log(`[BOOT:${phase}] start ${taskName}${task.__nxSourceFile ? ` @ ${task.__nxSourceFile}` : ''}`);
            try {
                await task();
                const dt = now() - t0;
                pushBounded(nx.diagnostics.modules, {
                    phase,
                    task: taskName,
                    ok: true,
                    durationMs: Math.round(dt),
                    source: task.__nxSourceFile || '',
                });
                console.log(`[BOOT:${phase}] done ${taskName} (${dt.toFixed(1)}ms)`);
                if (dt > 10) {
                    console.warn(`[BOOT] Slow ${phase} task: ${taskName} took ${dt.toFixed(1)}ms`);
                }
            } catch (error) {
                const dt = now() - t0;
                pushBounded(nx.diagnostics.modules, {
                    phase,
                    task: taskName,
                    ok: false,
                    durationMs: Math.round(dt),
                    source: task.__nxSourceFile || '',
                });
                noteDegraded(taskName, `${phase} failure`);
                recordError(`${phase}-task-failed`, error, { task: taskName });
            }
            await new Promise((resolve) => setTimeout(resolve, 0));
        }
    }

    function installGlobalRecovery() {
        if (nx.recovery.globalHandlersInstalled) return;
        nx.recovery.globalHandlersInstalled = true;

        window.addEventListener('error', (event) => {
            recordError('window-error', event.error || event.message, {
                source: event.filename || '',
                line: event.lineno || 0,
                column: event.colno || 0,
            });
        });

        window.addEventListener('unhandledrejection', (event) => {
            recordError('unhandled-rejection', event.reason, {});
        });

        ['pointerdown', 'keydown', 'focusin'].forEach((type) => {
            window.addEventListener(type, markFirstInteraction, { once: true, capture: true });
        });

        if ('PerformanceObserver' in window) {
            try {
                const observer = new PerformanceObserver((list) => {
                    list.getEntries().forEach((entry) => {
                        if (entry.duration > 50) {
                            recordPerformance('long-task', { durationMs: Math.round(entry.duration) });
                        }
                    });
                });
                observer.observe({ entryTypes: ['longtask'] });
            } catch (error) {
                recordError('perf-observer', error, {});
            }
        }
    }

    window.nxBoot = async function () {
        if (bootStarted) return;
        bootStarted = true;

        installGlobalRecovery();
        nx.state = 'booting';
        nx.diagnostics.bootStartedAt = now();
        recordStage('boot:init');
        console.log('[BOOT] Starting Modular App Initialize...');
        if (
            nx.debugFlags.disable.size ||
            nx.debugFlags.disableSources.size ||
            nx.debugFlags.disableTasks.size
        ) {
            console.warn('[NX:DEBUG] active flags', {
                disable: Array.from(nx.debugFlags.disable),
                disableSources: Array.from(nx.debugFlags.disableSources),
                disableTasks: Array.from(nx.debugFlags.disableTasks),
            });
        }

        const startTime = now();
        await runTaskList(window.NX_BOOT_TASKS.slice(), 'boot');

        nx.state = nx.diagnostics.degradedModules.length ? 'interactive-degraded' : 'interactive';
        nx.diagnostics.bootCompletedAt = now();
        nx.diagnostics.interactiveAt = nx.diagnostics.bootCompletedAt;
        recordStage('boot:interactive', {
            durationMs: Math.round(nx.diagnostics.bootCompletedAt - startTime),
            degraded: nx.diagnostics.degradedModules.length,
        });

        console.log(`[BOOT] Interactive state reached in ${(now() - startTime).toFixed(1)}ms`);

        const loadTasks = window.NX_LOAD_TASKS.slice();
        loadTasks.forEach((task, i) => {
            const run = async () => {
                await runTaskList([task], 'load');
            };
            if (window.requestIdleCallback) {
                requestIdleCallback(run, { timeout: 700 });
            } else {
                setTimeout(run, 100 + (i * 10));
            }
        });
    };

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', window.nxBoot, { once: true });
    } else {
        setTimeout(window.nxBoot, 10);
    }
})();
