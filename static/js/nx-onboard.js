/**
 * nx-onboard.js — Nexora Design System: Onboarding + Governance Runtime
 * Modules: NdsToast (canonical) | NdsOnboard (first-run) | NdsPerf (budget)
 */
(function(){'use strict';

/* ═══ 1. NdsToast — canonical toast (replaces scattered toast impls) ═══ */
const _region=(()=>{let r=document.getElementById('ndsToastRegion');if(!r){r=document.createElement('div');r.id='ndsToastRegion';r.className='nds-toast-region';r.setAttribute('role','status');r.setAttribute('aria-live','polite');document.body.appendChild(r);}return r;});
const ICONS={ok:'✓',err:'✕',warn:'⚠',info:'ℹ'};
const DURS={ok:4000,err:7000,warn:5000,info:4000};
function ndsToast(msg,type='info',opts={}){
  const r=_region();
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

/* ═══ 2. NdsOnboard — first-run experience ══════════════════════════════ */
const ONBOARD_KEY='nx_onboarded_v1';
const PRESETS=[
  {id:'builder',icon:'🏗',name:'Builder',desc:'Code + Inspector. Best for app development.'},
  {id:'debug',  icon:'🐛',name:'Debug',  desc:'Full panels + logs. For debugging sessions.'},
  {id:'minimal',icon:'◻', name:'Minimal',desc:'Clean focus view. No distractions.'},
  {id:'research',icon:'🔬',name:'Research',desc:'Wide context panels for research tasks.'},
];
const SAMPLES=[
  'Build a Python REST API with FastAPI',
  'Create a React dashboard with charts',
  'Write a web scraper and save to CSV',
  'Debug my Python script and fix errors',
];

function _showOnboard(){
  if(localStorage.getItem(ONBOARD_KEY))return;
  const ov=document.createElement('div');
  ov.id='ndsOnboard';
  ov.innerHTML=`
    <div class="nds-onboard-card" role="dialog" aria-modal="true" aria-labelledby="ndsOnboardTitle">
      <div class="nds-onboard-hero">
        <div class="nds-onboard-logo">✦</div>
        <div class="nds-onboard-title" id="ndsOnboardTitle">Welcome to Nexora AI</div>
        <div class="nds-onboard-sub">An intelligent workspace for building, debugging, and shipping with AI. Choose your starting layout:</div>
      </div>
      <div class="nds-onboard-body">
        <div class="nds-onboard-presets" id="ndsOnboardPresets">
          ${PRESETS.map(p=>`<button class="nds-onboard-preset" data-preset="${p.id}" title="${p.desc}">
            <div class="nds-onboard-preset__icon">${p.icon}</div>
            <div class="nds-onboard-preset__name">${p.name}</div>
            <div class="nds-onboard-preset__desc">${p.desc}</div>
          </button>`).join('')}
        </div>
        <div style="font-size:10px;color:var(--nds-text-dim);text-transform:uppercase;letter-spacing:.08em;margin-bottom:8px">Or start with a sample task</div>
        <div style="display:flex;flex-direction:column;gap:4px" id="ndsOnboardSamples">
          ${SAMPLES.map(s=>`<button class="nds-btn nds-btn--secondary nds-btn--sm" data-sample="${s}" style="text-align:left;justify-content:flex-start;">${s}</button>`).join('')}
        </div>
      </div>
      <div class="nds-onboard-footer">
        <span style="font-size:11px;color:var(--nds-text-dim)">You can change layout anytime via presets in the tab bar.</span>
        <button class="nds-btn nds-btn--primary" id="ndsOnboardDismiss">Get Started →</button>
      </div>
    </div>`;
  document.body.appendChild(ov);

  // Wire preset buttons
  ov.querySelectorAll('[data-preset]').forEach(btn=>{
    btn.addEventListener('click',()=>{
      if(typeof nxApplyPreset==='function')nxApplyPreset(btn.dataset.preset);
      _dismissOnboard(ov);
    });
  });

  // Wire sample task buttons
  ov.querySelectorAll('[data-sample]').forEach(btn=>{
    btn.addEventListener('click',()=>{
      const inp=document.getElementById('taskInput')||document.getElementById('nxTaskInput')||document.querySelector('textarea[name="task"]');
      if(inp){inp.value=btn.dataset.sample;inp.focus();}
      _dismissOnboard(ov);
    });
  });

  document.getElementById('ndsOnboardDismiss').onclick=()=>_dismissOnboard(ov);
  ov.addEventListener('click',e=>{if(e.target===ov)_dismissOnboard(ov);});

  // Trap focus
  const focusable=ov.querySelectorAll('button,[href],[tabindex]:not([tabindex="-1"])');
  const first=focusable[0],last=focusable[focusable.length-1];
  ov.addEventListener('keydown',e=>{
    if(e.key==='Escape'){_dismissOnboard(ov);return;}
    if(e.key==='Tab'){if(e.shiftKey&&document.activeElement===first){e.preventDefault();last.focus();}else if(!e.shiftKey&&document.activeElement===last){e.preventDefault();first.focus();}}
  });
  setTimeout(()=>first&&first.focus(),100);
}

function _dismissOnboard(ov){
  try{localStorage.setItem(ONBOARD_KEY,'1');}catch(_){}
  ov.style.opacity='0';ov.style.transition='opacity 200ms';
  setTimeout(()=>ov.remove(),210);
}

window.NdsOnboard={show:_showOnboard,reset:()=>{try{localStorage.removeItem(ONBOARD_KEY);}catch(_){}},};

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
  // Inject skip link for accessibility
  if(!document.querySelector('.nds-skip-link')){
    const sk=document.createElement('a');
    sk.href='#nxMainContent';sk.className='nds-skip-link';sk.textContent='Skip to main content';
    document.body.insertBefore(sk,document.body.firstChild);
  }
  // Label main content for skip link target
  const main=document.getElementById('nxMain')||document.querySelector('.nx-main');
  if(main&&!main.id)main.id='nxMainContent';
  if(main)main.setAttribute('tabindex','-1');
}

if(Array.isArray(window.NX_BOOT_TASKS)){window.NX_BOOT_TASKS.push(_boot);}
else{document.addEventListener('DOMContentLoaded',_boot);}

})();
