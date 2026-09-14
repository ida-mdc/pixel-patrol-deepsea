/* The page is a browser over the store `tiles` wrote: an index of the taxonomy,
   and beside it a directory per taxon holding pages of animals. Nothing is built
   into the HTML, so the page does not grow with the collection - what is on screen
   is what has been fetched. */
// Beside the page unless the build said otherwise. A collection is fifteen
// gigabytes of store and reports and a hundred kilobytes of page, and those two
// do not have to live in the same place - see `collect site --data-url`.
const TILES = (typeof window !== 'undefined' && window.PP_TILES) || 'tiles';
const SVGNS = 'http://www.w3.org/2000/svg';
/* The ring's geometry, in the units the sunburst is drawn in. The hole is wide
   because it is a button - the way back out - and not just the middle. Every wedge
   reaches RIM whether the naming got that deep or not, so the drawing is the same
   half circle at every level. */
const HOLE = 26, RING = 26, RINGS = 3;
const RIM = HOLE + RING * RINGS - 2;
/* The two branches that are not taxonomy: the register's names run out here. */
const UNPLACED = 'Unplaced', UNDECIDED = 'Undecided';
/* Below this a wedge is thinner than its own outline and not worth a path. */
const SLIVER = 0.004;

/* The only things this page says in its own words, because they are its own words:
   names this project gave, not animals somebody else described. */
const OURS = {
  Undecided: `The detector saw this animal several times and called it something
    different each time, with no rank the guesses shared - a fish one second, a
    sponge the next. Rather than pick the loudest, the name was dropped. Worth a
    look: this is where the detector is least sure.`,
  Unplaced: `Names the World Register of Marine Species does not carry: the
    detector's own classes, and the animals whose looks could not be reconciled.`,
};

const state = { index: null, path: [], taxa: [], queue: [], loading: false,
                done: false, pages: new Map(), clips: new Map(),
                keptKeys: new Set(), staged: null, csvUrl: '', drawingKept: 0,
                reels: new Map(), reelNow: [], rects: new Map(), painted: -1,
                filmTimer: null, shared: null };

const el = (id) => document.getElementById(id);

const say = (n) => n.toLocaleString();

/* Wide enough for the pictures to scroll in their own box, or narrow enough that
   the page's own scroll is the only one worth having. */
const paged = () => window.matchMedia('(min-width: 900px)').matches;

async function boot() {
  // Read before anything writes: settling the wall rewrites the address bar, and
  // the moment - or the collection - somebody was sent would be gone before it was
  // opened.
  const asked = askedFor();
  const sent = new URLSearchParams((location.hash || '').slice(1)).get('k') || '';
  try {
    state.index = await (await fetch(`${TILES}/index.json`)).json();
  } catch (err) {
    // Two different problems with the same symptom - a page and no pictures - and
    // the wrong answer to one of them is to rebuild a store that is already there.
    // A browser will not let a page opened from a disk read the files beside it.
    el('wall').innerHTML = location.protocol === 'file:'
      ? '<p class="empty">This page was opened from a disk rather than from a server, '
        + 'and a browser will not let it read the pictures beside it. Serve the '
        + 'folder instead - <code>python -m pixel_patrol_deepsea.serve .</code> in '
        + 'this directory, then open <code>http://localhost:8000</code>. The reports '
        + 'need it too, and the big ones need a server that answers byte ranges, '
        + 'which <code>python -m http.server</code> does not.</p>'
      : '<p class="empty">No pictures were written beside this page. '
        + 'Run <code>collect site</code> where the reports are.</p>';
    return;
  }
  // What was kept first: the tiles read it as they are drawn.
  refreshKept();
  drawJumps();
  wireFinding();
  focusOn(...fromHash());
  watchTheWall();
  wireTheStage();
  drawKept();
  openAsked(asked);
  openShared(sent);
}

/* ── the taxonomy, as rings ────────────────────────────────────────────────── */

/** The node the path names, walking down from the root. */
function nodeAt(path) {
  let node = state.index.tree;
  for (const step of path) {
    node = (node.children || []).find(child => child.name === step) ?? node;
  }
  return node;
}

/* ── finding a name ────────────────────────────────────────────────────────── */

/** Where each name sits in the tree, read out of the tree itself.
 *
 * Not out of a taxon's `above`, which is the register's classification and is not
 * the same thing: a species is filed under its genus and its chain stops at the
 * genus, so `above` names the parent for some and the node itself for others. The
 * tree already knows - every name is on exactly one node - and a path read from it
 * is the same path a click on that ring would have produced.
 */
function whereEachNameIs() {
  if (state.where) return state.where;
  const found = new Map();
  const walk = (node, path) => {
    for (const taxon of node.taxa || []) found.set(taxon, path);
    for (const child of node.children || []) walk(child, [...path, child.name]);
  };
  walk(state.index.tree, []);
  state.where = found;
  return found;
}

// Enough to choose from without becoming a second wall of its own.
const MOST_FOUND = 12;

/** The names worth offering for what has been typed, best first.
 *
 * A name that starts with what was typed beats one that merely contains it -
 * "cten" should reach Ctenophora before Lyssacinosida - and among equals the
 * commoner animal comes first, because a reader who types three letters is more
 * likely to be after the thing there are four thousand of.
 */
function matchesFor(taxa, query) {
  const wanted = query.trim().toLowerCase();
  if (!wanted) return [];
  const hits = [];
  for (const [name, about] of Object.entries(taxa || {})) {
    const at = name.toLowerCase().indexOf(wanted);
    if (at < 0) continue;
    hits.push({ name, count: about.count || 0, rank: about.rank || '', at });
  }
  hits.sort((a, b) => (a.at - b.at) || (b.count - a.count) || a.name.localeCompare(b.name));
  return hits.slice(0, MOST_FOUND);
}

function drawFound(hits) {
  const list = el('findList');
  list.innerHTML = '';
  state.foundAt = hits.length ? 0 : -1;
  if (!hits.length) {
    const none = document.createElement('li');
    none.className = 'none';
    none.textContent = 'no name like that was given';
    list.appendChild(none);
  }
  hits.forEach((hit, index) => {
    const row = document.createElement('li');
    row.setAttribute('role', 'option');
    row.className = index === 0 ? 'at' : '';
    row.innerHTML = `<span class="what">${escape(hit.name)}</span>`
      + `<span class="rank">${escape(hit.rank || 'no rank')}</span>`
      + `<span class="n">${hit.count.toLocaleString()}</span>`;
    row.addEventListener('mousedown', (event) => {   // before the input blurs
      event.preventDefault();
      pickFound(hit.name);
    });
    list.appendChild(row);
  });
  list.hidden = false;
  state.found = hits;
}

function shutFinding() {
  el('findList').hidden = true;
  state.found = [];
  state.foundAt = -1;
}

/** Show one name's animals, exactly as clicking its ring would. */
function pickFound(name) {
  const path = whereEachNameIs().get(name);
  if (!path) return;
  shutFinding();
  el('find').value = name;
  el('find').blur();
  focusOn(path, name);
}

function moveFinding(by) {
  const rows = [...el('findList').children].filter(row => !row.classList.contains('none'));
  if (!rows.length) return;
  state.foundAt = (state.foundAt + by + rows.length) % rows.length;
  rows.forEach((row, index) => row.classList.toggle('at', index === state.foundAt));
  rows[state.foundAt].scrollIntoView({ block: 'nearest' });
}

