import {
  AdditiveBlending, Box3, BoxGeometry, BufferGeometry, Color, CylinderGeometry, DirectionalLight, EdgesGeometry, Float32BufferAttribute,
  FrontSide, GreaterDepth, Group, HemisphereLight, InstancedMesh, LineBasicMaterial, LineSegments, Mesh, MeshBasicMaterial, MeshStandardMaterial, NormalBlending,
  Object3D, PerspectiveCamera, Plane, Raycaster, RingGeometry, Scene, ShaderMaterial, TOUCH, Vector2, Vector3, WebGLRenderer,
} from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { CSS2DObject, CSS2DRenderer } from 'three/addons/renderers/CSS2DRenderer.js';
import { cellRects, robotToModel, roomAt } from './model.js';
import { cssColor, mix } from './theme.js';

// The scene is in the house model's frame (metres, y up). Without a model it is the robot's map in metres:
// x → X, y → -Z. Map coordinates (mm) go through robotToModel.

const V = (x, y, z) => new Vector3(x, y, z);
const WALL = '255';
const FOV = 40;
const ROBOT_R = 0.175;
const MAP_WALL = 0.25;          // wall height drawn from the map when there is no model
const LIFT = 0.012;             // overlays float this far above the floor so they don't flicker into it
const MAX_GLIDE = 12000;        // ms: the robot glides between two poses over the time between them, up to this
// Room tints without a model: categorical, mixed into the card's background so they work on light and dark themes.
const ROOM_COLORS = ['#2a78d6', '#eb6834', '#1baf7a', '#c84fb4', '#d6a72a', '#4fb6c8', '#7a5fd6', '#d65f5f', '#5f9e3a', '#8a8a8a'];
const ease = (s) => (s < 0.5 ? 4 * s * s * s : 1 - Math.pow(-2 * s + 2, 3) / 2);

// Futuristic style, from the mmWave 3D Card: a hologram with glowing edges; the scan wave starts at the robot.
const HOLO_VERTEX = `
  varying vec3 vW, vN;
  void main() {
    vec4 w = modelMatrix * vec4(position, 1.0);
    vW = w.xyz;
    vN = normalize(mat3(modelMatrix) * normal);
    gl_Position = projectionMatrix * viewMatrix * w;
  }`;
const HOLO_WAVE = `
  uniform vec3 scanColor;
  uniform vec2 center;
  uniform float wave, waveOn;
  float scan(vec3 p) { float d = length(p.xz - center) - wave; return waveOn * exp(-d * d / 0.05); }`;
const HOLO_SURFACE = {
  vertexShader: HOLO_VERTEX,
  fragmentShader: `
    uniform vec3 color, base;
    uniform float opacity;
    varying vec3 vW, vN;
    ${HOLO_WAVE}
    void main() {
      float rim = pow(1.0 - abs(dot(normalize(vN), normalize(cameraPosition - vW))), 2.0);
      float a = 0.06 + 0.34 * rim;
      vec3 c = mix(base, color, 0.3 + 0.7 * rim);
      if (vN.y > 0.9) {
        vec2 q = vW.xz / 0.5, g = abs(fract(q - 0.5) - 0.5) / fwidth(q);
        float line = 1.0 - min(min(g.x, g.y), 1.0);
        a += 0.22 * line;
        c = mix(c, color, line);
      }
      float w = scan(vW);
      gl_FragColor = vec4(mix(c, scanColor, w), (a + 0.3 * w) * opacity);
      #include <colorspace_fragment>
    }`,
};
const HOLO_EDGE = {
  vertexShader: `
    varying vec3 vW;
    void main() {
      vec4 w = modelMatrix * vec4(position, 1.0);
      vW = w.xyz;
      gl_Position = projectionMatrix * viewMatrix * w;
    }`,
  fragmentShader: `
    uniform vec3 color;
    uniform float opacity;
    varying vec3 vW;
    ${HOLO_WAVE}
    void main() {
      float w = scan(vW);
      gl_FragColor = vec4(mix(color, scanColor, w), (0.5 + 0.5 * w) * opacity);
      #include <colorspace_fragment>
    }`,
};
const HOLO_EDGE_ANGLE = 28;
const WAVE_SPEED = 2.5;          // m/s
const WAVE_REACH = 8;            // m before the wave starts again

