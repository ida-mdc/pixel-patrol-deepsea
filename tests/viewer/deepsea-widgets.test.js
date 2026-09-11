import { describe, it, expect } from 'vitest';
import { findWindows, WINDOW_KINDS, eventsToCsv, summariseRecording, renderSpeciesFilter,
         sliceAt, describeMoment, animateStills, keptByKind, reportFindsThings,
         keptByQuality, parseAnimals, animalFrames, sunburstOf, lineageOf, trunkOf,
         branchColours, scaleColour, measuredColours, oneEach, asRate, verdictSource,
         sayWhatTheRateDid, taxonColour, compositionTraces, accumulationTraces,
         profileTraces, howFlat, intoDives, appendEventStrip, fetchTimelines }
  from '../../src/pixel_patrol_deepsea/viewer/plugin_deepsea.js';

/** A timeline of per-slice movement values, one slice every `step` frames. */
const timeline = (values, step = 30) =>
  values.map((movement, i) => ({ t: i * step, movement }));

describe('footage timeline windows', () => {
  it('marks a run of zero movement as frozen footage', () => {
    const windows = findWindows(timeline([5, 6, 5, 0, 0, 0, 0, 5, 6]));
    const frozen = windows.filter(w => w.kind === 'frozen');
    expect(frozen).toHaveLength(1);
    expect(frozen[0].fromT).toBe(90);
    expect(frozen[0].toT).toBe(180);
    expect(frozen[0].slices).toBe(4);
  });

  it('does not call a single odd slice a window', () => {
    // One lone zero between busy slices is a blip, not a stretch worth opening.
    const windows = findWindows(timeline([5, 5, 5, 0, 5, 5, 5]));
    expect(windows.some(w => w.kind === 'frozen')).toBe(false);
  });

  it('separates a still camera from a moving one', () => {
    const values = [...Array(10).fill(5), ...Array(6).fill(0.1), ...Array(10).fill(5), 90, 95];
    const kinds = new Set(findWindows(timeline(values)).map(w => w.kind));
    expect(kinds.has('dwell')).toBe(true);
    expect(kinds.has('active')).toBe(true);
  });

  it('reports the mean movement of each window', () => {
    const [window] = findWindows(timeline([0, 0, 0, 0]));
    expect(window.movement).toBe(0);
  });

  it('returns nothing for a timeline too short to have a run', () => {
    expect(findWindows(timeline([5]))).toEqual([]);
    expect(findWindows([])).toEqual([]);
  });

  it('every window kind it can emit is described for the legend', () => {
    const values = [...Array(10).fill(5), ...Array(6).fill(0.1), ...Array(4).fill(0), 90, 95];
    for (const w of findWindows(timeline(values))) expect(WINDOW_KINDS[w.kind]).toBeTruthy();
  });
});

describe('frozen footage on lossy video', () => {
  it('treats codec-noise-level movement as frozen, not as a still camera', () => {
    // A real dive tape's dead tail sat around 0.0001-0.0045; live footage never
    // went below 0.13. Exact-zero matching missed the whole dead stretch.
    const values = [...Array(10).fill(5), ...Array(8).fill(0.0004), ...Array(10).fill(5)];
    const frozen = findWindows(timeline(values)).filter(w => w.kind === 'frozen');
    expect(frozen).toHaveLength(1);
    expect(frozen[0].slices).toBe(8);
  });

  it('still calls genuinely quiet live footage a dwell, not frozen', () => {
    const values = [...Array(10).fill(5), ...Array(6).fill(0.2), ...Array(10).fill(5)];
    const kinds = findWindows(timeline(values)).map(w => w.kind);
    expect(kinds).toContain('dwell');
    expect(kinds).not.toContain('frozen');
  });
});

describe('timecode export', () => {
  const event = (over = {}) => ({
    kind: 'dwell', fromT: 900, toT: 1200, slices: 11, movement: 0.886, score: 4.2,
    recording: { name: 'dive.mp4', fps: 30 }, ...over,
  });

  it('writes one row per event with both seconds and a readable timecode', () => {
    const csv = eventsToCsv([event()], () => 30).split('\n');
    expect(csv[0]).toBe('recording,start_seconds,end_seconds,start_timecode,kind,movement,'
      + 'score,animals,taxon,confidence');
    expect(csv[1]).toBe('dive.mp4,30.00,40.00,00:00:30,dwell,0.8860,4.20,,,');
  });

  it('quotes a recording name containing a comma', () => {
    const named = event({ recording: { name: 'dive, take 2.mp4', fps: 30 } });
    expect(eventsToCsv([named], () => 30).split('\n')[1]).toContain('"dive, take 2.mp4"');
  });

  it('produces a header even when nothing was detected', () => {
    expect(eventsToCsv([], () => 30).split('\n')).toHaveLength(1);
  });
});

describe('per-recording triage totals', () => {
  const recording = { name: 'dive.mp4', fps: 30 };

  it('totals each kind of stretch in seconds of footage', () => {
    // 30 slices of one second each: 10 busy, 8 frozen, 12 ordinary.
    const values = [...Array(10).fill(9), ...Array(8).fill(0.0002), ...Array(12).fill(1)];
    const line = timeline(values);
    const summary = summariseRecording(recording, line, findWindows(line));
    expect(Math.round(summary.total)).toBe(30);
    expect(summary.dead).toBeGreaterThan(6);
    expect(summary.busy).toBeGreaterThan(0);
    expect(summary.events).toBeGreaterThan(0);
  });

  it('reports a length even when no stretch stands out', () => {
    const line = timeline(Array(20).fill(1));
    const summary = summariseRecording(recording, line, findWindows(line));
    expect(Math.round(summary.total)).toBe(20);
    expect(summary.dead).toBe(0);
  });

  it('never totals more of a kind than the recording is long', () => {
    const line = timeline(Array(40).fill(0.0001));
    const summary = summariseRecording(recording, line, findWindows(line));
    expect(summary.dead).toBeLessThanOrEqual(summary.total);
  });
});

describe('empty frames versus real dwells', () => {
  // Same movement in both cases - only the amount of detail differs, which is
  // exactly the case a motion-only classifier gets wrong.
  const held = (detail) => Array.from({ length: 8 }, () => ({ movement: 0.3, structure: detail }));
  const busy = (detail) => Array.from({ length: 10 }, () => ({ movement: 5, structure: detail }));
  const withTimes = (rows) => rows.map((r, i) => ({ ...r, t: i * 30 }));

  it('calls a still camera on a detailed scene a dwell', () => {
    const kinds = findWindows(withTimes([...busy(1200), ...held(1900), ...busy(1200)])).map(w => w.kind);
    expect(kinds).toContain('dwell');
    expect(kinds).not.toContain('empty');
  });

  it('calls a still camera on open water empty, not a dwell', () => {
    const kinds = findWindows(withTimes([...busy(150), ...held(9), ...busy(150)])).map(w => w.kind);
    expect(kinds).toContain('empty');
    expect(kinds).not.toContain('dwell');
  });

  it('falls back to dwell when no detail metric was recorded', () => {
    const noDetail = withTimes([...busy(NaN), ...held(NaN), ...busy(NaN)]);
    const kinds = findWindows(noDetail).map(w => w.kind);
    expect(kinds).toContain('dwell');
    expect(kinds).not.toContain('empty');
  });
});

