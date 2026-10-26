// Build the demo's houses, so the demo and the site never show a real home. Each folder gets map.json (a robot map as
// Tasshack's integration dumps it, which the demo reads) and a model:
//   houses/living-room: the mmWave 3D Card's example living room (see its CREDITS.md), plus a dock station, and the
//     map a robot would make of it: one room, with the hearth, the sofa, the coffee table and the bookcase as obstacles;
//   houses/flat: a made-up flat of six rooms, its model drawn from the map (floors, walls, furniture and the station).
// Usage, from card/: node demo/make-houses.mjs. It only adds the station to living-room.glb once.
import { readFileSync, writeFileSync } from 'node:fs';

const SIZE = 50;   // mm per cell
const WALL = 255;
const STATION = [0.34, 0.45, 0.25];   // m: width, height, depth of an X10+'s self-empty station
// Furniture codes as the demo's fromDump reads them.
const CODES = { single_bed: 1, double_bed: 2, armchair: 3, three_seat_sofa: 5, dining_table: 6, coffee_table: 8, toilet: 9 };

/** A grid of cells with the helpers both houses use. Cell (i, j) is at map x = left + i * SIZE, y = top + j * SIZE. */
class Grid {
  constructor(w, h, left, top) {
    Object.assign(this, { w, h, left, top, v: new Uint8Array(w * h) });
  }
  fill([x0, y0, x1, y1], v) { for (let y = y0; y < y1; y++) this.v.fill(v, y * this.w + x0, y * this.w + x1); }
  /** The map's run-length cells: { value: [x, y, n, …] }. */
  runs() {
    const data = {};
    for (let y = 0; y < this.h; y++) {
      for (let x = 0; x < this.w;) {
        const v = this.v[y * this.w + x];
        let n = 1;
        while (x + n < this.w && this.v[y * this.w + x + n] === v) n++;
        if (v) (data[v] ??= []).push(x, y, n);
        x += n;
      }
    }
    return data;
  }
  /** Cover the cells holding v with rectangles [x0, y0, x1, y1): runs in a row merged downwards. */
  rects(v) {
    const out = [], open = new Map();
    for (let y = 0; y <= this.h; y++) {
      const runs = new Set();
      for (let x = 0; y < this.h && x < this.w;) {
        if (this.v[y * this.w + x] !== v) { x++; continue; }
        const x0 = x;
        while (x < this.w && this.v[y * this.w + x] === v) x++;
        runs.add(`${x0},${x}`);
      }
      for (const [run, y0] of open) {
        if (!runs.has(run)) { const [a, b] = run.split(',').map(Number); out.push([a, y0, b, y]); open.delete(run); }
      }
      for (const run of runs) if (!open.has(run)) open.set(run, y);
    }
    return out;
  }
}

const b64 = (s) => Buffer.from(s).toString('base64');

function mapJson(grid, rooms, dock, furniture) {
  return JSON.stringify({
    map_id: 1, frame_id: 1, size: [grid.left, grid.top, 0, 0, grid.w, grid.h, SIZE], data: grid.runs(),
    segments: rooms.map(({ id, name, x, y }) => [id, Math.round(x), Math.round(y), 0, b64(name)]),
    charger_position: dock,
    furnitures: furniture.map(({ type, x, y, w, d }) => [Math.round(x), Math.round(y), w, d, CODES[type], 0, 0, 1]),
  });
}

/** Triangles for a GLB: floors and boxes in the model's frame (metres, y up), one colour. */
class Part {
  constructor(name, rgb) { Object.assign(this, { name, rgb, pos: [], nrm: [], idx: [] }); }
  quad(corners, normal) {
    const base = this.pos.length / 3;
    this.pos.push(...corners.flat());
    for (let i = 0; i < 4; i++) this.nrm.push(...normal);
    this.idx.push(base, base + 1, base + 2, base, base + 2, base + 3);
  }
  floor([x0, z0, x1, z1]) { this.quad([[x0, 0, z0], [x0, 0, z1], [x1, 0, z1], [x1, 0, z0]], [0, 1, 0]); }
  box([x0, z0, x1, z1], h) {
    this.quad([[x0, h, z0], [x0, h, z1], [x1, h, z1], [x1, h, z0]], [0, 1, 0]);
    this.quad([[x0, 0, z1], [x1, 0, z1], [x1, h, z1], [x0, h, z1]], [0, 0, 1]);
    this.quad([[x1, 0, z0], [x0, 0, z0], [x0, h, z0], [x1, h, z0]], [0, 0, -1]);
    this.quad([[x1, 0, z1], [x1, 0, z0], [x1, h, z0], [x1, h, z1]], [1, 0, 0]);
    this.quad([[x0, 0, z0], [x0, 0, z1], [x0, h, z1], [x0, h, z0]], [-1, 0, 0]);
  }
}

