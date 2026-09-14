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
const SOURCE = join(process.cwd(), 'src/pixel_patrol_deepsea/page/landing.js');

/** The page's script, out of the Python that writes it. */
/** The page's script, read from the file it lives in.
 *
 * It used to be cut back out of `landing.py` with a string split, because that is
 * where it was kept. It is a `.js` file now, so this is just reading it. */
function pageScript() {
  return readFileSync(SOURCE, 'utf8');
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
              videos: { 'EX2503_VID_20250413T012000Z_ROVHD_Low.mp4': 'https://ncei/ex2503.mp4' },
              takes: { 'EX2503_VID_20250413T012000Z_ROVHD_Low.mp4': 'ex2503--dive' },
              licence: 'public domain', credit: 'NOAA Ocean Exploration' },
    DSMOT: { frame: [1920, 1080], videos: {}, takes: {},
             licence: 'CC BY-SA 4.0', credit: 'MBARI (DeepSea-MOT)' },
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
    s: 124.5, c: 0.91, a: 1, n: 4, d: 7.5, b: [100, 40, 180, 130], l: 1, m: 0,
    w: '2025-04-13T01:22:04+00:00', z: 1847.3 },
  { t: 'Actiniaria', e: 'DSMOT', r: 'MD_BTL.mp4',
    s: 12.0, c: 0.7, a: 1, n: 1, d: 0, b: [10, 10, 60, 50], l: 1, m: 0 },
];

/* One recording's reel, the shape `_write_reels` writes: every animal in it, and
   every look the detector had at each, in order. */
const REEL = [
  { t: 'Actiniaria', s: 124.5, c: 0.91, d: 7.5, n: 3, g: 'actiniaria', p: 0, at: 0,
    k: [[122.0, 90, 30, 170, 120], [124.5, 100, 40, 180, 130],
        [126.0, 110, 50, 190, 140]] },
  { t: 'Cnidaria', s: 127.0, c: 0.6, d: 0, n: 1, g: 'actiniaria', p: 0, at: 1,
    k: [[127.0, 400, 200, 460, 260]] },
  { t: 'Porifera', s: 300.0, c: 0.8, d: 2, n: 2, g: 'actiniaria', p: 0, at: 1,
    k: [[300.0, 10, 10, 60, 60], [302.0, 12, 12, 62, 62]] },
];

