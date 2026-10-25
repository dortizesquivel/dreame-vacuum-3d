import { normalizeModel } from './model.js';
import { VacuumScene } from './scene.js';
import { readTheme } from './theme.js';

const DOMAIN = 'dreame_vacuum_3d';
const ZOOM_STEP = 1.4;

const STRINGS = {
  en: {
    noEntity: 'Set "entity" to a vacuum from Dreame Vacuum 3D.',
    waiting: 'Waiting for the map…',
    noMap: 'This version of the integration does not send the map yet.',
    noWebgl: 'This browser cannot draw 3D (WebGL).',
    modelError: 'Could not load the model: {error}',
    view3d: '3D', plan: 'Plan', follow: 'Follow', download: "Download the robot's floor plan (JSON)",
    clean: 'Clean {room}', cancel: 'Cancel', started: 'Cleaning {room}', failed: 'Could not start: {error}',
    zoomIn: 'Zoom in', zoomOut: 'Zoom out',
  },
  es: {
    noEntity: 'Pon en "entity" una aspiradora de Dreame Vacuum 3D.',
    waiting: 'Esperando al mapa…',
    noMap: 'Esta versión de la integración aún no envía el mapa.',
    noWebgl: 'Este navegador no puede dibujar en 3D (WebGL).',
    modelError: 'No se pudo cargar el modelo: {error}',
    view3d: '3D', plan: 'Plano', follow: 'Seguir', download: 'Descargar el plano del robot (JSON)',
    clean: 'Limpiar {room}', cancel: 'Cancelar', started: 'Limpiando {room}', failed: 'No se pudo empezar: {error}',
    zoomIn: 'Acercar', zoomOut: 'Alejar',
  },
};

const STYLE = `
  :host { display: block; }
  ha-card { overflow: hidden; }
  .head { display: flex; align-items: center; justify-content: space-between; gap: 12px; padding: 12px 16px; }
  .title { font-size: 16px; font-weight: 500; color: var(--primary-text-color); min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .chip { display: inline-flex; align-items: center; gap: 7px; flex: none; font-size: 12px; font-weight: 500; padding: 6px 10px;
    border: 1px solid var(--divider-color); border-radius: 999px; color: var(--primary-text-color); font-variant-numeric: tabular-nums; }
  .chip::before { content: ""; width: 7px; height: 7px; border-radius: 50%; background: var(--secondary-text-color); }
  .chip[data-active="true"]::before { background: var(--primary-color); }
  .viewport { position: relative; border-top: 1px solid var(--divider-color); overflow: hidden; }
  .viewport canvas { display: block; }
  .labels { position: absolute; inset: 0; pointer-events: none; z-index: 1; }
  .room { font-size: 11px; font-weight: 500; padding: 2px 6px; border-radius: 6px; white-space: nowrap; color: var(--primary-text-color);
    background: color-mix(in srgb, var(--card-background-color, #fff) 80%, transparent); }
  .room[data-current="true"] { color: var(--text-primary-color, #fff); background: var(--primary-color); }
  .thing { font-size: 11px; font-weight: 500; padding: 2px 6px; border-radius: 6px; white-space: nowrap; color: #1a1a1a;
    background: color-mix(in srgb, var(--warning-color, #ffa600) 85%, white); box-shadow: 0 1px 3px rgba(0, 0, 0, .3); }
  .thing[data-warn="true"] { color: #fff; background: var(--error-color, #db4437); }
  .status[hidden] { display: none; }
  .status { position: absolute; inset: 0; z-index: 3; display: grid; place-items: center; padding: 24px; text-align: center;
    color: var(--secondary-text-color); background: color-mix(in srgb, var(--card-background-color, #fff) 82%, transparent); }
  .seg { position: absolute; z-index: 2; display: flex; gap: 2px; padding: 3px; border-radius: 8px; border: 1px solid var(--divider-color);
    background: color-mix(in srgb, var(--card-background-color, #fff) 85%, transparent); backdrop-filter: blur(6px); }
  .views { top: 10px; right: 10px; }
  .zoom { top: 10px; left: 10px; flex-direction: column; }
  .seg button { font: inherit; font-size: 12px; font-weight: 500; color: var(--secondary-text-color); background: none; border: 0;
    border-radius: 6px; padding: 8px 10px; min-height: 32px; min-width: 32px; cursor: pointer; }
  .zoom button { font-size: 18px; line-height: 1; padding: 0; }
  .seg button:hover { color: var(--primary-text-color); background: color-mix(in srgb, var(--primary-text-color) 8%, transparent); }
  .seg button[aria-pressed="true"] { color: var(--text-primary-color, #fff); background: var(--primary-color); }
  button:focus-visible { outline: 2px solid var(--primary-color); outline-offset: 2px; }
  .panel { position: absolute; left: 10px; right: 10px; bottom: 10px; z-index: 2; display: flex; flex-wrap: wrap; align-items: center;
    justify-content: flex-end; gap: 8px; padding: 8px; border-radius: 10px; border: 1px solid var(--divider-color);
    background: color-mix(in srgb, var(--card-background-color, #fff) 90%, transparent); backdrop-filter: blur(6px); }
  .panel[hidden] { display: none; }
  .panel .msg { flex: 1 1 160px; font-size: 13px; color: var(--primary-text-color); }
  .panel .msg[data-error="true"] { color: var(--error-color, #db4437); }
  .panel button { font: inherit; font-size: 13px; font-weight: 500; color: var(--primary-text-color); background: none;
    border: 1px solid var(--divider-color); border-radius: 8px; padding: 8px 12px; min-height: 36px; cursor: pointer; }
  .panel button.primary { color: var(--text-primary-color, #fff); background: var(--primary-color); border-color: transparent; }
  .panel button[hidden] { display: none; }
`;