function wireFinding() {
  const box = el('find');
  if (!box) return;
  box.addEventListener('input', () => {
    const typed = box.value;
    if (!typed.trim()) { shutFinding(); return; }
    drawFound(matchesFor(state.index.taxa, typed));
  });
  box.addEventListener('keydown', (event) => {
    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      event.preventDefault();
      if (el('findList').hidden) drawFound(matchesFor(state.index.taxa, box.value));
      else moveFinding(event.key === 'ArrowDown' ? 1 : -1);
      return;
    }
    if (event.key === 'Enter') {
      event.preventDefault();
      const hit = (state.found || [])[state.foundAt];
      if (hit) pickFound(hit.name);
      return;
    }
    if (event.key === 'Escape') { box.value = ''; shutFinding(); box.blur(); }
  });
  box.addEventListener('blur', () => setTimeout(shutFinding, 0));
  box.addEventListener('focus', () => {
    if (box.value.trim()) drawFound(matchesFor(state.index.taxa, box.value));
  });
}


/** Every taxon under a node - the leaves whose pages the wall will read. */
function taxaUnder(node) {
  const found = [...(node.taxa || [])];
  for (const child of node.children || []) found.push(...taxaUnder(child));
  return found;
}

const RING_COLOURS = ['#35d6f5', '#4cc9f0', '#4895ef', '#5e7ce2', '#7b6cf6', '#9b5de5',
                      '#c05299', '#e5678b', '#f4845f', '#f7b267'];

function colourFor(name, depth) {
  let hash = 0;
  for (const ch of name) hash = (hash * 31 + ch.charCodeAt(0)) >>> 0;
  const base = RING_COLOURS[hash % RING_COLOURS.length];
  return depth === 0 ? base : shade(base, 1 - depth * 0.18);
}

function shade(hex, factor) {
  const n = parseInt(hex.slice(1), 16);
  const rgb = [n >> 16, (n >> 8) & 255, n & 255]
    .map(v => Math.max(0, Math.min(255, Math.round(v * factor))));
  return `rgb(${rgb.join(',')})`;
}

/** An SVG arc between two angles, at one ring's radius. */
function arc(from, to, inner, outer) {
  const point = (angle, r) => [r * Math.sin(angle), -r * Math.cos(angle)];
  const [x0, y0] = point(from, outer), [x1, y1] = point(to, outer);
  const [x2, y2] = point(to, inner), [x3, y3] = point(from, inner);
  const wide = to - from > Math.PI ? 1 : 0;
  return `M${x0} ${y0}A${outer} ${outer} 0 ${wide} 1 ${x1} ${y1}`
       + `L${x2} ${y2}A${inner} ${inner} 0 ${wide} 0 ${x3} ${y3}Z`;
}

/** One path in the ring: what it looks like, what it says, and where it goes. */
function paint(d, { fill, opacity, stroke, width, title, reads, go, cls }) {
  const path = document.createElementNS(SVGNS, 'path');
  path.setAttribute('d', d);
  path.setAttribute('fill', fill);
  path.setAttribute('fill-opacity', String(opacity));
  path.setAttribute('stroke', stroke || '#04101c');
  path.setAttribute('stroke-width', String(width || 0.6));
  if (cls) path.setAttribute('class', cls);
  const label = document.createElementNS(SVGNS, 'title');
  label.textContent = title;
  path.appendChild(label);
  path.addEventListener('mouseenter', () => readout(reads));
  path.addEventListener('mouseleave', () => readout(null));
  if (go) path.addEventListener('click', go);
  el('sunburst').appendChild(path);
}

/* The colour of a wedge that goes no deeper. Grey rather than absent: a ring that
   stopped where the naming stopped left the drawing a ragged three-quarters of a
   half circle, and a reader wondering what ate the rest. Everywhere it is grey, the
   answer is that these animals were named at that rank and no further. */
const UNRESOLVED = '#3d566b';

/** A wedge where the naming ran out, from its depth all the way to the rim. */
function unresolved(from, to, depth, title, reads, go) {
  if (depth >= RINGS) return;
  paint(arc(from, to, HOLE + depth * RING, RIM), {
    fill: UNRESOLVED, opacity: 0.34, width: 0.6, cls: 'rest', title, reads, go });
}

/** One branch's arc, and the grey beyond it when nothing under it has a name. */
function wedge(node, from, to, depth, here) {
  const inner = HOLE + depth * RING;
  paint(arc(from, to, inner, inner + RING - 2), {
    fill: colourFor(node.name, depth), opacity: 0.92 - depth * 0.18,
    stroke: here ? '#7fd4ff' : '#04101c', width: here ? 1.6 : 0.6,
    title: `${node.name} — ${say(node.count)}`, reads: node,
    go: () => focusOn(pathOf(node)) });
  if (!(node.children || []).length) {
    unresolved(from, to, depth + 1,
      `${node.name} — ${say(node.count)}, as deep as the naming goes`,
      node, () => focusOn(pathOf(node)));
  }
}

/** The angle a node keeps for itself: animals named at its rank and no deeper.
 *
 * Called Porifera and nothing more. It is often the commonest answer the detector
 * gives, and it fills out to the rim like anything else that goes no further. */
function leftover(node, from, to, depth) {
  const kids = node.children || [];
  if (!kids.length) return;
  const rest = node.count - kids.reduce((sum, child) => sum + child.count, 0);
  if (rest <= 0) return;
  const reads = { name: `${node.name}, no finer name`, count: rest };
  unresolved(from, to, depth, `${reads.name} — ${say(rest)}`, reads,
             () => focusOn(pathOf(node), node.name));
}

/* ── the address bar ───────────────────────────────────────────────────────── */

/** Where the URL says to be: `#t=Animalia/Porifera`, or `!Porifera` for a rest arc,
 *  and `&a=EX2301/…mp4/259.4` for a moment somebody opened.
 *
 * A branch worth showing someone is worth being able to send them - most of all
 * Undecided, which is the one anybody will want to argue about - and so is a
 * sighting. A link to this page is a link to what was on the screen. */
function fromHash() {
  const raw = new URLSearchParams((location.hash || '').slice(1)).get('t') || '';
  if (!raw) return [[]];
  const [trail, only] = raw.split('!');
  const path = trail.split('/').filter(Boolean);
  return [path, only || null];
}

function writeHash() {
  const trail = state.path.join('/') + (state.only ? `!${state.only}` : '');
  const parts = [];
  if (trail) parts.push(`t=${encodeURIComponent(trail)}`);
  const staged = state.staged;
  if (staged && staged.animal && staged.animal.t && !el('stage').hidden) {
    parts.push('a=' + encodeURIComponent(
      [staged.animal.e, staged.animal.r, staged.animal.s].join('/')));
  }
  history.replaceState(null, '', parts.length ? `#${parts.join('&')}` : location.pathname);
}

/** One way in per phylum, plus the whole collection and the names that are not taxa.
 *
 * This row used to be the four biggest branches anywhere in the tree, which put a
 * kingdom, a phylum and a class beside each other - three different ranks, in an
 * order nothing explained, and no way back to everything. Phylum is the rank the
 * reports group and colour by, and the one a reader actually navigates by: sponges,
 * cnidarians, fish. So the row is every phylum this collection turned up, biggest
 * first, and the two branches that are not phyla are marked rather than mixed in.
 *
 * Undecided is one of those and is not a failure to be hidden: it is every animal
 * the detector called one thing and then another, which is where its confusion is
 * legible. */
function drawJumps() {
  const jumps = el('jumps');
  if (!jumps) return;
  const root = state.index.tree;
  const phyla = [], odd = [];
  for (const kingdom of root.children || []) {
    if (kingdom.name === UNPLACED) {
      // Undecided sits inside Unplaced, so it is one click in and not a second
      // button beside the thing that contains it.
      odd.push({ node: kingdom, path: [kingdom.name] });
      continue;
    }
    // A phylum is the step below the kingdom, whatever depth the register's own
    // intermediate ranks would have put it at.
    for (const phylum of kingdom.children || []) {
      phyla.push({ node: phylum, path: [kingdom.name, phylum.name] });
    }
  }
  phyla.sort((a, b) => b.node.count - a.node.count);
  const all = { node: root, path: [], all: true };
  jumps.replaceChildren(...[all, ...phyla, ...odd].map(jumpFor));
  markJumps();
}