describe('timecodes carry what was found', () => {
  it('includes the animal count and taxon when a detector ran', () => {
    const seen = {
      kind: 'active', fromT: 0, toT: 30, slices: 2, movement: 5, score: 9,
      detections: 2.5, topClass: 'scyphozoa', recording: { name: 'dive.mp4', fps: 30 },
    };
    const row = eventsToCsv([seen], () => 30).split('\n')[1];
    expect(row).toContain('2.50,scyphozoa');
  });

  it('quotes a taxon containing a comma', () => {
    const seen = {
      kind: 'active', fromT: 0, toT: 30, slices: 2, movement: 5, score: 9,
      detections: 1, topClass: 'shrimp, unknown', recording: { name: 'd.mp4', fps: 30 },
    };
    expect(eventsToCsv([seen], () => 30)).toContain('"shrimp, unknown"');
  });
});

describe('animals as events', () => {
  const at = (rows) => rows.map((r, i) => ({ t: i * 30, ...r }));

  it('makes a stretch with detections its own kind', () => {
    const line = at([
      { movement: 1 }, { movement: 1 },
      { movement: 1, detections: 2 }, { movement: 1, detections: 1 },
      { movement: 1 }, { movement: 1 },
    ]);
    const kinds = findWindows(line).map(w => w.kind);
    expect(kinds).toContain('subject');
  });

  it('still calls dead footage frozen even when a detector fired', () => {
    // Nothing changing at all outranks a detection; a frozen frame repeating an
    // animal is dead tape, not a sighting.
    const line = at(Array.from({ length: 6 }, () => ({ movement: 0.0001, detections: 3 })));
    expect(findWindows(line).map(w => w.kind)).toContain('frozen');
  });

  it('falls back to movement when no detector ran', () => {
    const line = at([
      { movement: 5 }, { movement: 5 }, { movement: 0.2 }, { movement: 0.2 },
      { movement: 5 }, { movement: 5 },
    ]);
    const kinds = findWindows(line).map(w => w.kind);
    expect(kinds).not.toContain('subject');
    expect(kinds.length).toBeGreaterThan(0);
  });
});

describe('one event per species', () => {
  const at = (rows) => rows.map((r, i) => ({ t: i * 30, ...r }));

  it('does not merge two species into one sighting', () => {
    const line = at([
      { movement: 1, detections: 1, top_class: 'beroe' },
      { movement: 1, detections: 1, top_class: 'beroe' },
      { movement: 1, detections: 1, top_class: 'shrimp' },
      { movement: 1, detections: 1, top_class: 'shrimp' },
    ]);
    const subjects = findWindows(line).filter(w => w.kind === 'subject');
    expect(subjects.map(w => w.topClass).sort()).toEqual(['beroe', 'shrimp']);
  });

  it('still joins one species across a gap where nothing was classified', () => {
    // Movement of 5 sits between the dwell and activity thresholds here, so the two
    // middle slices are genuinely unremarkable footage rather than events of their own.
    const line = at([
      { movement: 1 }, { movement: 1 },
      { movement: 5, detections: 1, top_class: 'beroe' },
      { movement: 5, detections: 1, top_class: 'beroe' },
      { movement: 5 }, { movement: 5 },
      { movement: 5, detections: 1, top_class: 'beroe' },
      { movement: 5, detections: 1, top_class: 'beroe' },
      { movement: 10 }, { movement: 10 },
    ]);
    expect(findWindows(line).filter(w => w.kind === 'subject')).toHaveLength(1);
  });

  it('keeps a species that is only on screen for one slice', () => {
    // Three of the seven species in the midwater report appear in a single slice.
    // A lone movement blip is noise; a lone detection is a model naming an animal.
    const line = at([
      { movement: 5 }, { movement: 5 },
      { movement: 5, detections: 1, top_class: 'poeobius' },
      { movement: 5 }, { movement: 5 },
    ]);
    const subjects = findWindows(line).filter(w => w.kind === 'subject');
    expect(subjects).toHaveLength(1);
    expect(subjects[0].topClass).toBe('poeobius');
    expect(subjects[0].slices).toBe(1);
  });

  it('still ignores a lone slice that only crossed a movement threshold', () => {
    const line = at([{ movement: 5 }, { movement: 5 }, { movement: 0.001 },
                     { movement: 5 }, { movement: 5 }]);
    expect(findWindows(line).some(w => w.kind === 'frozen')).toBe(false);
  });

  it('names the event after the species that defines it', () => {
    const line = at(Array.from({ length: 4 },
      () => ({ movement: 1, detections: 2, top_class: 'cephalopoda' })));
    expect(findWindows(line)[0].topClass).toBe('cephalopoda');
  });
});


describe('the species filter', () => {
  const host = () => document.createElement('div');
  const eventsFor = (...names) => names.map(topClass => ({ topClass }));

  it('offers every species present, once each, in name order', () => {
    const node = host();
    renderSpeciesFilter(node, () => {})(eventsFor('shrimp', 'beroe', 'shrimp'));
    const select = node.querySelector('select');
    expect([...select.options].map(o => o.value)).toEqual(['all', 'beroe', 'shrimp']);
  });

  it('hides itself when there is nothing to choose between', () => {
    const node = host();
    const refresh = renderSpeciesFilter(node, () => {});
    refresh(eventsFor('beroe'));
    expect(node.querySelector('select').hidden).toBe(true);
    refresh(eventsFor('beroe', 'shrimp'));
    expect(node.querySelector('select').hidden).toBe(false);
  });

  it('keeps the chosen species when the scope changes but it is still there', () => {
    const node = host();
    const refresh = renderSpeciesFilter(node, () => {});
    refresh(eventsFor('beroe', 'shrimp'));
    node.querySelector('select').value = 'shrimp';
    expect(refresh(eventsFor('shrimp', 'cephalopoda'))).toBe('shrimp');
  });

  it('falls back to every species when the chosen one is no longer there', () => {
    // Silently keeping a dead selection would leave the gallery empty with no
    // visible reason why.
    const node = host();
    const refresh = renderSpeciesFilter(node, () => {});
    refresh(eventsFor('beroe', 'shrimp'));
    node.querySelector('select').value = 'shrimp';
    expect(refresh(eventsFor('beroe', 'cephalopoda'))).toBe('all');
    expect(node.querySelector('select').value).toBe('all');
  });

  it('reports a change to its caller', () => {
    const node = host();
    let chosen = null;
    renderSpeciesFilter(node, v => { chosen = v; })(eventsFor('beroe', 'shrimp'));
    const select = node.querySelector('select');
    select.value = 'beroe';
    select.dispatchEvent(new Event('change'));
    expect(chosen).toBe('beroe');
  });

  it('does not treat a taxon name as markup', () => {
    const node = host();
    renderSpeciesFilter(node, () => {})(eventsFor('a" onerror="x', 'beroe'));
    const values = [...node.querySelector('select').options].map(o => o.value);
    expect(values).toContain('a" onerror="x');
    expect(node.innerHTML).not.toContain('onerror="x"');
  });
});

