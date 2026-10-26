// A 3D model of the house (glTF/GLB) with the robot drawn on it. The config says where the dock is in the model;
// with the dock's pose in the robot's map, that one point and heading turn every map coordinate into the model's.

const UNITS = { m: 1, cm: 0.01, mm: 0.001, in: 0.0254, ft: 0.3048 };
export const MODEL_STYLES = ['textured', 'futuristic'];

const rad = (deg) => (deg * Math.PI) / 180;

function num(value, fallback, name) {
  if (value === undefined || value === null || value === '') return fallback;
  const n = Number(value);
  if (!Number.isFinite(n)) throw new Error(`"${name}" must be a number`);
  return n;
}

/**
 * model: { url, units, up, opacity, style, color, dock: { position: [x, y, z], heading } }
 * Positions are in the model's own frame after `units` and `up` are applied: metres, y up. dock.position is the point
 * on the floor under the docked robot's centre; its y is the floor's height. heading: where the docked robot faces,
 * in degrees around the vertical: 0 = the model's +z, 90 = +x.
 */
export function normalizeModel(m) {
  if (!m) return null;
  if (typeof m !== 'object') throw new Error('"model" must be an object');
  if (!m.url || typeof m.url !== 'string') throw new Error('"model.url" must be the URL of a .glb or .gltf file');
  const units = m.units ?? 'm';
  if (!(units in UNITS)) throw new Error(`"model.units" must be one of: ${Object.keys(UNITS).join(', ')}`);
  const up = m.up ?? 'y';
  if (up !== 'y' && up !== 'z') throw new Error('"model.up" must be y or z');
  const p = Array.isArray(m.dock?.position) ? m.dock.position.map(Number) : [];
  if (p.length !== 3 || !p.every(Number.isFinite)) {
    throw new Error('"model.dock.position" must be where the dock is in the model, like [1.2, 0, 4.5]');
  }
  const opacity = num(m.opacity, 1, 'model.opacity');
  if (opacity <= 0 || opacity > 1) throw new Error('"model.opacity" must be between 0 and 1');
  const style = m.style ?? 'textured';
  if (!MODEL_STYLES.includes(style)) throw new Error(`"model.style" must be one of: ${MODEL_STYLES.join(', ')}`);
  return {
    url: m.url,
    scale: UNITS[units],
    upRotX: up === 'z' ? -Math.PI / 2 : 0,
    opacity,
    style,
    color: m.color ? String(m.color) : null,
    dock: { position: p, heading: num(m.dock.heading, 0, 'model.dock.heading') },
  };
}

// The map's heading for an angle: x → +X and y → -Z, so the plan isn't mirrored (y is to the left of x seen from above).
const mapHeading = (angle) => Math.atan2(Math.cos(rad(angle)), -Math.sin(rad(angle)));

/**
 * Robot map → model. The map is in mm, its angles in degrees counterclockwise from +x. `dock` is the dock's pose in the
 * map, [x, y, angle]. Without a model the scene is the map itself in metres. Returns:
 *   point(x, y) → [X, Z]; toMap(X, Z) → [x, y]; heading(angle) → radians around +Y (0 = +Z); floor: the floor's Y.
 */
export function robotToModel(dock, model) {
  const [cx, cy, ca] = dock;
  const [px, floor, pz] = model ? model.dock.position : [cx / 1000, 0, -cy / 1000];
  const d = model ? rad(model.dock.heading) - mapHeading(ca) : 0;
  const c = Math.cos(d), s = Math.sin(d);
  return {
    floor,
    point(x, y) {
      const dx = (x - cx) / 1000, dz = -(y - cy) / 1000;
      return [px + dx * c + dz * s, pz - dx * s + dz * c];
    },
    toMap(X, Z) {
      const u = X - px, w = Z - pz;
      return [cx + 1000 * (u * c - w * s), cy - 1000 * (u * s + w * c)];
    },
    heading: (angle) => mapHeading(angle) + d,
  };
}

