# Changelog

All notable releases of Dreame Vacuum 3D are listed here. The [Release workflow](.github/workflows/release.yml) prepends each tagged version.

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