/** The station, standing 0.3 m behind the docked robot at (x, z) facing `heading` (degrees, 0 = +z, 90 = +x). */
function station(x, z, heading) {
  const a = (heading * Math.PI) / 180, fx = Math.sin(a), fz = Math.cos(a);
  const [w, h, d] = STATION, [hw, hd] = Math.abs(fz) > Math.abs(fx) ? [w / 2, d / 2] : [d / 2, w / 2];
  const cx = x - 0.3 * fx, cz = z - 0.3 * fz;
  const p = new Part('dock', [0.2, 0.2, 0.22]);
  p.box([cx - hw, cz - hd, cx + hw, cz + hd], h);
  return p;
}

// ---------- GLB ----------

function readGlb(file) {
  const buf = readFileSync(file);
  const jsonLength = buf.readUInt32LE(12);
  const gltf = JSON.parse(buf.subarray(20, 20 + jsonLength).toString());
  const binStart = 20 + jsonLength + 8;
  return { gltf, bin: buf.subarray(binStart, binStart + buf.readUInt32LE(20 + jsonLength)) };
}

/** Add parts to a glTF (meshes, materials and nodes in its first scene) and its binary buffer; returns the GLB. */
function writeGlb({ gltf, bin }, parts) {
  for (const k of ['nodes', 'meshes', 'materials', 'accessors', 'bufferViews']) gltf[k] ??= [];
  const chunks = [bin, Buffer.alloc(-bin.length & 3)];
  let length = bin.length + (-bin.length & 3);
  const accessor = (array, type, target) => {
    const bytes = Buffer.from(array.buffer);
    gltf.bufferViews.push({ buffer: 0, byteOffset: length, byteLength: bytes.length, target });
    chunks.push(bytes);
    length += bytes.length;   // 4-byte items: every view stays aligned
    const acc = { bufferView: gltf.bufferViews.length - 1, count: type === 'VEC3' ? array.length / 3 : array.length,
      componentType: array instanceof Float32Array ? 5126 : 5125, type };
    if (type === 'VEC3') {
      acc.min = [0, 1, 2].map((k) => Math.min(...array.filter((_, i) => i % 3 === k)));
      acc.max = [0, 1, 2].map((k) => Math.max(...array.filter((_, i) => i % 3 === k)));
    }
    gltf.accessors.push(acc);
    return gltf.accessors.length - 1;
  };
  for (const p of parts) {
    const attributes = { POSITION: accessor(new Float32Array(p.pos), 'VEC3', 34962), NORMAL: accessor(new Float32Array(p.nrm), 'VEC3', 34962) };
    const indices = accessor(new Uint32Array(p.idx), 'SCALAR', 34963);
    gltf.materials.push({ name: p.name, pbrMetallicRoughness: { baseColorFactor: [...p.rgb, 1], metallicFactor: 0, roughnessFactor: 0.9 } });
    gltf.meshes.push({ name: p.name, primitives: [{ attributes, indices, material: gltf.materials.length - 1 }] });
    gltf.nodes.push({ name: p.name, mesh: gltf.meshes.length - 1 });
    gltf.scenes[0].nodes.push(gltf.nodes.length - 1);
  }
  gltf.buffers = [{ byteLength: length }];
  let json = Buffer.from(JSON.stringify(gltf));
  json = Buffer.concat([json, Buffer.alloc(-json.length & 3, 0x20)]);
  const body = Buffer.concat(chunks);
  const head = Buffer.alloc(12);
  head.writeUInt32LE(0x46546c67, 0);
  head.writeUInt32LE(2, 4);
  head.writeUInt32LE(12 + 8 + json.length + 8 + body.length, 8);
  const chunk = (b, type) => { const h = Buffer.alloc(8); h.writeUInt32LE(b.length, 0); h.writeUInt32LE(type, 4); return [h, b]; };
  return Buffer.concat([head, ...chunk(json, 0x4e4f534a), ...chunk(body, 0x004e4942)]);
}

