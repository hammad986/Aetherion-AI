/**
 * nx-z43-exec-state.js — Z43A Execution State Wiring
 * Watches #runBtn.is-running and reflects it onto document.body[data-nx-exec]
 * so Z43 CSS can style the entire workspace based on execution state.
 *
 * Approach: MutationObserver on #runBtn — zero polling, zero timers.
 * Falls back to interval for environments where runBtn mounts late.
 */
(function () {
  'use strict';

  function syncExecState(btn) {
    const running = btn && btn.classList.contains('is-running');
    document.body.dataset.nxExec = running ? 'running' : 'idle';
  }

  function attachObserver(btn) {
    syncExecState(btn);
    var mo = new MutationObserver(function () { syncExecState(btn); });
    mo.observe(btn, { attributes: true, attributeFilter: ['class'] });
  }

  function init() {
    var btn = document.getElementById('runBtn');
    if (btn) {
      attachObserver(btn);
      return;
    }
    // runBtn not yet in DOM — wait for it
    var docObserver = new MutationObserver(function () {
      var b = document.getElementById('runBtn');
      if (b) { docObserver.disconnect(); attachObserver(b); }
    });
    docObserver.observe(document.body, { childList: true, subtree: true });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
