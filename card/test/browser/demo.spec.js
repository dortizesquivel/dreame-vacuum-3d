// The card in the demo, with its simulated robot: what a person would see, checked through the card's own state.
import { expect, test } from '@playwright/test';

/** Open the demo and wait for the card to have a map; collects the page's errors. */
async function open(page, query = '') {
  const errors = [];
  page.on('pageerror', (e) => errors.push(e.message));
  page.on('console', (m) => { if (m.type() === 'error' && !m.text().includes('favicon')) errors.push(m.text()); });
  // speed=0: the demo's own timer leaves the robot still; the tests move it.
  await page.goto(`/card/demo/?height=480&speed=0${query}`);
  await page.waitForFunction(() => window.demo?.card?._map?.rooms && window.demo.card._scene?.robot.visible);
  return errors;
}

/** Run the simulated robot for `seconds` of its time, sending each step to the card as the integration would. */
const run = (page, seconds, step = 0.5) => page.evaluate(async ([seconds, step]) => {
  const { sim, send } = window.demo;
  for (let t = 0; t < seconds; t += step) {
    sim.step(step);
    send();
    if (Math.round(t / step) % 20 === 0) await new Promise((r) => setTimeout(r, 5));
  }
}, [seconds, step]);

for (const house of ['living-room', 'flat']) {
  test(`the ${house} loads, with its rooms and no errors`, async ({ page }) => {
    const errors = await open(page, `&house=${house}`);
    const rooms = await page.evaluate(() => Object.values(window.demo.card._map.rooms).map((r) => r.name));
    expect(rooms.length).toBeGreaterThan(0);
    await expect(page.locator('dreame-vacuum-3d-card canvas')).toHaveCount(1);
    expect(errors).toEqual([]);
  });
}

test('the robot goes round the furniture, not through it', async ({ page }) => {
  test.setTimeout(300_000);   // 600 looks at the robot: about two minutes on a CI runner's software WebGL, sometimes more
  await open(page, '&start');
  // The living room's furniture (model metres) and how close the robot's centre may come (its radius is 0.175).
  const hits = await page.evaluate(async () => {
    const blocked = { hearth: [1.17, 0, 3.81, 0.82], sofa: [0.76, 3.64, 3.7, 4.64], table: [1.47, 2.11, 2.95, 3.11],
      bookcase: [5.41, 1.65, 5.69, 3.27], armchair: [3.59, 1.44, 4.71, 2.56] };
    const { sim, card, send } = window.demo, m = 0.1, hits = {};
    for (let i = 0; i < 150; i++) {
      sim.step(1.5);
      send();
      for (let k = 0; k < 4; k++) {   // let it glide there, looking at it on the way
        await new Promise((r) => setTimeout(r, 40));
        const p = card._scene.robot.position;
        for (const [name, [x0, z0, x1, z1]] of Object.entries(blocked)) {
          if (p.x > x0 - m && p.x < x1 + m && p.z > z0 - m && p.z < z1 + m) hits[name] = (hits[name] ?? 0) + 1;
        }
      }
    }
    return hits;
  });
  expect(hits).toEqual({});
});

test('the path sent in pieces is the path the robot drew', async ({ page }) => {
  await open(page, '&house=flat');
  await page.evaluate(() => { const { sim, send } = window.demo; sim.clean([1, 3, 2], { 1: 'both', 3: 'vacuum', 2: 'mop' }); send(); });
  await run(page, 900, 3);   // big steps: each message redraws the whole path on the card, slow on software WebGL
  const { same, strokes } = await page.evaluate(() => {
    const { sim, card } = window.demo, flat = (p) => JSON.stringify(p.map((s) => [s.mode, s.points]));
    return { same: flat(card._map.path) === flat(sim.path), strokes: sim.path.length };
  });
  expect(strokes).toBeGreaterThan(1);
  expect(same).toBe(true);
});

