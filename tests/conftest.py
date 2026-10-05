"""Real, isolated Home Assistant fixtures. All devices and identities are synthetic."""

import os
import sys

import pytest
from homeassistant import auth, bootstrap, config_entries, loader
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component

from tests.direct_backend import SyntheticLocalRobot

if project := os.environ.get("JEV_PROJECT_DIR"):
    # Both repositories use the custom_components namespace. Register it before
    # HA discovers integrations, just as an owner's installed folders would be.
    sys.path.insert(0, project)


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


@pytest.fixture
async def robot(tmp_path):
    """A public, invented local peer; only its loopback socket is reachable."""
    peer = await SyntheticLocalRobot(tmp_path / "invented-robot").start()
    try:
        yield peer
    finally:
        await peer.close()
