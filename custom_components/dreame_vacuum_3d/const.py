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