describe('detector confidence on an event', () => {
  const at = (rows) => rows.map((r, i) => ({ t: i * 30, ...r }));

  it('keeps the best look at the animal, not the average one', () => {
    // Mid-run the animal is half out of frame; the tile should still say 0.94.
    const line = at([
      { movement: 1 }, { movement: 1 },
      { movement: 1, detections: 1, top_class: 'cephalopoda', confidence: 0.94 },
      { movement: 1, detections: 1, top_class: 'cephalopoda', confidence: 0.31 },
      { movement: 1 }, { movement: 1 },
    ]);
    const [subject] = findWindows(line).filter(w => w.kind === 'subject');
    expect(subject.confidence).toBeCloseTo(0.94);
  });

  it('has no confidence to report when no detector ran', () => {
    const line = at([{ movement: 0 }, { movement: 0 }, { movement: 0 }]);
    expect(Number.isFinite(findWindows(line)[0].confidence)).toBe(false);
  });
});


describe('naming what a recording holds', () => {
  const rows = (...specs) => specs.map(([top_class, detections, confidence], i) =>
    ({ t: i * 30, movement: 1, detections, top_class, confidence }));

  it('names the species the detector was surest of, not the most numerous', () => {
    const summary = summariseRecording({ fps: 30 },
      rows(['shrimp', 4, 0.28], ['cephalopoda', 1, 0.94]), []);
    expect(summary.topClass).toBe('cephalopoda');
  });

  it('falls back to counts where no confidence was recorded', () => {
    const summary = summariseRecording({ fps: 30 },
      rows(['shrimp', 4, undefined], ['beroe', 1, undefined]), []);
    expect(summary.topClass).toBe('shrimp');
  });

  it('lists every species it saw, surest first', () => {
    const summary = summariseRecording({ fps: 30 },
      rows(['shrimp', 1, 0.3], ['beroe', 1, 0.9], ['shrimp', 1, 0.4]), []);
    expect(summary.species).toEqual(['beroe', 'shrimp']);
  });

  it('has no species to list when no detector ran', () => {
    const summary = summariseRecording({ fps: 30 },
      [{ t: 0, movement: 1 }, { t: 30, movement: 1 }], []);
    expect(summary.species).toEqual([]);
    expect(summary.topClass).toBe(null);
  });
});


describe('reading a position on the barcode', () => {
  const line = [{ t: 0 }, { t: 30 }, { t: 60 }, { t: 90 }];

  it('finds the slice under the pointer', () => {
    expect(sliceAt(line, 2.0, 30)).toBe(line[2]);
  });

  it('rounds to the nearest slice rather than the one before', () => {
    expect(sliceAt(line, 1.9, 30)).toBe(line[2]);
  });

  it('reports nothing past either end of the recording', () => {
    expect(sliceAt(line, -5, 30)).toBe(null);
    expect(sliceAt(line, 60, 30)).toBe(null);
    expect(sliceAt([], 1, 30)).toBe(null);
  });

  it('says what was in frame, with the species and how sure the detector was', () => {
    expect(describeMoment({ detections: 2, top_class: 'beroe', confidence: 0.91 }))
      .toBe(' · 2 beroe 0.91');
  });

  it('says nothing where no animal was found', () => {
    expect(describeMoment({ detections: 0, top_class: null })).toBe('');
    expect(describeMoment(null)).toBe('');
  });

  it('still reports a count when the species was not recorded', () => {
    expect(describeMoment({ detections: 1 })).toBe(' · 1');
  });
});


describe('cycling an event\'s frames', () => {
  const images = (n) => Array.from({ length: n }, () => document.createElement('img'));

  it('fits every frame into one fixed box', () => {
    // Crops are whatever size the animal was, so left to themselves the frames
    // resize the tile as it cycles.
    const host = document.createElement('div');
    const frames = images(3);
    animateStills(host, frames);
    expect(host.style.height).toBe('168px');
    for (const img of frames) {
      expect(img.style.height).toBe('100%');
      expect(img.style.width).toBe('100%');
      // Letterboxed by default; a steady clip asks for cover instead.
      expect(img.style.objectFit).toBe('contain');
    }
  });

  it('lets a steady clip fill the tile instead of letterboxing it', () => {
    // Frames cropped with one box are all the same shape, so there is nothing to
    // letterbox and filling the tile shows the animal bigger.
    const host = document.createElement('div');
    const frames = images(3);
    frames.forEach(img => { img.dataset.fit = 'cover'; });
    animateStills(host, frames);
    expect(frames[0].style.objectFit).toBe('cover');
  });

  it('shows the first frame and hides the rest', () => {
    const host = document.createElement('div');
    const frames = images(3);
    animateStills(host, frames);
    expect(frames.map(i => i.style.display)).toEqual(['block', 'none', 'none']);
  });

  it('holds a single frame still rather than starting a timer', () => {
    const host = document.createElement('div');
    const stop = animateStills(host, images(1));
    expect(typeof stop).toBe('function');
    expect(host.children).toHaveLength(1);
  });

  it('replaces whatever the tile was showing before', () => {
    const host = document.createElement('div');
    host.textContent = 'click to play';
    animateStills(host, images(2));
    expect(host.textContent).toBe('');
    expect(host.children).toHaveLength(2);
  });
});


describe('things that moved that nothing could name', () => {
  const at = (rows) => rows.map((r, i) => ({ t: i * 30, ...r }));

  it('marks a stretch where something moved and no detector named it', () => {
    const line = at([
      { movement: 5 }, { movement: 5 },
      { movement: 5, movers: 1 }, { movement: 5, movers: 2 },
      { movement: 5 }, { movement: 5 },
    ]);
    expect(findWindows(line).map(w => w.kind)).toContain('unnamed');
  });

  it('prefers the name where the detector had one', () => {
    // A name is more information than "something was there", so a slice with both
    // belongs to the species, not to the unknown pile.
    const line = at([
      { movement: 5 }, { movement: 5 },
      { movement: 5, movers: 1, detections: 1, top_class: 'fish' },
      { movement: 5, movers: 1, detections: 1, top_class: 'fish' },
      { movement: 5 }, { movement: 5 },
    ]);
    const kinds = findWindows(line).map(w => w.kind);
    expect(kinds).toContain('subject');
    expect(kinds).not.toContain('unnamed');
  });

  it('still calls dead footage frozen even when something appeared to move', () => {
    const line = at([{ movement: 0, movers: 3 }, { movement: 0, movers: 3 },
                     { movement: 0, movers: 3 }]);
    expect(findWindows(line).every(w => w.kind === 'frozen')).toBe(true);
  });

  it('says nothing about unnamed movers on a report where motion never ran', () => {
    const line = at([{ movement: 5 }, { movement: 5 }, { movement: 5 }, { movement: 5 }]);
    expect(findWindows(line).some(w => w.kind === 'unnamed')).toBe(false);
  });

  it('has a colour and an explanation like every other band', () => {
    expect(WINDOW_KINDS.unnamed.color).toBeTruthy();
    expect(WINDOW_KINDS.unnamed.desc).toMatch(/nobody has described/);
  });
});