test('it says what the robot does: washing its mops, stuck, paused, going home', async ({ page }) => {
  await open(page, '&house=flat&lang=es');
  const card = page.locator('dreame-vacuum-3d-card');
  const chip = card.locator('.chip');
  const toggle = card.locator('[data-cmd="toggle"]');
  const look = () => page.evaluate(() => {
    const s = window.demo.card._scene;
    return { badge: s.badge.visible ? s.badge.element.textContent : null, stuck: s.stuckGroup.children.length };
  });
  await expect(chip).toContainText('En la base');
  await toggle.click();   // start: every room
  await expect(chip).toContainText('Limpiando');
  await page.evaluate(async () => {
    const { sim, send } = window.demo;
    for (let i = 0; i < 8000 && sim.robotState !== 'washing'; i++) { sim.step(0.5); send(); if (i % 50 === 0) await new Promise((r) => setTimeout(r, 5)); }
  });
  await expect(chip).toContainText('Lavando las mopas');
  expect((await look()).badge).toBe('🫧 Lavando las mopas');
  await run(page, 60);
  await page.evaluate(() => { window.demo.sim.stuck(); window.demo.send(); });
  await expect(chip).toContainText('Robot atascado');
  expect(await look()).toEqual({ badge: '⚠️ Robot atascado', stuck: 2 });
  await toggle.click();   // start again
  await expect(chip).toContainText('Limpiando');
  expect((await look()).stuck).toBe(2);   // where it got stuck stays marked
  await toggle.click();   // pause
  await expect(chip).toContainText('En pausa');
  await card.locator('[data-cmd="return_to_base"]').click();
  await expect(chip).toContainText('Volviendo a la base');
});

test('a routine cleans its rooms', async ({ page }) => {
  await open(page, '&house=flat');
  await page.locator('dreame-vacuum-3d-card .routines button', { hasText: 'After dinner' }).click();
  await expect(page.locator('dreame-vacuum-3d-card .panel .msg')).toHaveText('Cleaning After dinner');
  expect(await page.evaluate(() => window.demo.sim.active)).toEqual([2, 1]);
});

test('it lets go of WebGL when it leaves the page, and draws only while it shows', async ({ page }) => {
  await open(page);
  const result = await page.evaluate(async () => {
    const card = window.demo.card, parent = card.parentNode;
    card.remove();
    const away = { scene: !!card._scene, canvases: card.shadowRoot.querySelectorAll('canvas').length };
    parent.append(card);
    await new Promise((r) => setTimeout(r, 300));
    const back = { scene: !!card._scene, canvases: card.shadowRoot.querySelectorAll('canvas').length };
    document.body.style.paddingTop = '4000px';
    await new Promise((r) => setTimeout(r, 400));
    const below = card._scene.running;
    document.body.style.paddingTop = '';
    await new Promise((r) => setTimeout(r, 400));
    return { away, back, below, shown: card._scene.running };
  });
  expect(result).toEqual({ away: { scene: false, canvases: 0 }, back: { scene: true, canvases: 1 }, below: false, shown: true });
});

test('a zone drawn on the floor gets cleaned', async ({ page }) => {
  await open(page, '&house=flat&view=plan');
  const card = page.locator('dreame-vacuum-3d-card');
  await card.locator('[data-zone]').click();
  await expect(card.locator('.panel .msg')).toHaveText('Drag on the floor to draw the zone');
  const box = await card.locator('canvas').boundingBox();
  const [cx, cy] = [box.x + box.width / 2, box.y + box.height / 2];
  await page.mouse.move(cx - 120, cy - 60);
  await page.mouse.down();
  await page.mouse.move(cx + 40, cy + 20, { steps: 8 });
  await page.mouse.move(cx + 120, cy + 60, { steps: 8 });
  await page.mouse.up();
  await expect(card.locator('.panel .msg')).toHaveText(/^Zone of [\d.]+ × [\d.]+ m$/);
  await card.locator('.panel .clean').click();
  await expect(card.locator('.panel .msg')).toHaveText('Cleaning the zone');
  const { state, active, lanes } = await page.evaluate(() => ({ ...window.demo.sim, lanes: window.demo.sim.route.length }));
  expect(state).toBe('cleaning');
  expect(active).toEqual([]);
  expect(lanes).toBeGreaterThan(2);
});

