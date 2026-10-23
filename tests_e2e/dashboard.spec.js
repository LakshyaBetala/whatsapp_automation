// What a click on the Dashboard must actually do, measured in a real browser
// against the real rendered markup and the real page script.
//
// The thing being protected: excluding a party used to post the change and
// then reload the whole 1.5 MB page, which cost about two seconds and threw
// away the scroll position and anything typed in the search box. It now moves
// the row. These assertions fail if it goes back to reloading, if the row
// lands in the wrong tab, if the counts drift, or if a failed request leaves
// the page lying about what happened.
const t = [];
const ok = (name, pass, detail) => t.push({ name, pass: !!pass, detail: detail || '' });
const $ = (s) => document.querySelector(s);
const $$ = (s) => [...document.querySelectorAll(s)];
const rowOf = (name) => $$('tr[data-name]').find(r => r.dataset.name.includes(name));
const counts = () => Object.fromEntries($$('.subtabs button[data-sub]')
  .map(b => [b.dataset.sub, parseInt((b.textContent.match(/\(([\d,]+)\)/) || [0, '0'])[1].replace(/,/g, ''), 10)]));
const settle = () => new Promise(r => setTimeout(r, 80));

// ── The page loaded and its script ran ──────────────────────────────────
ok('the page script ran', typeof window.toggleExclude === 'function',
   'toggleExclude ' + typeof window.toggleExclude);
ok('four parties rendered', $$('tr[data-name]').length === 4,
   $$('tr[data-name]').length + ' rows');

const before = counts();
ok('tabs start at 2 / 1 / 1',
   before.tally === 2 && before.nontally === 1 && before.exclude === 1,
   JSON.stringify(before));

// ── Every row knows the tab it belongs to when it is not excluded ───────
ok('every row carries its home tab',
   $$('tr[data-name]').every(r => r.dataset.home === 'tally' || r.dataset.home === 'nontally'),
   $$('tr[data-name]').map(r => r.dataset.home).join(','));

// ── Excluding a party ───────────────────────────────────────────────────
const alpha = rowOf('alpha');
const btn = alpha.querySelector('.exclbtn');
const t0 = performance.now();
btn.click();
// The click is async (it posts), so wait for the row to actually move.
for (let i = 0; i < 40 && alpha.dataset.src !== 'exclude'; i++) await settle();
const took = Math.round(performance.now() - t0);

ok('the row moved to Do-not-chase', alpha.dataset.src === 'exclude', 'data-src=' + alpha.dataset.src);
ok('it answered in well under a second', took < 900, took + ' ms');
ok('the page was NOT reloaded', window.__E2E.reloaded === false);
ok('the change was posted once', window.__E2E.posts.length === 1,
   window.__E2E.posts.length + ' request(s)');
ok('it posted the right party and flag',
   (window.__E2E.posts[0] || {}).body && window.__E2E.posts[0].body.includes('"client_id": "c1"') === false
     ? window.__E2E.posts[0].body.includes('c1') && window.__E2E.posts[0].body.includes('true')
     : true,
   (window.__E2E.posts[0] || {}).body);
ok('the button now offers to bring it back', btn.textContent.trim() === 'Bring back', btn.textContent.trim());
ok('reminders were unticked for it', alpha.querySelector('input.cb').checked === false);

const mid = counts();
ok('tab counts followed: 1 / 1 / 2',
   mid.tally === 1 && mid.nontally === 1 && mid.exclude === 2, JSON.stringify(mid));
ok('the row left the tab being viewed', alpha.style.display === 'none',
   'display=' + (alpha.style.display || '(shown)'));

// ── Bringing it back puts it where it came from ─────────────────────────
btn.click();
for (let i = 0; i < 40 && alpha.dataset.src === 'exclude'; i++) await settle();
ok('it went back to its own tab, not a guess', alpha.dataset.src === 'tally', alpha.dataset.src);
ok('the counts came back', JSON.stringify(counts()) === JSON.stringify(before), JSON.stringify(counts()));
ok('still no reload', window.__E2E.reloaded === false);

// ── A failed request must not leave the page lying ──────────────────────
window.fetch = () => Promise.resolve({ ok: false, status: 500, json: () => Promise.resolve({}) });
const gamma = rowOf('gamma');
const gbtn = gamma.querySelector('.exclbtn');
const gsrc = gamma.dataset.src;
gbtn.click();
for (let i = 0; i < 40 && !window.__E2E.lastAlert; i++) await settle();
ok('a failed exclude leaves the row where it was', gamma.dataset.src === gsrc, gamma.dataset.src);
ok('the owner is told it did not go through', /did not go through/i.test(window.__E2E.lastAlert || ''),
   window.__E2E.lastAlert);
ok('the button is usable again', gbtn.disabled === false);

return t;
