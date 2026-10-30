// A simulated robot for the demo: it leaves the dock, cleans rooms in lanes 30 cm apart, finds its way through doors
// on the map's grid and goes back. It sends the same map messages the integration sends.

const WALL = 255;
const LANE = 6;            // cells between lanes (50 mm cells: 30 cm)
const CLEARANCE = 4;       // cells kept from walls and furniture (20 cm: the robot's radius and a little)
const SPEED = 300;         // mm/s, about a real robot's
const TRAIL_STEP = 80;     // mm between path points
const WASH_EVERY = 40000;  // mm mopped between trips to the dock to wash the mops, as the X10+ does
const WASH_FOR = 20;       // s washing them

/** A robot map from a Tasshack map dump (the JSON its camera keeps): the integration's message without the robot. */
export function fromDump(d) {
  const [left, top, , , width, height, size] = d.size;
  const rooms = {};
  for (const s of d.segments ?? []) {
    let name = null;
    try { name = s[4] ? new TextDecoder().decode(Uint8Array.from(atob(s[4]), (c) => c.charCodeAt(0))).trim() : null; } catch { /* unnamed */ }
    rooms[s[0]] = { name, x: s[1], y: s[2] };
  }
  // Tasshack keeps furniture as [centre x, centre y, width, height, type, ?, angle, scale].
  const TYPES = { 1: 'single_bed', 2: 'double_bed', 3: 'armchair', 4: 'two_seat_sofa', 5: 'three_seat_sofa', 6: 'dining_table', 8: 'coffee_table', 9: 'toilet' };
  const furniture = (d.furnitures ?? []).filter((f) => TYPES[f[4]]).map((f) => ({
    type: TYPES[f[4]], x0: f[0] - f[2] / 2, y0: f[1] - f[3] / 2, width: f[2], height: f[3], angle: f[6] ?? 0,
  }));
  return { map_id: d.map_id, frame_id: d.frame_id, grid: { left, top, size, width, height }, cells: d.data, rooms, dock: d.charger_position, furniture };
}

const SEES = 1200;   // mm: how close the robot gets before it reports something on the floor

export class Sim {
  /** house: the demo house's config, with the things on its floor (see houses/<name>/config.json). */
  constructor(map, house = {}) {
    this.map = map;
    this.house = house;
    const { width, height, left, top, size } = map.grid;
    this.g = { width, height, left, top, size };
    this.id = new Uint8Array(width * height);
    for (const [id, runs] of Object.entries(map.cells)) {
      for (let i = 0; i + 2 < runs.length; i += 3) this.id.fill(Number(id), runs[i + 1] * width + runs[i], runs[i + 1] * width + runs[i] + runs[i + 2]);
    }
    this.hidden = this.plantThings();   // things on the floor, reported once the robot comes close
    // The new box stands on the floor like a wall: the robot goes round it (from the start: it is a simulation).
    for (const t of this.hidden) for (let i = 0; t.cells && i + 2 < t.cells.length; i += 3) {
      this.id.fill(WALL, t.cells[i + 1] * width + t.cells[i], t.cells[i + 1] * width + t.cells[i] + t.cells[i + 2]);
    }
    // Cells the robot's centre may use: a room's floor at least CLEARANCE cells from a wall or the unknown.
    this.free = new Uint8Array(width * height);
    for (let y = 0; y < height; y++) {
      for (let x = 0; x < width; x++) {
        const v = this.id[y * width + x];
        if (!v || v === WALL) continue;
        let ok = true;
        for (let dy = -CLEARANCE; ok && dy <= CLEARANCE; dy++) {
          for (let dx = -CLEARANCE; dx <= CLEARANCE; dx++) {
            const xx = x + dx, yy = y + dy;
            const w = xx < 0 || yy < 0 || xx >= width || yy >= height ? 0 : this.id[yy * width + xx];
            if (!w || w === WALL) { ok = false; break; }
          }
        }
        this.free[y * width + x] = ok ? 1 : 0;
      }
    }
    this.pose = [...map.dock];
    this.path = [];
    this.found = [];
    this.newCells = [];
    this.route = [];
    this.active = [];
    this.zones = [];          // what a zone cleaning was sent to, in mm
    this.state = 'docked';    // as Home Assistant's vacuum entity has it
    this.error = null;        // what stopped it, as the integration's error sensor has it
    this.held = null;         // the state it had before a pause or an error
    this.frame = map.frame_id ?? 0;
  }