function jumpFor({ node, path, all }) {
  const button = document.createElement('button');
  button.className = 'jump' + (all ? ' all' : '')
    + (node.name === UNDECIDED || node.name === UNPLACED ? ' odd' : '');
  button.dataset.path = path.join('/');
  button.innerHTML = `${escape(all ? 'everything' : node.name)} <span>${say(node.count)}</span>`;
  button.addEventListener('click', () => {
    focusOn(path);
    el('explore').scrollIntoView({ behavior: 'smooth', block: 'start' });
  });
  return button;
}

/** Light the button the wall is showing, so the row says where you are and not
 *  only where you could go. A branch deeper than a phylum lights its phylum. */
function markJumps() {
  const here = state.path.join('/');
  for (const button of el('jumps').children) {
    const path = button.dataset.path;
    const on = path ? here === path || here.startsWith(path + '/') : !here;
    button.classList.toggle('on', on);
  }
}

/** The path from the root down to a node, for a click on any ring. */
function pathOf(node) {
  const from = state.basePath || [];
  const base = nodeAt(from);
  if (node === base) return from;
  return [...from, ...(pathTo(base, node) || [])];
}

/** Three rings out from whatever is in focus, each child an arc of its parent.
 *
 * Half a circle, from straight up round to straight down, with the flat side on
 * the page's left edge. The same rings at twice the radius in a third of the width,
 * and the hole becomes big enough to be the button it ought to have been. */
function drawSun() {
  const svg = el('sunburst');
  const focus = nodeAt(state.path);
  // A leaf has no rings of its own. Draw its parent's and light the leaf up, so
  // the reader keeps the context instead of staring at an empty circle.
  const leaf = !(focus.children || []).length && state.path.length > 0;
  state.basePath = leaf ? state.path.slice(0, -1) : state.path;
  const base = nodeAt(state.basePath);
  svg.innerHTML = '';
  const place = (node, from, to, depth) => {
    if (depth >= RINGS || to - from < SLIVER) return;
    // The share of the *whole* wedge, not of what is left of it. Measuring against
    // the remainder shrank every child after the first - at the root, Animalia took
    // its honest four fifths and the two kingdoms after it got a fifth of a fifth
    // each, so the ring stopped at 151 degrees of 180 and the drawing came out as
    // some fraction of a half circle that changed with every branch.
    const whole = to - from, held = Math.max(1, node.count);
    let at = from;
    for (const child of node.children || []) {
      const span = whole * (child.count / held);
      if (span >= SLIVER) {
        wedge(child, at, at + span, depth, leaf && child.name === focus.name);
        place(child, at, at + span, depth + 1);
      }
      at += span;
    }
    if (to - at > SLIVER) leftover(node, at, to, depth);
  };
  place(base, 0, Math.PI, 0);
  drawHole();
  svg.setAttribute('viewBox', `-1 ${-RIM - 4} ${RIM + 5} ${2 * RIM + 8}`);
  readout(null);
  drawAbout(focus);
}

/** The half-disc at the flat side: a click in it is a step back out.
 *
 * The report's own sunburst has done this since it was a sunburst, and a reader
 * who has used one goes for the middle first. The breadcrumbs above do the same
 * thing for a keyboard and for jumping more than one step. */
function drawHole() {
  const up = state.only ? state.path : state.path.slice(0, -1);
  const somewhere = Boolean(state.only || state.path.length);
  const path = document.createElementNS(SVGNS, 'path');
  path.setAttribute('d', `M0 ${-HOLE}A${HOLE} ${HOLE} 0 0 1 0 ${HOLE}Z`);
  path.setAttribute('class', somewhere ? 'hole up' : 'hole');
  const title = document.createElementNS(SVGNS, 'title');
  title.textContent = somewhere
    ? `back to ${up.length ? up[up.length - 1] : 'everything'}`
    : 'the whole collection - click a ring to go in';
  path.appendChild(title);
  if (somewhere) path.addEventListener('click', () => focusOn(up));
  el('sunburst').appendChild(path);
  if (!somewhere) return;
  const label = document.createElementNS(SVGNS, 'text');
  label.setAttribute('class', 'back');
  label.setAttribute('x', '4');
  label.setAttribute('y', '2');
  label.textContent = '‹ BACK';
  el('sunburst').appendChild(label);
}

/** The line above the ring reads whatever the pointer is over, or the focus. */
function readout(node) {
  const focus = nodeAt(state.path);
  const shown = node || focus;
  el('sunRead').innerHTML = `<b>${say(shown.count)}</b><span>${shown.name === 'everything'
    ? 'animals, everything together' : escape(shown.name)}</span>`;
  el('sunRead').classList.toggle('hovering', Boolean(node));
}

/** Where to read about this branch - and nothing this page made up.
 *
 * The register is the record: somebody meeting `Asbestopluma` should land on the
 * people whose business it is, by identifier rather than by search. The
 * encyclopaedia is what actually describes the animal, and it is offered only where
 * `fetch_wikipedia` found an article, so the link is never a guess. The title it
 * found is worth showing as well, since `Holothuroidea` resolves to *Sea cucumber*,
 * which is the word somebody was looking for.
 *
 * The two buckets that are not taxonomy get a sentence of our own, because they are
 * names this project and the detector made up and nobody else has to explain. */
function drawAbout(focus) {
  const about = el('about');
  const named = state.index.taxa[focus.name];   // set when the detector says this name
  if (focus.name === 'everything') {
    about.innerHTML = `<p class="hint">Every animal the detector found, arranged by
      what it called them. Click a ring to go in; the pictures follow.</p>`;
    return;
  }
  const under = taxaUnder(focus).length;
  about.innerHTML = `
    <h3>${escape(focus.name)}${named && named.rank
      ? `<span class="rank">${escape(named.rank)}</span>` : ''}</h3>
    <p class="count">${say(focus.count)} animals${under > 1 ? ` · ${under} names` : ''}</p>
    ${OURS[focus.name] ? `<p class="hint">${escape(OURS[focus.name])}</p>` : ''}
    ${links(focus, named)}`;
}

/** The register, and the encyclopaedia where it has an article. */
function links(focus, named) {
  if (focus.name === UNPLACED || focus.name === UNDECIDED) return '';
  if (state.path[0] === UNPLACED) {
    return `<p class="hint">One of the detector's own class names. The World Register
      of Marine Species carries no record of it, so there is nothing to look up.</p>`;
  }
  const worms = named && named.aphia
    ? `https://www.marinespecies.org/aphia.php?p=taxdetails&id=${named.aphia}`
    : `https://www.marinespecies.org/aphia.php?p=taxlist&searchpar=0&tComp=begins&tName=${
        encodeURIComponent(focus.name)}`;
  const article = LOOKUP[focus.name];
  return `<p class="links">
    <a href="${worms}" target="_blank" rel="noopener">${named && named.aphia
      ? 'WoRMS record' : 'find it in WoRMS'} &rarr;</a>
    ${article ? `<a href="https://en.wikipedia.org/wiki/${
      encodeURIComponent(article.replace(/ /g, '_'))}" target="_blank"
      rel="noopener">Wikipedia: ${escape(article)} &rarr;</a>` : ''}</p>`;
}

