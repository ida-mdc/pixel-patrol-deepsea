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

/** The store, answered out of memory: an index, and empty pages of pictures. */
const store = (url) => {
  if (url.endsWith('index.json')) return Promise.resolve({ json: async () => INDEX });
  if (url.endsWith('.json')) return Promise.resolve({ json: async () => [] });
  return Promise.resolve({ ok: true, arrayBuffer: async () => new ArrayBuffer(0) });
};

function page() {
  document.body.innerHTML = `
    <section id="explore">
      <nav id="jumps"></nav><nav id="crumbs"></nav>
      <p id="sunRead"></p>
      <svg id="sunburst"></svg>
      <aside id="about"></aside>
      <div id="wall"></div><div id="more"></div>
    </section>`;
  const run = new Function('LOOKUP', 'fetch', pageScript()
    + '\n; return { state, focusOn, drawJumps, nodeAt };');
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
                             'Unplaced', 'Undecided']);
  });

  it('does not offer a kingdom or anything under a phylum', () => {
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