/** The store, answered out of memory: an index, a reel, and a page of pictures. */
const store = (url) => {
  if (url.endsWith('index.json')) return Promise.resolve({ json: async () => INDEX });
  if (url.includes('/reels/')) return Promise.resolve({ json: async () => REEL });
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
    <section id="kept" hidden><h2 id="keptTitle"></h2><p id="keptWhat"></p>
      <a id="keptCsv" href="#"></a><button id="keptClear"></button>
      <button id="keptShare"></button><button id="keptTake" hidden></button>
      <button id="keptMine" hidden></button>
      <p id="keptLink" hidden><input id="keptUrl"><span id="keptSaid"></span></p>
      <div id="keptWall"></div></section>
    <div class="stage" id="stage" hidden><div class="stage-box">
      <p><img id="stageCrop"><b id="stageName"></b><span id="stageFacts"></span>
        <span id="stageWhere"></span>
        <button class="star" id="stageStar"></button>
        <button id="stageShut"></button></p>
      <div class="stage-play" id="stagePlay"></div>
      <p><button id="stageBack"></button><input type="checkbox" id="stageBox" checked>
        <a id="stageFile"></a><span id="stageNote"></span></p>
      <p id="stageTerms"></p>
    </div></div>
    <div class="find"><input id="find"><ul class="found" id="findList" hidden></ul></div>`;
  const run = new Function('LOOKUP', 'fetch', pageScript()
    + '\n; return { state, focusOn, drawJumps, nodeAt, openStage, toggleKept, '
    + 'kept, asCsv, csvText, wireTheStage, refreshKept, drawKept, shutStage, tileFor, '
    + 'paintBoxes, boxAt, openAsked, askedFor, pickFromReel, packed, unpacked, '
    + 'linkTo, openShared, takeShared, shareThese, matchesFor, pickFound, '
    + 'wireFinding, whereEachNameIs };');
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

  it('draws the boxes in the coordinates of the frame they were drawn in', async () => {
    await api.openStage(ANIMALS[0], 'actiniaria', 0, 0, 'blob:still');
    expect(document.querySelector('#stagePlay svg').getAttribute('viewBox'))
      .toBe('0 0 640 360');
  });

  it('shows the crop, and says so, when no URL was recorded', () => {
    api.openStage(ANIMALS[1], 'actiniaria', 0, 1, 'blob:still');
    expect(document.querySelector('#stagePlay video')).toBeNull();
    expect(document.querySelector('#stagePlay img').src).toContain('blob:still');
    expect(document.getElementById('stageNote').textContent).toContain('not the footage');
    expect(document.getElementById('stageFile').hidden).toBe(true);
  });

  it('names the host when the recording will not play', () => {
    // Almost always the archive is having a day and nothing here is wrong: NCEI
    // answered 503 for every file under `/data/oceans` at one point, and the page
    // said only "the archive would not play this file here", which reads like the
    // page's own fault. A reader who can see whose host it was can check it.
    api.openStage(ANIMALS[0], 'actiniaria', 0, 0, 'blob:still');
    document.querySelector('#stagePlay video').dispatchEvent(new Event('error'));
    expect(document.getElementById('stageNote').textContent).toContain('ncei');
    // ...and the animal is still shown, from the crop the store already holds.
    expect(document.querySelector('#stagePlay video')).toBeNull();
    expect(document.querySelector('#stagePlay img')).toBeTruthy();
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
    // The page hands the browser a blob URL, which a test cannot read back; the
    // text it was made from is the same call one step earlier.
    expect(api.asCsv(api.kept())).toMatch(/^blob:/);
    const rows = api.csvText(api.kept()).split('\n');
    expect(rows[0]).toContain('recording');
    expect(rows[0]).toContain('video');
    expect(rows[1]).toContain('EX2503_VID_20250413T012000Z_ROVHD_Low.mp4');
    expect(rows[1]).toContain('124.5');
    expect(rows[1]).toContain('100,40,180,130');
    expect(rows[1]).toContain('https://ncei/ex2503.mp4');
  });

  it('gives the box the frame it was measured in, not the tile\'s film', () => {
    // `entry.f` is where that tile's frames went in the store. Written into the
    // file as the frame size - which is what this did - every box in it is
    // measured against a number that means nothing, and a reader scaling one onto
    // a video puts the rectangle somewhere else entirely.
    api.toggleKept(ANIMALS[0], 'actiniaria', 0, 0);
    const row = api.csvText(api.kept()).split('\n')[1];
    // The box, then the frame it was measured in, then the recording to scale it
    // onto: 640x360 is what the index says EX2503 was filmed at.
    expect(row).toContain('100,40,180,130,640,360,https://ncei/ex2503.mp4');
  });

  it('shows the favourites section only once there is something in it', async () => {
    expect(document.getElementById('kept').hidden).toBe(true);
    api.toggleKept(ANIMALS[0], 'actiniaria', 0, 0);
    await api.drawKept();
    expect(document.getElementById('kept').hidden).toBe(false);
    expect(document.querySelectorAll('#keptWall .tile')).toHaveLength(1);
  });
});


describe('the boxes, while the recording plays', () => {
  let api;
  /** A video with data in it, at a given second. */
  const playhead = (seconds) => {
    const video = document.querySelector('#stagePlay video');
    Object.defineProperty(video, 'readyState', { value: 4, configurable: true });
    video.currentTime = seconds;
    api.paintBoxes(true);
  };
  const drawn = () => [...document.querySelectorAll('#stagePlay svg rect')]
    .map(rect => ({
      name: rect.querySelector('title').textContent.split(' —')[0],
      here: rect.classList.contains('here'),
      box: ['x', 'y', 'width', 'height'].map(a => Number(rect.getAttribute(a))),
    }));

  beforeEach(async () => {
    api = page();
    api.state.index = INDEX;
    api.refreshKept();
    api.wireTheStage();
    await api.openStage(ANIMALS[0], 'actiniaria', 0, 0, 'blob:still');
  });
  afterEach(() => api.shutStage());

  it('draws the animal that was clicked, before the recording has loaded', () => {
    // A video with no data in it reads zero, and boxes drawn for the first second
    // of a dive belong to some other animal or to none. What was clicked is the
    // truth until the footage catches up with it.
    const video = document.querySelector('#stagePlay video');
    expect(video.readyState).toBe(0);
    api.paintBoxes(true);
    expect(drawn()).toEqual([
      { name: 'Actiniaria', here: true, box: [100, 40, 80, 90] }]);
  });

  it('draws the animal where it was at the second being played', () => {
    playhead(124.5);
    expect(drawn()).toEqual([
      { name: 'Actiniaria', here: true, box: [100, 40, 80, 90] }]);
  });

  it('moves the box between the looks the detector had', () => {
    // Halfway between the look at 122.0 and the look at 124.5. One box for the
    // whole sighting is wrong the moment anything swims.
    playhead(123.25);
    expect(drawn()[0].box).toEqual([95, 35, 80, 90]);
  });

  it('shows the other animals when they are on screen, and not before', () => {
    playhead(124.5);
    expect(drawn().map(one => one.name)).toEqual(['Actiniaria']);
    playhead(126.5);
    expect(drawn().map(one => one.name)).toEqual(['Actiniaria', 'Cnidaria']);
    // ...and the one three minutes later is nowhere near the screen.
    expect(drawn().map(one => one.name)).not.toContain('Porifera');
  });

  it('stops drawing an animal once the detector has stopped seeing it', () => {
    // The last look at this one was at 126.0. A box that hangs about after that is
    // a box around whatever happens to be there now.
    playhead(127.0);
    expect(drawn().map(one => one.name)).toEqual(['Cnidaria']);
  });

  it('draws nothing where nothing was seen', () => {
    playhead(200);
    expect(drawn()).toEqual([]);
  });

  it('hands over to another animal when its box is clicked, without seeking', async () => {
    playhead(127.0);
    const video = document.querySelector('#stagePlay video');
    const other = [...document.querySelectorAll('#stagePlay svg rect')]
      .find(rect => rect.querySelector('title').textContent.startsWith('Cnidaria'));
    other.dispatchEvent(new Event('click'));
    await new Promise(resolve => setTimeout(resolve, 0));
    expect(document.getElementById('stageName').textContent).toContain('Cnidaria');
    expect(video.currentTime).toBe(127.0);          // still watching, not re-seeked
    expect(document.querySelector('#stagePlay svg rect.here')
      .querySelector('title').textContent).toContain('Cnidaria');
  });

  it('puts the moment in the address bar, so the link is the moment', () => {
    expect(decodeURIComponent(location.hash)).toContain(
      'a=EX2503/EX2503_VID_20250413T012000Z_ROVHD_Low.mp4/124.5');
    api.shutStage();
    expect(location.hash).not.toContain('a=');
  });

  it('opens what the address bar asks for', async () => {
    api.shutStage();
    history.replaceState(null, '', '#a=' + encodeURIComponent(
      'EX2503/EX2503_VID_20250413T012000Z_ROVHD_Low.mp4/127'));
    // Read before the wall settles: `focusOn` rewrites the address bar at boot, and
    // the moment somebody was sent would be gone before it was opened.
    await api.openAsked(api.askedFor());
    expect(document.getElementById('stage').hidden).toBe(false);
    expect(document.getElementById('stageName').textContent).toContain('Cnidaria');
  });

  it('goes back to the moment it was opened on', () => {
    const video = document.querySelector('#stagePlay video');
    video.currentTime = 300;
    document.getElementById('stageBack').dispatchEvent(new Event('click'));
    expect(video.currentTime).toBe(122.5);          // the detection, two seconds early
  });
});

describe('a page that cannot read the store beside it', () => {
  /** The page, with a fetch that refuses the way a browser refuses. */
  const wallAfterBoot = async () => {
    document.body.innerHTML = '<div id="wall"><div id="more"></div></div>'
      + '<nav id="jumps"></nav><nav id="crumbs"></nav><p id="sunRead"></p>'
      + '<svg id="sunburst"></svg><aside id="about"></aside>'
      + '<section id="kept" hidden><a id="keptCsv"></a><button id="keptClear">'
      + '</button><div id="keptWall"></div></section>';
    const refuse = () => Promise.reject(new TypeError('Failed to fetch'));
    const run = new Function('LOOKUP', 'fetch', pageScript() + '\n; return { boot };');
    await run({}, refuse).boot();
    return document.getElementById('wall').textContent;
  };

  it('tells a served page the store is missing', async () => {
    expect(await wallAfterBoot()).toContain('collect site');
  });

  it('tells a page opened off a disk that that is the problem', async () => {
    // The same symptom - a page and no pictures - and the wrong fix is to rebuild
    // a store that is already sitting next to it.
    window.happyDOM.setURL('file:///somewhere/footage/index.html');
    try {
      const said = await wallAfterBoot();
      expect(said).toContain('opened from a disk');
      expect(said).toContain('http.server');
      expect(said).not.toContain('collect site');
    } finally {
      window.happyDOM.setURL('http://localhost/');
    }
  });
});

describe('what the overlay says about a sighting', () => {
  let api;
  beforeEach(async () => {
    api = page();
    api.state.index = INDEX;
    api.refreshKept();
    api.wireTheStage();
    await api.openStage(ANIMALS[0], 'actiniaria', 0, 0, 'blob:still');
  });
  afterEach(() => api.shutStage());

  it('says where and when it was, and how deep', () => {
    // A crop of a frame says none of this, and it is what anybody asks of a
    // deep-sea picture after what it is.
    const said = document.getElementById('stageWhere').textContent;
    expect(said).toContain('1847 m');
    expect(said).toContain('2025-04-13 01:22 UTC');
    expect(said).toContain('EX2503');
    expect(said).toContain('EX2503_VID_20250413T012000Z_ROVHD_Low.mp4');
  });

  it('leaves out a depth or a clock the report never had', async () => {
    await api.openStage(ANIMALS[1], 'actiniaria', 0, 1, 'blob:still');
    const said = document.getElementById('stageWhere').textContent;
    expect(said).toContain('DSMOT');
    expect(said).not.toContain('undefined');
    expect(said).not.toContain('NaN');
    expect(said).not.toMatch(/\bm\b/);
  });
});

describe('what the overlay says about whose picture it is', () => {
  let api;
  beforeEach(async () => {
    api = page();
    api.state.index = INDEX;
    api.refreshKept();
    api.wireTheStage();
    await api.openStage(ANIMALS[0], 'actiniaria', 0, 0, 'blob:still');
  });
  afterEach(() => api.shutStage());

  it('credits the archive the frame came from', () => {
    const said = document.getElementById('stageTerms').textContent;
    expect(said).toContain('NOAA Ocean Exploration');
    expect(said).toContain('public domain');
  });

  it('carries the share-alike one as share-alike', async () => {
    await api.openStage(ANIMALS[1], 'actiniaria', 0, 1, 'blob:still');
    const said = document.getElementById('stageTerms').textContent;
    expect(said).toContain('MBARI');
    expect(said).toContain('CC BY-SA 4.0');
  });

  it('says the name is a guess wherever it puts the name', () => {
    // Not only in a disclaimer at the top of a page somebody scrolled past. Where
    // the qualifier sits is the page author's to move - it has been a sentence under
    // the credit and it is now a prefix on the name itself - but a reader looking at
    // a picture must not be able to take the name for an identification.
    const named = document.getElementById('stageName').textContent;
    expect(named).toContain('Actiniaria');
    expect(named.toLowerCase()).toContain('guess');
  });

  it('puts the guess and the credit in the csv as columns', () => {
    api.toggleKept(ANIMALS[0], 'actiniaria', 0, 0);
    const rows = api.asCsv(api.kept());
    expect(rows).toMatch(/^blob:/);          // the page hands over a blob URL
    // The columns a stranger opens the file to, named in the page's own source.
    for (const column of ['taxon_is_a_guess', 'credit', 'licence', 'when', 'depth_m']) {
      expect(pageScript()).toContain(`'${column}'`);
    }
    expect(pageScript()).toContain("'yes, from an object detector'");
  });
});

describe('sending a collection to somebody', () => {
  let api;
  beforeEach(() => {
    api = page();
    api.state.index = INDEX;
    api.refreshKept();
    api.wireTheStage();
  });

  it('packs and unpacks the sightings, not where they sit in the store', async () => {
    // A store is rebuilt every time a collection grows and the pages are cut by
    // confidence, so a link that named a page and a place would rot. These four
    // fields find the animal again wherever it has moved to.
    api.toggleKept(ANIMALS[0], 'actiniaria', 0, 0);
    const payload = await api.packed(api.kept());
    expect(payload[0]).toMatch(/[zp]/);
    const back = await api.unpacked(payload);
    expect(back).toEqual([{ e: 'EX2503',
                            r: 'EX2503_VID_20250413T012000Z_ROVHD_Low.mp4',
                            s: 124.5, t: 'Actiniaria' }]);
  });

  it('makes a link that carries them', async () => {
    api.toggleKept(ANIMALS[0], 'actiniaria', 0, 0);
    const url = await api.linkTo(api.kept());
    expect(url).toContain('#k=');
    expect(url).not.toContain('undefined');
    const payload = decodeURIComponent(url.split('#k=')[1]);
    expect((await api.unpacked(payload))[0].t).toBe('Actiniaria');
  });

  it('squeezes a collection of many into a link somebody can send', async () => {
    // Twenty NOAA recordings are one long prefix twenty times over, which is what
    // deflate is for: the point of the exercise is that this fits in an address bar.
    const many = Array.from({ length: 20 }, (unused, i) => ({
      e: 'EX2503', r: `EX2503_VID_2025041${i % 10}T012000Z_ROVHD_Low.mp4`,
      s: 100 + i, t: 'Actiniaria' }));
    const url = await api.linkTo(many);
    expect(url.length).toBeLessThan(700);
  });

  it('shows what a link brought without touching what this browser kept', async () => {
    api.toggleKept(ANIMALS[1], 'actiniaria', 0, 1);          // mine
    const theirs = await api.packed([{ e: 'EX2503',
      r: 'EX2503_VID_20250413T012000Z_ROVHD_Low.mp4', s: 124.5, t: 'Actiniaria' }]);
    await api.openShared(theirs);
    expect(document.getElementById('keptTitle').textContent).toBe('Sent to you');
    expect(document.getElementById('keptWhat').textContent).toContain('somebody put in a link');
    expect(api.kept()).toHaveLength(1);                      // still only mine
    expect(api.kept()[0].t).toBe('Actiniaria');
    expect(api.kept()[0].e).toBe('DSMOT');
  });

  it('keeps a shared collection on top of your own when you ask', async () => {
    api.toggleKept(ANIMALS[1], 'actiniaria', 0, 1);
    await api.openShared(await api.packed([{ e: 'EX2503',
      r: 'EX2503_VID_20250413T012000Z_ROVHD_Low.mp4', s: 124.5, t: 'Actiniaria' }]));
    await api.takeShared();
    expect(api.kept().map(one => one.e).sort()).toEqual(['DSMOT', 'EX2503']);
    expect(document.getElementById('keptTitle').textContent).toBe('Your favourites');
  });

  it('takes a link that came from a browser with no compression in it', async () => {
    const plain = 'p' + btoa(JSON.stringify(
      [['EX2503', 'EX2503_VID_20250413T012000Z_ROVHD_Low.mp4', 124.5, 'Actiniaria']]))
      .replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
    expect((await api.unpacked(plain))[0].s).toBe(124.5);
  });

  it('says nothing and breaks nothing when the link is rubbish', async () => {
    await api.openShared('zzzz-not-a-payload');
    expect(api.state.shared).toBeNull();
  });
});

describe('finding a name', () => {
  let api;
  beforeEach(() => {
    api = page();
    api.state.index = INDEX;
    api.wireFinding();
  });

  const type = (what) => {
    const box = document.getElementById('find');
    box.value = what;
    box.dispatchEvent(new Event('input'));
    return [...document.getElementById('findList').children]
      .map(row => row.textContent);
  };

  it('offers the names that contain what was typed', () => {
    expect(api.matchesFor(INDEX.taxa, 'fera').map(h => h.name))
      .toEqual(['Porifera', 'Foraminifera']);
  });

  it('puts a name that starts with it above one that merely holds it', () => {
    // "cnid" should reach Cnidaria before anything that has it in the middle.
    const hits = api.matchesFor(INDEX.taxa, 'ni');
    expect(hits[0].name).toBe('Cnidaria');
  });

  it('breaks a tie on how much of it there is', () => {
    // Both start at the same place; thirty beats twenty.
    const hits = api.matchesFor({ Aa: { count: 20 }, Ab: { count: 30 } }, 'a');
    expect(hits.map(h => h.name)).toEqual(['Ab', 'Aa']);
  });

  it('cares nothing for case', () => {
    expect(api.matchesFor(INDEX.taxa, 'ACTIN').map(h => h.name)).toEqual(['Actiniaria']);
  });

  it('finds nothing for a name nobody gave', () => {
    expect(api.matchesFor(INDEX.taxa, 'tardigrada')).toEqual([]);
  });

  it('says so on screen rather than leaving an empty box', () => {
    expect(type('tardigrada').join()).toContain('no name like that was given');
  });

  it('shows the rank and the count beside each name', () => {
    const rows = type('actin');
    expect(rows[0]).toContain('Actiniaria');
    expect(rows[0]).toContain('Order');
    expect(rows[0]).toContain('30');
  });

  it('opens a name on the same path its own ring would', () => {
    // Read out of the tree, not out of the register's `above`: a species is filed
    // under its genus and its chain stops there, so the two disagree.
    api.pickFound('Actiniaria');
    expect(api.state.path).toEqual(['Animalia', 'Cnidaria', 'Hexacorallia', 'Actiniaria']);
    expect(api.state.only).toBe('Actiniaria');
  });

  it('finds a name the register could not place at all', () => {
    api.pickFound('Undecided');
    expect(api.state.path).toEqual(['Unplaced', 'Undecided']);
    expect(api.state.only).toBe('Undecided');
  });

  it('knows where every name in the tree lives', () => {
    const where = api.whereEachNameIs();
    expect([...where.keys()].sort()).toEqual(Object.keys(INDEX.taxa).sort());
  });

  it('shuts the list once a name has been taken', () => {
    type('actin');
    expect(document.getElementById('findList').hidden).toBe(false);
    api.pickFound('Actiniaria');
    expect(document.getElementById('findList').hidden).toBe(true);
    expect(document.getElementById('find').value).toBe('Actiniaria');
  });
});

describe('how sure the detector was', () => {
  let api;
  beforeEach(() => {
    api = page();
    api.state.index = INDEX;
    api.wireTheStage();
  });

  it('says it as a percentage, not as a number nobody explained', () => {
    // `0.91` beside a picture has no unit and no scale. Per cent needs no telling.
    const tile = api.tileFor(ANIMALS[0], new Uint8Array([255]), 'actiniaria', 0, 0);
    expect(tile.querySelector('.meta b').textContent).toBe('91%');
    expect(tile.title).toContain('91% certainty');
  });

  it('says the same thing over the picture as on the tile', async () => {
    await api.openStage(ANIMALS[0], 'actiniaria', 0, 0, 'blob:still');
    expect(document.getElementById('stageFacts').textContent).toContain('91% certainty');
  });

  it('keeps the detector\'s own number in the file somebody takes away', () => {
    // A page is read; a CSV is loaded into something else, which wants 0.91 and a
    // column name that says so.
    api.toggleKept(ANIMALS[0], 'actiniaria', 0, 0);
    const rows = api.csvText(api.kept()).split('\n');
    expect(rows[0]).toContain('certainty_0_to_1');
    expect(rows[1]).toContain('0.91');
  });
});