describe('what the gallery shows by default', () => {
  const event = (kind) => ({ kind, fromT: 0, recording: { name: 'd.mp4' } });

  it('keeps only things that were found, not things the footage did', () => {
    // A dive is mostly camera movement over open water. Showing all of it makes a
    // gallery of water with the animals buried in it.
    const kept = ['subject', 'unnamed', 'frozen', 'dwell', 'empty', 'active']
      .map(event).filter(e => keptByKind(e, 'found')).map(e => e.kind);
    expect(kept).toEqual(['subject', 'unnamed']);
  });

  it('still lets every kind through when asked', () => {
    expect(keptByKind(event('empty'), 'all')).toBe(true);
    expect(keptByKind(event('subject'), 'all')).toBe(true);
  });

  it('still narrows to one kind when asked', () => {
    expect(keptByKind(event('dwell'), 'dwell')).toBe(true);
    expect(keptByKind(event('subject'), 'dwell')).toBe(false);
  });

  it('knows a report can find things when a detector or motion ran', () => {
    expect(reportFindsThings({ allCols: ['detection_count'] })).toBe(true);
    expect(reportFindsThings({ allCols: ['moving_object_count'] })).toBe(true);
    expect(reportFindsThings({ allCols: ['bright_particle_count'] })).toBe(true);
  });

  it('knows a report that only measures movement cannot', () => {
    // There the movement kinds are all there is, and they are worth showing.
    expect(reportFindsThings({ allCols: ['frame_difference', 'fps'] })).toBe(false);
    expect(reportFindsThings({})).toBe(false);
  });
});

describe('the floor and the minimum length', () => {
  const event = (over = {}) => ({ kind: 'subject', slices: 4, confidence: 0.8,
                                  fromT: 0, recording: { name: 'd.mp4' }, ...over });

  it('drops a detection the model was unsure about', () => {
    expect(keptByQuality(event({ confidence: 0.2 }), 0.4, 1)).toBe(false);
    expect(keptByQuality(event({ confidence: 0.8 }), 0.4, 1)).toBe(true);
  });

  it('drops a stretch too short to be an encounter', () => {
    expect(keptByQuality(event({ slices: 1, kind: 'activity' }), 0, 2)).toBe(false);
    expect(keptByQuality(event({ slices: 2, kind: 'activity' }), 0, 2)).toBe(true);
  });

  it('does not hold an unnamed mover to a confidence it never had', () => {
    // Scoring the unknown band by a threshold it cannot meet would silently
    // delete the only thing here that can point at an undescribed species.
    const mover = event({ kind: 'unnamed', confidence: undefined });
    expect(keptByQuality(mover, 0.7, 1)).toBe(true);
  });

  it('does drop a named event with no score at all', () => {
    expect(keptByQuality(event({ confidence: undefined }), 0.4, 1)).toBe(false);
  });

  it('lets everything through when both controls are open', () => {
    expect(keptByQuality(event({ confidence: 0.01, slices: 1 }), 0, 1)).toBe(true);
  });

  it('keeps a named animal seen for a single slice', () => {
    // Footage sliced at five seconds holds a great many animals for one slice
    // only, and a minimum length is about movement noise, not about sightings.
    expect(keptByQuality(event({ kind: 'subject', slices: 1 }), 0, 4)).toBe(true);
    expect(keptByQuality(event({ kind: 'dwell', slices: 1 }), 0, 4)).toBe(false);
  });
});

describe('animating an event from the animals in it', () => {
  it('reads the per-animal crops out of the column', () => {
    const json = JSON.stringify([{ class: 'fish', conf: 0.7, crop: 'AAAA' },
                                 { class: 'fish', conf: 0.6, crop: 'BBBB' }]);
    expect(parseAnimals(json).map(a => a.crop)).toEqual(['AAAA', 'BBBB']);
  });

  it('ignores an entry with no crop, which cannot be shown', () => {
    const json = JSON.stringify([{ class: 'fish', conf: 0.7 }, { class: 'fish', crop: 'X' }]);
    expect(parseAnimals(json)).toHaveLength(1);
  });

  it('survives a column that is not json', () => {
    expect(parseAnimals('not json')).toEqual([]);
    expect(parseAnimals(null)).toEqual([]);
  });
});

describe("the frames one event's tile plays", () => {
  const frame = (t, of, second, box, extra = {}) =>
    ({ t, of, second, box, class: 'sea pen', clip: true, crop: `${t}-${of}-${second}`, ...extra });
  const event = { recording: { name: 'BD.mov' }, fromT: 0, toT: 50, slices: 2, topClass: 'sea pen' };

  it('plays one clip, not every clip the event spans', () => {
    // A five-second event covers several slices, and every slice cut a clip of every
    // animal in it. Playing them in a row is a jump cut between different animals.
    const animals = new Map([['BD.mov', [
      { t: 0, second: 0, box: [100, 100, 200, 200], class: 'sea pen', conf: 0.9, crop: 'best' },
      frame(0, 0, 0, [100, 100, 200, 200]), frame(0, 0, 0.1, [100, 100, 200, 200]),
      frame(50, 0, 5, [900, 900, 1000, 1000]), frame(50, 0, 5.1, [900, 900, 1000, 1000]),
    ]]]);
    const played = animalFrames(event, animals, 8);
    expect(new Set(played.map(a => a.t)).size).toBe(1);
  });

  it('plays the clip cut with the box the animal was found in', () => {
    const animals = new Map([['BD.mov', [
      { t: 0, second: 0, box: [900, 900, 1000, 1000], class: 'sea pen', conf: 0.9, crop: 'best' },
      frame(0, 0, 0, [100, 100, 200, 200]), frame(0, 0, 0.1, [100, 100, 200, 200]),
      frame(0, 1, 0, [900, 900, 1000, 1000]), frame(0, 1, 0.1, [900, 900, 1000, 1000]),
    ]]]);
    expect(animalFrames(event, animals, 8).every(a => a.of === 1)).toBe(true);
  });

  it('falls back to the per-detection crops when there is no clip', () => {
    const animals = new Map([['BD.mov', [
      { t: 0, second: 0, box: [1, 1, 2, 2], class: 'sea pen', conf: 0.9, crop: 'one' },
      { t: 50, second: 5, box: [1, 1, 2, 2], class: 'sea pen', conf: 0.8, crop: 'two' },
    ]]]);
    expect(animalFrames(event, animals, 8).map(a => a.crop)).toEqual(['one', 'two']);
  });

  it('leaves out species the event is not named after', () => {
    const animals = new Map([['BD.mov', [
      { t: 0, second: 0, box: [1, 1, 2, 2], class: 'sea pen', conf: 0.9, crop: 'mine' },
      { t: 0, second: 0, box: [9, 9, 9, 9], class: 'urchin', conf: 0.5, crop: 'theirs' },
    ]]]);
    expect(animalFrames(event, animals, 8).map(a => a.crop)).toEqual(['mine']);
  });
});

describe("an animal with no clip of its own", () => {
  it('shows its own crops rather than a neighbour\'s film', () => {
    const event = { recording: { name: 'BD.mov' }, fromT: 0, toT: 0, slices: 1, topClass: 'sea pen' };
    const animals = new Map([['BD.mov', [
      { t: 0, second: 0, box: [900, 900, 1000, 1000], class: 'sea pen', conf: 0.9, crop: 'mine' },
      { t: 0, of: 0, second: 0, box: [1, 1, 20, 20], class: 'sea pen', clip: true, crop: 'theirs-a' },
      { t: 0, of: 1, second: 0, box: [50, 50, 70, 70], class: 'sea pen', clip: true, crop: 'theirs-b' },
    ]]]);
    expect(animalFrames(event, animals, 8).map(a => a.crop)).toContain('mine');
  });
});


