# Make a 3D model of your house from photos

The card draws the robot on a 3D model of your house. This is a prompt for an AI coding agent that can run commands on your computer, such as Claude Code: it turns phone photos of each room into one model of the whole house, placed on the robot's own floor plan so the robot lands in the right spot without any calibration. It adapts the [room prompt of the mmWave 3D Card](https://github.com/dortizesquivel/mmwave-3d-card/blob/main/demo/models/living-room/prompt.md), which builds one room, to a house: the robot's map gives every room's outline and where the rooms are relative to each other, and the photos give the heights, the openings, the furniture and the textures.

## What you need

- **The robot's floor plan.** In the card, select **⤓** (*Download the robot's floor plan*). It saves `floor-plan-<map id>.json`: each room's cells (5 cm), its name, and where the dock is, in the robot's coordinates (mm).
- **Photos of each room**: 2 to 6 per room from different corners, so every wall and every large piece of furniture shows in at least one, and each photo shares a few things with another. Take them from about chest height with the whole room in frame from floor to ceiling; the phone's wide lens (0.5×) is fine. Don't move anything between photos. Put each room's photos in a folder named like the room on the robot's map (`Salón/`, `Cocina/`…). Rooms you skip stay as a flat floor.
- **Tools**: the agent uses Python (numpy, scipy, Pillow) and Node.js with three.js and Playwright to render, and installs what's missing.

The floor plan sets the scale, so no measurement is needed; one (a door's height, the ceiling's) still helps the agent check its work. Everything stays on your computer. A model textured with photos of your home is private: don't commit it to a public repository, and keep it out of `/config/www/` folders other people can read.

## The prompt

Replace the `{…}` blanks, then paste:

```text
Build a 3D model of my house for the Dreame Vacuum 3D card (Home Assistant), from my photos and my robot vacuum's
floor plan.

Floor plan: {PATH TO floor-plan-<id>.json}
Photos: {FOLDER WITH ONE SUBFOLDER OF PHOTOS PER ROOM, named like the rooms in the floor plan}
Output: {e.g. ~/Downloads/house-model/}

The floor plan is JSON: grid {left, top, size, width, height} in mm, cells {room id: [x, y, n, …]} as runs of n cells
starting at cell (x, y) (a cell is `size` mm; x right, y up when seen from above; "255" are walls and obstacles the robot
saw, "254" floor not in a room), rooms {id: {name, x, y}} and dock [x, y, angle in degrees counterclockwise from +x].
A cell's corner is (left + x·size, top + y·size).

Work in steps and show me the result of each one as an image, saved to the output folder with a progress.md that has
one line per iteration. Keep everything on my computer: don't upload the photos or the model anywhere.

1. Frame. Use the floor plan's frame for the whole house, in metres, y up: X = x / 1000, Z = -y / 1000, floor at y = 0.
   Draw the plan (rooms in colours, walls dark) as plan.png and check it against the room names I gave the folders.
2. Outlines. For each room, trace its outline from its cells (the floor the robot reached) and push it out to the
   walls the robot saw ("255" cells next to it): that is where the room's walls are, give or take 5 cm. Doors are where
   two rooms' cells touch. Keep these outlines: they are the plan every room is built on.
3. Cameras. For each room with photos: convert the photos to JPEG, read the focal length from EXIF (35 mm equivalent →
   pixels, on the diagonal), take the principal point at the centre, and estimate pitch and roll from the vertical
   vanishing point (door frames, wall corners).
4. Correspondences and fit, room by room. Mark points with known 3D meaning in zoomed crops with a pixel grid: the
   room's corners at the floor and the ceiling, door and window frames, corners of large furniture. Solve one
   least-squares problem per room (scipy.optimize.least_squares) for the camera poses, a shared focal length, the
   ceiling height, the openings and the furniture, with the room's outline from step 2 as a strong prior (the walls
   may move a few centimetres, not more). Report the RMS error in pixels and the sizes that came out (doors, sofa,
   table…) as sanity checks. Iterate until the RMS is below about 6 px; a point hundreds of pixels off is usually
   marked against the wrong corner. If the robot's outline and the photos disagree by more than 10 cm, tell me.
5. Geometry. Floors, ceilings and walls (with door and window openings) as single-sided planes facing into each room,
   so the card shows the house as a doll's house from outside. Furniture as boxes or simple parametric shapes that
   match the real pieces. Rooms without photos: their floor only, at y = 0. Render each room's edges over its photos
   from the fitted cameras and fix what doesn't line up.
6. Textures. Bake a texture for every surface by projecting the photos back onto it (about 300 px per metre), with
   occlusion (a depth map per photo, with a margin at furniture edges) and, per texel, the photo that sees it best:
   weight by (cos of the viewing angle ÷ distance)^8. Even out the exposure between photos. Mask people, clutter and
   open doors. Fill texels no photo sees with a push-pull inpaint or the surface's median colour. Photo textures carry
   their own light: make those materials unlit (KHR_materials_unlit). Don't upscale beyond about twice the photos'
   resolution.
7. Export. One GLB with three.js GLTFExporter: JPEG textures, under 16 MB. Name each room's group after its room. Load
   it back with GLTFLoader and check the mesh count, the house's size against the floor plan, and that the dock point
   sits on the floor next to a wall.
8. Report. The rooms built from photos and the ones left flat, each room's RMS, what the photos don't cover, and the
   card's config. The dock in the model is [dock.x / 1000, 0, -dock.y / 1000] and its heading in degrees is
   atan2(cos a, -sin a) (0 = the model's +z, 90 = +x), with a the dock's angle:

   model:
     url: /local/<file>.glb
     dock: { position: [x, 0, z], heading: <deg> }
     style: textured        # or futuristic
```

## Then, in Home Assistant

Copy the `.glb` to `/config/www/`, ideally with a name nobody would guess, and add the `model:` block the agent gave you to the card. If you make the model another way (a phone scanning app that exports GLB, or Blender), put the model's own point under the docked robot in `model.dock.position` and the direction the docked robot faces in `model.dock.heading`.