/** Cover a room's run-length cells ([x, y, n, …]) with rectangles [x0, y0, x1, y1] (cells, end exclusive). */
export function cellRects(runs) {
  const rows = new Map();
  for (let i = 0; i + 2 < runs.length; i += 3) {
    const [x, y, n] = [runs[i], runs[i + 1], runs[i + 2]];
    if (!rows.has(y)) rows.set(y, []);
    rows.get(y).push([x, x + n]);
  }
  const out = [];
  let open = new Map();     // "x0,x1" → first row of a rectangle still growing downwards
  let prevY = null;
  const ys = [...rows.keys()].sort((a, b) => a - b);
  const close = (keep, y) => {
    for (const [key, y0] of open) {
      if (!keep.has(key)) {
        const [x0, x1] = key.split(',').map(Number);
        out.push([x0, y0, x1, y]);
        open.delete(key);
      }
    }
  };
  for (const y of ys) {
    if (prevY !== null && y !== prevY + 1) close(new Set(), prevY + 1);
    const keys = new Set(rows.get(y).map(([a, b]) => `${a},${b}`));
    close(keys, y);
    for (const key of keys) if (!open.has(key)) open.set(key, y);
    prevY = y;
  }
  if (prevY !== null) close(new Set(), prevY + 1);
  return out;
}

/** Which room a map point is in: the cell's id from the run-length cells, or null. */
export function roomAt(map, x, y) {
  const { left, top, size } = map.grid;
  const cx = Math.floor((x - left) / size), cy = Math.floor((y - top) / size);
  for (const [id, runs] of Object.entries(map.cells)) {
    if (!map.rooms[id]) continue;
    for (let i = 0; i + 2 < runs.length; i += 3) {
      if (runs[i + 1] === cy && cx >= runs[i] && cx < runs[i] + runs[i + 2]) return Number(id);
    }
  }
  return null;
}

/** A routine's rooms as segment ids, null where the map has no such room: numbers are ids, text is a room's name. */
export function routineRooms(rooms, wanted) {
  const all = Object.entries(rooms ?? {});
  return wanted.map((x) => {
    const hit = typeof x === 'number'
      ? all.find(([id]) => Number(id) === x)
      : all.find(([, room]) => room.name?.trim().toLowerCase() === x.trim().toLowerCase());
    return hit ? Number(hit[0]) : null;
  });
}

/**
 * The point a fraction s (0 to 1) of the way along a polyline [[x, z], …], and the way it goes there (radians around
 * +Y, 0 = +z), or null on a polyline with no length.
 */
export function along(points, s) {
  const lengths = points.slice(1).map(([x, z], i) => Math.hypot(x - points[i][0], z - points[i][1]));
  let d = Math.min(1, Math.max(0, s)) * lengths.reduce((a, b) => a + b, 0);
  for (let i = 0; i < lengths.length; i++) {
    if (!lengths[i]) continue;
    const [ax, az] = points[i], [bx, bz] = points[i + 1];
    if (d <= lengths[i] || i === lengths.length - 1) {
      const k = Math.min(1, d / lengths[i]);
      return { x: ax + (bx - ax) * k, z: az + (bz - az) * k, dir: Math.atan2(bx - ax, bz - az) };
    }
    d -= lengths[i];
  }
  const [x, z] = points.at(-1);
  return { x, z, dir: null };
}

/**
 * The path with a message's new strokes added (the integration sends only those while the robot cleans): a stroke
 * with `joins` continues the last one. A new array; the strokes it doesn't touch are the old ones.
 */
export function appendPath(path, tail) {
  const out = [...(path ?? [])];
  for (const { joins, ...stroke } of tail) {
    const last = out.at(-1);
    if (joins && last) out[out.length - 1] = { ...last, points: [...last.points, ...stroke.points] };
    else out.push(stroke);
  }
  return out;
}