/** The steps from an ancestor down to a node, for a click three rings out. */
function pathTo(from, wanted, trail = []) {
  for (const child of from.children || []) {
    if (child === wanted) return [...trail, child.name];
    const deeper = pathTo(child, wanted, [...trail, child.name]);
    if (deeper) return deeper;
  }
  return null;
}

function drawCrumbs() {
  const crumbs = el('crumbs');
  crumbs.innerHTML = '';
  // At the root the trail is one step long and the button row already says so.
  if (!state.path.length && !state.only) return;
  const steps = ['everything', ...state.path];
  steps.forEach((step, depth) => {
    const button = document.createElement('button');
    button.textContent = depth === 0 ? 'everything' : step;
    button.addEventListener('click', () => focusOn(state.path.slice(0, depth)));
    crumbs.appendChild(button);
    if (depth < steps.length - 1 || state.only) {
      const sep = document.createElement('span');
      sep.className = 'sep'; sep.textContent = '›';
      crumbs.appendChild(sep);
    }
  });
  if (state.only) {
    const button = document.createElement('button');
    button.className = 'only';
    button.textContent = `just ${state.only}`;
    button.title = 'animals named no further than this - click to widen';
    button.addEventListener('click', () => focusOn(state.path));
    crumbs.appendChild(button);
  }
}

/* ── the wall ─────────────────────────────────────────────────────────────── */

/** Point the wall at a branch: its taxa, their pages, and nothing loaded yet. */
function focusOn(path, only = null) {
  state.path = path;
  state.only = only;
  const focus = nodeAt(path);
  const wanted = only ? [only] : taxaUnder(focus);
  state.taxa = wanted.filter(name => state.index.taxa[name]);
  // Round-robin the taxa rather than exhausting one: a reader who clicks Cnidaria
  // wants to see cnidarians, not four hundred of the commonest one first.
  state.queue = [];
  const pages = state.taxa.map(name => state.index.taxa[name].pages);
  for (let page = 0; page < Math.max(0, ...pages); page++) {
    state.taxa.forEach((name, i) => {
      if (page < pages[i]) state.queue.push({ taxon: name, page });
    });
  }
  state.done = false;
  writeHash();
  const wall = el('wall');
  wall.replaceChildren(el('more'));
  wall.scrollTop = 0;
  drawSun();
  drawCrumbs();
  markJumps();
  loadMore();
}

async function loadMore() {
  if (state.loading || state.done) return;
  const next = state.queue.shift();
  if (!next) { state.done = true; return; }
  state.loading = true;
  try {
    const slug = state.index.taxa[next.taxon].slug;
    const { animals, stills } = await pageOf(slug, next.page);
    const wall = el('wall');
    let at = 0;
    animals.forEach((animal, index) => {
      const still = stills.slice(at, at + animal.l);
      at += animal.l;
      wall.insertBefore(tileFor(animal, still, slug, next.page, index), el('more'));
    });
  } catch (err) {
    /* a page that will not load is one page, not the end of the wall */
  } finally {
    state.loading = false;
  }
  // Keep filling until the end of the wall is out of reach, or a box taller than
  // its first page never asks for a second.
  if (roomForMore()) loadMore();
}

/** Is the end of the wall still within reach of what is on screen? */
function roomForMore() {
  const wall = el('wall');
  if (paged()) return wall.scrollHeight - wall.scrollTop - wall.clientHeight < 600;
  return el('more').getBoundingClientRect().top < window.innerHeight * 1.5;
}

/** Fetch the next page when the end of the wall comes into view.
 *
 * Watched inside the wall's own box where it has one, so scrolling the ring or the
 * page does not pull in pictures nobody asked for - and so the wall stops growing
 * the page, which is what put the expeditions below it out of reach. */
function watchTheWall() {
  const wall = el('wall');
  if (window.IntersectionObserver) {
    new IntersectionObserver(entries => {
      if (entries.some(entry => entry.isIntersecting)) loadMore();
    }, { root: paged() ? wall : null, rootMargin: '600px' }).observe(el('more'));
  }
  wall.addEventListener('scroll', () => { if (roomForMore()) loadMore(); });
}

/** A page of the store: its sizes, and the one blob holding its stills.
 *
 * Fetched once and kept, because the wall asks for the same page again when a
 * reader walks back up the taxonomy into a branch they have already seen. */
async function pageOf(slug, page) {
  const key = `${slug}/${page}`;
  if (!state.pages.has(key)) {
    state.pages.set(key, (async () => {
      const [animals, stills] = await Promise.all([
        fetch(`${TILES}/${slug}/p${page}.json`).then(r => r.json()),
        fetch(`${TILES}/${slug}/p${page}.jpgs`).then(r => r.arrayBuffer()),
      ]);
      return { animals, stills: new Uint8Array(stills) };
    })());
  }
  return state.pages.get(key);
}

/** The page's animations, fetched the first time any tile on it is hovered.
 *
 * One blob for sixty animals rather than sixty files: hovering the first tile of a
 * page pays for all of them, and a page nobody hovers costs nothing. */
async function clipsOf(slug, page) {
  const key = `${slug}/${page}`;
  if (!state.clips.has(key)) {
    state.clips.set(key, fetch(`${TILES}/${slug}/p${page}.clips`)
      .then(r => r.ok ? r.arrayBuffer() : new ArrayBuffer(0))
      .then(buffer => new Uint8Array(buffer))
      .catch(() => new Uint8Array(0)));
  }
  return state.clips.get(key);
}

/** Where in the page's clip blob one animal's frames begin. */
function clipStart(animals, wanted) {
  let at = 0;
  for (const animal of animals) {
    if (animal === wanted) return at;
    for (const size of animal.f || []) at += size;
  }
  return at;
}

const asPicture = (bytes) => URL.createObjectURL(new Blob([bytes], { type: 'image/jpeg' }));

/** One frame of the contact sheet: the picture, two facts over it, and a way in.
 *
 * How sure the detector was and how long the animal stayed in view at the top, the
 * name at the bottom, both only while the pointer is on it. There is no play badge:
 * nearly every tile has a film, so a mark on nearly every tile said nothing and
 * cost a corner of the picture.
 *
 * Clicking it opens the footage at the second it was cut from, and the star keeps
 * the sighting. */
function tileFor(animal, still, slug, page, at) {
  const figure = document.createElement('figure');
  figure.className = 'tile' + (animal.m ? ' moving' : '');
  const img = document.createElement('img');
  img.loading = 'lazy';
  img.decoding = 'async';
  img.alt = animal.t;
  img.src = asPicture(still);
  figure.appendChild(img);
  const meta = document.createElement('div');
  meta.className = 'meta';
  meta.innerHTML = `<b>${sure(animal.c)}</b><span>${held(animal)}</span>`;
  figure.appendChild(meta);
  const caption = document.createElement('figcaption');
  caption.textContent = animal.t;
  figure.appendChild(caption);
  figure.appendChild(starFor(animal, slug, page, at));
  figure.title = `Detector guess: ${animal.t} · ${animal.e} · ${clock(animal.s)} · `
    + `${sure(animal.c)} certainty · ${animal.n} frame${animal.n === 1 ? '' : 's'}`
    + (animal.a < 1 ? ` · agreed ${Math.round(animal.a * 100)}%` : '')
    + ' — click to open the footage here';
  const open = () => openStage(animal, slug, page, at, img.src);
  figure.addEventListener('click', open);
  figure.addEventListener('keydown', (event) => {
    if (event.key === 'Enter') { event.preventDefault(); open(); }
  });
  if (animal.m) animate(figure, img, slug, page, animal);
  return figure;
}