describe('colouring the taxonomy tree', () => {
  // Two phyla, one of them with two classes under it, plus a name the register
  // does not know - which is every shape the tree can take.
  const taxonomy = {
    'sea pen':     { kingdom: 'Animalia', phylum: 'Cnidaria', class: 'Octocorallia' },
    'jelly':       { kingdom: 'Animalia', phylum: 'Cnidaria', class: 'Scyphozoa' },
    'sea cucumber':{ kingdom: 'Animalia', phylum: 'Echinodermata', class: 'Holothuroidea' },
  };
  const counts = { 'sea pen': 40, jelly: 6, 'sea cucumber': 12, gizmo: 3 };
  const ctx = { color: { palette: 'tab10',
                         getColors: (_p, n) => Array.from({ length: n }, (_v, i) => `c${i}`) } };

  it('places every name in the tree, known to the register or not', () => {
    const tree = sunburstOf(counts, taxonomy);
    expect(tree.labels).toContain('Cnidaria');
    expect(tree.labels).toContain('gizmo');
    expect(tree.ids.length).toBe(tree.branches.length);
  });

  it('gives one phylum one colour, whatever depth a node sits at', () => {
    const tree = sunburstOf(counts, taxonomy);
    const colours = branchColours(ctx, tree);
    const colourOf = (label) => colours[tree.labels.indexOf(label)];
    expect(colourOf('Octocorallia')).toBe(colourOf('Cnidaria'));
    expect(colourOf('Scyphozoa')).toBe(colourOf('Cnidaria'));
    expect(colourOf('Echinodermata')).not.toBe(colourOf('Cnidaria'));
  });

  it('leaves the rings above the phylum neutral', () => {
    // The complaint this fixes was a tree of one colour, and the cause was that
    // every animal shares a kingdom. Colouring the kingdom would bring it back.
    const tree = sunburstOf(counts, taxonomy);
    const colours = branchColours(ctx, tree);
    expect(tree.branches[tree.labels.indexOf('Animalia')]).toBe(null);
    expect(colours[tree.labels.indexOf('Animalia')]).toBe('#5a6472');
  });

  it('gives the widest phylum the palette\'s first colour', () => {
    const tree = sunburstOf(counts, taxonomy);
    const colours = branchColours(ctx, tree);
    expect(colours[tree.labels.indexOf('Cnidaria')]).toBe('c0');
  });

  it('reads the phylum by rank rather than by counting rings', () => {
    // A lineage missing a rank the register does not know is shorter, so a fixed
    // depth finds the phylum in one entry and the class in the next.
    const sparse = { 'odd worm': { kingdom: 'Animalia', phylum: 'Annelida' } };
    const lineage = lineageOf('odd worm', sparse);
    expect(trunkOf('odd worm', lineage, sparse)).toEqual({ name: 'Annelida', depth: 1 });
    const named = { 'sea pen': taxonomy['sea pen'] };
    expect(trunkOf('sea pen', lineageOf('sea pen', named), named).name).toBe('Cnidaria');
  });

  it('still colours a tree when the host offers no palette', () => {
    const tree = sunburstOf(counts, taxonomy);
    const colours = branchColours({ color: {} }, tree);
    expect(new Set(colours.filter(Boolean)).size).toBeGreaterThan(1);
  });
});


describe('the colour of the footage along its length', () => {
  // The canvas the strip is painted on has no 2D context under happy-dom, so what
  // is testable is everything up to the paint: which source the colour comes from,
  // and whether it survives the trip.
  const ctx = (columns, rows = []) => ({
    schema: { allCols: columns },
    sql: { q: (name) => `"${name}"`, dimSubsetWhere: () => ['"dim_t" IS NOT NULL'],
           groupCol: () => '"name"' },
    state: { groupCol: null },
    where: '',
    asked: [],
    async queryRows(sql) { this.asked.push(sql); return rows; },
  });
  const COLOURS = ['slice_red', 'slice_green', 'slice_blue'];
  const recording = { name: 'dive.mp4' };

  it('reads the colour from the columns the pipeline measured', async () => {
    const rows = [{ t: 0, r: 200, g: 100, b: 20 }, { t: 30, r: 10, g: 12, b: 40 }];
    const got = await measuredColours(ctx(COLOURS, rows), recording);
    expect(got.times).toEqual([0, 30]);
    expect(got.colours).toEqual([[200, 100, 20], [10, 12, 40]]);
  });

  it('says nothing rather than nothing-coloured when the columns are absent', async () => {
    // Null is what lets the caller fall back to the cached stills. An empty strip
    // here would report colour footage as colourless.
    expect(await measuredColours(ctx(['slice_thumbnail']), recording)).toBe(null);
  });

  it('falls back when the columns are there but the recording has no colour', async () => {
    expect(await measuredColours(ctx(COLOURS, []), recording)).toBe(null);
  });

  it('scales ten-bit footage without painting it black', () => {
    // The means are in the source's units, and the archive holds both 8-bit and
    // 10-bit recordings. A fixed divisor of 255 would clip one and a fixed 1023
    // would darken the other by a factor of four.
    expect(scaleColour([255, 128, 0])).toEqual([255, 128, 0]);
    expect(scaleColour([1023, 512, 0])).toEqual([255, 128, 0]);
    expect(scaleColour([1.0, 0.5, 0])).toEqual([255, 128, 0]);
  });

  it('treats a missing channel as dark rather than as NaN', () => {
    expect(scaleColour([200, null, undefined])).toEqual([200, 0, 0]);
  });
});


describe('counting a taxonomy by kind rather than by head', () => {
  const taxonomy = {
    'sea pen': { kingdom: 'Animalia', phylum: 'Cnidaria' },
    jelly:     { kingdom: 'Animalia', phylum: 'Cnidaria' },
    urchin:    { kingdom: 'Animalia', phylum: 'Echinodermata' },
  };

  it('gives every named class the same weight', () => {
    expect(oneEach({ 'sea pen': 10000, jelly: 3, urchin: 1 }))
      .toEqual({ 'sea pen': 1, jelly: 1, urchin: 1 });
  });

  it('makes a sparse phylum visible next to an abundant one', () => {
    // The whole point of the second mode: ten thousand sea pens and one urchin is
    // one arc and a sliver by abundance, and two thirds to one third by kind.
    const counts = { 'sea pen': 10000, jelly: 3, urchin: 1 };
    const byHead = sunburstOf(counts, taxonomy);
    const byKind = sunburstOf(oneEach(counts), taxonomy);
    const widthOf = (tree, label) => tree.values[tree.labels.indexOf(label)];
    expect(widthOf(byHead, 'Echinodermata') / widthOf(byHead, 'Cnidaria')).toBeLessThan(0.01);
    expect(widthOf(byKind, 'Echinodermata') / widthOf(byKind, 'Cnidaria')).toBeCloseTo(0.5);
  });
});

describe('animals as a rate rather than a count', () => {
  const traces = [{ name: 'Cnidaria', x: ['2016', '2018'], y: [100, 50] }];

  it('divides each bucket by the footage behind it', () => {
    const seconds = new Map([['2016', 200], ['2018', 25]]);
    expect(asRate(traces, seconds)[0].y).toEqual([0.5, 2]);
  });

  it('leaves a gap rather than a zero where no footage was measured', () => {
    // A zero says "we looked and saw nothing"; there was nothing to divide by.
    const [trace] = asRate(traces, new Map([['2016', 200]]));
    expect(trace.y).toEqual([0.5, null]);
  });

  it('leaves the counts alone when nothing can be measured', () => {
    expect(asRate(traces, new Map())).toBe(traces);
  });

  it('does not alter the traces it was given', () => {
    asRate(traces, new Map([['2016', 200], ['2018', 25]]));
    expect(traces[0].y).toEqual([100, 50]);
  });
});