export class VacuumScene {
  constructor(container) {
    this.container = container;
    this.renderer = new WebGLRenderer({ antialias: true, alpha: true });   // throws without WebGL
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    this.renderer.setClearColor(0x000000, 0);
    container.prepend(this.renderer.domElement);
    this.labels = new CSS2DRenderer();
    this.labels.domElement.className = 'labels';
    container.appendChild(this.labels.domElement);

    this.scene = new Scene();
    this.camera = new PerspectiveCamera(FOV, 1, 0.05, 300);
    this.controls = new OrbitControls(this.camera, this.renderer.domElement);
    this.controls.enableDamping = true;
    this.controls.maxPolarAngle = Math.PI * 0.49;
    this.controls.minDistance = 0.8;
    this.controls.maxDistance = 80;
    // Don't hijack dashboard scrolling: one finger scrolls the page, the wheel zooms only with Ctrl/⌘.
    this.controls.touches = { ONE: -1, TWO: TOUCH.DOLLY_ROTATE };
    this.renderer.domElement.style.touchAction = 'pan-y';
    this._onWheel = (e) => { this.controls.enableZoom = e.ctrlKey || e.metaKey; };
    this._onTouch = (e) => { if (e.pointerType === 'touch') this.controls.enableZoom = true; };
    container.addEventListener('wheel', this._onWheel, { capture: true, passive: true });
    container.addEventListener('pointerdown', this._onTouch, { capture: true, passive: true });
    this.controls.addEventListener('change', () => { this.dirty = true; });

    this.scene.add(new HemisphereLight(0xffffff, 0x8a8f99, 2.0));
    this.sun = new DirectionalLight(0xffffff, 1.4);
    this.sun.position.set(-6, 20, 10);
    this.scene.add(this.sun);

    this.modelGroup = new Group();      // the house model, as the file has it after units and up
    this.mapGroup = new Group();        // rooms and walls drawn from the robot's map when there is no model
    this.overlayGroup = new Group();    // rooms being cleaned or picked
    this.labelGroup = new Group();
    this.robot = makeRobot();
    this.robot.visible = false;
    this.scene.add(this.modelGroup, this.mapGroup, this.overlayGroup, this.labelGroup, this.robot);
    this.path = null;

    this.model = null;
    this.modelSig = '';
    this.modelBox = null;
    this.holo = null;
    this.map = null;
    this.xf = null;
    this.mapSig = '';
    this.overlaySig = '';
    this.theme = null;
    this.showLabels = true;
    this.view = '3d';
    this.follow = false;
    this.framed = false;
    this.glide = null;
    this.lastPose = 0;
    this.selected = null;
    this.onRoomTap = null;     // (room id) => void
    this.onModelError = null;  // (error) => void

    this.raycaster = new Raycaster();
    this._down = null;
    this._onDown = (e) => { this._down = [e.clientX, e.clientY]; };
    this._onUp = (e) => this._tap(e);
    this.renderer.domElement.addEventListener('pointerdown', this._onDown);
    this.renderer.domElement.addEventListener('pointerup', this._onUp);

    this.dirty = true;
    this.ro = new ResizeObserver(() => this._resize());
    this.ro.observe(container);
    this._resize();
  }

  // ---------- configuration ----------

  setTheme(theme) {
    this.theme = theme;
    this.robot.userData.body.material.color.set(theme.dark ? '#d9dce1' : '#f4f5f7');
    this.robot.userData.mark.material.color.set(theme.accent);
    this.robot.userData.ring.material.color.set(theme.accent);
    this.robot.userData.ghost.material.color.set(theme.accent);
    this.mapSig = this.overlaySig = '';
    this._holoTheme();
    if (this.map) this.setMap(this.map);
    this.dirty = true;
  }

  setLabels(on) {
    this.showLabels = on;
    this.labelGroup.visible = on;
    this.dirty = true;
  }