class DreameVacuum3DCard extends HTMLElement {
  static getStubConfig(hass) {
    const entity = Object.keys(hass?.states ?? {}).find((id) => id.startsWith('vacuum.'));
    return { entity: entity ?? '' };
  }

  setConfig(config) {
    if (!config || typeof config !== 'object') throw new Error('Invalid configuration');
    const height = Number(config.height ?? 420);
    if (!Number.isFinite(height) || height < 200) throw new Error('"height" must be a number of pixels, 200 or more');
    if (config.view && !['3d', 'plan'].includes(config.view)) throw new Error('"view" must be 3d or plan');
    const dryTime = Number(config.dry_time ?? 10);
    if (!Number.isFinite(dryTime) || dryTime <= 0) throw new Error('"dry_time" must be the minutes the mopped floor takes to dry');
    this._config = {
      ...config, height, dryTime, view: config.view ?? '3d', labels: config.labels !== false, model: normalizeModel(config.model),
    };
    if (this._sub && this._subEntity !== config.entity) this._unsubscribe();
    if (this._root) this._applyConfig();
  }

  set hass(hass) {
    const first = !this._hass;
    this._hass = hass;
    if (!this._config) return;
    if (!this._root) this._build();
    if (first || this._dark !== !!hass.themes?.darkMode) this._applyTheme();
    this._subscribe();
    this._scene?.setLanguage(this._lang());
    this._renderHead();
  }

  get hass() { return this._hass; }

  getCardSize() { return Math.ceil((this._config?.height ?? 420) / 50) + 1; }

  connectedCallback() {
    this._scene?.start();
    if (this._hass) this._subscribe();
  }

  disconnectedCallback() {
    this._scene?.stop();
    this._unsubscribe();
  }

  _lang() {
    return (this._hass?.locale?.language ?? this._hass?.language ?? 'en').split('-')[0];
  }

  _t(key, vars = {}) {
    const lang = this._lang();
    const text = (STRINGS[lang] ?? STRINGS.en)[key] ?? STRINGS.en[key];
    return text.replace(/\{(\w+)\}/g, (_, k) => vars[k] ?? '');
  }