describe('the verdict shares as a table', () => {
  const summary = (name, total, dead) => ({
    recording: { name, group: 'EX2503' },
    total, dead, held: 1, empty: 1, busy: total - dead - 2,
  });

  it('is a derived table, not a statement that creates one', () => {
    const source = verdictSource([summary('a.mp4', 10, 4)], 'Frozen');
    expect(source.table).not.toMatch(/CREATE|VIEW/i);
    expect(source.table).toContain('VALUES');
    expect(source.where).toBe("WHERE verdict = 'Frozen'");
  });

  it('casts the share, because VALUES infers a decimal', () => {
    // Every other column the engine plots is a double, and this was the only
    // place a DECIMAL reached approx_quantile.
    expect(verdictSource([summary('a.mp4', 10, 4)], 'Frozen').table)
      .toContain('CAST(share AS DOUBLE)');
  });

  it('quotes no reserved word into the column a plot groups by', () => {
    expect(verdictSource([summary('a.mp4', 10, 4)], 'Frozen').table).toContain('grp');
  });

  it('says nothing when no recording has any length', () => {
    expect(verdictSource([summary('a.mp4', 0, 0)], 'Frozen')).toBe(null);
  });

  it('escapes a recording whose name carries a quote', () => {
    const source = verdictSource([summary("dive's tape.mp4", 10, 4)], 'Frozen');
    expect(source.table).toContain("dive''s tape.mp4");
  });
});


describe('saying whether the rate changed anything', () => {
  const said = (seconds, unchanged = false) => {
    const host = document.createElement('div');
    sayWhatTheRateDid(host, seconds, unchanged);
    return host.textContent;
  };

  it('admits when it is the count rescaled', () => {
    // The complaint that produced this: on one expedition every recording is
    // sampled to about the same length, so the rate is the same shape and a
    // reader is left doubting the number instead of the footage behind it.
    expect(said(new Map([['2016', 840], ['2018', 900], ['2022', 890]])))
      .toContain('the count rescaled');
  });

  it('says so when the footage really is uneven', () => {
    // The combined report: twentyfold, and the two disagree about which depth
    // was busiest - 1,500 m by count against 4,300 m by rate.
    const text = said(new Map([['1500', 200], ['4300', 4100]]));
    expect(text).toContain('20.5x');
    expect(text).toContain('a different shape');
  });

  it('says there was no frame rate to divide by', () => {
    expect(said(new Map())).toContain('No frame rate');
  });

  it('does not claim a shape changed when the traces were left alone', () => {
    expect(said(new Map([['1500', 200], ['4300', 4100]]), true))
      .toContain('Nothing to divide by');
  });
});


describe('one animal is one event, even when another interrupts it', () => {
  // A benthic frame holds several species and `detection_top_class` is whichever
  // was most confident, so one coral's sighting reads as A, B, A as the ranking
  // flickers. Comparing only against the previous run let B split A in two.
  const slices = (labels, step = 50) => labels.map((top_class, i) => ({
    t: i * step, movement: 5, peak: 1, structure: 100, objects: 0,
    detections: top_class ? 1 : 0, top_class, confidence: 0.9,
  }));

  it('rejoins a species a different one interrupted', () => {
    const found = findWindows(slices(['coral', 'anemone', 'coral']));
    const corals = found.filter(w => w.topClass === 'coral');
    expect(corals).toHaveLength(1);
    expect(corals[0].fromT).toBe(0);
    expect(corals[0].toT).toBe(100);
  });

  it('keeps the interrupting species as its own event', () => {
    const found = findWindows(slices(['coral', 'anemone', 'coral']));
    expect(found.filter(w => w.topClass === 'anemone')).toHaveLength(1);
  });

  it('leaves two sightings far apart as two animals', () => {
    // Beyond the bridging gap it is a re-visit or a second individual, and
    // collapsing those would undercount the dive.
    const labels = ['coral', ...Array(14).fill('anemone'), 'coral'];
    expect(findWindows(slices(labels)).filter(w => w.topClass === 'coral')).toHaveLength(2);
  });

  it('does not merge two different species into one event', () => {
    const found = findWindows(slices(['coral', 'anemone']));
    expect(new Set(found.map(w => w.topClass))).toEqual(new Set(['coral', 'anemone']));
  });
});


describe('colouring the bands the way the tree is coloured', () => {
  const ctx = (over = {}) => ({
    color: { palette: 'tab10',
             getColors: (_p, n) => Array.from({ length: n }, (_v, i) => `hue${i}`),
             group: (g) => `group-${g}` },
    groupLabel: (g) => `label-${g}`,
    plot: { groupingLabel: () => 'expedition' },
    state: {},
    ...over,
  });
  const taxonomy = { 'sea pen': { kingdom: 'Animalia', phylum: 'Cnidaria' },
                     urchin:    { kingdom: 'Animalia', phylum: 'Echinodermata' } };
  const rows = [
    { bucket: '2016', taxon: 'sea pen', grp: 'EX2205', animals: 10 },
    { bucket: '2016', taxon: 'urchin',  grp: 'EX2503', animals: 4 },
    { bucket: '2018', taxon: 'sea pen', grp: 'EX2503', animals: 7 },
  ];

  it('gives a taxon the same colour in the bands as in the tree', () => {
    // The whole point: a reader should be able to look from the Cnidaria arc to
    // the Cnidaria band and see one colour.
    const here = ctx();
    const tree = sunburstOf({ 'sea pen': 10, urchin: 4 }, taxonomy);
    const arc = branchColours(here, tree)[tree.labels.indexOf('Cnidaria')];
    const { traces } = compositionTraces(rows, taxonomy, 'phylum', here);
    const band = traces.find(t => t.name === 'Cnidaria');
    expect(band.marker.color).toBe(arc);
  });

  it('keeps a taxon colour steady when the rank changes', () => {
    // Colouring by position in the current plot made a taxon change hue between
    // the tree and the bands, and between one rank and the next.
    const here = ctx();
    expect(taxonColour(here, 'Cnidaria')).toBe(taxonColour(here, 'Cnidaria'));
    expect(taxonColour(here, 'Cnidaria')).not.toBe(taxonColour(here, 'Echinodermata'));
  });

  it('splits by the report grouping when one is set', () => {
    const here = ctx({ state: { groupCol: 'expedition' } });
    const { traces, split } = compositionTraces(rows, taxonomy, 'phylum', here);
    expect(split.byGroup).toBe(true);
    expect(traces.map(t => t.name).sort()).toEqual(['label-EX2205', 'label-EX2503']);
  });

  it('uses the report group colours rather than inventing its own', () => {
    // Otherwise this is the one card in the report where a colour means
    // something different from everywhere else.
    const here = ctx({ state: { groupCol: 'expedition' } });
    const { traces } = compositionTraces(rows, taxonomy, 'phylum', here);
    expect(traces.find(t => t.name === 'label-EX2503').marker.color).toBe('group-EX2503');
  });

  it('falls back to the taxonomy with no grouping set', () => {
    const { split } = compositionTraces(rows, taxonomy, 'phylum', ctx());
    expect(split.byGroup).toBe(false);
  });
});


