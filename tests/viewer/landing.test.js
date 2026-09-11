/** The collection page's own script: the ways into the taxonomy, and the way back.
 *
 * The page is written by `landing.py` and its script lives in that file as a string,
 * because the page is one file a browser can open off a disk with no build step.
 * That is no reason for it to be the only untested code in the project: the string
 * is plain ES, so the test reads it out and runs it against a DOM and a stubbed
 * store, which is exactly what the browser does.
 *
 * What is checked is the behaviour that was asked for and was not there: a button
 * per phylum with everything among them, and a click in the middle of the ring
 * going back out a level the way the report's own sunburst does.
 */
import { describe, it, expect, beforeEach } from 'vitest';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';

// From the project root, where vitest runs: in a DOM environment `import.meta.url`
// is an http URL and readFileSync will not take one.
const SOURCE = join(process.cwd(), 'src/pixel_patrol_deepsea/landing.py');

/** The page's script, out of the Python that writes it. */
function pageScript() {
  const python = readFileSync(SOURCE, 'utf8');
  const after = python.split('SCRIPT = r"""')[1];
  return after.slice(0, after.lastIndexOf('"""'));
}

/* A collection with two kingdoms, four phyla and the bucket for names the register
   does not carry - the shape `tiles.build` writes, small enough to assert on. */
const INDEX = {
  tree: {
    name: 'everything', count: 100, children: [
      { name: 'Animalia', count: 80, children: [
        { name: 'Cnidaria', count: 50, taxa: ['Cnidaria'], children: [
          // A name sits at the node its whole lineage ends in, the way `_tree`
          // places it: the order, under the class, under the phylum.
          { name: 'Hexacorallia', count: 30, children: [
            { name: 'Actiniaria', count: 30, taxa: ['Actiniaria'] }] }] },
        { name: 'Porifera', count: 30, taxa: ['Porifera'] }] },
      { name: 'Chromista', count: 12, children: [
        { name: 'Foraminifera', count: 12, taxa: ['Foraminifera'] }] },
      { name: 'Unplaced', count: 8, children: [
        { name: 'Undecided', count: 6, taxa: ['Undecided'] },
        { name: 'LRJ complex', count: 2, taxa: ['LRJ complex'] }] },
    ],
  },
  where: {
    EX2503: { frame: [640, 360],
              videos: { 'EX2503_VID_20250413T012000Z_ROVHD_Low.mp4': 'https://ncei/ex2503.mp4' } },
    DSMOT: { frame: [1920, 1080], videos: {} },
  },
  taxa: {
    Actiniaria: { slug: 'actiniaria', count: 30, pages: 1, rank: 'Order',
                  aphia: 1360, above: ['Animalia', 'Cnidaria', 'Hexacorallia', 'Actiniaria'] },
    Cnidaria: { slug: 'cnidaria', count: 20, pages: 1, rank: 'Phylum', aphia: 1267,
                above: ['Animalia', 'Cnidaria'] },
    Porifera: { slug: 'porifera', count: 30, pages: 1, rank: 'Phylum', aphia: 558 },
    Foraminifera: { slug: 'foraminifera', count: 12, pages: 1 },
    Undecided: { slug: 'undecided', count: 6, pages: 1 },
    'LRJ complex': { slug: 'lrj-complex', count: 2, pages: 1 },
  },
};

/* What `fetch_wikipedia` found: a title where there is an article, nothing where
   there is not. Holothuroidea-style redirects are the point of storing the title. */
const LOOKUP = { Cnidaria: 'Cnidaria', Hexacorallia: 'Hexacorallia',
                 Actiniaria: 'Sea anemone' };

/* One page of one taxon, the shape `_write_page` writes: a still of one byte each,
   a duration, a box, and the recording it came out of. */
const ANIMALS = [
  { t: 'Actiniaria', e: 'EX2503', r: 'EX2503_VID_20250413T012000Z_ROVHD_Low.mp4',
    s: 124.5, c: 0.91, a: 1, n: 4, d: 7.5, b: [100, 40, 180, 130], l: 1, m: 0 },
  { t: 'Actiniaria', e: 'DSMOT', r: 'MD_BTL.mp4',
    s: 12.0, c: 0.7, a: 1, n: 1, d: 0, b: [10, 10, 60, 50], l: 1, m: 0 },
];

/** The store, answered out of memory: an index, and a page of pictures. */
const store = (url) => {
  if (url.endsWith('index.json')) return Promise.resolve({ json: async () => INDEX });
  if (url.endsWith('.json')) return Promise.resolve({
    json: async () => (url.includes('actiniaria') ? ANIMALS : []) });
  return Promise.resolve({ ok: true, arrayBuffer: async () => new ArrayBuffer(2) });
};

