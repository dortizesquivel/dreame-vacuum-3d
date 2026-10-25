import assert from 'node:assert/strict';
import { test } from 'node:test';
import { extendTrail, mops, vacuums, wetness } from '../src/trail.js';

const stroke = (mode, ...points) => ({ mode, points });

test('a path seen for the first time is dated backwards at the robot speed', () => {
  const t = extendTrail(null, [stroke('both', [0, 0], [300, 0], [300, 600])], 10000);
  assert.deepEqual(t.pieces.map((p) => p.t), [8000, 10000]);   // 600 mm took 2 s, so the first piece ended 2 s earlier
  assert.deepEqual(t.pieces.map((p) => p.mode), ['both', 'both']);
});

test('new pieces are spread between the last update and now', () => {
  let t = extendTrail(null, [stroke('vacuum', [0, 0], [100, 0])], 1000);
  t = extendTrail(t, [stroke('vacuum', [0, 0], [100, 0], [200, 0], [300, 0]), stroke('mop', [0, 50], [0, 60])], 4000);
  assert.deepEqual(t.pieces.map((p) => [p.mode, p.t]), [['vacuum', 1000], ['vacuum', 2000], ['vacuum', 3000], ['mop', 4000]]);
  const again = extendTrail(t, [stroke('vacuum', [0, 0], [100, 0], [200, 0], [300, 0]), stroke('mop', [0, 50], [0, 60])], 5000);
  assert.equal(again.pieces.length, 4);   // nothing new
});

test('another path is a new task: a new trail', () => {
  const t = extendTrail(null, [stroke('mop', [0, 0], [100, 0], [200, 0])], 1000);
  assert.equal(extendTrail(t, [stroke('mop', [5, 5], [6, 6])], 2000).pieces.length, 1);
  assert.equal(extendTrail(t, [stroke('mop', [0, 0], [100, 0])], 2000).pieces.length, 1);   // shorter: restarted
  assert.equal(extendTrail(t, null, 2000).pieces.length, 0);
});

test('wetness and modes', () => {
  assert.equal(wetness(0, 1000), 1);
  assert.ok(wetness(500, 1000) > 0.5);
  assert.equal(wetness(1000, 1000), 0);
  assert.equal(wetness(5000, 1000), 0);
  assert.deepEqual(['vacuum', 'mop', 'both'].map(vacuums), [true, false, true]);
  assert.deepEqual(['vacuum', 'mop', 'both'].map(mops), [false, true, true]);
});
