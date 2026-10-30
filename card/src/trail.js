// The robot's trail, with a time for each piece: the floor effects draw it, and the wet floor dries piece by piece.

const SPEED = 0.3;     // mm/ms (0.3 m/s), about a robot's: to date a path that was already there

/**
 * Add a path's new pieces to a trail. path: the whole task so far, as strokes [{ mode, points: [[x, y], …] }] (mm).
 * New pieces get times spread between the last update and `now`, so the trail dries as the robot drew it, not in steps;
 * a path seen for the first time (the card just opened) is dated backwards from `now` at the robot's speed while it
 * cleans (`live`), and long ago when it doesn't (a finished cleaning: dry). A path that isn't the old one grown (a new
 * task) starts a new trail.
 *   trail: { seen: [points per stroke], first: 'x,y', last: ms, pieces: [{ mode, a: [x, y], b: [x, y], t }] }
 */
export function extendTrail(trail, path, now, live = true) {
  const strokes = Array.isArray(path) ? path : [];
  const first = strokes[0]?.points?.[0]?.join() ?? null;
  const grown = trail && first === trail.first && strokes.length >= trail.seen.length
    && trail.seen.every((n, i) => (strokes[i]?.points?.length ?? 0) >= n);
  const t = grown ? trail : { seen: [], first, last: null, pieces: [] };
  const added = [];
  strokes.forEach((s, i) => {
    const pts = Array.isArray(s?.points) ? s.points : [];
    for (let j = Math.max(1, t.seen[i] ?? 0); j < pts.length; j++) added.push({ mode: s.mode ?? 'vacuum', a: pts[j - 1], b: pts[j] });
    t.seen[i] = pts.length;
  });
  if (t.last === null) {
    let at = live ? now : -Infinity;
    for (let k = added.length - 1; k >= 0; k--) {
      added[k].t = at;
      at -= Math.hypot(added[k].b[0] - added[k].a[0], added[k].b[1] - added[k].a[1]) / SPEED;
    }
  } else {
    added.forEach((p, k) => { p.t = t.last + ((now - t.last) * (k + 1)) / added.length; });
  }
  t.pieces.push(...added);
  t.last = now;
  return t;
}

/** How wet a mopped piece still is, from 1 (just mopped) to 0 (dry): it dries slowly at first, then faster. */
export function wetness(age, dryMs) {
  const s = Math.min(1, Math.max(0, age / dryMs));
  return 1 - s * s;
}

export const vacuums = (mode) => mode === 'vacuum' || mode === 'both';
export const mops = (mode) => mode === 'mop' || mode === 'both';
