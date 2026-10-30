# Roadmap

What the project plans to do over the next year, until the end of 2027, and what it won't do. Plans move with what people use it for: vote for the [feature requests](https://github.com/dortizesquivel/dreame-vacuum-3d/issues?q=label%3Aenhancement) you want with a 👍, or open one.

## Planned

- **More models marked tested**: only the X10+ (`dreame.vacuum.p2114a`) is tested today. Others are added from the diagnostics their owners send, as [CONTRIBUTING.md](CONTRIBUTING.md#adding-a-model-via-diagnostics) explains.
- **Map-edit services**: restricted zones, and merging, splitting and renaming rooms, under the same names as Tasshack's.
- **Upkeep**: keep up with Home Assistant, Xiaomi's cloud login and three.js releases, keep the dependencies current, and keep the OpenSSF Scorecard checks.

## Ideas, not decided

From the design notes; they go ahead if people ask for them.

- **The last saved map when Xiaomi's cloud doesn't answer**, so the card still shows the house.
- **Several floors at once**: the saved maps stacked, the active one highlighted.
- **A queue of cleanings**: "the kitchen mopping, then the living room vacuuming", one after the other.
- **Assist sentences**: "clean the kitchen", "how long has it got left?".

## Not planned

- **Obstacle photos**: the robot would upload floor-level photos of the home to Xiaomi's cloud.
- **Streaming the robot's camera**.
- **Editing no-go zones, walls or rooms in the 3D card**: the Xiaomi Home app already does it.
- **Other backends** such as Valetudo, and **vacuums that aren't Dreame**.
- **Automations inside the integration**, like not cleaning where someone is, or cleaning with solar surplus: they belong in Home Assistant automations, built on the integration's entities and services.
- **Telemetry or a cloud of our own**: the integration talks only to Xiaomi's cloud and to the robot.

## Done

See the [changelog](CHANGELOG.md) and the [releases](https://github.com/dortizesquivel/dreame-vacuum-3d/releases).