function page() {
  localStorage.clear();
  document.body.innerHTML = `
    <section id="explore">
      <nav id="jumps"></nav><nav id="crumbs"></nav>
      <p id="sunRead"></p>
      <svg id="sunburst"></svg>
      <aside id="about"></aside>
      <div id="wall"><div id="more"></div></div>
    </section>
    <section id="kept" hidden><a id="keptCsv" href="#"></a>
      <button id="keptClear"></button><div id="keptWall"></div></section>
    <div class="stage" id="stage" hidden><div class="stage-box">
      <p><b id="stageName"></b><span id="stageFacts"></span>
        <button class="star" id="stageStar"></button>
        <button id="stageShut"></button></p>
      <div class="stage-play" id="stagePlay"></div>
      <p><input type="checkbox" id="stageBox" checked>
        <a id="stageFile"></a><span id="stageNote"></span></p>
    </div></div>`;
  const run = new Function('LOOKUP', 'fetch', pageScript()
    + '\n; return { state, focusOn, drawJumps, nodeAt, openStage, toggleKept, '
    + 'kept, asCsv, wireTheStage, refreshKept, drawKept, shutStage, tileFor };');
  return run(LOOKUP, store);
}

const chips = () => [...document.getElementById('jumps').children]
  .map(b => b.textContent.trim().split(/\s+/)[0]);
const hole = () => document.querySelector('#sunburst .hole');

describe('the ways into the taxonomy', () => {
  let api;
  beforeEach(() => { api = page(); api.state.index = INDEX; api.drawJumps(); });

  it('offers everything and then one phylum each, biggest first', () => {
    // Not "the four biggest branches": those were a kingdom, a phylum and a class
    // in one row, which is three ranks and no grouping anybody can follow.
    expect(chips()).toEqual(['everything', 'Cnidaria', 'Porifera', 'Foraminifera',
                             'Unplaced']);
  });

  it('does not repeat in the row what is one click inside it', () => {
    // Undecided lives in Unplaced, and a button for each put the part beside the
    // whole with nothing saying which was which.
    expect(chips()).not.toContain('Undecided');
    expect(chips()).not.toContain('Animalia');
    expect(chips()).not.toContain('Hexacorallia');
  });

  it('marks the phylum the wall is showing, however deep inside it you are', () => {
    api.focusOn(['Animalia', 'Cnidaria', 'Hexacorallia']);
    const on = [...document.getElementById('jumps').children]
      .filter(b => b.classList.contains('on'))
      .map(b => b.dataset.path);
    expect(on).toEqual(['Animalia/Cnidaria']);
  });

  it('counts the whole collection on the button for it', () => {
    expect(document.getElementById('jumps').firstChild.textContent).toContain('100');
  });
});

describe('the middle of the ring', () => {
  let api;
  beforeEach(() => { api = page(); api.state.index = INDEX; api.drawJumps(); });

  it('goes back out a level, the way the report does', () => {
    api.focusOn(['Animalia', 'Cnidaria']);
    hole().dispatchEvent(new Event('click'));
    expect(api.state.path).toEqual(['Animalia']);
  });

  it('says where it is going, and keeps going until it is out', () => {
    api.focusOn(['Animalia', 'Cnidaria', 'Hexacorallia']);
    expect(hole().querySelector('title').textContent).toBe('back to Cnidaria');
    hole().dispatchEvent(new Event('click'));
    hole().dispatchEvent(new Event('click'));
    hole().dispatchEvent(new Event('click'));
    expect(api.state.path).toEqual([]);
  });

  it('is not a button at the root, where there is nowhere above', () => {
    api.focusOn([]);
    expect(hole().classList.contains('up')).toBe(false);
    expect(document.querySelector('#sunburst .back')).toBeNull();
  });

  it('widens a branch narrowed to one name rather than leaving it', () => {
    // `focusOn(path, only)` is a click on the grey arc: the animals named at this
    // rank and no deeper. Coming out of that is one step, not two.
    api.focusOn(['Animalia', 'Cnidaria'], 'Cnidaria');
    expect(api.state.only).toBe('Cnidaria');
    hole().dispatchEvent(new Event('click'));
    expect(api.state.only).toBeNull();
    expect(api.state.path).toEqual(['Animalia', 'Cnidaria']);
  });
});

