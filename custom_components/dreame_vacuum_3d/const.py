"""Constants for the Dreame Vacuum 3D integration."""

DOMAIN = "dreame_vacuum_3d"

CONF_AUTH = "auth"
CONF_COEXIST = "coexist"
CONF_CONSUMABLE_THRESHOLD = "consumable_threshold"
CONF_DID = "did"
CONF_NOTIFY = "notify"

DEFAULT_COEXIST = True
DEFAULT_CONSUMABLE_THRESHOLD = 15

# Xiaomi cloud regions offered in the config flow.
COUNTRIES = ("cn", "de", "i2", "ru", "sg", "us")

# Persistent notification categories (options).
NOTIFICATION = (
    "cleanup_completed",
    "consumable",
    "information",
    "warning",
    "error",
)

# Home Assistant bus events (entity_id + device_id always included).
EVENT_TASK_STATUS = f"{DOMAIN}_task_status"
EVENT_CONSUMABLE = f"{DOMAIN}_consumable"
EVENT_ERROR = f"{DOMAIN}_error"
EVENT_INFORMATION = f"{DOMAIN}_information"

# Consumable property → service/button name (life left %).
CONSUMABLE_LIFE: dict[str, str] = {
    "main_brush_left": "main_brush",
    "side_brush_left": "side_brush",
    "filter_left": "filter",
    "tank_filter_left": "tank_filter",
    "sensor_dirty_left": "sensor",
    "mop_pad_left": "mop_pad",
    "silver_ion_left": "silver_ion",
    "detergent_left": "detergent",
    "squeegee_left": "squeegee",
    "onboard_dirty_water_tank_left": "onboard_dirty_water_tank",
    "dirty_water_channel_dirty_left": "dirty_water_channel",
    "deodorizer_left": "deodorizer",
    "wheel_dirty_left": "wheel",
    "scale_inhibitor_left": "scale_inhibitor",
}