// ---------- the living room ----------
// Model frame: metres, floor at y = 0, the room from x 0 to 5.69 and z 0 to 5.85. The map is the model turned into mm
// with the dock at the origin: x = 1000 X - 4500, y = 5400 - 1000 Z (so map +y is the model's -z).

function livingRoom(dir) {
  const ROOM = [0, 0, 5.69, 5.85];
  const BLOCKED = [   // [x0, z0, x1, z1]: what stands on the floor, which the robot goes around
    [1.17, 0, 3.81, 0.82],     // hearth
    [0.76, 3.64, 3.7, 4.64],   // sofa (6 cm legs: too low to go under)
    [1.47, 2.11, 2.95, 3.11],  // coffee table
    [5.41, 1.65, 5.69, 3.27],  // bookcase
    [3.59, 1.44, 4.71, 2.56],  // armchair (its legs are tall enough to go under, but it reads as going through it)
  ];
  const DOCK = { x: 4.5, z: 5.4, heading: 180 };   // against the back wall, facing into the room
  const toMap = (X, Z) => [1000 * X - 4500, 5400 - 1000 * Z];
  const grid = new Grid(116, 119, -4550, -500);    // one cell of wall around the room
  for (let j = 0; j < grid.h; j++) {
    for (let i = 0; i < grid.w; i++) {
      const X = (grid.left + (i + 0.5) * SIZE + 4500) / 1000, Z = (5400 - grid.top - (j + 0.5) * SIZE) / 1000;
      const inside = ([x0, z0, x1, z1]) => X > x0 && X < x1 && Z > z0 && Z < z1;
      grid.v[j * grid.w + i] = !inside(ROOM) ? WALL : BLOCKED.some(inside) ? WALL : 1;
    }
  }
  const furniture = [
    { type: 'three_seat_sofa', at: [2.23, 4.14], w: 2940, d: 1000 },
    { type: 'coffee_table', at: [2.21, 2.61], w: 1480, d: 1000 },
    { type: 'armchair', at: [4.15, 2.0], w: 1120, d: 1120 },
  ].map(({ at, ...f }) => { const [x, y] = toMap(...at); return { ...f, x, y }; });
  const [rx, ry] = toMap(2.85, 2.2);
  writeFileSync(new URL('map.json', dir), mapJson(grid, [{ id: 1, name: 'Living room', x: rx, y: ry }], [...toMap(DOCK.x, DOCK.z), 90], furniture));

  const file = new URL('living-room.glb', dir);
  const glb = readGlb(file);
  if (!glb.gltf.nodes.some((n) => n.name === 'dock')) writeFileSync(file, writeGlb(glb, [station(DOCK.x, DOCK.z, DOCK.heading)]));
  return { dock: { position: [DOCK.x, 0, DOCK.z], heading: DOCK.heading } };
}

// ---------- the flat ----------
// Cells of 50 mm, 208 × 176 (10.4 × 8.8 m). Model frame: x = cell x, z = rows counted up from the bottom, in metres.