  setModel(model) {
    const sig = JSON.stringify(model);
    if (sig === this.modelSig) return;
    this.modelSig = sig;
    this.model = model;
    this.modelBox = null;
    this.holo = null;
    clearGroup(this.modelGroup);
    this._modelChanged();
    if (!model) return;
    loadModel(model.url).then((root) => {
      if (this.modelSig !== sig || this.disposed) { clearGroup(root); return; }
      const norm = new Group();
      norm.rotation.x = model.upRotX;
      norm.scale.setScalar(model.scale);
      norm.add(root);
      this.modelGroup.add(norm);
      if (model.style === 'futuristic') this._holoModel(root, model.opacity);
      else root.traverse((o) => { if (o.isMesh) [].concat(o.material).forEach((m) => modelMaterial(m, model.opacity)); });
      this.modelGroup.updateMatrixWorld(true);
      this.modelBox = new Box3().setFromObject(this.modelGroup);
      this.framed = false;
      this._modelChanged();
    }).catch((err) => {
      if (this.modelSig === sig) this.onModelError?.(err);
    });
  }

  _modelChanged() {
    this.xf = null;
    this.mapSig = this.overlaySig = '';
    if (this.map) this.setMap(this.map);
    this.dirty = true;
  }

  /**
   * map: { grid: { left, top, size, width, height }, cells: { id: [x, y, n, …] }, rooms: { id: { name, x, y } },
   *        robot: [x, y, angle], dock: [x, y, angle], path: [[[x, y], …], …], room, active: [id] }
   */
  setMap(map) {
    this.map = map;
    if (!map?.dock || !this.theme) return;
    const dockSig = map.dock.join();
    if (!this.xf || this.xf.dockSig !== dockSig) {
      this.xf = { ...robotToModel(map.dock, this.model), dockSig };
      this.mapSig = this.overlaySig = '';
    }
    const sig = `${map.map_id}|${Object.entries(map.cells ?? {}).map(([k, v]) => `${k}:${v.length}`).join()}`;
    if (sig !== this.mapSig) {
      this.mapSig = sig;
      this._buildMap();
    }
    const overlay = `${sig}|${(map.active ?? []).join()}|${this.selected}`;
    if (overlay !== this.overlaySig) {
      this.overlaySig = overlay;
      this._buildOverlay();
    }
    this._setPath(map.path);
    this._setPose(map.robot ?? map.dock);
    for (const l of this.labelGroup.children) l.element.dataset.current = String(l.userData.id === map.room);
    if (!this.framed) this.setView(this.view, true);
    this.dirty = true;
  }

  /** While the robot cleans, a ring pulses around it (and the futuristic style's scan wave starts at it). */
  setCleaning(on) {
    this.robot.userData.cleaning = on;
    this.dirty = true;
  }

  select(id) {
    this.selected = id ?? null;
    if (this.map) this.setMap(this.map);
  }

  // ---------- building ----------

  _rectMesh(runs, y, color, opacity) {
    const { left, top, size } = this.map.grid;
    const pos = [];
    for (const [x0, y0, x1, y1] of cellRects(runs)) {
      // Cell corners in map mm → model. A rectangle stays a parallelogram after any rotation, so 4 corners do.
      const c = [[x0, y0], [x1, y0], [x1, y1], [x0, y1]].map(([i, j]) => this.xf.point(left + i * size, top + j * size));
      pos.push(...c[0], ...c[1], ...c[2], ...c[0], ...c[2], ...c[3]);   // facing up: the map → model turn is a mirror
    }
    const g = new BufferGeometry();
    const xyz = new Float32Array((pos.length / 2) * 3);
    for (let i = 0, k = 0; i < pos.length; i += 2, k += 3) { xyz[k] = pos[i]; xyz[k + 1] = y; xyz[k + 2] = pos[i + 1]; }
    g.setAttribute('position', new Float32BufferAttribute(xyz, 3));
    return new Mesh(g, new MeshBasicMaterial({ color, transparent: opacity < 1, opacity, depthWrite: opacity >= 1 }));
  }