  _build() {
    const root = this.attachShadow({ mode: 'open' });
    root.innerHTML = `
      <style>${STYLE}</style>
      <ha-card>
        <div class="head"><div class="title"></div><div class="chip" role="status"></div></div>
        <div class="viewport">
          <div class="seg views">
            <button type="button" data-view="3d"></button>
            <button type="button" data-view="plan"></button>
            <button type="button" data-follow></button>
          </div>
          <div class="seg zoom">
            <button type="button" data-zoom="in">+</button>
            <button type="button" data-zoom="out">−</button>
            <button type="button" data-download>⤓</button>
          </div>
          <div class="panel" hidden>
            <div class="msg" aria-live="polite"></div>
            <button type="button" class="cancel"></button>
            <button type="button" class="primary clean"></button>
          </div>
          <div class="status"></div>
        </div>
      </ha-card>`;
    this._root = root;
    const $ = (s) => root.querySelector(s);
    this._els = { title: $('.title'), chip: $('.chip'), viewport: $('.viewport'), status: $('.status'), panel: $('.panel'),
      msg: $('.panel .msg'), clean: $('.panel .clean'), cancel: $('.panel .cancel') };
    try {
      this._scene = new VacuumScene(this._els.viewport);
    } catch {
      this._status(this._t('noWebgl'));
      return;
    }
    this._scene.onRoomTap = (id) => this._pick(id);
    this._scene.onModelError = (err) => this._status(this._t('modelError', { error: err.message }));
    root.querySelectorAll('[data-view]').forEach((b) => b.addEventListener('click', () => {
      this._view = b.dataset.view;
      this._follow = false;
      this._scene.setFollow(false);
      this._scene.setView(this._view);
      this._renderButtons();
    }));
    root.querySelector('[data-follow]').addEventListener('click', () => {
      this._follow = !this._follow;
      this._scene.setFollow(this._follow);
      this._renderButtons();
    });
    root.querySelectorAll('[data-zoom]').forEach((b) => b.addEventListener('click', () => this._scene.zoom(b.dataset.zoom === 'in' ? ZOOM_STEP : 1 / ZOOM_STEP)));
    root.querySelector('[data-download]').addEventListener('click', () => this._download());
    this._els.cancel.addEventListener('click', () => this._pick(null));
    this._els.clean.addEventListener('click', () => this._clean());
    this._applyConfig();
    this._scene.start();
  }

  _applyConfig() {
    const c = this._config;
    this._els.viewport.style.height = `${c.height}px`;
    this._view = c.view;
    this._els.title.hidden = c.title === false || c.title === '';
    if (!this._scene) return;
    this._scene.setLabels(c.labels);
    this._scene.setLanguage(this._lang());
    this._scene.setDryTime(c.dryTime);
    this._scene.setModel(c.model);
    this._scene.setView(this._view, true);
    this._renderButtons();
    this._status(c.entity ? (this._map ? '' : this._t('waiting')) : this._t('noEntity'));
  }

  _applyTheme() {
    this._dark = !!this._hass.themes?.darkMode;
    this._scene?.setTheme(readTheme(this, this._dark));
  }

  _renderButtons() {
    const r = this._root;
    r.querySelector('[data-view="3d"]').textContent = this._t('view3d');
    r.querySelector('[data-view="plan"]').textContent = this._t('plan');
    r.querySelector('[data-follow]').textContent = this._t('follow');
    r.querySelectorAll('[data-view]').forEach((b) => b.setAttribute('aria-pressed', String(!this._follow && b.dataset.view === this._view)));
    r.querySelector('[data-follow]').setAttribute('aria-pressed', String(!!this._follow));
    r.querySelector('[data-zoom="in"]').setAttribute('aria-label', this._t('zoomIn'));
    r.querySelector('[data-zoom="out"]').setAttribute('aria-label', this._t('zoomOut'));
    r.querySelector('[data-download]').setAttribute('aria-label', this._t('download'));
    r.querySelector('[data-download]').title = this._t('download');
    this._els.cancel.textContent = this._t('cancel');
  }

