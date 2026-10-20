// native-sim-template-version: 27
/**
 * Patches @expo/serve-sim 0.4.0's input-socket admission, in place.
 *
 * serve-sim admits at most 8 input WebSockets per stream, shared by every
 * viewer, and never pings them — so a viewer that went away without a clean
 * TCP close (sleeping laptop, dropped tunnel, a tab the proxy lost track of)
 * holds its slot until the session ends. The stream page retries once a
 * second and shows "Simulator input failed: Simulator input unavailable;
 * retry after other clients disconnect" on every refusal, which reads as
 * "someone else is driving" when nobody is. The same message also covers a
 * capture session that is not running, which is a different problem.
 *
 * Three changes, each anchored on an exact string from the 0.4.0 bundle so a
 * serve-sim bump that moves the code fails this step loudly instead of
 * silently shipping unpatched:
 *   1. the per-stream cap goes from 8 to 64;
 *   2. every admitted input socket is pinged every 15s and terminated after
 *      a missed pong, the same pattern serve-sim already uses for its WebKit
 *      inspector sockets — a dead viewer frees its slot within ~30s;
 *   3. "capture not running" gets its own close reason.
 *
 * It also drops the "EAS Simulator" link (to expo.dev) from the devices
 * sidebar of the stream page, which is embedded in the bundle as base64,
 * and on phone widths (below Tailwind's sm, 640px) hides the top row (devices
 * menu, status pill, share/full screen/tools/logs) and every bottom action
 * but Screenshot and the AX overlay toggle.
 *
 * Usage: node patch-serve-sim.cjs <path to dist/serve-sim.js>
 */
const fs = require('node:fs');

const file = process.argv[2];
if (!file) {
  console.error('usage: patch-serve-sim.cjs <dist/serve-sim.js>');
  process.exit(2);
}
let src = fs.readFileSync(file, 'utf8');

if (src.includes('/* native-sim: input sockets patched */')) {
  console.log('serve-sim already patched');
  process.exit(0);
}

