/**
 * Footage timeline: where in a recording something happens.
 *
 * Every other widget summarises a dataset; this one reads one recording along
 * its own time axis. It plots `frame_difference` (from the raster-temporal
 * processor) per T slice, marks the stretches worth a human's attention, and
 * loads evidence for one stretch at a time.
 *
 * Two kinds of evidence, because neither is always available:
 *   - thumbnails, which travel inside the parquet and always work, but are one
 *     frame each and cannot show movement;
 *   - the footage itself, which shows movement but only when the viewer can
 *     reach it. Give it a base URL and each window becomes a seeked <video>.
 * Both are fetched only for the window the user opens - the timeline itself is
 * drawn from scalar columns, so opening this widget never pulls a blob.
 */

const MIN_WINDOW_ROWS = 2;     // ignore single-slice blips when grouping runs
const MERGE_GAP_ROWS  = 10;    // bridge same-kind runs separated by less than this
const EMPTY_DETAIL_RATIO = 0.2;  // below this share of a recording's usual detail, nothing is in frame
// Lossy compression means a frozen frame never differs by exactly zero - codec
// noise keeps it just above. On a real dive tape the dead tail topped out at
// 0.0045 intensity levels while the quietest live footage sat at 0.133, so
// anything below this threshold is "nothing is changing", not "a still camera".
const FROZEN_BELOW      = 0.01;
const DWELL_PERCENTILE  = 15;  // below this = camera holding still
const ACTIVE_PERCENTILE = 90;  // above this = camera or scene moving hard

export const WINDOW_KINDS = {
  frozen: { label: 'Frozen',   color: '#dc3545', desc: 'Effectively no change between frames - duplicated, dropped, or frozen footage.' },
  dwell:  { label: 'Dwell',    color: '#198754', desc: 'Camera holding still on something with structure in the frame. In ROV and microscope footage this is usually someone looking at a subject.' },
  empty:  { label: 'Empty',    color: '#adb5bd', desc: 'Holding still on almost nothing - open water, a blank field. Moves exactly like a dwell, so only the amount of detail in the frame tells them apart.' },
  subject: { label: 'Subject',  color: '#0d6efd', finding: true, desc: 'A detector found an animal here. On footage a detector understands, this is the event worth opening - movement only says the camera moved.' },
  unnamed: { label: 'Unnamed',  color: '#6f42c1', finding: true, desc: 'Something moved independently of the camera and no detector could put a name to it. Usually a species outside the model\'s vocabulary, sometimes debris - either way it is the only band here that can point at an animal nobody has described.' },
  active: { label: 'Activity', color: '#fd7e14', desc: 'Large frame-to-frame change - transit, a cut, or a subject entering the frame.' },
};

/** Kinds that are something found, as against something noticed about the footage.
 *
 * Frozen, dwell, empty and activity all describe the recording: the camera stopped,
 * the camera swung, the water is empty. They are the answer to "is this tape worth
 * anyone's time", which is a real question when no detector has run. They are not
 * the answer to "what is in it", and mixed into the same gallery they bury it -
 * a dive is mostly camera movement over open water, so most tiles are water.
 */
export const FINDING_KINDS = Object.entries(WINDOW_KINDS)
  .filter(([, meta]) => meta.finding).map(([key]) => key);

/** True when the report has something that finds things, rather than only movement. */
export function reportFindsThings(schema) {
  return ['detection_count', 'moving_object_count', 'bright_particle_count']
    .some(column => (schema?.allCols ?? []).includes(column));
}

// ── data ──────────────────────────────────────────────────────────────────────

/** Recordings that have a time axis, most slices first.
 *
 * Per-slice rows only exist in the all-rows table: `pp_data` is narrowed to
 * whole-image rows (obs_level = 0), which by definition have no dim_t.
 */
async function fetchRecordings(ctx) {
  const { q } = ctx.sql;
  const fpsCol = ctx.schema.allCols.includes('fps') ? `MAX(${q('fps')})` : 'NULL';
  const key = recordingKey(ctx);
  // Where a recording came out of a container - a manifest of video links, say -
  // every row shares the container's file name and the recording is the child. Then
  // source_url is the real address of it, which is what a player wants.
  const source = ctx.schema.allCols.includes('source_url')
    ? `MAX(${q('source_url')})` : `MAX(${q('path')})`;
  // The viewer's grouping comes along, so a plot of many recordings can be split
  // the way the reader has asked for the rest of the report to be split. MAX
  // rather than the column itself because this is grouped by recording: a
  // recording belongs to one expedition, one archive, one anything worth grouping
  // a footage collection by.
  return ctx.queryRows(`
    SELECT ${key} AS name, ${source} AS path, ${fpsCol} AS fps, COUNT(*) AS slices,
           MAX(${groupExpr(ctx)}) AS "group"
    FROM ${sliceTable(ctx)} WHERE ${q('dim_t')} IS NOT NULL
    GROUP BY 1 ORDER BY slices DESC`);
}

/** The column the reader has grouped the report by, or nothing.
 *
 * Optional because grouping is: a report opened with no grouping chosen, and every
 * widget test that builds a bare ctx, has no groupCol to call. A plot that splits
 * by nothing is the right answer there rather than an exception.
 */
function whereClause(ctx, extra) {
  try { return ctx.sql.andWhere?.(ctx.where, extra) ?? `WHERE ${extra}`; }
  catch { return `WHERE ${extra}`; }
}


function groupExpr(ctx) {
  try { return ctx.sql.groupCol?.() ?? 'NULL'; }
  catch { return 'NULL'; }
}

/** What counts as one recording in this report.
 *
 * One file is usually one recording, but a container file holds many and names them
 * with child_id. Grouping by the file name there would collapse an entire
 * expedition into a single timeline.
 */
export function recordingKey(ctx) {
  const { q } = ctx.sql;
  return ctx.schema.allCols.includes('child_id')
    ? `COALESCE(${q('child_id')}, ${q('name')})` : q('name');
}

const sliceTable = (ctx) => ctx.schema.allTable ?? 'pp_all';

/** One row per T slice of one recording: the whole timeline, scalars only. */
/** The optional columns a timeline reads, named the same way however it is asked
 * for. Shared by the one-recording and the whole-collection queries so the two
 * cannot drift into returning different shapes of row.
 */
function timelineColumns(ctx, { only } = {}) {
  const { q } = ctx.sql;
  // A column the caller has no use for is sent as a null rather than dropped, so
  // every row has the same shape however it was asked for and nothing downstream
  // has to know which query it came from. The saving is the values: the triage and
  // taxonomy widgets read six of these thirteen, over every slice of every
  // recording, and the rest was a third of a million numbers crossing the wasm
  // boundary to be ignored.
  const wanted = only ? new Set(only) : null;
  const has = (column, name) => (!wanted || wanted.has(name))
    && ctx.schema.allCols.includes(column);
  const extra = has('frame_difference_max', 'peak') ? `, ${q('frame_difference_max')} AS peak` : ', NULL AS peak';
  // Something has to stand in for "is there anything in the frame": a camera parked
  // on a coral colony and one parked on open water move exactly alike. Laplacian
  // variance separates them best - by two orders of magnitude on real footage - but
  // it comes from raster-quality, whose spectral_slope costs more per slice than the
  // detector does. Where that was skipped, intensity spread is the cheap stand-in:
  // weaker, but it still tells a lit subject from empty water.
  const detailColumn = (!wanted || wanted.has('structure'))
    ? ['laplacian_variance', 'std_intensity'].find(c => ctx.schema.allCols.includes(c))
    : null;
  const structure = detailColumn ? `, ${q(detailColumn)} AS structure` : ', NULL AS structure';
  // Present only when raster-particles ran. Small bright particles: the animals in
  // midwater footage, marine snow near a lit seafloor - the scene decides which.
  const objects = has('bright_particle_count', 'objects')
    ? `, ${q('bright_particle_count')} AS objects` : ', NULL AS objects';
  // Present only when a detector ran. This is the one signal here that knows what an
  // animal is, so it takes precedence over the particle count wherever it exists.
  const detections = has('detection_count', 'detections')
    ? `, ${q('detection_count')} AS detections, ${q('detection_top_class')} AS top_class`
      + `, ${q('detection_confidence')} AS confidence`
    : ', NULL AS detections, NULL AS top_class, NULL AS confidence';
  // Present only when raster-motion ran. Unlike every other signal here this one
  // needs no model, so it is the only thing that can flag an animal no detector has
  // a class for - and the two together say more than either does alone.
  const movers = has('moving_object_count', 'movers')
    ? `, ${q('moving_object_count')} AS movers, ${q('camera_speed')} AS camera_speed`
    : ', NULL AS movers, NULL AS camera_speed';
  // Present only when slice-location ran, which needs the archive to publish
  // navigation beside the video. A dive's shape is its depth trace - the descent,
  // the hours of work on the bottom, the ascent - and the UTC stamp is the only
  // thing that can put this recording beside one from another decade.
  const depth = has('depth_m', 'depth') ? `, ${q('depth_m')} AS depth` : ', NULL AS depth';
  const clock = has('recorded_at', 'at') ? `, ${q('recorded_at')} AS at` : ', NULL AS at';
  return `${extra}${structure}${objects}${detections}${movers}${depth}${clock}`;
}

async function fetchTimeline(ctx, recording) {
  const { q } = ctx.sql;
  const parts = ctx.sql.dimSubsetWhere({ split: new Set(['t']) });
  parts.push(`${recordingKey(ctx)} = ${literal(recording.name)}`);
  const rows = await ctx.queryRows(`
    SELECT ${q('dim_t')} AS t, ${q('frame_difference')} AS movement${timelineColumns(ctx)}
    FROM ${sliceTable(ctx)} WHERE ${parts.join(' AND ')} ORDER BY t`);
  return rows.filter(r => Number.isFinite(Number(r.movement)));
}

/** Every recording's timeline, in one query rather than one query each.
 *
 * The widgets that read a whole collection - triage, and the taxonomy tree -
 * asked for one recording at a time and awaited each before asking for the next.
 * On one dive that is one query. On a collection of 287 recordings it is 287
 * round trips to duckdb in series, each with its own Arrow decode, and it is why
 * those two widgets took minutes to paint while the rest of the page was ready.
 *
 * Same rows, same order, same filtering - the only difference is that the
 * recording each row belongs to arrives as a column instead of as a predicate.
 */
// Recordings per query. One query for a whole collection is the fewest round
// trips and the largest single result, and the largest single result is the one
// duckdb-wasm has to hold, sort and hand over in one piece - in a browser, with a
// fixed heap. Sixty-four recordings is around twenty thousand rows: few enough
// queries that the cost is the work rather than the waiting, small enough that no
// single one is an outlier.
const TIMELINES_PER_QUERY = 64;

export async function fetchTimelines(ctx, recordings, { only } = {}) {
  const { q } = ctx.sql;
  if (recordings.length <= 1) {
    const only = recordings[0];
    if (!only) return new Map();
    return new Map([[String(only.name), await fetchTimeline(ctx, only)]]);
  }
  const wanted = new Set(recordings.map(recording => String(recording.name)));
  const byRecording = new Map();
  for (let from = 0; from < recordings.length; from += TIMELINES_PER_QUERY) {
    const batch = recordings.slice(from, from + TIMELINES_PER_QUERY);
    const parts = ctx.sql.dimSubsetWhere({ split: new Set(['t']) });
    parts.push(`${recordingKey(ctx)} IN (${batch.map(r => literal(r.name)).join(', ')})`);
    // No ORDER BY: sorting the whole table is the most expensive thing this could
    // ask a browser to do, and the rows have to be split by recording here anyway.
    // Sorting each recording's own few hundred afterwards costs nothing.
    const rows = await ctx.queryRows(`
      SELECT ${recordingKey(ctx)} AS rec, ${q('dim_t')} AS t, ${q('frame_difference')} AS movement${timelineColumns(ctx, { only })}
      FROM ${sliceTable(ctx)} WHERE ${parts.join(' AND ')}`);
    for (const row of rows) {
      if (!Number.isFinite(Number(row.movement))) continue;
      const name = String(row.rec);
      if (!wanted.has(name)) continue;
      if (!byRecording.has(name)) byRecording.set(name, []);
      byRecording.get(name).push(row);
    }
  }
  for (const timeline of byRecording.values()) {
    timeline.sort((a, b) => Number(a.t) - Number(b.t));
  }
  return byRecording;
}

// ── event detection ───────────────────────────────────────────────────────────

/** Contiguous runs of slices that share a classification, longer than a blip. */
export function findWindows(timeline) {
  if (timeline.length < MIN_WINDOW_ROWS) return [];
  const values = timeline.map(r => Number(r.movement));
  const dwellBelow  = percentile(values.filter(v => v >= FROZEN_BELOW), DWELL_PERCENTILE);
  const activeAbove = percentile(values, ACTIVE_PERCENTILE);
  const detail = detailBaseline(timeline);
  const runs = [];
  for (const [index, row] of timeline.entries()) {
    const kind = classify(Number(row.movement), Number(row.structure), Number(row.detections),
                          dwellBelow, activeAbove, detail, Number(row.movers));
    // A named animal is part of the run's identity, not just a label on it: two
    // different species one after another are two sightings, and merging them would
    // hide every species but the first.
    const label = kind === 'subject' ? (row.top_class ?? '') : null;
    const open = runs[runs.length - 1];
    if (open && open.kind === kind && open.label === label && open.endIndex === index - 1) {
      open.endIndex = index;
    } else if (kind) {
      runs.push({ kind, label, startIndex: index, endIndex: index });
    }
  }
  return mergeNearbyRuns(runs)
    .filter(isLongEnough)
    .map(run => describeWindow(run, timeline, values));
}

/** Long enough to be an event rather than a threshold wobble.
 *
 * Every kind but one is a movement value crossing a line, and one slice either
 * side of a line is noise. A detection is not: a model looked at the frame and
 * named the animal in it. Three of the seven species in the midwater report are
 * only ever on screen for a single slice, and holding them to the same minimum
 * dropped them from the gallery entirely - the report said seven species and
 * showed four.
 */
function isLongEnough(run) {
  if (run.kind === 'subject') return true;
  return run.endIndex - run.startIndex + 1 >= MIN_WINDOW_ROWS;
}

/** Join same-kind runs separated by only a moment of ordinary footage.
 *
 * One sampling sequence does not read as one event to a threshold: the camera
 * swings, settles, swings again. Left alone that produced fifteen tiles across
 * seven minutes, all of the same manipulator working the same coral field.
 * Bridging short gaps turns them back into the single event a person would name.
 *
 * Every run still open, not just the one before this - which is the difference
 * between a gallery of animals and a gallery of the same animal. A benthic frame
 * holds several species at once and `detection_top_class` is whichever was most
 * confident, so one coral's sighting reads as Paragorgia, Actiniaria, Paragorgia
 * as the ranking flickers between them. Comparing only against the previous run
 * let that middle species break the coral in two and neither half could find the
 * other again. On EX2205 it is the difference between 260 events and 175, and
 * between 119 tiles repeating a species already on screen and 34.
 */
function mergeNearbyRuns(runs) {
  const merged = [];
  for (const run of runs) {
    const open = stillOpen(merged, run);
    if (open) open.endIndex = Math.max(open.endIndex, run.endIndex);
    else merged.push({ ...run });
  }
  return merged;
}

/** The most recent run this one continues, if any is still within reach. */
function stillOpen(merged, run) {
  for (let i = merged.length - 1; i >= 0; i -= 1) {
    const candidate = merged[i];
    // Runs are in start order, so once one ends too far back every earlier one
    // does too.
    if (run.startIndex - candidate.endIndex - 1 > MERGE_GAP_ROWS) return null;
    if (candidate.kind === run.kind && candidate.label === run.label) return candidate;
  }
  return null;
}

function classify(value, structure, detections, dwellBelow, activeAbove, detail, movers) {
  // Frozen first: dead footage is dead whatever else is true of it.
  if (value < FROZEN_BELOW) return 'frozen';
  // Then anything a detector actually recognised. Movement only ever says the
  // camera moved; a named animal is the thing someone came to find, so where both
  // apply the animal wins.
  if (detections > 0) return 'subject';
  // Then something that moved independently of the camera without being recognised.
  // Below a named animal because a name is more information, above everything else
  // because nothing else here can point at a species that has no name yet.
  if (movers > 0) return 'unnamed';
  if (value <= dwellBelow) return isEmpty(structure, detail) ? 'empty' : 'dwell';
  if (value >= activeAbove) return 'active';
  return null;
}

/** True when the frame holds far less detail than this recording usually does.
 *
 * The comparison has to be relative: two cameras on the same dive differed
 * eightfold in baseline detail, so any absolute cutoff would call one of them
 * empty throughout. Against its own median an empty frame is unmistakable -
 * open water measured 0.06 of the recording's median where a coral colony
 * measured 1.6.
 */
function isEmpty(structure, detail) {
  return detail > 0 && Number.isFinite(structure) && structure < detail * EMPTY_DETAIL_RATIO;
}

function detailBaseline(timeline) {
  const values = timeline.map(r => Number(r.structure)).filter(Number.isFinite);
  return values.length ? percentile(values, 50) : 0;
}

function describeWindow(run, timeline, values) {
  const slice = values.slice(run.startIndex, run.endIndex + 1);
  const rows = timeline.slice(run.startIndex, run.endIndex + 1);
  return {
    kind:     run.kind,
    label:    run.label ?? null,
    fromT:    Number(timeline[run.startIndex].t),
    toT:      Number(timeline[run.endIndex].t),
    slices:   run.endIndex - run.startIndex + 1,
    movement: slice.reduce((a, b) => a + b, 0) / slice.length,
    objects:  meanOf(rows.map(r => Number(r.objects))),
    detections: meanOf(rows.map(r => Number(r.detections))),
    topClass: run.label || mostConfidentClass(rows),
    // The best look at the animal, not the average one: a run holds slices where it
    // was half out of frame, and those should not talk down the one clear view.
    confidence: maxOf(rows.map(r => Number(r.confidence))),
  };
}

/** The label from the busiest slice in the window - the sighting worth naming. */
/** The species this stretch is most likely to actually be.
 *
 * Confidence where the detector reported it, count only as a fallback: the slice
 * with four faint things in it is not a better answer than the slice with one
 * unmistakable one, and ranking by count said it was.
 */
function mostConfidentClass(rows) {
  let best = -1, label = null;
  for (const row of rows) {
    if (!row.top_class) continue;
    const score = Number.isFinite(Number(row.confidence)) ? Number(row.confidence) : Number(row.detections);
    if (Number.isFinite(score) && score > best) { best = score; label = row.top_class; }
  }
  return label;
}

/** Every species named in these rows, most confident first. */
function speciesIn(rows) {
  const best = new Map();
  for (const row of rows) {
    if (!row.top_class) continue;
    const score = Number(row.confidence) || Number(row.detections) || 0;
    best.set(row.top_class, Math.max(best.get(row.top_class) ?? 0, score));
  }
  return [...best.entries()].sort((a, b) => b[1] - a[1]).map(([name]) => name);
}

function maxOf(values) {
  const usable = values.filter(Number.isFinite);
  return usable.length ? Math.max(...usable) : NaN;
}

function meanOf(values) {
  const usable = values.filter(Number.isFinite);
  return usable.length ? usable.reduce((a, b) => a + b, 0) / usable.length : NaN;
}

// ── rendering ─────────────────────────────────────────────────────────────────

/** Colour chips for the event kinds actually present, so the bands read on sight. */
function renderKindLegend(host, kinds) {
  const present = Object.entries(WINDOW_KINDS).filter(([key]) => kinds.has(key));
  if (!present.length) return;
  const bar = document.createElement('div');
  bar.className = 'small text-muted';
  bar.style.cssText = 'display:flex;gap:12px;flex-wrap:wrap;margin:2px 0 6px';
  bar.innerHTML = present.map(([, meta]) =>
    `<span title="${escapeHtmlText(meta.desc)}" style="display:inline-flex;align-items:center;gap:4px">`
    + `<span style="width:11px;height:11px;border-radius:2px;background:${meta.color};display:inline-block"></span>`
    + `${meta.label}</span>`).join('');
  host.appendChild(bar);
}

