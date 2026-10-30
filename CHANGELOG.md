# Changelog

All notable releases of Dreame Vacuum 3D are listed here. The [Release workflow](.github/workflows/release.yml) prepends each tagged version.

## v0.1.14 - 2026-10-30

- docs(security): the integration has a dashboard card (818dfd8)
- fix(integration): service names, empty lists, malformed map data, zones for the card (db69d8c)
- fix(card): keyboard rows, Close, errors, coverage, view, settings button, low cards (be54ff0)
- fix(scene): labels, tapped names, zone dust, big maps, a leaked buffer, recalibration (b93f1e5)

## v0.1.13 - 2026-10-30

- test(card): let the dust fade before counting renders (142440d)
- fix(scene): fix pose queue, stuck mark, floor effects and context loss bugs (30881bf)
- fix(card): header at phone width, lost settings clicks, stale history (08a8f30)
- fix(integration): a robot without a token sets up through the cloud (0347f65)
- fix(integration): resetting a consumable the robot lacks is a clear error (603b91d)
- fix(integration): AI detection switches flipped together keep both bits (e9c6a91)
- fix(integration): keep the partial map frames when the whole one fails (7e10f1b)
- fix(integration): keep what is new on the floor across a pause (54e10bf)
- fix(integration): pausing mid-task no longer ends the task (e17a771)
- fix(integration): an offline robot makes its entities unavailable (dbabe3a)

## v0.1.12 - 2026-10-30

- fix(card): the robot settles after a turn past ±π, and keeps up on slow frames (4741ec9)
- test(card): give the furniture test its own five minutes (857eb25)
- test(card): give the path catch-up test room on a slow CI runner (d49cbd8)
- docs: the demo moves the robot every half second, and the GIF has no lag (a082794)
- test(card): passes, arrow keys, a zone's settings, the controls option, no scan wave (430ea29)
- docs(readme): the cleaning settings and the controls option (1127fe5)
- test(card): the cleaning settings (2e8c965)
- feat(demo): the robot's cleaning settings, and cleaning with its mode (bb5a16d)
- feat(card): cleaning settings: mode, suction, water and passes (4a58afc)
- test: split the map-warning filter's condition (d45ee2e)
- test: the map-warning filter in a helper, within the line length (2b419e7)
- test: count only the integration's own map warnings (2c21251)
- test(card): the path follows the robot, which catches up within 2 s (7cae4a1)
- fix(card): the path and floor effects don't run ahead of the robot, and it keeps up (930ecfe)
- chore(deps): bump the actions group with 6 updates (11d74ad)
- chore(deps): bump the pip group with 3 updates (4be84c7)
- chore(deps-dev): bump @playwright/test in /card in the npm group (e81cc7f)
- docs(readme): the demo GIF and the futuristic screenshot without the flicker (ab78547)
- test(card): overlays above the rug, the view buttons, the futuristic hologram (376adc8)
- fix(card): the floor overlays don't flicker over a rug as the camera turns (ae10bd6)
- docs: private vulnerability reporting is enabled (0bedc6d)
- docs: list the lint, CodeQL, Scorecard and Dependabot checks (bb6d816)
- docs(readme): Downloads, CodeQL and OpenSSF Scorecard badges (26a7ccf)
- docs: OpenSSF Best Practices answers in .bestpractices.json (babb3f2)
- docs: code of conduct, governance and roadmap (0f105f9)
- docs: bug report and feature request forms (568f3ff)
- ci: monthly Dependabot updates for actions, npm and pip (f3eec04)
- ci: CodeQL and OpenSSF Scorecard (80bf333)
- chore(card): lint with ESLint's recommended rules in CI (49c7447)
- ci: drop the DCO sign-off check (0527103)
- docs(readme): the left card follows the robot after swinging around the room; the GIF and the futuristic screenshot without the scan wave (021bda1)
- fix(card): no scan wave in the futuristic style (f501a9f)
- docs(readme): the demo GIF's right card is the futuristic hologram (e2c19a6)
- docs(readme): the demo GIF shows two cards side by side (6702916)
- docs(readme): the demo GIF goes from 3D to Follow to Plan (27e4559)
- docs(readme): a demo GIF that shows the room from the first frame and swings around it (bc66079)

## v0.1.11 - 2026-10-28

- fix(map): don't warn that the robot sent no map while it's paused (45d9419)

## v0.1.10 - 2026-10-28

- fix: end a return stuck on the dock, and resume a paused return to the dock (debe88b)
- fix(history): look again 10 and 30 minutes after a cleaning (e60b8da)
- fix(card): show the robot's error, and where it stopped, when the vacuum only says paused (aa20c93)

## v0.1.9 - 2026-10-26

- feat(map): what the lidar newly sees, updated while the robot cleans (158c50e)
- test(card): lighter path test, more time for browser tests on CI (d74380f)
- docs(readme): an animated demo at the top, and the live demo link to the project's page (bd4a85e)

## v0.1.8 - 2026-10-26

- feat(card): past cleanings: replay one, and the coverage of the last five (d2f3122)
- feat: the cleaning history, with when each room was last cleaned (cb04859)
- fix(demo): the rug without the coffee table's ghost legs; option to hide the floor effects (7118068)
- feat(card): drag a zone on the floor to clean it (2706899)
- test(card): browser tests on the demo, in CI (231a050)
- feat(card): what the robot is doing, where it got stuck, and start/pause/dock buttons (2e866fb)
- feat(map): lighter live messages, faster while a card shows the map (2b4d848)
- fix(map): until a first frame comes, ask the robot again in 30 s (5258cca)

## v0.1.7 - 2026-10-26

- fix(card): the robot follows its path between poses, round the furniture (2106576)
- feat(demo): made-up houses and a landing page on GitHub Pages (adebf1f)
- feat(card): routines, no drawing off screen, obstacle tags clear of room names (dec2b33)

## v0.1.6 - 2026-10-26

- fix(card): the robot keeps moving between batches of poses (f7a87f2)

## v0.1.5 - 2026-10-26

- feat: follow the robot while it cleans with the frames it logs in the cloud (27b4d9e)

## v0.1.4 - 2026-10-25

- fix: new obstacles are what wasn't there in the last cleaning (d9f8955)

## v0.1.3 - 2026-10-25

- feat: draw what the robot finds on the floor (b09e78d)

## v0.1.2 - 2026-10-25

- feat: floor effects for vacuuming and mopping (6f25516)
- fix: decrypt the X10+'s live map and wait for its upload (971ff86)

## v0.1.1 - 2026-10-25

- docs: the map pipeline, the card and their trust boundaries (afae1f3)
- feat: a 3D card with the robot moving through the house (fe05221)
- feat: decode the robot's map and send it to a WebSocket subscription (1afe9ff)
- docs: list the new CI checks (90e2391)
- style: format with ruff format and check it in CI (04cff2d)
- ci: type-check with mypy, check DCO sign-off, raise the coverage floor to 95 (81b7eec)
- fix: close the phase 1 review findings (ee08eb8)