  _wallMesh(runs, height, color) {
    const { left, top, size } = this.map.grid;
    const rects = cellRects(runs);
    const walls = new InstancedMesh(new BoxGeometry(1, 1, 1), new MeshStandardMaterial({ color, roughness: 0.9 }), rects.length);
    const o = new Object3D();
    rects.forEach(([x0, y0, x1, y1], i) => {
      const [ax, az] = this.xf.point(left + x0 * size, top + y0 * size);
      const [bx, bz] = this.xf.point(left + x1 * size, top + y1 * size);
      const [cx, cz] = this.xf.point(left + x1 * size, top + y0 * size);
      o.position.set((ax + bx) / 2, this.xf.floor + height / 2, (az + bz) / 2);
      o.rotation.y = Math.atan2(-(cz - az), cx - ax);
      o.scale.set(Math.hypot(cx - ax, cz - az), height, Math.hypot(bx - cx, bz - cz));
      o.updateMatrix();
      walls.setMatrixAt(i, o.matrix);
    });
    return walls;
  }

  _buildMap() {
    clearGroup(this.mapGroup);
    clearGroup(this.labelGroup);
    const { cells = {}, rooms = {} } = this.map;
    const th = this.theme;
    if (!this.model) {
      const ids = Object.keys(cells).filter((id) => id !== WALL).sort((a, b) => a - b);
      ids.forEach((id, i) => {
        const tint = rooms[id] ? mix(th.bg, ROOM_COLORS[i % ROOM_COLORS.length], th.dark ? 0.35 : 0.28) : mix(th.bg, th.fg2, 0.12);
        this.mapGroup.add(this._rectMesh(cells[id], this.xf.floor, tint, 1));
      });
      if (cells[WALL]) this.mapGroup.add(this._wallMesh(cells[WALL], MAP_WALL, mix(th.bg, th.fg, th.dark ? 0.55 : 0.35)));
      this.mapGroup.add(this._dockMesh());
    }
    for (const [id, r] of Object.entries(rooms)) {
      if (!r.name || !cells[id]) continue;
      const [x, z] = this.xf.point(r.x, r.y);
      const el = document.createElement('div');
      el.className = 'room';
      el.textContent = r.name;
      const label = new CSS2DObject(el);
      label.position.set(x, this.xf.floor + 0.3, z);
      label.userData.id = Number(id);
      this.labelGroup.add(label);
    }
    this.labelGroup.visible = this.showLabels;
  }

  _dockMesh() {
    // About an X10+ self-empty station: 0.34 m wide, 0.45 m tall, standing behind the docked robot.
    const [x, y, a] = this.map.dock;
    const [px, pz] = this.xf.point(x, y), h = this.xf.heading(a);
    const m = new Mesh(new BoxGeometry(0.34, 0.45, 0.25), new MeshStandardMaterial({ color: this.theme.dark ? '#3a3d44' : '#2e3138', roughness: 0.6 }));
    m.position.set(px - 0.3 * Math.sin(h), this.xf.floor + 0.225, pz - 0.3 * Math.cos(h));
    m.rotation.y = h;
    return m;
  }

  _buildOverlay() {
    clearGroup(this.overlayGroup);
    const { cells = {}, active = [] } = this.map;
    const accent = this.theme.accent;
    const ids = new Set([...active.map(String), ...(this.selected != null ? [String(this.selected)] : [])]);
    for (const id of ids) {
      if (!cells[id]) continue;
      const strong = String(this.selected) === id;
      this.overlayGroup.add(this._rectMesh(cells[id], this.xf.floor + LIFT, accent, strong ? 0.45 : 0.22));
    }
  }

  /** path: polylines of map points, [[[x, y], …], …]. */
  _setPath(path) {
    if (this.path) {
      this.scene.remove(this.path);
      this.path.geometry.dispose();
      this.path.material.dispose();
      this.path = null;
    }
    if (!path?.length) return;
    const y = this.xf.floor + LIFT * 1.5;
    const at = ([x, yy]) => { const [X, Z] = this.xf.point(x, yy); return V(X, y, Z); };
    const pts = [];
    for (const line of path) for (let i = 1; i < line.length; i++) pts.push(at(line[i - 1]), at(line[i]));
    this.path = new LineSegments(new BufferGeometry().setFromPoints(pts),
      new LineBasicMaterial({ color: this.theme.accent, transparent: true, opacity: 0.75 }));
    this.scene.add(this.path);
  }

