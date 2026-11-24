import { appendPath, normalizeModel, roomAt, routineRooms } from './model.js';
import { VacuumScene } from './scene.js';
import { readTheme } from './theme.js';
import { thingLabel } from './things.js';

const DOMAIN = 'dreame_vacuum_3d';
const ZOOM_STEP = 1.4;
const REPLAY_SPEED = 20;     // a past cleaning plays this many times faster than the robot went
const COVERAGE = 5;          // the coverage shows this many past cleanings
const MAX_ZONES = 10;        // the robot cleans at most this many zones in one task
// What the robot does that gets a tag over it, by its state sensor's value (the integration's DreameVacuumState).
const BADGES = {
  charging: '⚡', smart_charging: '⚡',
  washing: '🫧', washing_paused: '🫧', clean_add_water: '🫧', returning_to_wash: '🫧',
  drying: '💨', auto_emptying: '🗑️', emptying: '🗑️', returning_auto_empty: '🗑️',
  paused: '⏸️', error: '⚠️',
};
const ICONS = {   // Material Design Icons
  start: 'M8,5.14V19.14L19,12.14L8,5.14Z',
  pause: 'M14,19H18V5H14M6,19H10V5H6V19Z',
  dock: 'M10,20V14H14V20H19V12H22L12,3L2,12H5V20H10Z',
  zone: 'M3,3H9V5H5V9H3V3M15,3H21V9H19V5H15V3M3,15H5V19H9V21H3V15M19,15H21V21H15V19H19V15M8,8H16V16H8V8Z',
  point: 'M12,8A4,4 0 0,1 16,12A4,4 0 0,1 12,16A4,4 0 0,1 8,12A4,4 0 0,1 12,8M3.05,13H1V11H3.05C3.5,6.83 6.83,3.5 11,3.05V1H13V3.05C17.17,3.5 20.5,6.83 20.95,11H23V13H20.95C20.5,17.17 17.17,20.5 13,20.95V23H11V20.95C6.83,20.5 3.5,17.17 3.05,13M12,5A7,7 0 0,0 5,12A7,7 0 0,0 12,19A7,7 0 0,0 19,12A7,7 0 0,0 12,5Z',
  settings: 'M3,17V19H9V17H3M3,5V7H13V5H3M13,21V19H21V17H13V15H11V21H13M7,9V11H3V13H7V15H9V9H7M21,13V11H11V13H21M15,9H17V7H21V5H17V3H15V9Z',
  station: 'M8,2H16A2,2 0 0,1 18,4V11.5H6V4A2,2 0 0,1 8,2M10,5V7H14V5H10M12,13A4.5,4.5 0 0,1 16.5,17.5A4.5,4.5 0 0,1 12,22A4.5,4.5 0 0,1 7.5,17.5A4.5,4.5 0 0,1 12,13Z',   // the dock's tower, the robot at its foot
  plus: 'M19,13H13V19H11V13H5V11H11V5H13V11H19V13Z',
  minus: 'M19,13H5V11H19V13Z',
  download: 'M5,20H19V18H5M19,9H15V3H9V9H5L12,16L19,9Z',
  history: 'M13.5,8H12V13L16.28,15.54L17,14.33L13.5,12.25V8M13,3A9,9 0 0,0 4,12H1L4.96,16.03L9,12H6A7,7 0 0,1 13,5A7,7 0 0,1 20,12A7,7 0 0,1 13,19C11.07,19 9.32,18.21 8.06,16.94L6.64,18.36C8.27,20 10.5,21 13,21A9,9 0 0,0 22,12A9,9 0 0,0 13,3',
};
const svg = (name) => `<svg viewBox="0 0 24 24" aria-hidden="true"><path d="${ICONS[name]}"></path></svg>`;
// A disc with a play triangle cut out of it, as a CSS mask: the disc takes the theme's accent.
const PLAY = `url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24'%3E%3Cpath fill-rule='evenodd' d='M12,2A10,10 0 1,0 12,22A10,10 0 1,0 12,2ZM10,8V16L16,12Z'/%3E%3C/svg%3E")`;

// The cleaning settings: the robot's select entities by translation key. A level row draws a meter, its options in order.
const SETTINGS = [
  { key: 'cleaning_mode', label: 'mode' },
  { key: 'suction_level', label: 'suction', level: true, off: ['mopping'] },
  { key: 'water_volume', label: 'water', level: true, off: ['sweeping'] },
];
const MOPPING = ['mopping', 'sweeping_and_mopping', 'mopping_after_sweeping'];
// The dock's actions: the integration's buttons by translation key. While the dock washes or dries, its stop takes the place
// of its start (by the robot's state sensor's value).
const STATION = [
  { key: 'empty', label: 'empty', icon: '🗑️' },
  { key: 'wash_mop', label: 'wash', icon: '🫧', stop: 'stop_washing', stopLabel: 'stopWash',
    busy: ['washing', 'washing_paused', 'clean_add_water'] },
  { key: 'dry_mop', label: 'dry', icon: '💨', stop: 'stop_drying', stopLabel: 'stopDry', busy: ['drying'] },
];

const STRINGS = {
  en: {
    noEntity: 'Set "entity" to a vacuum from Dreame Vacuum 3D.',
    waiting: 'Waiting for the map…',
    noMap: 'This version of the integration does not send the map yet.',
    noWebgl: 'This browser cannot draw 3D (WebGL).',
    modelError: 'Could not load the model: {error}',
    view3d: '3D', plan: 'Plan', follow: 'Follow', download: "Download the robot's floor plan (JSON)",
    clean: 'Clean {room}', cancel: 'Cancel', close: 'Close', started: 'Cleaning {room}', failed: 'Could not start: {error}',
    zoomIn: 'Zoom in', zoomOut: 'Zoom out', missing: 'Not on the map: {rooms}',
    start: 'Start', pause: 'Pause', dock: 'Back to the dock', commandFailed: 'The robot did not take it: {error}',
    zone: 'Draw zones to clean', drawZone: 'Drag on the floor to draw a zone; draw more to clean them all', zoneSize: 'Zone of {w} × {h} m',
    cleanZone: 'Clean the zone', theZone: 'the zone', zones: '{n} zones: {sizes}', cleanZones: 'Clean {n} zones', theZones: '{n} zones',
    undo: 'Undo the last zone', maxZones: 'At most {n} zones', rooms: 'In this order: {rooms}', cleanRooms: 'Clean {n} rooms',
    history: 'Past cleanings', noHistory: 'No cleanings yet', lastCleanings: 'Last cleanings: tap one to watch it again',
    coverage: 'Coverage', live: 'Live', replaying: 'Replaying {when}, ×{speed}', replayed: 'Replayed {when}',
    otherMap: 'That cleaning was on another map', noReplay: 'Could not get that cleaning: {error}', whole: 'the whole house',
    settings: 'Cleaning settings', mode: 'Mode', suction: 'Suction', water: 'Water', repeats: 'Passes',
    nextCleaning: 'The mode changes on the next cleaning', fitMop: 'Fit the mop pads to mop', noSettings: 'This robot has no cleaning settings to change',
    station: 'Dock: empty the bin, wash or dry the mop', stationTitle: 'The dock', empty: 'Empty the dust bin', wash: 'Wash the mop',
    stopWash: 'Stop washing the mop', dry: 'Dry the mop', stopDry: 'Stop drying the mop', sent: '{action}: sent to the dock',
    point: 'Pick a spot: clean around it or send the robot there', pickPoint: 'Tap the floor to pick a spot',
    spotPicked: 'A spot in {room}', spotFloor: 'A spot on the floor', spot: 'Clean around it', goHere: 'Go here',
    theSpot: 'around the spot', going: 'On its way to the spot',
    ignore: 'Ignore it', avoid: 'Avoid it again', ignored: '{thing}: the robot goes over it', avoided: '{thing}: the robot avoids it',
  },
  es: {
    noEntity: 'Pon en "entity" una aspiradora de Dreame Vacuum 3D.',
    waiting: 'Esperando al mapa…',
    noMap: 'Esta versión de la integración aún no envía el mapa.',
    noWebgl: 'Este navegador no puede dibujar en 3D (WebGL).',
    modelError: 'No se pudo cargar el modelo: {error}',
    view3d: '3D', plan: 'Plano', follow: 'Seguir', download: 'Descargar el plano del robot (JSON)',
    clean: 'Limpiar {room}', cancel: 'Cancelar', close: 'Cerrar', started: 'Limpiando {room}', failed: 'No se pudo empezar: {error}',
    zoomIn: 'Acercar', zoomOut: 'Alejar', missing: 'No están en el mapa: {rooms}',
    start: 'Empezar', pause: 'Pausar', dock: 'Volver a la base', commandFailed: 'El robot no lo aceptó: {error}',
    zone: 'Dibujar zonas para limpiar', drawZone: 'Arrastra sobre el suelo para dibujar una zona; dibuja más para limpiarlas todas', zoneSize: 'Zona de {w} × {h} m',
    cleanZone: 'Limpiar la zona', theZone: 'la zona', zones: '{n} zonas: {sizes}', cleanZones: 'Limpiar {n} zonas', theZones: '{n} zonas',
    undo: 'Deshacer la última zona', maxZones: 'Como mucho {n} zonas', rooms: 'En este orden: {rooms}', cleanRooms: 'Limpiar {n} habitaciones',
    history: 'Limpiezas anteriores', noHistory: 'Aún no hay limpiezas', lastCleanings: 'Últimas limpiezas: toca una para verla otra vez',
    coverage: 'Cobertura', live: 'En directo', replaying: 'Repitiendo {when}, ×{speed}', replayed: 'Repetida {when}',
    otherMap: 'Esa limpieza fue en otro mapa', noReplay: 'No se pudo traer esa limpieza: {error}', whole: 'toda la casa',
    settings: 'Ajustes de limpieza', mode: 'Modo', suction: 'Succión', water: 'Agua', repeats: 'Pasadas',
    nextCleaning: 'El modo cambia en la próxima limpieza', fitMop: 'Pon las mopas para fregar', noSettings: 'Este robot no tiene ajustes de limpieza que cambiar',
    station: 'Base: vaciar el depósito, lavar o secar la mopa', stationTitle: 'La base', empty: 'Vaciar el depósito', wash: 'Lavar la mopa',
    stopWash: 'Parar el lavado de la mopa', dry: 'Secar la mopa', stopDry: 'Parar el secado de la mopa', sent: '{action}: enviado a la base',
    point: 'Elegir un punto: limpiar alrededor o mandar el robot allí', pickPoint: 'Toca el suelo para elegir un punto',
    spotPicked: 'Un punto en {room}', spotFloor: 'Un punto del suelo', spot: 'Limpiar alrededor', goHere: 'Ir aquí',
    theSpot: 'alrededor del punto', going: 'De camino al punto',
    ignore: 'Ignorarlo', avoid: 'Volver a evitarlo', ignored: '{thing}: el robot pasa por encima', avoided: '{thing}: el robot lo evita',
  },
};