  /** The house's things on the floor, placed from a room's centre; and a box nobody had put there when it was mapped. */
  plantThings() {
    const at = ({ room, at: [dx, dy] }) => {
      const r = this.map.rooms[room];
      return r ? [r.x + dx, r.y + dy] : null;
    };
    const things = (this.house.things ?? []).filter((t) => at(t)).map((t) => {
      const [x, y] = at(t);
      return { x, y, type: t.type, confidence: 96, source: 'camera' };
    });
    // 30 by 20 cm, which the lidar outlines.
    const box = this.house.new && at(this.house.new);
    if (box) {
      const [cx, cy] = this.toCell(box);
      things.push({ cells: [cx, cy, 6, cx, cy + 1, 6, cx, cy + 2, 6, cx, cy + 3, 6], x: box[0], y: box[1] });
    }
    return things;
  }

  toCell([x, y]) { return [Math.floor((x - this.g.left) / this.g.size), Math.floor((y - this.g.top) / this.g.size)]; }
  toMm([cx, cy]) { return [this.g.left + (cx + 0.5) * this.g.size, this.g.top + (cy + 0.5) * this.g.size]; }

  /** Nearest free cell to a cell, by a breadth-first search over everything. */
  nearestFree([cx, cy]) {
    const { width, height } = this.g;
    const seen = new Uint8Array(width * height), q = [[cx, cy]];
    seen[cy * width + cx] = 1;
    for (let i = 0; i < q.length; i++) {
      const [x, y] = q[i];
      if (this.free[y * width + x]) return [x, y];
      for (const [dx, dy] of [[1, 0], [-1, 0], [0, 1], [0, -1]]) {
        const xx = x + dx, yy = y + dy;
        if (xx < 0 || yy < 0 || xx >= width || yy >= height || seen[yy * width + xx]) continue;
        seen[yy * width + xx] = 1;
        q.push([xx, yy]);
      }
    }
    return [cx, cy];
  }

  /** Cells from a to b through free cells (8-neighbour breadth-first search), corners only. */
  way(a, b) {
    const { width, height } = this.g;
    const start = this.nearestFree(a), goal = this.nearestFree(b);
    const prev = new Int32Array(width * height).fill(-1);
    const s = start[1] * width + start[0], t = goal[1] * width + goal[0];
    prev[s] = s;
    const q = [s];
    for (let i = 0; i < q.length && prev[t] < 0; i++) {
      const c = q[i], x = c % width, y = (c - x) / width;
      for (const [dx, dy] of [[1, 0], [-1, 0], [0, 1], [0, -1], [1, 1], [1, -1], [-1, 1], [-1, -1]]) {
        const xx = x + dx, yy = y + dy, n = yy * width + xx;
        if (xx < 0 || yy < 0 || xx >= width || yy >= height || prev[n] >= 0 || !this.free[n]) continue;
        prev[n] = c;
        q.push(n);
      }
    }
    if (prev[t] < 0) return [goal];
    const cells = [];
    for (let c = t; c !== s; c = prev[c]) cells.push([c % width, Math.floor(c / width)]);
    cells.push(start);
    cells.reverse();
    return cells.filter((p, i) => {    // keep the corners
      if (i === 0 || i === cells.length - 1) return true;
      const [a0, a1] = cells[i - 1], [c0, c1] = cells[i + 1];
      return p[0] - a0 !== c0 - p[0] || p[1] - a1 !== c1 - p[1];
    });
  }

