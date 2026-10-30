import {
  AdditiveBlending, BufferAttribute, BufferGeometry, CanvasTexture, Group, Mesh, MeshBasicMaterial, MeshStandardMaterial,
  PMREMGenerator, Points, PointsMaterial, SRGBColorSpace,
} from 'three';
import { RoomEnvironment } from 'three/addons/environments/RoomEnvironment.js';
import { cellRects } from './model.js';
import { extendTrail, mops, vacuums, wetness } from './trail.js';

// Floor effects along the robot's trail. Vacuuming: the rooms it is cleaning look dusty and its swath clears the dust,
// with a brief glint behind it, and dust specks fly into it. Mopping: the floor it passed is darker and glossy, and
// dries out over `dryMs`, from the edges in. Each effect is a canvas laid over the map's grid as a texture.

const PX = 2;                 // canvas pixels per map cell (2.5 cm with the usual 50 mm cells)
const MAX_PX = 2048;
const VACUUM_WIDTH = 280;     // mm swept by the brushes and the suction
const MOP_WIDTH = 340;        // mm under the mop pads
const GLINT_MS = 20000;       // how long the floor just vacuumed glints
const REDRAW_MS = 1000;       // how often drying is redrawn, at most
const TILE = 32;              // canvas px: drying redraws only the tiles where a piece came, went or dried a step
const LEVELS = 8;             // drying is drawn in this many steps, each one a single stroke (no darker overlaps)
const SPECKS = 48;

export class FloorEffects {
  constructor(renderer, robot) {
    this.renderer = renderer;
    this.group = new Group();
    this.robot = robot;
    this.dryMs = 10 * 60000;
    this.trail = null;
    this.layers = null;
    this.gridSig = '';
    this.dustSig = '';
    this.cleaning = false;
    this.mode = null;          // what the robot is doing now: vacuum, mop or both
    this.dustOpacity = 0;
    this.drawnAt = 0;
    this.full = true;          // draw the drying layers again whole, not only where they changed
    this.specks = makeSpecks();
    this.specks.visible = false;
    robot.add(this.specks);
    this.shown = { dust: true, wet: true };
    this.coverage = null;      // past cleanings' paths to show the coverage of, or null
  }

  /**
   * Where past cleanings went: each one's path drawn at the brushes' width, faint, so the floor more of them covered
   * shows stronger. paths: [[{ mode, points }, …], …] in map mm, or null to hide it.
   */
  setCoverage(paths) {
    this.coverage = paths;
    this._drawCoverage();
  }

  _drawCoverage() {
    if (!this.layers) return;
    const { ctx, canvas, tex, mesh } = this.layers.coverage;
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    mesh.visible = !!this.coverage?.length;
    if (mesh.visible) {
      ctx.globalAlpha = 0.22;
      ctx.strokeStyle = this.theme.accent;
      for (const path of this.coverage) {
        this._stroke(ctx, path.flatMap(({ points = [] }) => points.slice(1).map((b, i) => ({ a: points[i], b }))), VACUUM_WIDTH);
      }
      ctx.globalAlpha = 1;
    }
    tex.needsUpdate = true;
  }

  /** Which effects show: the vacuumed floor (dust cleared, glint, specks) and the mopped one (wet, drying). */
  setShown({ dust = true, wet = true }) {
    this.shown = { dust, wet };
    this._applyShown();
  }

  _applyShown() {
    if (!this.layers) return;
    this.layers.dust.mesh.visible = this.layers.glint.mesh.visible = this.shown.dust;
    this.layers.wet.mesh.visible = this.shown.wet;
  }

  setTheme(theme) {
    this.theme = theme;
    this.specks.material.color.set(theme.dark ? '#d8cbb4' : '#6b5a46');
    if (this.layers) this.dustPattern = dustPattern(this.layers.dust.ctx, theme.dark);
    this.dustSig = '';
    this.drawnAt = 0;
    this.full = true;
    this._drawCoverage();
  }

  setDryTime(minutes) {
    this.dryMs = Math.max(0.1, minutes) * 60000;
    this.drawnAt = 0;
    this.full = true;
  }

  setCleaning(on) {
    if (on === this.cleaning) return;
    this.cleaning = on;
    if (this.map) this._updateDust();
  }

