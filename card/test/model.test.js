import assert from 'node:assert/strict';
import { test } from 'node:test';
import { cellRects, normalizeModel, robotToModel, roomAt } from '../src/model.js';

const close = (a, b) => a.forEach((v, i) => assert.ok(Math.abs(v - b[i]) < 1e-9, `${a} ≠ ${b}`));

test('the dock lands on model.dock and the docked robot faces its heading', () => {
  for (const [angle, heading] of [[90, 180], [0, 90], [-30, 0], [200, 270]]) {
    const model = normalizeModel({ url: 'x.glb', dock: { position: [3, 0.1, 4], heading } });
    const t = robotToModel([-5083, -474, angle], model);
    close(t.point(-5083, -474), [3, 4]);
    assert.equal(t.floor, 0.1);
    const h = t.heading(angle);
    close([Math.sin(h), Math.cos(h)], [Math.sin((heading * Math.PI) / 180), Math.cos((heading * Math.PI) / 180)]);
  }
});

test('the map keeps its handedness and scale, and toMap undoes point', () => {
  const t = robotToModel([1000, 2000, 90], normalizeModel({ url: 'x.glb', dock: { position: [0, 0, 0], heading: 180 } }));
  // Facing +y on the map is facing -Z here, so 1 m ahead of the dock is 1 m along -Z and +x is +X.
  close(t.point(1000, 3000), [0, -1]);
  close(t.point(2000, 2000), [1, 0]);
  close(t.toMap(...t.point(1234, -567)), [1234, -567]);
  const none = robotToModel([1000, 2000, 90], null);
  close(none.point(1000, 2000), [1, -2]);
});

test('cellRects covers exactly the cells', () => {
  const runs = [2, 0, 3, 2, 1, 3, 1, 2, 4, 5, 2, 1, 2, 4, 2];
  const cells = new Set();
  for (let i = 0; i < runs.length; i += 3) for (let x = runs[i]; x < runs[i] + runs[i + 2]; x++) cells.add(`${x},${runs[i + 1]}`);
  const covered = [];
  for (const [x0, y0, x1, y1] of cellRects(runs)) for (let y = y0; y < y1; y++) for (let x = x0; x < x1; x++) covered.push(`${x},${y}`);
  assert.deepEqual(covered.sort(), [...cells].sort());
  assert.deepEqual(cellRects([2, 0, 3, 2, 1, 3]), [[2, 0, 5, 2]]);
});

test('roomAt finds the room under a point', () => {
  const map = { grid: { left: -100, top: -100, size: 50 }, cells: { 3: [1, 1, 2], 255: [0, 0, 1] }, rooms: { 3: { name: 'A' } } };
  assert.equal(roomAt(map, -100 + 75, -100 + 60), 3);
  assert.equal(roomAt(map, -100 + 125, -100 + 99), 3);
  assert.equal(roomAt(map, -100 + 10, -100 + 10), null);   // a wall
  assert.equal(roomAt(map, 500, 500), null);
});

test('normalizeModel rejects a model without a dock', () => {
  assert.throws(() => normalizeModel({ url: 'x.glb' }), /model.dock.position/);
  assert.throws(() => normalizeModel({ url: 'x.glb', dock: { position: [0, 0, 0] }, style: 'neon' }), /model.style/);
});