  /** Lanes across a room, alternating direction, as cell end points. */
  lanes(room) {
    const { width, height } = this.g;
    // A room's id, or a zone [x0, y0, x1, y1] in cells across any room.
    const ok = Array.isArray(room)
      ? (x, y) => this.free[y * width + x] && x >= room[0] && x <= room[2] && y >= room[1] && y <= room[3]
      : (x, y) => this.free[y * width + x] && this.id[y * width + x] === room;
    const out = [];
    let flip = false;
    for (let y = Array.isArray(room) ? Math.max(0, room[1]) : 0; y < height; y += LANE) {
      let x = 0;
      while (x < width) {
        while (x < width && !ok(x, y)) x++;
        const x0 = x;
        while (x < width && ok(x, y)) x++;
        if (x - x0 >= 4) out.push(flip ? [[x - 1, y], [x0, y]] : [[x0, y], [x - 1, y]]);
      }
      flip = !flip;
    }
    return out;
  }

  /** Clean a zone [x0, y0, x1, y1] (mm), then go back to the dock. */
  cleanZone([x0, y0, x1, y1]) {
    const [a, b] = [this.toCell([x0, y0]), this.toCell([x1, y1])];
    this.clean([[a[0], a[1], b[0], b[1]]]);
    this.active = [];
    this.zones = [[x0, y0, x1, y1]];
  }

  /** Clean these rooms (or zones, in cells), in order, then go back to the dock. modes: { room: vacuum | mop | both }, else this.mode, else both. */
  clean(rooms, modes = {}) {
    let at = this.toCell(this.pose);
    const route = [];
    let mode = 'both', mopped = 0;
    const dock = this.toCell(this.map.dock);
    for (const room of rooms) {
      mode = modes[room] ?? this.mode ?? 'both';
      for (const [a, b] of this.lanes(room)) {
        route.push(...this.way(at, a).map((c) => ({ c, mode })), { c: b, mode });
        at = b;
        if (mode === 'vacuum') continue;
        mopped += Math.abs(b[0] - a[0]) * this.g.size;
        if (mopped < WASH_EVERY) continue;
        // To the dock to wash the mops, and back to where it was.
        mopped = 0;
        route.push(...this.way(at, dock).map((c) => ({ c, mode, wash: 'to' })), { c: dock, mode, wash: 'at', wait: WASH_FOR },
          ...this.way(dock, at).map((c) => ({ c, mode })));
      }
    }
    route.push(...this.way(at, this.toCell(this.map.dock)).map((c) => ({ c, mode, home: true })));
    this.route = route.map((r) => ({ ...r, mm: this.toMm(r.c) }));
    this.route.push({ mm: this.map.dock.slice(0, 2), mode, home: true });
    this.active = rooms;
    this.zones = [];
    this.path = [];
    this.found = [];
    this.newCells = [];
    this.hidden = this.plantThings();
    this.state = 'cleaning';
    this.error = this.held = null;
  }

  /** Home Assistant's vacuum services: pause, start (resume, or clean everything from the dock) and back to the dock. */
  pause() {
    if (!['cleaning', 'returning'].includes(this.state)) return;
    this.held = this.state;
    this.state = 'paused';
  }

  start(everything) {
    if (this.held) [this.state, this.held, this.error] = [this.held, null, null];
    else if (this.state === 'docked') everything();
  }

  goHome() {
    if (this.state === 'docked') return;
    const mode = this.path.at(-1)?.mode ?? 'vacuum';
    this.route = [...this.way(this.toCell(this.pose), this.toCell(this.map.dock)).map((c) => ({ c, mode, home: true, mm: this.toMm(c) })),
      { mm: this.map.dock.slice(0, 2), mode, home: true }];
    this.state = 'returning';
    this.error = this.held = null;
  }

  /** Stuck where it is, as when a wheel catches on something: it waits for start. Like the X10+, the vacuum then
   * says "paused" and only the error sensor says why. */
  stuck() {
    if (!['cleaning', 'returning'].includes(this.state)) return;
    this.held = this.state;
    this.state = 'paused';
    this.error = 'robot_stuck';
  }