  /** map and xf as in VacuumScene.setMap; called with each map message. */
  setMap(map, xf) {
    const g = map.grid;
    if (!g || !this.theme) return;
    // Where the map's origin lands and which way it turns: the map's dock and the model's (a new model, or calibrated again).
    const sig = `${g.left},${g.top},${g.width},${g.height},${g.size}|${xf.point(0, 0)}|${xf.heading(0)}|${xf.floor}`;
    if (sig !== this.gridSig) {
      this.gridSig = sig;
      this._build(g, xf);
    }
    this.map = map;
    // Not cleaning, a path already there is a finished cleaning's: it dried long ago.
    this.trail = extendTrail(this.trail, map.path, performance.now(), this.cleaning);
    const last = this.trail.pieces[this.trail.pieces.length - 1];
    this.mode = last?.mode ?? null;
    this._updateDust();
    this.drawnAt = 0;
  }

  /** Dust on the rooms being cleaned (every room when the task names none), cleared where the robot vacuumed. */
  _updateDust() {
    const map = this.map;
    const rooms = this.cleaning ? (map.active?.length ? map.active.map(String) : Object.keys(map.rooms ?? {})) : [];
    this.dustRooms = rooms.filter((id) => map.cells?.[id]);
    const sig = this.dustRooms.join(), n = this.trail.pieces.length;
    if (sig !== this.dustSig || this.trail !== this.dustTrail) {
      this.dustSig = sig;
      this._drawDust(map.cells);
    } else if (n > this.dustDrawn && this.dustRooms.length) {
      this._clearDust(this.trail.pieces.slice(this.dustDrawn));   // the trail grew: clear only its new pieces
    }
    this.dustTrail = this.trail;
    this.dustDrawn = n;
  }

  _build(g, xf) {
    this.dispose(false);
    const w = Math.min(MAX_PX, g.width * PX), h = Math.min(MAX_PX, g.height * PX);
    this.scale = Math.min(w / (g.width * g.size), h / (g.height * g.size));   // canvas px per mm
    this.grid = g;
    const right = g.left + g.width * g.size, bottom = g.top + g.height * g.size;
    const corners = [[g.left, g.top], [right, g.top], [right, bottom], [g.left, bottom]].map(([x, y]) => xf.point(x, y));
    const layer = (lift, material) => {
      const canvas = document.createElement('canvas');
      canvas.width = w;
      canvas.height = h;
      const tex = new CanvasTexture(canvas);
      tex.flipY = false;
      tex.colorSpace = SRGBColorSpace;
      material.map = tex;
      const geo = new BufferGeometry();
      geo.setAttribute('position', new BufferAttribute(new Float32Array(corners.flatMap(([X, Z]) => [X, xf.floor + lift, Z])), 3));
      geo.setAttribute('normal', new BufferAttribute(new Float32Array([0, 1, 0, 0, 1, 0, 0, 1, 0, 0, 1, 0]), 3));
      geo.setAttribute('uv', new BufferAttribute(new Float32Array([0, 0, 1, 0, 1, 1, 0, 1]), 2));
      geo.setIndex([0, 1, 2, 0, 2, 3]);   // facing up: the map → model turn is a mirror
      const mesh = new Mesh(geo, material);
      mesh.renderOrder = 1;
      this.group.add(mesh);
      return { canvas, ctx: canvas.getContext('2d'), tex, mesh };
    };
    this.env ??= makeEnv(this.renderer);
    const flat = { transparent: true, depthWrite: false, polygonOffset: true, polygonOffsetFactor: -2 };
    // Above a rug's top (about 12 mm in the demo's room), below the room overlay (LIFT in scene.js): a layer inside the rug
    // flickers as the camera turns.
    this.layers = {
      coverage: layer(0.013, new MeshBasicMaterial({ ...flat })),
      // Glossier than water on a floor really is, so the sheen also shows when seen from above.
      wet: layer(0.015, new MeshStandardMaterial({ ...flat, roughness: 0.1, metalness: 0.35, envMap: this.env, envMapIntensity: 1.6 })),
      dust: layer(0.017, new MeshBasicMaterial({ ...flat, opacity: 0 })),
      glint: layer(0.019, new MeshBasicMaterial({ ...flat, blending: AdditiveBlending })),
    };
    this.dustPattern = dustPattern(this.layers.dust.ctx, this.theme.dark);
    this.dustSig = '';
    this._applyShown();
    this._drawCoverage();
  }

  _px([x, y]) {
    return [(x - this.grid.left) * this.scale, (y - this.grid.top) * this.scale];
  }

  /** One stroke through these pieces, so where they overlap it isn't drawn twice. */
  _stroke(ctx, pieces, width) {
    if (!pieces.length) return;
    ctx.lineWidth = width * this.scale;
    ctx.beginPath();
    for (const p of pieces) {
      ctx.moveTo(...this._px(p.a));
      ctx.lineTo(...this._px(p.b));
    }
    ctx.stroke();
  }

