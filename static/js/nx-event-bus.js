/* ═══════════════════════════════════════════════════════════════════════════
   NX Event Bus — Z22 Cross-Module Communication
   Typed, owned, lightweight event bus.  No external deps.
   Usage:
     NxBus.on('tabChange', handler, 'MyModule');
     NxBus.emit('tabChange', { tab: 'logs' });
     NxBus.clear('MyModule'); // cleanup on module teardown
   ═══════════════════════════════════════════════════════════════════════════ */
(function NxEventBus() {
  'use strict';

  /* Map<eventName, Array<{fn, owner}>> */
  const _registry = new Map();

  function on(event, fn, owner) {
    if (typeof fn !== 'function') return;
    if (!_registry.has(event)) _registry.set(event, []);
    _registry.get(event).push({ fn, owner: owner || 'anonymous' });
  }

  function once(event, fn, owner) {
    function wrapper(data) {
      fn(data);
      off(event, wrapper);
    }
    on(event, wrapper, owner);
  }

  function off(event, fn) {
    if (!_registry.has(event)) return;
    _registry.set(event, _registry.get(event).filter(e => e.fn !== fn));
  }

  function emit(event, data) {
    if (!_registry.has(event)) return;
    const handlers = _registry.get(event).slice(); // snapshot for re-entrancy safety
    for (const { fn } of handlers) {
      try { fn(data); } catch (err) {
        console.warn('[NxBus] Handler error for "' + event + '":', err);
      }
    }
  }

  /* Remove all handlers registered by a given owner module (call on cleanup) */
  function clear(owner) {
    for (const [event, handlers] of _registry.entries()) {
      _registry.set(event, handlers.filter(e => e.owner !== owner));
    }
  }

  /* Debug helper — list all registered events */
  function _debug() {
    const out = {};
    for (const [k, v] of _registry.entries()) out[k] = v.map(e => e.owner);
    return out;
  }

  window.NxBus = { on, once, off, emit, clear, _debug };
  console.log('[NxBus] Event bus ready');
})();