/** The star on a tile, lit when this sighting is one somebody kept. */
function starFor(animal, slug, page, at) {
  const star = document.createElement('button');
  const key = keyOf(animal);
  star.className = 'star' + (state.keptKeys.has(key) ? ' on' : '');
  star.dataset.key = key;
  star.textContent = state.keptKeys.has(key) ? '★' : '☆';
  star.title = 'keep this sighting';
  star.addEventListener('click', (event) => {
    event.stopPropagation();            // the tile itself opens the footage
    toggleKept(animal, slug, page, at);
  });
  return star;
}

/** How deep it was, where the vehicle's track said so. */
const deep = (metres) => (metres || metres === 0) ? `${Math.round(metres)} m` : '';

/** When it was filmed, in the archive's own UTC, trimmed to the minute. */
function stamp(when) {
  if (!when) return '';
  const said = String(when).replace('T', ' ').replace('+00:00', '');
  return escape(said.slice(0, 16)) + ' UTC';
}

/** How sure the detector was, as something a reader can read.
 *
 * A bare `0.94` beside a picture is a number with no unit and no scale - it was on
 * the tiles for months and it says nothing to anybody who has not been told what it
 * is. Per cent needs no telling. It is still the detector's own number and still
 * means what it always meant: how sure the model is, not how likely the name is to
 * be right. */
const sure = (confidence) => `${Math.round(confidence * 100)}%`;


/** How long the animal was in view. One look is a moment, not a duration. */
function held(animal) {
  const seconds = animal.d || 0;
  return seconds >= 0.05 ? `${seconds.toFixed(1)}s` : '·';
}

/** The seconds around an animal, cut out of its page's clip blob on first hover. */
function animate(figure, img, slug, page, animal) {
  let frames = null, timer = null, still = img.src;
  const start = async () => {
    if (!frames) {
      try {
        const [{ animals }, blob] = await Promise.all([
          pageOf(slug, page), clipsOf(slug, page)]);
        let at = clipStart(animals, animal);
        frames = (animal.f || []).map(size => {
          const picture = asPicture(blob.slice(at, at + size));
          at += size;
          return picture;
        });
      } catch { frames = []; }
    }
    if (!frames.length || timer) return;
    let at = 0;
    timer = setInterval(() => { img.src = frames[at++ % frames.length]; }, 110);
  };
  const stop = () => { clearInterval(timer); timer = null; img.src = still; };
  figure.addEventListener('mouseenter', start);
  figure.addEventListener('focusin', start);
  figure.addEventListener('mouseleave', stop);
  figure.addEventListener('focusout', stop);
  figure.tabIndex = 0;
}

/* ── the moment itself ─────────────────────────────────────────────────────── */

/** What the store knows about where an animal came from: the archive's own file for
 *  its recording, the frame its box is measured in, and the reel of everything else
 *  found in the same recording. */
const whereFrom = (animal) => (state.index.where || {})[animal.e] || {};
const fileOf = (animal) => (whereFrom(animal).videos || {})[animal.r] || '';
const frameOf = (animal) => whereFrom(animal).frame || [16, 9];
const reelName = (animal) => (whereFrom(animal).takes || {})[animal.r] || '';

/** A couple of seconds before the detection, because an animal arriving on screen
 *  is most of what tells a reader whether the box is around anything. */
const LEAD_IN = 2;
/* How long one look is worth drawing a box for, and how far apart two looks can be
   and still be one animal moving rather than one animal twice. The detector looks
   about once a second, so between two looks the box is moved along - and across a
   longer gap it is not, because nothing was seen in between. */
const A_LOOK = 0.7, A_STRIDE = 2.5;

/** Every animal found in one recording, fetched once and kept. */
async function reelOf(animal) {
  const name = reelName(animal);
  if (!name) return [];
  if (!state.reels.has(name)) {
    state.reels.set(name, fetch(`${TILES}/reels/${name}.json`)
      .then(answer => answer.json()).catch(() => []));
  }
  return state.reels.get(name);
}

/** Open the footage at the second this animal was found.
 *
 * Nothing is copied or re-hosted: the archives serve their own files over byte
 * ranges, so a browser can seek into a 900 MB recording and fetch only the piece it
 * needs. Where the manifest named no URL - or the archive will not play in a page -
 * the crop is shown instead and the note says so.
 *
 * Every other animal the detector found in the same recording is drawn as well,
 * where and when it was found, because the question somebody has while a dive plays
 * in front of them is what else is on screen. Clicking one of those takes over: the
 * video keeps playing, and the header, the star and the address bar follow it. */
async function openStage(animal, slug, page, at, stillSrc) {
  const before = state.staged;
  const same = before && before.animal && !el('stage').hidden
    && before.animal.e === animal.e && before.animal.r === animal.r;
  state.staged = { animal, slug, page, at, still: stillSrc };
  const [wide, high] = frameOf(animal);
  const file = fileOf(animal);
  const from = Math.max(0, (animal.s || 0) - LEAD_IN);
  if (!same) {
    const play = el('stagePlay');
    play.style.aspectRatio = `${wide} / ${high}`;
    play.replaceChildren(
      file ? footage(file, from, slug, page, at) : theCrop(stillSrc, animal, slug, page, at),
      boxLayer(wide, high));
    play.classList.toggle('no-box', !el('stageBox').checked);
    state.rects = new Map();
    state.reelNow = [];
    el('stageNote').textContent = file ? ''
      : 'No URL was recorded for this recording, so this is the crop and not the footage.';
    el('stageFile').href = file ? `${file}#t=${from.toFixed(1)}` : '#';
    el('stageFile').hidden = !file;
  }
  el('stage').hidden = false;
  sayStaged();
  if (same) { paintBoxes(true); return; }
  followAlong();
  state.reelNow = await reelOf(animal);
  paintBoxes(true);
}

/** Who is in focus: the header, the crop, the star, the way back, the address bar. */
function sayStaged() {
  const { animal, still } = state.staged;
  el('stageName').textContent = "Detector guess: " + animal.t;
  el('stageFacts').textContent = [
    `${sure(animal.c)} certainty`,
    `${held(animal)} in view`,
    `${animal.n} frame${animal.n === 1 ? '' : 's'}`,
  ].join(' · ');
  // Where and when it was, which is what anybody asks of a deep-sea picture after
  // what it is. The depth and the clock come out of the report; the recording is
  // the archive's own file name, and it is the thing to cite.
  el('stageWhere').innerHTML = [
    deep(animal.z), stamp(animal.w), `${escape(animal.e)} · ${clock(animal.s)}`,
    `<span class="reel">${escape(animal.r)}</span>`,
  ].filter(Boolean).join(' <span class="sep">·</span> ');
  // Whose frame this is, and what the name over it is worth. Both belong on the
  // thing itself: a crop travels, and it arrives without the page around it.
  const terms = whereFrom(animal);
  el('stageTerms').innerHTML = [
    terms.credit ? `<span class="credit">${escape(terms.credit)}</span>` : '',
    terms.licence ? escape(terms.licence) : '',
  ].filter(Boolean).join(' <span class="sep">·</span> ');
  const crop = el('stageCrop');
  crop.src = still || '';
  crop.hidden = !still;
  crop.alt = animal.t;
  el('stageStar').dataset.key = keyOf(animal);
  el('stageBack').textContent = `↺ back to ${clock(animal.s)}`;
  markKept();
  writeHash();
}

/** Whose file this is, for a message about it failing. */
function hostOf(url) {
  try {
    return new URL(url, location.href).hostname;
  } catch {
    return 'The archive';
  }
}