function renderTimelinePlot(host, ctx, timeline, windows, fps, onPick,
                            onMoment = onPick, strip = null) {
  const seconds = timeline.map(r => toSeconds(Number(r.t), fps));
  // Where the archive published navigation, every point knows the moment and the
  // depth it was filmed at. Carried as customdata rather than as another axis: a
  // reader wants it when pointing at a stretch, not as a third line to disentangle.
  const placed = timeline.map(r => [
    Number.isFinite(Number(r.depth)) ? Number(r.depth) : null,
    r.at ? String(r.at).replace('T', ' ').slice(0, 19) : null]);
  const knowsWhere = placed.some(([metres, at]) => metres != null || at != null);
  const traces = [{
    type: 'scattergl', mode: 'lines', name: 'movement',
    x: seconds, y: timeline.map(r => Number(r.movement)),
    line: { width: 1, color: '#495057' },
    ...(knowsWhere ? { customdata: placed } : {}),
    hovertemplate: knowsWhere
      ? '%{x}s<br>movement %{y:.3f}<br>%{customdata[1]} UTC<br>%{customdata[0]:,.0f} m deep<extra></extra>'
      : '%{x}s<br>movement %{y:.3f}<extra></extra>',
  }];
  // Where a detector actually saw something, drawn over the movement it cannot explain.
  const animals = timeline.map(r => Number(r.detections));
  if (animals.some(Number.isFinite)) {
    traces.push({
      type: 'scattergl', mode: 'markers', name: 'animals', yaxis: 'y2',
      x: seconds.filter((_, i) => animals[i] > 0),
      y: animals.filter(v => v > 0),
      marker: { size: 6, color: '#0d6efd', opacity: 0.75 },
      hovertemplate: '%{x}s<br>%{y:.1f} animals<extra></extra>',
    });
  }
  const layout = {
    height: 260,
    margin: { l: 55, r: 12, t: 8, b: 40 },
    xaxis: { title: fps ? 'time (s)' : 'frame index', zeroline: false },
    yaxis: { title: 'frame difference', rangemode: 'tozero' },
    shapes: windows.map(w => ({
      type: 'rect', xref: 'x', yref: 'paper', layer: 'below',
      x0: toSeconds(w.fromT, fps), x1: toSeconds(w.toT, fps), y0: 0, y1: 1,
      fillcolor: WINDOW_KINDS[w.kind].color, opacity: 0.16, line: { width: 0 },
    })),
    showlegend: false,
  };
  if (strip) {
    // The colour of the footage, above the curve and on the same x axis, so a band
    // of colour and the movement under it are the same moment by construction
    // rather than by eye. This was a strip in its own widget with its own bucket
    // width and its own pixel scale, and it lined up with the curve only by
    // accident. The plot area is squeezed to leave room; `sizing: stretch` lets
    // one pixel per slice fill whatever width the card ends up.
    layout.images = [{
      source: strip.url, xref: 'x', yref: 'paper',
      x: strip.from, y: 1, sizex: Math.max(1e-6, strip.to - strip.from),
      sizey: STRIP_SHARE, xanchor: 'left', yanchor: 'top',
      sizing: 'stretch', layer: 'above',
    }];
    layout.yaxis.domain = [0, 1 - STRIP_SHARE - 0.04];
    layout.height = 300;
  }
  if (traces.length > 1) {
    layout.yaxis2 = { title: 'animals', overlaying: 'y', side: 'right',
                      rangemode: 'tozero', showgrid: false,
                      ...(strip ? { domain: layout.yaxis.domain } : {}) };
    layout.margin.r = 46;
    layout.showlegend = true;
    layout.legend = { orientation: 'h', y: 1.12, x: 0 };
  }
  const div = ctx.plot.append(host, traces, layout);
  div.on('plotly_click', ev => {
    const point = ev.points?.[0];
    if (point?.x == null) return;
    // An animal marker is the count at one slice, and what a reader wants when
    // they click one is those animals rather than the stretch around them.
    if (point.data?.name === 'animals') { onMoment(Number(point.x)); return; }
    onPick(nearestWindow(windows, fromSeconds(point.x, fps))
           ?? pointWindow(fromSeconds(point.x, fps)));
  });
  return div;
}

function renderEvidence(host, recording, window, fps, footageBase) {
  const startSeconds = toSeconds(window.fromT, fps);
  const heading = `<div class="small text-muted mb-2">`
    + `${WINDOW_KINDS[window.kind]?.label ?? 'Window'} at <strong>${formatClock(startSeconds, fps)}</strong>`
    + ` · ${formatDuration(toSeconds(window.toT, fps) - startSeconds, fps, window.slices)}</div>`;
  host.innerHTML = heading + (footageBase
    ? videoHtml(footageBase, recording, startSeconds)
    : '<div class="no-data">Set a footage base URL below to watch this window.</div>');
}

function videoHtml(base, recording, startSeconds) {
  const url = footageUrl(base, recording, startSeconds);
  return `<video controls crossorigin="anonymous" preload="metadata" src="${escapeAttribute(url)}"`
    + ` style="max-width:100%;max-height:320px;display:block;margin-bottom:6px"></video>`
    + `<a href="${escapeAttribute(url)}" target="_blank" rel="noopener" class="small">`
    + `Open the recording at this point in a new tab</a>`;
}

function footageUrl(base, recording, startSeconds) {
  return `${recordingUrl(base, recording)}#t=${Math.max(0, Math.floor(startSeconds))}`;
}

