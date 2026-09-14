/** Smoke tests: every widget draws something against a report-shaped context.
 *
 * The unit tests cover the window logic; nothing covered the wiring between it and
 * the DOM, which is where a renamed argument or a helper used before it is defined
 * shows up. These render each widget the way the viewer does and check it produced
 * a tile rather than an exception.
 */
import { describe, it, expect, beforeEach } from 'vitest';
import widgets from '../../src/pixel_patrol_deepsea/viewer/plugin_deepsea.js';

const COLUMNS = ['frame_difference', 'frame_difference_max', 'mean_intensity', 'fps',
                 'name', 'path', 'std_intensity', 'detection_count', 'detection_top_class',
                 'detection_confidence', 'detection_crop', 'slice_thumbnail',
                 'slice_red', 'slice_green', 'slice_blue',
                 // What the slice-location processor writes. Over-time needs one
                 // of them for an axis, and a located report has both.
                 'recorded_at', 'depth_m',
                 // What `triage.describe` writes: the verdict per slice and, on
                 // each recording's own row, the seconds it spent in each. A
                 // report without them is one the widgets cannot judge.
                 'slice_verdict', 'footage_seconds',
                 'verdict_seconds_frozen', 'verdict_seconds_subject',
                 'verdict_seconds_unnamed', 'verdict_seconds_dwell',
                 'verdict_seconds_empty', 'verdict_seconds_active'];

const TIMELINE = Array.from({ length: 12 }, (_, i) => ({
  t: i * 30,
  // The mock answers every query with these rows, so a column any widget aliases
  // has to be here or that widget silently reads nothing and renders "no data" -
  // which is indistinguishable from a widget that is actually broken.
  depth: 2200 + i * 100,
  when_: i * 0.5,
  stamp: 1745700000 + i * 300,
  grp: '',
  ...Object.fromEntries(Array.from({ length: 6 }, (_v, c) => [`c${c}`, 12])),
  low: 2200, high: 3400, seconds: 30,
  taxon: i % 3 === 0 ? 'beroe' : 'sea pen',
  animals: i + 1,
  movement: [0, 0, 0, 5, 5, 9, 9, 1, 1, 40, 5, 5][i],
  peak: 1,
  structure: 100,
  objects: 2,
  value: 120,
  bucket: i,
  channel: null,
  // Two sightings of two species, far enough apart to stay two events: one
  // recording with one event in it cannot show that a gallery of several tiles
  // shares one stylesheet.
  detections: [5, 6, 10].includes(i) ? 1 : 0,
  top_class: i === 5 || i === 6 ? 'beroe' : i === 10 ? 'sea pen' : null,
  confidence: i === 5 ? 0.91 : i === 6 ? 0.8 : i === 10 ? 0.7 : null,
  // What the report says each slice was doing. Decided in Python and read here,
  // so a mock report has to carry it or every widget that reads a verdict finds
  // an unjudged recording and draws nothing.
  verdict: [5, 6, 10].includes(i) ? 'subject'
           : [0, 1, 2].includes(i) ? 'frozen' : 'active',
  // The per-recording totals, on every row because the mock answers every query
  // with these and the widgets read them off whichever row they get.
  footage_seconds: 360,
  verdict_seconds_frozen: 60, verdict_seconds_subject: 90,
  verdict_seconds_unnamed: 0, verdict_seconds_dwell: 30,
  verdict_seconds_empty: 0, verdict_seconds_active: 180,
  // ...and under the names the summaries query gives them, since the mock hands
  // back these rows for every query rather than running the SQL.
  name: 'dive.mp4', total: 360,
  frozen: 60, subject: 90, unnamed: 0, dwell: 30, empty: 0, active: 180,
}));

const emptyArrow = { numRows: 0, schema: { fields: [] }, getChildAt: () => null };

/** The one query the mock answers with a recording rather than with slices.
 *
 * Matched on the column it selects, not on its GROUP BY: `GROUP BY 1, 2` also
 * matches `GROUP BY 1, 2, 3`, so adding a third grouping column to another query
 * silently handed it the recordings row and every widget downstream drew nothing.
 */
const isRecordingsQuery = (sql) => sql.includes('AS slices');