const STYLE = `
  /* Every colour comes from the Home Assistant theme: a surface, two neutral fills, a tint of the accent and a glass for
     what floats over the map. */
  :host { display: block;
    --dv-surface: var(--ha-card-background, var(--card-background-color, #fff));
    --dv-fill: color-mix(in srgb, var(--primary-text-color) 7%, transparent);
    --dv-fill-hover: color-mix(in srgb, var(--primary-text-color) 12%, transparent);
    --dv-tonal: color-mix(in srgb, var(--primary-color) 16%, transparent);
    --dv-glass: color-mix(in srgb, var(--dv-surface) 80%, transparent);
    --dv-ease: cubic-bezier(.2, .8, .2, 1); --dv-play: ${PLAY}; }
  ha-card { overflow: hidden; }
  button { -webkit-tap-highlight-color: transparent; }
  button:focus-visible { outline: 2px solid var(--primary-color); outline-offset: 2px; }
  [hidden] { display: none !important; }

  /* The header: who and how it is on the left, what to tell it on the right. */
  .head { display: flex; align-items: center; justify-content: space-between; gap: 12px; padding: 12px 12px 12px 16px; min-height: 40px; }
  .about { display: grid; gap: 2px; min-width: 0; }
  .title { font-size: 16px; font-weight: 500; line-height: 1.3; color: var(--primary-text-color); min-width: 0; overflow: hidden;
    text-overflow: ellipsis; white-space: nowrap; }
  /* A block, not a flex box, so a long status ends in an ellipsis on a narrow card instead of pushing the buttons out. */
  .chip { display: block; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: 13px; line-height: 1.4;
    color: var(--secondary-text-color); font-variant-numeric: tabular-nums; }
  .chip:empty { display: none; }
  .chip::before { content: ""; display: inline-block; vertical-align: 1px; margin-right: 7px; width: 8px; height: 8px; border-radius: 50%;
    background: var(--secondary-text-color); opacity: .6; }
  .chip[data-active="true"]::before { background: var(--primary-color); opacity: 1; animation: dv-pulse 2s ease-out infinite; }
  .chip[data-warn="true"] { color: var(--error-color, #db4437); }
  .chip[data-warn="true"]::before { background: var(--error-color, #db4437); opacity: 1; animation: none; }
  @keyframes dv-pulse { from { box-shadow: 0 0 0 0 color-mix(in srgb, var(--primary-color) 55%, transparent); }
    70%, to { box-shadow: 0 0 0 6px transparent; } }
  .ctl { display: flex; gap: 8px; flex: none; }
  .ctl button { display: grid; place-items: center; width: 40px; height: 40px; padding: 0; border: 0; border-radius: 50%; cursor: pointer;
    color: var(--primary-text-color); background: var(--dv-fill); transition: background-color .15s, transform .15s var(--dv-ease); }
  .ctl button:hover { background: var(--dv-fill-hover); }
  /* Start or pause is the one thing most people come for: the filled button. */
  .ctl [data-cmd="toggle"] { color: var(--text-primary-color, #fff); background: var(--primary-color); }
  .ctl [data-cmd="toggle"]:hover { background: color-mix(in srgb, var(--primary-color) 88%, var(--primary-text-color)); }
  .ctl button:active { transform: scale(.94); }
  .ctl button:disabled { color: var(--disabled-text-color, #bdbdbd); cursor: default; background: var(--dv-fill); transform: none; }
  .ctl svg { width: 22px; height: 22px; fill: currentColor; }

  /* The map, and what floats over it. */
  .viewport { position: relative; border-top: 1px solid var(--divider-color); overflow: hidden; }
  .viewport canvas { display: block; }
  .labels { position: absolute; inset: 0; pointer-events: none; z-index: 1; }
  .room { font-size: 11px; font-weight: 500; line-height: 1.3; padding: 3px 8px; border-radius: 999px; white-space: nowrap;
    color: var(--primary-text-color); background: var(--dv-glass); box-shadow: 0 1px 3px rgba(0, 0, 0, .18); }
  .room[data-current="true"] { color: var(--text-primary-color, #fff); background: var(--primary-color); }
  .thing { font-size: 11px; font-weight: 500; line-height: 1.3; padding: 3px 8px; border-radius: 999px; white-space: nowrap; color: #1a1a1a;
    background: color-mix(in srgb, var(--warning-color, #ffa600) 85%, white); box-shadow: 0 1px 3px rgba(0, 0, 0, .3); }
  .thing[data-warn="true"] { color: #fff; background: var(--error-color, #db4437); }
  .thing[data-ignored="true"] { opacity: .55; text-decoration: line-through; }
  .badge[data-warn="false"] { color: var(--primary-text-color); background: color-mix(in srgb, var(--dv-surface) 92%, transparent); }
  .status { position: absolute; inset: 0; z-index: 3; display: grid; place-content: center; justify-items: center; gap: 12px; padding: 24px;
    text-align: center; font-size: 14px; line-height: 1.4; color: var(--secondary-text-color);
    background: color-mix(in srgb, var(--dv-surface) 86%, transparent); }
  .status[data-wait="true"]::before { content: ""; width: 28px; height: 28px; border-radius: 50%; border: 3px solid var(--dv-fill-hover);
    border-top-color: var(--primary-color); animation: dv-spin .9s linear infinite; }
  @keyframes dv-spin { to { transform: rotate(1turn); } }
  .seg { position: absolute; z-index: 2; display: flex; gap: 2px; padding: 4px; border-radius: 14px; box-sizing: border-box;
    border: 1px solid var(--divider-color); background: var(--dv-glass); backdrop-filter: blur(12px) saturate(1.4);
    -webkit-backdrop-filter: blur(12px) saturate(1.4); box-shadow: 0 2px 8px rgba(0, 0, 0, .12); }
  .seg button { position: relative; display: grid; place-items: center; font: inherit; font-size: 13px; font-weight: 500; line-height: 1;
    color: var(--primary-text-color); background: none; border: 0; border-radius: 10px; padding: 0 12px; min-height: 36px; min-width: 36px;
    cursor: pointer; transition: background-color .15s, color .15s; }
  .seg button:hover { background: var(--dv-fill-hover); }
  .seg button[aria-pressed="true"] { color: var(--text-primary-color, #fff); background: var(--primary-color); }
  .seg svg { width: 20px; height: 20px; fill: currentColor; }
  .views { top: 10px; right: 10px; }
  /* The tools, down the left: what to do on the map. On a low card they give up some height, so all stay in the card. */
  .tools { top: 10px; left: 10px; flex-direction: column; max-height: calc(100% - 20px); }
  .tools button { padding: 0; width: 40px; flex: 0 1 40px; min-height: 0; }
  /* Moving about the map: small, in the far corner. */
  .zoom { bottom: 10px; right: 10px; }
  .zoom button { padding: 0; width: 36px; color: var(--secondary-text-color); }
  .zoom button:hover { color: var(--primary-text-color); }
  /* What a tool is for, beside it: for a mouse over it or the keyboard on it. */
  .tools button::after, .zoom button::after { content: attr(aria-label); position: absolute; top: 50%; z-index: 1; width: max-content;
    max-width: 200px; padding: 6px 10px; border-radius: 8px; font-size: 12px; font-weight: 500; line-height: 1.35; text-align: left;
    color: var(--dv-surface); background: var(--primary-text-color); box-shadow: 0 2px 8px rgba(0, 0, 0, .2);
    pointer-events: none; opacity: 0; visibility: hidden; transform: translateY(-50%); transition: opacity .12s .25s, visibility 0s .25s; }
  .tools button::after { left: calc(100% + 12px); }
  .zoom button::after { top: auto; bottom: calc(100% + 12px); right: 0; transform: none; }
  .seg button[aria-pressed="true"]::after { display: none; }   /* its drawer says it now */
  .tools button:focus-visible::after, .zoom button:focus-visible::after { opacity: 1; visibility: visible; }
  @media (hover: hover) { .tools button:hover::after, .zoom button:hover::after { opacity: 1; visibility: visible; } }

  /* The drawer: opens under the map, so the map and its buttons stay in view. Its head says what it is and closes it;
     an offer to clean has its buttons at the foot, the one that starts it filled. */
  .panel { container-type: inline-size; display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 12px; align-items: center;
    padding: 14px 16px 16px; border-top: 1px solid var(--divider-color);
    background: color-mix(in srgb, var(--primary-color) 4%, var(--dv-surface)); animation: dv-open .22s var(--dv-ease); }
  .panel > * { grid-column: 1 / -1; min-width: 0; }
  @keyframes dv-open { from { opacity: 0; transform: translateY(-6px); } }
  .panel .msg { font-size: 15px; font-weight: 600; line-height: 1.35; color: var(--primary-text-color); text-wrap: pretty; }
  .panel .msg:empty { display: none; }
  .panel .msg[data-error="true"] { display: flex; gap: 8px; font-size: 13px; font-weight: 500; color: var(--error-color, #db4437);
    padding: 10px 12px; border-radius: 12px; background: color-mix(in srgb, var(--error-color, #db4437) 10%, transparent); }
  .panel .msg[data-error="true"]::before { content: "!"; flex: none; display: grid; place-items: center; width: 18px; height: 18px;
    border-radius: 50%; font-size: 12px; font-weight: 700; color: var(--dv-surface); background: var(--error-color, #db4437); }
  .panel button { font: inherit; font-size: 14px; font-weight: 500; color: var(--primary-text-color); background: none; cursor: pointer;
    border: 0; border-radius: 999px; padding: 0 16px; min-height: 40px; transition: background-color .15s, color .15s, transform .15s var(--dv-ease); }
  .panel button:hover { background: var(--dv-fill); }
  .panel .actions { display: flex; flex-wrap: wrap; align-items: center; justify-content: flex-end; gap: 8px; }
  .panel .actions button { background: var(--dv-fill); }
  .panel .actions button:hover { background: var(--dv-fill-hover); }
  .panel .actions .undo { margin-right: auto; padding: 0 12px; color: var(--secondary-text-color); background: none; }
  .panel .actions .second { color: var(--primary-text-color); background: var(--dv-tonal); }
  .panel .actions .second:hover { background: color-mix(in srgb, var(--primary-color) 26%, transparent); }
  .panel .actions button.primary { min-height: 44px; padding: 0 24px; font-weight: 600; color: var(--text-primary-color, #fff);
    background: var(--primary-color); }
  .panel .actions button.primary:hover { background: color-mix(in srgb, var(--primary-color) 88%, var(--primary-text-color)); }
  .panel .actions button:active { transform: scale(.97); }
  .panel .actions button:disabled { opacity: .6; cursor: default; transform: none; }
  .panel .actions:has(.primary:not([hidden])) :is(.cancel, .second) { min-height: 44px; }
  /* On a phone the button that starts it takes the width; with a second thing to do, a row of its own. */
  @container (max-width: 420px) {
    .panel .actions button.primary { flex: 1 1 0; }
    .panel .actions:has(.second:not([hidden])) button { flex: 1 1 0; }
    .panel .actions:has(.second:not([hidden])) button.primary { flex-basis: 100%; order: -1; }
    .panel .actions .undo { flex: 1 1 100%; margin: 0; text-align: left; min-height: 36px; }
  }
  /* Nothing to start, only to close: Close sits in the drawer's head, beside what it says. */
  .panel:not(:has(.actions > :not(.cancel):not([hidden]))) > .msg { grid-column: 1; grid-row: 1; }
  .panel:not(:has(.actions > :not(.cancel):not([hidden]))) > .actions { grid-column: 2; grid-row: 1; align-self: start; }
  .panel:not(:has(.actions > :not(.cancel):not([hidden]))) .cancel { min-height: 36px; padding: 0 14px; font-size: 13px; }
  /* Past cleanings: rows to play again. */
  .panel .list { display: grid; gap: 6px; max-height: 232px; overflow-y: auto; }
  .panel .list button { display: grid; grid-template-columns: auto minmax(0, 1fr); column-gap: 12px; row-gap: 1px; align-items: center;
    text-align: left; border-radius: 12px; padding: 8px 14px 8px 8px; min-height: 52px; background: var(--dv-fill); }
  .panel .list button:hover { background: var(--dv-fill-hover); }
  .panel .list button[data-start]::before { content: ""; grid-row: 1 / 3; width: 36px; height: 36px;
    background: var(--primary-color); -webkit-mask: var(--dv-play) center / contain no-repeat; mask: var(--dv-play) center / contain no-repeat; }
  .panel .list button:disabled { color: var(--secondary-text-color); cursor: default; background: none; }
  .panel .list button:disabled::before { background: var(--disabled-text-color, #bdbdbd); }
  .panel .list .when { font-weight: 600; align-self: end; }
  .panel .list .when::first-letter { text-transform: uppercase; }
  .panel .list .what { font-size: 12px; color: var(--secondary-text-color); align-self: start; overflow: hidden; text-overflow: ellipsis;
    white-space: nowrap; }
  /* The dock's actions: tiles to tap, the icon above what it does. */
  .panel .list:has([data-station-entity]) { grid-template-columns: repeat(auto-fit, minmax(84px, 1fr)); gap: 8px; max-height: none; overflow: visible; }
  .panel .list [data-station-entity] { grid-template-columns: minmax(0, 1fr); justify-items: center; align-content: center; row-gap: 8px;
    text-align: center; min-height: 92px; padding: 12px 8px; border-radius: 16px; line-height: 1.25; background: var(--dv-fill); }
  .panel .list [data-station-entity][data-busy] { background: var(--dv-tonal); box-shadow: inset 0 0 0 1.5px var(--primary-color); }
  .panel .list [data-station-entity]:active { transform: scale(.97); }
  .panel .list .ico { display: grid; place-items: center; width: 40px; height: 40px; border-radius: 50%; font-size: 20px; line-height: 1;
    background: var(--dv-surface); box-shadow: 0 1px 2px rgba(0, 0, 0, .12); }
  /* Chips: the coverage switch, and the settings an offer will clean with. */
  .panel button.coverage, .panel button.tune { justify-self: start; display: inline-flex; align-items: center; gap: 8px; max-width: 100%;
    min-height: 36px; padding: 0 14px; font-size: 13px; background: var(--dv-fill); }
  .panel button.coverage:hover, .panel button.tune:hover { background: var(--dv-fill-hover); }
  .panel button.coverage::before { content: ""; width: 14px; height: 14px; border-radius: 50%; box-sizing: border-box;
    border: 2px solid var(--secondary-text-color); }
  .panel button.coverage[aria-pressed="true"] { color: var(--text-primary-color, #fff); background: var(--primary-color); }
  .panel button.coverage[aria-pressed="true"]::before { border-color: currentColor; background: currentColor; box-shadow: inset 0 0 0 2px var(--primary-color); }
  .panel .tune svg { width: 18px; height: 18px; fill: currentColor; flex: none; color: var(--secondary-text-color); }
  .panel .tune span { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .panel .tune::after { content: ""; flex: none; width: 6px; height: 6px; margin: -3px 2px 0 0; border: solid var(--secondary-text-color);
    border-width: 0 2px 2px 0; transform: rotate(45deg); transition: transform .2s var(--dv-ease); }
  .panel .tune[aria-expanded="true"] { background: var(--dv-tonal); }
  .panel .tune[aria-expanded="true"]::after { transform: translateY(3px) rotate(225deg); }
  .panel .opts { display: grid; gap: 10px; }
  /* A setting: its name over its options, beside them when the card is wide. */
  .opts .field { display: grid; gap: 6px; }
  @container (min-width: 520px) { .opts .field { grid-template-columns: 88px 1fr; align-items: center; gap: 12px; } }
  .opts .name { font-size: 13px; font-weight: 500; color: var(--secondary-text-color); }
  /* The options: a track with a thumb that slides to the one picked, as on a dial. The thumb is a tint of the accent,
     so the picked option's name stays in the text colour: easy to read on any theme. */
  .opts .row { position: relative; display: grid; grid-auto-flow: column; grid-auto-columns: 1fr; padding: 3px; border-radius: 14px;
    background: var(--dv-fill); isolation: isolate; }
  .opts .row::before { content: ""; position: absolute; z-index: -1; top: 3px; bottom: 3px; border-radius: 11px;
    background: color-mix(in srgb, var(--primary-color) 24%, var(--dv-surface));
    width: calc((100% - 6px) / var(--n, 1)); left: calc(3px + (100% - 6px) / var(--n, 1) * var(--i, 0));
    box-shadow: inset 0 0 0 1.5px color-mix(in srgb, var(--primary-color) 70%, transparent), 0 1px 3px rgba(0, 0, 0, .14);
    transition: left .28s cubic-bezier(.3, 1.2, .4, 1); }
  .opts .row:not([style*="--i"])::before { opacity: 0; }
  .opts .row button { display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 4px; min-width: 0; min-height: 44px;
    padding: 6px 4px; border-radius: 11px; font-size: 13px; font-weight: 500; color: var(--secondary-text-color); background: none; }
  .opts .row button:hover { background: none; }
  .opts .row button[aria-checked="true"] { color: var(--primary-text-color); font-weight: 600; }
  .opts .row button:disabled { opacity: .4; cursor: default; }
  .opts .row button:not(:disabled):not([aria-checked="true"]):hover { color: var(--primary-text-color); }
  .opts .row button:focus-visible { outline-offset: -2px; }
  .opts .row span { line-height: 1.2; text-align: center; overflow-wrap: anywhere; }
  /* The level meter: one bar per step up to this one, rising, so the steps read at a glance from across the room. */
  .opts .meter { display: flex; align-items: flex-end; gap: 3px; height: 14px; }
  .opts .meter i { width: 4px; border-radius: 2px; background: currentColor; opacity: .28; }
  .opts .meter i[data-on] { opacity: 1; }
  .opts [aria-checked="true"] .meter i[data-on] { background: var(--primary-color); }
  .opts .note { margin: 0; font-size: 12px; line-height: 1.4; color: var(--secondary-text-color); }

  /* The routines: one tap starts one. */
  .routines { display: flex; flex-wrap: wrap; gap: 8px; padding: 12px 16px; border-top: 1px solid var(--divider-color); }
  .routines button { display: inline-flex; align-items: center; gap: 8px; font: inherit; font-size: 13px; font-weight: 500;
    color: var(--primary-text-color); background: var(--dv-fill); border: 0; border-radius: 999px; padding: 0 16px 0 12px; min-height: 40px;
    cursor: pointer; transition: background-color .15s, transform .15s var(--dv-ease); }
  .routines button::before { content: ""; flex: none; width: 0; height: 0; border: solid transparent; border-width: 5px 0 5px 8px;
    border-left-color: var(--primary-color); }
  .routines button:hover { background: var(--dv-fill-hover); }
  .routines button:active { transform: scale(.97); }
  .routines button:disabled { color: var(--disabled-text-color, #bdbdbd); cursor: default; background: none; transform: none;
    box-shadow: inset 0 0 0 1px var(--divider-color); }
  .routines button:disabled::before { border-left-color: currentColor; }

  @media (prefers-reduced-motion: reduce) {
    .panel, .chip::before, .status::before { animation: none !important; }
    .opts .row::before, .panel .tune::after, button, .seg button::after { transition: none !important; }
    button:active { transform: none !important; }
  }
`;