function recordingUrl(base, recording) {
  const known = String(recording.path ?? '');
  // Anything read out of a manifest carries the address it was streamed from, so
  // there is nothing for a base URL to complete.
  if (/^https?:\/\//i.test(known)) return known;
  const path = String(recording.path ?? recording.name).split('/').map(encodeURIComponent).join('/');
  return `${base.replace(/\/+$/, '')}/${path}`;
}

/** True when every recording already knows where it came from. */
export function recordingsKnowTheirSource(recordings) {
  return recordings.length > 0
    && recordings.every(r => /^https?:\/\//i.test(String(r.path ?? '')));
}

/** Prefill from ?footage=<base-url> so a report can be shared as one link. */
function initialFootageBase() {
  try { return new URLSearchParams(window.location.search).get('footage') ?? ''; }
  catch { return ''; }
}

function renderFootageInput(host, initial, onChange) {
  const wrap = document.createElement('div');
  wrap.className = 'mb-2';
  wrap.innerHTML = '<label class="form-label small mb-1">Footage base URL '
    + '<span class="text-muted">(optional) &mdash; where the recordings are served from, '
    + 'so clicking a tile plays that stretch of the original video. The report itself '
    + 'needs none of this: the tiles come out of the parquet. Give it the folder the '
    + 'recordings sit in, e.g. <code>https://example.org/dives/</code>, and the '
    + 'recording name is appended.</span></label>';
  const input = document.createElement('input');
  input.type = 'url';
  input.className = 'form-control form-control-sm';
  input.placeholder = 'https://example.org/videos';
  input.value = initial ?? '';
  input.addEventListener('change', () => onChange(input.value.trim()));
  wrap.appendChild(input);
  host.appendChild(wrap);
}

// ── helpers ───────────────────────────────────────────────────────────────────

const toSeconds   = (t, fps) => (fps ? t / fps : t);
const fromSeconds = (s, fps) => (fps ? s * fps : s);
const literal     = (value) => `'${String(value).replace(/'/g, "''")}'`;
const escapeAttribute = (value) => String(value).replace(/"/g, '&quot;');

function percentile(values, p) {
  if (!values.length) return 0;
  const sorted = [...values].sort((a, b) => a - b);
  return sorted[Math.min(sorted.length - 1, Math.floor((p / 100) * sorted.length))];
}

function nearestWindow(windows, t) {
  return windows.find(w => t >= w.fromT && t <= w.toT);
}

function pointWindow(t) {
  return { kind: 'active', fromT: Math.max(0, Math.round(t)), toT: Math.round(t), slices: 1, movement: NaN };
}

/** Read the two columns off an Arrow table by vector, the way the mosaic does.
 *
 * A row proxy hands back a wrapper the binary decoder does not recognise, so
 * the thumbnail silently comes out blank; the column vector hands back bytes.
 */
function formatClock(seconds, fps) {
  if (!fps) return `frame ${Math.round(seconds)}`;
  const total = Math.max(0, Math.round(seconds));
  const minutes = Math.floor(total / 60);
  return `${String(Math.floor(minutes / 60)).padStart(2, '0')}:${String(minutes % 60).padStart(2, '0')}:${String(total % 60).padStart(2, '0')}`;
}

function formatDuration(seconds, fps, slices) {
  return fps ? `${Math.max(1, Math.round(seconds))} s` : `${slices} slices`;
}

// ── event gallery ─────────────────────────────────────────────────────────────

const GALLERY_FRAMES = 6;    // per-slice stills sampled across one event
const ANIMAL_FRAMES  = 16;   // crops of the animal itself, which are far cheaper
const GALLERY_MAX    = 48;   // events rendered at once
const FLIPBOOK_MS    = 130;  // ms per frame while cycling
const PREVIEW_WIDTH  = 168;
// Square, and the same square the collection board uses, so the two read as one
// thing. Fixed so that cycling frames of different crops cannot resize the tile.
const PREVIEW_HEIGHT = 168;

/** How sure the detector has to be, and how long an event has to last.
 *
 * Both are judgements rather than facts, so both are on the page rather than baked
 * in - but these numbers are not guesses. Each label is what that floor actually
 * did to this pipeline's own output over MBARI's annotated DeepSea-MOT sequences,
 * where a box around every animal in every frame makes "how clean is this"
 * answerable rather than arguable. 4,433 detections against 5,878 annotated
 * animals, all five sequences pooled - pooled because a threshold does not know
 * which recording a row came from:
 *
 *   floor   precision   recall
 *    0.00     0.905      0.683
 *    0.04     0.950      0.649
 *    0.06     0.967      0.621
 *    0.37     0.990      0.425
 *
 * These are fused scores - the frame is read at three sizes and a size that did
 * not see the animal votes zero - so they are lower than a single pass's numbers
 * and not comparable to them. A floor of 0.4 on this scale, which is what this
 * used to default to, keeps two animals in five.
 *
 * And the precision column is a floor rather than a figure: cropping the confident
 * boxes DeepSea-MOT has no annotation under shows real sea pens and shrimp the
 * benchmark did not label, so the true precision is higher than any of these.
 */
const CONFIDENCE_FLOORS = [
  { label: 'any confidence - 91% right, 68% of them', value: 0 },
  { label: 'over 0.04 - 95% right, 65% of them', value: 0.04 },
  { label: 'over 0.06 - 97% right, 62% of them', value: 0.06 },
  { label: 'over 0.37 - 99% right, 43% of them', value: 0.37 },
];
// Open on everything. The floors below say what each one costs and a reader can
// pick one, but the report's own job is to show what it found - starting from a
// filtered view means the first thing a reader sees is a smaller number than the
// report actually contains, with no indication that a control did that.
const DEFAULT_FLOOR = 0;

const LENGTHS = [
  { label: 'any length', value: 1 },
  { label: '2 slices or more', value: 2 },
  { label: '4 slices or more', value: 4 },
  { label: '8 slices or more', value: 8 },
];
// Any length, for the same reason: a great many animals are in frame for one
// slice only, and hiding them by default hides finds.
const DEFAULT_LENGTH = 1;

/** Whether an event clears the floor and the minimum length.
 *
 * The length rule does not apply to a named animal, and that exception is the
 * point of it. A minimum length exists because a *movement* threshold crossed for
 * one slice is noise rather than an encounter - but a detector naming an animal in
 * one slice is a sighting, and on footage sliced at five seconds a great many
 * animals are only in frame for one. Holding those to the same rule hid finds,
 * which is the opposite of what the filter is for.
 */
export function keptByQuality(event, floor, minSlices) {
  if (event.kind !== 'subject' && event.slices < minSlices) return false;
  if (!floor) return true;
  // Only a named event has a confidence to judge; a mover nothing recognised has
  // no score, and holding it to one would silently drop the unknown-species band.
  if (!Number.isFinite(event.confidence)) return event.kind !== 'subject';
  return event.confidence >= floor;
}

const SORTS = {
  interest: { label: 'Interest', compare: (a, b) => b.score - a.score },
  time:     { label: 'Time',     compare: (a, b) => a.fromT - b.fromT },
  duration: { label: 'Duration', compare: (a, b) => b.slices - a.slices },
  movement: { label: 'Movement', compare: (a, b) => b.movement - a.movement },
  particles: { label: 'Particles in frame', compare: (a, b) => b.objects - a.objects, needsObjects: true },
  animals:   { label: 'Animals detected', compare: (a, b) => (b.detections || 0) - (a.detections || 0), needsDetections: true },
  // Count and certainty rank differently: three faint shrimp outrank one unmistakable
  // squid by count, and the squid is the tile someone wants to see first.
  confidence: { label: 'Detector confidence', compare: (a, b) => (b.confidence || 0) - (a.confidence || 0), needsDetections: true },
};

/** Typical movement and spread, used to say how unusual one window is. */
function movementBaseline(timeline) {
  const values = timeline.map(r => Number(r.movement));
  const spread = percentile(values, 75) - percentile(values, 25);
  return { median: percentile(values, 50), spread: spread || 1 };
}

/** How far this window sits from the recording's normal, weighted by how long it holds.
 *
 * Deviation is measured as a log ratio, not a difference. Movement is unbounded
 * above and floored at zero, so a plain difference lets one fast pan outscore
 * anything quiet: on a real dive tape a nine-minute frozen stretch ranked 19th,
 * below fifteen near-identical camera swings. The log ratio treats "ten times
 * more movement than usual" and "ten times less" as equally strange, which moved
 * that frozen stretch to first and surfaced a 51-second hold that had been
 * invisible. Duration enters as a log so a long event outranks a brief one
 * without a two-minute transit burying everything else.
 */
function interestScore(window, baseline) {
  const ratio = (window.movement + FROZEN_BELOW / 10) / baseline.median;
  return Math.abs(Math.log2(ratio)) * Math.log2(1 + window.slices);
}

function scoreWindows(windows, timeline, recording) {
  const baseline = movementBaseline(timeline);
  return windows.map(w => ({ ...w, recording, score: interestScore(w, baseline) }));
}

/** Every recording's events, scored against its own baseline then pooled.
 *
 * Scoring per recording matters: a tape shot close to the seafloor moves more
 * throughout than one spent transiting, and a shared baseline would rank the
 * whole busy tape above everything on the quiet one.
 */
async function collectEvents(ctx, recordings) {
  const timelines = await fetchTimelines(ctx, recordings);
  const events = [];
  for (const recording of recordings) {
    const timeline = timelines.get(String(recording.name)) ?? [];
    if (!timeline.length) continue;
    events.push(...scoreWindows(findWindows(timeline), timeline, recording));
  }
  return events;
}

/** Stills for the events, straight out of the parquet.
 *
 * The slice-thumbnail processor writes one small JPEG per slice, so a preview is a
 * lookup rather than a seek into a remote recording. That is the difference between
 * a gallery that fills instantly and one that spends 43 MB of range requests
 * filling a single screen. An event covering several slices animates by cycling the
 * stills it already has.
 */
async function fetchEventStills(ctx, events) {
  if (!ctx.schema.allCols.includes('slice_thumbnail')) return new Map();
  const { q } = ctx.sql;
  // A close-up of the animal beats a wide shot of dark water, so the crop is
  // preferred wherever the detector left one.
  const crop = ctx.schema.allCols.includes('detection_crop')
    ? `COALESCE(${q('detection_crop')}, ${q('slice_thumbnail')})` : q('slice_thumbnail');
  const wanted = new Map();               // recording name -> set of dim_t
  for (const event of events) {
    const name = event.recording.name;
    if (!wanted.has(name)) wanted.set(name, new Set());
    for (const t of stillTimes(event)) wanted.get(name).add(t);
  }
  const stills = new Map();
  for (const [name, times] of wanted) {
    const table = await ctx.query(`
      SELECT ${q('dim_t')} AS t, ${crop} AS still
      FROM ${sliceTable(ctx)}
      WHERE ${recordingKey(ctx)} = ${literal(name)} AND ${q('slice_thumbnail')} IS NOT NULL
        AND ${q('dim_t')} IN (${[...times].join(', ')})`);
    stills.set(name, decodeStills(ctx, table));
  }
  return stills;
}

/** Every animal the detector wrote into the event's slices, in time order.
 *
 * The per-slice stills give one frame per slice, so a two-slice event animates two
 * images and a one-slice event does not animate at all. The detections column holds
 * a crop per animal per frame the detector looked at - three of them per look - and
 * they are of the animal rather than the whole scene, a tenth of a second apart. So
 * a tile built from those actually moves.
 */
async function fetchEventAnimals(ctx, events) {
  if (!ctx.schema.allCols.includes('detections')) return new Map();
  const { q } = ctx.sql;
  const wanted = new Map();
  for (const event of events) {
    const name = event.recording.name;
    if (!wanted.has(name)) wanted.set(name, new Set());
    for (const t of sliceRange(event)) wanted.get(name).add(t);
  }
  const found = new Map();
  for (const [name, times] of wanted) {
    const rows = await ctx.queryRows(`
      SELECT ${q('dim_t')} AS t, ${q('detections')} AS detections
      FROM ${sliceTable(ctx)}
      WHERE ${recordingKey(ctx)} = ${literal(name)} AND ${q('detections')} IS NOT NULL
        AND ${q('dim_t')} IN (${[...times].join(', ')})
      ORDER BY t`);
    found.set(name, rows.flatMap(row => parseAnimals(row.detections)
      .map(animal => ({ ...animal, t: Number(row.t) }))));
  }
  return found;
}

// How many tiles a preview is: enough to see what kind of footage this is, few
// enough that each is still big enough to recognise an animal in.
const PREVIEW_TILES = 4;

/** The first few animals in the report, as `{ src, label }` ready for an <img>.
 *
 * Per-animal crops first, because a crop is the animal and a slice still is the
 * animal plus a screenful of water. Falls back to the cached stills of the
 * highest-scoring events, so a report from a run with no detector still previews
 * as footage rather than as nothing.
 */
async function previewShots(ctx, most) {
  const { q } = ctx.sql;
  if (ctx.schema.allCols.includes('detections')) {
    const rows = await ctx.queryRows(`
      SELECT ${q('detections')} AS detections, ${q('detection_confidence')} AS confidence
      FROM ${sliceTable(ctx)}
      ${whereClause(ctx, `${q('detections')} IS NOT NULL`)}
      ORDER BY ${q('detection_confidence')} DESC NULLS LAST
      LIMIT ${most * 3}`);
    const shots = [];
    for (const row of rows) {
      for (const animal of parseAnimals(row.detections)) {
        shots.push({ src: `data:image/jpeg;base64,${animal.crop}`, label: animal.class });
        if (shots.length >= most) return shots;
      }
    }
    if (shots.length) return shots;
  }
  if (!ctx.schema.allCols.includes('slice_thumbnail')) return [];
  const table = await ctx.query(`
    SELECT ${q('dim_t')} AS t, ${q('slice_thumbnail')} AS still
    FROM ${sliceTable(ctx)}
    ${whereClause(ctx, `${q('slice_thumbnail')} IS NOT NULL`)}
    LIMIT ${most}`);
  return [...decodeStills(ctx, table).values()]
    .map(bytes => ({ src: URL.createObjectURL(new Blob([bytes], { type: 'image/jpeg' })) }));
}

export function parseAnimals(json) {
  try {
    const animals = JSON.parse(json);
    return Array.isArray(animals) ? animals.filter(a => a && a.crop) : [];
  } catch { return []; }
}

/** Every slice the event covers, which is what its animals have to be looked up by. */
function sliceRange(event) {
  const step = eventStep(event);
  const times = [];
  for (let t = event.fromT; t <= event.toT && times.length < 400; t += step) times.push(t);
  return times.length ? times : [event.fromT];
}

/** The frames of one event's own animal, oldest first.
 *
 * Filtered to the species the event is named after, so a tile of a squid does not
 * flick through a shrimp that shared one of its slices.
 */
export function animalFrames(event, animals, most) {
  const mine = (animals.get(event.recording.name) ?? [])
    .filter(a => a.t >= event.fromT && a.t <= event.toT)
    .filter(a => !event.topClass || a.class === event.topClass);
  // One clip, always - never a run of them. Its frames are consecutive and cropped
  // with one box, so they play as movement; the per-detection crops are each cropped
  // to their own box, so an animal detected at 15 px and again at 68 px jumps in
  // scale between frames however carefully they are ordered. A five-second event
  // covers several slices and each slice cut a clip of every animal in it, so
  // playing them all is a jump cut between different animals in different places.
  const use = oneClip(mine) ?? mine;
  use.sort((a, b) => (a.second ?? a.t) - (b.second ?? b.t));
  const step = Math.max(1, Math.floor(use.length / most));
  return use.filter((_, i) => i % step === 0).slice(0, most);
}

// How much a clip's box has to land on an animal's box to be that animal's clip.
const ITS_OWN_CLIP = 0.3;

/** The single clip belonging to this event's most convincing animal, or null. */
function oneClip(animals) {
  const clips = animals.filter(a => a.clip);
  if (clips.length < 2) return null;
  const best = animals.filter(a => !a.clip)
    .reduce((a, b) => (a && a.conf >= b.conf ? a : b), null);
  const groups = new Map();
  for (const frame of clips) {
    const key = `${frame.t}/${frame.of ?? 0}`;
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(frame);
  }
  // The clip cut with the box that animal was found in. Reports written before
  // clips were per-animal have one group per slice, and this picks the first.
  const rank = (group) => (best && best.t === group[0].t
    ? overlap(group[0].box, best.box) : 0);
  const pick = [...groups.values()].reduce((a, b) => (rank(a) >= rank(b) ? a : b));
  // Only if it is this animal's own clip. A slice cuts one for each of its few most
  // convincing animals, so the rest have none, and playing a neighbour's film is
  // exactly the confusion this is here to end.
  return (best && rank(pick) < ITS_OWN_CLIP && groups.size > 1) ? null : pick;
}

/** Intersection over union of two boxes, 0 when they do not touch. */
function overlap(one, other) {
  if (!Array.isArray(one) || !Array.isArray(other) || one.length !== 4) return 0;
  const wide = Math.max(0, Math.min(one[2], other[2]) - Math.max(one[0], other[0]));
  const high = Math.max(0, Math.min(one[3], other[3]) - Math.max(one[1], other[1]));
  const both = wide * high;
  const union = (one[2] - one[0]) * (one[3] - one[1])
    + (other[2] - other[0]) * (other[3] - other[1]) - both;
  return union > 0 ? both / union : 0;
}

/** True when these frames all came from one box, so they can be shown edge to edge. */
function framesAreSteady(frames) {
  return frames.length > 1 && frames.every(a => a.clip);
}

/** Slice positions to preview: the whole event when short, evenly spread when long. */
function stillTimes(event) {
  const step = Math.max(1, Math.round(event.slices / GALLERY_FRAMES));
  const times = [];
  for (let i = 0; i < event.slices && times.length < GALLERY_FRAMES; i += step) {
    times.push(event.fromT + i * eventStep(event));
  }
  return times.length ? times : [event.fromT];
}

const eventStep = (event) => (event.slices > 1
  ? Math.round((event.toT - event.fromT) / (event.slices - 1)) || 1
  : 1);

function decodeStills(ctx, table) {
  const byTime = new Map();
  const index = table.schema.fields.findIndex(f => f.name === 'still');
  const times = table.getChildAt(table.schema.fields.findIndex(f => f.name === 't'));
  const blobs = index < 0 ? null : table.getChildAt(index);
  for (let i = 0; i < Number(table?.numRows ?? 0); i++) {
    const bytes = ctx.data.extractBinary(blobs?.get(i));
    if (bytes?.length) byTime.set(Number(times.get(i)), bytes);
  }
  return byTime;
}

/** Cycle an event's stills in place; returns a stop function.
 *
 * The frames are not all the same shape - a crop is whatever size the animal was,
 * so a squid filling the frame and a speck of a ctenophore differ wildly. Letting
 * them set their own size makes the tile jump about as it cycles, so every frame is
 * fitted into one fixed box instead.
 */
export function animateStills(host, images) {
  host.innerHTML = '';
  host.style.height = `${PREVIEW_HEIGHT}px`;
  images.forEach((img, i) => {
    img.style.cssText = `width:100%;height:100%;object-fit:${img.dataset.fit || 'contain'};`
      + `background:#06101c;display:${i ? 'none' : 'block'}`;
    host.appendChild(img);
  });
  if (images.length < 2) return () => {};
  let index = 0;
  const timer = setInterval(() => {
    images[index].style.display = 'none';
    index = (index + 1) % images.length;
    images[index].style.display = 'block';
  }, FLIPBOOK_MS);
  return () => clearInterval(timer);
}

function stillsToImages(event, stills) {
  const forRecording = stills.get(event.recording.name);
  if (!forRecording) return [];
  return stillTimes(event)
    .map(t => forRecording.get(t))
    .filter(Boolean)
    .map((bytes) => {
      const img = new Image();
      img.src = URL.createObjectURL(new Blob([bytes], { type: 'image/jpeg' }));
      img.decoding = 'async';
      return img;
    });
}

/** One stylesheet for the gallery, added once per render.
 *
 * The tiles used to be built from inline style strings, which is how they ended up
 * with a text header above a black box: every change was a string edit rather than
 * a design. A picture-first tile with the caption under it is the same shape as the
 * collection board, so the two read as one thing.
 */
const GALLERY_CSS = `
.pp-events { display: flex; flex-wrap: wrap; gap: 10px; }
.pp-event { margin: 0; width: ${PREVIEW_WIDTH}px; border: 1px solid rgba(128,128,128,.22);
            border-radius: 10px; overflow: hidden; cursor: pointer; background: transparent;
            transition: border-color .12s, transform .12s; }
.pp-event:hover { border-color: var(--pp-kind, #0d6efd); transform: translateY(-1px); }
.pp-shot { height: ${PREVIEW_HEIGHT}px; background: #06101c; display: flex;
           align-items: center; justify-content: center; font-size: 11px; color: #6c757d; }
.pp-shot img { width: 100%; height: 100%; object-fit: contain; }
.pp-event figcaption { padding: .45rem .6rem .5rem; font-size: 12px; line-height: 1.35; }
.pp-kind { display: inline-flex; align-items: center; gap: .3rem; font-size: 10px;
           text-transform: uppercase; letter-spacing: .06em; font-weight: 600;
           color: var(--pp-kind); }
.pp-kind::before { content: ""; width: 7px; height: 7px; border-radius: 50%;
                   background: var(--pp-kind); }
.pp-name { display: block; font-weight: 600; margin-top: .15rem; }
.pp-name em { font-style: normal; font-weight: 400; color: #6c757d; }
.pp-when, .pp-where { display: block; color: #868e96; font-size: 11px; }
.pp-where { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.pp-event video { width: 100%; display: block; background: #000; }
`;

function renderEventCard(grid, event, fps, stills, animals, footageBase, cleanups, showSource) {
  const meta = WINDOW_KINDS[event.kind];
  const card = document.createElement('figure');
  card.className = 'pp-event';
  card.style.setProperty('--pp-kind', meta.color);

  const shot = document.createElement('div');
  shot.className = 'pp-shot';
  card.appendChild(shot);

  const caption = document.createElement('figcaption');
  caption.innerHTML =
    `<span class="pp-kind">${meta.label}</span>`
    + eventName(event)
    + `<span class="pp-when">${formatClock(toSeconds(event.fromT, fps), fps)}`
    + ` &middot; ${formatDuration(toSeconds(event.toT - event.fromT, fps), fps, event.slices)}`
    + eventExtras(event) + `</span>`
    + (showSource ? `<span class="pp-where">${escapeHtmlText(event.recording.name)}</span>` : '');
  card.appendChild(caption);
  grid.appendChild(card);

  const images = animalImages(event, animals) ;
  const frames = images.length > 1 ? images : stillsToImages(event, stills);
  if (frames.length) cleanups.push(animateStills(shot, frames));
  else shot.textContent = footageBase ? 'click to play' : 'no stills stored';

  card.addEventListener('click', () => expandEvent(card, event, fps, footageBase));
}

/** One tile showing one animal, playing whatever crops of it the report holds.
 *
 * The same shape as a gallery card without the event around it: the timeline
 * clicks on a moment rather than on a stretch, so there is no kind, no duration
 * and no length to caption it with.
 */
function appendAnimalTile(grid, shots) {
  const card = document.createElement('figure');
  card.className = 'pp-event';
  card.style.setProperty('--pp-kind', WINDOW_KINDS.subject.color);
  const shot = document.createElement('div');
  shot.className = 'pp-shot';
  card.appendChild(shot);
  const named = shots.find(s => s.class)?.class;
  const sure = shots.map(s => Number(s.conf)).filter(Number.isFinite);
  card.appendChild(Object.assign(document.createElement('figcaption'), {
    innerHTML: (named ? `<span class="pp-name">${escapeHtmlText(named)}`
      + (sure.length ? ` <em>${Math.max(...sure).toFixed(2)}</em>` : '') + '</span>' : '')
      + `<span class="pp-when">${shots.length} frame${shots.length === 1 ? '' : 's'}</span>`,
  }));
  grid.appendChild(card);
  const steady = framesAreSteady(shots);
  const images = shots.map((animal) => {
    const image = new Image();
    image.src = `data:image/jpeg;base64,${animal.crop}`;
    image.decoding = 'async';
    image.dataset.fit = steady ? 'cover' : 'contain';
    return image;
  });
  return animateStills(shot, images);
}

/** The whole frame at a moment, when nothing was named in it. */
function appendStillTile(host, image, when) {
  const card = document.createElement('figure');
  card.className = 'pp-event';
  card.style.setProperty('--pp-kind', WINDOW_KINDS.dwell.color);
  const shot = document.createElement('div');
  shot.className = 'pp-shot';
  card.appendChild(shot);
  card.appendChild(Object.assign(document.createElement('figcaption'), {
    innerHTML: `<span class="pp-when">the frame at ${escapeHtmlText(when)}</span>`,
  }));
  const grid = appendDiv(host);
  grid.className = 'pp-events';
  grid.appendChild(card);
  animateStills(shot, [image]);
}

/** An <img> per frame of this event's animal, from the crops in the report. */
function animalImages(event, animals) {
  const frames = animalFrames(event, animals, ANIMAL_FRAMES);
  const steady = framesAreSteady(frames);
  return frames.map((animal) => {
    const image = new Image();
    image.src = `data:image/jpeg;base64,${animal.crop}`;
    image.decoding = 'async';
    // A steady clip can fill the tile; crops of differing shapes have to be letter-
    // boxed or they jump about, which is the lesser of the two evils.
    image.dataset.fit = steady ? 'cover' : 'contain';
    return image;
  });
}

/** What the tile is of, when anything named it. */
function eventName(event) {
  if (!event.topClass) return '';
  const sure = Number.isFinite(event.confidence) ? ` <em>${event.confidence.toFixed(2)}</em>` : '';
  return `<span class="pp-name">${escapeHtmlText(event.topClass)}${sure}</span>`;
}

/** The numbers worth reading at a glance, only when they were actually measured. */
function eventExtras(event) {
  const bits = [];
  if (Number.isFinite(event.detections) && event.detections > 0) {
    bits.push(`${event.detections.toFixed(1)} animals`);
  } else if (Number.isFinite(event.objects)) {
    bits.push(`${event.objects.toFixed(0)} particles`);
  }
  bits.push(`movement ${event.movement.toFixed(2)}`);
  return ` · ${bits.join(' · ')}`;
}

/** Swap the flipbook for a real player limited to the event, on click. */
function expandEvent(card, event, fps, footageBase) {
  if (card.querySelector('video')) return;
  if (!footageBase) return;
  const start = Math.floor(toSeconds(event.fromT, fps));
  const end = Math.ceil(toSeconds(event.toT, fps)) + 1;
  const player = document.createElement('video');
  player.controls = true;
  player.autoplay = true;
  player.muted = true;
  player.crossOrigin = 'anonymous';
  player.src = `${recordingUrl(footageBase, event.recording)}#t=${start},${end}`;
  player.style.cssText = 'width:100%;display:block;background:#000';
  card.appendChild(player);
}

/** Picker with an "everything" option, so events can be ranked across recordings.
 *  Ranking within one tape answers "what happened on this tape"; ranking across
 *  them answers "what should I look at first", which is the question someone with
 *  a season of footage actually has. */
function renderGalleryScope(host, recordings, onChange) {
  const select = document.createElement('select');
  select.className = 'form-select form-select-sm';
  select.style.maxWidth = '440px';
  const options = recordings.length > 1 ? [`<option value="all">All ${recordings.length} recordings</option>`] : [];
  select.innerHTML = options.concat(recordings.map((r, i) =>
    `<option value="${i}">${escapeHtmlText(r.name)}</option>`)).join('');
  select.addEventListener('change', () => onChange(select.value));
  host.appendChild(select);
  return select;
}

/** The event list as CSV, which is the form an annotation tool can actually take.
 *
 * The point of the whole exercise is to hand a person timecodes worth their
 * attention, so the list has to be able to leave the report.
 */
export function eventsToCsv(events, fpsOf) {
  const header = 'recording,start_seconds,end_seconds,start_timecode,kind,movement,score,animals,taxon,confidence';
  const lines = events.map((event) => {
    const fps = fpsOf(event);
    const start = toSeconds(event.fromT, fps);
    const end = toSeconds(event.toT, fps);
    return [csvCell(event.recording?.name ?? ''), start.toFixed(2), end.toFixed(2),
            formatClock(start, fps), event.kind, event.movement.toFixed(4), event.score.toFixed(2),
            Number.isFinite(event.detections) ? event.detections.toFixed(2) : '',
            csvCell(event.topClass ?? ''),
            Number.isFinite(event.confidence) ? event.confidence.toFixed(2) : ''].join(',');
  });
  return [header, ...lines].join('\n');
}

const csvCell = (value) => (/[",\n]/.test(value) ? `"${value.replace(/"/g, '""')}"` : value);


function downloadCsv(filename, text) {
  const url = URL.createObjectURL(new Blob([text], { type: 'text/csv' }));
  const link = document.createElement('a');
  link.href = url;
  link.download = filename;
  link.click();
  URL.revokeObjectURL(url);
}

/** One line saying what is on screen, and what was found overall. */
function renderGalleryHeadline(host, events, shownCount, recordingCount) {
  const withAnimals = events.filter(e => e.detections > 0);
  const taxa = [...new Set(withAnimals.map(e => e.topClass).filter(Boolean))];
  const line = document.createElement('div');
  line.className = 'small text-muted';
  line.style.cssText = 'width:100%;margin-bottom:2px';
  const parts = [`<strong>${events.length}</strong> event${events.length === 1 ? '' : 's'}`
    + ` across <strong>${recordingCount}</strong> recording${recordingCount === 1 ? '' : 's'}`];
  if (withAnimals.length) {
    parts.push(`<strong style="color:#0d6efd">${withAnimals.length}</strong> with animals`);
    if (taxa.length) parts.push(taxa.slice(0, 4).map(escapeHtmlText).join(', '));
  }
  if (shownCount < events.length) parts.push(`showing the top ${shownCount}`);
  line.innerHTML = parts.join(' · ');
  host.appendChild(line);
}

/** Narrow the gallery to one species; refreshed whenever the scope changes.
 *
 * Sorting does not answer "show me the cephalopods". Across a few expeditions the
 * species list runs to fifteen names, and without this the only way to see one of
 * them is to scroll past the other fourteen. Options are built as DOM nodes rather
 * than markup so a taxon name never has to be escaped into an attribute.
 */
function option(label, value) {
  const node = document.createElement('option');
  node.value = value;
  node.textContent = label;
  return node;
}

/** Whether one event survives the kind filter. */
export function keptByKind(event, kindFilter) {
  if (kindFilter === 'all') return true;
  if (kindFilter === 'found') return FINDING_KINDS.includes(event.kind);
  return event.kind === kindFilter;
}

function emptyMessage(kindFilter, speciesFilter, floor = 0, minSlices = 1) {
  if (floor > 0 || minSlices > 1) {
    return `<div class="text-muted small">Nothing here is `
      + (floor ? `over ${floor} confidence` : '')
      + (floor && minSlices > 1 ? ' and ' : '')
      + (minSlices > 1 ? `${minSlices} slices or longer` : '')
      + `. Loosen either control to see more.</div>`;
  }
  if (speciesFilter !== 'all') {
    return `<div class="text-muted small">No ${escapeHtmlText(speciesFilter)} here`
      + (kindFilter === 'all' ? '.' : ` in ${WINDOW_KINDS[kindFilter].label.toLowerCase()} stretches.`)
      + '</div>';
  }
  if (kindFilter === 'found') {
    return '<div class="text-muted small">Nothing was found here - no animal named, '
      + 'nothing moving against the background. Switch to <em>Every kind</em> to see '
      + 'what the footage was doing.</div>';
  }
  return kindFilter === 'all'
    ? '<div class="text-muted small">Movement is uniform - no stretch stands out.</div>'
    : `<div class="text-muted small">No ${WINDOW_KINDS[kindFilter].label.toLowerCase()} stretches here.</div>`;
}

export function renderSpeciesFilter(host, onChange) {
  const select = document.createElement('select');
  select.className = 'form-select form-select-sm';
  select.style.maxWidth = '190px';
  select.hidden = true;
  select.addEventListener('change', () => onChange(select.value));
  host.appendChild(select);

  // Returns the filter actually in force: a species can vanish when the scope
  // changes, and silently keeping a dead selection would empty the gallery.
  return (events) => {
    const taxa = [...new Set(events.map(e => e.topClass).filter(Boolean))].sort();
    const wanted = select.value;
    select.hidden = taxa.length < 2;
    select.replaceChildren(option('Any species', 'all'),
                           ...taxa.map(taxon => option(taxon, taxon)));
    select.value = taxa.includes(wanted) ? wanted : 'all';
    return select.value;
  };
}

function renderGalleryControls(host, has, onChange, onKind, onSpecies, onFloor,
                               onLength, onExport) {
  const bar = document.createElement('div');
  bar.className = 'd-flex gap-2 align-items-center mb-2';
  bar.innerHTML = '<span class="small text-muted">Sort by</span>';
  const select = document.createElement('select');
  select.className = 'form-select form-select-sm';
  select.style.maxWidth = '160px';
  const usable = Object.entries(SORTS)
    .filter(([, v]) => (!v.needsObjects || has.objects) && (!v.needsDetections || has.detections));
  const initial = has.detections ? 'animals' : 'interest';
  select.innerHTML = usable.map(([k, v]) =>
    `<option value="${k}"${k === initial ? ' selected' : ''}>${v.label}</option>`).join('');
  select.addEventListener('change', () => onChange(select.value));
  bar.appendChild(select);

  // Ranked purely by strangeness, dead footage crowds the top of a whole
  // collection - right for a QC pass, wrong when you came looking for animals.
  const kinds = document.createElement('select');
  kinds.className = 'form-select form-select-sm';
  kinds.style.maxWidth = '150px';
  const initialKind = has.findings ? 'found' : 'all';
  kinds.replaceChildren(
    ...(has.findings ? [option('Only what was found', 'found')] : []),
    option('Every kind', 'all'),
    ...Object.entries(WINDOW_KINDS).map(([key, meta]) => option(`${meta.label} only`, key)));
  kinds.value = initialKind;
  kinds.addEventListener('change', () => onKind(kinds.value));
  bar.appendChild(kinds);

  const refreshSpecies = renderSpeciesFilter(bar, onSpecies);

  if (has.detections) {
    const floors = document.createElement('select');
    floors.className = 'form-select form-select-sm';
    floors.style.maxWidth = '145px';
    floors.replaceChildren(...CONFIDENCE_FLOORS.map(f => option(f.label, String(f.value))));
    floors.value = String(DEFAULT_FLOOR);
    floors.addEventListener('change', () => onFloor(Number(floors.value)));
    bar.appendChild(floors);
  }

  const lengths = document.createElement('select');
  lengths.className = 'form-select form-select-sm';
  lengths.style.maxWidth = '155px';
  lengths.replaceChildren(...LENGTHS.map(l => option(l.label, String(l.value))));
  lengths.value = String(DEFAULT_LENGTH);
  lengths.addEventListener('change', () => onLength(Number(lengths.value)));
  bar.appendChild(lengths);

  const exportButton = document.createElement('button');
  exportButton.className = 'btn btn-sm btn-outline-secondary';
  exportButton.textContent = 'Download timecodes (CSV)';
  exportButton.addEventListener('click', onExport);
  bar.appendChild(exportButton);

  host.appendChild(bar);
  return refreshSpecies;
}


/** The events along a recording, as one strip of coloured bands.
 *
 * The same bands the card shades over its movement curve, on the same axis, with
 * the curve left out - which is all that fits in a tile. Where the report has no
 * measured colour this is the strip it can still draw, and it answers the same
 * question the colour strip does: is there anything in this recording, and
 * whereabouts.
 *
 * Positioned rather than painted, so it needs no canvas: each band is a div at a
 * percentage offset along the recording. A band narrower than `THINNEST_BAND` is
 * widened to it, because a one-slice event is real and a zero-width div is not
 * visible.
 */
export function appendEventStrip(host, timeline, windows) {
  if (!timeline.length || !windows.length) return false;
  const first = Number(timeline[0].t);
  const step = stepFrames(timeline);
  const span = Number(timeline[timeline.length - 1].t) + step - first;
  if (!(span > 0)) return false;
  const strip = appendDiv(host, 'position:relative;width:100%;height:100%;'
    + 'min-height:54px;border-radius:3px;overflow:hidden;background:#eef1f4');
  for (const window of windows) {
    const from = (Number(window.fromT) - first) / span;
    const to = (Number(window.toT) + step - first) / span;
    const width = Math.max(THINNEST_BAND, (to - from) * 100);
    const band = appendDiv(strip, 'position:absolute;top:0;bottom:0;'
      + `left:${(from * 100).toFixed(3)}%;width:${width.toFixed(3)}%;`
      + `background:${WINDOW_KINDS[window.kind]?.color ?? '#adb5bd'}`);
    band.className = 'pp-strip-band';
    band.title = `${WINDOW_KINDS[window.kind]?.label ?? window.kind}`
      + (window.topClass ? ` - ${window.topClass}` : '');
  }
  return true;
}

// Per cent of the strip. Below this a band is there and cannot be seen.
const THINNEST_BAND = 0.6;

// ── the footage's colour along its length ────────────────────────────────────

// How much of the timeline plot's height the colour strip takes.
const STRIP_SHARE = 0.16;
// The grid one cached still is averaged over, per side.
const AVERAGE_OVER = 4;

/** The footage's own colour along its length, as one image to lay over the plot.
 *
 * Two sources, and which one is available is the whole story of this strip. It
 * began as per-channel mean intensities and came out grey, because the pipeline
 * hands each colour channel to a separate leaf and aggregating those leaves
 * averages the three into one number - so it was redrawn from the cached stills,
 * which are in colour. That tied it to `slice_thumbnail`, and the combined report
 * drops the pictures to stay small, so the strip disappeared from exactly the
 * report that spans the most footage.
 *
 * `slice-colour` fixes the original mistake instead of working around it: three
 * floats per slice, resolved to red, green and blue where the channel is still
 * known. They cost 24 bytes against 3 KB for a still, need no JPEG decoding, and
 * survive a report with no frames in it - so they are preferred, and the stills
 * are the fallback for footage processed before the column existed.
 *
 * Returned as a data URL in the timeline's own x units, because the other half of
 * the problem was alignment: a strip drawn beside the plot had its own bucket
 * width and its own pixel scale, and lined up with the curve underneath only by
 * accident. As a Plotly layout image anchored to the x axis it cannot drift.
 */
async function barcodeImage(ctx, recording, timeline, fps) {
  const measured = await measuredColours(ctx, recording);
  const { times, colours } = measured ?? await stillColours(ctx, recording);
  if (!times.length) return null;
  const show = stretch(colours);
  const canvas = document.createElement('canvas');
  canvas.width = colours.length;
  canvas.height = 1;
  const paper = canvas.getContext('2d');
  if (!paper) return null;
  colours.forEach((colour, x) => {
    const [r, g, b] = show(colour);
    paper.fillStyle = `rgb(${r},${g},${b})`;
    paper.fillRect(x, 0, 1, 1);
  });
  return {
    url: canvas.toDataURL('image/png'),
    from: toSeconds(times[0], fps),
    to: toSeconds(times[times.length - 1] + (times[1] - times[0] || 1), fps),
  };
}

/** Brightness and saturation stretched so deep-sea footage can be told apart.
 *
 * Measured on the reports this was written for, the mean colour of a cached still
 * has a channel spread of 10 to 32 out of 255 - EX2503's lit seafloor is 10. That
 * is a real tint and it is invisible: drawn raw the strip is a column of nearly
 * identical greys, which is exactly the complaint the per-channel version got.
 *
 * So two stretches, and they do different jobs. Brightness is stretched over the
 * strip's own percentiles, which is what makes a dim tape readable at all.
 * Saturation is stretched about each column's own grey point, which is what makes
 * a green-gold seafloor distinguishable from blue midwater.
 *
 * The hue is the footage's; the *strength* of it is exaggerated on purpose, and
 * the widget says so. A colour here is a landmark for finding your way along a
 * recording, not a measurement of what colour the water was.
 */
function stretch(colours) {
  const luma = colours.map(([r, g, b]) => 0.299 * r + 0.587 * g + 0.114 * b);
  const low = percentile(luma, 2);
  const span = (percentile(luma, 98) - low) || 1;
  return (colour) => {
    const grey = (colour[0] + colour[1] + colour[2]) / 3;
    const lit = Math.max(0, Math.min(1, (grey - low) / span));
    // Back to 8-bit around a brightness the stretch has spread out, with the
    // departure from grey amplified.
    return colour.map(value => Math.max(0, Math.min(255,
      Math.round(40 + 200 * lit + SATURATION * (value - grey)))));
  };
}

// How much the departure from grey is exaggerated. Three makes a 10-level tint a
// 30-level one, which is the difference between a strip that reads and one that
// does not.
const SATURATION = 3;

/** The colour of every slice, as the pipeline measured it.
 *
 * Null rather than an empty strip when the columns are absent, so the caller can
 * fall back to the stills instead of reporting footage as colourless.
 */
export async function measuredColours(ctx, recording) {
  const { q } = ctx.sql;
  if (!CHANNEL_COLUMNS.every(col => ctx.schema.allCols.includes(col))) return null;
  const [red, green, blue] = CHANNEL_COLUMNS;
  const rows = await ctx.queryRows(`
    SELECT ${q('dim_t')} AS t, ${q(red)} AS r, ${q(green)} AS g, ${q(blue)} AS b
    FROM ${sliceTable(ctx)}
    WHERE ${recordingKey(ctx)} = ${literal(recording.name)}
      AND ${q('dim_t')} IS NOT NULL AND ${q(red)} IS NOT NULL
    ORDER BY 1`);
  if (!rows.length) return null;
  return {
    times: rows.map(row => Number(row.t)),
    colours: rows.map(row => scaleColour([row.r, row.g, row.b])),
  };
}

// The columns `slice-colour` writes, in the order they are painted in.
const CHANNEL_COLUMNS = ['slice_red', 'slice_green', 'slice_blue'];

/** Channel means to 8-bit, whatever the footage's own range was.
 *
 * Recordings arrive as 8-bit and as 10-bit, and the means are in the source's
 * units, so a fixed divisor would paint half the archive black. The largest
 * channel decides which range this is - `stretch` handles the rest, and it works
 * off the strip's own percentiles anyway.
 */
export function scaleColour(channels) {
  const values = channels.map(v => (Number.isFinite(Number(v)) ? Number(v) : 0));
  const top = Math.max(...values);
  const full = top <= 1.001 ? 1 : top <= 255 ? 255 : top <= 1023 ? 1023 : 65535;
  return values.map(v => Math.max(0, Math.min(255, Math.round(255 * v / full))));
}

/** The same colours, averaged out of the cached stills.
 *
 * The older path, kept because a report processed before `slice-colour` existed
 * still has its pictures and there is no reason to show it a blank strip.
 */
async function stillColours(ctx, recording) {
  const empty = { times: [], colours: [] };
  if (!ctx.schema.allCols.includes('slice_thumbnail')) return empty;
  const { q } = ctx.sql;
  const table = await ctx.query(`
    SELECT ${q('dim_t')} AS t, ${q('slice_thumbnail')} AS still
    FROM ${sliceTable(ctx)}
    WHERE ${recordingKey(ctx)} = ${literal(recording.name)}
      AND ${q('dim_t')} IS NOT NULL AND ${q('slice_thumbnail')} IS NOT NULL
    ORDER BY 1`);
  const stills = decodeStills(ctx, table);
  if (!stills.size) return empty;
  const times = [...stills.keys()].sort((a, b) => a - b);
  const colours = [];
  for (const t of times) {
    const colour = await averageColour(stills.get(t));
    colours.push(colour ?? colours[colours.length - 1] ?? [20, 30, 45]);
  }
  return { times, colours };
}

/** The mean colour of one cached still.
 *
 * Averaged over a small grid rather than by drawing into a single pixel: a
 * one-pixel destination leaves the browser free to pick a sampling filter, and
 * one sampled pixel of a dim frame is not its colour. Sixteen are.
 */
async function averageColour(bytes) {
  try {
    const bitmap = await createImageBitmap(new Blob([bytes], { type: 'image/jpeg' }));
    const canvas = document.createElement('canvas');
    canvas.width = canvas.height = AVERAGE_OVER;
    const paper = canvas.getContext('2d', { willReadFrequently: true });
    if (!paper) return null;
    paper.drawImage(bitmap, 0, 0, AVERAGE_OVER, AVERAGE_OVER);
    bitmap.close?.();
    const { data } = paper.getImageData(0, 0, AVERAGE_OVER, AVERAGE_OVER);
    const total = [0, 0, 0];
    for (let i = 0; i < data.length; i += 4) {
      total[0] += data[i]; total[1] += data[i + 1]; total[2] += data[i + 2];
    }
    const pixels = data.length / 4;
    return total.map(sum => Math.round(sum / pixels));
  } catch {
    return null;
  }
}


/** The slice the pointer is over, or null when it is past the end of the timeline.
 *
 * Slices are evenly spaced, so this is arithmetic rather than a search - but the
 * pointer can also sit in the half-slice of padding at either end, and rounding
 * blindly would report the first slice for a position before the recording starts.
 */
export function sliceAt(timeline, second, fps) {
  if (!timeline.length) return null;
  const step = toSeconds(stepFrames(timeline), fps);
  const index = Math.round((second - toSeconds(Number(timeline[0].t), fps)) / (step || 1));
  return index >= 0 && index < timeline.length ? timeline[index] : null;
}

/** What was in frame there, said in as few words as the row supports. */
export function describeMoment(row) {
  if (!row) return '';
  const animals = Number(row.detections);
  if (!(animals > 0)) return '';
  const name = row.top_class ? ` ${row.top_class}` : '';
  const sure = Number.isFinite(Number(row.confidence)) ? ` ${Number(row.confidence).toFixed(2)}` : '';
  return ` · ${animals}${name}${sure}`;
}

/** Minute markers under the strip, so a position can be read without hovering. */
function minuteTicks(barcode, totalSeconds, scale, fps) {
  const step = tickStep(totalSeconds);
  const axis = document.createElement('div');
  axis.style.cssText = `position:relative;height:12px;width:${Math.round(barcode.columns.length * scale)}px`;
  for (let at = 0; at <= totalSeconds; at += step) {
    const label = document.createElement('span');
    label.textContent = formatClock(at, fps).replace(/^00:/, '');
    label.style.cssText = `position:absolute;font-size:9px;color:#adb5bd;transform:translateX(-50%);`
      + `left:${(at / barcode.secondsPerBucket) * scale}px`;
    axis.appendChild(label);
  }
  return axis;
}

const tickStep = (totalSeconds) => (totalSeconds > 2400 ? 600 : totalSeconds > 600 ? 300 : 60);

function escapeHtmlText(value) {
  const node = document.createElement('span');
  node.textContent = String(value);
  return node.innerHTML;
}


// ── review triage ─────────────────────────────────────────────────────────────

const DEAD_FOOTAGE_WARN = 0.05;   // flag a recording once this much of it is dead

/** Per-recording totals: how much of it is dead, held, busy, and how many events. */
export function summariseRecording(recording, timeline, windows) {
  const fps = Number(recording.fps) || null;
  const seconds = (kind) => windows
    .filter(w => w.kind === kind)
    .reduce((total, w) => total + toSeconds(w.toT - w.fromT, fps), 0);
  const total = toSeconds(timeline.length * stepFrames(timeline), fps);
  return {
    recording, fps, total, events: windows.length,
    dead: seconds('frozen'), held: seconds('dwell'),
    empty: seconds('empty'), busy: seconds('active'),
    animals: meanOf(timeline.map(r => Number(r.detections))),
    topClass: mostConfidentClass(timeline),
    species: speciesIn(timeline),
  };
}

/** Frames between consecutive slices, so a row count can be turned into a duration. */
function stepFrames(timeline) {
  return timeline.length > 1 ? Number(timeline[1].t) - Number(timeline[0].t) : 1;
}

function triageHeadline(summaries) {
  const total = summaries.reduce((sum, s) => sum + s.total, 0);
  const dead = summaries.reduce((sum, s) => sum + s.dead, 0);
  const held = summaries.reduce((sum, s) => sum + s.held, 0);
  return { total, dead, held, deadShare: total ? dead / total : 0 };
}


// Everything `findWindows` and `summariseRecording` read, and nothing else. The
// rest of a timeline row - the peak, the confidence, the camera speed, the depth,
// the clock - belongs to the widgets that draw them, and over every slice of a
// collection it is a third of a million values fetched to be ignored.
const SUMMARY_FIELDS = ['structure', 'detections', 'movers', 'top_class'];

let summariesFor = null;        // one report's summaries, kept while it is open

async function collectSummaries(ctx) {
  // The preview asks for these and then the preview plot asks again, which was
  // the same twelve queries twice over on a collection.
  if (summariesFor && summariesFor.key === summaryKey(ctx)) return summariesFor.value;
  const recordings = await fetchRecordings(ctx);
  const timelines = await fetchTimelines(ctx, recordings, { only: SUMMARY_FIELDS });
  const summaries = [];
  for (const recording of recordings) {
    const timeline = timelines.get(String(recording.name)) ?? [];
    if (!timeline.length) continue;
    summaries.push(summariseRecording(recording, timeline, findWindows(timeline)));
  }
  summariesFor = { key: summaryKey(ctx), value: summaries };
  return summaries;
}

/** What makes one report's summaries different from another's. */
function summaryKey(ctx) {
  return [sliceTable(ctx), ctx.where ?? '', ctx.state?.groupCol ?? ''].join('|');
}

// ── plugins ─────────────────────────────────────────────────────────────────

const timelineWidget = {
  id: 'temporal-timeline',
  required_inputs: ['frame_difference', 'dim_<axis>'],
  inputs: ['frame_difference_max', 'laplacian_variance', 'std_intensity',
    'bright_particle_count', 'detection_count', 'detection_top_class',
    'detection_confidence', 'detections', 'moving_object_count', 'camera_speed',
    'depth_m', 'recorded_at', 'slice_red', 'slice_green', 'slice_blue',
    'fps', 'name', 'path', 'child_id', 'source_url'],
  group: 'Visualization',
  scope: 'slice',
  label: 'Footage Timeline',
  shortLabel: 'Timeline',
  info: [
    'One recording, read three ways on a shared time axis.',
    '',
    'The **colour strip** across the top is the mean of each colour channel per slice, one column',
    'each. It sits inside the plot so a band of colour and the movement below it are the same',
    'moment. A dark band is unlit water or lights off; a flat unchanging band is often dead tape;',
    'an abrupt colour break is a scene or camera change.',
    '',
    'Brightness and saturation are stretched. A deep-sea frame\'s mean colour spans ten to thirty',
    'levels out of 255, which is a real tint and invisible drawn raw. The hue is the footage\'s, the',
    'strength of it is exaggerated - a landmark for navigating a recording, not a measurement.',
    '',
    'Below, **frame-to-frame movement**, with shaded bands over the stretches worth opening:',
    '',
    '- **Frozen** - effectively no change between frames: duplicated, dropped, or frozen tape.',
    '- **Dwell** - the camera holding still on something with detail in the frame.',
    '- **Empty** - holding still on almost nothing. Moves like a dwell; only the amount of detail separates them.',
    '- **Subject** - a detector named an animal here.',
    '- **Unnamed** - something moved independently of the camera and no detector named it.',
    '- **Activity** - large change: transit, a cut, or a subject entering the frame.',
    '',
    'Where the report carries cached pictures, clicking a blue animal marker shows the animals',
    'found at that moment, one tile per creature.',
    '',
    'Reports with no measured colour in them have no strip; the preview tile then shows the event',
    'bands on their own.',
  ].join('\n'),

  requires(schema) {
    // dim_* columns are tracked in dimCols, never in allCols.
    return schema.allCols.includes('frame_difference') && (schema.dimCols ?? []).includes('dim_t');
  },

  async overviewMessage(ctx) {
    try {
      const [recording] = await fetchRecordings(ctx);
      if (!recording) return null;
      const windows = findWindows(await fetchTimeline(ctx, recording));
      const frozen = windows.filter(w => w.kind === 'frozen');
      const fps = Number(recording.fps) || null;
      // Where a detector ran, what it found is the headline. Frozen footage is a
      // real warning, but it is the answer to a question nobody opened this to ask.
      const subjects = windows.filter(w => w.kind === 'subject');
      if (subjects.length) {
        const taxa = new Set(subjects.map(w => w.topClass).filter(Boolean));
        return `<strong>${subjects.length}</strong> stretch${subjects.length === 1 ? '' : 'es'} with animals in `
          + `<strong>${escapeHtmlText(recording.name)}</strong>`
          + (taxa.size ? `, ${taxa.size} species: ${[...taxa].slice(0, 3).map(escapeHtmlText).join(', ')}.` : '.');
      }
      if (frozen.length) {
        const dead = frozen.reduce((sum, w) => sum + toSeconds(w.toT - w.fromT, fps), 0);
        return { text: `<strong>${formatDuration(dead, fps, frozen.length)}</strong> of frozen footage in <strong>${escapeHtmlText(recording.name)}</strong>.`, warning: true };
      }
      const dwell = windows.filter(w => w.kind === 'dwell').length;
      return `<strong>${windows.length}</strong> notable stretches, <strong>${dwell}</strong> of them the camera holding still.`;
    } catch { return null; }
  },

  /** The recording read along its length, which is what the card is.
   *
   * Either of the two strips the card draws, never a summary of them: a tile
   * showing a distribution the card does not contain is a preview of a different
   * widget. The colour strip first, and where the report carries no measured
   * colour, the events along the same axis - both answer "what is in this
   * recording, and where" at tile size, which a quartile cannot.
   */
  async overviewPlot(container, ctx) {
    const [recording] = await fetchRecordings(ctx);
    if (!recording) return false;
    const fps = Number(recording.fps) || null;
    const timeline = await fetchTimeline(ctx, recording);
    const strip = await barcodeImage(ctx, recording, timeline, fps);
    if (!strip) return appendEventStrip(container, timeline, findWindows(timeline));
    const frame = appendDiv(container,
      'position:relative;width:100%;height:100%;min-height:54px;overflow:hidden;border-radius:3px');
    const image = new Image();
    image.src = strip.url;
    image.alt = `Colour along ${recording.name}`;
    // One pixel tall and as many wide as there are slices, stretched to the tile:
    // the columns are the data, so smoothing across them would invent colours
    // between two slices that were never in the footage.
    image.style.cssText = 'width:100%;height:100%;display:block;'
      + 'image-rendering:pixelated;object-fit:fill';
    frame.appendChild(image);
    return true;
  },

  async render(container, ctx) {
    const recordings = await fetchRecordings(ctx);
    if (!recordings.length) {
      container.innerHTML = '<div class="no-data">No time slices found. Process with a T slice size, e.g. --slice-size T=30.</div>';
      return;
    }

    // One recording at a time, read from three angles that belong together: the
    // colour of the footage over its length, the movement curve with the events
    // marked on it, and - where the report cached pictures - the animals at
    // whatever moment was clicked. This was three widgets and the barcode was one
    // of them; a barcode of a single recording is the same act of exploring it.
    const picker    = appendRecordingPicker(container, ctx, recordings);
    const stripHost = appendDiv(container);
    const plotHost  = appendDiv(container);
    const tileHost  = appendDiv(container, 'margin-top:8px');
    const sheet = document.createElement('style');
    sheet.textContent = GALLERY_CSS;
    container.appendChild(sheet);
    let cleanups = [];

    const draw = async (recording) => {
      cleanups.forEach(stop => stop());
      cleanups = [];
      stripHost.innerHTML = plotHost.innerHTML = tileHost.innerHTML = '';
      const fps = Number(recording.fps) || null;
      const timeline = await fetchTimeline(ctx, recording);
      if (!timeline.length) {
        plotHost.innerHTML = '<div class="no-data">No movement values for this recording.</div>';
        return;
      }
      const windows = findWindows(timeline);
      renderKindLegend(stripHost, new Set(windows.map(w => w.kind)));

      const showAt = (seconds) => showAnimalsAt(tileHost, ctx, recording, seconds, fps, cleanups);
      const strip = await barcodeImage(ctx, recording, timeline, fps);
      if (!strip) {
        // Silence here cost an afternoon: a strip that cannot be drawn and simply
        // was not looks exactly like a bug, so say which of the two sources is
        // missing rather than nothing at all.
        const why = appendDiv(stripHost);
        why.className = 'text-muted small';
        why.textContent = CHANNEL_COLUMNS.every(col => ctx.schema.allCols.includes(col))
          ? 'No colour measured for this recording, so there is no colour strip.'
          : 'This report has no per-channel slice colour in it, so there is no '
            + 'colour strip. Process with the slice-colour processor for one.';
      }
      renderTimelinePlot(plotHost, ctx, timeline, windows, fps,
                         (w) => showAt(toSeconds(w.fromT, fps)),
                         (seconds) => showAt(seconds), strip);
      if (hasPictures(ctx.schema)) {
        const hint = appendDiv(tileHost);
        hint.className = 'text-muted small';
        hint.textContent = 'Click a point on the curve to see the animals found at '
          + 'that moment.';
      }
    };

    picker.addEventListener('change', () => draw(recordings[Number(picker.value)]));
    await draw(recordings[0]);
  },
};

/** Whether the report cached anything to look at, as against numbers to plot. */
export function hasPictures(schema) {
  return schema.allCols.includes('detections')
    || schema.allCols.includes('slice_thumbnail');
}

/** The animals the detector wrote down at one moment, as tiles.
 *
 * The blue points on the curve are a count, and a count is the one thing about a
 * sighting that does not tell you whether to believe it. Everything needed to
 * show the animals themselves is already in the row the point came from.
 */
async function showAnimalsAt(host, ctx, recording, seconds, fps, cleanups) {
  host.innerHTML = '';
  if (!hasPictures(ctx.schema)) {
    host.innerHTML = '<div class="text-muted small">This report carries no cached '
      + 'pictures, so there is nothing to show at that moment - only the counts on '
      + 'the curve above.</div>';
    return;
  }
  const at = Math.max(0, Math.round(seconds * (fps || 1)));
  const event = {
    recording, fromT: at, toT: at, slices: 1, kind: 'subject',
    confidence: NaN, topClass: null,
  };
  const [animals, stills] = await Promise.all([
    fetchEventAnimals(ctx, [event]), fetchEventStills(ctx, [event])]);
  const frames = animalFrames(event, animals, GALLERY_FRAMES * 4);
  const heading = appendDiv(host, 'margin-bottom:4px');
  heading.className = 'small';
  const when = formatClock(seconds, fps);
  if (!frames.length) {
    const still = stillsToImages(event, stills);
    heading.innerHTML = `Nothing named at <strong>${when}</strong>.`;
    if (still.length) appendStillTile(host, still[0], when);
    return;
  }
  // One tile per animal, not one per crop: the same creature is written down once
  // per frame the detector looked at, and a wall of the same shrimp eight times
  // reads as eight shrimp.
  const byAnimal = new Map();
  for (const frame of frames) {
    const key = `${frame.of ?? 'x'}/${(frame.box ?? []).join(',')}`;
    if (!byAnimal.has(key)) byAnimal.set(key, []);
    byAnimal.get(key).push(frame);
  }
  heading.innerHTML = `<strong>${byAnimal.size}</strong> animal`
    + `${byAnimal.size === 1 ? '' : 's'} at <strong>${when}</strong>`;
  const grid = appendDiv(host);
  grid.className = 'pp-events';
  for (const shots of byAnimal.values()) {
    cleanups.push(appendAnimalTile(grid, shots));
  }
}

function appendDiv(host, style = '') {
  const div = document.createElement('div');
  if (style) div.style.cssText = style;
  host.appendChild(div);
  return div;
}

function appendRecordingPicker(container, ctx, recordings) {
  const wrap = document.createElement('div');
  wrap.className = 'mb-2';
  const select = document.createElement('select');
  select.className = 'form-select form-select-sm';
  select.style.maxWidth = '480px';
  select.innerHTML = recordings.map((r, i) =>
    `<option value="${i}">${ctx.plot.escapeHtml(r.name)} (${r.slices} slices)</option>`).join('');
  if (recordings.length > 1) { wrap.appendChild(select); container.appendChild(wrap); }
  return select;
}

const galleryWidget = {
  id: 'temporal-gallery',
  required_inputs: ['frame_difference', 'dim_<axis>'],
  inputs: ['frame_difference_max', 'laplacian_variance', 'std_intensity',
    'bright_particle_count', 'detection_count', 'detection_top_class',
    'detection_confidence', 'detections', 'moving_object_count', 'camera_speed',
    'fps', 'name', 'path', 'child_id', 'source_url',
    'slice_thumbnail', 'detection_crop'],
  group: 'Visualization',
  scope: 'slice',
  label: 'Event Gallery',
  shortLabel: 'Events',
  info: [
    'One tile per detected event, ranked so the most unusual come first.',
    '',
    'Tiles come out of the report itself, so they need no access to the recordings. Where a',
    'detector found something the tile shows a close-up of the animal and animates the crops it',
    'has, a tenth of a second apart; otherwise it shows the slice stills. Clicking a tile plays',
    'that stretch, if a footage base URL is set.',
    '',
    '**Ranking** multiplies how far an event sits from the recording\'s normal movement by how long',
    'it holds, so a sustained stretch outranks a one-second spike. Where a detector ran, the',
    'default is to rank by animals instead, and the list can be narrowed to one species or sorted',
    'by confidence.',
    '',
    '**Filter.** Where the report can find things - a detector, motion, particles - the gallery',
    'shows only those and leaves out the bands that merely describe the footage, which would',
    'otherwise be most of the tiles. Switch to *every kind* for a QC pass.',
    '',
    'Two controls are judgements rather than facts. A **confidence floor**, because on benthic',
    'footage a detection under 0.4 is more often a rock than an animal, and a **minimum length**,',
    'because a one-slice stretch is a threshold crossing rather than an encounter. Both hide real',
    'things as well - loosen them and look.',
    '',
    '**Download timecodes** writes the ranked list as CSV, species and confidence included.',
  ].join('\n'),

  // Without cached pictures every tile reads "no stills stored", which is a wall
  // of empty boxes describing stretches a reader cannot see. The timeline and the
  // taxonomy still say what a report like that found.
  requires: (schema) => timelineWidget.requires(schema) && hasPictures(schema),

  async overviewMessage(ctx) {
    try {
      const [recording] = await fetchRecordings(ctx);
      if (!recording) return null;
      const timeline = await fetchTimeline(ctx, recording);
      const events = scoreWindows(findWindows(timeline), timeline);
      if (!events.length) return null;
      const top = [...events].sort(SORTS.interest.compare)[0];
      const fps = Number(recording.fps) || null;
      return `<strong>${events.length}</strong> events; the most unusual is a `
        + `<strong>${WINDOW_KINDS[top.kind].label.toLowerCase()}</strong> stretch at `
        + `<strong>${formatClock(toSeconds(top.fromT, fps), fps)}</strong>.`;
    } catch { return null; }
  },

  /** A few of the animals, as a contact sheet.
   *
   * The one widget in the report whose whole subject is pictures, and it was the
   * one with no preview - a tile showing the group emoji for a gallery of
   * creatures. There is nothing to plot here: what a reader wants to know before
   * opening it is what is in it, and the honest summary of that is the first few
   * tiles. Crops where the report has them, whole frames otherwise, which is the
   * same order of preference the gallery itself uses.
   */
  async overviewPlot(container, ctx) {
    const shots = await previewShots(ctx, PREVIEW_TILES);
    if (!shots.length) return false;
    const sheet = appendDiv(container,
      `display:grid;grid-template-columns:repeat(${shots.length}, 1fr);`
      + 'gap:2px;width:100%;height:100%;min-height:54px');
    for (const shot of shots) {
      const frame = appendDiv(sheet, 'position:relative;overflow:hidden;border-radius:2px');
      const image = new Image();
      image.src = shot.src;
      image.alt = shot.label ?? 'a detected animal';
      image.title = shot.label ?? '';
      image.decoding = 'async';
      image.style.cssText = 'width:100%;height:100%;object-fit:cover;display:block';
      frame.appendChild(image);
    }
    return true;
  },

  async render(container, ctx) {
    const recordings = await fetchRecordings(ctx);
    if (!recordings.length) {
      container.innerHTML = '<div class="no-data">No time slices found. Process with a T slice size, e.g. --slice-size T=30.</div>';
      return;
    }

    let footageBase = initialFootageBase();
    let sortKey = ctx.schema.allCols.includes('detection_count') ? 'animals' : 'interest';
    // Open on what was found where the report can find things. A dive is mostly
    // camera movement over open water, and a gallery that shows all of it is a
    // gallery of water.
    let kindFilter = reportFindsThings(ctx.schema) ? 'found' : 'all';
    let speciesFilter = 'all';
    let floor = ctx.schema.allCols.includes('detections') ? DEFAULT_FLOOR : 0;
    let minSlices = DEFAULT_LENGTH;
    let scope = recordings.length > 1 ? 'all' : '0';
    let cleanups = [];

    const scopeHost = appendDiv(container, 'margin-bottom:6px');
    renderGalleryScope(scopeHost, recordings, (value) => { scope = value; draw(); });
    const controlHost = appendDiv(container);
    const sheet = document.createElement('style');
    sheet.textContent = GALLERY_CSS;
    container.appendChild(sheet);
    const gridHost = appendDiv(container);
    gridHost.className = 'pp-events';
    // The gallery does not ask for a footage base URL. Everything it shows comes
    // out of the parquet - the stills, the clips, the boxes - so the question was
    // only ever about playing the original recording behind a tile, and it took a
    // paragraph of explanation to ask. The timeline still offers it, and
    // ?footage=<base-url> still prefills it here for anyone who wants it.
    let latest = [];
    const refreshSpecies = renderGalleryControls(controlHost,
      { objects: ctx.schema.allCols.includes('bright_particle_count'),
        detections: ctx.schema.allCols.includes('detection_count'),
        findings: reportFindsThings(ctx.schema) },
      (value) => { sortKey = value; draw(); },
      (value) => { kindFilter = value; draw(); },
      (value) => { speciesFilter = value; draw(); },
      (value) => { floor = value; draw(); },
      (value) => { minSlices = value; draw(); },
      () => downloadCsv('footage-events.csv',
                        eventsToCsv(latest, e => Number(e.recording.fps) || null)));

    async function draw() {
      cleanups.forEach(stop => stop());
      cleanups = [];
      gridHost.innerHTML = '<span class="text-muted small">Reading timelines…</span>';
      const chosen = scope === 'all' ? recordings : [recordings[Number(scope)]];
      const found = await collectEvents(ctx, chosen);
      speciesFilter = refreshSpecies(found);
      const events = found
        .filter(event => keptByKind(event, kindFilter))
        .filter(event => keptByQuality(event, floor, minSlices))
        .filter(event => speciesFilter === 'all' || event.topClass === speciesFilter)
        .sort(SORTS[sortKey].compare);
      latest = events;
      gridHost.innerHTML = '';
      if (!events.length) {
        gridHost.innerHTML = emptyMessage(kindFilter, speciesFilter, floor, minSlices);
        return;
      }
      const shown = events.slice(0, GALLERY_MAX);
      renderGalleryHeadline(gridHost, events, shown.length, chosen.length);
      const stills = await fetchEventStills(ctx, shown);
      const animals = await fetchEventAnimals(ctx, shown);
      for (const event of shown) {
        renderEventCard(gridHost, event, Number(event.recording.fps) || null,
                        stills, animals, footageBase, cleanups, chosen.length > 1);
      }
    }

    await draw();
  },
};

const triageWidget = {
  id: 'temporal-triage',
  required_inputs: ['frame_difference', 'dim_<axis>'],
  inputs: ['frame_difference_max', 'laplacian_variance', 'std_intensity',
    'bright_particle_count', 'detection_count', 'detection_top_class',
    'detection_confidence', 'detections', 'moving_object_count', 'camera_speed',
    'depth_m', 'recorded_at',
    'fps', 'name', 'path', 'child_id', 'source_url'],
  group: 'Summary',
  scope: 'slice',
  label: 'Footage Triage',
  shortLabel: 'Triage',
  info: [
    'Which recordings are worth a person\'s time, as distributions rather than totals.',
    '',
    'The first four plots are **what the footage was doing**, as a percentage of each recording:',
    '',
    '- **Frozen** - never changes: duplicated tape, a dropped feed, a frozen frame. Time no one should spend watching.',
    '- **Dwell** - the camera holding still on something with detail in frame.',
    '- **Empty** - holding still on open water or a blank field. Moves like a dwell; only the detail in frame separates them.',
    '- **Activity** - large frame-to-frame change: transit, cuts, subjects entering frame.',
    '',
    'One point per recording, so a wide spread means the collection is uneven and a tight one',
    'means it is uniformly good or uniformly bad. The headline above gives the totals.',
    '',
    'Below, the **per-slice measurements** each verdict is derived from - movement, animals per',
    'frame, camera speed, independent movers, depth - each with a note on what it means and which',
    'direction is which. Only the columns this report actually has appear.',
    '',
    'Plot kind follows the amount of data: a bar for one point per group, every point for a few, a',
    'violin for many, a box once there are more than are worth sending to the browser. Enable',
    'statistical comparisons in the sidebar for pairwise tests between groups.',
  ].join('\n'),

  requires: (schema) => timelineWidget.requires(schema),

  async overviewMessage(ctx) {
    try {
      const summaries = await collectSummaries(ctx);
      if (!summaries.length) return null;
      const { total, dead, deadShare } = triageHeadline(summaries);
      const fps = summaries[0].fps;
      const text = `<strong>${formatClock(total, fps)}</strong> of footage across `
        + `<strong>${summaries.length}</strong> recording${summaries.length === 1 ? '' : 's'}; `
        + `<strong>${formatClock(dead, fps)}</strong> of it is dead.`;
      return deadShare >= DEAD_FOOTAGE_WARN ? { text, warning: true } : text;
    } catch { return null; }
  },

  /** How much of each recording is dead, as the engine's compact preview.
   *
   * This was a single stacked bar of the four verdicts, built here by hand, and it
   * read as a striped ribbon with no axis - four numbers stacked to 100% say what
   * the footage was doing only if you already know the colours. One verdict as a
   * distribution says the thing triage is for: whether the dead footage is spread
   * evenly across the collection or concentrated in a few recordings worth
   * throwing out. Same engine, and so the same plot kind, as everything else.
   */
  async overviewPlot(container, ctx) {
    const distribution = ctx.plot.engine?.renderDistribution;
    if (!distribution) return false;
    const source = verdictSource(await collectSummaries(ctx), WINDOW_KINDS.frozen.label);
    if (!source) return false;
    return distribution(container, ctx, {
      numCol:          'share',
      source,
      catSql:          'grp',
      yLabel:          'per cent dead',
      series:          { isCategory: true },
      categoriesOrder: ctx.groups,
      catLabelFn:      ctx.groupLabel,
      allPointsBelow:  500,
      mini:            true,
    });
  },

  async render(container, ctx) {
    const summaries = await collectSummaries(ctx);
    if (!summaries.length) {
      container.innerHTML = '<div class="no-data">No time slices found. Process with a T slice size, e.g. --slice-size T=30.</div>';
      return;
    }
    const { total, dead, held } = triageHeadline(summaries);
    const fps = summaries[0].fps;
    const headline = appendDiv(container, 'margin-bottom:8px');
    headline.className = 'small';
    headline.innerHTML = `<strong>${formatClock(total, fps)}</strong> of footage · `
      + `<span style="color:${WINDOW_KINDS.frozen.color}"><strong>${formatClock(dead, fps)}</strong> dead</span> · `
      + `<span style="color:${WINDOW_KINDS.dwell.color}"><strong>${formatClock(held, fps)}</strong> held</span>`;
    await renderTriagePlots(appendDiv(container), ctx, summaries);
  },
};

/** What the footage was doing, as distributions rather than as totals.
 *
 * Triage asks a comparison - which of these is worth a person's time - and a
 * table of durations was the hardest possible way to make one. Bars were not much
 * better: four stacked totals per recording say how much of it was dead without
 * saying anything about the footage.
 *
 * Two kinds of distribution, because the triage has two kinds of number. The
 * verdicts are shares of a recording and are computed here, from the windows; the
 * measurements are per-slice columns and go through the viewer's own distribution
 * engine, so they get the large-data box fallback, the significance test and the
 * grouping without a reimplementation.
 */
async function renderTriagePlots(host, ctx, summaries) {
  await renderVerdictShares(appendDiv(host), ctx, summaries);
  await renderMeasurements(appendDiv(host, 'margin-top:14px'), ctx);
}

/** The per-slice measurements, drawn the way the quality-metric widget draws its own.
 *
 * That widget is the reference for what a distribution card in this viewer looks
 * like, and this one was reinventing the parts of it that are visible: its own
 * flex wrapper instead of `ctx.plot.flexGrid`, its own forced 380px height
 * instead of a cell wide enough for the engine to size itself in, a plain string
 * where the engine takes a note with a direction badge, and no significance even
 * when the reader has asked for it. Every one of those is a setting the engine
 * already has.
 */
async function renderMeasurements(host, ctx) {
  const distribution = ctx.plot.engine?.renderDistribution;
  const columns = triageColumns(ctx.schema);
  if (!distribution || !columns.length) return;
  const { flexGrid, groupingLabel } = ctx.plot;
  const source = sliceSource(ctx);
  const counted = await countMeasured(ctx, columns, source);

  // Two per row once the notes are showing, because a note sits beside its plot
  // and three columns of both is a cramped read - the same cap the quality
  // widget applies for the same reason.
  const perRow = ctx.state?.showInfo ? 2 : (ctx.groups?.length ?? 1) <= 2 ? 3 : 2;
  const { wrap, flexBasisPct } = flexGrid(host, perRow);

  for (const { col, label, unit, why } of columns) {
    const n = counted[col] ?? 0;
    if (!n) continue;
    const cell = appendDiv(wrap,
      `flex:0 0 ${flexBasisPct}%;min-width:300px;margin-bottom:20px;box-sizing:border-box`);
    await distribution(cell, ctx, {
      numCol:          col,
      source,
      catSql:          groupExpr(ctx),
      catLabel:        groupingLabel(''),
      yLabel:          unit,
      title:           `${label}<br><sup>one point per slice; n=${n.toLocaleString()}</sup>`,
      showSignificance: !!ctx.state?.showSignificance,
      maxRawPoints:    ctx.plot.engine.CONSTANTS?.MAX_VIOLIN_POINTS,
      series:          { isCategory: true },
      categoriesOrder: ctx.groups,
      catLabelFn:      ctx.groupLabel,
      sideInfo:        why,
    });
    if (!cell.childElementCount) cell.remove();
  }
  if (!wrap.childElementCount) {
    host.innerHTML = '<div class="no-data">Nothing measured per slice to compare.</div>';
  }
}

/** How many slices each column was actually measured on.
 *
 * One query for all of them rather than one each, and it goes in the subtitle -
 * a distribution with no sample size beside it is the plot people over-read
 * hardest, and these columns are optional processors, so "n=12" and "n=90,000"
 * both happen.
 */
async function countMeasured(ctx, columns, source) {
  const { q } = ctx.sql;
  const selects = columns
    .map(({ col }, i) => `COUNT(${q(col)}) AS c${i}`).join(', ');
  const [row = {}] = await ctx.queryRows(
    `SELECT ${selects} FROM ${source.table} ${source.where}`);
  return Object.fromEntries(columns.map(({ col }, i) => [col, Number(row[`c${i}`] ?? 0)]));
}

/** The rows that are one time slice each, pinned the way every other widget pins them.
 *
 * Through `dimSubsetWhere`, which is the viewer's own answer to "which rows of
 * this long-format table are the T slices": it sets the observation level and
 * requires every other dimension to be null. Filtering on `dim_t IS NOT NULL`
 * alone leaves in every deeper level, so a slice is counted once per channel and
 * the distribution is drawn over a table that is three times too long.
 */
function sliceSource(ctx) {
  const parts = ctx.sql.dimSubsetWhere?.({ split: new Set(['t']) })
    ?? [`${ctx.sql.q('dim_t')} IS NOT NULL`];
  return { table: sliceTable(ctx),
           where: parts.length ? `WHERE ${parts.join(' AND ')}` : '' };
}

/** How much of each recording was dead, held, empty or busy, as distributions.
 *
 * This is the thing triage actually decides, and it was the one number the plots
 * did not show. A share rather than a duration, because a recording is whatever
 * length it is and "four minutes dead" means opposite things in five minutes and
 * in an hour.
 *
 * Drawn by the viewer's own engine rather than by hand, which needs the numbers in
 * SQL - they are computed here, from the windows, because the classification uses
 * per-recording percentile thresholds and reimplementing that in SQL would be two
 * definitions of "dead" waiting to disagree. So they are staged as a view. What
 * that buys is the engine choosing the plot: a bar where there is one recording
 * per group, every point where there are a few, a violin where there are many,
 * and a box once there are more points than are worth sending to the browser.
 * Doing that by hand is how you end up with a violin of one observation.
 */
/** The four verdicts as a table the engine can plot, without creating anything.
 *
 * The classification uses per-recording percentile thresholds, so it is computed
 * in JavaScript from the windows rather than in SQL - reimplementing "dead" in
 * two places is two definitions waiting to disagree. The engine needs a table,
 * though, and this was a `CREATE OR REPLACE TEMP VIEW`: a DDL statement issued
 * from a widget, which assumes the widget and the engine share one connection
 * and that the connection accepts DDL at all. Neither is a widget's business.
 *
 * A derived table costs nothing and assumes nothing. `grp` rather than `"group"`
 * so no reserved word is quoted into every query built on top of it, and the
 * share is cast, because `VALUES` with decimal literals infers DECIMAL and every
 * other column the engine plots is a double.
 */
export function verdictSource(summaries, verdict) {
  const rows = [];
  for (const summary of summaries) {
    if (!(summary.total > 0)) continue;
    for (const [kind, field] of Object.entries(VERDICT_FIELD)) {
      const share = 100 * summary[field] / summary.total;
      if (!Number.isFinite(share)) continue;
      rows.push(`(${literal(shortName(summary.recording.name))}, `
        + `${literal(String(summary.recording.group ?? ''))}, `
        + `${literal(WINDOW_KINDS[kind].label)}, ${share.toFixed(4)})`);
    }
  }
  if (!rows.length) return null;
  return {
    table: `(SELECT recording, grp, verdict, CAST(share AS DOUBLE) AS share
             FROM (VALUES ${rows.join(', ')}) AS v(recording, grp, verdict, share)) AS verdicts`,
    where: `WHERE verdict = ${literal(verdict)}`,
  };
}

/** A recording's name, short enough for a hover label. */
function shortName(name) {
  const bare = String(name).split('/').pop();
  return bare.length > 34 ? `${bare.slice(0, 31)}\u2026` : bare;
}

async function renderVerdictShares(host, ctx, summaries) {
  const distribution = ctx.plot.engine?.renderDistribution;
  if (!distribution) return;
  const { flexGrid, groupingLabel } = ctx.plot;
  // Two across, always: each of these carries a note beside it, and four verdicts
  // in one row leaves neither the note nor the plot enough width to be read.
  const { wrap, flexBasisPct } = flexGrid(host, 2);
  for (const kind of ['frozen', 'dwell', 'empty', 'active']) {
    const meta = WINDOW_KINDS[kind];
    const source = verdictSource(summaries, meta.label);
    if (!source) return;
    const cell = appendDiv(wrap,
      `flex:0 0 ${flexBasisPct}%;min-width:300px;margin-bottom:20px;box-sizing:border-box`);
    await distribution(cell, ctx, {
      numCol:          'share',
      source,
      catSql:          'grp',
      catLabel:        groupingLabel(''),
      yLabel:          'per cent of the recording',
      title:           `${meta.label} footage`
                       + `<br><sup>one point per recording; n=${summaries.length}</sup>`,
      showSignificance: !!ctx.state?.showSignificance,
      series:          { isCategory: true },
      categoriesOrder: ctx.groups,
      catLabelFn:      ctx.groupLabel,
      sideInfo:        VERDICT_NOTES[kind],
      allPointsBelow:  500,
    });
    if (!cell.childElementCount) cell.remove();
  }
}

/** What each verdict means, and which way is worse - the direction badge the
 * quality metrics get, for the four numbers triage actually decides on. */
const VERDICT_NOTES = {
  frozen: { text: WINDOW_KINDS.frozen.desc, goodDirection: 'down',
            hintUp: 'more unusable tape', hintDown: 'less' },
  dwell:  { text: WINDOW_KINDS.dwell.desc,
            hintUp: 'more time held on a subject', hintDown: 'less' },
  empty:  { text: WINDOW_KINDS.empty.desc, goodDirection: 'down',
            hintUp: 'more time held on nothing', hintDown: 'less' },
  active: { text: WINDOW_KINDS.active.desc,
            hintUp: 'more transit and cuts', hintDown: 'steadier footage' },
};

// Which field of a recording's summary holds each verdict's seconds.
const VERDICT_FIELD = { frozen: 'dead', dwell: 'held', empty: 'empty', active: 'busy' };

/** The per-slice columns worth a distribution, and what each one triages.
 *
 * Not the image-quality metrics. `laplacian_variance` and `std_intensity` are
 * real measurements and there is a widget for them; here they would be two more
 * cards of numbers with no bearing on whether a recording is worth watching, and
 * the report already answers that question elsewhere.
 *
 * Only the columns the report has, too: `raster-motion` and the detector are
 * optional, and naming a column that is not there is a SQL error rather than an
 * empty plot.
 */
function triageColumns(schema) {
  const wanted = [
    { col: 'frame_difference', label: 'Movement', unit: 'mean absolute difference',
      why: { text: 'How much the picture changes between frames. This is what the '
               + 'verdicts above are derived from: movement piled against zero is '
               + 'frozen or duplicated tape, and a long right tail is transit and cuts.',
             hintUp: 'transit, cuts, a moving camera',
             hintDown: 'a held shot - or frozen tape' } },
    { col: 'detection_count', label: 'Animals per frame', unit: 'animals',
      why: { text: 'How many animals the detector named per frame it read. A floor '
               + 'rather than a census - it reads one frame a second and finds about '
               + '0.83 of the distinct animals present.',
             hintUp: 'more crowded footage', hintDown: 'emptier footage' } },
    { col: 'camera_speed', label: 'Camera speed', unit: 'px/s',
      why: { text: 'How fast the camera itself is moving, by phase correlation. Near '
               + 'zero is a vehicle holding station, which usually means someone is '
               + 'looking at something; high is transit.',
             hintUp: 'transit', hintDown: 'holding station' } },
    { col: 'moving_object_count', label: 'Moving objects', unit: 'objects',
      why: { text: 'Things moving independently of the camera, found with no model '
               + 'and no class list - so this is the one signal that can flag an '
               + 'animal no detector has a category for.',
             hintUp: 'more independent movers to look at',
             hintDown: 'a still scene, or only the camera moving' } },
    { col: 'depth_m', label: 'Depth', unit: 'metres',
      why: { text: 'Where the archive published navigation beside the video. Not a '
               + 'triage signal so much as the context for one: what is worth '
               + 'watching on a shelf and at four thousand metres are different '
               + 'questions.',
             hintUp: 'deeper', hintDown: 'shallower' } },
  ];
  return wanted.filter(({ col }) => schema.allCols.includes(col));
}


// ── what was found, arranged by what it is ────────────────────────────────────

/** The taxonomy of the detector's vocabulary, if the collection shipped one.
 *
 * A detector's classes are a flat list of names, and a flat list is the least
 * useful way to look at sixty of them: `Actiniaria`, `Isididae`, `Antimora
 * microlepis`, `Calyptogena` says nothing about two being cnidarians, one a fish
 * and one a clam, which is the first thing anybody asks of a dive.
 *
 * The names are Linnaean so the hierarchy exists; it is just not in the model.
 * `collect site` writes it beside the parquets as `taxonomy.json`, resolved once
 * from the World Register of Marine Species - see `fetch_taxonomy`. Fetched
 * rather than embedded because it is 100 KB and most reports use a few dozen
 * entries of it; absent, the tree falls back to one ring of bare names, which is
 * still better than nothing and is why this never throws.
 */
let taxonomyPromise = null;

function loadTaxonomy(ctx) {
  if (taxonomyPromise) return taxonomyPromise;
  const beside = ['../taxonomy.json', './taxonomy.json', '../../taxonomy.json'];
  taxonomyPromise = (async () => {
    for (const where of beside) {
      try {
        const response = await fetch(new URL(where, window.location.href));
        if (response.ok) return await response.json();
      } catch { /* try the next place it might be */ }
    }
    return {};
  })();
  return taxonomyPromise;
}

const RANKS = ['kingdom', 'phylum', 'class', 'order', 'family', 'genus'];

/** The ranks above a name, coarsest first, then the name itself.
 *
 * A name the register does not know still gets a place - under `Not an animal`
 * when it is one of the detector's gear and substrate classes, `Unplaced`
 * otherwise - because a tree holding fewer animals than the report does is worse
 * than an untidy one.
 */
export function lineageOf(name, taxonomy) {
  const entry = taxonomy?.[name];
  if (!entry) return [NOT_ANIMAL.has(name) ? 'Not an animal' : 'Unplaced', name];
  const above = RANKS.map(rank => entry[rank]).filter(Boolean);
  return above[above.length - 1] === name ? above : above.concat([name]);
}

// The detector's own non-taxonomic classes: its gear, the substrate, and traces
// left by animals rather than animals. Worth a branch of their own rather than a
// shrug, because "the ROV photographed its own sampler 40 times" is a thing a
// reader should be able to see.
const NOT_ANIMAL = new Set([
  'anchor', 'bone', 'carapace', 'carcass', 'detritus', 'Detritus Sampler',
  'eggcase', 'equipment', 'Equipment', 'geologic', 'ink', 'inner filter', 'kelp',
  'Krill molt', 'marine snow', 'molt', 'mung', 'outer filter', 'salp detritus',
  'sand', 'shell', 'ship container', 'sinker', 'stalk', 'Suction Sampler',
  'Tomopterid eggcase', 'trash', 'tube', 'wood', 'bacterial mat',
  'amphipod tube mat', 'medusa carcass', 'gastrozooid', 'salp chain',
]);

/** Plotly sunburst arrays from counts per taxon name.
 *
 * Every ancestor carries the sum of what is under it, which is what makes a ring
 * readable: the Cnidaria arc is as wide as the cnidarians found, not as wide as
 * the number of cnidarian classes.
 *
 * `branches` is which phylum each node sits in, and it is what makes the tree
 * legible rather than decorative. Plotly colours a sunburst per top-level branch,
 * and every animal has one top-level branch - Animalia - so the whole tree came
 * out one colour. The phylum is the rank a reader actually navigates by, and it is
 * a rank rather than a depth: `lineageOf` drops the ranks the register does not
 * know, so counting rings inwards from the outside finds a different rank in every
 * lineage.
 */
export function sunburstOf(counts, taxonomy) {
  const total = new Map();          // full path -> animals under it
  const parent = new Map();         // full path -> parent path
  const label = new Map();          // full path -> the name to draw
  const branch = new Map();         // full path -> the phylum it sits in
  for (const [name, found] of Object.entries(counts)) {
    const lineage = lineageOf(name, taxonomy);
    const trunk = trunkOf(name, lineage, taxonomy);
    let path = '';
    for (const [depth, step] of lineage.entries()) {
      const here = path ? `${path}>${step}` : step;
      total.set(here, (total.get(here) ?? 0) + found);
      if (!parent.has(here)) {
        parent.set(here, depth ? path : '');
        label.set(here, step);
      }
      if (depth >= trunk.depth && !branch.has(here)) branch.set(here, trunk.name);
      path = here;
    }
  }
  const ids = [...total.keys()];
  return {
    ids,
    labels: ids.map(id => label.get(id)),
    parents: ids.map(id => parent.get(id)),
    values: ids.map(id => total.get(id)),
    branches: ids.map(id => branch.get(id) ?? null),
  };
}

/** The phylum a name belongs to, and where in its lineage that phylum sits.
 *
 * Everything at or below that depth takes the phylum's colour; the kingdom above
 * it takes none, because a ring that is the whole tree carries no information by
 * being coloured. Names the register does not know already have a branch of their
 * own from `lineageOf` - `Unplaced`, `Not an animal` - and that is their trunk.
 */
export function trunkOf(name, lineage, taxonomy) {
  const phylum = taxonomy?.[name]?.phylum;
  const depth = phylum ? lineage.indexOf(phylum) : -1;
  return depth >= 0 ? { name: phylum, depth } : { name: lineage[0], depth: 0 };
}

/** The colour of one taxon, anywhere in the report.
 *
 * Fixed by the name rather than by rank in the current plot, which is the only
 * way the sunburst's Cnidaria arc and the band called Cnidaria under it can be
 * relied on to be the same colour. Ordering by abundance gives the biggest
 * branches the palette's clearest hues, and it also makes the colour depend on
 * the plot: a taxon moves hue between the tree and the bands, between the two
 * axes, and between counting modes, and then the colours are decoration.
 *
 * The cost is that two taxa can land on the same hue. Both are labelled, and a
 * legend that agrees with itself across a card is worth more than one that never
 * repeats a colour.
 */
export function taxonColour(ctx, name) {
  // The host builds a scale from whatever palette it is asked for but does not say
  // which one the reader has chosen, so this asks for tab20 by name: twenty hues,
  // from the viewer's own default family, and the same twenty on every reload. The
  // cost is that switching the report's palette leaves a taxonomy where it was,
  // which is a cheaper thing to give up than a change to the viewer.
  const palette = ctx.color?.getColors?.(ctx.color.palette ?? 'tab20', TAXON_HUES)
    ?? PHYLUM_FALLBACK;
  return palette[nameHash(String(name)) % palette.length];
}

// Enough hues to tell a dive's phyla apart; more than a reader can hold anyway.
const TAXON_HUES = 20;

/** A small stable hash, so a name gets the same hue in every report and reload. */
function nameHash(name) {
  let hash = 0;
  for (let i = 0; i < name.length; i += 1) {
    hash = (hash * 31 + name.charCodeAt(i)) % 0x7fffffff;
  }
  return hash;
}

/** A colour per sunburst node, by the phylum it sits in. */
export function branchColours(ctx, tree) {
  // Above the phylum there is nothing to distinguish, so those rings stay the
  // neutral grey that lets the coloured ones read as the answer.
  return tree.branches.map(name => (name ? taxonColour(ctx, name) : '#5a6472'));
}

/** Distinct animals per taxon, counted the way the gallery counts them.
 *
 * One animal, not one detection: a sea pen detected in forty slices is one sea
 * pen. Events already group a taxon's consecutive slices into one sighting, so
 * counting events counts creatures rather than frames.
 */
async function countByTaxon(ctx, recordings, floor) {
  const found = await collectEvents(ctx, recordings);
  const counts = {};
  for (const event of found) {
    if (!event.topClass) continue;
    if (floor && Number.isFinite(event.confidence) && event.confidence < floor) continue;
    counts[event.topClass] = (counts[event.topClass] ?? 0) + 1;
  }
  return counts;
}

/** Which colour is which phylum, for the branches big enough to matter.
 *
 * The ring labels say what everything is already, so this is not a legend so much
 * as a way to find a phylum in the tree without reading every arc. Only the
 * branches worth finding: a key of forty entries is a wall of text.
 */
function renderPhylumKey(host, ctx, tree, unit = 'animals') {
  const colours = branchColours(ctx, tree);
  const seen = new Map();
  tree.branches.forEach((name, i) => {
    if (name && !seen.has(name)) seen.set(name, { colour: colours[i], animals: tree.values[i] });
  });
  const shown = [...seen.entries()]
    .sort((a, b) => b[1].animals - a[1].animals)
    .slice(0, MOST_PHYLA);
  if (shown.length < 2) return;
  const key = appendDiv(host, 'display:flex;flex-wrap:wrap;gap:4px 14px;margin-top:8px');
  key.className = 'small text-muted';
  for (const [name, { colour, animals }] of shown) {
    const entry = appendDiv(key, 'display:flex;align-items:center;gap:5px');
    appendDiv(entry, `width:10px;height:10px;border-radius:2px;background:${colour}`);
    const text = document.createElement('span');
    text.textContent = `${name} (${animals.toLocaleString()} ${unit})`;
    entry.appendChild(text);
  }
}

// Enough to find your way around, few enough to read in one glance.
const MOST_PHYLA = 12;

// What an arc's width means.
const TAXON_COUNTS = [
  { key: 'kinds',   label: 'by kind of animal', byKind: true, unit: 'kinds' },
  { key: 'animals', label: 'by animals found',                unit: 'animals' },
];

/** The same taxa, counted one apiece.
 *
 * Turns a tree of abundance into a tree of richness: each named class contributes
 * one, so a branch is as wide as the number of *kinds* under it. Worth having as
 * its own reading because the two disagree in the way that matters - a seafloor
 * of ten thousand sea pens and two dozen other species is one arc and a fringe by
 * abundance, and an evenly divided tree by kind.
 */
export function oneEach(counts) {
  return Object.fromEntries(Object.keys(counts).map(name => [name, 1]));
}

// Only reached where the host offers no palette. A tree in these colours is worse
// than one in the report's, and far better than one in a single blue.
const PHYLUM_FALLBACK = ['#4e79a7', '#f28e2b', '#59a14f', '#e15759', '#b07aa1',
                         '#76b7b2', '#edc948', '#9c755f', '#ff9da7', '#bab0ac'];

const taxonomyWidget = {
  id: 'temporal-taxonomy',
  required_inputs: ['detection_top_class', 'dim_<axis>'],
  inputs: ['detection_count', 'detection_confidence', 'detections',
    'frame_difference', 'fps', 'name', 'path', 'child_id'],
  group: 'Summary',
  scope: 'slice',
  label: 'What Was Found',
  shortLabel: 'Taxonomy',
  info: [
    'Every animal the report found, arranged by what it is. Click a ring to descend into it.',
    '',
    'Two readings of the same tree:',
    '',
    '- **By animals found** - abundance. An arc is as wide as the creatures counted under it.',
    '- **By kind of animal** - richness. Every named class weighs the same, so an arc is as wide as the number of *kinds* under it.',
    '',
    'They disagree where it matters: a seafloor of ten thousand sea pens and two dozen other',
    'species is one arc and a fringe by abundance, and an evenly divided tree by kind.',
    '',
    'Colours are the **phylum** each branch sits in, with a key below the tree. Rings above the',
    'phylum are grey.',
    '',
    'The **confidence floor** drops detections the detector was less sure of than the chosen value.',
    '',
    '- One count is one **animal**, not one detection: a sea pen seen in forty slices is one sea pen.',
    '- Lineages come from the **World Register of Marine Species**, resolved once for the detector\'s whole vocabulary.',
    '- **Not an animal** is a real branch - the detector has classes for the vehicle\'s own sampler, for marine snow, and for traces like eggcases and moults.',
    '- **Unplaced** is a name the register did not recognise.',
    '',
    'The names are the detector\'s, and it is confidently wrong about some of them. This arranges',
    'what it said; it does not check it.',
  ].join('\n'),

  requires(schema) {
    return schema.allCols.includes('detection_top_class')
      && (schema.dimCols ?? []).includes('dim_t');
  },

  async overviewPlot(container, ctx) {
    const recordings = await fetchRecordings(ctx);
    if (!recordings.length) return false;
    const [counts, taxonomy] = await Promise.all([
      countByTaxon(ctx, recordings, 0), loadTaxonomy(ctx)]);
    if (!Object.keys(counts).length) return false;
    const tree = sunburstOf(counts, taxonomy);
    ctx.plot.appendMini(container,
      [{ type: 'sunburst', ...tree, branchvalues: 'total',
         marker: { colors: branchColours(ctx, tree) },
         textinfo: 'none', hoverinfo: 'skip', sort: true }],
      { margin: { l: 0, r: 0, t: 0, b: 0 } });
    return true;
  },

  async render(container, ctx) {
    const recordings = await fetchRecordings(ctx);
    if (!recordings.length) {
      container.innerHTML = '<div class="no-data">No time slices found.</div>';
      return;
    }
    const taxonomy = await loadTaxonomy(ctx);
    await renderTaxonomyTree(appendDiv(container), ctx, recordings, taxonomy);
  },
};

/** The sunburst, with its confidence floor and its two ways of counting. */
async function renderTaxonomyTree(container, ctx, recordings, taxonomy) {
    const note = appendDiv(container, 'margin-bottom:6px');
    const host = appendDiv(container);

    let floor = 0;
    let counting = TAXON_COUNTS[0];
    const controls = appendDiv(container,
      'display:flex;gap:8px;margin-top:6px;flex-wrap:wrap');
    controls.className = 'small';
    const floors = document.createElement('select');
    floors.className = 'form-select form-select-sm';
    floors.style.maxWidth = '260px';
    floors.replaceChildren(...CONFIDENCE_FLOORS.map(f => option(f.label, String(f.value))));
    floors.value = String(floor);
    floors.addEventListener('change', () => { floor = Number(floors.value); draw(); });
    controls.appendChild(floors);

    // Two questions with the same tree and different arcs. Richness treats a
    // thousand sea pens and one anglerfish as one branch each, which is how a
    // sparse phylum stays visible; abundance is what was actually in front of the
    // camera, and on benthic footage that is one arc and forty slivers.
    const counts = document.createElement('select');
    counts.className = 'form-select form-select-sm';
    counts.style.maxWidth = '240px';
    counts.replaceChildren(...TAXON_COUNTS.map(c => option(c.label, c.key)));
    counts.value = counting.key;
    counts.addEventListener('change', () => {
      counting = TAXON_COUNTS.find(c => c.key === counts.value) ?? TAXON_COUNTS[0];
      draw();
    });
    controls.appendChild(counts);

    async function draw() {
      host.innerHTML = '<span class="text-muted small">Reading timelines…</span>';
      const found = await countByTaxon(ctx, recordings, floor);
      const animals = Object.values(found).reduce((a, b) => a + b, 0);
      if (!animals) {
        host.innerHTML = '<div class="no-data">Nothing named above this confidence.</div>';
        note.textContent = '';
        return;
      }
      const placed = Object.keys(found).filter(n => taxonomy[n]).length;
      const named = Object.keys(found).length;
      note.innerHTML = `<strong>${animals.toLocaleString()}</strong> animals in `
        + `<strong>${named}</strong> classes`
        + (Object.keys(taxonomy).length
          ? `, ${placed} of them placed in the taxonomy`
          : ' &mdash; no taxonomy beside this report, so the tree is one ring of names');
      host.innerHTML = '';
      const tree = sunburstOf(counting.byKind ? oneEach(found) : found, taxonomy);
      ctx.plot.append(host,
        [{ type: 'sunburst', ...tree, branchvalues: 'total', sort: true,
           maxdepth: 4,
           marker: { colors: branchColours(ctx, tree) },
           hovertemplate: `%{label}<br>%{value} ${counting.unit}<extra></extra>`,
           insidetextorientation: 'radial' }],
        { height: 620, margin: { l: 0, r: 0, t: 10, b: 0 } });
      renderPhylumKey(host, ctx, tree, counting.unit);
    }
    await draw();
}


// ── the same animals, laid along an axis ─────────────────────────────────────

/** How many kinds of animal, and how many animals, per stretch of time.
 *
 * A line per recording was unreadable the moment there was more than a handful:
 * five expeditions of nine recordings is forty-five overlapping traces of a
 * spiky per-slice count, and nothing in it can be read. What a reader of a
 * collection actually wants is the shape of the fauna over time - how rich the
 * footage was, and when - which is one bar per bucket rather than one line per
 * file.
 *
 * Two numbers, because they answer different questions and disagree usefully:
 * **kinds** is how many distinct classes were named in the bucket, and **animals
 * per frame** is how crowded it was. A bucket can be crowded and monotonous - a
 * seabed of one sea pen - or sparse and varied.
 */
async function fetchBiodiversity(ctx, axis) {
  const { q } = ctx.sql;
  // The grouping comes along so the bands can be split by it, because with a
  // grouping set that is how the reader has asked to see the whole report.
  return ctx.queryRows(`
    SELECT ${bucketExpr(ctx, axis)} AS bucket,
           ${q('detection_top_class')} AS taxon,
           ${groupExpr(ctx)} AS grp,
           COALESCE(SUM(${q('detection_count')}), 0) AS animals
    FROM ${sliceTable(ctx)}
    ${whereClause(ctx, `${q('dim_t')} IS NOT NULL`)}
    GROUP BY 1, 2, 3 HAVING bucket IS NOT NULL
    ORDER BY ${bucketOrder(ctx, axis)}`);
}

/** The SQL expression that puts a slice in a bucket, in one place.
 *
 * Shared because the counts and the seconds they are divided by have to land in
 * the same buckets, and two copies of a truncation is two chances to bucket them
 * differently and get a rate that is a ratio of different things.
 *
 * Formatted to a string in SQL and plotted as a category, because `DATE_TRUNC`
 * hands back a timestamp and a timestamp handed to Plotly as a bare value is
 * epoch milliseconds - which is how a plot of five years came out labelled
 * `1.7T`. A category axis then keeps whatever order the rows arrive in, which is
 * what `bucketOrder` is for: `'900'` sorts before `'90'` as text, and a depth
 * axis in that order is nonsense.
 */
function bucketExpr(ctx, axis, cols = {}) {
  const at = cols.at ?? ctx.sql.q('recorded_at');
  const deep = cols.deep ?? ctx.sql.q('depth_m');
  if (axis.deep) {
    return `CAST(CAST(FLOOR(${deep} / ${axis.step}) * ${axis.step} AS BIGINT) AS VARCHAR)`;
  }
  return `STRFTIME(DATE_TRUNC('${axis.unit}', CAST(${at} AS TIMESTAMP)), '${axis.format}')`;
}

/** What to sort the buckets by, which is not always the label they carry.
 *
 * Depth is a number rendered as text, so it has to be ordered by the number.
 * A formatted timestamp already sorts correctly as text, every format here being
 * most-significant-first.
 */
function bucketOrder(ctx, axis) {
  return axis.deep ? `MIN(${ctx.sql.q('depth_m')})` : '1';
}

// How the x axis can be read.
//
// The clock is what "over time" means, and the axis this widget is for. It used
// to fall back to how far through each recording a sighting was, for a report
// with no timestamps, and that is not the same question: a percentage through a
// file is an artefact of how the footage was cut, and nothing about the animals.
//
// Depth is the other axis the fauna actually varies along, and on a dive that
// descends four kilometres it varies along it hard - which is a comparison a
// reader can make and the relative axis never offered. Either axis needs its own
// column, and a report with neither has nothing to place anything on and says so.
const AXES = [
  { key: 'clock', label: 'by the clock', needs: 'recorded_at',
    title: 'by the clock' },
  { key: 'deep',  label: 'by depth',     needs: 'depth_m', deep: true,
    title: 'depth (m)' },
];

const axesFor = (schema) => AXES.filter(a => schema.allCols.includes(a.needs));

// What the height of a band means. Absolute counts answer "when did we see the
// most", which is partly a question about how much footage was shot that year;
// the rate answers "when was the water busiest", which is usually the one meant.
const COUNTS = [
  { key: 'animals', label: 'animals found',     axis: 'animals found' },
  { key: 'rate',    label: 'animals per second of footage', rate: true,
    axis: 'animals per second of footage' },
];

// Buckets coarse enough to read and fine enough to have a shape in them, chosen
// from the span the report covers rather than fixed - this has to work for
// fourteen minutes of one dive and for a decade of one camera. The format is what
// the axis is labelled with, because the bucket is a category rather than a date.
const UNITS = [
  { unit: 'hour',  label: 'by hour',  format: '%Y-%m-%d %H:00', spanDays: 2 },
  { unit: 'day',   label: 'by day',   format: '%Y-%m-%d',       spanDays: 120 },
  { unit: 'month', label: 'by month', format: '%Y-%m',          spanDays: 3650 },
  { unit: 'year',  label: 'by year',  format: '%Y',             spanDays: Infinity },
];

function unitForSpan(rows) {
  const times = rows.map(r => new Date(r.bucket).getTime()).filter(Number.isFinite);
  if (times.length < 2) return UNITS[1];
  const days = (Math.max(...times) - Math.min(...times)) / 86400000;
  return UNITS.find(u => days <= u.spanDays) ?? UNITS[UNITS.length - 1];
}

// Ranks to offer, coarsest first. Phylum is the default because it is the rank
// that separates a seabed of cnidarians and echinoderms from a midwater tape of
// ctenophores and salps, which is the distinction this plot exists to show.
const COMPOSITION_RANKS = ['phylum', 'class', 'order'];

/** Animals per time bucket, split by a rank of the taxonomy.
 *
 * A count of kinds over time was the previous answer and it was not one: a number
 * with no units that goes up when the footage is longer. What a reader of a
 * collection is actually asking is *what was down there and when* - so the height
 * is animals and the colours are the taxonomy, and a bucket's composition is the
 * shape of the fauna rather than a single figure standing in for it.
 */
/** Whether dividing by the footage actually changed anything, said out loud.
 *
 * A reader switched to the rate on a single expedition, saw the same shape, and
 * did not believe it - rightly, because it *was* the same shape. Every recording
 * in that report is sampled to about the same length, so the footage behind each
 * bucket varies by a tenth and the rate is the count rescaled. On the combined
 * report it varies twentyfold and the two disagree about which depth was busiest:
 * 1,500 m by count, 4,300 m by rate.
 *
 * So the plot says which of those it is. A control that looks like it did nothing
 * is worse than no control - the reader is left doubting the number rather than
 * the spread of the footage, which is the thing actually worth knowing.
 */
export function sayWhatTheRateDid(host, seconds, unchanged) {
  const note = appendDiv(host);
  note.className = 'text-muted small';
  if (!seconds.size) {
    note.textContent = 'No frame rate in this report, so a rate cannot be worked '
      + 'out; showing counts.';
    return;
  }
  if (unchanged) {
    note.textContent = 'Nothing to divide by here; showing counts.';
    return;
  }
  const held = [...seconds.values()].filter(value => value > 0);
  const spread = Math.max(...held) / Math.min(...held);
  note.textContent = spread < RATE_WORTH_IT
    ? `The footage behind each bucket varies by only ${spread.toFixed(1)}x, so this `
      + 'is the count rescaled rather than a different shape.'
    : `The footage behind each bucket varies by ${spread.toFixed(1)}x, so this is a `
      + 'different shape from the count - buckets with little footage in them rise.';
}

// Below this, dividing by the footage rescales the plot rather than reshaping it.
const RATE_WORTH_IT = 1.5;

/** The same stacked bands, divided by the footage behind each bucket.
 *
 * A copy rather than a mutation: the counts are what the headline above the plot
 * reports, and dividing them in place makes "2,847 animals" become "0.4 animals".
 * Buckets with no measurable footage are dropped to null rather than to zero,
 * because a gap in the plot is true and a zero is a claim that nothing was there.
 */
export function asRate(traces, seconds) {
  if (!seconds.size) return traces;
  return traces.map(trace => ({
    ...trace,
    y: trace.y.map((value, i) => {
      const held = seconds.get(trace.x[i]);
      return held > 0 ? value / held : null;
    }),
  }));
}

export function compositionTraces(rows, taxonomy, rank, ctx) {
  const split = bandSplit(ctx, taxonomy, rank);
  const perBucket = new Map();     // bucket -> band -> animals
  const totals = new Map();        // band -> animals, for ordering the stack
  for (const row of rows) {
    const animals = Number(row.animals);
    if (!(animals > 0)) continue;
    const band = split.of(row);
    if (!perBucket.has(row.bucket)) perBucket.set(row.bucket, new Map());
    const here = perBucket.get(row.bucket);
    here.set(band, (here.get(band) ?? 0) + animals);
    totals.set(band, (totals.get(band) ?? 0) + animals);
  }
  const buckets = [...perBucket.keys()];
  // Biggest first, so the stack reads from the dominant band up and the legend
  // is in the order a reader would name them.
  const bands = [...totals.entries()].sort((a, b) => b[1] - a[1]).map(([g]) => g);
  const shown = bands.slice(0, MOST_GROUPS);
  const rest = bands.slice(MOST_GROUPS);
  const traces = shown.map(band => ({
    type: 'bar', name: split.label(band),
    x: buckets, y: buckets.map(b => perBucket.get(b).get(band) ?? 0),
    marker: { color: split.colour(band) },
    hovertemplate: `%{y:,.0f} ${escapeHtmlText(split.label(band))}<extra>%{x}</extra>`,
  }));
  if (rest.length) {
    traces.push({
      type: 'bar', name: `${rest.length} more`,
      x: buckets,
      y: buckets.map(b => rest.reduce((sum, g) => sum + (perBucket.get(b).get(g) ?? 0), 0)),
      marker: { color: '#adb5bd' },
      hovertemplate: `%{y:,.0f} in ${rest.length} smaller groups<extra>%{x}</extra>`,
    });
  }
  return { traces, groups: bands.length, split };
}

/** What a band of the stack is, and what colour it takes.
 *
 * Two answers, and the reader has already chosen between them elsewhere. With a
 * grouping set the whole report is split that way and these bars should be too,
 * in the report's own group colours - otherwise this is the one card where a
 * colour means something different. With no grouping the interesting split is
 * the taxonomy, in the colours the tree above already uses for it.
 */
function bandSplit(ctx, taxonomy, rank) {
  if (ctx?.state?.groupCol) {
    return {
      byGroup: true,
      of: (row) => String(row.grp ?? ''),
      label: (band) => ctx.groupLabel?.(band) ?? band,
      colour: (band) => ctx.color?.group?.(band),
    };
  }
  return {
    byGroup: false,
    of: (row) => rankOf(row.taxon, taxonomy, rank),
    label: (band) => band,
    colour: (band) => taxonColour(ctx, band),
  };
}

// Beyond this many bands a stack is a colour puzzle rather than a plot; the rest
// are summed into one grey band rather than dropped.
const MOST_GROUPS = 9;

/** The rank a class sits at, or an honest bucket for the ones with no rank. */
function rankOf(taxon, taxonomy, rank) {
  if (!taxon) return 'Unnamed';
  const entry = taxonomy?.[taxon];
  if (entry?.[rank]) return entry[rank];
  if (entry) return `${rank} unrecorded`;
  return NOT_ANIMAL.has(taxon) ? 'Not an animal' : 'Unplaced';
}

/** The taxonomy laid along an axis: when each kind was seen, or how deep.
 *
 * Was a widget of its own called "over time", which is where the depth axis went
 * wrong - a plot of zonation under a heading about chronology. It is the same
 * animals as the tree above it, arranged by where they were rather than by what
 * they are, so it belongs beside the tree.
 */
/** The taxonomy laid along one axis: which kinds were seen when, or how deep.
 *
 * Was half of the taxonomy card, drawing both axes at once, and before that a
 * widget called "over time" that also held the depth plot. Both were wrong in the
 * same way - a plot of zonation and a plot of chronology answer different
 * questions and belong under different headings. So each axis has its own widget
 * now, and this draws one of them.
 */
async function renderComposition(container, ctx, taxonomy, key) {
  const axis = await axisFor(ctx, key);
  if (!axis) {
    const why = appendDiv(container);
    why.className = 'text-muted small';
    why.textContent = `This report has no ${key === 'deep' ? 'depths' : 'timestamps'} in `
      + 'it, so there is nowhere to lay these animals out. Process with the '
      + 'slice-location processor, where the archive publishes navigation beside '
      + 'the video.';
    return;
  }

  let rank = 'phylum';
  let counting = COUNTS[0];
  const controls = appendDiv(container, 'display:flex;gap:8px;margin-bottom:8px;flex-wrap:wrap');
  controls.className = 'small';

  const counts = document.createElement('select');
  counts.className = 'form-select form-select-sm';
  counts.style.maxWidth = '260px';
  counts.replaceChildren(...COUNTS.map(c => option(c.label, c.key)));
  counts.value = counting.key;
  counts.addEventListener('change', () => {
    counting = COUNTS.find(c => c.key === counts.value) ?? COUNTS[0];
    draw();
  });
  controls.appendChild(counts);

  const ranks = document.createElement('select');
  ranks.className = 'form-select form-select-sm';
  ranks.style.maxWidth = '200px';
  ranks.replaceChildren(...COMPOSITION_RANKS.map(r => option(`grouped by ${r}`, r)));
  ranks.value = rank;
  ranks.addEventListener('change', () => { rank = ranks.value; draw(); });
  controls.appendChild(ranks);
  if (!Object.keys(taxonomy).length) {
    ranks.disabled = true;
    sayInControls(controls, 'No taxonomy beside this report, so the bands are bare class names.');
  }
  if (ctx.state?.groupCol) {
    ranks.disabled = true;
    sayInControls(controls, `Split by ${ctx.plot.groupingLabel('grouping')} rather than by `
      + 'taxonomy, to match the rest of the report.');
  }

  const panel = { axis, head: Object.assign(appendDiv(container, 'margin:4px 0'),
                                            { className: 'small' }),
                  host: appendDiv(container) };
  async function draw() { await drawAxis(panel, ctx, taxonomy, rank, counting); }
  await draw();
}

function sayInControls(controls, text) {
  const note = appendDiv(controls);
  note.className = 'text-muted small';
  note.textContent = text;
}

/** Depth against time, one line per dive rather than one per file.
 *
 * Nothing is cropped anywhere in this pipeline, and the flat lines this replaces
 * were the honest consequence of that. NOAA publishes ROV footage as five-minute
 * segments - 3,002 frames at 10 fps - and every frame of every one is read. Five
 * minutes is simply not long enough for a vehicle working the bottom to change
 * depth: the median segment moves 2.9 m and the largest 16.1 m, against an axis
 * three kilometres tall.
 *
 * The depth change is there; it is *between* the segments. EX2503's 12 April dive
 * runs 4,859 m to 4,731 m across four hours of it, and the 27 April dive climbs
 * from 4,260 m to 2,262 m. Drawn per file that is fourteen flat lines all starting
 * at zero; drawn per dive, against the dive's own clock, it is the profile.
 *
 * Dives are found by the gaps between segments, because no column names them -
 * the archive's dive numbers live in the ancillary data, not in the proxy
 * filename. Segments less than `DIVE_GAP_HOURS` apart are one dive, which
 * separates EX2503's four dives cleanly: the widest gap inside one is 45 minutes
 * and the narrowest between two is 19 hours.
 */
async function fetchDepthProfiles(ctx) {
  const { q } = ctx.sql;
  // Naming a column the report does not have is a SQL error, not an empty
  // result - and in the browser's wasm build that arrives as `_setThrew is not
  // defined` with the message gone.
  if (!ctx.schema.allCols.includes('depth_m')) {
    return { profiles: new Map(), unit: null, perDive: false };
  }
  const onClock = ctx.schema.allCols.includes('recorded_at');
  return onClock ? diveProfiles(ctx) : fileProfiles(ctx);
}

/** One line per dive, on the dive's own clock, with a break at each segment edge.
 *
 * The timestamp is aliased `stamp`, not `at`: `at` opens `AT TIME ZONE` in
 * DuckDB's grammar, so `ORDER BY at` is a parse error rather than a missing
 * column. Second time in this file.
 */
/** Seconds of footage to average into one point, so a profile stays drawable.
 *
 * A collection is one row per second of footage: nine expeditions came to 86,000
 * of them, and a dive profile drawn from 86,000 SVG points is a page that hangs
 * before it paints. Depth is the one measurement here that cannot move quickly -
 * a vehicle descends at under a metre a second - so averaging twenty seconds of it
 * into one point removes nothing a reader could see, and the shape of a descent,
 * a terraced survey or a flat transect survives intact.
 *
 * Below the target the query is left alone, which keeps a single recording exact
 * and the aggregation out of the common case.
 */
const PROFILE_POINTS = 4000;

async function profileBucket(ctx, where) {
  const { q } = ctx.sql;
  const [counted] = await ctx.queryRows(`
    SELECT COUNT(*) AS n FROM ${sliceTable(ctx)} ${whereClause(ctx, where)}`);
  const rows = Number(counted?.n ?? 0);
  return rows > PROFILE_POINTS ? Math.ceil(rows / PROFILE_POINTS) : 0;
}

async function diveProfiles(ctx) {
  const { q } = ctx.sql;
  const where = `${q('dim_t')} IS NOT NULL AND ${q('depth_m')} IS NOT NULL `
    + `AND ${q('recorded_at')} IS NOT NULL`;
  const bucket = await profileBucket(ctx, where);
  const stamp = `EPOCH(CAST(${q('recorded_at')} AS TIMESTAMP))`;
  const rows = await ctx.queryRows(bucket ? `
    SELECT rec, grp, MIN(stamp) AS stamp, AVG(depth) AS depth
    FROM (SELECT ${recordingKey(ctx)} AS rec, ${groupExpr(ctx)} AS grp,
                 ${stamp} AS stamp, ${q('depth_m')} AS depth
          FROM ${sliceTable(ctx)} ${whereClause(ctx, where)})
    GROUP BY rec, grp, FLOOR(stamp / ${bucket})
    ORDER BY stamp` : `
    SELECT ${recordingKey(ctx)} AS rec, ${groupExpr(ctx)} AS grp,
           ${stamp} AS stamp, ${q('depth_m')} AS depth
    FROM ${sliceTable(ctx)}
    ${whereClause(ctx, where)}
    ORDER BY stamp`);
  const clean = rows
    .map(row => ({ rec: row.rec, grp: row.grp, at: Number(row.stamp), depth: Number(row.depth) }))
    .filter(row => Number.isFinite(row.at) && Number.isFinite(row.depth));
  if (!clean.length) return fileProfiles(ctx);
  return { profiles: intoDives(clean), unit: 'hours into the dive', perDive: true };
}

/** Timestamped depths, clustered into dives and placed on each dive's own clock.
 *
 * Separated from the query so the clustering can be argued with directly - it is
 * a threshold, and a threshold that silently merged two dives would produce a
 * profile of something that never happened.
 */
export function intoDives(rows, gapHours = DIVE_GAP_HOURS) {
  const profiles = new Map();
  let dive = null;
  let previous = null;
  for (const row of rows) {
    if (!dive || (previous && row.at - previous.at > gapHours * 3600)) {
      dive = { grp: row.grp, x: [], y: [], start: row.at, from: row.rec, segments: 0 };
      profiles.set(diveName(row, profiles.size), dive);
      previous = null;
    }
    // A break between segments rather than a line drawn through footage nobody
    // collected: the gap is real and interpolating across it would invent a depth.
    if (previous && row.rec !== previous.rec) {
      dive.x.push(null);
      dive.y.push(null);
    }
    if (!previous || row.rec !== previous.rec) dive.segments += 1;
    dive.x.push((row.at - dive.start) / 3600);
    dive.y.push(row.depth);
    previous = row;
  }
  return profiles;
}

/** The dive a segment starts, named by when it started. */
function diveName(row, sofar) {
  const when = new Date(row.at * 1000);
  return Number.isFinite(when.getTime())
    ? `dive of ${when.toISOString().slice(0, 16).replace('T', ' ')} UTC`
    : `dive ${sofar + 1}`;
}

// Segments closer together than this are the same dive. Wide enough for the
// hours a collection leaves between the samples it took from one dive, narrow
// enough to keep two dives on consecutive days apart.
const DIVE_GAP_HOURS = 6;

/** One line per file, against its own run time - for a report with no clock. */
async function fileProfiles(ctx) {
  const { q } = ctx.sql;
  const timed = ctx.schema.allCols.includes('fps');
  const when = timed
    ? `CAST(${q('dim_t')} AS DOUBLE) / NULLIF(${q('fps')}, 0) / 60`
    : `CAST(${q('dim_t')} AS DOUBLE)`;
  const rows = await ctx.queryRows(`
    SELECT ${recordingKey(ctx)} AS rec, ${groupExpr(ctx)} AS grp,
           ${when} AS when_, ${q('depth_m')} AS depth
    FROM ${sliceTable(ctx)}
    ${whereClause(ctx, `${q('dim_t')} IS NOT NULL AND ${q('depth_m')} IS NOT NULL`)}
    ORDER BY 1, ${q('dim_t')}`);
  const profiles = new Map();
  for (const row of rows) {
    const depth = Number(row.depth);
    const at = Number(row.when_);
    if (!Number.isFinite(depth) || !Number.isFinite(at)) continue;
    if (!profiles.has(row.rec)) {
      profiles.set(row.rec, { grp: row.grp, x: [], y: [], segments: 1 });
    }
    const trace = profiles.get(row.rec);
    trace.x.push(at);
    trace.y.push(depth);
  }
  return { profiles,
           unit: timed ? 'minutes into the recording' : 'frames into the recording',
           perDive: false };
}

/** The profiles as Plotly lines, one per recording.
 *
 * "Each line is one recording" has to be true on the screen, not just in a
 * caption. With a grouping set the colour carries that, because it is the split
 * the reader has already said they care about and five expeditions is a legend.
 * Without one, every line was the same blue and the plot said nothing a single
 * line would not have - so each recording takes its own hue, keyed off its name
 * so it does not shuffle between redraws.
 *
 * The legend only appears while it is still a legend. Past a dozen recordings the
 * names are the taller half of the card, and the hover is the better answer.
 */
export function profileTraces(ctx, byRecording, { mini = false, relative = false } = {}) {
  const grouped = !!ctx.state?.groupCol;
  const named = byRecording.size <= MOST_PROFILES_NAMED;
  const legendSeen = new Set();
  // Every point that will be drawn, not this trace's share of them: the cost is
  // the figure's, and forty profiles of two hundred points each is the same eight
  // thousand marks as one profile of eight thousand.
  const points = [...byRecording.values()].reduce((sum, t) => sum + t.x.length, 0);
  return [...byRecording.entries()].map(([name, trace]) => {
    const band = String(trace.grp ?? '');
    const first = grouped ? !legendSeen.has(band) : named;
    if (grouped && first) legendSeen.add(band);
    const start = trace.y.find(value => Number.isFinite(value)) ?? 0;
    return {
      // WebGL past the point where SVG markers stop being free. A dive profile of a
      // whole collection is thousands of points however hard the query thins it.
      type: points > 2000 ? 'scattergl' : 'scatter',
      // Markers as well as lines, because a dive is drawn as the few minutes of it
      // that were collected and a five-minute piece of a six-hour axis is three
      // pixels of line - but only while there are few enough of them to see. Past
      // that they overlap into a thick line saying nothing the line did not, and
      // cost a DOM node each.
      mode: points > 400 ? 'lines' : 'lines+markers',
      marker: { size: 3 },
      name: grouped ? (ctx.groupLabel?.(band) ?? band) : shortName(name),
      x: trace.x,
      y: relative
        ? trace.y.map(depth => (Number.isFinite(depth) ? depth - start : null))
        : trace.y,
      line: { width: 1.6,
              color: grouped ? ctx.color?.group?.(band) : taxonColour(ctx, name) },
      opacity: 0.9,
      showlegend: !mini && first,
      legendgroup: grouped ? band : undefined,
      hovertemplate: `${escapeHtmlText(shortName(name))}`
        + (relative ? '<br>%{y:+,.1f} m at %{x:.1f}<extra></extra>'
                    : '<br>%{y:,.0f} m at %{x:.1f}<extra></extra>'),
    };
  });
}

// Beyond this the legend is taller than the plot, and the hover does the naming.
const MOST_PROFILES_NAMED = 12;

// What the y axis measures. Absolute depth is where the footage was shot, and on
// a collection spanning three kilometres that is the whole axis - so the metres a
// vehicle gains or loses inside one five-minute window round to a flat line.
// Relative depth throws the ladder away and keeps only that movement, which is
// the only thing in this plot the first reading cannot show.
const PROFILE_MODES = [
  { key: 'actual', label: 'actual depth', axis: 'depth (m)' },
  { key: 'change', label: 'change during the recording', relative: true,
    axis: 'metres below the start of the recording' },
];

/** How much depth moves inside a recording, against how much the axis spans.
 *
 * A reader looking at thirty-seven flat lines is owed an answer to "is that
 * real". It is: on the collected expeditions the vehicle is holding station on
 * the bottom - that is what the footage was selected for - and the most any
 * recording moves is sixteen metres over five minutes against an axis three and a
 * third kilometres tall. Saying so is cheaper than making them wonder.
 */
export function howFlat(byRecording) {
  // Nulls are the breaks between one dive's segments; Math.max reads them as zero
  // and would report every profile as reaching the surface.
  const depthsOf = (trace) => trace.y.filter(value => Number.isFinite(value));
  const spans = [...byRecording.values()]
    .map(trace => depthsOf(trace))
    .filter(depths => depths.length)
    .map(depths => Math.max(...depths) - Math.min(...depths))
    .sort((a, b) => a - b);
  if (!spans.length) return null;
  const depths = [...byRecording.values()].flatMap(depthsOf);
  return {
    typical: spans[Math.floor(spans.length / 2)],
    most: spans[spans.length - 1],
    axis: Math.max(...depths) - Math.min(...depths),
  };
}

/** How many kinds had been seen by each bucket, and how many were new in it.
 *
 * The question a merged collection raises and a composition plot cannot answer:
 * is this still finding things. A curve that keeps climbing says the fauna is
 * nowhere near exhausted; one that flattens says later footage is re-finding what
 * the earlier footage already had. The bars underneath are where the additions
 * came from, which is the part worth arguing with - a spike is either a new
 * habitat or a detector that changed its mind.
 *
 * Cumulative over the buckets in axis order, so it only makes sense on an axis
 * that has an order. That is time.
 */
export function accumulationTraces(rows) {
  const perBucket = new Map();
  for (const row of rows) {
    if (!perBucket.has(row.bucket)) perBucket.set(row.bucket, new Set());
    if (row.taxon) perBucket.get(row.bucket).add(row.taxon);
  }
  const buckets = [...perBucket.keys()];
  const seen = new Set();
  const cumulative = [];
  const fresh = [];
  for (const bucket of buckets) {
    let added = 0;
    for (const taxon of perBucket.get(bucket)) {
      if (!seen.has(taxon)) { seen.add(taxon); added += 1; }
    }
    fresh.push(added);
    cumulative.push(seen.size);
  }
  return { buckets, cumulative, fresh };
}

/** One axis's stacked composition, with what it is above it and a caveat below. */
async function drawAxis(panel, ctx, taxonomy, rank, counting) {
  const { axis, head, host } = panel;
  host.innerHTML = '<span class="text-muted small">Reading&hellip;</span>';
  const rows = await fetchBiodiversity(ctx, axis);
  const seconds = counting.rate ? await fetchFootageSeconds(ctx, axis) : null;
  const { traces, groups, split } = compositionTraces(rows, taxonomy, rank, ctx);
  if (!traces.length) {
    head.textContent = '';
    host.innerHTML = '<div class="no-data">Nothing named to place on this axis.</div>';
    return;
  }
  const animals = traces.reduce((sum, t) => sum + t.y.reduce((a, b) => a + b, 0), 0);
  const buckets = new Set(rows.map(r => r.bucket)).size;
  const shown = seconds ? asRate(traces, seconds) : traces;
  const bands = split.byGroup ? ctx.plot.groupingLabel('grouping') : rank;
  head.innerHTML = `<strong>${animals.toLocaleString()}</strong> animals over `
    + `<strong>${buckets}</strong> ${axis.deep ? `${axis.step} m depth bins`
      : `${axis.label.replace('by ', '')}s`}, in <strong>${groups}</strong> `
    + `${bands}${groups === 1 || split.byGroup ? '' : 's'}`;
  host.innerHTML = '';
  if (seconds) sayWhatTheRateDid(host, seconds, shown === traces);
  ctx.plot.append(host, shown, {
    height: 380, barmode: 'stack',
    margin: { l: 68, r: 16, t: 8, b: 64 },
    xaxis: { title: axis.title, type: 'category' },
    yaxis: { title: seconds?.size ? counting.axis : COUNTS[0].axis, rangemode: 'tozero' },
    showlegend: true,
    legend: { orientation: 'h', y: -0.24, x: 0 },
  });
}

/** The axis to open on, and the bucket size that suits the span it covers. */
async function defaultAxis(ctx) {
  return axisFor(ctx, 'clock');
}

/** Seconds of footage in each bucket, so a count can be turned into a rate.
 *
 * Not the length of the bucket - a year is a year whether the ship sailed or not.
 * What the rate has to divide by is how much footage was *read* in it, which is
 * the slices summed by their own length: the gap to the next slice of the same
 * recording, over that recording's frame rate. The last slice of each recording
 * has no next one and is left out, which costs one slice per file.
 *
 * Per bucket rather than per bucket and taxon, because the same slices carry
 * every taxon in them and summing per taxon would count the footage once per
 * species in it.
 */
async function fetchFootageSeconds(ctx, axis) {
  const { q } = ctx.sql;
  // The timestamp is aliased `stamp`, not `at`: `at` opens `AT TIME ZONE` in
  // DuckDB's grammar, so `CAST(at AS TIMESTAMP)` does not fail to find a column,
  // it fails to parse - and in the browser's wasm build a DuckDB error arrives as
  // `_setThrew is not defined`, with the message thrown away.
  if (!ctx.schema.allCols.includes('fps')) return new Map();
  const rec = recordingKey(ctx);
  const at = ctx.schema.allCols.includes('recorded_at') ? q('recorded_at') : 'NULL';
  const deep = ctx.schema.allCols.includes('depth_m') ? q('depth_m') : 'NULL';
  const rows = await ctx.queryRows(`
    WITH placed AS (
      SELECT CAST(${q('dim_t')} AS DOUBLE) AS t,
             LEAD(CAST(${q('dim_t')} AS DOUBLE))
               OVER (PARTITION BY ${rec} ORDER BY ${q('dim_t')}) AS next_t,
             ${q('fps')} AS fps, ${at} AS stamp, ${deep} AS deep
      FROM ${sliceTable(ctx)}
      ${whereClause(ctx, `${q('dim_t')} IS NOT NULL`)}
    )
    SELECT ${bucketExpr(ctx, axis, { at: 'stamp', deep: 'deep' })} AS bucket,
           SUM((next_t - t) / fps) AS seconds
    FROM placed
    WHERE next_t IS NOT NULL AND fps > 0
    GROUP BY 1 HAVING bucket IS NOT NULL`);
  return new Map(rows.map(row => [row.bucket, Number(row.seconds)]));
}

/** One axis, with the bucket size that suits the span this report happens to cover.
 *
 * Both axes need it and neither can be fixed: the time axis has to work for
 * fourteen minutes of one dive and a decade of one camera, and the depth axis for
 * a shelf transect and a four-kilometre descent. So the span is measured first
 * and the bucket chosen from it.
 */
async function axisFor(ctx, key) {
  const usable = axesFor(ctx.schema);
  const chosen = usable.find(a => a.key === key) ?? usable[0];
  if (!chosen) return null;
  if (chosen.deep) return { ...chosen, step: await depthStep(ctx) };
  // One coarse read to find the span, then the bucket that suits it.
  const rows = await fetchBiodiversity(ctx, { ...chosen, ...UNITS[1] });
  return { ...chosen, ...unitForSpan(rows) };
}

// Depth bins, coarsest last. A hundred metres over a 4,000 m descent is forty
// bars, which reads; over a 60 m shelf dive it is one.
const DEPTH_STEPS = [1, 5, 10, 25, 50, 100, 250, 500];

/** A depth bin that gives the report somewhere between a few and a few dozen bars. */
async function depthStep(ctx) {
  const { q } = ctx.sql;
  const [row = {}] = await ctx.queryRows(`
    SELECT MIN(${q('depth_m')}) AS low, MAX(${q('depth_m')}) AS high
    FROM ${sliceTable(ctx)}
    ${whereClause(ctx, `${q('depth_m')} IS NOT NULL`)}`);
  const span = Number(row.high) - Number(row.low);
  if (!Number.isFinite(span) || span <= 0) return DEPTH_STEPS[DEPTH_STEPS.length - 1];
  return DEPTH_STEPS.find(step => span / step <= MOST_DEPTH_BINS)
    ?? DEPTH_STEPS[DEPTH_STEPS.length - 1];
}

const MOST_DEPTH_BINS = 30;



const depthWidget = {
  id: 'temporal-depth',
  required_inputs: ['depth_m', 'dim_<axis>'],
  inputs: ['detection_count', 'detection_top_class', 'fps', 'name', 'path',
    'child_id', 'recorded_at'],
  group: 'Visualization',
  scope: 'slice',
  label: 'Depth',
  shortLabel: 'Depth',
  multiPlot: true,
  info: [
    'Where the footage was shot, and what was found there.',
    '',
    'The **dive profiles** are depth against time, one line per dive: a descent falls away, a',
    'transect holds one depth, a stepped survey terraces, a fixed camera is a flat line.',
    '',
    'Per dive rather than per file, because archives publish ROV video in segments of a few',
    'minutes and that is too short for a vehicle working the bottom to change depth - the change',
    'is between the segments. So the segments of one dive share a line on that dive\'s own clock,',
    'split wherever they are more than six hours apart. Each piece of line is footage read end to',
    'end and each gap is footage the report does not contain, left as a gap rather than joined up.',
    '',
    '**Change during the recording** subtracts each line\'s own starting depth, so every dive starts',
    'at zero and the axis is metres gained or lost. Use it where the dives sit close enough',
    'together that the absolute axis flattens them.',
    '',
    'Below, **what was found at each depth**. Deep-sea fauna varies with depth more than with',
    'anything else here, so these bands read as zonation. The bin follows the range the report',
    'covers. A bar is animals counted, or animals per second of footage - the second divides out',
    'how much footage each bin holds, since a bin the vehicle lingered in holds more of',
    'everything. The plot says whether that changed the shape or only the scale.',
    '',
    'Depth is whatever navigation the archive published beside the video, and no better.',
  ].join('\n'),

  requires(schema) {
    return schema.allCols.includes('depth_m') && (schema.dimCols ?? []).includes('dim_t');
  },

  async overviewMessage(ctx) {
    try {
      const { profiles } = await fetchDepthProfiles(ctx);
      if (!profiles.size) return null;
      const depths = [...profiles.values()].flatMap(t => t.y);
      if (!depths.length) return null;
      const low = Math.min(...depths);
      const high = Math.max(...depths);
      return `<strong>${profiles.size}</strong> recording${profiles.size === 1 ? '' : 's'} `
        + `between <strong>${Math.round(low).toLocaleString()}</strong> and `
        + `<strong>${Math.round(high).toLocaleString()} m</strong>.`;
    } catch { return null; }
  },

  /** The profiles, which are the most recognisable thing this card holds. */
  async overviewPlot(container, ctx) {
    const { profiles } = await fetchDepthProfiles(ctx);
    if (!profiles.size) return false;
    ctx.plot.appendMini(container, profileTraces(ctx, profiles, { mini: true }), {
      margin: { l: 34, r: 4, t: 4, b: 18 }, showlegend: false,
      yaxis: { autorange: 'reversed' },
    });
    return true;
  },

  async render(container, ctx) {
    const { profiles, unit, perDive } = await fetchDepthProfiles(ctx);
    if (!profiles.size) {
      container.innerHTML = '<div class="no-data">No depths in this report. Process with '
        + 'the slice-location processor, where the archive publishes navigation beside '
        + 'the video.</div>';
      return;
    }
    const head = appendDiv(container, 'margin-bottom:4px');
    head.className = 'small';
    const depths = [...profiles.values()]
      .flatMap(t => t.y).filter(value => Number.isFinite(value));
    const flat = howFlat(profiles);
    const segments = [...profiles.values()].reduce((sum, t) => sum + (t.segments ?? 1), 0);
    head.innerHTML = `<strong>${profiles.size}</strong> `
      + `${perDive ? 'dive' : 'profile'}${profiles.size === 1 ? '' : 's'}`
      + (perDive ? ` from <strong>${segments}</strong> published `
          + `segment${segments === 1 ? '' : 's'}` : '')
      + `, <strong>${Math.round(Math.min(...depths)).toLocaleString()}</strong> to `
      + `<strong>${Math.round(Math.max(...depths)).toLocaleString()} m</strong>`;

    let mode = PROFILE_MODES[0];
    const modes = document.createElement('select');
    modes.className = 'form-select form-select-sm';
    modes.style.cssText = 'max-width:280px;margin-bottom:6px';
    modes.replaceChildren(...PROFILE_MODES.map(m => option(m.label, m.key)));
    modes.value = mode.key;
    modes.addEventListener('change', () => {
      mode = PROFILE_MODES.find(m => m.key === modes.value) ?? PROFILE_MODES[0];
      drawProfiles();
    });
    container.appendChild(modes);

    const plotHost = appendDiv(container);
    const noteHost = appendDiv(container);
    function drawProfiles() {
      plotHost.innerHTML = '';
      ctx.plot.append(plotHost, profileTraces(ctx, profiles, { relative: mode.relative }), {
        height: 340, margin: { l: 68, r: 16, t: 8, b: 56 },
        xaxis: { title: unit },
        // Deeper is down, which is the only way a depth axis reads as depth.
        yaxis: { title: mode.axis, autorange: 'reversed' },
        showlegend: true,
        legend: { orientation: 'h', y: -0.3, x: 0 },
      });
      sayWhyItIsFlat(noteHost, profiles, flat, mode, perDive);
    }
    drawProfiles();
    const taxonomy = await loadTaxonomy(ctx);
    await renderComposition(appendDiv(container, 'margin-top:22px'), ctx, taxonomy, 'deep');
  },
};

/** Why the lines are flat, and where to look instead. */
function sayWhyItIsFlat(host, profiles, flat, mode, perDive) {
  host.innerHTML = '';
  host.className = 'text-muted small';
  const lines = [];
  if (profiles.size > MOST_PROFILES_NAMED) {
    lines.push(`${profiles.size} ${perDive ? 'dives' : 'recordings'}, too many to name `
      + 'in a legend - hover a line for which one it is.');
  }
  if (perDive) {
    lines.push('One line per dive, broken where the footage is. The archive publishes '
      + 'ROV video in five-minute segments and a collection samples a few of them per '
      + 'dive, so each piece of line is a segment that was read end to end and each '
      + 'gap is footage nobody collected - drawn as a gap rather than joined up, '
      + 'because a line across it would be an invented depth.');
  }
  if (flat && !mode.relative && flat.most < flat.axis * FLAT_ENOUGH) {
    lines.push(`The lines look flat because they are: a ${perDive ? 'dive' : 'recording'} `
      + `here moves ${flat.typical.toFixed(1)} m and ${flat.most.toFixed(0)} m at the `
      + `very most, against an axis ${Math.round(flat.axis).toLocaleString()} m tall. `
      + 'Switch to the change during the recording to see that movement.');
  }
  for (const text of lines) {
    const note = appendDiv(host, 'margin-top:4px');
    note.textContent = text;
  }
}

// Below this share of the axis, a line cannot be told from flat.
const FLAT_ENOUGH = 0.02;

const whenWidget = {
  id: 'temporal-when',
  required_inputs: ['recorded_at', 'dim_<axis>'],
  inputs: ['detection_count', 'detection_top_class', 'fps', 'name', 'path',
    'child_id', 'depth_m'],
  group: 'Visualization',
  scope: 'slice',
  label: 'When',
  shortLabel: 'When',
  multiPlot: true,
  info: [
    'When the animals were found, on the UTC clock the archive published beside the footage.',
    '',
    'Buckets are hours, days, months or years depending on how long the report spans, so a decade',
    'of one camera and a single dive both read, and merged expeditions line up on one axis.',
    '',
    'A bar is animals counted, or **animals per second of footage**. A year with twice the animals',
    'in it may only be a year with twice the dive time; the rate divides that out, and the plot',
    'says whether it changed the shape or only the scale. Bands are the taxonomy at the chosen',
    'rank, or the report\'s grouping where one is set.',
    '',
    'Below, the **species accumulation curve** - how many distinct kinds had been named by each',
    'bucket, with the new arrivals as bars. A curve still climbing means the fauna is nowhere near',
    'exhausted; one that flattens means later footage is re-finding what earlier footage already',
    'had.',
    '',
    'The bars are worth checking against the tiles: a spike is either a new habitat or a detector',
    'changing its mind. Read the whole curve as a floor - it counts only the classes the model was',
    'trained on, named at the rate the footage was sampled.',
  ].join('\n'),

  requires(schema) {
    return schema.allCols.includes('recorded_at')
      && schema.allCols.includes('detection_top_class')
      && (schema.dimCols ?? []).includes('dim_t');
  },

  async overviewMessage(ctx) {
    try {
      const axis = await axisFor(ctx, 'clock');
      if (!axis) return null;
      const rows = await fetchBiodiversity(ctx, axis);
      const { buckets, cumulative } = accumulationTraces(rows);
      if (!buckets.length) return null;
      return `<strong>${cumulative[cumulative.length - 1]}</strong> kinds named across `
        + `<strong>${buckets[0]}</strong> to <strong>${buckets[buckets.length - 1]}</strong>.`;
    } catch { return null; }
  },

  async overviewPlot(container, ctx) {
    const axis = await axisFor(ctx, 'clock');
    if (!axis) return false;
    const [rows, taxonomy] = await Promise.all([
      fetchBiodiversity(ctx, axis), loadTaxonomy(ctx)]);
    if (rows.length < 2) return false;
    const { traces } = compositionTraces(rows, taxonomy, 'phylum', ctx);
    if (!traces.length) return false;
    ctx.plot.appendMini(container, traces, {
      barmode: 'stack', margin: { l: 24, r: 4, t: 4, b: 18 }, showlegend: false,
      xaxis: { type: 'category' },
    });
    return true;
  },

  async render(container, ctx) {
    const axis = await axisFor(ctx, 'clock');
    if (!axis) {
      container.innerHTML = '<div class="no-data">No timestamps in this report, so there '
        + 'is no clock to place a sighting on. Process with the slice-location '
        + 'processor, where the archive publishes navigation beside the video.</div>';
      return;
    }
    const taxonomy = await loadTaxonomy(ctx);
    await renderComposition(appendDiv(container), ctx, taxonomy, 'clock');
    await renderAccumulation(appendDiv(container, 'margin-top:22px'), ctx, axis);
  },
};

/** The species accumulation curve, with what was new in each bucket underneath. */
async function renderAccumulation(container, ctx, axis) {
  const rows = await fetchBiodiversity(ctx, axis);
  const { buckets, cumulative, fresh } = accumulationTraces(rows);
  if (buckets.length < 2) {
    const why = appendDiv(container);
    why.className = 'text-muted small';
    why.textContent = 'Only one bucket on this axis, so there is no curve to draw.';
    return;
  }
  const head = appendDiv(container, 'margin-bottom:4px');
  head.className = 'small';
  const last = cumulative[cumulative.length - 1];
  const added = fresh[fresh.length - 1];
  head.innerHTML = `<strong>${last}</strong> kinds named by the end, `
    + `<strong>${added}</strong> of them first seen in the last bucket`;
  ctx.plot.append(appendDiv(container), [
    { type: 'bar', name: 'new kinds', x: buckets, y: fresh,
      marker: { color: '#adb5bd' },
      hovertemplate: '%{y} first seen here<extra>%{x}</extra>' },
    { type: 'scatter', mode: 'lines+markers', name: 'kinds so far',
      x: buckets, y: cumulative, line: { width: 2, color: '#0d6efd' },
      hovertemplate: '%{y} kinds by %{x}<extra></extra>' },
  ], {
    height: 300, margin: { l: 68, r: 16, t: 8, b: 64 },
    xaxis: { title: axis.title, type: 'category' },
    yaxis: { title: 'kinds of animal', rangemode: 'tozero' },
    showlegend: true,
    legend: { orientation: 'h', y: -0.3, x: 0 },
  });
}

// Order matters: within a group the viewer keeps registration order, so the gallery
// lands above the movement curve. Someone opening a deep-sea report wants to see what
// was found before they read how much the camera moved - and the taxonomy before the
// gallery, because "what is in here" comes before "show me one of them".
export default [taxonomyWidget, galleryWidget, whenWidget, depthWidget,
                timelineWidget, triageWidget];
