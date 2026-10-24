from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.dreame_vacuum_3d.const import DOMAIN


async def test_entry_sets_up(hass):
    entry = MockConfigEntry(domain=DOMAIN, data={})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
