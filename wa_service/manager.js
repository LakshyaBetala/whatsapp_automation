// manager.js - runs one WhatsApp service (index.js) per shop whose WhatsApp
// lives on this server.
//
// Every minute it asks the backend which shops (GET /internal/wa-shops, which
// answers only localhost callers with WA_API_KEY), starts the missing ones,
// stops the ones that left the list, and restarts any that crash, with a
// backoff (2s doubling to 60s; reset after two healthy minutes). Each shop is
// its own process on its own 127.0.0.1 port: one shop's crash or lockup never
// touches another, and the per-shop code is exactly the field-hardened
// index.js a shop laptop runs. Sessions live under this folder's .baileys_auth
// (session-shop-<business id>), so the existing state bundle carries them.
//
// Run by systemd (asva-shops.service) as server-asva. No root needed.
'use strict';

const { spawn } = require('child_process');
const http = require('http');
const path = require('path');

const BACKEND_URL = process.env.BACKEND_URL || 'http://localhost:8000';
const WA_API_KEY = process.env.WA_API_KEY || '';
const POLL_MS = Number(process.env.MANAGER_POLL_MS || 60000);
const DIR = __dirname;

// business_id -> { proc, port, token, restarts, startedAt, timer, wanted }
const children = new Map();

function fetchShops() {
    return new Promise((resolve, reject) => {
        const u = new URL('/internal/wa-shops', BACKEND_URL);
        const req = http.get(u, { headers: { 'x-asva-key': WA_API_KEY }, timeout: 15000 }, (res) => {
            let body = '';
            res.on('data', (c) => { body += c; });
            res.on('end', () => {
                if (res.statusCode !== 200) return reject(new Error('HTTP ' + res.statusCode));
                try { resolve(JSON.parse(body).shops || []); } catch (e) { reject(e); }
            });
        });
        req.on('timeout', () => req.destroy(new Error('timeout')));
        req.on('error', reject);
    });
}

function tagOf(id) { return String(id).slice(0, 8); }

function startChild(shop) {
    const id = shop.business_id;
    const c = children.get(id) || { restarts: 0 };
    c.port = shop.port;
    c.token = shop.token;
    c.wanted = true;
    const tag = tagOf(id);
    const env = {
        ...process.env,
        PORT: String(shop.port),
        SESSION_ID: 'shop-' + id,
        WA_CHANNEL: 'shop',
        WA_TOKEN: shop.token,          // proves inbound forwards + loads the customer allow-list
        WA_API_KEY,                    // guards this shop process's own HTTP API
        WA_BIND_HOST: '127.0.0.1',     // never reachable from outside the box
        BACKEND_URL,
        WA_AUTH_DIR: DIR,
    };
    delete env.WEBHOOK_SECRET;         // the bot number's secret: a shop never carries it
    delete env.MANAGER_POLL_MS;
    const proc = spawn(process.execPath, [path.join(DIR, 'index.js')],
        { cwd: DIR, env, stdio: ['ignore', 'pipe', 'pipe'] });
    c.proc = proc;
    c.startedAt = Date.now();
    const pipe = (s) => s.on('data', (d) => String(d).split('\n').filter(Boolean)
        .forEach((line) => console.log(`[${tag}] ${line}`)));
    pipe(proc.stdout);
    pipe(proc.stderr);
    proc.on('exit', (code, signal) => {
        c.proc = null;
        if (!c.wanted) { console.log(`[${tag}] stopped`); return; }
        if (Date.now() - c.startedAt > 120000) c.restarts = 0;   // it ran fine for a while
        c.restarts += 1;
        const delay = Math.min(2000 * 2 ** (c.restarts - 1), 60000);
        console.log(`[${tag}] exited (code=${code} signal=${signal}); restarting in ${Math.round(delay / 1000)}s`);
        c.timer = setTimeout(() => {
            c.timer = null;
            if (c.wanted) startChild({ business_id: id, port: c.port, token: c.token });
        }, delay);
    });
    children.set(id, c);
    console.log(`[${tag}] started on 127.0.0.1:${shop.port} (pid ${proc.pid})`);
}

function stopChild(id) {
    const c = children.get(id);
    if (!c) return;
    c.wanted = false;
    if (c.timer) clearTimeout(c.timer);
    if (c.proc) c.proc.kill('SIGTERM');
    children.delete(id);
}

async function reconcile() {
    let shops;
    try {
        shops = await fetchShops();
    } catch (e) {
        // Backend restarting or unreachable: keep every running shop as it is.
        console.warn(`manager: shop list unavailable (${e.message}); keeping ${children.size} running`);
        return;
    }
    const want = new Map(shops.map((s) => [s.business_id, s]));
    for (const id of [...children.keys()]) {
        if (!want.has(id)) { console.log(`[${tagOf(id)}] no longer on the server list`); stopChild(id); }
    }
    for (const s of shops) {
        const c = children.get(s.business_id);
        if (!c) startChild(s);
        // Port or token changed. Only stop it here: SIGTERM is asynchronous, so
        // spawning the replacement in the same breath put two Baileys processes
        // on one auth directory, which corrupts the session. The next poll sees
        // no child and starts it cleanly.
        else if (c.port !== s.port || c.token !== s.token) stopChild(s.business_id);
    }
}

function shutdown() {
    console.log('manager: stopping all shop WhatsApp processes');
    for (const id of [...children.keys()]) stopChild(id);
    setTimeout(() => process.exit(0), 3000);
}
process.on('SIGTERM', shutdown);
process.on('SIGINT', shutdown);

if (!WA_API_KEY) {
    console.error('manager: WA_API_KEY is not set; refusing to start (shop processes would be unauthenticated).');
    process.exit(1);
}
console.log(`ASVA shop WhatsApp manager: backend ${BACKEND_URL}, checking every ${POLL_MS / 1000}s`);
reconcile();
setInterval(reconcile, POLL_MS);
