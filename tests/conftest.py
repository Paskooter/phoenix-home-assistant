"""Real, isolated Home Assistant fixtures. All devices and identities are synthetic."""

import pytest
from homeassistant import auth, bootstrap, config_entries, loader
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component


@pytest.fixture
async def hass(tmp_path):
    hass = HomeAssistant(str(tmp_path))
    hass.config.skip_pip = True
    hass.config.language = "en"
    hass.config.time_zone = "UTC"
    loader.async_setup(hass)
    hass.config_entries = config_entries.ConfigEntries(hass, {})
    assert await bootstrap.async_load_base_functionality(hass)
    hass.auth = await auth.auth_manager_from_config(hass, [{"type": "homeassistant"}], [])
    assert await async_setup_component(hass, "homeassistant", {})
    yield hass
    await hass.async_stop(force=True)