  _renderHead() {
    const st = this._hass.states[this._config.entity];
    this._els.title.textContent = this._config.title || st?.attributes.friendly_name || 'Dreame Vacuum 3D';
    const parts = [];
    if (st) parts.push(this._hass.formatEntityState?.(st) ?? st.state);
    const room = this._map?.rooms?.[this._map.room]?.name;
    if (room) parts.push(room);
    if (st?.attributes.battery_level != null) parts.push(`${st.attributes.battery_level} %`);
    this._els.chip.textContent = parts.join(' · ');
    this._els.chip.dataset.active = String(['cleaning', 'returning'].includes(st?.state));
    this._scene?.setCleaning(st?.state === 'cleaning');
  }

  _status(text) {
    this._els.status.textContent = text;
    this._els.status.hidden = !text;
  }

  _subscribe() {
    const entity = this._config?.entity;
    if (this._sub || !entity || !this._hass?.connection || !this.isConnected) return;
    this._subEntity = entity;
    this._sub = this._hass.connection
      .subscribeMessage((map) => this._onMap(map), { type: `${DOMAIN}/map/subscribe`, entity_id: entity })
      .catch((err) => {
        this._sub = null;
        this._status(err?.code === 'unknown_command' ? this._t('noMap') : String(err?.message ?? err));
      });
  }

  _unsubscribe() {
    const sub = this._sub;
    this._sub = null;
    sub?.then((unsub) => unsub?.()).catch(() => {});
  }

  /** The first message has everything; later ones carry the rooms only when they changed. */
  _onMap(message) {
    this._map = { ...this._map, ...message };
    this._status('');
    this._scene?.setMap(this._map);
    if (this._hass) this._renderHead();
  }

  /** The robot's floor plan as JSON, to build a 3D model of the house on it (see docs/house-model.md). */
  _download() {
    if (!this._map?.grid) return;
    const { map_id, grid, cells, rooms, dock } = this._map;
    const blob = new Blob([JSON.stringify({ map_id, grid, cells, rooms, dock })], { type: 'application/json' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = `floor-plan-${map_id}.json`;
    a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 0);
  }

  /** A tap on a room offers to clean it; a tap elsewhere or Cancel closes the offer. */
  _pick(id) {
    const room = id != null ? this._map?.rooms?.[id] : null;
    this._picked = room ? id : null;
    this._scene.select(this._picked);
    const { panel, msg, clean } = this._els;
    panel.hidden = !room;
    if (!room) return;
    msg.dataset.error = 'false';
    msg.textContent = '';
    clean.hidden = false;
    clean.textContent = this._t('clean', { room: room.name ?? `#${id}` });
  }

  async _clean() {
    const id = this._picked;
    if (id == null) return;
    const name = this._map.rooms[id]?.name ?? `#${id}`;
    const { msg, clean } = this._els;
    clean.disabled = true;
    try {
      await this._hass.callService(DOMAIN, 'vacuum_clean_segment', { segments: [Number(id)] }, { entity_id: this._config.entity });
      msg.textContent = this._t('started', { room: name });
      clean.hidden = true;
    } catch (err) {
      msg.dataset.error = 'true';
      msg.textContent = this._t('failed', { error: err?.message ?? String(err) });
    } finally {
      clean.disabled = false;
    }
  }
}

if (!customElements.get('dreame-vacuum-3d-card')) {
  customElements.define('dreame-vacuum-3d-card', DreameVacuum3DCard);
  window.customCards = window.customCards || [];
  window.customCards.push({
    type: 'dreame-vacuum-3d-card',
    name: 'Dreame Vacuum 3D Card',
    description: 'The robot moving through a 3D model of your house',
    preview: false,
    documentationURL: 'https://github.com/dortizesquivel/dreame-vacuum-3d',
  });
  console.info('%c DREAME-VACUUM-3D-CARD ', 'color:#fff;background:#03a9f4;font-weight:700');
}