function flat(dir) {
  const W = 208, H = 176, T = 1;   // T: cells of wall on each room's edge, 10 cm between rooms
  const ROOMS = [   // cell rectangles [x0, y0, x1, y1)
    { id: 1, name: 'Living room', rect: [0, 94, 120, 176] },
    { id: 2, name: 'Kitchen', rect: [120, 94, 208, 176] },
    { id: 3, name: 'Hall', rect: [0, 70, 208, 94] },
    { id: 4, name: 'Bedroom', rect: [0, 0, 80, 70] },
    { id: 5, name: 'Bathroom', rect: [80, 0, 120, 70] },
    { id: 6, name: 'Study', rect: [120, 0, 208, 70] },
  ];
  const DOORS = [[40, 69, 56, 71], [92, 69, 108, 71], [150, 69, 166, 71], [180, 93, 196, 95], [20, 93, 60, 95], [119, 120, 121, 150]];
  const FURNITURE = [   // type, centre (cells), width and depth (mm), height (m)
    ['three_seat_sofa', 60, 150, 2100, 900, 0.8], ['coffee_table', 60, 128, 1000, 550, 0.45], ['dining_table', 160, 140, 1600, 900, 0.75],
    ['double_bed', 30, 30, 1600, 2000, 0.5], ['toilet', 112, 12, 400, 650, 0.4], ['single_bed', 190, 30, 900, 1900, 0.5],
  ];
  const DOCK = [10, 140, 0];   // cells and map angle: against the living room's left wall, facing +x
  const COLORS = [[0.55, 0.71, 0.89], [0.95, 0.73, 0.53], [0.62, 0.84, 0.62], [0.93, 0.6, 0.62], [0.78, 0.67, 0.89], [0.8, 0.72, 0.62]];

  const grid = new Grid(W, H, -800, -7000);   // the dock near the map's origin, as on a real robot's
  for (const r of ROOMS) grid.fill(r.rect, r.id);
  for (const [x0, y0, x1, y1] of ROOMS.map((r) => r.rect)) {
    grid.fill([x0, y0, x1, y0 + T], WALL);
    grid.fill([x0, y1 - T, x1, y1], WALL);
    grid.fill([x0, y0, x0 + T, y1], WALL);
    grid.fill([x1 - T, y0, x1, y1], WALL);
  }
  for (const [x0, y0, x1, y1] of DOORS) {   // a door's cells join the room below it (or right of it)
    const across = x1 - x0 < y1 - y0;
    for (let y = y0; y < y1; y++) for (let x = x0; x < x1; x++) grid.v[y * W + x] = across ? grid.v[y * W + x1 + 1] : grid.v[(y1 + 1) * W + x];
  }
  // The model's floors and walls; then the furniture, solid in the model, joins the map's walls: the robot goes round
  // it, as the lidar maps it.
  const floors = ROOMS.map(({ id }) => grid.rects(id)), wallRects = grid.rects(WALL);
  for (const [, cx, cy, w, d] of FURNITURE) {
    const hw = w / 2 / SIZE, hd = d / 2 / SIZE;
    grid.fill([Math.round(cx - hw), Math.round(cy - hd), Math.round(cx + hw), Math.round(cy + hd)], WALL);
  }
  const mm = (cx, cy) => [grid.left + cx * SIZE, grid.top + cy * SIZE];
  const rooms = ROOMS.map(({ id, name, rect: [x0, y0, x1, y1] }) => { const [x, y] = mm((x0 + x1) / 2, (y0 + y1) / 2); return { id, name, x, y }; });
  const furniture = FURNITURE.map(([type, cx, cy, w, d]) => { const [x, y] = mm(cx, cy); return { type, x, y, w, d }; });
  writeFileSync(new URL('map.json', dir), mapJson(grid, rooms, [...mm(DOCK[0], DOCK[1]), DOCK[2]], furniture));

  const m = SIZE / 1000, toModel = ([x0, y0, x1, y1]) => [x0 * m, (H - y1) * m, x1 * m, (H - y0) * m];
  const parts = ROOMS.map(({ name }, i) => {
    const p = new Part(name, COLORS[i % COLORS.length]);
    floors[i].forEach((r) => p.floor(toModel(r)));
    return p;
  });
  const walls = new Part('walls', [0.93, 0.93, 0.91]);
  wallRects.forEach((r) => walls.box(toModel(r), 1.0));   // 1 m: a doll's house the camera sees into
  const things = new Part('furniture', [0.62, 0.6, 0.58]);
  for (const [, cx, cy, w, d, h] of FURNITURE) {
    const x = cx * m, z = (H - cy) * m;
    things.box([x - w / 2000, z - d / 2000, x + w / 2000, z + d / 2000], h);
  }
  // Map +x is the model's +x (a heading of 90) and map +y its -z (180).
  const dock = { position: [DOCK[0] * m, 0, (H - DOCK[1]) * m], heading: 90 + DOCK[2] };
  const empty = { gltf: { asset: { version: '2.0', generator: 'dreame-vacuum-3d demo flat' }, scene: 0, scenes: [{ nodes: [] }] }, bin: Buffer.alloc(0) };
  writeFileSync(new URL('flat.glb', dir), writeGlb(empty, [...parts, walls, things, station(dock.position[0], dock.position[2], dock.heading)]));
  return { dock };
}

const houses = new URL('./houses/', import.meta.url);
console.log(JSON.stringify({
  'living-room': livingRoom(new URL('living-room/', houses)),
  flat: flat(new URL('flat/', houses)),
}));
