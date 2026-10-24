"""Config flow for Dreame Vacuum 3D."""

from __future__ import annotations

from homeassistant import config_entries

from .const import DOMAIN


class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Add a Dreame vacuum."""

    VERSION = 1