test('the dust and wet floor can be hidden, together or one of them', async ({ page }) => {
  await open(page, '&effects=0');
  await run(page, 20);
  const shown = () => page.evaluate(() => {
    const { layers, specks } = window.demo.card._scene.effects;
    return { dust: layers.dust.mesh.visible, glint: layers.glint.mesh.visible, wet: layers.wet.mesh.visible, specks: specks.visible };
  });
  expect(await shown()).toEqual({ dust: false, glint: false, wet: false, specks: false });
  await page.evaluate(() => { const { card } = window.demo; card.setConfig({ ...card._config, model: undefined, effects: { wet: false } }); });
  expect(await shown()).toMatchObject({ dust: true, glint: true, wet: false });
});

/** Wait for the house model to be in the scene. */
const modelLoaded = (page) => page.waitForFunction(() => window.demo.card._scene.modelGroup.children.length > 0);

test('the floor overlays sit above the rug, so they do not flicker into it as the camera turns', async ({ page }) => {
  await open(page);
  await modelLoaded(page);
  await run(page, 20);
  const { rugTop, lowest } = await page.evaluate(() => {
    const s = window.demo.card._scene, floor = s.xf.floor;
    // What the model has flat on the floor (a rug, a mat): meshes no taller than 3 cm whose bottom is at the floor.
    let rugTop = floor;
    s.modelGroup.updateMatrixWorld(true);
    s.modelGroup.traverse((o) => {
      if (!o.isMesh) return;
      o.geometry.computeBoundingBox();
      const b = o.geometry.boundingBox.clone().applyMatrix4(o.matrixWorld);
      if (b.max.y - b.min.y < 0.03 && b.min.y < floor + 0.01) rugTop = Math.max(rugTop, b.max.y);
    });
    const ys = [];
    for (const l of Object.values(s.effects.layers)) ys.push(l.mesh.geometry.attributes.position.getY(0));
    s.overlayGroup.traverse((o) => { if (o.isMesh) { o.geometry.computeBoundingBox(); ys.push(o.geometry.boundingBox.clone().applyMatrix4(o.matrixWorld).min.y); } });
    return { rugTop, lowest: Math.min(...ys) };
  });
  expect(rugTop).toBeGreaterThan(0);   // the demo's living room has a rug
  expect(lowest).toBeGreaterThan(rugTop);
});

test('the path does not run ahead of the robot: it shows as the robot gets there', async ({ page }) => {
  await open(page);
  await page.evaluate(() => { const { sim, send, card } = window.demo; sim.clean(Object.keys(card._map.rooms).map(Number)); send(); });
  await run(page, 10);
  await page.waitForFunction(() => { const s = window.demo.card._scene; return !s.glide && !s.queue.length; });
  await run(page, 15);   // a batch of poses arrives at once, as from the integration
  const drawn = () => page.evaluate(() => {
    const s = window.demo.card._scene;
    return { shown: s.path.geometry.drawRange.count, all: s.path.geometry.attributes.position.count, gliding: !!s.glide || s.queue.length > 0 };
  });
  const before = await drawn();
  expect(before.gliding).toBe(true);
  expect(before.shown).toBeLessThan(before.all);
  // It catches up with the batch within about two seconds, so it isn't drawn far from where the robot really is.
  // The timeout is wider: each glide starts on the next frame, and software WebGL on a CI runner draws few of them.
  await page.waitForFunction(() => { const s = window.demo.card._scene; return !s.glide && !s.queue.length; }, null, { timeout: 10_000 });
  const after = await drawn();
  expect(after.shown).toBeGreaterThanOrEqual(after.all);
});