function footage(file, from, slug, page, at) {
  const video = document.createElement('video');
  video.src = `${file}#t=${from.toFixed(1)}`;
  video.controls = true;
  video.autoplay = true;
  video.muted = true;            // a page that makes noise unasked is a rude page
  video.playsInline = true;
  video.preload = 'metadata';
  video.addEventListener('loadedmetadata', () => {
    // The media fragment is a request, not a promise; some servers ignore it.
    if (Math.abs(video.currentTime - from) > 1) video.currentTime = from;
  });
  video.addEventListener('error', () => {
    // The archive is down, or the file is one this browser will not decode. Either
    // way there is something to show: the crop, and the clip cut around it.
    //
    // Named, because the answer is almost always that the archive is having a day
    // and nothing here is wrong - NCEI answered 503 for every file under
    // `/data/oceans` for as long as it took to work that out - and a reader who
    // can see whose host it was can check it, or open the recording with the link
    // beside this one and get the same answer from the archive itself.
    el('stageNote').textContent = `${hostOf(file)} would not play this file here.`;
    const staged = state.staged;
    const crop = theCrop(staged && staged.still, staged ? staged.animal : {},
                         slug, page, at);
    video.replaceWith(crop);
  });
  return video;
}

/** The animal itself, where the footage will not play.
 *
 * The still is what the tile showed and it is the least this can do; the clip the
 * detector cut is six frames of the same seconds, and it is already in the store.
 * A reader who cannot get the recording should at least see the animal move. */
function theCrop(stillSrc, animal, slug, page, at) {
  const img = document.createElement('img');
  img.src = stillSrc || '';
  img.alt = animal.t;
  playTheClip(img, slug, page, at);
  return img;
}

async function playTheClip(img, slug, page, at) {
  clearInterval(state.filmTimer);
  state.filmTimer = null;
  if (slug === undefined || page === undefined || at === undefined) return;
  try {
    const [{ animals }, blob] = await Promise.all([pageOf(slug, page), clipsOf(slug, page)]);
    const animal = animals[at];
    if (!animal || !animal.m || !(animal.f || []).length) return;
    let from = clipStart(animals, animal);
    const frames = animal.f.map(size => {
      const picture = asPicture(blob.slice(from, from + size));
      from += size;
      return picture;
    });
    if (!frames.length || el('stage').hidden) return;
    let step = 0;
    state.filmTimer = setInterval(() => {
      if (el('stage').hidden) { clearInterval(state.filmTimer); return; }
      img.src = frames[step++ % frames.length];
    }, 140);
    el('stageNote').textContent = el('stageNote').textContent
      + ' Playing the seconds the detector cut instead.';
  } catch { /* the still is what there is */ }
}

/** The layer the boxes are drawn on, in the coordinates of the analysed frame.
 *
 * `meet` rather than `none`: the video is fitted into the stage the same way, so
 * the two letterbox together whatever the window does to them. */
function boxLayer(wide, high) {
  const svg = document.createElementNS(SVGNS, 'svg');
  svg.setAttribute('id', 'stageBoxes');
  svg.setAttribute('viewBox', `0 0 ${wide} ${high}`);
  svg.setAttribute('preserveAspectRatio', 'xMidYMid meet');
  return svg;
}

/** Where an animal was at this second, between the two looks nearest to it.
 *
 * One box for the whole time an animal was in view is wrong the moment anything
 * swims: it was drawn at one second and the frame has moved on. So the box moves
 * between the looks the detector actually had, and where two looks are too far
 * apart to call it movement, it is simply not drawn. */
function boxAt(entry, now) {
  const looks = entry.k || [];
  if (!looks.length) return null;
  if (now < looks[0][0] - A_LOOK) return null;
  if (now > looks[looks.length - 1][0] + A_LOOK) return null;
  const after = looks.findIndex(look => look[0] >= now);
  if (after < 0) return looks[looks.length - 1].slice(1);
  if (after === 0) return looks[0].slice(1);
  const before = looks[after - 1], next = looks[after];
  const span = next[0] - before[0];
  if (span > A_STRIDE) {
    if (now - before[0] <= A_LOOK) return before.slice(1);
    if (next[0] - now <= A_LOOK) return next.slice(1);
    return null;
  }
  const part = span > 0 ? (now - before[0]) / span : 0;
  return [1, 2, 3, 4].map(i => before[i] + (next[i] - before[i]) * part);
}

const sameSighting = (entry, animal) =>
  Boolean(animal) && entry.t === animal.t && Math.abs(entry.s - animal.s) < 0.06;

/** Draw every animal that belongs on screen at the moment being played. */
function paintBoxes(force) {
  const svg = el('stageBoxes');
  if (!svg) return;
  // Until the video has data and has got to where it was asked to start, its
  // clock reads zero - and boxes drawn for the first second of a recording are
  // some other animal's, or none. The second somebody clicked is the truth until
  // the footage catches up with it.
  const video = el('stagePlay').querySelector('video');
  const live = video && video.readyState >= 2 && video.currentTime > 0;
  const now = live ? video.currentTime : (state.staged ? state.staged.animal.s : 0);
  if (!force && Math.abs(now - state.painted) < 0.04) return;
  state.painted = now;
  const focus = state.staged && state.staged.animal;
  const gone = new Set(state.rects.keys());
  (state.reelNow || []).forEach((entry, index) => {
    const box = boxAt(entry, now);
    if (!box) return;
    gone.delete(index);
    let drawn = state.rects.get(index);
    if (!drawn) {
      drawn = { rect: document.createElementNS(SVGNS, 'rect'),
                label: document.createElementNS(SVGNS, 'text') };
      drawn.rect.addEventListener('click', () => pickFromReel(entry));
      // Only the animal in focus is named on screen. A crowded seabed puts
      // twenty-five boxes up at once, and twenty-five names over them is a wall of
      // text with a dive behind it - so the rest say who they are when pointed at.
      drawn.rect.addEventListener('mouseenter', () => {
        drawn.label.textContent = entry.t;
      });
      drawn.rect.addEventListener('mouseleave', () => {
        drawn.label.textContent = sameSighting(entry, state.staged
          && state.staged.animal) ? entry.t : '';
      });
      const title = document.createElementNS(SVGNS, 'title');
      title.textContent = `${entry.t} — ${sure(entry.c)} certainty, ${clock(entry.s)}`;
      drawn.rect.appendChild(title);
      state.rects.set(index, drawn);
      svg.append(drawn.rect, drawn.label);
    }
    const here = sameSighting(entry, focus);
    if (drawn.label.textContent !== entry.t || !here) {
      drawn.label.textContent = here ? entry.t : '';
    }
    drawn.rect.setAttribute('x', String(box[0]));
    drawn.rect.setAttribute('y', String(box[1]));
    drawn.rect.setAttribute('width', String(Math.max(1, box[2] - box[0])));
    drawn.rect.setAttribute('height', String(Math.max(1, box[3] - box[1])));
    drawn.rect.setAttribute('class', here ? 'here' : '');
    drawn.label.setAttribute('x', String(box[0]));
    drawn.label.setAttribute('y', String(Math.max(9, box[1] - 3)));
    drawn.label.setAttribute('class', here ? 'here' : '');
  });
  for (const index of gone) {
    const drawn = state.rects.get(index);
    drawn.rect.remove();
    drawn.label.remove();
    state.rects.delete(index);
  }
}

/** Follow the playhead for as long as the stage is open. */
function followAlong() {
  if (el('stage').hidden) return;
  paintBoxes(false);
  if (window.requestAnimationFrame) requestAnimationFrame(followAlong);
}

/** Take over the stage with another animal from the same recording.
 *
 * No seeking: the reader is watching, and this animal is on screen now. The way
 * back to its own moment is the button in the corner. */
