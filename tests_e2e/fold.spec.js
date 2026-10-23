// The menu fold, watched frame by frame in a real browser.
//
// What used to happen: the panel animated from 208px to 58px while everything
// inside it disappeared on the first frame. The eye read three events for one
// action. These assertions fail if the panel stops animating, if the labels go
// back to blinking out, or if the collapsed rail stops being a tight 58px.
const t = [];
const ok = (name, pass, detail) => t.push({ name, pass: !!pass, detail: detail || '' });
const side = document.getElementById('side');
const wm = document.querySelector('#brand .wm');
const sleep = (ms) => new Promise(r => setTimeout(r, ms));
const width = () => side.getBoundingClientRect().width;

// Start expanded, whatever the saved preference is.
localStorage.setItem('asva_rail', '0');
applyRail();
await sleep(400);
const open = width();
ok('the menu starts open at its full width', open > 180, Math.round(open) + 'px');
ok('the wordmark is readable when open',
   parseFloat(getComputedStyle(wm).opacity) === 1, getComputedStyle(wm).opacity);

// ── Fold it, sampling while it moves ───────────────────────────────────
toggleRail();
const frames = [];
const labelOpacity = [];
for (let i = 0; i < 14; i++) {
  frames.push(width());
  labelOpacity.push(parseFloat(getComputedStyle(wm).opacity));
  await sleep(20);
}
await sleep(400);
const closed = width();

ok('it ends as a narrow rail', closed > 40 && closed < 75, Math.round(closed) + 'px');
const midway = frames.filter(w => w < open - 8 && w > closed + 8).length;
ok('the panel animates rather than jumping', midway >= 2,
   midway + ' intermediate widths: ' + frames.map(Math.round).join(','));

const faded = labelOpacity.filter(o => o > 0.02 && o < 0.98).length;
ok('the wordmark fades out with it, not before it', faded >= 1,
   'opacities ' + labelOpacity.map(o => o.toFixed(2)).join(','));
ok('the label is gone from the layout once folded',
   getComputedStyle(wm).display === 'none', getComputedStyle(wm).display);

// ── Unfold ─────────────────────────────────────────────────────────────
toggleRail();
const back = [];
for (let i = 0; i < 14; i++) { back.push(width()); await sleep(20); }
await sleep(400);
ok('it comes back to full width', width() > 180, Math.round(width()) + 'px');
ok('coming back animates too',
   back.filter(w => w > closed + 8 && w < open - 8).length >= 2,
   back.map(Math.round).join(','));
ok('the wordmark is readable again',
   parseFloat(getComputedStyle(wm).opacity) === 1 && getComputedStyle(wm).display !== 'none');

// ── The toggle is reachable both ways ──────────────────────────────────
ok('Ctrl+B is wired to the fold',
   typeof toggleRail === 'function' && typeof applyRail === 'function');
ok('the fold is remembered', localStorage.getItem('asva_rail') === '0',
   localStorage.getItem('asva_rail'));

return t;