describe('whether the collection is still finding things', () => {
  const rows = (pairs) => pairs.map(([bucket, taxon]) => ({ bucket, taxon, animals: 1 }));

  it('counts a kind once, in the bucket it was first seen', () => {
    const { buckets, cumulative, fresh } = accumulationTraces(rows([
      ['2016', 'beroe'], ['2018', 'beroe'], ['2018', 'sea pen'],
    ]));
    expect(buckets).toEqual(['2016', '2018']);
    expect(cumulative).toEqual([1, 2]);
    expect(fresh).toEqual([1, 1]);
  });

  it('flattens when later footage only re-finds what is known', () => {
    // The reading the curve exists for: a flat tail means the fauna is being
    // re-found rather than added to.
    const { cumulative } = accumulationTraces(rows([
      ['2016', 'beroe'], ['2018', 'beroe'], ['2022', 'beroe'],
    ]));
    expect(cumulative).toEqual([1, 1, 1]);
  });

  it('never goes down', () => {
    const { cumulative } = accumulationTraces(rows([
      ['a', 'x'], ['a', 'y'], ['b', 'x'], ['c', 'z'],
    ]));
    expect(cumulative).toEqual([2, 2, 3]);
  });

  it('keeps a bucket that named nothing, so a gap in the effort shows', () => {
    const { buckets, fresh } = accumulationTraces(rows([['a', 'x'], ['b', null]]));
    expect(buckets).toEqual(['a', 'b']);
    expect(fresh).toEqual([1, 0]);
  });
});


describe('one line per recording, visibly', () => {
  const ctx = (over = {}) => ({
    color: { palette: 'tab10',
             getColors: (_p, n) => Array.from({ length: n }, (_v, i) => `hue${i}`),
             group: (g) => `group-${g}` },
    groupLabel: (g) => `label-${g}`,
    state: {},
    ...over,
  });
  const profiles = (count) => new Map(Array.from({ length: count }, (_v, i) => [
    `/dives/dive${i}.mp4`, { grp: i % 2 ? 'EX2205' : 'EX2503', x: [0, 1], y: [100, 200] },
  ]));

  it('draws a trace per recording', () => {
    expect(profileTraces(ctx(), profiles(3))).toHaveLength(3);
  });

  it('gives each recording its own colour when nothing else distinguishes them', () => {
    // Every line used to be the same blue, which said nothing a single line
    // would not have said.
    const colours = profileTraces(ctx(), profiles(3)).map(t => t.line.color);
    expect(new Set(colours).size).toBe(3);
  });

  it('keeps a recording the same colour across redraws', () => {
    const first = profileTraces(ctx(), profiles(3)).map(t => t.line.color);
    const again = profileTraces(ctx(), profiles(3)).map(t => t.line.color);
    expect(again).toEqual(first);
  });

  it('carries the grouping instead where one is set', () => {
    const here = ctx({ state: { groupCol: 'expedition' } });
    const traces = profileTraces(here, profiles(4));
    expect(new Set(traces.map(t => t.line.color))).toEqual(
      new Set(['group-EX2205', 'group-EX2503']));
    // One legend entry per group, not per recording.
    expect(traces.filter(t => t.showlegend)).toHaveLength(2);
  });

  it('names them in the legend while the legend is still readable', () => {
    expect(profileTraces(ctx(), profiles(4)).every(t => t.showlegend)).toBe(true);
    expect(profileTraces(ctx(), profiles(40)).some(t => t.showlegend)).toBe(false);
  });

  it('shows no legend at all on a preview tile', () => {
    expect(profileTraces(ctx(), profiles(3), { mini: true })
      .some(t => t.showlegend)).toBe(false);
  });
});


describe('showing the movement an absolute axis hides', () => {
  const ctx = () => ({
    color: { palette: 'tab10',
             getColors: (_p, n) => Array.from({ length: n }, (_v, i) => `hue${i}`) },
    state: {},
  });
  // What the collection actually holds: a vehicle holding station on the bottom,
  // moving a few metres through a five-minute window, at wildly different depths.
  const held = new Map([
    ['/a.mp4', { grp: '', x: [0, 1, 2], y: [2262, 2264, 2266] }],
    ['/b.mp4', { grp: '', x: [0, 1, 2], y: [4807, 4815, 4823] }],
  ]);

  it('measures how flat the lines are against the axis they sit on', () => {
    const flat = howFlat(held);
    expect(flat.typical).toBe(16);
    expect(flat.most).toBe(16);
    expect(flat.axis).toBe(4823 - 2262);
  });

  it('starts every line at zero in the relative reading', () => {
    // The absolute axis is 2,561 m tall here, so a 4 m descent rounds to flat.
    // Subtracting each recording's own start is what makes it visible.
    const traces = profileTraces(ctx(), held, { relative: true });
    expect(traces.map(t => t.y[0])).toEqual([0, 0]);
    expect(traces.map(t => t.y[2])).toEqual([4, 16]);
  });

  it('leaves the absolute reading absolute', () => {
    const traces = profileTraces(ctx(), held);
    expect(traces[0].y).toEqual([2262, 2264, 2266]);
  });

  it('signs the relative hover, because up and down both happen', () => {
    const [trace] = profileTraces(ctx(), held, { relative: true });
    expect(trace.hovertemplate).toContain('%{y:+,.1f}');
  });

  it('says nothing about a report with no profiles in it', () => {
    expect(howFlat(new Map())).toBe(null);
  });
});


describe('recovering a dive from the segments it was published in', () => {
  // The archive publishes ROV video in five-minute files, so one dive arrives as
  // several. Nothing is cropped - five minutes is just too short for a vehicle
  // working the bottom to change depth, and the change is between the segments.
  const HOUR = 3600;
  const slice = (rec, at, depth) => ({ rec, grp: '', at, depth });

  it('joins segments of one dive into one line', () => {
    const dives = intoDives([
      slice('a.mp4', 0, 4859), slice('a.mp4', 300, 4857),
      slice('b.mp4', 2700, 4780), slice('b.mp4', 3000, 4778),
    ]);
    expect(dives.size).toBe(1);
    const [dive] = [...dives.values()];
    expect(dive.segments).toBe(2);
    expect(dive.y.filter(Number.isFinite)).toEqual([4859, 4857, 4780, 4778]);
  });

  it('breaks the line between segments rather than inventing a depth', () => {
    const [dive] = [...intoDives([
      slice('a.mp4', 0, 4859), slice('b.mp4', 2700, 4780),
    ]).values()];
    expect(dive.y).toEqual([4859, null, 4780]);
  });

  it('starts each dive at zero on its own clock', () => {
    const dives = intoDives([
      slice('a.mp4', 1000, 4859),
      slice('b.mp4', 1000 + 20 * HOUR, 2262),
    ]);
    expect(dives.size).toBe(2);
    expect([...dives.values()].map(d => d.x[0])).toEqual([0, 0]);
  });

  it('splits on a long gap and not on a short one', () => {
    // EX2503's widest gap inside one dive is 45 minutes; its narrowest gap
    // between two dives is 19 hours. Six hours separates them with room to spare.
    expect(intoDives([slice('a', 0, 100), slice('b', 45 * 60, 110)]).size).toBe(1);
    expect(intoDives([slice('a', 0, 100), slice('b', 19 * HOUR, 110)]).size).toBe(2);
  });

  it('measures the movement across a dive rather than within one segment', () => {
    const dives = intoDives([
      slice('a.mp4', 0, 4859), slice('a.mp4', 300, 4857),
      slice('b.mp4', 3 * HOUR, 4731),
    ]);
    expect(howFlat(dives).most).toBe(4859 - 4731);
  });
});