async function pickFromReel(entry) {
  const staged = state.staged;
  if (!staged || !staged.animal) return;
  const own = (entry.k || []).find(look => Math.abs(look[0] - entry.s) < 0.06)
    || (entry.k || [])[0] || [];
  const animal = { t: entry.t, e: staged.animal.e, r: staged.animal.r, s: entry.s,
                   c: entry.c, d: entry.d, n: entry.n, a: 1, b: own.slice(1) };
  const still = await stillOf(entry.g, entry.p, entry.at);
  openStage(animal, entry.g, entry.p, entry.at, still);
}

/** One tile's picture, cut out of the page it lives on. */
async function stillOf(slug, page, at) {
  try {
    const { animals, stills } = await pageOf(slug, page);
    const animal = animals[at];
    if (!animal) return '';
    let from = 0;
    for (const before of animals.slice(0, at)) from += before.l;
    return asPicture(stills.slice(from, from + animal.l));
  } catch { return ''; }
}

function shutStage() {
  clearInterval(state.filmTimer);
  state.filmTimer = null;
  const video = el('stagePlay').querySelector('video');
  if (video) {                    // or it goes on fetching the recording unwatched
    video.pause();
    video.removeAttribute('src');
    video.load();
  }
  el('stagePlay').replaceChildren();
  el('stage').hidden = true;
  state.staged = null;
  state.rects = new Map();
  writeHash();
}

/** The sighting the address bar asks for, if it asks for one.
 *
 * A link to this page is a link to what was on the screen: `#a=EX2301/…mp4/259.4`
 * opens that recording there, with that animal in focus, which is what somebody
 * sending the link meant to send. */
const askedFor = () =>
  new URLSearchParams((location.hash || '').slice(1)).get('a') || '';

async function openAsked(asked) {
  if (!asked) return;
  const [expedition, recording, second] = asked.split('/');
  if (!expedition || !recording || second === undefined) return;
  const reel = await reelOf({ e: expedition, r: recording });
  const entry = reel.find(one => Math.abs(one.s - Number(second)) < 0.06);
  if (!entry) return;
  state.staged = { animal: { e: expedition, r: recording } };   // for pickFromReel
  state.reelNow = reel;
  await pickFromReel(entry);
}

/* ── what somebody kept ───────────────────────────────────────────────────── */

/* In this browser and nowhere else. There is no account to have and nothing is
   sent anywhere, so `localStorage` is the whole of it - which also means a reader
   who clears their browser loses them, and the CSV is how they keep them. */
const KEPT = 'pp-deepsea-kept';
const keyOf = (animal) => `${animal.e}|${animal.r}|${animal.s}|${animal.t}`;

function kept() {
  try { return JSON.parse(localStorage.getItem(KEPT)) || []; } catch { return []; }
}

function refreshKept() {
  state.keptKeys = new Set(kept().map(entry => entry.k));
}

function keepThese(list) {
  try { localStorage.setItem(KEPT, JSON.stringify(list)); }
  catch { /* a private window, or a full one. The page still works. */ }
  refreshKept();
  markKept();
  drawKept();
}

/** Star or unstar one sighting.
 *
 * Enough of it is written down to find the moment again in the archive - the
 * recording, the second, the box - and where it sits in the store, so its picture
 * can be drawn again without keeping a copy of it. */
function toggleKept(animal, slug, page, at) {
  const list = kept(), key = keyOf(animal);
  const found = list.findIndex(entry => entry.k === key);
  if (found >= 0) list.splice(found, 1);
  else list.push({ k: key, slug, page, at, t: animal.t, e: animal.e, r: animal.r,
                   s: animal.s, c: animal.c, d: animal.d || 0, n: animal.n,
                   b: animal.b || [], v: fileOf(animal), f: frameOf(animal) });
  keepThese(list);
}

function markKept() {
  for (const star of document.querySelectorAll('.star[data-key]')) {
    const on = state.keptKeys.has(star.dataset.key);
    star.classList.toggle('on', on);
    star.textContent = on ? '★' : '☆';
    star.title = on ? 'kept - click to forget it' : 'keep this sighting';
  }
}

/** The favourites, drawn from the store rather than from a copy of the pictures.
 *
 * Built whole and then put in place, because starring two tiles quickly had two of
 * these running at once: both cleared the wall, both waited on a page, and both
 * appended what they had, so a reader with one favourite was shown two of it.
 *
 * Shows what a link brought where there is one, and what this browser kept where
 * there is not. */
async function drawKept() {
  const mine = ++state.drawingKept;
  const theirs = state.shared;
  const list = theirs || kept();
  el('kept').hidden = !list.length;
  el('keptCsv').href = list.length ? asCsv(list) : '#';
  sayWhoseTheseAre(list, Boolean(theirs));
  const tiles = [];
  for (const entry of list) {
    try {
      const found = theirs ? await foundAgain(entry) : await fromTheStore(entry);
      if (!found) continue;
      tiles.push(tileFor(found.animal, found.still, found.slug, found.page, found.at));
    } catch { /* a favourite whose page will not load is one tile fewer */ }
  }
  if (mine !== state.drawingKept) return;     // a later draw has taken over
  el('keptWall').replaceChildren(...tiles);
  markKept();
}

/** One of this browser's own favourites, by where it sits in the store. */
async function fromTheStore(entry) {
  const { animals, stills } = await pageOf(entry.slug, entry.page);
  const animal = animals[entry.at];
  if (!animal) return null;
  let at = 0;
  for (const before of animals.slice(0, entry.at)) at += before.l;
  return { animal, still: stills.slice(at, at + animal.l),
           slug: entry.slug, page: entry.page, at: entry.at };
}

/** Whose collection is on screen, and what can be done with it. */
function sayWhoseTheseAre(list, theirs) {
  el('keptTitle').textContent = theirs ? 'Sent to you' : 'Your favourites';
  el('keptWhat').textContent = theirs
    ? `${list.length} sighting${list.length === 1 ? '' : 's'} somebody put in a link. `
      + 'They are not in this browser until you keep them, and keeping them does not '
      + 'lose whatever you had.'
    : 'Starred sightings, kept in this browser and sent nowhere. The CSV carries the '
      + 'name, the recording, the second and the box the detector drew, which is '
      + "enough for somebody else to find the moment in the archive's own file.";
  el('keptTake').hidden = !theirs;
  el('keptMine').hidden = !theirs;
  el('keptClear').hidden = theirs;
  el('keptShare').hidden = theirs;
}

/** Take a shared collection into this browser, on top of what is already here. */
async function takeShared() {
  const theirs = state.shared || [];
  const list = kept();
  const have = new Set(list.map(entry => entry.k));
  for (const share of theirs) {
    const found = await foundAgain(share);
    if (!found) continue;
    const animal = found.animal;
    const key = keyOf(animal);
    if (have.has(key)) continue;
    have.add(key);
    list.push({ k: key, slug: found.slug, page: found.page, at: found.at,
                t: animal.t, e: animal.e, r: animal.r, s: animal.s, c: animal.c,
                d: animal.d || 0, n: animal.n, w: animal.w || '', z: animal.z,
                b: animal.b || [], v: fileOf(animal), f: frameOf(animal) });
  }
  state.shared = null;
  history.replaceState(null, '', location.href.split('#')[0]);
  keepThese(list);
}

/* ── sending them to somebody ──────────────────────────────────────────────── */

/* A shared collection travels in the link itself. There is no server here and
   nothing to host: the page is a folder, so a favourite cannot be given an id
   somewhere and fetched back. What it can be is written into the address.
 *
 * Four fields an animal is found again by - expedition, recording, second, name -
 * deflated and base64'd, which on a NOAA collection is mostly one prefix repeated
 * and squeezes to about a tenth. Twenty favourites make a link of a few hundred
 * characters. The store can be rebuilt underneath it and the link still resolves,
 * because none of those four is a position in a file. */