  _drawDust(cells) {
    const { ctx, canvas, tex } = this.layers.dust;
    if (!this.dustRooms.length) return;   // keep the last drawing while it fades out
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    const k = this.grid.size * this.scale;
    ctx.fillStyle = this.dustPattern;
    for (const id of this.dustRooms) for (const [x0, y0, x1, y1] of cellRects(cells[id])) ctx.fillRect(x0 * k, y0 * k, (x1 - x0) * k, (y1 - y0) * k);
    tex.needsUpdate = true;
    this._clearDust(this.trail.pieces);
  }

  /** Clear the dust where these pieces vacuumed. */
  _clearDust(pieces) {
    const { ctx, tex } = this.layers.dust;
    const swept = pieces.filter((p) => vacuums(p.mode));
    if (!swept.length) return;
    ctx.globalCompositeOperation = 'destination-out';
    ctx.lineCap = ctx.lineJoin = 'round';
    ctx.strokeStyle = '#000';
    this._stroke(ctx, swept, VACUUM_WIDTH);
    ctx.globalCompositeOperation = 'source-over';
    tex.needsUpdate = true;
  }

  /** After the WebGL context was lost: the wet floor's reflections were drawn on the GPU, so they are made again. */
  restore() {
    if (!this.env) return;
    this.env.dispose();
    this.env = makeEnv(this.renderer);
    if (this.layers) this.layers.wet.mesh.material.envMap = this.env;
  }

  /**
   * Redraw what changes with time: the glint fading and the floor drying, each in a few steps. Only the tiles where a
   * piece came, went or dried a step are drawn again (a long trail is slow to draw whole, with the blur, in software).
   * Returns whether anything is still changing (busy) and whether it drew anything (changed).
   */
  _drawDrying(now) {
    const { wet, glint } = this.layers;
    const pieces = this.trail?.pieces ?? [];
    let busy = false, changed = false;
    for (const [layer, keep, life, width, paint] of [
      [glint, vacuums, GLINT_MS, VACUUM_WIDTH, (ctx, s) => { ctx.strokeStyle = `rgba(255,255,255,${0.14 * s})`; return 1; }],
      [wet, mops, this.dryMs, MOP_WIDTH, (ctx, s) => {
        ctx.strokeStyle = this.theme.dark ? `rgba(6,14,24,${0.7 * s})` : `rgba(28,52,78,${0.58 * s})`;
        return 0.55 + 0.45 * s;   // a drying patch shrinks from its edges
      }],
    ]) {
      const { ctx, canvas, tex } = layer;
      const levels = new Map();   // piece → its drying step
      for (const p of pieces) {
        if (!keep(p.mode) || now - p.t >= life) continue;
        const s = layer === wet ? wetness(now - p.t, life) : 1 - (now - p.t) / life;
        if (s > 0) levels.set(p, Math.min(LEVELS - 1, Math.floor(s * LEVELS)));
      }
      busy ||= levels.size > 0;
      const cols = Math.ceil(canvas.width / TILE), rows = Math.ceil(canvas.height / TILE);
      const pad = (width * this.scale) / 2 + 6;   // half the stroke, and the blur
      // The tiles a piece's stroke reaches, until `each` returns true.
      const tiles = (p, each) => {
        const [ax, ay] = this._px(p.a), [bx, by] = this._px(p.b);
        const x0 = Math.max(0, Math.floor((Math.min(ax, bx) - pad) / TILE)), x1 = Math.min(cols - 1, Math.floor((Math.max(ax, bx) + pad) / TILE));
        const y0 = Math.max(0, Math.floor((Math.min(ay, by) - pad) / TILE)), y1 = Math.min(rows - 1, Math.floor((Math.max(ay, by) + pad) / TILE));
        for (let y = y0; y <= y1; y++) for (let x = x0; x <= x1; x++) if (each(y * cols + x)) return true;
        return false;
      };
      let dirty = null;   // the tiles to draw again; null: all of them
      if (layer.levels && !this.full) {
        dirty = new Set();
        const mark = (p) => tiles(p, (i) => { dirty.add(i); });
        for (const [p, level] of levels) if (layer.levels.get(p) !== level) mark(p);
        for (const p of layer.levels.keys()) if (!levels.has(p)) mark(p);
        if (!dirty.size) continue;
      }
      layer.levels = levels;
      ctx.save();
      if (dirty) {
        ctx.beginPath();
        for (const i of dirty) ctx.rect((i % cols) * TILE, Math.floor(i / cols) * TILE, TILE, TILE);
        ctx.clip();
      }
      ctx.clearRect(0, 0, canvas.width, canvas.height);
      ctx.lineCap = ctx.lineJoin = 'round';
      ctx.filter = 'blur(1.5px)';
      const steps = Array.from({ length: LEVELS }, () => []);
      for (const [p, level] of levels) if (!dirty || tiles(p, (i) => dirty.has(i))) steps[level].push(p);
      steps.forEach((group, i) => {
        if (!group.length) return;
        const s = (i + 1) / LEVELS;
        this._stroke(ctx, group, width * paint(ctx, s));
      });
      ctx.restore();
      tex.needsUpdate = true;
      changed = true;
    }
    this.full = false;
    return { busy, changed };
  }