test('the 3D, Plan and Follow buttons move the camera', async ({ page }) => {
  await open(page);
  const card = page.locator('dreame-vacuum-3d-card');
  const camera = () => page.evaluate(() => {
    const s = window.demo.card._scene, d = s.camera.position.clone().sub(s.controls.target).normalize();
    return { up: d.y, follow: s.follow, toRobot: s.controls.target.distanceTo(s.robot.position) };
  });
  await card.locator('[data-view="plan"]').click();
  await page.waitForTimeout(1200);   // the camera's 800 ms move
  expect((await camera()).up).toBeGreaterThan(0.999);
  await expect(card.locator('[data-view="plan"]')).toHaveAttribute('aria-pressed', 'true');
  await card.locator('[data-follow]').click();
  await expect(card.locator('[data-follow]')).toHaveAttribute('aria-pressed', 'true');
  await expect(card.locator('[data-view="plan"]')).toHaveAttribute('aria-pressed', 'false');
  await expect.poll(async () => (await camera()).toRobot).toBeLessThan(0.1);
  expect((await camera()).follow).toBe(true);
  await card.locator('[data-view="3d"]').click();
  await page.waitForTimeout(1200);
  const c = await camera();
  expect(c.follow).toBe(false);
  expect(c.up).toBeLessThan(0.95);   // tilted, not from straight above
});

test('the futuristic style draws the house as a hologram', async ({ page }) => {
  const errors = await open(page, '&style=futuristic');
  await modelLoaded(page);
  const holo = await page.evaluate(() => {
    const s = window.demo.card._scene, kinds = new Set();
    s.modelGroup.traverse((o) => { if (o.material) kinds.add(o.material.type); });
    return { holo: !!s.holo, kinds: [...kinds] };
  });
  expect(holo).toEqual({ holo: true, kinds: ['ShaderMaterial'] });
  // No scan wave sweeps across it any more.
  expect(await page.evaluate(() => Object.keys(window.demo.card._scene.holo.uniforms).sort())).toEqual(['base', 'color', 'opacity']);
  expect(errors).toEqual([]);
});

test('the cleaning settings change the robot\'s, and a room cleans with them', async ({ page }) => {
  await open(page);
  const card = page.locator('dreame-vacuum-3d-card');
  await card.locator('[data-settings]').click();
  const mode = card.locator('.opts [data-key="cleaning_mode"]');
  await expect(mode.locator('[aria-checked="true"]')).toHaveText('Vacuum and mop');
  await mode.locator('[data-option="mopping"]').click();
  await expect(mode.locator('[aria-checked="true"]')).toHaveText('Mop');
  // Mopping only: suction doesn't apply.
  await expect(card.locator('.opts [data-key="suction_level"] button').first()).toBeDisabled();
  await card.locator('.opts [data-key="water_volume"] [data-option="high"]').click();
  expect(await page.evaluate(() => ({ ...window.demo.settings }))).toMatchObject({ cleaning_mode: 'mopping', water_volume: 'high' });
  // A room's offer says what it will clean with, and cleans with it.
  await page.evaluate(() => window.demo.card._pick(1));
  await expect(card.locator('.panel .tune')).toHaveText('Mop · High');
  await card.locator('.panel .clean').click();
  await run(page, 20);
  expect(await page.evaluate(() => [...new Set(window.demo.sim.path.map((s) => s.mode))])).toEqual(['mop']);
});

test('the cleaning settings while it cleans: the mode waits, the suction changes', async ({ page }) => {
  await open(page, '&lang=es');
  await page.evaluate(() => { const { sim, send, card } = window.demo; sim.clean(Object.keys(card._map.rooms).map(Number)); send(); });
  const card = page.locator('dreame-vacuum-3d-card');
  await card.locator('[data-settings]').click();
  await expect(card.locator('.opts [data-key="cleaning_mode"] [data-option="mopping"]')).toHaveText('Fregar');
  await expect(card.locator('.opts [data-key="cleaning_mode"] button').first()).toBeDisabled();
  await expect(card.locator('.opts .note')).toHaveText('El modo cambia en la próxima limpieza');
  const strong = card.locator('.opts [data-key="suction_level"] [data-option="strong"]');
  await strong.click();
  await expect(strong).toHaveAttribute('aria-checked', 'true');
  await expect(strong).toHaveText('Fuerte');
});