/** Make node `a` the same as `b`, keeping a's nodes where they have the same shape; where not, b's replace them. */
function patch(a, b) {
  if (a.nodeName !== b.nodeName || a.childNodes.length !== b.childNodes.length) {
    a.replaceWith(b);
    return;
  }
  if (a.nodeType === Node.TEXT_NODE) {
    if (a.data !== b.data) a.data = b.data;
    return;
  }
  for (const { name } of [...a.attributes]) if (!b.hasAttribute(name)) a.removeAttribute(name);
  for (const { name, value } of b.attributes) if (a.getAttribute(name) !== value) a.setAttribute(name, value);
  [...b.childNodes].forEach((c, i) => patch(a.childNodes[i], c));
}

class DreameVacuum3DCard extends HTMLElement {
  _seq = 0;   // bumped as the panel changes: an answer that comes after is stale

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
    const fx = config.effects ?? true;
    if (typeof fx !== 'boolean' && (typeof fx !== 'object' || fx === null || Array.isArray(fx))) {
      throw new Error('"effects" must be true, false, or which to show: { dust: false } or { wet: false }');
    }
    const effects = typeof fx === 'boolean' ? { dust: fx, wet: fx } : { dust: fx.dust !== false, wet: fx.wet !== false };
    if (config.controls != null && typeof config.controls !== 'boolean') throw new Error('"controls" must be true or false');
    const routines = config.routines ?? [];
    if (!Array.isArray(routines) || !routines.every((r) => Array.isArray(r?.rooms) && r.rooms.length
      && r.rooms.every((x) => typeof x === 'number' || (typeof x === 'string' && x.trim())))) {
      throw new Error('"routines" must be a list, each with the "rooms" to clean: names or numbers');
    }
    const other = this._config && this._config.entity !== config.entity;
    this._config = {
      ...config, height, dryTime, routines, effects, view: config.view ?? '3d', labels: config.labels !== false, model: normalizeModel(config.model),
    };
    if (this._sub && this._subEntity !== config.entity) this._unsubscribe();
    if (other) {   // another robot: nothing of the last one's
      if (this._els) this._pick(null);
      this._map = this._cleanings = this._coverage = this._relatedOf = null;
      this._stuck = false;
      this._repeats = 1;
      this._scene?.setCoverage(null);
    }
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
    if (this._shownLang !== this._lang()) {
      this._shownLang = this._lang();
      this._renderButtons();
    }
    this._renderHead();
  }

  get hass() { return this._hass; }

  getCardSize() { return Math.ceil((this._config?.height ?? 420) / 50) + 1; }

  connectedCallback() {
    this._mountScene();
    if (this._hass) this._subscribe();
  }

  disconnectedCallback() {
    this._unmountScene();
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
        <div class="head"><div class="about"><div class="title"></div><div class="chip" role="status"></div></div>
          <div class="ctl"><button type="button" data-cmd="toggle"><svg viewBox="0 0 24 24"><path></path></svg></button>
          <button type="button" data-cmd="return_to_base"><svg viewBox="0 0 24 24"><path d="${ICONS.dock}"></path></svg></button></div></div>
        <div class="viewport">
          <div class="seg views">
            <button type="button" data-view="3d"></button>
            <button type="button" data-view="plan"></button>
            <button type="button" data-follow></button>
          </div>
          <div class="seg zoom">
            <button type="button" data-zoom="in">${svg('plus')}</button>
            <button type="button" data-zoom="out">${svg('minus')}</button>
            <button type="button" data-download>${svg('download')}</button>
          </div>
          <div class="seg tools">
            <button type="button" data-zone>${svg('zone')}</button>
            <button type="button" data-point>${svg('point')}</button>
            <button type="button" data-history aria-pressed="false">${svg('history')}</button>
            <button type="button" data-station aria-pressed="false" hidden>${svg('station')}</button>
            <button type="button" data-settings aria-pressed="false" hidden>${svg('settings')}</button>
          </div>
          <div class="status"></div>
        </div>
        <div class="panel" hidden>
          <div class="msg" aria-live="polite"></div>
          <div class="list" hidden></div>
          <button type="button" class="tune" hidden><svg viewBox="0 0 24 24"><path d="${ICONS.settings}"></path></svg><span></span></button>
          <div class="opts" hidden></div>
          <button type="button" class="coverage" hidden></button>
          <div class="actions">
            <button type="button" class="undo" hidden></button>
            <button type="button" class="cancel"></button>
            <button type="button" class="second" hidden></button>
            <button type="button" class="primary clean"></button>
          </div>
        </div>
        <div class="routines" hidden></div>
      </ha-card>`;
    this._root = root;
    const $ = (s) => root.querySelector(s);
    this._els = { title: $('.title'), chip: $('.chip'), viewport: $('.viewport'), status: $('.status'), panel: $('.panel'),
      msg: $('.panel .msg'), clean: $('.panel .clean'), cancel: $('.panel .cancel'), undo: $('.panel .undo'), routines: $('.routines'),
      list: $('.panel .list'), coverage: $('.panel .coverage'), opts: $('.panel .opts'), tune: $('.panel .tune'),
      settings: $('[data-settings]'), history: $('[data-history]'), station: $('[data-station]'), second: $('.panel .second') };
    root.querySelectorAll('[data-view]').forEach((b) => b.addEventListener('click', () => {
      this._view = b.dataset.view;
      this._follow = false;
      this._scene?.setFollow(false);
      this._scene?.setView(this._view);
      this._renderButtons();
    }));
    root.querySelector('[data-follow]').addEventListener('click', () => {
      this._follow = !this._follow;
      this._scene?.setFollow(this._follow);
      this._renderButtons();
    });
    root.querySelectorAll('[data-zoom]').forEach((b) => b.addEventListener('click', () => this._scene?.zoom(b.dataset.zoom === 'in' ? ZOOM_STEP : 1 / ZOOM_STEP)));
    root.querySelector('[data-download]').addEventListener('click', () => this._download());
    root.querySelector('[data-zone]').addEventListener('click', () => this._setZoneMode(!this._zoneMode));
    root.querySelector('[data-point]').addEventListener('click', () => this._setPointMode(!this._pointMode));
    this._els.second.addEventListener('click', () => this._second?.());
    this._els.history.addEventListener('click', () => {   // again closes the list; from a replay it goes back to the list
      if (this._els.history.getAttribute('aria-pressed') === 'true' && !this._els.list.hidden) this._pick(null);
      else this._showHistory();
    });
    this._els.settings.addEventListener('click', () => {
      if (this._els.settings.getAttribute('aria-pressed') === 'true') this._pick(null);
      else this._showSettings();
    });
    this._els.station.addEventListener('click', () => {
      if (this._els.station.getAttribute('aria-pressed') === 'true') this._pick(null);
      else this._showStation();
    });
    this._els.tune.addEventListener('click', () => {
      this._els.opts.hidden = !this._els.opts.hidden;
      this._renderSettings();
    });
    this._els.opts.addEventListener('click', (e) => {
      const b = e.target.closest('button[data-option]');
      if (b && !b.disabled) this._setOption(b.closest('[data-key]').dataset.key, b.dataset.option);
    });
    this._els.opts.addEventListener('keydown', (e) => this._arrowKeys(e));
    this._els.list.addEventListener('click', (e) => {
      const station = e.target.closest('button[data-station-entity]');
      if (station) { this._stationAction(station); return; }
      const b = e.target.closest('button[data-start]');
      if (b) this._replay(Number(b.dataset.start));
    });
    this._els.coverage.addEventListener('click', () => this._toggleCoverage());
    this._els.cancel.addEventListener('click', () => this._pick(null));
    this._els.undo.addEventListener('click', () => this._undoZone());
    this._els.clean.addEventListener('click', () => this._clean());
    root.querySelectorAll('[data-cmd]').forEach((b) => b.addEventListener('click', () => this._command(b.dataset.cmd)));
    this._els.routines.addEventListener('click', (e) => {
      const b = e.target.closest('button[data-routine]');
      if (b) this._runRoutine(Number(b.dataset.routine));
    });
    this._applyConfig();
    this._mountScene();
  }

  /** WebGL contexts are few per page: the scene lives only while the card is on the page, and draws only while it shows. */
  _mountScene() {
    if (this._scene || !this._root || !this.isConnected || this._noWebgl) return;
    try {
      this._scene = new VacuumScene(this._els.viewport);
    } catch {
      this._noWebgl = true;
      this._status(this._t('noWebgl'));
      return;
    }
    const s = this._scene;
    s.onRoomTap = (id) => this._pick(id);
    s.onZone = (rect) => this._offerZone(rect);
    s.onPoint = (at) => this._offerPoint(at);
    s.onObstacleTap = (o) => this._offerObstacle(o);
    s.onModelError = (err) => this._status(this._t('modelError', { error: err.message }));
    this._configureScene();
    s.setView(this._view, true);
    s.setFollow(!!this._follow);
    if (this._hass) {
      this._applyTheme();
      this._renderHead();
    }
    s.setCoverage(this._coverage);
    if (this._map) s.setMap(this._map);
    this._io = new IntersectionObserver(([e]) => (e.isIntersecting ? this._scene?.start() : this._scene?.stop()));
    this._io.observe(this._els.viewport);
  }

  _unmountScene() {
    this._io?.disconnect();
    this._io = null;
    this._scene?.dispose();
    this._scene = null;
    if (this._els) this._pick(null);
  }

  _applyConfig() {
    const c = this._config;
    this._els.viewport.style.height = `${c.height}px`;
    const newView = c.view !== this._configView;   // else the view stays the one the person chose
    if (newView) this._view = this._configView = c.view;
    this._els.title.hidden = c.title === false || c.title === '';
    this._renderButtons();
    this._renderRoutines();
    this._status(this._noWebgl ? this._t('noWebgl') : !c.entity ? this._t('noEntity') : this._map ? '' : this._t('waiting'));
    this._configureScene();
    if (newView) this._scene?.setView(this._view, true);
  }

  _configureScene() {
    const s = this._scene, c = this._config;
    if (!s) return;
    s.setLabels(c.labels);
    s.setLanguage(this._lang());
    s.setDryTime(c.dryTime);
    s.setEffects(c.effects);
    s.setModel(c.model);
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
    r.querySelector('[data-history]').setAttribute('aria-label', this._t('history'));
    r.querySelector('[data-zone]').setAttribute('aria-label', this._t('zone'));
    r.querySelector('[data-zone]').setAttribute('aria-pressed', String(!!this._zoneMode));
    r.querySelector('[data-point]').setAttribute('aria-label', this._t('point'));
    r.querySelector('[data-point]').setAttribute('aria-pressed', String(!!this._pointMode));
    r.querySelector('[data-station]').setAttribute('aria-label', this._t('station'));
    r.querySelector('[data-settings]').setAttribute('aria-label', this._t('settings'));
    r.querySelector('[data-cmd="return_to_base"]').setAttribute('aria-label', this._t('dock'));
    r.querySelector('[data-cmd="return_to_base"]').title = this._t('dock');
  }

  _renderHead() {
    const h = this._hass, st = h.states[this._config.entity];
    this._els.title.textContent = this._config.title || st?.attributes.friendly_name || 'Dreame Vacuum 3D';
    // The robot's own state says more than the vacuum's (washing its mops, not just cleaning); an error says why.
    const detail = this._related('state'), error = this._related('error');
    const format = (s) => h.formatEntityState?.(s) ?? s.state;
    // An error shows whatever the vacuum says: stuck on its way home, the X10+ reports "paused", not "error".
    const failing = !!error && !['no_error', 'unknown', 'unavailable'].includes(error.state);
    const badge = failing || st?.state === 'error' ? BADGES.error : BADGES[detail?.state] ?? null;
    const why = failing ? format(error) : null;
    const doing = why ?? (badge && detail ? format(detail) : null);
    const parts = [];
    if (st) parts.push(doing ?? format(st));
    const room = this._map?.rooms?.[this._map.room]?.name;
    if (room) parts.push(room);
    if (st?.attributes.battery_level != null) parts.push(`${st.attributes.battery_level} %`);
    this._els.chip.textContent = parts.join(' · ');
    this._els.chip.dataset.active = String(['cleaning', 'returning'].includes(st?.state));
    this._els.chip.dataset.warn = String(failing || st?.state === 'error');
    this._scene?.setCleaning(st?.state === 'cleaning');
    this._scene?.setBadge(badge && doing ? `${badge} ${doing}` : null, failing || st?.state === 'error');
    // Where it stopped for an error (a warning while it goes on cleaning isn't where it got stuck).
    const stuck = st?.state === 'error' || (failing && !['cleaning', 'returning', 'docked'].includes(st?.state));
    if (stuck && !this._stuck) this._scene?.markStuck(why ?? format(st));
    this._stuck = stuck;
    // Start, or pause while it cleans or heads home; back to the dock unless it is there.
    const busy = ['cleaning', 'returning'].includes(st?.state);
    const toggle = this._root.querySelector('[data-cmd="toggle"]');
    toggle.querySelector('path').setAttribute('d', busy ? ICONS.pause : ICONS.start);
    toggle.setAttribute('aria-label', this._t(busy ? 'pause' : 'start'));
    toggle.title = this._t(busy ? 'pause' : 'start');
    toggle.disabled = !st;
    this._root.querySelector('[data-cmd="return_to_base"]').disabled = !st || st.state === 'docked' || st.state === 'returning';
    this._els.settings.hidden = this._config.controls === false || !SETTINGS.some((x) => this._related(x.key));
    this._els.station.hidden = this._config.controls === false || !this._stationActions().length;
    this._renderSettings();
    if (this._stationOpen) this._renderStation();
  }

  /** The dock's actions this robot has, each as the button entity it presses now (its stop while the dock does it). */
  _stationActions() {
    const doing = this._related('state')?.state;
    return STATION.flatMap((a) => {
      const stop = a.stop && a.busy.includes(doing) ? this._related(a.stop) : null;
      const entity = stop ?? this._related(a.key);
      return entity ? [{ ...a, entity: entity.entity_id, label: stop ? a.stopLabel : a.label, running: !!stop }] : [];
    });
  }

  /** The dock's actions, from the button in the left column: empty the bin, wash or dry the mop. */
  _showStation() {
    this._pick(null);
    const { panel, msg, clean, cancel } = this._els;
    panel.hidden = false;
    clean.hidden = true;
    msg.textContent = this._t('stationTitle');
    cancel.textContent = this._t('close');
    this._els.station.setAttribute('aria-pressed', 'true');
    this._stationOpen = true;
    this._renderStation();
  }

  _renderStation() {
    const { list } = this._els;
    const actions = this._stationActions();
    const shown = [...list.querySelectorAll('button[data-station-entity]')].map((b) => b.dataset.stationEntity).join();
    if (!list.hidden && shown === actions.map((a) => a.entity).join()) return;   // keep focus on an unchanged list
    list.replaceChildren(...actions.map((a) => {
      const b = document.createElement('button');
      b.type = 'button';
      b.dataset.stationEntity = a.entity;
      b.dataset.label = a.label;
      const icon = Object.assign(document.createElement('span'), { className: 'ico', textContent: a.icon });
      icon.setAttribute('aria-hidden', 'true');
      b.append(icon, ' ', Object.assign(document.createElement('span'), { textContent: this._t(a.label) }));
      if (a.running) b.dataset.busy = '';
      return b;
    }));
    list.hidden = !actions.length;
  }

  async _stationAction(button) {
    const { msg } = this._els;
    const action = this._t(button.dataset.label);
    button.disabled = true;
    try {
      await this._hass.callService('button', 'press', {}, { entity_id: button.dataset.stationEntity });
      msg.dataset.error = 'false';
      msg.textContent = this._t('sent', { action });
    } catch (err) {
      msg.dataset.error = 'true';
      msg.textContent = this._t('commandFailed', { error: err?.message ?? String(err) });
    } finally {
      button.disabled = false;
    }
  }

  /** The settings sheet alone, from the button in the left column. */
  _showSettings() {
    this._pick(null);
    const { panel, msg, clean, cancel, opts } = this._els;
    panel.hidden = false;
    clean.hidden = true;
    opts.hidden = false;
    msg.textContent = this._t('settings');
    cancel.textContent = this._t('close');
    this._els.settings.setAttribute('aria-pressed', 'true');
    this._renderSettings();
  }

  /** The settings rows, each showing the robot's setting now; and the summary of them on the panel's Settings button. */
  _renderSettings() {
    const { opts, tune } = this._els;
    if (!opts || this._config.controls === false || (opts.hidden && tune.hidden)) return;   // nothing of it shows
    const h = this._hass, st = h.states[this._config.entity];
    const label = (s, o) => h.formatEntityState?.(s, o) ?? o.replace(/_/g, ' ');
    const mode = this._related('cleaning_mode')?.state;
    const mop = this._related('mop_attached');
    const cleaning = ['cleaning', 'returning', 'paused'].includes(st?.state);
    const rows = [];
    const summary = [];
    for (const def of SETTINGS) {
      const s = this._related(def.key);
      const options = s?.attributes.options;
      if (!options?.length) continue;
      if (!def.off?.includes(mode)) summary.push(label(s, s.state));
      const row = this._radioRow(def.key, this._t(def.label), options.map((o, i) => ({
        value: o, text: label(s, o), level: def.level ? [i + 1, options.length] : null,
        // The mode is the task's: it can't change while it runs. Mopping needs the pads on.
        disabled: (def.key === 'cleaning_mode' && (cleaning || (mop?.state === 'off' && MOPPING.includes(o)))) || !!def.off?.includes(mode),
        title: def.key === 'cleaning_mode' && mop?.state === 'off' && MOPPING.includes(o) ? this._t('fitMop') : '',
      })), s.state);
      rows.push(row);
    }
    if (rows.length) {
      const passes = [1, 2, 3].map((n) => ({ value: String(n), text: `×${n}`, disabled: cleaning }));
      rows.push(this._radioRow('repeats', this._t('repeats'), passes, String(this._repeats ?? 1)));
      if ((this._repeats ?? 1) > 1) summary.push(`×${this._repeats}`);
      if (cleaning) rows.push(Object.assign(document.createElement('p'), { className: 'note', textContent: this._t('nextCleaning') }));
    } else {
      rows.push(Object.assign(document.createElement('p'), { className: 'note', textContent: this._t('noSettings') }));
    }
    // On every state update: the rows are updated in place, so a click halfway through (or the focus) stays on its button;
    // rebuilt only when they change shape, keeping the focus on the option it was on.
    const focused = this._root.activeElement?.closest?.('[data-option]');
    const where = focused && [focused.closest('[data-key]').dataset.key, focused.dataset.option];
    if (opts.childNodes.length === rows.length) rows.forEach((row, i) => patch(opts.childNodes[i], row));
    else opts.replaceChildren(...rows);
    if (where && !focused.isConnected) opts.querySelector(`[data-key="${where[0]}"] [data-option="${where[1]}"]`)?.focus();
    tune.querySelector('span').textContent = summary.join(' · ') || this._t('settings');
    tune.setAttribute('aria-expanded', String(!opts.hidden));
    tune.setAttribute('aria-label', `${this._t('settings')}: ${summary.join(', ')}`);
  }

  /** A label and a row of options to pick one of (radio buttons), the picked one filled. */
  _radioRow(key, name, options, value) {
    const id = `opt-${key}`;
    const title = Object.assign(document.createElement('span'), { className: 'name', id, textContent: name });
    const row = document.createElement('div');
    row.className = 'row';
    row.dataset.key = key;
    row.setAttribute('role', 'radiogroup');
    row.setAttribute('aria-labelledby', id);
    // The row's one Tab stop: the picked option, or the first that can be picked when that one can't.
    const stop = (options.find((o) => o.value === value && !o.disabled) ?? options.find((o) => !o.disabled))?.value;
    // The thumb under the picked option: --i of --n.
    const at = options.findIndex((o) => o.value === value);
    row.style.setProperty('--n', String(options.length));
    if (at >= 0) row.style.setProperty('--i', String(at));
    row.append(...options.map((o) => {
      const b = document.createElement('button');
      b.type = 'button';
      b.dataset.option = o.value;
      b.setAttribute('role', 'radio');
      b.setAttribute('aria-checked', String(o.value === value));
      b.tabIndex = o.value === stop ? 0 : -1;
      b.disabled = o.disabled;
      if (o.title) b.title = o.title;
      if (o.level) {
        const meter = document.createElement('span');
        meter.className = 'meter';
        meter.setAttribute('aria-hidden', 'true');
        for (let k = 1; k <= o.level[1]; k++) {
          const bar = document.createElement('i');
          bar.style.height = `${(k / o.level[1]) * 100}%`;
          if (k <= o.level[0]) bar.dataset.on = '';
          meter.append(bar);
        }
        b.append(meter);
      }
      b.append(Object.assign(document.createElement('span'), { textContent: o.text }));
      return b;
    }));
    const field = document.createElement('div');
    field.className = 'field';
    field.append(title, row);
    return field;
  }

  /** Arrow keys move between a row's options, as in any radio group. */
  _arrowKeys(e) {
    const step = { ArrowRight: 1, ArrowDown: 1, ArrowLeft: -1, ArrowUp: -1 }[e.key];
    const b = e.target.closest('button[data-option]');
    if (!step || !b) return;
    e.preventDefault();
    const all = [...b.parentElement.querySelectorAll('button:not(:disabled)')];
    const next = all[(all.indexOf(b) + step + all.length) % all.length];
    next?.focus();
    next?.click();
  }

  /** Change one of the robot's settings (it keeps it for the next cleanings too), or the passes of the next task. */
  async _setOption(key, option) {
    if (key === 'repeats') {
      this._repeats = Number(option);
      this._renderSettings();
      return;
    }
    const s = this._related(key);
    if (!s) return;
    const { msg } = this._els;
    try {
      await this._hass.callService('select', 'select_option', { option }, { entity_id: s.entity_id });
      if (this._beforeError != null && msg.dataset.error === 'true') {   // it went through: back to what the panel said
        msg.dataset.error = 'false';
        msg.textContent = this._beforeError;
      }
      this._beforeError = null;
    } catch (err) {
      if (msg.dataset.error !== 'true') this._beforeError = msg.textContent;
      msg.dataset.error = 'true';
      msg.textContent = this._t('commandFailed', { error: err?.message ?? String(err) });
    }
  }

  /** Another entity of the same robot by its translation key, such as the integration's state or error sensor. */
  _related(key) {
    const h = this._hass, all = h.entities ?? {};
    const device = all[this._config.entity]?.device_id;
    if (!device) return null;
    if (this._relatedOf !== all) { this._relatedOf = all; this._relatedIds = {}; }
    const id = this._relatedIds[key] ??= Object.keys(all).find((e) => all[e].device_id === device && all[e].platform === DOMAIN
      && all[e].translation_key === key) ?? null;
    return id ? h.states[id] ?? null : null;
  }

  async _command(cmd) {
    const st = this._hass.states[this._config.entity];
    const service = cmd === 'toggle' ? (['cleaning', 'returning'].includes(st?.state) ? 'pause' : 'start') : cmd;
    try {
      await this._hass.callService('vacuum', service, {}, { entity_id: this._config.entity });
      if (service === 'start') this._repeats = 1;   // as after any cleaning started: the passes were for one task
    } catch (err) {
      this._pick(null);
      const { panel, msg, clean, cancel } = this._els;
      panel.hidden = false;
      clean.hidden = true;
      cancel.textContent = this._t('close');
      msg.dataset.error = 'true';
      msg.textContent = this._t('commandFailed', { error: err?.message ?? String(err) });
    }
  }

  _status(text) {
    this._els.status.textContent = text;
    this._els.status.hidden = !text;
    this._els.status.dataset.wait = String(!!text && text === this._t('waiting'));
  }

  _subscribe() {
    const entity = this._config?.entity;
    if (this._sub || !entity || !this._hass?.connection || !this.isConnected) return;
    this._subEntity = entity;
    // A message of the last robot's, on its way out after the entity changed, is not this one's.
    this._sub = this._hass.connection
      .subscribeMessage((map) => entity === this._config.entity && this._onMap(map), { type: `${DOMAIN}/map/subscribe`, entity_id: entity })
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

  /** The first message has everything; later ones carry the rooms only when they changed, and the path's new strokes. */
  _onMap({ path_tail: tail, ...message }) {
    const first = !this._map;
    this._map = { ...this._map, ...message };
    if (tail) this._map.path = appendPath(this._map.path, tail);
    if (!this._noWebgl) this._status('');
    if (!this._replaying) this._scene?.setMap(this._map);   // a replay shows a past cleaning meanwhile
    if (first || 'rooms' in message) this._labelRoutines();
    if (this._hass) this._renderHead();
  }

  _renderRoutines() {
    const box = this._els.routines;
    box.replaceChildren(...this._config.routines.map((_, i) => {
      const b = document.createElement('button');
      b.type = 'button';
      b.dataset.routine = String(i);
      return b;
    }));
    box.hidden = !this._config.routines.length;
    this._labelRoutines();
  }

  /** A routine's button shows its name, or its rooms; it is off until the map has every room it names. */
  _labelRoutines() {
    const rooms = this._map?.rooms ?? {};
    this._els.routines.querySelectorAll('button').forEach((b) => {
      const r = this._config.routines[b.dataset.routine];
      const ids = routineRooms(this._map?.rooms, r.rooms);
      b.textContent = r.name || r.rooms.map((x, i) => rooms[ids[i]]?.name ?? x).join(' + ');
      b.disabled = !this._map || ids.includes(null);
      b.title = b.disabled && this._map ? this._t('missing', { rooms: r.rooms.filter((_, i) => ids[i] == null).join(', ') }) : '';
    });
  }

  async _runRoutine(i) {
    const r = this._config.routines[i];
    const segments = routineRooms(this._map?.rooms, r.rooms);
    if (!this._map || segments.includes(null)) return;
    const data = { segments };
    for (const k of ['repeats', 'suction_level', 'water_volume']) if (r[k] != null) data[k] = r[k];
    const name = this._els.routines.querySelector(`[data-routine="${i}"]`).textContent;
    this._pick(null);
    const { panel, clean, cancel } = this._els;
    panel.hidden = false;
    clean.hidden = true;
    cancel.textContent = this._t('close');
    await this._start(data, name);
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

  /** In zone mode a drag on the floor draws a zone to clean, instead of turning the view; Cancel leaves it. */
  _setZoneMode(on) {
    this._pick(null);
    this._zoneMode = on;
    this._scene?.setZoneMode(on);
    this._renderButtons();
    if (!on) return;
    const { panel, msg, clean, cancel } = this._els;
    panel.hidden = false;
    clean.hidden = true;
    msg.textContent = this._t('drawZone');
    cancel.textContent = this._t('cancel');
  }

  /** In point mode a tap on the floor picks a spot: to clean around it, or to send the robot there. */
  _setPointMode(on) {
    this._pick(null);
    this._pointMode = on;
    this._scene?.setPointMode(on);
    this._renderButtons();
    if (!on) return;
    const { panel, msg, clean, cancel } = this._els;
    panel.hidden = false;
    clean.hidden = true;
    msg.textContent = this._t('pickPoint');
    cancel.textContent = this._t('cancel');
  }

  _offerPoint(at) {
    this._point = at;
    this._scene?.showPoint(at);
    const { msg, clean, cancel, second } = this._els;
    const room = this._map?.rooms?.[roomAt(this._map, ...at)]?.name;
    msg.dataset.error = 'false';
    msg.textContent = room ? this._t('spotPicked', { room }) : this._t('spotFloor');
    cancel.textContent = this._t('cancel');
    clean.hidden = false;
    clean.textContent = this._t('spot');
    second.hidden = false;
    second.textContent = this._t('goHere');
    this._second = () => this._goHere();
    this._offerSettings();
  }

  async _goHere() {
    if (!this._point) return;
    const [x, y] = this._point;
    const { msg, cancel, second } = this._els;
    second.disabled = true;
    try {
      await this._hass.callService(DOMAIN, 'vacuum_goto', { x, y }, { entity_id: this._config.entity });
      msg.dataset.error = 'false';
      msg.textContent = this._t('going');
      cancel.textContent = this._t('close');
    } catch (err) {
      msg.dataset.error = 'true';
      msg.textContent = this._t('failed', { error: err?.message ?? String(err) });
    } finally {
      second.disabled = false;
    }
  }

  /** A tap on an obstacle the robot found: have it go over it from now on, or avoid it again. */
  _offerObstacle(o) {
    this._pick(null);
    const { panel, msg, clean, cancel } = this._els;
    const name = thingLabel(o.type, this._lang());
    panel.hidden = false;
    msg.dataset.error = 'false';
    msg.textContent = this._t(o.ignored ? 'ignored' : 'avoided', { thing: name });
    cancel.textContent = this._t('close');
    clean.hidden = false;
    clean.textContent = this._t(o.ignored ? 'avoid' : 'ignore');
    this._obstacle = o;
  }

  async _ignoreObstacle() {
    const o = this._obstacle;
    if (!o) return;
    const { msg, clean } = this._els;
    clean.disabled = true;
    try {
      await this._hass.callService(DOMAIN, 'vacuum_set_obstacle_ignore', { x: Math.trunc(o.x), y: Math.trunc(o.y), obstacle_ignored: !o.ignored },
        { entity_id: this._config.entity });
      o.ignored = !o.ignored;
      msg.dataset.error = 'false';
      msg.textContent = this._t(o.ignored ? 'ignored' : 'avoided', { thing: thingLabel(o.type, this._lang()) });
      clean.textContent = this._t(o.ignored ? 'avoid' : 'ignore');
      if (this._map) this._scene?.setMap({ ...this._map, obstacles: [...(this._map.obstacles ?? [])] });
    } catch (err) {
      msg.dataset.error = 'true';
      msg.textContent = this._t('commandFailed', { error: err?.message ?? String(err) });
    } finally {
      clean.disabled = false;
    }
  }

  /** A zone drawn joins the others drawn since zone mode began, up to MAX_ZONES. */
  _offerZone(rect) {
    const zones = this._zones ?? [];
    if (zones.length >= MAX_ZONES) {
      this._scene.setZones(zones);
      this._els.msg.dataset.error = 'true';
      this._els.msg.textContent = this._t('maxZones', { n: MAX_ZONES });
      return;
    }
    this._zones = [...zones, rect];
    this._renderZones();
  }

  _undoZone() {
    this._zones = (this._zones ?? []).slice(0, -1);
    this._renderZones();
  }

  /** The zones drawn, on the floor and on the panel: their sizes, and the button that cleans them. */
  _renderZones() {
    const zones = this._zones ?? [];
    this._scene?.setZones(zones);
    const { panel, msg, clean, cancel, undo } = this._els;
    const m = (mm) => (mm / 1000).toLocaleString(this._lang(), { maximumFractionDigits: 1 });
    const size = (r) => `${m(r[2] - r[0])} × ${m(r[3] - r[1])} m`;
    panel.hidden = false;
    msg.dataset.error = 'false';
    cancel.textContent = this._t('cancel');
    undo.textContent = this._t('undo');
    undo.hidden = !zones.length;
    if (!zones.length) {
      msg.textContent = this._t('drawZone');
      clean.hidden = true;
      this._els.tune.hidden = true;
      this._els.opts.hidden = true;
      return;
    }
    const [one] = zones;
    msg.textContent = zones.length === 1
      ? this._t('zoneSize', { w: m(one[2] - one[0]), h: m(one[3] - one[1]) })
      : this._t('zones', { n: zones.length, sizes: zones.map(size).join(' · ') });
    clean.hidden = false;
    clean.textContent = zones.length === 1 ? this._t('cleanZone') : this._t('cleanZones', { n: zones.length });
    this._offerSettings();
  }

  /** The last cleanings, to replay one, and the coverage of the last few. */
  async _showHistory() {
    this._pick(null);
    const { panel, msg, list, coverage, clean, cancel } = this._els;
    panel.hidden = false;
    clean.hidden = true;
    cancel.textContent = this._t('close');
    this._els.history.setAttribute('aria-pressed', 'true');
    const seq = this._seq;
    let cleanings;
    try {
      ({ cleanings } = await this._hass.callWS({ type: `${DOMAIN}/history`, entity_id: this._config.entity }));
    } catch (err) {
      if (seq !== this._seq) return;
      msg.dataset.error = 'true';
      msg.textContent = this._t('noReplay', { error: err?.message ?? String(err) });
      return;
    }
    if (seq !== this._seq) return;   // the panel closed, or shows something else, meanwhile
    this._cleanings = cleanings;
    msg.textContent = this._t(cleanings.length ? 'lastCleanings' : 'noHistory');
    const rooms = this._map?.rooms ?? {};
    const when = (s) => new Date(s * 1000).toLocaleString(this._lang(), { weekday: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
    list.replaceChildren(...cleanings.slice(0, 10).map((c) => {
      const b = document.createElement('button');
      b.type = 'button';
      b.dataset.start = String(c.start);
      b.disabled = !c.has_map;
      const where = c.rooms.length ? c.rooms.map((r) => rooms[r]?.name ?? `#${r}`).join(', ') : this._t('whole');
      // When on the first line; how long, how much and where under it.
      const what = [c.duration != null && `${c.duration} min`, c.area != null && `${c.area} m²`, where].filter(Boolean).join(', ');
      b.append(Object.assign(document.createElement('span'), { className: 'when', textContent: when(c.start) }), ' ',
        Object.assign(document.createElement('span'), { className: 'what', textContent: what }));
      return b;
    }));
    list.hidden = !cleanings.length;
    coverage.hidden = !cleanings.some((c) => c.has_map);
    coverage.textContent = this._t('coverage');
    coverage.setAttribute('aria-pressed', String(!!this._coverage));
  }

  async _path(start) {
    return this._hass.callWS({ type: `${DOMAIN}/history/path`, entity_id: this._config.entity, start });
  }

  /** Play a past cleaning back on the map, REPLAY_SPEED times faster than the robot went. */
  async _replay(start) {
    const { msg, list, cancel } = this._els;
    const when = new Date(start * 1000).toLocaleString(this._lang(), { weekday: 'long', hour: '2-digit', minute: '2-digit' });
    const seq = ++this._seq;   // a later tap, or closing the panel, makes this one stale
    let past;
    try {
      past = await this._path(start);
    } catch (err) {
      if (seq !== this._seq) return;
      msg.dataset.error = 'true';
      msg.textContent = this._t('noReplay', { error: err?.message ?? String(err) });
      return;
    }
    if (seq !== this._seq) return;
    if (this._map && past.map_id !== this._map.map_id) {
      msg.dataset.error = 'true';
      msg.textContent = this._t('otherMap');
      return;
    }
    this._stopReplay();
    list.hidden = true;
    msg.dataset.error = 'false';
    msg.textContent = this._t('replaying', { when, speed: REPLAY_SPEED });
    cancel.textContent = this._t('live');
    const points = past.path.flatMap((s) => s.points.map((p) => [...p, s.mode]));
    const length = points.slice(1).reduce((d, p, i) => d + Math.hypot(p[0] - points[i][0], p[1] - points[i][1]), 0);
    const dur = Math.min(90000, Math.max(8000, length / 0.3 / REPLAY_SPEED));   // the robot does about 0.3 m/s
    const t0 = performance.now();
    const frame = () => {
      const s = Math.min(1, (performance.now() - t0) / dur), n = Math.max(2, Math.round(s * points.length));
      // The path as far as it had gone, in its strokes.
      const path = [];
      for (const [x, y, mode] of points.slice(0, n)) {
        if (path.at(-1)?.mode !== mode) path.push({ mode, points: path.length ? [path.at(-1).points.at(-1)] : [] });
        path.at(-1).points.push([x, y]);
      }
      const [a, b] = [points[n - 2], points[n - 1]];
      const angle = Math.round((Math.atan2(b[1] - a[1], b[0] - a[0]) * 180) / Math.PI);
      this._scene?.setMap({ ...this._map, path, robot: [b[0], b[1], angle], active: past.rooms, room: null, obstacles: [], new_cells: [] });
      if (s >= 1) {
        clearInterval(this._replaying);
        this._replaying = -1;   // finished: the map stays on the end of the replay until Live
        msg.textContent = this._t('replayed', { when });
      }
    };
    this._replaying = setInterval(frame, 120);
    frame();
  }

  _stopReplay() {
    if (!this._replaying) return;
    clearInterval(this._replaying);
    this._replaying = null;
    if (this._map) this._scene?.setMap(this._map);
  }

  async _toggleCoverage() {
    const { coverage } = this._els;
    if (this._coverageBusy) return;   // still fetching the last ones: a second tap would fetch them again
    if (this._coverage) {
      this._coverage = null;
      this._scene?.setCoverage(null);
    } else {
      const starts = (this._cleanings ?? []).filter((c) => c.has_map && c.map_id === this._map?.map_id).slice(0, COVERAGE);
      const paths = [];
      this._coverageBusy = true;
      try {
        for (const c of starts) paths.push((await this._path(c.start).catch(() => null))?.path ?? []);
      } finally {
        this._coverageBusy = false;
      }
      this._coverage = paths;
      this._scene?.setCoverage(paths);
    }
    coverage.setAttribute('aria-pressed', String(!!this._coverage));
  }

  /** A tap on a room offers to clean it; a tap elsewhere or Cancel closes the offer (and leaves zone mode). */
  _pick(id) {
    this._seq++;   // whatever the panel was waiting for is stale now
    this._stopReplay();
    if (this._els) {
      this._els.list.hidden = true;
      this._els.coverage.hidden = true;
      this._els.opts.hidden = true;
      this._els.tune.hidden = true;
      this._els.settings.setAttribute('aria-pressed', 'false');
      this._els.station.setAttribute('aria-pressed', 'false');
      this._els.history.setAttribute('aria-pressed', 'false');
      this._stationOpen = false;
    }
    if (id == null && this._zoneMode) {
      this._zoneMode = false;
      this._zones = [];
      this._scene?.setZoneMode(false);
      if (this._root) this._renderButtons();
    }
    if (id == null && this._pointMode) {
      this._pointMode = false;
      this._point = null;
      this._scene?.setPointMode(false);
      if (this._root) this._renderButtons();
    }
    this._second = null;
    this._obstacle = null;
    if (this._els) {
      this._els.undo.hidden = true;
      this._els.second.hidden = true;
    }
    // A tap on a room adds it to the rooms to clean, or takes it off again; anywhere else clears them.
    const rooms = this._map?.rooms ?? {};
    const picked = (this._picked ?? []).filter((r) => rooms[r]);
    const key = id != null && rooms[id] ? String(id) : null;
    this._picked = key == null ? [] : picked.includes(key) ? picked.filter((r) => r !== key) : [...picked, key];
    this._scene?.select(this._picked);
    const { panel, msg, clean, cancel } = this._els;
    panel.hidden = !this._picked.length;
    msg.dataset.error = 'false';
    msg.textContent = '';
    if (!this._picked.length) return;
    const name = (r) => rooms[r]?.name ?? `#${r}`;
    clean.hidden = false;
    if (this._picked.length === 1) {
      clean.textContent = this._t('clean', { room: name(this._picked[0]) });
    } else {
      msg.textContent = this._t('rooms', { rooms: this._picked.map(name).join(' → ') });
      clean.textContent = this._t('cleanRooms', { n: this._picked.length });
    }
    cancel.textContent = this._t('cancel');
    this._offerSettings();
  }

  /** On an offer to clean, the settings it will clean with, as a button that opens them. */
  _offerSettings() {
    const { tune, settings } = this._els;
    tune.hidden = settings.hidden;
    this._renderSettings();
  }

  async _clean() {
    if (this._obstacle) { await this._ignoreObstacle(); return; }
    if (this._point) { await this._cleanSpot(); return; }
    const ids = this._picked ?? [], zones = this._zones ?? [];
    if (!ids.length && !zones.length) return;
    const { clean } = this._els;
    clean.disabled = true;
    try {
      const repeats = (this._repeats ?? 1) > 1 ? { repeats: this._repeats } : {};
      const name = (r) => this._map.rooms[r]?.name ?? `#${r}`;
      // One call for them all: the robot cleans the rooms in the order they were picked, the zones as drawn.
      const started = zones.length
        ? await this._start({ zone: zones, ...repeats }, zones.length === 1 ? this._t('theZone') : this._t('theZones', { n: zones.length }), 'vacuum_clean_zone')
        : await this._start({ segments: ids.map(Number), ...repeats }, ids.map(name).join(', '));
      if (started) {
        // That task has its rooms and zones: a tap or a drag now starts a new pick (the zones stay drawn till closed).
        this._picked = [];
        this._zones = [];
        clean.hidden = true;
        this._els.undo.hidden = true;
        this._els.tune.hidden = true;
        this._els.opts.hidden = true;
      }
    } finally {
      clean.disabled = false;
    }
  }

  async _cleanSpot() {
    const { clean, second } = this._els;
    clean.disabled = true;
    try {
      const repeats = (this._repeats ?? 1) > 1 ? { repeats: this._repeats } : {};
      if (await this._start({ points: [this._point], ...repeats }, this._t('theSpot'), 'vacuum_clean_spot')) {
        clean.hidden = true;
        second.hidden = true;
        this._els.tune.hidden = true;
        this._els.opts.hidden = true;
      }
    } finally {
      clean.disabled = false;
    }
  }

  /** Start cleaning rooms (or a zone) and say so on the panel; true if the robot took it. */
  async _start(data, name, service = 'vacuum_clean_segment') {
    const { msg, cancel } = this._els;
    try {
      await this._hass.callService(DOMAIN, service, data, { entity_id: this._config.entity });
      msg.dataset.error = 'false';
      msg.textContent = this._t('started', { room: name });
      cancel.textContent = this._t('close');
      this._repeats = 1;   // the passes are for that task: the next one-tap cleaning goes once
      return true;
    } catch (err) {
      msg.dataset.error = 'true';
      msg.textContent = this._t('failed', { error: err?.message ?? String(err) });
      return false;
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