const shareOf = (entry) => [entry.e, entry.r, entry.s, entry.t];

function toBase64Url(bytes) {
  let binary = '';
  for (let at = 0; at < bytes.length; at += 0x8000) {
    binary += String.fromCharCode(...bytes.subarray(at, at + 0x8000));
  }
  return btoa(binary).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}

function fromBase64Url(text) {
  const padded = String(text).replace(/-/g, '+').replace(/_/g, '/');
  const binary = atob(padded + '='.repeat((4 - padded.length % 4) % 4));
  return Uint8Array.from(binary, ch => ch.charCodeAt(0));
}

/** The kept list as one string for the address bar: `z` deflated, `p` plain. */
async function packed(list) {
  const bytes = new TextEncoder().encode(JSON.stringify(list.map(shareOf)));
  if (!window.CompressionStream) return `p${toBase64Url(bytes)}`;
  try {
    const squeezed = await new Response(new Blob([bytes]).stream()
      .pipeThrough(new CompressionStream('deflate-raw'))).arrayBuffer();
    return `z${toBase64Url(new Uint8Array(squeezed))}`;
  } catch {
    return `p${toBase64Url(bytes)}`;
  }
}

async function unpacked(payload) {
  const body = fromBase64Url(String(payload).slice(1));
  const bytes = String(payload)[0] === 'z'
    ? new Uint8Array(await new Response(new Blob([body]).stream()
        .pipeThrough(new DecompressionStream('deflate-raw'))).arrayBuffer())
    : body;
  return JSON.parse(new TextDecoder().decode(bytes))
    .map(([e, r, second, t]) => ({ e, r, s: Number(second), t }));
}

/** A link that carries these sightings, whoever opens it. */
async function linkTo(list) {
  const here = location.href.split('#')[0];
  return `${here}#k=${encodeURIComponent(await packed(list))}`;
}

/** One shared sighting, found again in this collection.
 *
 * By what it is and when, not by where it sits in the store: the pages are cut by
 * confidence and a rebuild moves everything, so a link that named a page and a
 * place would rot the next time the collection grew. */
async function foundAgain(share) {
  const reel = await reelOf(share);
  const near = (one) => Math.abs(one.s - share.s) < 0.06;
  const entry = reel.find(one => one.t === share.t && near(one)) || reel.find(near);
  if (!entry) return null;
  const { animals, stills } = await pageOf(entry.g, entry.p);
  const animal = animals[entry.at];
  if (!animal) return null;
  let from = 0;
  for (const before of animals.slice(0, entry.at)) from += before.l;
  return { animal, still: stills.slice(from, from + animal.l),
           slug: entry.g, page: entry.p, at: entry.at };
}

/** Open what a link brought: theirs, beside yours rather than over it.
 *
 * Somebody else's collection does not overwrite the one in this browser - it is
 * shown as what it is, with a button that takes it. Nobody should lose their own
 * list by following a link. */
async function openShared(payload) {
  if (!payload) return;
  try {
    state.shared = await unpacked(payload);
  } catch {
    state.shared = null;
    return;
  }
  drawKept();
  el('kept').scrollIntoView({ behavior: 'smooth', block: 'start' });
}

/** The favourites as a file somebody else could use.
 *
 * The recording, the second and the box are the useful part: with those three a
 * reader opens the archive's own file and finds the same animal, whatever happens
 * to this page. */
function asCsv(list) {
  if (state.csvUrl) URL.revokeObjectURL(state.csvUrl);
  state.csvUrl = URL.createObjectURL(new Blob([csvText(list)], { type: 'text/csv' }));
  return state.csvUrl;
}

/** Those same favourites as the text of the file.
 *
 * Apart from `asCsv` because a blob URL is the one thing about the file a test
 * cannot read back, and a test that writes the rows out a second time to check
 * them is not checking anything - it reproduced this file's own frame-size bug
 * faithfully for as long as both were wrong. */
function csvText(list) {
  // `taxon_is_a_guess` is a column rather than a footnote because a CSV is the one
  // thing here that leaves and gets read somewhere else, by somebody who never saw
  // the disclaimer. Same for whose footage each row came from.
  // The page says 94%; a file somebody loads into something else wants the number
  // the detector actually produced, and a header that says which it is.
  const head = ['taxon', 'taxon_is_a_guess', 'certainty_0_to_1', 'seconds_in_view', 'frames',
                'expedition', 'recording', 'second', 'when', 'depth_m',
                'x0', 'y0', 'x1', 'y1', 'frame_width', 'frame_height', 'video',
                'credit', 'licence'];
  const rows = [head, ...list.map(entry => {
    const box = (entry.b || []).concat(['', '', '', '']).slice(0, 4);
    // The box's own coordinate system, which is a fact about the recording and
    // lives with the recording. `entry.f` is where the tile's film went in the
    // store, and writing those two offsets down as a frame size - which is what
    // this did - makes every box in the file unreadable.
    const frame = frameOf(entry);
    const terms = (state.index.where || {})[entry.e] || {};
    return [entry.t, 'yes, from an object detector', entry.c, entry.d, entry.n,
            entry.e, entry.r, entry.s, entry.w || '',
            entry.z === undefined || entry.z === null ? '' : entry.z,
            ...box, ...frame, entry.v || '',
            terms.credit || '', terms.licence || ''];
  })];
  return rows.map(row => row.map(cell => {
    const text = cell === undefined || cell === null ? '' : String(cell);
    return /[",\n]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
  }).join(',')).join('\n');
}

/** The controls that are on the page once rather than once per tile. */
function wireTheStage() {
  el('stageShut').addEventListener('click', shutStage);
  el('stage').addEventListener('click', (event) => {
    if (event.target === el('stage')) shutStage();
  });
  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape' && !el('stage').hidden) shutStage();
  });
  el('stageBox').addEventListener('change', () => {
    el('stagePlay').classList.toggle('no-box', !el('stageBox').checked);
  });
  el('stageBack').addEventListener('click', () => {
    const video = el('stagePlay').querySelector('video');
    if (!video || !state.staged) return;
    video.currentTime = Math.max(0, (state.staged.animal.s || 0) - LEAD_IN);
    video.play().catch(() => { /* a paused video at the right second is fine */ });
  });
  el('stageStar').addEventListener('click', () => {
    const staged = state.staged;
    if (staged) toggleKept(staged.animal, staged.slug, staged.page, staged.at);
  });
  el('keptClear').addEventListener('click', () => keepThese([]));
  el('keptShare').addEventListener('click', shareThese);
  el('keptTake').addEventListener('click', takeShared);
  el('keptMine').addEventListener('click', () => {
    state.shared = null;
    history.replaceState(null, '', location.href.split('#')[0]);
    drawKept();
  });
}

/** Put a link to this collection where it can be copied.
 *
 * Written into a field as well as onto the clipboard: a browser can refuse the
 * clipboard, and "copied" with nothing copied is worse than showing the link. */
async function shareThese() {
  const list = kept();
  if (!list.length) return;
  const url = await linkTo(list);
  el('keptLink').hidden = false;
  el('keptUrl').value = url;
  el('keptUrl').select();
  try {
    await navigator.clipboard.writeText(url);
    el('keptSaid').textContent = `copied · ${list.length} sightings`;
  } catch {
    el('keptSaid').textContent = 'select it and copy';
  }
}

const escape = (text) => String(text).replace(/[<>&]/g, ch =>
  ({ '<': '&lt;', '>': '&gt;', '&': '&amp;' })[ch]);

const clock = (seconds) => {
  const whole = Math.floor(seconds);
  return `${String(Math.floor(whole / 60)).padStart(2, '0')}:${String(whole % 60).padStart(2, '0')}`;
};

boot();
