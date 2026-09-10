import { describe, it, expect } from 'vitest';
import widgets from '../../src/pixel_patrol_deepsea/viewer/plugin_deepsea.js';
import SOURCE from '../../src/pixel_patrol_deepsea/viewer/plugin_deepsea.js?raw';

// The footage module ships several widgets from one file so they can share the
// window detection; the viewer registry accepts an array as readily as one object.
const schema = (over = {}) => ({
  metricCols: [], dimCols: [], groupCols: [], dimensionInfo: {}, allCols: [], blobCols: [], ...over,
});

const ENABLING = {
  'temporal-triage':   { allCols: ['frame_difference'], dimCols: ['dim_t'] },
  'temporal-timeline': { allCols: ['frame_difference'], dimCols: ['dim_t'] },
  // The gallery is tiles, so it needs something cached to put in them - a report
  // of numbers alone would draw a wall of "no stills stored".
  'temporal-gallery':  { allCols: ['frame_difference', 'detections'], dimCols: ['dim_t'] },
  // The taxonomy card carries both what was found and where: the tree, and the
  // same animals laid along a clock or a depth. The axis is a section of it
  // rather than a widget of its own, so the tree alone is enough to show the card.
  'temporal-taxonomy': { allCols: ['detection_top_class'], dimCols: ['dim_t'] },
  // Depth needs a depth, and nothing else: the profiles are worth drawing on a
  // report with no detector in it at all.
  'temporal-depth': { allCols: ['depth_m'], dimCols: ['dim_t'] },
  'temporal-when': {
    allCols: ['recorded_at', 'detection_top_class'], dimCols: ['dim_t'] },
};

describe('footage widgets', () => {
  it('exports the whole set', () => {
    expect(widgets.map(w => w.id).sort()).toEqual(Object.keys(ENABLING).sort());
  });

  it('offers what was found before how the camera moved', () => {
    // "What is in here" comes before "show me one of them", which comes before
    // "how much did the camera move".
    const ids = widgets.map(w => w.id);
    expect(ids.indexOf('temporal-taxonomy')).toBeLessThan(ids.indexOf('temporal-gallery'));
  });

  it('leaves the gallery out of a report with no pictures in it', () => {
    const numbersOnly = schema({ allCols: ['frame_difference'], dimCols: ['dim_t'] });
    const gallery = widgets.find(w => w.id === 'temporal-gallery');
    const timeline = widgets.find(w => w.id === 'temporal-timeline');
    expect(gallery.requires(numbersOnly)).toBe(false);
    // and the timeline still works, because a curve needs no stills
    expect(timeline.requires(numbersOnly)).toBe(true);
  });

  it('offers the gallery before the movement curve', () => {
    // Within a group the viewer keeps registration order, and someone opening a
    // deep-sea report wants what was found before how much the camera moved.
    const ids = widgets.map(w => w.id);
    expect(ids.indexOf('temporal-gallery')).toBeLessThan(ids.indexOf('temporal-timeline'));
  });

  it.each(widgets.map(w => [w.id, w]))('%s satisfies the plugin contract', (id, w) => {
    expect(typeof w.label).toBe('string');
    expect(typeof w.render).toBe('function');
    expect(typeof w.requires).toBe('function');
    expect(['Summary', 'Visualization']).toContain(w.group);
    expect(['file', 'image', 'slice']).toContain(w.scope);
  });

  it.each(widgets.map(w => [w.id, w]))('%s stays hidden without its columns', (id, w) => {
    expect(w.requires(schema())).toBe(false);
  });

  it.each(widgets.map(w => [w.id, w]))('%s appears once its columns are present', (id, w) => {
    expect(w.requires(schema(ENABLING[id]))).toBe(true);
  });

  it('declares every optional column it reads', () => {
    // The schema map draws widget-to-column links from these lists, so a column the
    // widgets read but do not declare simply does not appear as connected to anything.
    const read = new Set([...SOURCE.matchAll(/allCols\.includes\('([a-z_]+)'\)/g)].map(m => m[1]));
    const declared = new Set(widgets.flatMap(w => [...(w.required_inputs ?? []), ...(w.inputs ?? [])]));
    expect([...read].filter(col => !declared.has(col))).toEqual([]);
  });

  it('declares required inputs the way the schema catalog names them', () => {
    for (const w of widgets) {
      for (const col of w.required_inputs ?? []) {
        expect(col).not.toMatch(/^dim_[a-z]$/);   // catalog uses the dim_<axis> pattern
      }
    }
  });
});
