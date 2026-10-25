// What the robot finds on the floor, and the furniture it recognises: how the card names and draws them.
// size: [width, height, depth] in metres, about the real thing (the robot gives a point, not a size); flat ones are
// stains, round ones cylinders; warn ones get the warning colour.

export const OBSTACLES = {
  shoes: { en: 'Shoes', es: 'Zapatos', icon: '👟', size: [0.28, 0.1, 0.11] },
  sock: { en: 'Sock', es: 'Calcetín', icon: '🧦', size: [0.22, 0.03, 0.09] },
  wire: { en: 'Cable', es: 'Cable', icon: '🔌', size: [0.5, 0.012, 0.012] },
  thread: { en: 'Thread', es: 'Hilo', icon: '🧵', size: [0.3, 0.008, 0.008] },
  toy: { en: 'Toy', es: 'Juguete', icon: '🧸', size: [0.15, 0.12, 0.15] },
  pet_waste: { en: 'Pet waste', es: 'Excremento', icon: '💩', size: [0.08, 0.04, 0.06], warn: true },
  trash_can: { en: 'Bin', es: 'Papelera', icon: '🗑️', size: [0.28, 0.35, 0.28], round: true },
  fabric: { en: 'Fabric', es: 'Tela', icon: '🧺', size: [0.45, 0.02, 0.3] },
  power_strip: { en: 'Power strip', es: 'Regleta', icon: '🔌', size: [0.3, 0.04, 0.06] },
  scale: { en: 'Scale', es: 'Báscula', icon: '⚖️', size: [0.3, 0.03, 0.3] },
  liquid_stain: { en: 'Spill', es: 'Líquido', icon: '💧', size: [0.3, 0, 0.25], flat: true, warn: true },
  stain: { en: 'Stain', es: 'Mancha', icon: '🟤', size: [0.25, 0, 0.2], flat: true },
  pet: { en: 'Pet', es: 'Mascota', icon: '🐾', size: [0.5, 0.3, 0.22] },
  cleaning_tools: { en: 'Cleaning tools', es: 'Útiles de limpieza', icon: '🧹', size: [0.3, 0.1, 0.3] },
  leg: { en: 'Furniture leg', es: 'Pata', icon: '🪑', size: [0.06, 0.4, 0.06], round: true },
  particles: { en: 'Crumbs', es: 'Migas', icon: '🍪', size: [0.2, 0, 0.2], flat: true },
  food_bowl: { en: 'Food bowl', es: 'Comedero', icon: '🥣', size: [0.18, 0.06, 0.18], round: true },
  pet_bed: { en: 'Pet bed', es: 'Cama de mascota', icon: '🐕', size: [0.6, 0.15, 0.5] },
  hair: { en: 'Hair', es: 'Pelos', icon: '🧶', size: [0.15, 0, 0.15], flat: true },
  paper: { en: 'Paper', es: 'Papel', icon: '📄', size: [0.21, 0, 0.3], flat: true },
  sundry: { en: 'Things', es: 'Objetos', icon: '📦', size: [0.2, 0.1, 0.2] },
  obstacle: { en: 'Obstacle', es: 'Obstáculo', icon: '⚠️', size: [0.2, 0.12, 0.2] },
};

/** Furniture heights (m), for drawing it as boxes on the robot's own map (a house model has its own furniture). */
export const FURNITURE_HEIGHT = {
  single_bed: 0.5, double_bed: 0.5, armchair: 0.8, two_seat_sofa: 0.8, three_seat_sofa: 0.8, l_shaped_sofa: 0.8,
  dining_table: 0.75, table: 0.75, coffee_table: 0.45, nightstand: 0.55, toilet: 0.4, litter_box: 0.4, pet_bed: 0.15,
  food_bowl: 0.06, pet_toilet: 0.3, refrigerator: 1.8, washing_machine: 0.85, air_conditioner: 1.8, tv_cabinet: 0.5,
  bookshelf: 1.8, shoe_cabinet: 1, wardrobe: 2, plant: 0.9, floor_mirror: 1.6,
};

export const NEW_HEIGHT = 0.12;   // m: what the lidar newly sees is drawn this tall (it gives the outline, not the height)

/** The obstacle's name in the card's language, with its icon. */
export function thingLabel(type, lang) {
  const o = OBSTACLES[type] ?? OBSTACLES.obstacle;
  return `${o.icon} ${o[lang] ?? o.en}`;
}

/** A turn for an obstacle, the same every time for the same spot, so they don't all line up. */
export const turnOf = (x, y) => (((Math.sin(x * 12.9898 + y * 78.233) * 43758.5453) % 1) + 1) % 1 * Math.PI;