  _setPose([x, y, a]) {
    const [X, Z] = this.xf.point(x, y);
    const to = { x: X, z: Z, h: this.xf.heading(a) };
    const now = performance.now();
    const r = this.robot;
    if (!r.visible) {
      r.visible = true;
      Object.assign(r.userData, { pose: to });
    } else {
      const from = r.userData.pose;
      if (from.x === to.x && from.z === to.z && from.h === to.h) return;
      // Glide over the time since the last pose: the robot keeps moving until the next one arrives.
      const dur = Math.min(MAX_GLIDE, Math.max(300, now - this.lastPose));
      let dh = (to.h - from.h) % (2 * Math.PI);
      if (dh > Math.PI) dh -= 2 * Math.PI;
      if (dh < -Math.PI) dh += 2 * Math.PI;
      this.glide = { t0: now, dur, from: { ...from }, to: { ...to, h: from.h + dh } };
    }
    this.lastPose = now;
    this._placeRobot(r.userData.pose);
  }

  _placeRobot(p) {
    this.robot.userData.pose = p;
    this.robot.position.set(p.x, this.xf.floor, p.z);
    this.robot.rotation.y = p.h;
  }

  _holoModel(root, opacity) {
    const u = {
      color: { value: new Color() }, base: { value: new Color() }, scanColor: { value: new Color() },
      opacity: { value: opacity }, wave: { value: 0 }, waveOn: { value: 0 }, center: { value: new Vector2() },
    };
    const common = { uniforms: u, transparent: true, depthWrite: false };
    const surface = new ShaderMaterial({ ...HOLO_SURFACE, ...common, side: FrontSide });
    const edge = new ShaderMaterial({ ...HOLO_EDGE, ...common });
    const meshes = [];
    root.traverse((o) => { if (o.isMesh) meshes.push(o); });
    for (const o of meshes) {
      [].concat(o.material).forEach(disposeMaterial);
      o.material = surface;
      o.add(new LineSegments(new EdgesGeometry(o.geometry, HOLO_EDGE_ANGLE), edge));
    }
    this.holo = { uniforms: u, materials: [surface, edge] };
    this._holoTheme();
  }

  _holoTheme() {
    if (!this.holo || !this.theme) return;
    const th = this.theme, u = this.holo.uniforms;
    const color = cssColor(this.model?.color, th.model, th.bg);
    u.color.value.set(color);
    u.base.value.set(mix(th.bg, color, th.dark ? 0.25 : 0.45));
    u.scanColor.value.set(th.accent);
    for (const m of this.holo.materials) m.blending = th.dark ? AdditiveBlending : NormalBlending;
  }

  // ---------- camera ----------

  _bounds() {
    if (this.modelBox && !this.modelBox.isEmpty()) return this.modelBox.clone();
    const box = new Box3().setFromObject(this.mapGroup);
    if (box.isEmpty() && this.robot.visible) box.setFromCenterAndSize(this.robot.position, V(4, 1, 4));
    return box;
  }

  /** view: '3d' (from above at an angle) or 'plan' (straight down, the map's +y up the screen). */
  setView(name, instant = false) {
    this.view = name;
    const box = this._bounds();
    if (box.isEmpty()) return;
    this.framed = true;
    const center = box.getCenter(V(0, 0, 0)).setY(this.xf?.floor ?? 0);
    const size = box.getSize(V(0, 0, 0));
    const t = Math.tan((FOV / 2) * Math.PI / 180);
    const aspect = this.camera.aspect || 1.6;
    const d = Math.max((size.z / 2 + 0.5) / t, (size.x / 2 + 0.5) / (t * aspect));
    const dir = name === 'plan' ? V(0, 1, 0.0001) : V(0, 0.78, 0.62);
    const pos = center.clone().add(dir.normalize().multiplyScalar(d * (name === 'plan' ? 1 : 0.95)));
    this.controls.maxDistance = Math.max(80, d * 3);
    if (instant || window.matchMedia?.('(prefers-reduced-motion: reduce)').matches) {
      this.tween = null;
      this.camera.position.copy(pos);
      this.controls.target.copy(center);
      this.controls.update();
    } else {
      this.tween = { t0: performance.now(), dur: 800, pos, target: center, fromPos: this.camera.position.clone(), fromTarget: this.controls.target.clone() };
    }
    this.dirty = true;
  }

