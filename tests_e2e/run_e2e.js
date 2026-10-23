// A browser test harness with no dependencies.
//
// Launches the installed Chrome headless with remote debugging, opens a page,
// and runs assertions inside it over the devtools protocol. Node 24 has a
// WebSocket client built in, so this needs nothing from npm, which matters:
// the point of these tests is to catch a page whose script is broken, and a
// test rig that cannot be installed never runs.
//
// Usage:  node tests_e2e/run_e2e.js <url> <spec.js> [<spec.js> ...]
//
// A spec file is evaluated IN THE PAGE, inside an async function, and must
// `return` an array of {name, pass, detail} (or {results: [...]}). Top-level
// await works; the value of a trailing expression does not, hence `return`.
const { spawn } = require('child_process');
const fs = require('fs');
const path = require('path');
const os = require('os');

const CHROME = [
  'C:/Program Files/Google/Chrome/Application/chrome.exe',
  'C:/Program Files (x86)/Google/Chrome/Application/chrome.exe',
  '/usr/bin/google-chrome',
].find(p => { try { return fs.existsSync(p); } catch (e) { return false; } });

if (!CHROME) { console.error('No Chrome found.'); process.exit(2); }

const PORT = 9333 + (process.pid % 200);

function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }

async function targets() {
  const r = await fetch(`http://127.0.0.1:${PORT}/json/list`);
  return r.json();
}

async function waitForChrome(tries = 60) {
  for (let i = 0; i < tries; i++) {
    try { await targets(); return true; } catch (e) { await sleep(250); }
  }
  return false;
}

// One devtools session: send commands, await their replies by id.
function session(wsUrl) {
  const ws = new WebSocket(wsUrl);
  let id = 0;
  const pending = new Map();
  const ready = new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
  ws.onmessage = (ev) => {
    const msg = JSON.parse(ev.data);
    if (msg.id && pending.has(msg.id)) {
      const { resolve, reject } = pending.get(msg.id);
      pending.delete(msg.id);
      msg.error ? reject(new Error(JSON.stringify(msg.error))) : resolve(msg.result);
    }
  };
  return {
    ready,
    send(method, params = {}) {
      const myId = ++id;
      return new Promise((resolve, reject) => {
        pending.set(myId, { resolve, reject });
        ws.send(JSON.stringify({ id: myId, method, params }));
      });
    },
    close() { try { ws.close(); } catch (e) {} },
  };
}

(async () => {
  const [url, ...specs] = process.argv.slice(2);
  if (!url || !specs.length) {
    console.error('usage: node tests_e2e/run_e2e.js <url> <spec.js> [...]');
    process.exit(2);
  }

  const profile = fs.mkdtempSync(path.join(os.tmpdir(), 'asva-e2e-'));
  const chrome = spawn(CHROME, [
    '--headless=new', `--remote-debugging-port=${PORT}`, `--user-data-dir=${profile}`,
    '--no-first-run', '--no-default-browser-check', '--disable-gpu',
    '--window-size=1280,900', 'about:blank',
  ], { stdio: 'ignore' });

  let failures = 0;
  try {
    if (!await waitForChrome()) throw new Error('Chrome did not start');
    const page = (await targets()).find(t => t.type === 'page');
    const s = session(page.webSocketDebuggerUrl);
    await s.ready;
    await s.send('Page.enable');
    await s.send('Runtime.enable');
    await s.send('Page.navigate', { url });
    // Wait for the document to be interactive and its scripts to have run.
    for (let i = 0; i < 80; i++) {
      const r = await s.send('Runtime.evaluate', {
        expression: 'document.readyState', returnByValue: true,
      });
      if (r.result.value === 'complete') break;
      await sleep(100);
    }
    await sleep(250);

    for (const spec of specs) {
      const code = fs.readFileSync(spec, 'utf8');
      const r = await s.send('Runtime.evaluate', {
        expression: `(async () => { ${code} })()`,
        awaitPromise: true, returnByValue: true,
      });
      if (r.exceptionDetails) {
        console.log(`\n${path.basename(spec)}: THREW`);
        console.log('   ' + (r.exceptionDetails.exception?.description || '').split('\n')[0]);
        failures++;
        continue;
      }
      const out = r.result.value;
      const results = Array.isArray(out) ? out : (out && out.results) || [];
      console.log(`\n${path.basename(spec)}`);
      for (const t of results) {
        console.log(`   ${t.pass ? 'PASS' : 'FAIL'}  ${t.name}${t.detail ? '  (' + t.detail + ')' : ''}`);
        if (!t.pass) failures++;
      }
      if (!results.length) { console.log('   (no assertions returned)'); failures++; }
    }
    s.close();
  } catch (e) {
    console.error('harness error:', e.message);
    failures++;
  } finally {
    chrome.kill();
    try { fs.rmSync(profile, { recursive: true, force: true }); } catch (e) {}
  }

  console.log(failures ? `\n${failures} failing` : '\nall green');
  process.exit(failures ? 1 : 0);
})();
