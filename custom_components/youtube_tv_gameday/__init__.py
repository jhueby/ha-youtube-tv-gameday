"""YouTube TV Gameday: live scores in Home Assistant, tap a game to put it on your TV via YouTube TV."""
from __future__ import annotations

import asyncio
import hashlib
import logging
from pathlib import Path

import aiohttp
import voluptuous as vol

from homeassistant.components import websocket_api
from homeassistant.components.frontend import add_extra_js_url
from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.const import Platform
from homeassistant.core import Event, HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.typing import ConfigType

from .const import ACTION_PREFIX, CARD_URL, DOMAIN, VERSION
from .coordinator import GameDayCoordinator
from .espn import fetch_summary
from .youtube_tv import TuneError

_LOGGER = logging.getLogger(__name__)
PLATFORMS = [Platform.SENSOR]
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

GameDayConfigEntry = ConfigEntry[GameDayCoordinator]

SERVICE_TUNE = "tune_channel"
SERVICE_SCOREBOARD = "scoreboard"
ATTR_ENTRY = "config_entry_id"


def _coordinator(hass: HomeAssistant, entry_id: str | None) -> GameDayCoordinator:
    entries = [e for e in hass.config_entries.async_entries(DOMAIN) if e.state is ConfigEntryState.LOADED]
    if entry_id:
        entries = [e for e in entries if e.entry_id == entry_id]
    if not entries:
        raise ServiceValidationError("YouTube TV Gameday is not set up")
    return entries[0].runtime_data


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Serve the card, register services and the websocket command (once per HA start)."""
    card = Path(__file__).parent / "frontend" / "youtube-tv-gameday-card.js"
    await hass.http.async_register_static_paths([StaticPathConfig(CARD_URL, str(card), True)])
    # cache-bust on content, not just version, so dashboards pick up card edits
    digest = await hass.async_add_executor_job(lambda: hashlib.sha1(card.read_bytes()).hexdigest()[:8])
    add_extra_js_url(hass, f"{CARD_URL}?v={VERSION}-{digest}")
    websocket_api.async_register_command(hass, ws_scoreboard)
    websocket_api.async_register_command(hass, ws_game)

    async def notification_action(event: Event) -> None:
        """'Watch on <channel>' button on a close-game notification (mobile_app)."""
        action = str(event.data.get("action") or "")
        if not action.startswith(ACTION_PREFIX):
            return
        try:
            await _coordinator(hass, None).async_tune(action[len(ACTION_PREFIX):], "from alert")
        except (ServiceValidationError, TuneError) as err:
            _LOGGER.warning("Watch from notification failed: %s", err)

    hass.bus.async_listen("mobile_app_notification_action", notification_action)

    async def tune_channel(call: ServiceCall) -> ServiceResponse:
        coord = _coordinator(hass, call.data.get(ATTR_ENTRY))
        channel = call.data["channel"]
        try:
            await coord.async_tune(channel, call.data.get("label"))
        except TuneError as err:
            raise HomeAssistantError(f"Couldn't tune {channel}: {err}") from err
        return {"channel": channel}

    async def scoreboard(call: ServiceCall) -> ServiceResponse:
        coord = _coordinator(hass, call.data.get(ATTR_ENTRY))
        await coord.async_fresh()
        return coord.payload()

    hass.services.async_register(
        DOMAIN, SERVICE_TUNE, tune_channel,
        schema=vol.Schema({vol.Required("channel"): cv.string, vol.Optional("label"): cv.string,
                           vol.Optional(ATTR_ENTRY): cv.string}),
        supports_response=SupportsResponse.OPTIONAL)
    hass.services.async_register(
        DOMAIN, SERVICE_SCOREBOARD, scoreboard,
        schema=vol.Schema({vol.Optional(ATTR_ENTRY): cv.string}),
        supports_response=SupportsResponse.ONLY)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: GameDayConfigEntry) -> bool:
    coordinator = GameDayCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_options_updated))
    return True


async def _options_updated(hass: HomeAssistant, entry: GameDayConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: GameDayConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


@websocket_api.websocket_command({vol.Required("type"): "youtube_tv_gameday/scoreboard", vol.Optional("entry_id"): str})
@websocket_api.async_response
async def ws_scoreboard(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict) -> None:
    """The card's poll: refresh if stale, return games + channel bar + what's on."""
    try:
        coord = _coordinator(hass, msg.get("entry_id"))
    except ServiceValidationError as err:
        connection.send_error(msg["id"], "not_found", str(err))
        return
    await coord.async_fresh()
    connection.send_result(msg["id"], coord.payload())


@websocket_api.websocket_command({vol.Required("type"): "youtube_tv_gameday/game",
                                  vol.Required("game_id"): str, vol.Optional("entry_id"): str})
@websocket_api.async_response
async def ws_game(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict) -> None:
    """Detail for one game (the card's long-press sheet)."""
    try:
        coord = _coordinator(hass, msg.get("entry_id"))
    except ServiceValidationError as err:
        connection.send_error(msg["id"], "not_found", str(err))
        return
    game = next((g for g in coord.data or [] if g["id"] == msg["game_id"]), None)
    if game is None:
        connection.send_error(msg["id"], "not_found", "That game isn't on today's scoreboard")
        return
    try:
        detail = await fetch_summary(coord.session, game["sport"], game["slug"], game["id"])
    except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as err:
        connection.send_error(msg["id"], "unavailable", f"ESPN summary failed: {err}")
        return
    detail.update(channel=game.get("channel"), nets=game.get("nets"), alert=game.get("alert"), league=game["league"])
    connection.send_result(msg["id"], detail)
