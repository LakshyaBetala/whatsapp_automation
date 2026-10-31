"""The phone app's front end, kept out of the router so it can be read.

One file, no build step, no framework. It is served by app.routers.mobile as a
single HTML document and talks to /m/api/* with the shop's own token.

Layout rules this file keeps to, because a shopkeeper uses it one-handed while
standing in his shop:
  - four destinations, in a bottom bar within thumb reach, never a hamburger;
  - one job per screen, and the first screen answers "what do I do now";
  - money is tabular-nums and Indian-grouped, so lakhs read at a glance;
  - urgency is a stripe down the left of a row, not a rainbow of pills;
  - a party opens as a full page, never a dialog, because a dialog on a 360px
    screen puts its own buttons off the bottom edge;
  - every action says what happened, on the row it happened to.
"""

APP_HTML = r"""<!doctype html><html lang="en"><head>
<meta charset="utf-8"><title>ASVA</title>
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="theme-color" content="#f5f6f2" media="(prefers-color-scheme: light)">
<meta name="theme-color" content="#0e1411" media="(prefers-color-scheme: dark)">
<meta name="referrer" content="no-referrer">
<link rel="manifest" href="/m/manifest.webmanifest">
<link rel="apple-touch-icon" href="/logo.png">
<style>
 :root{
   --bg:#f5f6f2;--bg2:#eef1ea;--card:#ffffff;--ink:#132019;--ink2:#37473e;--muted:#6d7f74;
   --line:#e7ebe4;--line2:#eef1ea;--accent:#0a7d33;--accent2:#12a150;--accent-ink:#0a7d33;
   --accent-wash:#eaf6ee;
   --wa:#25d366;--wa-ink:#06331a;
   --crit:#c23b22;--crit-bg:#fdece8;--warn:#8a5a00;--warn-bg:#fbf3e0;--ok:#0a7d33;
   --shadow:0 1px 2px rgba(18,40,25,.04),0 10px 26px -12px rgba(18,40,25,.14);
   --shadow-sm:0 1px 2px rgba(18,40,25,.05);
   --r:18px;--r2:14px;--r3:10px;
   --tabh:62px;
 }
 @media (prefers-color-scheme:dark){:root{
   --bg:#0e1411;--bg2:#131a16;--card:#18201b;--ink:#e9f1eb;--ink2:#b6c6bc;--muted:#879a8f;
   --line:#26302a;--line2:#212a25;--accent:#46d67e;--accent2:#3fce76;--accent-ink:#9ff0bd;
   --accent-wash:#16271c;
   --wa:#25d366;--wa-ink:#04240f;
   --crit:#ff8a70;--crit-bg:#33201c;--warn:#e7b96b;--warn-bg:#2e2717;--ok:#46d67e;
   --shadow:0 1px 2px rgba(0,0,0,.3),0 12px 28px -14px rgba(0,0,0,.6);
   --shadow-sm:0 1px 2px rgba(0,0,0,.35);
 }}
 *{box-sizing:border-box;-webkit-tap-highlight-color:transparent}
 html,body{overflow-x:clip}
 body{margin:0;font-family:'SF Pro Display','Helvetica Neue',system-ui,-apple-system,'Segoe UI',Roboto,sans-serif;
   background:var(--bg);color:var(--ink);-webkit-font-smoothing:antialiased;
   text-rendering:optimizeLegibility;padding-bottom:calc(var(--tabh) + env(safe-area-inset-bottom))}
 body.noTabs{padding-bottom:28px}
 @media (prefers-reduced-motion:no-preference){
   .fade{animation:fade .34s cubic-bezier(.16,1,.3,1) both}
   .d1{animation-delay:.05s}.d2{animation-delay:.1s}
   @keyframes fade{from{opacity:0;transform:translateY(8px)}to{opacity:1;transform:none}}
 }
 .num{font-variant-numeric:tabular-nums;font-feature-settings:"tnum"}

 /* ── chrome ─────────────────────────────────────────────────────────── */
 header{position:sticky;top:0;z-index:30;display:flex;align-items:center;gap:11px;
   padding:calc(env(safe-area-inset-top) + 11px) 16px 11px;background:color-mix(in srgb,var(--bg) 88%,transparent);
   backdrop-filter:saturate(150%) blur(14px);border-bottom:1px solid var(--line)}
 header .mark{flex:none;width:32px;height:32px;display:grid;place-items:center;
   line-height:0}
 header .mark img,header .mark svg{display:block;width:32px;height:32px}
 .login .lm img,.login .lm svg{display:block;width:100%;height:100%}
 header .who{flex:1;min-width:0}
 header .n{font-weight:700;font-size:.97rem;letter-spacing:-.01em;white-space:nowrap;
   overflow:hidden;text-overflow:ellipsis}
 header .s{font-size:.73rem;color:var(--muted);margin-top:1px}
 header .back{flex:none;width:34px;height:34px;border:1px solid var(--line);background:var(--card);
   color:var(--ink);border-radius:999px;font-size:1.1rem;line-height:1;cursor:pointer}
 .wrap{padding:16px 16px 26px;max-width:620px;margin:0 auto}

 /* ── bottom bar ─────────────────────────────────────────────────────── */
 nav.tabs{position:fixed;left:0;right:0;bottom:0;z-index:40;display:grid;
   grid-template-columns:repeat(4,1fr);background:color-mix(in srgb,var(--bg) 92%,transparent);
   backdrop-filter:saturate(150%) blur(16px);border-top:1px solid var(--line);
   padding-bottom:env(safe-area-inset-bottom)}
 nav.tabs button{appearance:none;border:0;background:none;padding:9px 2px 8px;cursor:pointer;
   display:flex;flex-direction:column;align-items:center;gap:3px;color:var(--muted);
   font:inherit;font-size:.68rem;font-weight:600;letter-spacing:.01em;min-height:var(--tabh)}
 nav.tabs button svg{width:22px;height:22px}
 nav.tabs button.on{color:var(--accent-ink)}
 nav.tabs .dot{position:absolute;margin:-2px 0 0 15px;width:7px;height:7px;border-radius:999px;
   background:var(--crit)}

 /* ── blocks ─────────────────────────────────────────────────────────── */
 .lede{font-size:1.32rem;line-height:1.26;letter-spacing:-.022em;font-weight:700;margin:2px 0 4px}
 .leded{color:var(--muted);font-size:.9rem;line-height:1.5}
 .sect{display:flex;align-items:baseline;gap:8px;margin:24px 2px 9px;
   font-size:.72rem;font-weight:700;letter-spacing:.09em;text-transform:uppercase;color:var(--muted)}
 .sect .cnt{margin-left:auto;letter-spacing:0;text-transform:none;font-weight:600;font-size:.76rem}
 .card{background:var(--card);border:1px solid var(--line);border-radius:var(--r);
   padding:16px;box-shadow:var(--shadow-sm)}
 .card+.card{margin-top:10px}

 .big{background:var(--card);border:1px solid var(--line);border-radius:var(--r);
   padding:18px;box-shadow:var(--shadow)}
 .big .l{font-size:.72rem;font-weight:700;letter-spacing:.09em;text-transform:uppercase;color:var(--muted)}
 .big .v{font-size:2.05rem;font-weight:700;letter-spacing:-.03em;margin-top:6px;line-height:1}
 .big .s{color:var(--muted);font-size:.84rem;margin-top:7px}
 .duo{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-top:10px}
 .duo .big{padding:14px}
 .duo .big .v{font-size:1.32rem;margin-top:4px}

 /* act card: the one thing to do, in the accent wash. Used at most once. */
 .act{background:var(--accent-wash);border:1px solid color-mix(in srgb,var(--accent) 24%,transparent);
   border-radius:var(--r);padding:17px}
 .act h3{margin:0 0 6px;font-size:1.02rem;letter-spacing:-.015em}
 .act p{margin:0;color:var(--ink2);font-size:.88rem;line-height:1.52}

 /* ── rows ───────────────────────────────────────────────────────────── */
 .rows{background:var(--card);border:1px solid var(--line);border-radius:var(--r);overflow:hidden;
   box-shadow:var(--shadow-sm)}
 .row{display:flex;align-items:center;gap:12px;padding:13px 14px;cursor:pointer;
   border-top:1px solid var(--line2);position:relative;background:none;border-left:0;
   border-right:0;border-bottom:0;width:100%;text-align:left;font:inherit;color:inherit}
 .row:first-child{border-top:0}
 .row:active{background:var(--bg2)}
 .row .stripe{position:absolute;left:0;top:0;bottom:0;width:3px;background:transparent}
 .row.sv2 .stripe{background:var(--crit)}
 .row.sv1 .stripe{background:var(--warn)}
 .row .nm{flex:1;min-width:0}
 .row .nm b{display:block;font-weight:650;font-size:.95rem;letter-spacing:-.01em;
   overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
 .row .nm span{display:block;font-size:.76rem;color:var(--muted);margin-top:2px}
 .row .amt{flex:none;font-weight:700;font-size:.95rem;letter-spacing:-.01em}
 .row .chev{flex:none;color:var(--muted);font-size:1.15rem;line-height:1;opacity:.6}
 .row .mini{flex:none;width:34px;height:34px;border-radius:999px;display:grid;place-items:center;
   background:var(--bg2);color:var(--ink2);border:1px solid var(--line)}
 .row .mini svg{width:17px;height:17px}
 .rk{flex:none;width:22px;text-align:center;font-weight:700;font-size:.8rem;color:var(--muted)}
 .trow{display:block;padding:12px 14px 11px;border-top:1px solid var(--line2);position:relative;
   cursor:pointer}
 .trow:first-child{border-top:0}
 .trow:active{background:var(--bg2)}
 .trow.sv2 .stripe{background:var(--crit)}
 .trow.sv1 .stripe{background:var(--warn)}
 .thead{display:flex;align-items:center;gap:12px}
 /* .row .nm rules do not reach here, and without these the party name and the
    "312 days late" line render as one run-on string. */
 .trow .nm{flex:1;min-width:0}
 .trow .nm b{display:block;font-weight:650;font-size:.95rem;letter-spacing:-.01em;
   overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
 /* direct child only: the reliability dot is a span INSIDE <b>, and a blanket
    span{display:block} here knocked it onto its own line above the name. */
 .trow .nm > span{display:block;font-size:.76rem;color:var(--muted);margin-top:2px}
 .trow .amt{flex:none;font-weight:700;font-size:.95rem;letter-spacing:-.01em}
 .gd{display:inline-block;width:7px;height:7px;border-radius:999px;margin-right:7px;
   vertical-align:middle}
 .ract{display:flex;gap:7px;margin:10px 0 0 34px;flex-wrap:wrap}
 .qa{appearance:none;display:inline-flex;align-items:center;justify-content:center;gap:6px;
   font:inherit;font-size:.82rem;font-weight:650;padding:8px 12px;border-radius:9px;
   border:1px solid var(--line);background:var(--bg2);color:var(--ink2);cursor:pointer;
   text-decoration:none;min-height:38px;white-space:nowrap}
 .qa svg{width:15px;height:15px}
 .qa:active{transform:scale(.97)}
 .qa[disabled]{opacity:.55;pointer-events:none}
 .qa-pri{background:var(--accent);border-color:var(--accent);color:#fff}
 /* 320px phones are still common in this trade. Shrink the action strip
    rather than let it wrap, so Check never drops onto its own line. */
 @media(max-width:379px){
   .ract{margin-left:0;gap:6px}
   .qa{font-size:.78rem;padding:7px 9px}
   .qa svg{width:14px;height:14px}
   .big .l{letter-spacing:.06em}
 }

 /* ── settings rows ──────────────────────────────────────────────────── */
 .srow{display:flex;align-items:center;gap:12px;padding:14px;border-top:1px solid var(--line2)}
 .srow:first-child{border-top:0}
 .srow .sl{flex:1;min-width:0}
 .srow .sl b{display:block;font-weight:600;font-size:.92rem}
 .srow .sl span{display:block;font-size:.77rem;color:var(--muted);margin-top:2px;line-height:1.45}
 .sw{flex:none;width:50px;height:30px;border-radius:999px;border:1px solid var(--line);
   background:var(--bg2);position:relative;cursor:pointer;transition:background .18s}
 .sw::after{content:'';position:absolute;top:3px;left:3px;width:22px;height:22px;border-radius:999px;
   background:#fff;box-shadow:0 1px 3px rgba(0,0,0,.22);transition:transform .18s}
 .sw.on{background:var(--accent);border-color:var(--accent)}
 .sw.on::after{transform:translateX(20px)}

 /* ── controls ───────────────────────────────────────────────────────── */
 .btn{appearance:none;display:inline-flex;align-items:center;justify-content:center;gap:8px;
   font:inherit;font-weight:650;font-size:.93rem;padding:13px 16px;border-radius:var(--r2);
   border:1px solid var(--line);background:var(--card);color:var(--ink);cursor:pointer;
   text-decoration:none;min-height:48px}
 .btn:active{transform:scale(.985)}
 .btn[disabled]{opacity:.55;pointer-events:none}
 .btn svg{width:18px;height:18px}
 .btn-pri{background:var(--accent);border-color:var(--accent);color:#fff}
 .btn-wa{background:var(--wa);border-color:var(--wa);color:var(--wa-ink)}
 .btn-dang{color:var(--crit);border-color:color-mix(in srgb,var(--crit) 35%,transparent)}
 .btn-full{width:100%}
 .acts{display:flex;gap:9px;flex-wrap:wrap}
 .acts .btn{flex:1 1 0;min-width:132px}
 input,select{font:inherit;width:100%;padding:12px 13px;border:1px solid var(--line);
   border-radius:var(--r2);background:var(--card);color:var(--ink);min-height:48px}
 input:focus-visible,select:focus-visible,.btn:focus-visible,.row:focus-visible,
 nav.tabs button:focus-visible,.sw:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
 label.fl{display:block;font-size:.78rem;font-weight:600;color:var(--muted);margin:0 0 6px 2px}
 .fgrid{display:grid;gap:12px}

 .find{display:flex;gap:9px;margin-bottom:11px}
 .find input{flex:1}
 .find select{flex:none;width:auto;min-width:118px}
 .chips{display:flex;gap:7px;overflow-x:auto;padding:0 0 4px;margin:0 0 11px;
   scrollbar-width:none;-ms-overflow-style:none}
 .chips::-webkit-scrollbar{display:none}
 .chips::after{content:'';flex:none;width:4px}
 .chip{flex:none;appearance:none;font:inherit;font-size:.82rem;font-weight:600;padding:8px 13px;
   border-radius:999px;border:1px solid var(--line);background:var(--card);color:var(--ink2);
   cursor:pointer;white-space:nowrap;min-height:38px}
 .chip.on{background:var(--ink);border-color:var(--ink);color:var(--bg)}

 .pill{display:inline-block;font-size:.72rem;font-weight:700;padding:4px 9px;border-radius:999px;
   letter-spacing:.02em}
 .pill-on{background:var(--accent-wash);color:var(--accent-ink)}
 .pill-off{background:var(--bg2);color:var(--muted)}
 .pill-crit{background:var(--crit-bg);color:var(--crit)}
 .pill-warn{background:var(--warn-bg);color:var(--warn)}

 .bill{display:flex;align-items:center;gap:10px;padding:11px 0;border-top:1px solid var(--line2);
   font-size:.9rem}
 .bill:first-child{border-top:0}
 .bill .inv{flex:1;min-width:0;color:var(--ink2);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
 .bill .od{margin-left:7px;font-size:.72rem;font-weight:700;color:var(--crit)}
 .bill .amt{font-weight:650}
 .letter{white-space:pre-wrap;font-size:.9rem;line-height:1.62;color:var(--ink2);overflow-wrap:anywhere}
 .msg{padding:11px 0;border-top:1px solid var(--line2);font-size:.9rem;color:var(--ink2);line-height:1.5}
 .msg:first-child{border-top:0}
 .msg span{display:block;font-size:.73rem;color:var(--muted);margin-top:3px}

 .empty{text-align:center;padding:40px 18px;color:var(--muted)}
 .empty .em{width:34px;height:34px;margin:0 auto 12px;border:1.5px solid var(--line);
   border-radius:999px;display:grid;place-items:center}
 .empty .em svg{width:17px;height:17px}
 .empty b{display:block;color:var(--ink);font-size:1rem;margin-bottom:5px}
 .err{background:var(--crit-bg);color:var(--crit);border-radius:var(--r);padding:16px;
   text-align:center;line-height:1.55}
 .shim{background:linear-gradient(100deg,var(--bg2) 20%,var(--line2) 42%,var(--bg2) 64%);
   background-size:220% 100%;border-radius:var(--r3);animation:sh 1.3s linear infinite}
 @keyframes sh{to{background-position:-120% 0}}
 .pager{display:flex;align-items:center;gap:10px;justify-content:center;margin-top:14px}
 .pinfo{color:var(--muted);font-size:.82rem;text-align:center;margin-top:12px}
 .foot{color:var(--muted);font-size:.76rem;text-align:center;line-height:1.6;margin:24px 0 4px}
 .foot a{color:var(--accent-ink)}

 #tst{position:fixed;left:50%;transform:translateX(-50%);bottom:calc(var(--tabh) + 18px);
   z-index:90;background:var(--ink);color:var(--bg);padding:11px 17px;border-radius:999px;
   font-size:.88rem;font-weight:600;box-shadow:var(--shadow);max-width:88vw;text-align:center}
 .install{display:flex;align-items:center;gap:11px;background:var(--card);border:1px solid var(--line);
   border-radius:var(--r);padding:13px 14px;margin-bottom:14px;box-shadow:var(--shadow-sm)}
 .install .it{flex:1;font-size:.86rem;line-height:1.45;color:var(--ink2)}
 .install .x{appearance:none;border:0;background:none;color:var(--muted);font-size:1.25rem;
   cursor:pointer;padding:0 2px;min-height:40px;min-width:32px}

 .login{min-height:76vh;display:flex;flex-direction:column;justify-content:center;
   padding:28px 20px;max-width:420px;margin:0 auto}
 .login .lm{width:50px;height:50px;margin-bottom:20px}
 .login h1{font-size:1.5rem;letter-spacing:-.025em;margin:0 0 7px}
 .login p{color:var(--muted);margin:0 0 20px;line-height:1.55;font-size:.92rem}
</style></head><body>
<div id="app"></div>
<script>
// ── token ────────────────────────────────────────────────────────────────
// A token in the address bar ends up in history and in any Referer header, so
// it is stored once and scrubbed out of the URL immediately.
var TOKEN = '';
try {
  var qs = new URLSearchParams(location.search);
  var qt = (qs.get('token') || '').trim();
  if (qt) {
    localStorage.setItem('asva_m_token', qt);
    history.replaceState(null, '', location.pathname);
  }
  TOKEN = localStorage.getItem('asva_m_token') || '';
} catch (e) { TOKEN = ''; }

var app = document.getElementById('app');
var S = {sum:null, today:null, money:null, picks:null, inbox:null, cp:null};
var TAB = 'today';
var INSTALL_EVT = null;
window.addEventListener('beforeinstallprompt', function(e){ e.preventDefault(); INSTALL_EVT = e; });

// ── helpers ──────────────────────────────────────────────────────────────
function esc(s){return String(s==null?'':s).replace(/[&<>"]/g,function(m){
  return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[m];});}
function jq(v){return JSON.stringify(String(v==null?'':v)).replace(/"/g,'&quot;');}
function digits(w){return String(w||'').replace(/[^0-9]/g,'');}
function waNum(w){var t=digits(w);if(t.length===10)t='91'+t;return t.length>=11?t:'';}
function telNum(w){var t=digits(w);return t.length>=10?('+'+(t.length===10?'91':'')+t):'';}
function rupee(v){return '&#8377;'+esc(v);}
var MON = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
function dmy(iso){
  var m = /^(\d{4})-(\d{2})-(\d{2})/.exec(String(iso||''));
  if (!m) return String(iso||'');
  return String(+m[3]) + ' ' + MON[+m[2]-1];
}

var I = {
  today:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M3 12h4l2.5 6 5-13L17 12h4"/></svg>',
  people:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><circle cx="9" cy="8" r="3.2"/><path d="M3 20c0-3.3 2.7-5.5 6-5.5s6 2.2 6 5.5"/><path d="M16 5.5a3.2 3.2 0 0 1 0 5"/><path d="M18 14.8c2 .7 3 2.6 3 5.2"/></svg>',
  money:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M7 6h8M7 10h8M13 6c2.8 0 2.8 4 0 4H8l6 8"/></svg>',
  gear:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="3"/><path d="M12 3v2.2M12 18.8V21M4.2 7.5l1.9 1.1M17.9 15.4l1.9 1.1M4.2 16.5l1.9-1.1M17.9 8.6l1.9-1.1"/></svg>',
  wa:'<svg viewBox="0 0 24 24" fill="currentColor"><path d="M12.04 2C6.6 2 2.2 6.4 2.2 11.84c0 1.74.46 3.42 1.32 4.9L2 22l5.4-1.42a9.8 9.8 0 0 0 4.64 1.18c5.44 0 9.84-4.4 9.84-9.84S17.48 2 12.04 2m5.72 13.9c-.24.68-1.4 1.3-1.94 1.34-.54.06-1.02.24-3.48-.72s-4-3.52-4.16-3.68c-.16-.18-1-1.32-1-2.52s.62-1.78.84-2.02c.22-.26.48-.3.64-.3h.46c.16 0 .36-.04.56.42.2.5.7 1.7.76 1.82.06.12.1.26.02.42-.08.16-.16.26-.3.42l-.26.3c-.1.1-.2.2-.1.4.1.18.5.84 1.08 1.36.74.66 1.26.86 1.44.96.18.1.3.08.4-.04.12-.14.5-.58.64-.78.12-.2.26-.16.42-.1.18.06 1.1.52 1.3.62.2.1.32.14.36.22.04.1.04.56-.18 1.22"/></svg>',
  call:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M5 3.5h3l1.6 4-2 1.4a11.5 11.5 0 0 0 5.5 5.5l1.4-2 4 1.6v3c0 .8-.7 1.5-1.5 1.5A15.5 15.5 0 0 1 3.5 5C3.5 4.2 4.2 3.5 5 3.5"/></svg>',
  bell:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M18 15.5V10a6 6 0 1 0-12 0v5.5L4.5 18h15z"/><path d="M9.5 21h5"/></svg>',
  tick:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.1" stroke-linecap="round" stroke-linejoin="round"><path d="M4.5 12.5l5 5 10-11"/></svg>',
  find:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"><circle cx="11" cy="11" r="6.2"/><path d="M15.6 15.6 21 21"/></svg>',
  plus:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M12 5v14M5 12h14"/></svg>',
  cam:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M3 8.5h3.2l1.4-2h8.8l1.4 2H21v10H3z"/><circle cx="12" cy="13" r="3.2"/></svg>'
};
// The real ASVA mark, served by the site router on this same host. What was
// here before was a hand-drawn green chevron that resembled nothing.
var MARK = '<img src="/logo.png" alt="ASVA" width="64" height="64"'
 + ' style="width:100%;height:100%;object-fit:contain;display:block">';

// ── net ──────────────────────────────────────────────────────────────────
async function api(path){
  var r = await fetch('/m/api/' + path + (path.indexOf('?')<0?'?':'&')
                      + 'token=' + encodeURIComponent(TOKEN), {cache:'no-store'});
  if (r.status === 401) { logout(); throw new Error('unauthorised'); }
  if (!r.ok) throw new Error('http ' + r.status);
  return r.json();
}
async function post(path, body){
  body = body || {}; body.token = TOKEN;
  var r = await fetch('/m/api/' + path, {method:'POST',
    headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)});
  if (r.status === 401) { logout(); throw new Error('unauthorised'); }
  var j = null; try { j = await r.json(); } catch(e){}
  if (!r.ok) throw new Error((j && (j.detail || j.error)) || ('http ' + r.status));
  return j || {};
}
function toast(m){
  var t = document.getElementById('tst');
  if (!t) { t = document.createElement('div'); t.id = 'tst'; document.body.appendChild(t); }
  t.innerHTML = esc(m);
  clearTimeout(t._h); t._h = setTimeout(function(){ if(t) t.remove(); }, 2600);
}
function busy(btn, label){
  if (!btn) return function(){};
  var old = btn.innerHTML; btn.disabled = true; btn.innerHTML = esc(label || 'Working...');
  return function(){ btn.disabled = false; btn.innerHTML = old; };
}

// ── chrome ───────────────────────────────────────────────────────────────
function header(title, sub, back){
  return '<header>'
   + (back ? '<button class="back" onclick="' + esc(back) + '" aria-label="Back">&#8592;</button>'
           : '<div class="mark">' + MARK + '</div>')
   + '<div class="who"><div class="n">' + esc(title) + '</div>'
   + '<div class="s">' + esc(sub || '') + '</div></div></header>';
}
function tabs(){
  var items = [['today','Today',I.today], ['parties','Parties',I.people],
               ['money','Money',I.money], ['setup','Setup',I.gear]];
  var needs = S.sum && S.sum.stage === 'pick';
  return '<nav class="tabs">' + items.map(function(it){
    var dot = (it[0] === 'setup' && needs) ? '<span class="dot"></span>' : '';
    return '<button class="' + (TAB === it[0] ? 'on' : '') + '" onclick="go(' + jq(it[0]) + ')">'
      + it[2] + dot + '<span>' + it[1] + '</span></button>';
  }).join('') + '</nav>';
}
function shell(title, sub, inner){
  document.body.classList.remove('noTabs');
  app.innerHTML = header(title, sub, '') + '<div class="wrap">' + inner + '</div>' + tabs();
}
function skeleton(){
  var b = function(w,h){ return '<div class="shim" style="height:'+h+'px;width:'+w+'"></div>'; };
  var rows = '';
  for (var i=0;i<5;i++) rows += '<div style="display:flex;gap:12px;align-items:center;padding:13px 2px">'
    + b('58%',13) + '<div style="flex:1"></div>' + b('62px',13) + '</div>';
  return b('100%',96) + '<div style="height:14px"></div>' + rows;
}
function errBox(retry){
  return '<div class="err">Could not reach ASVA.<br>Check your internet.'
   + '<div style="margin-top:12px"><button class="btn" onclick="' + esc(retry)
   + '">Try again</button></div></div>';
}
function emptyBox(icon, head, sub){
  return '<div class="empty"><div class="em">' + icon + '</div><b>' + esc(head) + '</b>'
   + esc(sub || '') + '</div>';
}

// ── router ───────────────────────────────────────────────────────────────
function go(tab){
  TAB = tab; window.scrollTo(0,0);
  if (tab === 'today')   return renderToday();
  if (tab === 'parties') return renderParties();
  if (tab === 'money')   return renderMoney();
  return renderSetup();
}
function shopName(){
  return (S.today && S.today.business) || (S.sum && S.sum.business_name) || 'ASVA';
}
function shopSub(){
  var d = S.today || S.sum;
  if (!d) return '';
  var t = d.tally_synced;
  var on = (d.reminders_on_count !== undefined) ? d.reminders_on_count : d.reminders_on;
  if (on === undefined) return '';
  return 'Tally ' + (t === 'never' ? 'not synced yet' : t)
       + ' · chasing ' + on + ' part' + (on === 1 ? 'y' : 'ies');
}

async function boot(){
  if (!TOKEN) return loginView();
  shell('ASVA', '', skeleton());
  try { S.sum = await api('summary'); }
  catch (e) { if (String(e.message) !== 'unauthorised')
                shell('ASVA','', errBox('boot()')); return; }
  go(TAB);
}

// ── 1. TODAY ─────────────────────────────────────────────────────────────
function severity(days, paused){
  if (paused) return ['', 'on promise'];
  if (days >= 60) return ['sv2', days + ' days late'];
  if (days >= 15) return ['sv1', days + ' days late'];
  if (days > 0)   return ['', days + ' day' + (days>1?'s':'') + ' late'];
  return ['', 'not due yet'];
}
function actCard(){
  var d = S.today || S.sum;
  if (!d) return '';
  if (d.stage === 'pick') {
    return '<div class="act fade"><h3>ASVA is not reminding anybody yet</h3>'
     + '<p>It never chases a customer you have not picked. Start with the ten who '
     + 'owe you the most, for the longest.</p>'
     + '<div style="margin-top:13px"><button class="btn btn-pri" onclick="pickView(10)">'
     + 'Pick my ten</button></div></div>';
  }
  if (d.stage === 'review') {
    return '<div class="act fade"><h3>Your ' + d.stage_on + ' picked customers have had two weeks</h3>'
     + '<p>Open Money to see what they paid. If you are happy with it, point ASVA at '
     + 'the next fifty.</p>'
     + '<div style="margin-top:13px;display:flex;gap:9px;flex-wrap:wrap">'
     + '<button class="btn btn-pri" onclick="pickView(50)">Add the next 50</button>'
     + '<button class="btn" onclick="go(\'money\')">See what they paid</button>'
     + '</div></div>';
  }
  return '';
}
function inr(n){
  // Indian grouping, done here because the morning brief hands over raw floats.
  var x = Math.round(Number(n) || 0), s = String(Math.abs(x)), out;
  if (s.length <= 3) out = s;
  else { var last3 = s.slice(-3), rest = s.slice(0, -3);
         out = rest.replace(/\B(?=(\d{2})+(?!\d))/g, ',') + ',' + last3; }
  return (x < 0 ? '-' : '') + out;
}
function gradeDot(p){
  if (!p.grade) return '';
  return '<span class="gd" title="' + esc(p.grade_label || '') + '" style="background:'
   + esc(p.grade_color || 'var(--muted)') + '"></span>';
}
async function renderToday(){
  if (!S.today) {
    shell(shopName(), shopSub(), skeleton());
    try { S.today = await api('today'); }
    catch (e) { if (String(e.message) !== 'unauthorised')
                  shell(shopName(), shopSub(), errBox('renderToday()')); return; }
    try { S.cp = await api('checkpoint'); } catch (e) { S.cp = {}; }
  }
  var d = S.today;
  var act = actCard();
  var chase = d.chase || [];

  // The brief's own opening line, so the app and the 8am message agree.
  var hello = '<div class="lede">' + esc(d.greeting || 'Today') + '</div>'
   + '<div class="leded">' + esc(d.date_label || '') + '</div>';

  var mi = d.money_in || {today:{amount:0,count:0}, yesterday:{amount:0,count:0}};
  var money = '<div class="duo" style="margin-top:14px">'
   + '<div class="big fade"><div class="l">Came in</div><div class="v num">'
   + rupee(inr(mi.today.amount)) + '</div><div class="s">'
   + (mi.today.count ? mi.today.count + ' receipt' + (mi.today.count>1?'s':'')
                     : 'nothing yet') + '</div></div>'
   + '<div class="big fade"><div class="l">Still out</div><div class="v num">'
   + rupee(inr((d.outstanding||{}).total)) + '</div><div class="s">'
   + ((d.outstanding||{}).party_count || 0) + ' parties</div></div></div>';

  // Needs a decision: the handful of things only the owner can settle. These
  // were the bot messages he used to get and ignore.
  var dec = (d.decisions || []).length
    ? '<div class="sect">Needs you<span class="cnt">' + d.decisions.length + '</span></div>'
      + '<div class="rows fade d1">' + d.decisions.map(function(x){
          var go_ = x.action === 'numbers' ? "setPF('nonum');go('parties')"
                  : (x.client_id ? 'partyView(' + jq(x.client_id) + ')' : "go('money')");
          return '<button class="row sv1" onclick="' + esc(go_) + '">'
           + '<span class="stripe"></span>'
           + '<span class="nm"><b>' + esc(x.display) + '</b><span>' + esc(x.text)
           + '</span></span><span class="chev">&#8250;</span></button>'; }).join('')
      + '</div>'
    : '';

  var list = chase.length
    ? '<div class="rows fade d1">' + chase.map(function(p, i){
        var sv = severity(p.days_late, false);
        var why = !p.has_number ? sv[1] + ' · no number'
                : (p.reminders_on === false ? sv[1] + ' · not chased' : sv[1]);
        var w = waNum(p.phone);
        var acts = '<span class="ract">'
         + (p.has_number
             ? '<button class="qa qa-pri" onclick="event.stopPropagation();rowRemind('
               + jq(p.client_id) + ',' + jq(p.name) + ',this)">' + I.bell + ' Remind</button>'
               + '<a class="qa" target="_blank" rel="noopener" onclick="event.stopPropagation()"'
               + ' href="https://wa.me/' + esc(w) + '">' + I.wa + '</a>'
             : '<button class="qa qa-pri" onclick="event.stopPropagation();partyView('
               + jq(p.client_id) + ')">' + I.plus + ' Add number</button>')
         + '<button class="qa" onclick="event.stopPropagation();rowStop(' + jq(p.client_id)
         + ',' + jq(p.display) + ',' + (p.reminders_on ? 'true' : 'false') + ',this)">'
         + (p.reminders_on ? 'Stop' : 'Start') + '</button>'
         + '<button class="qa" onclick="event.stopPropagation();partyView('
         + jq(p.client_id) + ')">Check</button></span>';
        return '<div class="trow ' + sv[0] + '" onclick="partyView(' + jq(p.client_id) + ')">'
         + '<span class="stripe"></span>'
         + '<span class="thead"><span class="rk num">' + (i+1) + '</span>'
         + '<span class="nm"><b>' + gradeDot(p) + esc(p.display) + '</b><span>'
         + esc(why) + '</span></span>'
         + '<span class="amt num">' + rupee(inr(p.amount)) + '</span></span>'
         + acts + '</div>'; }).join('') + '</div>'
    : emptyBox(I.tick, 'Nobody to chase today',
               'Every overdue party has been handled.');

  var waDown = wa().state === 'down'
    ? '<div class="act fade" style="background:var(--crit-bg);border-color:'
      + 'color-mix(in srgb,var(--crit) 30%,transparent);margin-bottom:14px">'
      + '<h3 style="color:var(--crit)">Your WhatsApp is not connected</h3>'
      + '<p>Nothing is going out to any customer right now. Open ASVA on the '
      + 'shop computer and scan the code again. Everything below is waiting, '
      + 'not lost.</p></div>'
    : '';

  // Today's list, before it goes out. Holding a party who has already paid
  // is a thirty second job that stops an embarrassing message, and it used to
  // mean replying HOLD to a chat thread.
  var cp = S.cp || {};
  var checkpoint = (cp.open && (cp.items || []).length)
    ? '<div class="sect">Going out today<span class="cnt">'
      + cp.items.length + (cp.held ? ' \u00b7 ' + cp.held + ' held' : '') + '</span></div>'
      + '<div class="rows fade d1">' + cp.items.map(function(it){
          return '<div class="row" style="cursor:default">'
           + '<span class="nm"><b>' + esc(it.name) + '</b><span>'
           + (it.held ? 'held, nothing goes to them today' : 'will be reminded today')
           + '</span></span><span class="amt num">' + rupee(it.amount) + '</span>'
           + (it.held ? '<span class="pill pill-off">held</span>'
               : '<button class="qa" onclick="doHold(' + jq(it.client_id) + ',this)">'
                 + 'Hold</button>') + '</div>'; }).join('') + '</div>'
      + (cp.held < cp.items.length
          ? '<div style="margin-top:10px"><button class="btn btn-full"'
            + ' onclick="doHoldAll(this)">They have all paid, hold everyone</button></div>'
          : '')
    : '';

  shell(shopName(), shopSub(),
    installBar() + waDown + hello + money
    + (act ? '<div style="margin-top:14px">' + act + '</div>' : '')
    + dec + checkpoint
    + '<div class="sect">Chase today<span class="cnt">'
    + (d.chase_overdue_count || chase.length) + ' overdue</span></div>'
    + list
    + ((d.chase_overdue_count || 0) > chase.length
        ? '<div style="margin-top:12px"><button class="btn btn-full" onclick="go(\'parties\')">'
          + 'See all parties</button></div>' : '')
    + '<div class="foot">'
    + (d.sent_today ? '<b>' + d.sent_today + '</b> reminder'
        + (d.sent_today === 1 ? '' : 's') + ' went out today.<br>' : '')
    + 'This is the same list as your morning message.<br>'
    + 'ASVA sends from your own WhatsApp number.</div>');
}
async function doHold(cid, btn){
  var done = busy(btn, '...');
  try { await post('hold', {client_id: cid});
        toast('Held for today'); S.cp = null; S.today = null; renderToday(); }
  catch (e) { toast('Could not hold: ' + e.message); done(); }
}
async function doHoldAll(btn){
  var done = busy(btn, 'Holding...');
  try { var r = await post('hold', {all: true});
        toast('Held ' + (r.held || 0) + ' for today');
        S.cp = null; S.today = null; renderToday(); }
  catch (e) { toast('Could not hold: ' + e.message); done(); }
}
async function rowRemind(cid, name, btn){
  var done = busy(btn, 'Sending');
  try { await post('remind', {party: name}); toast('Reminder queued'); btn.innerHTML = 'Sent'; }
  catch (e) { toast('Could not send: ' + e.message); done(); }
}
async function rowStop(cid, name, isOn, btn){
  var done = busy(btn, '...');
  try { await post('reminders', {client_id: cid, on: !isOn});
        toast(isOn ? name + ' will not be chased' : 'ASVA will chase ' + name);
        S.today = null; S.sum = null; await refresh(); renderToday(); }
  catch (e) { toast('Could not save: ' + e.message); done(); }
}

// ── 2. PARTIES ───────────────────────────────────────────────────────────
var PQ = '', PF = 'owing', PSORT = 'overdue', PPAGE = 0, PER = 25;
function renderParties(){
  shell(shopName(), shopSub(),
    '<button class="btn btn-full fade" style="margin-bottom:12px" onclick="newPartyView()">'
    + I.plus + ' Add a party</button>'
    + '<div class="chips" id="pchips"></div>'
    + '<div class="find"><input id="q" placeholder="Search a party" autocomplete="off"'
    + ' value="' + esc(PQ) + '">'
    + '<select id="srt"><option value="overdue">Most late</option>'
    + '<option value="amount">Biggest</option><option value="name">A to Z</option></select></div>'
    + '<div id="plist"></div><div id="ppage"></div>');
  var chips = [['owing','Owing'],['chasing','ASVA chasing'],['promise','On promise'],
               ['off','Not chased'],['nonum','No number'],['all','All']];
  document.getElementById('pchips').innerHTML = chips.map(function(c){
    return '<button class="chip ' + (PF===c[0]?'on':'') + '" onclick="setPF(' + jq(c[0]) + ')">'
      + c[1] + '</button>'; }).join('');
  var q = document.getElementById('q');
  q.oninput = function(){ PQ = q.value; PPAGE = 0; paintParties(); };
  var sr = document.getElementById('srt'); sr.value = PSORT;
  sr.onchange = function(){ PSORT = sr.value; PPAGE = 0; paintParties(); };
  paintParties();
}
function setPF(f){ PF = f; PPAGE = 0; if (TAB === 'parties') renderParties(); }

// A party who is not in Tally yet: a cash customer, a new buyer, somebody the
// owner just agreed terms with standing in the shop. It was desktop-only, and
// the owner is never at the desktop when it happens.
function newPartyView(){
  document.body.classList.add('noTabs');
  app.innerHTML = header('Add a party', '', "go('parties')")
   + '<div class="wrap"><div class="card fade"><div class="fgrid">'
   + '<div><label class="fl" for="npname">Party name</label>'
   + '<input id="npname" placeholder="As you would write it in Tally"></div>'
   + '<div><label class="fl" for="npnum">Their WhatsApp number</label>'
   + '<input id="npnum" type="tel" inputmode="numeric" placeholder="9876543210"></div>'
   + '<div><label class="fl" for="npcd">Days to pay</label>'
   + '<input id="npcd" type="number" inputmode="numeric" value="30" min="0" max="365">'
   + '</div></div>'
   + '<div style="margin-top:16px"><button class="btn btn-pri btn-full" id="npsave"'
   + ' onclick="newPartySave(this)">Add this party</button></div></div>'
   + '<div class="foot">ASVA will not chase them until you switch their reminders '
   + 'on. Bills you add to them work straight away.</div></div>';
  var f = document.getElementById('npname'); if (f) f.focus();
}
async function newPartySave(btn){
  var nm = fval('npname');
  if (!nm) return toast('Enter the party name.');
  var num = digits(fval('npnum'));
  if (num && num.length < 10) return toast('That number is too short.');
  var cd = parseInt(fval('npcd'), 10);
  var done = busy(btn, 'Adding...');
  try {
    var r = await post('party', {name: nm, whatsapp_number: num,
                                 credit_days: (cd >= 0 && cd <= 365) ? cd : 30});
    toast('Added ' + nm);
    await refresh();
    if (r && r.client_id) { TAB = 'parties'; partyView(r.client_id); }
    else go('parties');
  } catch (e) { toast('Could not add: ' + e.message); done(); }
}
function partyPool(){
  var all = S.sum.chase || [];
  if (PF === 'chasing') return all.filter(function(p){ return p.reminders_on && !p.paused; });
  if (PF === 'promise') return all.filter(function(p){ return p.paused; });
  if (PF === 'off')     return all.filter(function(p){ return !p.reminders_on; });
  if (PF === 'nonum')   return all.filter(function(p){ return !p.whatsapp; });
  return all;                                   // owing + all are the same list
}
function paintParties(){
  var term = PQ.trim().toLowerCase();
  var rows = partyPool().filter(function(p){
    return !term || String(p.name).toLowerCase().indexOf(term) >= 0; });
  if (PSORT === 'amount')    rows = rows.slice().sort(function(a,b){ return b.amt - a.amt; });
  else if (PSORT === 'name') rows = rows.slice().sort(function(a,b){
    return String(a.name).localeCompare(String(b.name)); });
  var total = rows.length, start = PPAGE * PER;
  if (start >= total) { PPAGE = 0; start = 0; }
  var page = rows.slice(start, start + PER);
  var el = document.getElementById('plist');
  if (!total) {
    el.innerHTML = emptyBox(term ? I.find : I.tick,
      term ? 'No party matches that' : 'Nothing here',
      term ? 'Try part of the name.' : 'This list is empty right now.');
    document.getElementById('ppage').innerHTML = ''; return;
  }
  el.innerHTML = '<div class="rows">' + page.map(function(p){
    var sv = severity(p.overdue_days, p.paused);
    // severity() already answers "on promise" when paused, so ask it for the
    // lateness instead and add the state once, not twice.
    var late = severity(p.overdue_days, false)[1];
    var tag = p.paused ? 'on promise \u00b7 ' + late
            : (p.reminders_on === false ? late + ' \u00b7 not chased' : late);
    return '<button class="row ' + (p.paused ? '' : sv[0]) + '" onclick="partyView(' + jq(p.id) + ')">'
     + '<span class="stripe"></span>'
     + '<span class="nm"><b>' + esc(p.name) + '</b><span>' + esc(tag) + '</span></span>'
     + '<span class="amt num">' + rupee(p.outstanding) + '</span>'
     + '<span class="chev">&#8250;</span></button>'; }).join('') + '</div>';
  var pg = document.getElementById('ppage');
  pg.innerHTML = total > PER
    ? '<div class="pager"><button class="btn" ' + (PPAGE===0?'disabled':'')
      + ' onclick="pstep(-1)">Back</button><span class="pinfo num" style="margin:0">'
      + (start+1) + '-' + (start+page.length) + ' of ' + total + '</span>'
      + '<button class="btn" ' + (start+PER>=total?'disabled':'') + ' onclick="pstep(1)">Next</button></div>'
    : '<div class="pinfo num">' + total + ' part' + (total===1?'y':'ies') + '</div>';
}
function pstep(n){ PPAGE += n; paintParties(); window.scrollTo(0,0); }

// ── 3. PARTY (full page) ─────────────────────────────────────────────────
var CUR = null;
async function partyView(id){
  document.body.classList.remove('noTabs');
  app.innerHTML = header('Loading', '', 'go(\'' + TAB + '\')')
   + '<div class="wrap">' + skeleton() + '</div>' + tabs();
  var d;
  try { d = await api('party?id=' + encodeURIComponent(id)); }
  catch (e) { if (String(e.message) !== 'unauthorised')
      app.innerHTML = header('ASVA','', 'go(\''+TAB+'\')')
        + '<div class="wrap">' + errBox('partyView(' + jq(id) + ')') + '</div>' + tabs();
    return; }
  CUR = d;
  paintParty();
}
function paintParty(){
  var d = CUR;
  var w = waNum(d.whatsapp), t = telNum(d.whatsapp);
  var head = '<div class="big fade"><div class="l">Outstanding</div>'
   + '<div class="v num">' + rupee(d.outstanding) + '</div><div class="s">'
   + (d.open_bills.length ? d.open_bills.length + ' open bill'
        + (d.open_bills.length>1?'s':'') : 'no open bills')
   + ' · <span class="pill ' + (d.reminders_on ? 'pill-on' : 'pill-off') + '">'
   + (d.reminders_on ? 'ASVA is chasing' : 'not being chased') + '</span></div></div>';

  var reach = w
    ? '<a class="btn btn-wa" href="https://wa.me/' + esc(w) + '" target="_blank" rel="noopener">'
      + I.wa + ' WhatsApp</a>' + (t ? '<a class="btn" href="tel:'+esc(t)+'">'+I.call+' Call</a>' : '')
    : (t ? '<a class="btn btn-wa" href="tel:'+esc(t)+'">' + I.call + ' Call</a>'
         : '<button class="btn btn-pri btn-full" onclick="numberView()">Add a WhatsApp number</button>');
  var actions = '<div class="acts fade d1" style="margin-top:12px">' + reach
   + (w ? '<button class="btn" onclick="doRemind(this)">' + I.bell + ' Remind now</button>' : '')
   + '</div>';

  var promise = '';
  if (d.promise) {
    var when = d.promise.promise_date || d.promise.hold_until || '';
    promise = '<div class="card fade d1" style="margin-top:12px;border-color:'
     + 'color-mix(in srgb,var(--warn) 32%,transparent)">'
     + '<b>On promise' + (when ? ' until ' + esc(dmy(when)) : '') + '</b>'
     + (d.promise.said ? '<div class="msg" style="border:0;padding:8px 0 0">&ldquo;'
         + esc(d.promise.said) + '&rdquo;</div>' : '')
     + '<div style="margin-top:11px"><button class="btn btn-full" onclick="doRelease(this)">'
     + 'He has not paid, chase again</button></div></div>';
  }

  var sched = (d.schedule && d.schedule.label)
    ? '<div class="card fade d1" style="margin-top:12px;display:flex;'
      + 'align-items:center;gap:12px"><div style="flex:1;min-width:0">'
      + '<div style="font-size:.72rem;font-weight:700;letter-spacing:.09em;'
      + 'text-transform:uppercase;color:var(--muted)">Next reminder</div>'
      + '<div style="font-weight:650;margin-top:3px">' + esc(d.schedule.label)
      + '</div></div>'
      + (d.reminders_on ? '' : '<span class="pill pill-off">paused</span>')
      + '</div>'
    : '';
  var settled = (d.settled_bills && d.settled_bills.length)
    ? '<div class="sect">Settled bills<span class="cnt">' + d.settled_count
      + '</span></div><div class="card fade d2">' + d.settled_bills.map(function(b){
          return '<div class="bill"><div class="inv">' + esc(b.invoice)
           + '</div><div class="amt num" style="color:var(--muted)">'
           + rupee(b.amount) + '</div></div>'; }).join('') + '</div>'
    : '';
  var bills = d.open_bills.length
    ? '<div class="card fade d2">' + d.open_bills.map(function(b){
        return '<div class="bill"><div class="inv">' + esc(b.invoice)
         + (b.overdue_days > 0 ? '<span class="od num">' + b.overdue_days + 'd</span>' : '')
         + '</div><div class="amt num">' + rupee(b.amount) + '</div></div>'; }).join('')
      + '</div>'
    : '';
  var replies = (d.recent && d.recent.length)
    ? '<div class="sect">What they said</div><div class="card fade d2">'
      + d.recent.map(function(m){
          return '<div class="msg">&ldquo;' + esc(m.text) + '&rdquo;'
           + (m.when ? '<span>' + esc(dmy(m.when)) + '</span>' : '') + '</div>'; }).join('')
      + '</div>'
    : '';

  var settings = '<div class="sect">This party</div><div class="rows fade d2">'
   + srow('Reminders', d.reminders_on ? 'ASVA chases this party on its own schedule'
                                      : 'ASVA will not chase this party',
          '<button class="sw ' + (d.reminders_on ? 'on' : '') + '" role="switch"'
          + ' aria-checked="' + (d.reminders_on ? 'true' : 'false')
          + '" aria-label="Reminders" onclick="toggleRem(this)"></button>')
   + srow('Credit days', (d.credit_days || 30) + ' days to pay after a bill',
          '<button class="btn" style="min-height:40px;padding:9px 13px" onclick="termsView()">Change</button>')
   + srow('WhatsApp number', d.whatsapp ? esc(d.whatsapp) : 'none saved, so ASVA cannot reach them',
          '<button class="btn" style="min-height:40px;padding:9px 13px" onclick="numberView()">'
          + (d.whatsapp ? 'Change' : 'Add') + '</button>')
   + srow('Do not chase', d.excluded ? 'this party is on the do-not-chase list'
                                     : 'keep them out of every list and reminder',
          '<button class="sw ' + (d.excluded ? 'on' : '') + '" role="switch"'
          + ' aria-checked="' + (d.excluded ? 'true' : 'false')
          + '" aria-label="Do not chase" onclick="toggleExcl(this)"></button>')
   + '</div>';

  var book = '<div class="sect">Book something</div>'
   + '<button class="btn btn-pri btn-full fade d2" onclick="payView()">' + I.tick
   + ' Payment received</button>'
   + '<button class="btn btn-full fade d2" style="margin-top:9px" onclick="billView()">'
   + I.plus + ' Add a bill</button>'
   + (d.whatsapp
       ? '<button class="btn btn-full fade d2" style="margin-top:9px"'
         + ' onclick="letterView()">Formal reminder letter</button>' : '');

  app.innerHTML = header(d.name, d.whatsapp || 'no number saved', 'go(\'' + TAB + '\')')
   + '<div class="wrap">' + head + actions + promise
   + sched
   + (bills ? '<div class="sect">Open bills</div>' + bills : '')
   + settled + book + settings + replies + '</div>' + tabs();
}
function srow(title, sub, control){
  return '<div class="srow"><div class="sl"><b>' + esc(title) + '</b><span>' + sub
   + '</span></div>' + control + '</div>';
}

// ── party actions ────────────────────────────────────────────────────────
async function doRemind(btn){
  var done = busy(btn, 'Sending...');
  try { await post('remind', {party: CUR.name}); toast('Reminder queued for ' + CUR.name); }
  catch (e) { toast('Could not send: ' + e.message); }
  done();
}
async function doRelease(btn){
  var done = busy(btn, 'Working...');
  try { await post('release', {client_id: CUR.id}); toast('Back in the chase list');
        await refresh(); partyView(CUR.id); }
  catch (e) { toast('Could not do that: ' + e.message); done(); }
}
async function toggleRem(el){
  var next = !el.classList.contains('on');
  el.classList.toggle('on', next); el.setAttribute('aria-checked', next ? 'true' : 'false');
  try { await post('reminders', {client_id: CUR.id, on: next});
        CUR.reminders_on = next;
        toast(next ? 'ASVA will chase ' + CUR.name : CUR.name + ' will not be chased');
        refresh(); }
  catch (e) { el.classList.toggle('on', !next); toast('Could not save: ' + e.message); }
}
async function toggleExcl(el){
  var next = !el.classList.contains('on');
  el.classList.toggle('on', next); el.setAttribute('aria-checked', next ? 'true' : 'false');
  try { await post('exclude', {client_id: CUR.id, on: next});
        CUR.excluded = next;
        toast(next ? 'Moved to do-not-chase' : 'Back in the normal list');
        refresh(); }
  catch (e) { el.classList.toggle('on', !next); toast('Could not save: ' + e.message); }
}

// ── small forms, as their own page (never a dialog) ──────────────────────
function formPage(title, sub, fields, submitLabel, onsubmit){
  document.body.classList.add('noTabs');
  app.innerHTML = header(title, sub, 'paintParty()')
   + '<div class="wrap"><div class="card fade"><div class="fgrid">' + fields + '</div>'
   + '<div style="margin-top:16px"><button class="btn btn-pri btn-full" id="fsub">'
   + esc(submitLabel) + '</button></div></div>'
   + '<div class="foot">This changes only ' + esc(CUR ? CUR.name : 'this party') + '.</div></div>';
  document.getElementById('fsub').onclick = function(){ onsubmit(this); };
  var first = app.querySelector('input'); if (first) first.focus();
}
function fval(id){ var e = document.getElementById(id); return e ? e.value.trim() : ''; }

function payView(){
  formPage('Payment received', CUR.name,
    '<div><label class="fl" for="amt">How much came in</label>'
    + '<input id="amt" type="number" inputmode="decimal" min="1" placeholder="e.g. 25000"></div>'
    + '<div><label class="fl" for="pdate">When</label>'
    + '<input id="pdate" type="date" value="' + today() + '"></div>',
    'Book this payment', async function(btn){
      var amt = parseFloat(fval('amt'));
      if (!(amt > 0)) return toast('Enter the amount that came in.');
      var done = busy(btn, 'Booking...');
      try { await post('record-payment',
              {client_id: CUR.id, amount: amt, payment_date: fval('pdate') || null});
            toast('Saved. It posts when the shop computer is on.');
            await refresh(); partyView(CUR.id); }
      catch (e) { toast('Could not book: ' + e.message); done(); }
    });
}
function billView(){
  formPage('Add a bill', CUR.name,
    '<div><label class="fl" for="bamt">Bill amount</label>'
    + '<input id="bamt" type="number" inputmode="decimal" min="1" placeholder="e.g. 18500"></div>'
    + '<div><label class="fl" for="binv">Bill number (optional)</label>'
    + '<input id="binv" placeholder="e.g. 1042"></div>'
    + '<div><label class="fl" for="bdate">Bill date</label>'
    + '<input id="bdate" type="date" value="' + today() + '"></div>',
    'Add this bill', async function(btn){
      var amt = parseFloat(fval('bamt'));
      if (!(amt > 0)) return toast('Enter the bill amount.');
      var done = busy(btn, 'Adding...');
      try { await post('bill', {client_id: CUR.id, amount: amt,
              invoice_number: fval('binv'), invoice_date: fval('bdate') || null});
            toast('Bill added'); await refresh(); partyView(CUR.id); }
      catch (e) { toast('Could not add: ' + e.message); done(); }
    });
}
function termsView(){
  formPage('Credit days', CUR.name,
    '<div><label class="fl" for="cd">Days this party gets to pay</label>'
    + '<input id="cd" type="number" inputmode="numeric" min="0" max="365" value="'
    + esc(CUR.credit_days || 30) + '"></div>',
    'Save', async function(btn){
      var dys = parseInt(fval('cd'), 10);
      if (!(dys >= 0 && dys <= 365)) return toast('Enter a number between 0 and 365.');
      var done = busy(btn, 'Saving...');
      try { await post('terms', {client_id: CUR.id, days: dys});
            toast('Credit days saved'); await refresh(); partyView(CUR.id); }
      catch (e) { toast('Could not save: ' + e.message); done(); }
    });
}
function numberView(){
  formPage('WhatsApp number', CUR.name,
    '<div><label class="fl" for="num">Their 10 digit mobile</label>'
    + '<input id="num" type="tel" inputmode="numeric" placeholder="9876543210" value="'
    + esc(String(CUR.whatsapp || '').slice(-10)) + '"></div>',
    'Save number', async function(btn){
      var n = digits(fval('num'));
      if (n.length < 10) return toast('Enter the 10 digit mobile number.');
      var done = busy(btn, 'Saving...');
      try { await post('number', {client_id: CUR.id, number: n});
            toast('Number saved'); await refresh(); partyView(CUR.id); }
      catch (e) { toast('Could not save: ' + e.message); done(); }
    });
}
function today(){
  var d = new Date(Date.now() + 5.5*3600*1000);
  return d.toISOString().slice(0,10);
}

// The formal reminder. Two screens on purpose: the owner reads the exact words
// first and sends on a second, separate tap. Escalation is never one gesture.
async function letterView(){
  document.body.classList.add('noTabs');
  app.innerHTML = header('Formal reminder', CUR.name, 'paintParty()')
   + '<div class="wrap">' + skeleton() + '</div>';
  var r;
  try { r = await post('letter', {client_id: CUR.id}); }
  catch (e) { toast('Could not build it: ' + e.message); return paintParty(); }
  var body = r.text || r.message || r.body || '';
  if (r.ok === false || !body) {
    app.innerHTML = header('Formal reminder', CUR.name, 'paintParty()')
     + '<div class="wrap">' + emptyBox(I.find, 'Nothing to send',
        r.detail || 'This party has no overdue bill that needs a formal letter.')
     + '</div>';
    return;
  }
  app.innerHTML = header('Formal reminder', CUR.name, 'paintParty()')
   + '<div class="wrap">'
   + '<div class="leded" style="margin-bottom:12px">This is exactly what '
   + esc(CUR.name) + ' will receive. Read it before you send it.</div>'
   + '<div class="card fade"><div class="letter">' + esc(body) + '</div></div>'
   + '<div style="margin-top:14px"><button class="btn btn-pri btn-full" id="lsend"'
   // Not "Send this to <party name>": a long trade name wraps the button to
   // two lines, and a two-line button is the clearest sign of a page nobody
   // opened on a phone.
   + ' onclick="letterSend(this)">Send this letter</button></div>'
   + '<div class="foot">ASVA will not send a letter on its own. This one goes '
   + 'only because you tapped send.</div></div>';
}
async function letterSend(btn){
  var done = busy(btn, 'Sending...');
  try { await post('letter/send', {client_id: CUR.id});
        toast('Letter sent to ' + CUR.name); partyView(CUR.id); }
  catch (e) { toast('Could not send: ' + e.message); done(); }
}

// ── 4. MONEY ─────────────────────────────────────────────────────────────
async function renderMoney(){
  shell(shopName(), shopSub(), skeleton());
  var m;
  try { m = await api('money'); }
  catch (e) { if (String(e.message) !== 'unauthorised')
                shell(shopName(), shopSub(), errBox('renderMoney()')); return; }
  S.money = m;
  try { S.inbox = await api('inbox'); } catch (e) { S.inbox = {pending:[], signals:[]}; }
  var low = (m.collected_pct !== null && m.collected_pct !== undefined
             && m.collected_pct < 40);
  var pct = (m.collected_pct === null || m.collected_pct === undefined) ? ''
    : '<div class="s">' + (low ? '<span class="pill pill-crit">' + m.collected_pct
         + '%</span> of what you billed this month'
        : m.collected_pct + '% of what you billed this month') + '</div>';
  var promises = (m.promises || []).length
    ? '<div class="rows fade d2">' + m.promises.map(function(p){
        // What he actually said beats the date we parsed out of it: "Friday
        // pakka sir" is the thing the owner recognises.
        var when = p.promise_date || p.hold_until || '';
        var line = p.said ? '“' + p.said + '”' : 'promised to pay';
        if (when) line += ' · ' + dmy(when);
        return '<div class="row" style="cursor:default">'
         + '<span class="nm"><b>' + esc(p.name) + '</b><span>' + esc(line) + '</span></span>'
         + '<button class="btn" style="min-height:40px;padding:9px 12px" onclick="releaseFrom('
         + jq(p.client_id) + ',this)">Chase again</button></div>'; }).join('') + '</div>'
    : emptyBox(I.tick, 'No open promises', 'Nobody has asked for more time.');

  // "Someone says they paid" used to arrive as a chat nudge and was settled
  // by scrolling back up the thread to find it. It is a list now.
  var inb = S.inbox || {};
  var claims = (inb.pending || []).concat([]);
  var sigs = (inb.signals || []);
  var sigBlock = sigs.length
    ? '<div class="sect">Tapped pay, nothing yet<span class="cnt">' + sigs.length
      + '</span></div><div class="rows fade">' + sigs.map(function(g){
          return '<div class="row" style="cursor:default">'
           + '<span class="nm"><b>' + esc(g.party_display || g.name || 'A customer')
           + '</b><span>opened your pay page'
           + (g.amount ? ' for ' + rupee(inr(g.amount)) : '') + '</span></span>'
           + '<button class="qa" onclick="sigGone(' + jq(g.id) + ',this)">Clear</button>'
           + '</div>'; }).join('') + '</div>'
    : '';
  var inbox = claims.length
    ? '<div class="sect">Says they paid<span class="cnt">' + claims.length + '</span></div>'
      + '<div class="rows fade">' + claims.map(function(c){
          var amt = c.amount ? rupee(inr(c.amount)) : 'amount not given';
          return '<div class="row" style="cursor:default;display:block">'
           + '<div style="display:flex;align-items:center;gap:12px">'
           + '<span class="nm"><b>' + esc(c.party_display || c.name || 'A customer')
           + '</b><span>' + esc(c.note || c.raw_text || 'said they have paid')
           + '</span></span><span class="amt num">' + amt + '</span></div>'
           + '<div class="ract" style="margin-left:0">'
           + '<button class="qa qa-pri" onclick="claimOk(' + jq(c.id) + ',this)">'
           + 'Book it</button>'
           + '<button class="qa" onclick="claimNo(' + jq(c.id) + ',this)">Not a payment'
           + '</button></div></div>'; }).join('') + '</div>'
    : '';

  // On the 1st of a month every shop has recovered zero, and a big zero with
  // no context reads as "the app is broken" on exactly the day an owner is
  // most likely to open it. Last month carries the screen until this one has
  // numbers of its own.
  var nil = String(m.recovered_this_month || '').replace(/[^1-9]/g, '') === '';
  var lastLine = (nil && m.recovered_last_month)
    ? '<div class="s">Nothing yet this month. Last month you recovered '
      + rupee(m.recovered_last_month) + '.</div>'
    : '';

  shell(shopName(), shopSub(),
    inbox + sigBlock
    + '<div class="big fade"><div class="l">Recovered in ' + esc(m.recovered_month || 'this month')
    + '</div><div class="v num">' + rupee(m.recovered_this_month) + '</div>'
    + pct + lastLine + '</div>'
    + '<div class="duo">'
    + '<div class="big fade d1"><div class="l">Coming in</div><div class="v num">'
    + rupee(m.coming_total) + '</div><div class="s">next ' + m.coming_days + ' days</div></div>'
    + '<div class="big fade d1"><div class="l">Billed</div><div class="v num">'
    + rupee(m.billed_this_month) + '</div><div class="s">this month</div></div></div>'
    + '<div class="sect">Promised to pay<span class="cnt">' + (m.promises||[]).length + '</span></div>'
    + promises
    + '<div class="sect">Add a bill</div>'
    + '<button class="btn btn-full fade" onclick="photoPick()">' + I.cam
    + ' Read a bill from a photo</button>'
    + '<input type="file" id="pbfile" accept="image/*"'
    + ' style="display:none" onchange="photoRead(this)">'
    + '<div class="hint" style="margin-top:9px;color:var(--muted);font-size:.8rem;'
    + 'line-height:1.5">Pick it from your gallery (WhatsApp saves what customers '
    + 'send you), or take a new photo. You can also forward the bill straight to '
    + 'the ASVA number and it will read it there.</div>'
    + '<div class="foot">Recovered counts receipts booked in Tally.<br>'
    + 'Coming in is what customers promised plus what falls due.</div>');
}
async function claimOk(id, btn){
  var done = busy(btn, 'Booking...');
  try { await post('pending/confirm', {id: id});
        toast('Approved. It posts when the shop computer is on.');
        S.inbox = null; await refresh(); renderMoney(); }
  catch (e) { toast('Could not book: ' + e.message); done(); }
}
async function sigGone(id, btn){
  var done = busy(btn, '...');
  try { await post('signal/dismiss', {id: id}); toast('Cleared');
        S.inbox = null; renderMoney(); }
  catch (e) { toast('Could not clear: ' + e.message); done(); }
}
async function claimNo(id, btn){
  var done = busy(btn, '...');
  try { await post('pending/cancel', {id: id});
        toast('Cleared'); S.inbox = null; renderMoney(); }
  catch (e) { toast('Could not clear: ' + e.message); done(); }
}

// ── a bill from a photo ─────────────────────────────────────────────────
// The camera is on the phone, so this is the one thing the chat did that the
// phone has the better claim to.
function photoPick(){ document.getElementById('pbfile').click(); }
function photoRead(inp){
  var f = inp.files && inp.files[0];
  inp.value = '';
  if (!f) return;
  if (f.size > 6 * 1024 * 1024) return toast('That photo is too large. Try again.');
  toast('Reading the photo...');
  var fr = new FileReader();
  fr.onload = async function(){
    var b64 = String(fr.result).split(',')[1] || '';
    try {
      var r = await post('photo-bill', {image_b64: b64, image_type: f.type || 'image/jpeg'});
      if (!r.ok) return toast(r.detail || 'Could not read that photo.');
      photoForm(r.read || {});
    } catch (e) { toast('Could not read it: ' + e.message); }
  };
  fr.onerror = function(){ toast('Could not open that photo.'); };
  fr.readAsDataURL(f);
}
function photoForm(rd){
  document.body.classList.add('noTabs');
  app.innerHTML = header('Bill from a photo', 'check it before saving',
                         "go('money')")
   + '<div class="wrap"><div class="leded" style="margin-bottom:12px">'
   + 'ASVA read this from the photo. Fix anything it got wrong.</div>'
   + '<div class="card fade"><div class="fgrid">'
   + '<div><label class="fl" for="pbname">Party</label><input id="pbname" value="'
   + esc(rd.party_name || '') + '" placeholder="Party name"></div>'
   + '<div><label class="fl" for="pbamt">Amount</label><input id="pbamt" type="number"'
   + ' inputmode="decimal" value="' + esc(rd.amount == null ? '' : rd.amount) + '"></div>'
   + '<div><label class="fl" for="pbno">Bill number</label><input id="pbno" value="'
   + esc(rd.bill_number || '') + '"></div>'
   + '<div><label class="fl" for="pbdate">Bill date</label><input id="pbdate" type="date"'
   + ' value="' + esc(rd.bill_date || today()) + '"></div></div>'
   + '<div style="margin-top:16px"><button class="btn btn-pri btn-full" id="pbsave"'
   + ' onclick="photoSave(this)">Save this bill</button></div></div>'
   + '<div class="foot">Saving creates the bill and, if the party has a number, '
   + 'ASVA sends it to them.</div></div>';
}
async function photoSave(btn){
  var nm = fval('pbname'), amt = parseFloat(fval('pbamt'));
  if (!nm) return toast('Enter the party name.');
  if (!(amt > 0)) return toast('Enter the bill amount.');
  var done = busy(btn, 'Saving...');
  try { await post('photo-bill/save', {party_name: nm, amount: amt,
          bill_number: fval('pbno'), bill_date: fval('pbdate') || null});
        toast('Bill saved'); await refresh(); go('money'); }
  catch (e) { toast('Could not save: ' + e.message); done(); }
}

async function releaseFrom(cid, btn){
  var done = busy(btn, '...');
  try { await post('release', {client_id: cid}); toast('Back in the chase list');
        await refresh(); renderMoney(); }
  catch (e) { toast('Could not do that: ' + e.message); done(); }
}

// ── 5. SETUP ─────────────────────────────────────────────────────────────
function wa(){ return (S.today && S.today.wa) || {state:'unknown'}; }
function waTag(){
  var st = wa().state;
  return st === 'up' ? 'connected' : (st === 'down' ? 'not connected' : 'unknown');
}
function waPill(){
  var st = wa().state;
  return st === 'up' ? 'pill-on' : (st === 'down' ? 'pill-crit' : 'pill-off');
}
function waWord(){
  var st = wa().state;
  if (st === 'up') return 'reminders can go out right now';
  if (st === 'down') return 'nothing can go out until it is reconnected on the shop computer';
  return 'ASVA has not checked recently';
}
function renderSetup(){
  var d = S.today || S.sum;
  // the brief calls it reminders_on_count, the summary calls it reminders_on
  var onN = (d.reminders_on_count !== undefined) ? d.reminders_on_count
          : (d.reminders_on || 0);
  var pickRow = srow('Who ASVA chases',
    onN + ' of your parties are being reminded'
    + (d.stage === 'pick' ? '. Nobody yet, so nothing is going out.' : ''),
    '<button class="btn" style="min-height:40px;padding:9px 13px" onclick="pickView(10)">'
    + (d.stage === 'pick' ? 'Pick' : 'Add more') + '</button>');
  shell(shopName(), shopSub(),
    '<div class="lede">Setup</div>'
    + '<div class="leded">Whether ASVA is working, and the few things worth '
    + 'changing from your phone. Batches, timings, UPI and letters live on the '
    + 'shop computer.</div>'
    + '<div class="sect">Is it working</div><div class="rows fade">' + pickRow
    + srow('Tally', d.tally_synced === 'never' ? 'has not synced yet'
            : 'last synced ' + d.tally_synced,
           '<span class="pill ' + (d.tally_synced === 'today' ? 'pill-on' : 'pill-warn') + '">'
           + (d.tally_synced === 'today' ? 'fresh' : 'check') + '</span>')
    + srow('Your WhatsApp', waWord(),
           '<span class="pill ' + waPill() + '">' + waTag() + '</span>') + '</div>'
    + '<div class="sect">Help</div><div class="rows fade d1">'
    + srow('Ask ASVA', 'Message the ASVA number on WhatsApp and someone answers',
           '<a class="btn" style="min-height:40px;padding:9px 13px" target="_blank" rel="noopener"'
           + ' href="https://wa.me/919344110272?text=' + encodeURIComponent('Hi, I need help with ASVA')
           + '">Open chat</a>')
    + srow('The computer app', 'Setup, batches, letters and reports live there',
           '<a class="btn" style="min-height:40px;padding:9px 13px" href="/download">Get it</a>')
    + '</div>'
    + '<div class="sect">This phone</div><div class="rows fade d1">'
    + srow('Install ASVA', 'Add it to your home screen so it opens like an app',
           '<button class="btn" style="min-height:40px;padding:9px 13px" onclick="doInstall()">'
           + 'Install</button>')
    + srow('Sign out', 'You will need your ASVA code again',
           '<button class="btn btn-dang" style="min-height:40px;padding:9px 13px"'
           + ' onclick="logout()">Sign out</button>')
    + '</div>'
    + '<div class="foot">ASVA never files anything with anyone.<br>'
    + 'Reminders go from your own WhatsApp number.</div>');
}

// ── 6. PICK TEN ──────────────────────────────────────────────────────────
var PICKED = {};
async function pickView(n){
  document.body.classList.add('noTabs');
  app.innerHTML = header('Who should ASVA chase?', '', 'go(\'' + TAB + '\')')
   + '<div class="wrap">' + skeleton() + '</div>';
  var d;
  try { d = await api('picks?n=' + encodeURIComponent(n || 10)); }
  catch (e) { if (String(e.message) !== 'unauthorised')
      app.innerHTML = header('Who should ASVA chase?','', 'go(\''+TAB+'\')')
        + '<div class="wrap">' + errBox('pickView(' + (n||10) + ')') + '</div>';
    return; }
  S.picks = d.picks || [];
  PICKED = {}; S.picks.forEach(function(p){ PICKED[p.id] = true; });
  if (!S.picks.length) {
    app.innerHTML = header('Who should ASVA chase?','', 'go(\''+TAB+'\')')
     + '<div class="wrap">' + emptyBox(I.tick, 'Nothing to pick',
        'Either everyone with money out is already being chased, or nobody here has '
        + 'a WhatsApp number yet.') + '</div>';
    return;
  }
  paintPick();
}
function paintPick(){
  var n = Object.keys(PICKED).filter(function(k){ return PICKED[k]; }).length;
  app.innerHTML = header('Who should ASVA chase?', '', 'go(\'' + TAB + '\')')
   + '<div class="wrap">'
   + '<div class="leded" style="margin-bottom:14px">These owe you the most, for the '
   + 'longest. ASVA will remind only the ones you leave ticked and stay completely '
   + 'silent about everybody else.</div>'
   + '<div class="rows fade">' + S.picks.map(function(p){
       var on = !!PICKED[p.id];
       return '<button class="row" onclick="togglePick(' + jq(p.id) + ')">'
        + '<span class="mini" style="' + (on
            ? 'background:var(--accent);border-color:var(--accent);color:#fff'
            : '') + '">' + (on ? I.tick : '') + '</span>'
        + '<span class="nm"><b>' + esc(p.name) + '</b><span>'
        + esc(p.days ? p.days + ' days late' : 'not due yet') + '</span></span>'
        + '<span class="amt num">' + rupee(p.outstanding) + '</span></button>'; }).join('')
   + '</div>'
   + '<div style="margin-top:16px"><button class="btn btn-pri btn-full" id="psave"'
   + (n ? '' : ' disabled') + ' onclick="savePick(this)">Start with these '
   + '<span class="num">' + n + '</span></button></div>'
   + '<div class="foot">You can turn any party on or off later.<br>'
   + 'Bills and replies keep working for everyone either way.</div></div>';
}
function togglePick(id){ PICKED[id] = !PICKED[id]; paintPick(); }
async function savePick(btn){
  var ids = Object.keys(PICKED).filter(function(k){ return PICKED[k]; });
  if (!ids.length) return toast('Tick at least one party.');
  var done = busy(btn, 'Saving...');
  try { await post('pick', {client_ids: ids});
        toast('ASVA will chase these ' + ids.length);
        await refresh(); go('today'); }
  catch (e) { toast('Could not save: ' + e.message); done(); }
}

// ── install / auth ───────────────────────────────────────────────────────
function installBar(){
  var standalone = false;
  try { standalone = window.matchMedia('(display-mode: standalone)').matches
          || window.navigator.standalone === true; } catch (e) {}
  var hidden = false; try { hidden = localStorage.getItem('asva_m_installed') === '1'; } catch(e){}
  if (standalone || hidden) return '';
  var txt = INSTALL_EVT ? 'Install ASVA on this phone so it opens like an app.'
    : 'Add ASVA to your home screen from your browser menu, and it opens like an app.';
  var btn = INSTALL_EVT ? '<button class="btn" style="min-height:40px;padding:9px 13px"'
    + ' onclick="doInstall()">Install</button>' : '';
  return '<div class="install" id="ibar"><div class="it">' + txt + '</div>' + btn
   + '<button class="x" onclick="hideInstall()" aria-label="Dismiss">&times;</button></div>';
}
async function doInstall(){
  if (INSTALL_EVT) { INSTALL_EVT.prompt();
    try { await INSTALL_EVT.userChoice; } catch (e) {} INSTALL_EVT = null; hideInstall(); }
  else toast('Use your browser menu, then Add to Home screen.');
}
function hideInstall(){
  try { localStorage.setItem('asva_m_installed','1'); } catch(e){}
  var b = document.getElementById('ibar'); if (b) b.remove();
}
function logout(){
  try { localStorage.removeItem('asva_m_token'); } catch(e){}
  TOKEN = ''; S = {sum:null, today:null, money:null, picks:null, inbox:null,
                 cp:null}; loginView();
}
function loginView(){
  document.body.classList.add('noTabs');
  app.innerHTML = '<div class="login fade"><div class="lm">' + MARK + '</div>'
   + '<h1>ASVA on your phone</h1>'
   + '<p>Open the ASVA app on your shop computer, go to Settings and scan the phone '
   + 'QR code. Or type the code it shows you.</p>'
   + '<div class="fgrid"><input id="tk" placeholder="Your ASVA code" autocomplete="off"'
   + ' autocapitalize="characters" spellcheck="false">'
   + '<button class="btn btn-pri btn-full" onclick="doLogin()">Open my shop</button></div>'
   + '<div class="foot" style="text-align:left">The code links this phone to your shop '
   + 'only. Nothing else can be opened with it.</div></div>';
  var i = document.getElementById('tk');
  i.onkeydown = function(e){ if (e.key === 'Enter') doLogin(); };
}
function doLogin(){
  var v = (document.getElementById('tk').value || '').trim();
  if (!v) return toast('Enter the code from the computer app.');
  try { localStorage.setItem('asva_m_token', v); } catch(e){}
  TOKEN = v; boot();
}
async function refresh(){ S.today = null; S.money = null; S.inbox = null; S.cp = null;
  try { S.sum = await api('summary'); } catch (e) {} }

// ── go ───────────────────────────────────────────────────────────────────
boot();
if ('serviceWorker' in navigator) {
  window.addEventListener('load', function(){
    navigator.serviceWorker.register('/m/sw.js').catch(function(){});
  });
}
</script></body></html>"""
