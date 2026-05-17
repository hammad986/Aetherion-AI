/**
 * nx-onboard.js — Aetherion Design System: Onboarding + Governance Runtime
 * Z59B: Enhanced first-run experience with keyboard shortcuts + starter tasks
 * Modules: NdsToast (canonical) | NdsOnboard (first-run) | NdsPerf (budget)
 */
(function(){'use strict';

/* ═══ 1. NdsToast — canonical toast (replaces scattered toast impls) ═══ */
const _region=(()=>{let r=document.getElementById('ndsToastRegion');if(!r){r=document.createElement('div');r.id='ndsToastRegion';r.className='nds-toast-region';r.setAttribute('role','status');r.setAttribute('aria-live','polite');r.setAttribute('aria-atomic','false');document.body.appendChild(r);}return r;});
const ICONS={ok:'✓',err:'✕',warn:'⚠',info:'ℹ'};
const DURS={ok:4000,err:7000,warn:5000,info:4000};

// Track active toasts to prevent duplicates
const _activeToasts = new Set();
const MAX_TOASTS = 3;

function ndsToast(msg,type='info',opts={}){
  // Deduplicate identical messages within 2 seconds
  const dedupeKey = type + ':' + msg;
  if (_activeToasts.has(dedupeKey)) return;
  _activeToasts.add(dedupeKey);
  setTimeout(() => _activeToasts.delete(dedupeKey), 2000);

  const r=_region();
  // Enforce max visible toasts
  const existing = r.querySelectorAll('.nds-toast');
  if (existing.length >= MAX_TOASTS) {
    const oldest = existing[0];
    oldest.setAttribute('data-leaving','');
    setTimeout(() => oldest.remove(), 200);
  }

  const el=document.createElement('div');
  el.className='nds-toast';el.setAttribute('data-type',type);
  el.setAttribute('role','alert');
  el.innerHTML=`<span style="flex-shrink:0">${ICONS[type]||ICONS.info}</span><span style="flex:1">${msg}</span>`;
  if(opts.action){const b=document.createElement('button');b.className='nds-btn nds-btn--xs nds-btn--ghost';b.textContent=opts.action.label;b.onclick=opts.action.fn;el.appendChild(b);}
  r.appendChild(el);
  const dur=opts.duration||DURS[type]||4000;
  setTimeout(()=>{el.setAttribute('data-leaving','');setTimeout(()=>el.remove(),200);},dur);
}

window.NdsToast=ndsToast;

// Shim legacy nxToast → NdsToast
window.nxToast=function(msg,type){ndsToast(msg,type||'info');};

/* ═══ 2. NdsOnboard — first-run experience (Z59B enhanced) ══════════════ */
const ONBOARD_KEY='nx_onboarded_v2';
const PRESETS=[
  {id:'builder', icon:'◈', name:'Builder',  desc:'Code + Inspector. Best for app development.'},
  {id:'debug',   icon:'◉', name:'Debug',    desc:'Full panels + logs. For debugging sessions.'},
  {id:'minimal', icon:'◻', name:'Minimal',  desc:'Clean focus view. No distractions.'},
  {id:'research',icon:'◎', name:'Research', desc:'Wide context panels for research tasks.'},
];
const SAMPLES=[
  {icon:'⬡', label:'Build a Python REST API with FastAPI',       task:'Build a Python REST API with FastAPI and SQLite'},
  {icon:'◈', label:'Create a React dashboard with charts',        task:'Create a React dashboard with live charts and data'},
  {icon:'◎', label:'Write a CLI tool in Python',                  task:'Write a CLI tool in Python with argument parsing'},
  {icon:'◉', label:'Debug and fix errors in my code',             task:'Debug my code, identify all errors and fix them'},
];
const SHORTCUTS=[
  {key:'⌘K',  desc:'Command palette'},
  {key:'↵',   desc:'Run task'},
  {key:'⌘/',  desc:'Toggle inspector'},
];

function _showOnboard(){
  if(localStorage.getItem(ONBOARD_KEY))return;
  const ov=document.createElement('div');
  ov.id='ndsOnboard';
  ov.style.cssText='position:fixed;inset:0;z-index:99985;background:rgba(13,17,23,0.88);backdrop-filter:blur(6px);display:flex;align-items:center;justify-content:center;animation:nxAuthCardIn 260ms ease both;';
  ov.innerHTML=`
    <div style="background:#161b22;border:1px solid #30363d;border-radius:16px;padding:32px 28px 24px;width:100%;max-width:440px;box-shadow:0 24px 64px rgba(0,0,0,0.6);position:relative;animation:nxAuthCardIn 300ms 60ms cubic-bezier(0.34,1.2,0.64,1) both;"
         role="dialog" aria-modal="true" aria-labelledby="ndsOnboardTitle">

      <!-- Header -->
      <div style="text-align:center;margin-bottom:24px;">
        <div style="width:48px;height:48px;background:rgba(188,140,255,0.1);border:1px solid rgba(188,140,255,0.2);border-radius:12px;display:flex;align-items:center;justify-content:center;margin:0 auto 12px;">
          <svg width="22" height="22" viewBox="0 0 32 32" fill="none"><polygon points="16,3 20.5,11.5 30,13 23,20 24.7,29.5 16,25 7.3,29.5 9,20 2,13 11.5,11.5" fill="#bc8cff" stroke="#6b3fa0" stroke-width="0.5"/></svg>
        </div>
        <div id="ndsOnboardTitle" style="font-size:18px;font-weight:700;color:#e6edf3;letter-spacing:-0.01em;margin-bottom:4px;">Welcome to Aetherion</div>
        <div style="font-size:13px;color:#6e7681;line-height:1.5;">An autonomous AI workspace that plans, codes, and ships software for you.</div>
      </div>

      <!-- Runtime readiness -->
      <div style="display:flex;align-items:center;gap:8px;padding:8px 14px;background:rgba(63,185,80,0.06);border:1px solid rgba(63,185,80,0.18);border-radius:8px;font-size:11.5px;color:#3fb950;font-weight:500;margin-bottom:20px;">
        <div style="width:6px;height:6px;border-radius:50%;background:#3fb950;flex-shrink:0;animation:z59PulseGreen 2s ease-in-out infinite;"></div>
        Ready
      </div>

      <!-- Layout presets -->
      <div style="font-size:10px;font-weight:700;color:#484f58;text-transform:uppercase;letter-spacing:.08em;margin-bottom:8px;">Choose your starting layout</div>
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:7px;margin-bottom:20px;" id="ndsOnboardPresets">
        ${PRESETS.map(p=>`<button class="nds-btn nds-btn--secondary" data-preset="${p.id}" title="${p.desc}"
          style="flex-direction:column;align-items:flex-start;padding:10px 12px;height:auto;gap:3px;text-align:left;">
          <span style="font-size:13px;font-weight:600;color:#c9d1d9;">${p.icon} ${p.name}</span>
          <span style="font-size:11px;color:#6e7681;font-weight:400;line-height:1.3;">${p.desc}</span>
        </button>`).join('')}
      </div>

      <!-- Starter tasks -->
      <div style="font-size:10px;font-weight:700;color:#484f58;text-transform:uppercase;letter-spacing:.08em;margin-bottom:8px;">Or start with a task</div>
      <div style="display:flex;flex-direction:column;gap:5px;margin-bottom:20px;" id="ndsOnboardSamples">
        ${SAMPLES.map(s=>`<button style="display:flex;align-items:center;gap:10px;padding:9px 12px;background:rgba(255,255,255,0.03);border:1px solid #21262d;border-radius:8px;color:#8b949e;font-size:12.5px;font-family:inherit;cursor:pointer;text-align:left;transition:all 140ms ease;"
          data-sample="${s.task}"
          onmouseenter="this.style.background='rgba(188,140,255,0.07)';this.style.borderColor='rgba(188,140,255,0.3)';this.style.color='#c9d1d9';"
          onmouseleave="this.style.background='rgba(255,255,255,0.03)';this.style.borderColor='#21262d';this.style.color='#8b949e';">
          <span style="opacity:0.5;font-size:13px;">${s.icon}</span>
          <span>${s.label}</span>
        </button>`).join('')}
      </div>

      <!-- Keyboard shortcuts -->
      <div style="display:flex;align-items:center;gap:14px;padding:10px 12px;background:#0d1117;border-radius:8px;border:1px solid #21262d;margin-bottom:20px;">
        <span style="font-size:10px;font-weight:700;color:#484f58;text-transform:uppercase;letter-spacing:.06em;white-space:nowrap;">Shortcuts</span>
        ${SHORTCUTS.map(s=>`<span style="display:flex;align-items:center;gap:5px;font-size:11px;color:#6e7681;">
          <kbd style="display:inline-flex;align-items:center;padding:2px 6px;background:#21262d;border:1px solid #30363d;border-radius:4px;font-size:10px;font-family:'JetBrains Mono',monospace;color:#8b949e;line-height:1.4;">${s.key}</kbd>
          <span>${s.desc}</span>
        </span>`).join('')}
      </div>

      <!-- Footer -->
      <div style="display:flex;align-items:center;justify-content:space-between;">
        <span style="font-size:11px;color:#484f58;">Layout can be changed anytime via presets.</span>
        <button class="nds-btn nds-btn--primary" id="ndsOnboardDismiss" style="padding:8px 18px;">Get Started</button>
      </div>
    </div>`;

  document.body.appendChild(ov);

  ov.querySelectorAll('[data-preset]').forEach(btn=>{
    btn.addEventListener('click',()=>{
      if(typeof nxApplyPreset==='function')nxApplyPreset(btn.dataset.preset);
      _dismissOnboard(ov);
    });
  });

  ov.querySelectorAll('[data-sample]').forEach(btn=>{
    btn.addEventListener('click',()=>{
      const inp=document.getElementById('taskInput')||document.getElementById('nxTaskInput')||document.querySelector('textarea[name="task"]');
      if(inp){inp.value=btn.dataset.sample;inp.focus();}
      _dismissOnboard(ov);
    });
  });

  const dismissBtn = document.getElementById('ndsOnboardDismiss');
  if(dismissBtn) dismissBtn.onclick=()=>_dismissOnboard(ov);
  ov.addEventListener('click',e=>{if(e.target===ov)_dismissOnboard(ov);});

  // Focus trap
  const focusable=ov.querySelectorAll('button,[href],[tabindex]:not([tabindex="-1"])');
  const first=focusable[0],last=focusable[focusable.length-1];
  ov.addEventListener('keydown',e=>{
    if(e.key==='Escape'){_dismissOnboard(ov);return;}
    if(e.key==='Tab'){if(e.shiftKey&&document.activeElement===first){e.preventDefault();last.focus();}else if(!e.shiftKey&&document.activeElement===last){e.preventDefault();first.focus();}}
  });
  setTimeout(()=>first&&first.focus(),120);
}

function _dismissOnboard(ov){
  try{localStorage.setItem(ONBOARD_KEY,'1');}catch(_){}
  ov.style.opacity='0';ov.style.transition='opacity 220ms ease';
  setTimeout(()=>ov.remove(),230);
}

window.NdsOnboard={
  show:_showOnboard,
  reset:()=>{try{localStorage.removeItem(ONBOARD_KEY);localStorage.removeItem('nx_onboarded_v1');}catch(_){}},
};

/* ═══ 3. NdsPerf — frontend performance budget governor ══════════════════ */
const BUDGET={
  maxDomNodes:3500,
  maxAnimations:12,
  maxObservers:8,
  logBatchMs:60,
  streamBatchMs:80,
  longTaskMs:50,
};
let _observerCount=0;
const _origMO=window.MutationObserver;
window.MutationObserver=function(cb){_observerCount++;const mo=new _origMO(cb);const origObserve=mo.observe.bind(mo);const origDisc=mo.disconnect.bind(mo);mo.disconnect=function(){_observerCount--;origDisc();};return mo;};
Object.assign(window.MutationObserver,_origMO);

let _lastDomCheck=0;
function _checkBudget(){
  const now=Date.now();
  if(now-_lastDomCheck<10000)return;
  _lastDomCheck=now;
  const n=document.querySelectorAll('*').length;
  if(n>BUDGET.maxDomNodes){console.warn(`[NDS Perf] DOM nodes: ${n} exceeds budget ${BUDGET.maxDomNodes}. Prune inactive panels.`);}
  if(_observerCount>BUDGET.maxObservers){console.warn(`[NDS Perf] MutationObservers: ${_observerCount} exceeds budget ${BUDGET.maxObservers}.`);}
}
setInterval(_checkBudget,15000);

window.NdsPerf={BUDGET,getObserverCount:()=>_observerCount,checkNow:_checkBudget};

/* ═══ Boot ══════════════════════════════════════════════════════════════ */
function _boot(){
  _showOnboard();
  // Label main content for skip link target
  const main=document.getElementById('nxMain')||document.querySelector('.nx-main');
  if(main&&!main.id)main.id='nxMainContent';
}

if(document.readyState==='loading'){
  document.addEventListener('DOMContentLoaded',_boot);
}else{
  setTimeout(_boot,0);
}

})();