const context = () => ({
  schema: { allCols: COLUMNS, dimCols: ['dim_t'], blobCols: ['slice_thumbnail'] },
  // Mirrors the real ctx.sql, so a widget that misuses it fails here rather than
  // in a browser. andWhere and groupCol are what the distribution engine needs.
  sql: {
    q: (name) => `"${name}"`,
    dimSubsetWhere: () => ['"dim_t" IS NOT NULL'],
    andWhere: (where, condition) => (condition
      ? (where ? `${where} AND ${condition}` : `WHERE ${condition}`) : where),
    groupCol: () => '"name"',
  },
  data: { extractBinary: () => null },
  // The palette helpers, for the same reason as the engine stub below: a widget
  // that colours an ad-hoc grouping of its own reaches for these, and left
  // undefined they turn a card into a TypeError rather than a failing assertion.
  color: {
    group: () => '#4e79a7',
    getColors: (_palette, n) => Array.from({ length: n }, (_v, i) => `#${(i + 1) * 111111}`),
    getPaletteNames: () => ['tab10'],
    palette: 'tab10',
    hexToRgba: (hex) => hex,
  },

  plot: {
    escapeHtml: (value) => String(value).replace(/[<>&"]/g, ''),
    // Plotly draws into a div and hands it back with an .on() for click handlers.
    append(host, traces, layout) {
      const div = document.createElement('div');
      div.dataset.traces = String(traces.length);
      div.on = () => {};
      host.appendChild(div);
      return div;
    },
    appendMini(host, traces) {
      const div = document.createElement('div');
      div.dataset.traces = String(traces.length);
      host.appendChild(div);
      return div;
    },
    groupingLabel: () => '',
    niceName: (name) => String(name),
    // The layout helpers the viewer binds for plugins. Stubbed for the same
    // reason as the engine: a widget that lays its plots out through the shared
    // grid must not fail here because the mock only knew about drawing.
    flexGrid(host, perRow) {
      const wrap = document.createElement('div');
      host.appendChild(wrap);
      return { wrap, flexBasisPct: 100 / Math.max(1, perRow) };
    },
    invariantTable: () => {},
    dataAvailabilityWarning: () => {},
    // The shared distribution engine. Stubbed rather than left undefined, because
    // a widget that only reaches its own drawing code when this is present is a
    // widget the tests never run: `shortName is not defined` shipped behind an
    // early return here.
    engine: {
      async renderDistribution(host, ctx, spec) {
        ctx.asked?.push(`distribution:${spec.numCol}:${spec.source.table}`);
        const div = document.createElement('div');
        div.dataset.numCol = spec.numCol;
        host.appendChild(div);
        return true;
      },
    },
  },
  groups: [''],
  groupLabel: (g) => String(g),
  async queryRows(sql) {
    if (isRecordingsQuery(sql)) {
      return [{ name: 'dive.mp4', path: '/dives/dive.mp4', fps: 30, slices: TIMELINE.length }];
    }
    return TIMELINE;
  },
  async query() { return emptyArrow; },
});

// happy-dom has no 2D canvas, and the barcode is nothing but canvas drawing.
const DRAWS_ON_CANVAS = new Set(['temporal-barcode']);

describe.each(widgets.filter(w => !DRAWS_ON_CANVAS.has(w.id)).map(w => [w.id, w]))('%s', (id, widget) => {
  let container;
  beforeEach(() => { container = document.createElement('div'); });

  it('draws something for a report that has everything', async () => {
    await widget.render(container, context());
    expect(container.innerHTML).not.toBe('');
    expect(container.querySelector('.no-data')).toBe(null);
  });

  it('says so rather than throwing when there are no recordings', async () => {
    // Each widget explains its own emptiness in its own terms - the depth card
    // has no depths, the clock card no timestamps - so what is asserted is that
    // it said something rather than drawing an empty frame.
    const ctx = { ...context(), queryRows: async () => [] };
    await widget.render(container, ctx);
    expect(container.querySelector('.no-data')?.textContent ?? '').not.toBe('');
  });

  it('summarises itself for the overview without throwing', async () => {
    if (!widget.overviewMessage) return;
    const message = await widget.overviewMessage(context());
    expect(message === null || typeof message === 'string' || typeof message === 'object').toBe(true);
  });

  it('draws its preview tile, or says it drew nothing', async () => {
    // The tile is a second rendering of the same widget and nothing tested it, so
    // a preview could be broken - or hand-painted into something that looks
    // nothing like the rest of the gallery - with every other test still green.
    if (!widget.overviewPlot) return;
    const drew = await widget.overviewPlot(container, context());
    expect(typeof drew === 'boolean' || drew === undefined).toBe(true);
    if (drew) expect(container.childElementCount).toBeGreaterThan(0);
  });

  it('draws a measurement preview through the shared distribution engine', async () => {
    // A tile that summarises a per-slice measurement has to do it through the one
    // engine, so it picks bar, points, violin or box the way the rest of the
    // gallery does. The triage tile was a stacked ribbon painted here by hand,
    // which is how a preview ends up disagreeing with the card it opens.
    if (!PLOTS_A_MEASUREMENT.has(widget.id)) return;
    const ctx = context();
    ctx.asked = [];
    expect(await widget.overviewPlot(container, ctx)).toBe(true);
    expect(ctx.asked.some(q => q.startsWith('distribution:'))).toBe(true);
  });
});

// The previews that summarise a per-slice number, and so belong to the engine.
// Named rather than derived, because the rest are deliberate exceptions rather
// than oversights: a taxonomy is a sunburst, biodiversity over time is a stacked
// composition, the gallery is a contact sheet of animals and the timeline is the
// recording's colour strip. All of them still go through ctx.plot, so they are
// sized and styled like every other tile.
const PLOTS_A_MEASUREMENT = new Set(['temporal-triage']);

it('has a preview for every widget worth previewing', () => {
  // Guards the list above: renaming a widget would otherwise silently empty it
  // and take the assertion with it.
  const withPreviews = widgets.filter(w => w.overviewPlot).map(w => w.id);
  expect([...PLOTS_A_MEASUREMENT].every(id => withPreviews.includes(id))).toBe(true);
  expect(withPreviews.length).toBeGreaterThanOrEqual(PLOTS_A_MEASUREMENT.size);
});

describe('the gallery, in detail', () => {
  const gallery = widgets.find(w => w.id === 'temporal-gallery');

  it('drops the species filter for a report with no detector', async () => {
    const container = document.createElement('div');
    const bare = context();
    bare.schema = { ...bare.schema, allCols: ['frame_difference', 'fps', 'name', 'path'] };
    await gallery.render(container, bare);
    const selects = [...container.querySelectorAll('select')].filter(s => !s.hidden);
    expect(selects.some(s => [...s.options].some(o => o.value === 'confidence'))).toBe(false);
  });
});

describe('a report that came out of a container', () => {
  const gallery = widgets.find(w => w.id === 'temporal-gallery');

  /** Two recordings inside one manifest file: same name, different child_id. */
  const containerContext = () => {
    const base = context();
    const asked = [];
    return {
      ...base,
      schema: { ...base.schema, allCols: [...COLUMNS, 'child_id', 'source_url'] },
      async queryRows(sql) {
        asked.push(sql);
        if (sql.includes('GROUP BY 1')) {
          return [{ name: 'dive_one', path: 'https://a/one.mp4', fps: 30, slices: 12 },
                  { name: 'dive_two', path: 'https://a/two.mp4', fps: 30, slices: 12 }];
        }
        return TIMELINE;
      },
      asked,
    };
  };

  it('treats each child as its own recording', async () => {
    const container = document.createElement('div');
    const ctx = containerContext();
    await gallery.render(container, ctx);
    const scope = container.querySelector('select');
    expect([...scope.options].map(o => o.textContent)).toContain('dive_one');
    expect([...scope.options].map(o => o.textContent)).toContain('dive_two');
  });

  it('groups and filters on child_id rather than the file name', async () => {
    const ctx = containerContext();
    await gallery.render(document.createElement('div'), ctx);
    const grouped = ctx.asked.find(s => s.includes('GROUP BY 1'));
    expect(grouped).toContain('COALESCE');
    expect(grouped).toContain('child_id');
    const timeline = ctx.asked.find(s => s.includes('AS movement'));
    expect(timeline).toContain('COALESCE');
  });

  it('still groups on name when there is no child_id', async () => {
    const ctx = context();
    const asked = [];
    await gallery.render(document.createElement('div'),
                         { ...ctx, queryRows: async (sql) => (asked.push(sql), ctx.queryRows(sql)) });
    expect(asked.find(s => s.includes('GROUP BY 1'))).not.toContain('child_id');
  });
});

describe('how a gallery tile is built', () => {
  const gallery = widgets.find(w => w.id === 'temporal-gallery');

  const rendered = async () => {
    const container = document.createElement('div');
    await gallery.render(container, context());
    return container;
  };

  it('puts the picture above the caption, like the collection board', async () => {
    const card = (await rendered()).querySelector('figure.pp-event');
    expect(card).toBeTruthy();
    const parts = [...card.children].map(c => c.tagName);
    expect(parts.indexOf('DIV')).toBeLessThan(parts.indexOf('FIGCAPTION'));
  });

  it('carries the kind as a colour rather than a border stripe', async () => {
    const card = (await rendered()).querySelector('figure.pp-event');
    expect(card.style.getPropertyValue('--pp-kind')).toBeTruthy();
    expect(card.querySelector('.pp-kind')).toBeTruthy();
  });

  it('names the species once, not twice', async () => {
    const card = (await rendered()).querySelector('figure.pp-event');
    const text = card.textContent;
    expect(text).toContain('beroe');
    expect(text.split('beroe').length - 1).toBe(1);
  });

  it('adds its stylesheet once however many tiles there are', async () => {
    const container = await rendered();
    expect(container.querySelectorAll('style')).toHaveLength(1);
    expect(container.querySelectorAll('figure.pp-event').length).toBeGreaterThan(1);
  });
});

describe('a tile of a report merged without its clip frames', () => {
  // Which is every expedition's own report: the clip frames are 84% of one and
  // only the collection keeps them. What is left per animal is a crop or three,
  // each cut to its own box seconds apart, and cycling those is a flicker between
  // three differently sized pictures rather than anything the animal did.
  const gallery = widgets.find(w => w.id === 'temporal-gallery');

  const crops = (...confs) => JSON.stringify(
    confs.map((conf, i) => ({ class: 'beroe', conf, box: [i, i, i + 9, i + 9],
                              crop: `crop-${conf}` })));

  const withCrops = () => {
    const ctx = context();
    const rows = ctx.queryRows;
    return { ...ctx,
      schema: { ...ctx.schema, allCols: [...ctx.schema.allCols, 'detections'] },
      async queryRows(sql) {
        // The animals query, not the timeline's `"detection_count" AS detections`.
        if (sql.includes('"detections" AS detections')) {
          return [{ t: 150, detections: crops(0.5, 0.91, 0.7) },
                  { t: 180, detections: crops(0.8) }];
        }
        return rows(sql);
      } };
  };

  it('shows one picture, and it is the most convincing crop', async () => {
    const container = document.createElement('div');
    await gallery.render(container, withCrops());
    const card = container.querySelector('figure.pp-event');
    const shown = card.querySelectorAll('img');
    expect(shown).toHaveLength(1);
    expect(shown[0].src).toBe('data:image/jpeg;base64,crop-0.91');
  });
});

describe('a report with the colour axis kept whole', () => {
  // The barcode is drawn by the timeline now: a colour strip of one recording
  // is the same act of exploring it as the curve underneath.
  const timeline = widgets.find(w => w.id === 'temporal-timeline');
  const gallery = widgets.find(w => w.id === 'temporal-gallery');

  /** No dim_c at all, which is what --slice-size C=-1 produces. */
  const noChannels = () => {
    const base = context();
    const asked = [];
    return {
      ...base,
      schema: { ...base.schema, dimCols: ['dim_t'] },
      async queryRows(sql) { asked.push(sql); return base.queryRows(sql); },
      asked,
    };
  };

  it('never asks for a dim_c column, because colour comes from the stills now', async () => {
    // The strip used to tint each column from per-channel mean intensities, and
    // the pipeline asks for the colour axis whole so the detector works in RGB -
    // which leaves one intensity per slice and a grey strip. Naming dim_c when it
    // is absent is also not an empty result but a SQL error, which DuckDB-WASM
    // surfaces as "_setThrew is not defined".
    for (const dimCols of [['dim_t'], ['dim_t', 'dim_c']]) {
      const ctx = noChannels();
      ctx.schema = { ...ctx.schema, dimCols };
      await timeline.render(document.createElement('div'), ctx).catch(() => {});
      expect(ctx.asked.some(s => s.includes('dim_c'))).toBe(false);
    }
  });

  it('never asks for a footage URL, wherever the recordings live', async () => {
    // Everything the gallery shows comes out of the parquet - the stills, the
    // clips, the boxes - so the question was only ever about playing the original
    // behind a tile, and it cost a paragraph of explanation to ask.
    for (const path of ['https://a/one.mp4', 'dives/one.mp4']) {
      const container = document.createElement('div');
      const ctx = context();
      ctx.queryRows = async (sql) => (sql.includes('GROUP BY 1')
        ? [{ name: 'one', path, fps: 30, slices: 12 }]
        : TIMELINE);
      await gallery.render(container, ctx);
      expect(container.querySelector('input[type="url"]')).toBe(null);
    }
  });

});


describe('the previews, in detail', () => {
  const widgetsById = Object.fromEntries(widgets.map(w => [w.id, w]));

  it('never issues DDL to draw a widget', async () => {
    // The verdict shares were staged as a CREATE OR REPLACE TEMP VIEW, which
    // assumes a widget and the plot engine share one connection and that the
    // connection takes DDL at all. A derived table assumes neither.
    const ctx = context();
    const issued = [];
    ctx.query = async (sql) => { issued.push(sql); return emptyArrow; };
    ctx.queryRows = async (sql) => {
      issued.push(sql);
      return isRecordingsQuery(sql)
        ? [{ name: 'dive.mp4', path: '/d/dive.mp4', fps: 30, slices: TIMELINE.length }]
        : TIMELINE;
    };
    await widgetsById['temporal-triage'].render(document.createElement('div'), ctx);
    expect(issued.some(sql => /\b(CREATE|DROP|ALTER)\b/i.test(sql))).toBe(false);
  });

  it('pins the slice rows rather than only requiring a time index', async () => {
    // dim_t IS NOT NULL alone leaves every deeper aggregation level in, so a
    // slice is counted once per channel and the distribution is over a table
    // three times too long.
    const ctx = context();
    ctx.asked = [];
    const asked = [];
    ctx.queryRows = async (sql) => {
      asked.push(sql);
      return isRecordingsQuery(sql)
        ? [{ name: 'dive.mp4', path: '/d/dive.mp4', fps: 30, slices: TIMELINE.length }]
        : TIMELINE;
    };
    await widgetsById['temporal-triage'].render(document.createElement('div'), ctx);
    expect(asked.some(sql => sql.includes('COUNT("frame_difference")'))).toBe(true);
  });

  it('shows the gallery as a sheet of animals', async () => {
    const container = document.createElement('div');
    const ctx = context();
    ctx.schema = { ...ctx.schema, allCols: [...ctx.schema.allCols, 'detections'] };
    ctx.queryRows = async (sql) => (sql.includes('"detections"')
      ? [{ detections: JSON.stringify([{ crop: 'aaa', class: 'beroe' }]), confidence: 0.9 }]
      : TIMELINE);
    expect(await widgetsById['temporal-gallery'].overviewPlot(container, ctx)).toBe(true);
    const images = [...container.querySelectorAll('img')];
    expect(images.length).toBeGreaterThan(0);
    expect(images[0].src).toContain('data:image/jpeg;base64,aaa');
  });

  it('falls back to the event strip when there is no colour to draw', async () => {
    // The barcode is the timeline's preview and it needs a 2D canvas, which
    // happy-dom has not got. What must not happen is a tile showing a plot the
    // card does not contain - so the fallback is the card's other strip.
    const container = document.createElement('div');
    const ctx = context();
    ctx.asked = [];
    expect(await widgetsById['temporal-timeline'].overviewPlot(container, ctx)).toBe(true);
    expect(ctx.asked.some(q => q.startsWith('distribution:'))).toBe(false);
    const bands = [...container.querySelectorAll('.pp-strip-band')];
    expect(bands.length).toBeGreaterThan(0);
    expect(bands.every(b => b.title)).toBe(true);
  });
});


describe('what was found, and where it was found', () => {
  const byId = Object.fromEntries(widgets.map(w => [w.id, w]));

  it('keeps the tree on its own card', async () => {
    // The tree and the axes were one card, and before that the axes were one
    // widget called "over time" that also held the depth plot. Neither survived
    // contact with a reader: a plot of zonation and a plot of chronology are
    // different questions and belong under different headings.
    const container = document.createElement('div');
    await byId['temporal-taxonomy'].render(container, context());
    expect(container.textContent).toContain('classes');
    expect(container.textContent).not.toContain('depth bins');
  });

  it('puts the depth composition on the depth card, under the profiles', async () => {
    const container = document.createElement('div');
    await byId['temporal-depth'].render(container, context());
    expect(container.textContent).toContain('dive');
    expect(container.textContent).toContain('depth bins');
  });

  it('puts the clock composition and the accumulation curve on the when card', async () => {
    const container = document.createElement('div');
    await byId['temporal-when'].render(container, context());
    expect(container.textContent).toContain('kinds named by the end');
  });

  it('says why rather than going quiet when a card has no column', async () => {
    const container = document.createElement('div');
    const ctx = context();
    ctx.schema = { ...ctx.schema, allCols: ctx.schema.allCols.filter(c => c !== 'depth_m') };
    await byId['temporal-depth'].render(container, ctx);
    expect(container.textContent).toContain('No depths in this report');
  });
});