describe('where the page sends a reader to read about a branch', () => {
  let api;
  beforeEach(() => { api = page(); api.state.index = INDEX; api.drawJumps(); });

  it('links the register by identifier, not by search', () => {
    api.focusOn(['Animalia', 'Cnidaria']);
    const about = document.getElementById('about');
    expect(about.textContent).toContain('Phylum');
    expect(about.querySelector('.links a').href).toContain('id=1267');
    expect(about.querySelector('.links a').textContent).toContain('WoRMS record');
  });

  it('offers the article it was told exists, under the title it has', () => {
    // `Actiniaria` is a redirect to *Sea anemone*, which is the word somebody
    // clicking on a Latin name was looking for.
    api.focusOn(['Animalia', 'Cnidaria', 'Hexacorallia', 'Actiniaria']);
    const wiki = [...document.querySelectorAll('#about .links a')]
      .find(a => a.href.includes('wikipedia'));
    expect(wiki.textContent).toContain('Sea anemone');
    expect(wiki.href).toContain('/wiki/Sea_anemone');
  });

  it('offers no article for a name that has none', () => {
    api.focusOn(['Animalia', 'Porifera']);
    const links = [...document.querySelectorAll('#about .links a')];
    expect(links.some(a => a.href.includes('wikipedia'))).toBe(false);
    expect(links).toHaveLength(1);
  });

  it('describes nothing in its own words, except the names it made up itself', () => {
    api.focusOn(['Animalia', 'Cnidaria', 'Hexacorallia']);
    expect(document.getElementById('about').querySelector('.hint')).toBeNull();
    api.focusOn(['Unplaced', 'Undecided']);
    expect(document.getElementById('about').textContent)
      .toContain('the name was dropped');
  });

  it('says there is nothing to look up for the detector\'s own classes', () => {
    api.focusOn(['Unplaced', 'LRJ complex']);
    const about = document.getElementById('about');
    expect(about.textContent).toContain('nothing to look up');
    expect(about.querySelector('a')).toBeNull();
  });

  it('reads out the count and the name above the ring', () => {
    api.focusOn(['Animalia', 'Porifera']);
    expect(document.getElementById('sunRead').textContent).toBe('30Porifera');
  });
});

describe('the ring is a half circle at every level', () => {
  let api;
  beforeEach(() => { api = page(); api.state.index = INDEX; api.drawJumps(); });

  /** The angle every wedge in the outermost ring covers, added up. */
  const rimAngle = () => {
    const rim = 26 + 26 * 3 - 2;
    let covered = 0;
    for (const path of document.querySelectorAll('#sunburst path')) {
      const d = path.getAttribute('d');
      const outer = Number(d.match(/A([\d.]+) /)[1]);
      if (Math.abs(outer - rim) > 0.01) continue;      // not a wedge that reaches
      const [x0, y0] = d.match(/^M([-\d.e]+) ([-\d.e]+)/).slice(1).map(Number);
      const [x1, y1] = d.match(/1 ([-\d.e]+) ([-\d.e]+)L/).slice(1).map(Number);
      covered += Math.atan2(x1, -y1) - Math.atan2(x0, -y0);
    }
    return covered;
  };

  it('fills the half at the root, where every branch goes deeper', () => {
    api.focusOn([]);
    expect(rimAngle()).toBeCloseTo(Math.PI, 2);
  });

  it('fills it where the naming stops one ring in', () => {
    // Porifera holds one name and no branches below it: without the grey the
    // drawing stopped at the first ring over that whole wedge, and the half circle
    // came out a ragged three-quarters.
    api.focusOn(['Animalia', 'Porifera']);
    expect(rimAngle()).toBeCloseTo(Math.PI, 2);
  });

  it('fills it under a branch whose animals are mostly named no further', () => {
    api.focusOn(['Animalia', 'Cnidaria']);
    expect(rimAngle()).toBeCloseTo(Math.PI, 2);
  });
});

