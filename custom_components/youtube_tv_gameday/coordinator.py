"""Scoreboard data + tuning for one YouTube TV Gameday entry (one TV)."""
from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util

from .const import (CONF_APP_URL, CONF_FAVORITES, CONF_LEAGUES, CONF_LINEUP, CONF_LOCALS,
                    CONF_POWER_ENTITY, DEFAULT_LEAGUES, DOMAIN)
from .espn import ChannelMap, fetch_scoreboard
from .youtube_tv import TuneError, tune

_LOGGER = logging.getLogger(__name__)
FRESH = timedelta(seconds=25)


class GameDayCoordinator(DataUpdateCoordinator[list[dict[str, Any]]]):
    """Polls ESPN once a minute; the card asks for a refresh when it's older than 25 s."""

    config_entry: ConfigEntry

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__(hass, _LOGGER, config_entry=entry, name=DOMAIN, update_interval=timedelta(seconds=60))
        self.session = async_get_clientsession(hass)
        self.now_channel: str | None = None
        self.now_label: str | None = None
        self.tuned_at = None
        self._tune_lock = asyncio.Lock()

    # --- options -------------------------------------------------------------------------
    @property
    def lineup(self) -> dict[str, str]:
        return self.config_entry.options.get(CONF_LINEUP, {})

    @property
    def channels(self) -> ChannelMap:
        return ChannelMap(self.lineup, self.config_entry.options.get(CONF_LOCALS, {}))

    @property
    def favorites(self) -> list[str]:
        return self.config_entry.options.get(CONF_FAVORITES, [])

    # --- data ----------------------------------------------------------------------------
    async def _async_update_data(self) -> list[dict[str, Any]]:
        leagues = self.config_entry.options.get(CONF_LEAGUES, DEFAULT_LEAGUES)
        games = await fetch_scoreboard(self.session, leagues, dt_util.now(), self.channels)
        self.fetched_at = dt_util.utcnow()
        return games

    async def async_fresh(self) -> None:
        """Refresh if the data is older than FRESH (the card calls this on every poll)."""
        fetched = getattr(self, "fetched_at", None)
        if self.data is None or fetched is None or dt_util.utcnow() - fetched > FRESH:
            await self.async_refresh()

    def payload(self) -> dict[str, Any]:
        """What the card needs, in one message."""
        return {
            "games": self.data or [],
            "updated": (getattr(self, "fetched_at", None) or dt_util.utcnow()).isoformat(),
            "now": self.now_channel,
            "favorites": self.favorites,
            "quick": self.channels.quick_channels(),
        }

    # --- tuning --------------------------------------------------------------------------
    async def async_tune(self, channel: str, label: str | None = None) -> None:
        video_id = self.lineup.get(channel)
        if not video_id:
            raise TuneError(f"{channel} is not in your YouTube TV lineup")
        async with self._tune_lock:
            await self._power_on()
            await tune(self.session, self.config_entry.data[CONF_APP_URL], video_id)
            self.now_channel, self.now_label, self.tuned_at = channel, label, dt_util.now()
            self.async_update_listeners()

    async def _power_on(self) -> None:
        entity = self.config_entry.options.get(CONF_POWER_ENTITY)
        if not entity:
            return
        state = self.hass.states.get(entity)
        if state and state.state not in ("off", "standby", "unavailable", "unknown"):
            return
        await self.hass.services.async_call("media_player", "turn_on", {"entity_id": entity}, blocking=True)
        for _ in range(20):
            await asyncio.sleep(2)
            state = self.hass.states.get(entity)
            if state and state.state not in ("off", "standby", "unavailable", "unknown"):
                break
        await asyncio.sleep(8)  # let the TV finish starting its apps before DIAL