  setFollow(on) {
    this.follow = on;
    if (on && this.robot.visible) {
      // Close in on the robot, keeping the current angle.
      const offset = this.camera.position.clone().sub(this.controls.target).setLength(4.5);
      const target = this.robot.position.clone();
      this.tween = { t0: performance.now(), dur: 800, pos: target.clone().add(offset), target, fromPos: this.camera.position.clone(), fromTarget: this.controls.target.clone() };
    }
    this.dirty = true;
  }

  zoom(factor) {
    const offset = this.camera.position.clone().sub(this.controls.target);
    const d = Math.min(this.controls.maxDistance, Math.max(this.controls.minDistance, offset.length() / factor));
    this.camera.position.copy(this.controls.target).add(offset.setLength(d));
    this.controls.update();
    this.dirty = true;
  }

  _tap(e) {
    const down = this._down;
    this._down = null;
    if (!down || Math.hypot(e.clientX - down[0], e.clientY - down[1]) > 6 || !this.xf || !this.map?.grid) return;
    const r = this.renderer.domElement.getBoundingClientRect();
    const ndc = new Vector2(((e.clientX - r.left) / r.width) * 2 - 1, -((e.clientY - r.top) / r.height) * 2 + 1);
    this.raycaster.setFromCamera(ndc, this.camera);
    const hit = this.raycaster.ray.intersectPlane(new Plane(V(0, 1, 0), -this.xf.floor), V(0, 0, 0));
    if (!hit) return;
    const [x, y] = this.xf.toMap(hit.x, hit.z);
    this.onRoomTap?.(roomAt(this.map, x, y));
  }

  // ---------- lifecycle ----------

  start() {
    if (this.running) return;
    this.running = true;
    const loop = () => {
      if (!this.running) return;
      this.raf = requestAnimationFrame(loop);
      this._frame();
    };
    loop();
  }

  stop() {
    this.running = false;
    cancelAnimationFrame(this.raf);
  }

  dispose() {
    this.disposed = true;
    this.stop();
    this.ro.disconnect();
    this.container.removeEventListener('wheel', this._onWheel, { capture: true });
    this.container.removeEventListener('pointerdown', this._onTouch, { capture: true });
    this.renderer.domElement.removeEventListener('pointerdown', this._onDown);
    this.renderer.domElement.removeEventListener('pointerup', this._onUp);
    this.controls.dispose();
    this.scene.traverse((o) => {
      o.geometry?.dispose();
      if (o.material) [].concat(o.material).forEach(disposeMaterial);
    });
    clearGroup(this.labelGroup);
    this.renderer.dispose();
    this.renderer.forceContextLoss();
    this.renderer.domElement.remove();
    this.labels.domElement.remove();
  }

  _resize() {
    const w = this.container.clientWidth, h = this.container.clientHeight;
    if (!w || !h) return;
    this.renderer.setSize(w, h);
    this.labels.setSize(w, h);
    this.camera.aspect = w / h;
    this.camera.updateProjectionMatrix();
    this.dirty = true;
  }