  /** What the robot itself says it does, as the integration's state sensor has it. */
  get robotState() {
    const next = this.route[0];
    if (this.state === 'cleaning' && next?.wash === 'at') return 'washing';
    if (this.state === 'returning' && next?.wash === 'to') return 'returning_to_wash';
    const mode = this.path.at(-1)?.mode;
    return { docked: 'charging_completed', paused: 'paused', returning: 'returning' }[this.state]
      ?? { vacuum: 'sweeping', mop: 'mopping' }[mode] ?? 'sweeping_and_mopping';
  }

  /** Advance `seconds` of robot time. */
  step(seconds) {
    if (this.state === 'paused') return;
    let budget = SPEED * seconds;
    while (budget > 0 && this.route.length) {
      const next = this.route[0];
      if (next.wait) {   // washing the mops at the dock
        this.state = 'cleaning';
        const t = budget / SPEED;
        if (t < next.wait) { next.wait -= t; break; }
        budget -= next.wait * SPEED;
        this.route.shift();
        continue;
      }
      const [x, y] = this.pose, [tx, ty] = next.mm;
      const d = Math.hypot(tx - x, ty - y);
      if (d > 1) this.pose[2] = Math.round((Math.atan2(ty - y, tx - x) * 180) / Math.PI);
      // Heading to the dock (to finish, or to wash the mops) is "returning"; washing them there is still cleaning.
      this.state = next.home || next.wash === 'to' ? 'returning' : 'cleaning';
      if (d <= budget) {
        this.pose[0] = tx; this.pose[1] = ty;
        budget -= d;
        this.route.shift();
      } else {
        this.pose[0] = x + ((tx - x) * budget) / d;
        this.pose[1] = y + ((ty - y) * budget) / d;
        budget = 0;
      }
      // The path is strokes, one per mode: a new one starts where the robot changes what it does.
      const point = [Math.round(this.pose[0]), Math.round(this.pose[1])];
      let stroke = this.path[this.path.length - 1];
      if (!stroke || stroke.mode !== next.mode) {
        stroke = { mode: next.mode, points: [stroke ? stroke.points[stroke.points.length - 1] : [x, y].map(Math.round)] };
        this.path.push(stroke);
      }
      const last = stroke.points[stroke.points.length - 1];
      if (Math.hypot(point[0] - last[0], point[1] - last[1]) >= TRAIL_STEP) stroke.points.push(point);
    }
    for (const t of this.hidden.filter((h) => Math.hypot(h.x - this.pose[0], h.y - this.pose[1]) < SEES)) {
      this.hidden.splice(this.hidden.indexOf(t), 1);
      if (t.cells) this.newCells.push(...t.cells);
      else this.found.push(t);
    }
    if (!this.route.length && this.state !== 'docked') {
      this.pose = [...this.map.dock];
      this.state = 'docked';
      this.active = [];
      this.zones = [];
    }
  }

  /**
   * The integration's message. With delta, the path comes as its new strokes since the last message (path_tail, a
   * stroke with joins continuing the last one), as the integration sends them while the robot cleans.
   */
  message({ delta = false } = {}) {
    const [cx, cy] = this.toCell(this.pose);
    const id = this.id[cy * this.g.width + cx];
    this.frame += 1;
    const message = {
      ...this.map,
      frame_id: this.frame,
      robot: this.pose.map(Math.round),
      path: this.path.map((st) => ({ mode: st.mode, points: st.points.slice() })),
      room: this.map.rooms[id] ? id : null,
      active: this.active.slice(),
      zones: this.zones.slice(),
      obstacles: this.found.slice(),
      new_cells: this.newCells.slice(),
    };
    const sent = this.sent;
    this.sent = { path: this.path, strokes: this.path.length, points: this.path.at(-1)?.points.length ?? 0 };
    if (!delta || sent?.path !== this.path) return message;
    const last = this.path[sent.strokes - 1];
    const tail = [
      ...(last && last.points.length > sent.points ? [{ mode: last.mode, points: last.points.slice(sent.points), joins: true }] : []),
      ...this.path.slice(sent.strokes).map((s) => ({ mode: s.mode, points: s.points.slice() })),
    ];
    delete message.path;
    return tail.length ? { ...message, path_tail: tail } : message;
  }
}
