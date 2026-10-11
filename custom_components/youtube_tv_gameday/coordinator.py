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

from .const import (ACTION_PREFIX, CONF_ALERT_NOTIFY, CONF_ALERT_SCOPE, CONF_APP_URL, CONF_FAVORITES,
                    CONF_LEAGUES, CONF_LINEUP, CONF_LOCALS, CONF_POWER_ENTITY, DEFAULT_ALERT_SCOPE,
                    DEFAULT_LEAGUES, DOMAIN, EVENT_ALERT, STREAMING)
from .espn import ChannelMap, fetch_scoreboard
from .youtube_tv import TuneError, find_apps, launch_app, tune

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
        self.apps: dict[str, str] = {}          # streaming service -> DIAL app name on the TV
        self._apps_checked = None
        self._alerted: set[tuple[str, str]] = set()   # (game id, alert kind) already announced
        self._alert_day = None

    # --- options -------------------------------------------------------------------------
    @property
    def lineup(self) -> dict[str, str]:
        return self.config_entry.options.get(CONF_LINEUP, {})

    @property
    def channels(self) -> ChannelMap:
        return ChannelMap(self.lineup, self.config_entry.options.get(CONF_LOCALS, {}), self.apps)

    @property
    def favorites(self) -> list[str]:
        return self.config_entry.options.get(CONF_FAVORITES, [])

    # --- data ----------------------------------------------------------------------------
    async def _async_update_data(self) -> list[dict[str, Any]]:
        leagues = self.config_entry.options.get(CONF_LEAGUES, DEFAULT_LEAGUES)
        await self._refresh_apps()
        games = await fetch_scoreboard(self.session, leagues, dt_util.now(), self.channels)
        self.fetched_at = dt_util.utcnow()
        self._announce(games)
        return games

    # --- close-game alerts ---------------------------------------------------------------
    def is_favorite(self, game: dict[str, Any]) -> bool:
        favs = {f.lower() for f in self.favorites}
        return any(str(t.get(k) or "").lower() in favs for t in game["teams"] for k in ("name", "full", "abbr"))

    def _in_scope(self, game: dict[str, Any]) -> bool:
        scope = self.config_entry.options.get(CONF_ALERT_SCOPE, DEFAULT_ALERT_SCOPE)
        return ("all" in scope
                or ("favorites" in scope and self.is_favorite(game))
                or ("ranked" in scope and any(t.get("rank") for t in game["teams"]))
                or ("my_channels" in scope and game.get("channel")))

    def _announce(self, games: list[dict[str, Any]]) -> None:
        """Fire each (game, alert kind) once: an HA event always, a phone notification if configured."""
        today = dt_util.now().date()
        if today != self._alert_day:
            self._alerted.clear()
            self._alert_day = today
        for g in games:
            alert = g.get("alert")
            if not alert or (g["id"], alert["kind"]) in self._alerted:
                continue
            self._alerted.add((g["id"], alert["kind"]))
            score = " – ".join(f"{t.get('name')} {t.get('score')}" for t in g["teams"])
            data = {"game_id": g["id"], "league": g["league"], "kind": alert["kind"], "text": alert["text"],
                    "score": score, "channel": g.get("channel"), "favorite": self.is_favorite(g),
                    "in_scope": self._in_scope(g)}
            self.hass.bus.async_fire(EVENT_ALERT, data)
            target = self.config_entry.options.get(CONF_ALERT_NOTIFY)
            if target and data["in_scope"]:
                self.hass.async_create_task(self._notify(target, g, alert, score))

    async def _notify(self, target: str, game: dict[str, Any], alert: dict[str, str], score: str) -> None:
        title = {"close": "Close game", "ot": "Overtime", "upset": "Upset alert"}.get(alert["kind"], "Game alert")
        channel = game.get("channel")
        message = f"{score}\n{alert['text']}" + (f" · on {channel}" if channel else "")
        data: dict[str, Any] = {"tag": f"ytgd-{game['id']}", "group": "youtube-tv-gameday"}
        if channel:
            data["actions"] = [{"action": f"{ACTION_PREFIX}{channel}", "title": f"Watch on {channel}"}]
        domain, _, service = target.partition(".")
        try:
            await self.hass.services.async_call(domain or "notify", service or target,
                                                {"title": f"{title}: {game['league']}", "message": message, "data": data})
        except Exception as err:  # noqa: BLE001 - a bad notify target must not break polling
            _LOGGER.warning("Alert notification via %s failed: %s", target, err)

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
            "tv": self._tv_state(),
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

    def _tv_state(self) -> str | None:
        """'on' / 'off' (or None when no power entity is configured) — the card uses it for messaging."""
        entity = self.config_entry.options.get(CONF_POWER_ENTITY)
        state = self.hass.states.get(entity) if entity else None
        if state is None:
            return None
        return "off" if state.state in ("off", "standby", "unavailable", "unknown") else "on"

    async def _refresh_apps(self) -> None:
        """Which streaming apps the TV has (re-checked every 6 h; the TV must be reachable)."""
        now = dt_util.utcnow()
        if self._apps_checked and now - self._apps_checked < timedelta(hours=6) and self.apps:
            return
        found = await find_apps(self.session, self.config_entry.data[CONF_APP_URL],
                                {svc: dial for svc, (_, dial) in STREAMING.items()})
        if found or not self.apps:
            self.apps = found
        self._apps_checked = now

    async def async_open_app(self, service: str, label: str | None = None) -> None:
        """Turn the TV on if needed and open a streaming app (for games not on YouTube TV)."""
        dial = self.apps.get(service)
        if not dial:
            raise TuneError(f"{service} isn't installed on the TV (or the TV wasn't reachable when checked)")
        async with self._tune_lock:
            await self._power_on()
            await launch_app(self.session, self.config_entry.data[CONF_APP_URL], dial)
            self.now_channel, self.now_label, self.tuned_at = service, label, dt_util.now()
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