  _frame() {
    const now = performance.now();
    let moving = false;
    if (this.glide) {
      const g = this.glide, s = Math.min(1, (now - g.t0) / g.dur);
      this._placeRobot({ x: g.from.x + (g.to.x - g.from.x) * s, z: g.from.z + (g.to.z - g.from.z) * s, h: g.from.h + (g.to.h - g.from.h) * s });
      if (s >= 1) this.glide = null;
      moving = true;
    }
    if (this.tween) {
      const tw = this.tween, s = Math.min(1, (now - tw.t0) / tw.dur), k = ease(s);
      this.camera.position.lerpVectors(tw.fromPos, tw.pos, k);
      this.controls.target.lerpVectors(tw.fromTarget, tw.target, k);
      if (s >= 1) this.tween = null;
      moving = true;
    } else if (this.follow && this.robot.visible) {
      const delta = this.robot.position.clone().sub(this.controls.target).multiplyScalar(0.08);
      if (delta.lengthSq() > 1e-8) {
        this.controls.target.add(delta);
        this.camera.position.add(delta);
        moving = true;
      }
    }
    const cleaning = this.robot.userData.cleaning;
    const ring = this.robot.userData.ring;
    ring.visible = !!cleaning;
    if (cleaning) {
      const p = (now / 1600) % 1;
      ring.scale.setScalar(1 + p * 1.6);
      ring.material.opacity = 0.55 * (1 - p);
      moving = true;
    }
    if (this.holo) {
      const u = this.holo.uniforms;
      u.waveOn.value = cleaning ? 1 : 0;
      if (cleaning) {
        u.wave.value = ((now / 1000) * WAVE_SPEED) % WAVE_REACH;
        u.center.value.set(this.robot.position.x, this.robot.position.z);
      }
    }
    if (this.controls.update()) moving = true;
    if (!moving && !this.dirty) return;
    this.dirty = false;
    this.renderer.render(this.scene, this.camera);
    this.labels.render(this.scene, this.camera);
  }
}

/** A robot vacuum about the size of an X10+: a 35 cm disc, a lidar turret, a mark at the front and a pulse ring. */
function makeRobot() {
  const g = new Group();
  const body = new Mesh(new CylinderGeometry(ROBOT_R, ROBOT_R, 0.085, 48), new MeshStandardMaterial({ color: '#f4f5f7', roughness: 0.45, metalness: 0.05 }));
  body.position.y = 0.0425 + 0.01;
  const lidar = new Mesh(new CylinderGeometry(0.045, 0.045, 0.03, 32), new MeshStandardMaterial({ color: '#2b2e33', roughness: 0.4 }));
  lidar.position.set(0, 0.11, -0.03);
  const mark = new Mesh(new BoxGeometry(0.16, 0.02, 0.03), new MeshBasicMaterial({ color: '#03a9f4' }));
  mark.position.set(0, 0.1, 0.14);
  const ring = new Mesh(new RingGeometry(ROBOT_R + 0.02, ROBOT_R + 0.05, 48), new MeshBasicMaterial({ color: '#03a9f4', transparent: true, depthWrite: false }));
  ring.rotation.x = -Math.PI / 2;
  ring.position.y = LIFT * 2;
  // Where walls hide the robot, its outline shows through them in the accent colour.
  const ghost = new Mesh(body.geometry, new MeshBasicMaterial({ color: '#03a9f4', transparent: true, opacity: 0.7, depthWrite: false, depthFunc: GreaterDepth }));
  ghost.position.copy(body.position);
  ghost.renderOrder = 10;
  g.add(body, lidar, mark, ring, ghost);
  g.userData = { body, mark, ring, ghost, cleaning: false };
  return g;
}

const modelFiles = new Map();
function loadModel(url) {
  if (!modelFiles.has(url)) {
    modelFiles.set(url, fetch(url).then((r) => {
      if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
      return r.arrayBuffer();
    }));
    modelFiles.get(url).catch(() => modelFiles.delete(url));
  }
  const base = url.slice(0, url.lastIndexOf('/') + 1);
  return modelFiles.get(url).then((buf) => new GLTFLoader().parseAsync(buf, base)).then((gltf) => gltf.scene);
}

// Materials stay as the file has them. Single-sided, so walls whose inside faces away from the camera vanish and the
// house reads like a doll's house.
function modelMaterial(m, opacity) {
  m.side = FrontSide;
  if (opacity < 1) {
    m.transparent = true;
    m.opacity *= opacity;
    m.depthWrite = false;
  }
  return m;
}

function disposeMaterial(m) {
  m.map?.dispose();
  m.dispose();
}

function clearGroup(group) {
  group.traverse((o) => {
    if (o.isCSS2DObject) o.element.remove();
    if (o === group) return;
    o.geometry?.dispose();
    if (o.material) [].concat(o.material).forEach(disposeMaterial);
  });
  group.clear();
}