  /** Called every frame; returns whether the scene needs drawing. */
  frame(now) {
    if (!this.layers) return false;
    let changed = false;
    const target = this.dustRooms?.length ? 1 : 0;
    const dust = this.layers.dust.mesh.material;
    if (Math.abs(dust.opacity - target) > 0.005) {
      dust.opacity += (target - dust.opacity) * 0.06;
      changed = true;
    }
    if (now - this.drawnAt >= REDRAW_MS) {
      const { busy, changed: drew } = this._drawDrying(now);
      this.drawnAt = busy ? now : Infinity;   // nothing wet or glinting: wait for the next map
      if (drew) changed = true;
    }
    const sucking = this.cleaning && vacuums(this.mode) && this.shown.dust;
    this.specks.visible = sucking;
    if (sucking) {
      moveSpecks(this.specks, now);
      changed = true;
    }
    return changed;
  }

  dispose(all = true) {
    if (this.layers) {
      for (const { mesh, tex } of Object.values(this.layers)) {
        this.group.remove(mesh);
        mesh.geometry.dispose();
        mesh.material.dispose();
        tex.dispose();
      }
      this.layers = null;
    }
    if (all) {
      this.env?.dispose();
      this.specks.geometry.dispose();
      this.specks.material.dispose();
    }
  }
}

// Specks of dust flying into the front of the robot, in its own frame (+Z is its front).
function makeSpecks() {
  const geo = new BufferGeometry();
  geo.setAttribute('position', new BufferAttribute(new Float32Array(SPECKS * 3), 3));
  return new Points(geo, new PointsMaterial({ size: 0.018, transparent: true, opacity: 0.85, depthWrite: false }));
}

function moveSpecks(points, now) {
  const pos = points.geometry.attributes.position;
  for (let i = 0; i < SPECKS; i++) {
    const a = -1.2 + 2.4 * frac(i * 0.381966), r = 0.22 + 0.22 * frac(i * 0.7548), h = 0.01 + 0.05 * frac(i * 0.5698);
    const s = frac(now / 700 + frac(i * 0.618034));
    const k = s * s;                                      // slow at first, then sucked in
    const x = Math.sin(a) * r, z = 0.12 + Math.cos(a) * r * 0.8;
    pos.setXYZ(i, x * (1 - k), h * (1 - k) + 0.01, z + (0.1 - z) * k);
  }
  pos.needsUpdate = true;
}

const frac = (v) => v - Math.floor(v);

/** A room's light to reflect, for the wet floor's sheen. */
function makeEnv(renderer) {
  const pmrem = new PMREMGenerator(renderer);
  const env = pmrem.fromScene(new RoomEnvironment(), 0.04).texture;
  pmrem.dispose();
  return env;
}

/** Fine dust and a faint haze, the same every time (a seeded pattern). */
function dustPattern(ctx, dark) {
  const tile = document.createElement('canvas');
  tile.width = tile.height = 96;
  const g = tile.getContext('2d');
  const rgb = dark ? '214,200,176' : '110,92,70';
  g.fillStyle = `rgba(${rgb},0.16)`;
  g.fillRect(0, 0, 96, 96);
  let seed = 7;
  const rand = () => ((seed = (seed * 16807) % 2147483647) / 2147483647);
  for (let i = 0; i < 260; i++) {
    g.fillStyle = `rgba(${rgb},${0.25 + rand() * 0.45})`;
    g.beginPath();
    g.arc(rand() * 96, rand() * 96, rand() < 0.85 ? 0.6 : 1.3, 0, Math.PI * 2);
    g.fill();
  }
  return ctx.createPattern(tile, 'repeat');
}