describe('the events as a strip, for a tile', () => {
  const slices = (count, step = 30) =>
    Array.from({ length: count }, (_v, i) => ({ t: i * step }));
  const drawn = (timeline, windows) => {
    const host = document.createElement('div');
    const did = appendEventStrip(host, timeline, windows);
    return { did, bands: [...host.querySelectorAll('.pp-strip-band')] };
  };

  it('places a band where its event sits along the recording', () => {
    // Ten slices 30 frames apart span 300 frames, so an event over the middle
    // two starts at 40% and is 20% wide.
    const { bands } = drawn(slices(10), [{ kind: 'subject', fromT: 120, toT: 150 }]);
    expect(bands).toHaveLength(1);
    expect(parseFloat(bands[0].style.left)).toBeCloseTo(40);
    expect(parseFloat(bands[0].style.width)).toBeCloseTo(20);
  });

  it('colours a band by what kind of event it is', () => {
    const { bands } = drawn(slices(4), [
      { kind: 'frozen', fromT: 0, toT: 0 }, { kind: 'subject', fromT: 90, toT: 90 }]);
    expect(bands.map(b => b.title)).toEqual(['Frozen', 'Subject']);
    expect(new Set(bands.map(b => b.style.background)).size).toBe(2);
  });

  it('names the species in the band a detector found', () => {
    const { bands } = drawn(slices(4),
      [{ kind: 'subject', fromT: 0, toT: 30, topClass: 'beroe' }]);
    expect(bands[0].title).toBe('Subject - beroe');
  });

  it('widens a band too thin to see', () => {
    // A one-slice event out of two hundred is real and 0.5% of a tile wide.
    const { bands } = drawn(slices(200), [{ kind: 'subject', fromT: 0, toT: 0 }]);
    expect(parseFloat(bands[0].style.width)).toBeGreaterThanOrEqual(0.6);
  });

  it('draws nothing rather than an empty strip when there are no events', () => {
    expect(drawn(slices(10), []).did).toBe(false);
    expect(drawn([], [{ kind: 'subject', fromT: 0, toT: 0 }]).did).toBe(false);
  });
});

describe('depth profiles stay drawable on a whole collection', () => {
  const wide = (recordings, each) => {
    const out = new Map();
    for (let r = 0; r < recordings; r++) {
      out.set(`rec${r}.mp4`, {
        grp: 'g', segments: 1,
        x: Array.from({ length: each }, (_, i) => i),
        y: Array.from({ length: each }, (_, i) => 100 + i),
      });
    }
    return out;
  };
  const ctx = () => ({ state: { groupCol: null }, color: {}, groupLabel: null });

  it('draws markers while there are few enough of them to see', () => {
    const [trace] = profileTraces(ctx(), wide(1, 50));
    expect(trace.mode).toBe('lines+markers');
    expect(trace.type).toBe('scatter');
  });

  it('drops to lines once the markers would overlap into a thick line', () => {
    const traces = profileTraces(ctx(), wide(1, 1200));
    expect(traces[0].mode).toBe('lines');
  });

  it('counts the whole figure, not one trace, when deciding', () => {
    // Forty profiles of two hundred points is the same eight thousand marks as
    // one profile of eight thousand, and the browser pays the same either way.
    const traces = profileTraces(ctx(), wide(40, 200));
    expect(traces[0].mode).toBe('lines');
    expect(traces[0].type).toBe('scattergl');
  });

  it('stays on svg for a report small enough not to need webgl', () => {
    expect(profileTraces(ctx(), wide(3, 100))[0].type).toBe('scatter');
  });
});

describe('a collection is read in one query, not one per recording', () => {
  // Triage and the taxonomy tree asked for one recording at a time and awaited
  // each before asking for the next. On 287 recordings that is 287 round trips to
  // duckdb in series, which is why they took minutes while the rest of the page
  // was ready.
  const ctxFor = (rows, asked) => ({
    sql: {
      q: name => `"${name}"`,
      dimSubsetWhere: () => ['1=1'],
      groupCol: () => null,
    },
    schema: { allCols: ['frame_difference', 'dim_t', 'name', 'detection_count',
                        'detection_top_class', 'detection_confidence'],
              dimCols: ['dim_t'] },
    state: { groupCol: null },
    where: '',
    async queryRows(sql) { asked.push(sql); return rows; },
  });

  it('asks once per batch of recordings, not once per recording', async () => {
    const asked = [];
    const rows = [];
    for (const rec of ['a.mp4', 'b.mp4', 'c.mp4']) {
      for (let t = 0; t < 6; t++) {
        rows.push({ rec, t: t * 10, movement: 5 + t, detections: 1,
                    top_class: 'fish', confidence: 0.9 });
      }
    }
    const ctx = ctxFor(rows, asked);
    const got = await fetchTimelines(ctx, [{ name: 'a.mp4' }, { name: 'b.mp4' }, { name: 'c.mp4' }]);
    expect(asked).toHaveLength(1);
    expect(asked[0]).not.toMatch(/ORDER BY/);
    expect([...got.keys()].sort()).toEqual(['a.mp4', 'b.mp4', 'c.mp4']);
    expect(got.get('b.mp4')).toHaveLength(6);
  });

  it('sorts each recording by time itself, since the query does not', async () => {
    // Sorting the whole table is the most expensive thing this could ask a browser
    // to do, and the rows are split by recording here anyway.
    const asked = [];
    const rows = [{ rec: 'a.mp4', t: 20, movement: 3 }, { rec: 'a.mp4', t: 0, movement: 1 },
                  { rec: 'a.mp4', t: 10, movement: 2 }];
    const got = await fetchTimelines(ctxFor(rows, asked), [{ name: 'a.mp4' }, { name: 'b.mp4' }]);
    expect(got.get('a.mp4').map(r => r.t)).toEqual([0, 10, 20]);
  });

  it('keeps each recording to its own rows', async () => {
    const asked = [];
    const rows = [{ rec: 'a.mp4', t: 0, movement: 1 }, { rec: 'b.mp4', t: 0, movement: 2 }];
    const got = await fetchTimelines(ctxFor(rows, asked), [{ name: 'a.mp4' }, { name: 'b.mp4' }]);
    expect(got.get('a.mp4').map(r => r.movement)).toEqual([1]);
    expect(got.get('b.mp4').map(r => r.movement)).toEqual([2]);
  });

  it('drops unmeasurable rows exactly as the single-recording read does', async () => {
    // Parity, not an opinion: both keep a null as zero - `Number(null)` is 0 - and
    // both drop what cannot be read as a number at all. Worth pinning, because the
    // two queries have to return the same rows for the batched one to be a
    // substitution rather than a second behaviour.
    const rows = [{ rec: 'a.mp4', t: 0, movement: 1 },
                  { rec: 'a.mp4', t: 1, movement: null },
                  { rec: 'a.mp4', t: 2, movement: 'seabed' }];
    const got = await fetchTimelines(ctxFor(rows, []), [{ name: 'a.mp4' }, { name: 'b.mp4' }]);
    expect(got.get('a.mp4').map(r => r.t)).toEqual([0, 1]);
  });
});