test('the cleaning settings: no mopping without the pads, and none at all with controls: false', async ({ page }) => {
  await open(page, '&mop=0');
  const card = page.locator('dreame-vacuum-3d-card');
  await card.locator('[data-settings]').click();
  await expect(card.locator('.opts [data-option="mopping"]')).toBeDisabled();
  await expect(card.locator('.opts [data-option="mopping"]')).toHaveAttribute('title', 'Fit the mop pads to mop');
  await expect(card.locator('.opts [data-option="sweeping"]')).toBeEnabled();
  await page.evaluate(() => { const { card } = window.demo; card.setConfig({ ...card._config, model: undefined, controls: false }); card.hass = card._hass; });
  await expect(card.locator('[data-settings]')).toBeHidden();
});

test('the cleaning settings: passes go with the task, arrow keys move along a row, and a zone shows them too', async ({ page }) => {
  await open(page, '&house=flat&view=plan');
  const card = page.locator('dreame-vacuum-3d-card');
  await card.locator('[data-settings]').click();
  await card.locator('.opts [data-key="repeats"] [data-option="2"]').click();
  // Arrow keys: from Standard to Strong, and it is the robot's setting.
  await card.locator('.opts [data-key="suction_level"] [data-option="standard"]').focus();
  await page.keyboard.press('ArrowRight');
  await expect(card.locator('.opts [data-key="suction_level"] [aria-checked="true"]')).toHaveText('Strong');
  await expect(card.locator('.opts [data-key="suction_level"] [data-option="strong"]')).toBeFocused();
  // A zone's offer says what it will clean with.
  await card.locator('[data-zone]').click();
  const box = await card.locator('canvas').boundingBox();
  const [cx, cy] = [box.x + box.width / 2, box.y + box.height / 2];
  await page.mouse.move(cx - 100, cy - 50);
  await page.mouse.down();
  await page.mouse.move(cx + 100, cy + 50, { steps: 8 });
  await page.mouse.up();
  await expect(card.locator('.panel .tune')).toHaveText('Vacuum and mop · Strong · Medium · ×2');
  await card.locator('.panel .clean').click();
  const call = await page.evaluate(() => window.demo.calls.at(-1));
  expect(call).toMatchObject({ service: 'vacuum_clean_zone', data: { repeats: 2 } });
});

test('"controls" must be true or false', async ({ page }) => {
  await open(page);
  const message = await page.evaluate(() => {
    try { window.demo.card.setConfig({ ...window.demo.card._config, controls: 'no' }); return null; } catch (e) { return e.message; }
  });
  expect(message).toBe('"controls" must be true or false');
});

test('past cleanings: replay one, and show the coverage of the last ones', async ({ page }) => {
  await open(page, '&house=flat');
  const card = page.locator('dreame-vacuum-3d-card');
  await card.locator('[data-history]').click();
  const items = card.locator('.panel .list button');
  await expect(items).toHaveCount(3);
  await expect(card.locator('.panel .clean')).toBeHidden();
  await expect(items.first()).toContainText('min');
  await items.first().click();
  await expect(card.locator('.panel .msg')).toContainText('Replaying');
  await page.waitForFunction(() => window.demo.card._scene.map.path.flatMap((s) => s.points).length > 50);
  expect(await page.evaluate(() => window.demo.sim.path.length)).toBe(0);   // the live robot hasn't moved
  await card.locator('.panel .cancel').click();   // Live
  expect(await page.evaluate(() => [window.demo.card._replaying, window.demo.card._scene.map.path.length])).toEqual([null, 0]);

  await card.locator('[data-history]').click();
  await card.locator('.panel .coverage').click();
  await expect(card.locator('.panel .coverage')).toHaveAttribute('aria-pressed', 'true');
  const coverage = await page.evaluate(() => {
    const fx = window.demo.card._scene.effects;
    return { paths: fx.coverage.length, shown: fx.layers.coverage.mesh.visible };
  });
  expect(coverage).toEqual({ paths: 3, shown: true });
});