describe('a tile is a way into the footage', () => {
  let api;
  beforeEach(() => {
    api = page();
    api.state.index = INDEX;
    api.refreshKept();
    api.wireTheStage();
  });

  const tile = (which = 0) =>
    api.tileFor(ANIMALS[which], new Uint8Array([255]), 'actiniaria', 0, which);

  it('opens the archive\'s own recording, a couple of seconds early', () => {
    api.openStage(ANIMALS[0], 'actiniaria', 0, 0, 'blob:still');
    expect(document.getElementById('stage').hidden).toBe(false);
    const video = document.querySelector('#stagePlay video');
    // 124.5 seconds in, opened at 122.5: an animal arriving on screen is most of
    // what tells a reader whether the box is around anything.
    expect(video.getAttribute('src')).toBe('https://ncei/ex2503.mp4#t=122.5');
    expect(document.getElementById('stageFile').getAttribute('href'))
      .toBe('https://ncei/ex2503.mp4#t=122.5');
  });

  it('draws the box in the coordinates of the frame it was drawn in', () => {
    api.openStage(ANIMALS[0], 'actiniaria', 0, 0, 'blob:still');
    const svg = document.querySelector('#stagePlay svg');
    expect(svg.getAttribute('viewBox')).toBe('0 0 640 360');
    const rect = svg.querySelector('rect');
    expect([rect.getAttribute('x'), rect.getAttribute('y'),
            rect.getAttribute('width'), rect.getAttribute('height')])
      .toEqual(['100', '40', '80', '90']);
  });

  it('shows the crop, and says so, when no URL was recorded', () => {
    api.openStage(ANIMALS[1], 'actiniaria', 0, 1, 'blob:still');
    expect(document.querySelector('#stagePlay video')).toBeNull();
    expect(document.querySelector('#stagePlay img').src).toContain('blob:still');
    expect(document.getElementById('stageNote').textContent).toContain('not the footage');
    expect(document.getElementById('stageFile').hidden).toBe(true);
  });

  it('stops fetching the recording when it is closed', () => {
    api.openStage(ANIMALS[0], 'actiniaria', 0, 0, 'blob:still');
    api.shutStage();
    expect(document.getElementById('stage').hidden).toBe(true);
    expect(document.querySelector('#stagePlay video')).toBeNull();
  });
});

describe('what somebody kept', () => {
  let api;
  beforeEach(() => {
    api = page();
    api.state.index = INDEX;
    api.refreshKept();
    api.wireTheStage();
  });

  it('keeps a sighting in this browser, and forgets it again', () => {
    api.toggleKept(ANIMALS[0], 'actiniaria', 0, 0);
    expect(api.kept().map(e => e.t)).toEqual(['Actiniaria']);
    expect(api.kept()[0]).toMatchObject({
      e: 'EX2503', s: 124.5, b: [100, 40, 180, 130], v: 'https://ncei/ex2503.mp4' });
    api.toggleKept(ANIMALS[0], 'actiniaria', 0, 0);
    expect(api.kept()).toEqual([]);
  });

  it('lights the star on a tile that was kept, and only that one', () => {
    api.toggleKept(ANIMALS[0], 'actiniaria', 0, 0);
    const first = api.tileFor(ANIMALS[0], new Uint8Array([255]), 'actiniaria', 0, 0);
    const second = api.tileFor(ANIMALS[1], new Uint8Array([255]), 'actiniaria', 0, 1);
    expect(first.querySelector('.star').classList.contains('on')).toBe(true);
    expect(second.querySelector('.star').classList.contains('on')).toBe(false);
  });

  it('starring a tile does not also open the footage', () => {
    const figure = api.tileFor(ANIMALS[0], new Uint8Array([255]), 'actiniaria', 0, 0);
    document.body.appendChild(figure);
    figure.querySelector('.star').dispatchEvent(
      new MouseEvent('click', { bubbles: true }));
    expect(api.kept()).toHaveLength(1);
    expect(document.getElementById('stage').hidden).toBe(true);
  });

  it('writes a csv a stranger could use to find the moment', () => {
    api.toggleKept(ANIMALS[0], 'actiniaria', 0, 0);
    // The page hands the browser a blob URL, which a test cannot read back, so the
    // rows are built here the same way and the columns checked against those.
    expect(api.asCsv(api.kept())).toMatch(/^blob:/);
    const rows = rowsOf(api.kept());
    expect(rows[0]).toContain('recording');
    expect(rows[0]).toContain('video');
    expect(rows[1]).toContain('EX2503_VID_20250413T012000Z_ROVHD_Low.mp4');
    expect(rows[1]).toContain('124.5');
    expect(rows[1]).toContain('100,40,180,130');
    expect(rows[1]).toContain('https://ncei/ex2503.mp4');
  });

  it('shows the favourites section only once there is something in it', async () => {
    expect(document.getElementById('kept').hidden).toBe(true);
    api.toggleKept(ANIMALS[0], 'actiniaria', 0, 0);
    await api.drawKept();
    expect(document.getElementById('kept').hidden).toBe(false);
    expect(document.querySelectorAll('#keptWall .tile')).toHaveLength(1);
  });
});

/** The CSV the page would write, as rows, without going through a blob URL. */
function rowsOf(list) {
  const head = ['taxon', 'confidence', 'seconds_in_view', 'looks', 'expedition',
                'recording', 'second', 'x0', 'y0', 'x1', 'y1',
                'frame_width', 'frame_height', 'video'];
  return [head.join(','), ...list.map(entry => [
    entry.t, entry.c, entry.d, entry.n, entry.e, entry.r, entry.s,
    ...(entry.b || []), ...(entry.f || []), entry.v].join(','))];
}