// 1 + 2 + 3: the admission check. `$` is the socket parameter in the 0.4.0
// bundle; the regex captures whatever the minifier named it and the cap
// constant, so a rebuild with different names still matches as long as the
// shape is the same.
const admit = /attachHidSocket\((\$|\w+)\)\{if\(this\.phase!=="running"\|\|this\.hidSockets\.size>=(\$?\w+)\)\{\1\.close\(1013,"Simulator input unavailable; retry after other clients disconnect"\);return\}this\.hidSockets\.add\(\1\),this\.admittedHidSockets\.add\(\1\),/;
const m = src.match(admit);
if (!m) {
  console.error('patch-serve-sim: admission check not found — serve-sim changed, review the patch');
  process.exit(1);
}
const [, sock, cap] = m;
// serve-sim hands attachHidSocket two kinds of socket: a `ws` WebSocket, and
// a small wrapper ({send, on, close}) for its raw-upgrade path that has no
// ping/pong/once. Calling `.once` on the wrapper threw and took the whole
// server down (inti.5), so the heartbeat only goes on sockets that have it.
const heartbeat =
  `(typeof ${sock}.ping==="function"&&typeof ${sock}.once==="function"&&typeof ${sock}.terminate==="function"&&(()=>{let alive=!0;const timer=setInterval(()=>{if(!alive){try{${sock}.terminate()}catch{}return}alive=!1;try{${sock}.ping()}catch{}},15000);` +
  `${sock}.on("pong",()=>{alive=!0});${sock}.once("close",()=>clearInterval(timer))})()),`;
src = src.replace(
  admit,
  `attachHidSocket(${sock}){if(this.phase!=="running"){${sock}.close(1013,"Simulator capture is not running yet; retrying");return}` +
    `if(this.hidSockets.size>=${cap}){${sock}.close(1013,"Simulator input unavailable; retry after other clients disconnect");return}` +
    `this.hidSockets.add(${sock}),this.admittedHidSockets.add(${sock}),${heartbeat}`,
);

// 1: the cap itself. Declared in a `var` list as `<name>=8,`.
const capDecl = new RegExp(`([,\\s])${cap.replace('$', '\\$')}=8([,;])`);
if (!capDecl.test(src)) {
  console.error(`patch-serve-sim: cap constant ${cap}=8 not found — serve-sim changed, review the patch`);
  process.exit(1);
}
src = src.replace(capDecl, `$1${cap}=64$2`);

// The stream page is one HTML document, base64 in the bundle. Its brand link
// component (`<a href="https://expo.dev/services/simulators">EAS Simulator</a>`)
// is rendered from two places, so emptying the component removes it from both.
const page = /Buffer\.from\("(PCFkb2N0[A-Za-z0-9+/=]+)","base64"\)/;
const pm = src.match(page);
if (!pm) {
  console.error('patch-serve-sim: embedded stream page not found — serve-sim changed, review the patch');
  process.exit(1);
}
let html = Buffer.from(pm[1], 'base64').toString('utf-8');
const brand = /function (\$?\w+)\(\{className:\$=""\}\)\{return \$?\w+\("a",\{href:"https:\/\/expo\.dev\/services\/simulators",[\s\S]*?children:"EAS Simulator"\}\)\}/;
if (!brand.test(html)) {
  console.error('patch-serve-sim: EAS Simulator link not found — serve-sim changed, review the patch');
  process.exit(1);
}
html = html.replace(brand, 'function $1(){return null}');
// Phone widths: the stream page is shown inside the inti.computer project view,
// which has its own chrome, so only the screen, Screenshot and AX stay.
// Selected by the aria-labels serve-sim renders; :has() picks the two
// fixed toolbars (devices menu on the left, share/full screen/tools/logs on
// the right) by the button they contain.
const mobileCss =
  '<style>@media (max-width:639.98px){' +
  'div:has(> [aria-label="Open devices sidebar"]),' +
  'div:has(> [aria-label="Open tools panel"]),' +
  '[aria-label="Simulator status"],' +
  '[aria-label="Reload React Native bundle"],' +
  '[aria-label="Home"],' +
  '[aria-label="Rotate device"]' +
  '{display:none!important}}</style>';
for (const label of ['Open devices sidebar', 'Open tools panel', 'Simulator status', 'Home', 'Rotate device']) {
  if (!html.includes(`"aria-label":"${label}"`)) {
    console.error(`patch-serve-sim: "${label}" not found in the stream page — serve-sim changed, review the patch`);
    process.exit(1);
  }
}
if (!html.includes('</head>')) {
  console.error('patch-serve-sim: </head> not found in the stream page — serve-sim changed, review the patch');
  process.exit(1);
}
// Input in the page keeps the session alive: the gate stamps its activity
// file on /__native-sim/keepalive, and an embedding page (inti.computer) hears
// a postMessage so it can keep its own machine up too. Once a minute at most.
const activityJs =
  '<script>(()=>{let t=0;const f=()=>{const n=Date.now();if(n-t<60000)return;t=n;' +
  "fetch('/__native-sim/keepalive',{method:'POST',credentials:'same-origin'}).catch(()=>{});" +
  "try{if(parent!==window)parent.postMessage({type:'native-sim:activity'},'*')}catch{}};" +
  "for(const e of['pointerdown','keydown','wheel'])addEventListener(e,f,{passive:true,capture:true})})()</script>";
html = html.replace('</head>', () => `${mobileCss}${activityJs}</head>`);
src = src.replace(page, () => `Buffer.from("${Buffer.from(html, 'utf-8').toString('base64')}","base64")`);

// Appended, not prepended: the bundle starts with a shebang line.
src = src + '\n/* native-sim: input sockets patched */\n';
fs.writeFileSync(file, src);
console.log(`serve-sim patched: input cap ${cap} 8 → 64, heartbeat on input sockets, EAS Simulator link removed, mobile toolbar trimmed, input keeps the session alive`);
