"""Setup: pick the TV, import the YouTube TV lineup, map local stations."""
from __future__ import annotations

import json
import re
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (EntitySelector, EntitySelectorConfig, SelectOptionDict,
                                            SelectSelector, SelectSelectorConfig, SelectSelectorMode,
                                            TextSelector, TextSelectorConfig)

from .const import (ALERT_SCOPES, CONF_ALERT_NOTIFY, CONF_ALERT_SCOPE, CONF_APP_URL, CONF_FAVORITES, CONF_LOCAL_TEAMS,
                    CONF_LEAGUES, CONF_LINEUP, CONF_LOCALS, CONF_POWER_ENTITY, CONF_TOP_EVENTS, DEFAULT_ALERT_SCOPE,
                    DEFAULT_LEAGUES, DEFAULT_TOP_EVENTS, DOMAIN, LEAGUES, LOCAL_NETWORKS)
from .espn import norm
from .youtube_tv import guess_app_urls, discover, probe

MANUAL = "manual"
NONE = "-"
_WATCH = re.compile(r"(?:watch/|^|(?<![A-Za-z0-9_-]))([A-Za-z0-9_-]{11})(?![A-Za-z0-9_-])")
_NOT_LOCAL = ("news", "business", "weather", "soul", "sports", "deportes", "live", "now")


def parse_lineup(text: str) -> dict[str, str]:
    """Accept the JSON from the lineup snippet, or lines of 'Channel name: <watch url or id>'."""
    text = (text or "").strip()
    out: dict[str, str] = {}
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            for name, value in data.items():
                m = _WATCH.search(str(value))
                if m and str(name).strip():
                    out[str(name).strip()] = m.group(1)
            return out
    except ValueError:
        pass
    for line in text.splitlines():
        if ":" not in line and "\t" not in line:
            continue
        name, _, value = line.replace("\t", ":", 1).partition(":")
        m = _WATCH.search(value.strip())
        if m and name.strip():
            out[name.strip()] = m.group(1)
    return out


def guess_locals(lineup: dict[str, str]) -> dict[str, str]:
    guesses: dict[str, str] = {}
    for brand in LOCAL_NETWORKS:
        key = norm(brand)
        cands = [n for n in lineup if key in norm(n).split() and not any(w in norm(n) for w in _NOT_LOCAL)]
        cands.sort(key=lambda n: (not re.search(r"\d", n), len(n)))   # prefer "FOX 5" over "FOX"
        if cands:
            guesses[brand] = cands[0]
    return guesses


def locals_schema(lineup: dict[str, str], current: dict[str, str]) -> vol.Schema:
    options = [SelectOptionDict(value=NONE, label="(not in my lineup)")] + \
              [SelectOptionDict(value=n, label=n) for n in sorted(lineup, key=str.lower)]
    sel = SelectSelector(SelectSelectorConfig(options=options, mode=SelectSelectorMode.DROPDOWN))
    return vol.Schema({vol.Optional(b, default=current.get(b, NONE)): sel for b in LOCAL_NETWORKS})


def _clean_locals(user_input: dict[str, Any]) -> dict[str, str]:
    return {b: v for b, v in user_input.items() if b in LOCAL_NETWORKS and v and v != NONE}


LINEUP_HELP = {"live_url": "https://tv.youtube.com/live", "example": "ESPN: https://tv.youtube.com/watch/..."}
LINEUP_SCHEMA = vol.Schema({vol.Required(CONF_LINEUP): TextSelector(TextSelectorConfig(multiline=True))})


class GameDayConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self) -> None:
        self._devices: dict[str, str] = {}
        self._app_url: str | None = None
        self._title = "YouTube TV Gameday"
        self._lineup: dict[str, str] = {}

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            if user_input["device"] == MANUAL:
                return await self.async_step_manual()
            self._app_url = user_input["device"]
            self._title = self._devices.get(self._app_url, "YouTube TV Gameday")
            return await self._chosen()
        session = async_get_clientsession(self.hass)
        found = await discover(session)
        self._devices = {d.app_url: d.name for d in found}
        labels = {d.app_url: f"{d.name} ({d.host})" for d in found}
        if not found:
            return await self.async_step_manual()
        options = [SelectOptionDict(value=u, label=labels[u]) for u in self._devices]
        options.append(SelectOptionDict(value=MANUAL, label="Enter the TV's address myself"))
        return self.async_show_form(step_id="user", data_schema=vol.Schema({
            vol.Required("device"): SelectSelector(SelectSelectorConfig(options=options))}))

    async def async_step_manual(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            session = async_get_clientsession(self.hass)
            value = user_input["host"].strip()
            if value.startswith("http"):
                url = value if await probe(session, value) else None
            else:
                url = await guess_app_urls(session, value)
            if url:
                self._app_url, self._title = url, value
                return await self._chosen()
            errors["base"] = "no_youtube_tv"
        return self.async_show_form(step_id="manual", errors=errors,
                                    data_schema=vol.Schema({vol.Required("host"): str}))

    async def _chosen(self) -> ConfigFlowResult:
        await self.async_set_unique_id(self._app_url)
        self._abort_if_unique_id_configured()
        return await self.async_step_lineup()

    async def async_step_lineup(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            self._lineup = parse_lineup(user_input[CONF_LINEUP])
            if self._lineup:
                return await self.async_step_locals()
            errors["base"] = "bad_lineup"
        return self.async_show_form(step_id="lineup", data_schema=LINEUP_SCHEMA, errors=errors,
                                    description_placeholders=LINEUP_HELP)

    async def async_step_locals(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(
                title=self._title, data={CONF_APP_URL: self._app_url},
                options={CONF_LINEUP: self._lineup, CONF_LOCALS: _clean_locals(user_input),
                         CONF_FAVORITES: [], CONF_LEAGUES: DEFAULT_LEAGUES})
        return self.async_show_form(step_id="locals", data_schema=locals_schema(self._lineup, guess_locals(self._lineup)),
                                    description_placeholders={"count": str(len(self._lineup))})

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return GameDayOptionsFlow()


class GameDayOptionsFlow(OptionsFlow):
    """Favorites/leagues/power, re-import the lineup, or remap local stations."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return self.async_show_menu(step_id="init", menu_options=["general", "lineup", "locals"])

    def _save(self, **changes: Any) -> ConfigFlowResult:
        return self.async_create_entry(data={**self.config_entry.options, **changes})

    async def async_step_general(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        opts = self.config_entry.options
        if user_input is not None:
            favs = [f.strip() for f in user_input.get(CONF_FAVORITES, "").split(",") if f.strip()]
            notify = user_input.get(CONF_ALERT_NOTIFY)
            return self._save(**{CONF_FAVORITES: favs, CONF_LEAGUES: user_input[CONF_LEAGUES],
                                 CONF_TOP_EVENTS: user_input.get(CONF_TOP_EVENTS, DEFAULT_TOP_EVENTS),
                                 CONF_POWER_ENTITY: user_input.get(CONF_POWER_ENTITY),
                                 CONF_LOCAL_TEAMS: [t.strip() for t in user_input.get(CONF_LOCAL_TEAMS, "").split(",") if t.strip()],
                                 CONF_ALERT_SCOPE: user_input.get(CONF_ALERT_SCOPE, []),
                                 CONF_ALERT_NOTIFY: None if notify in (None, NONE) else notify})
        league_opts = [SelectOptionDict(value=k, label=v[0]) for k, v in LEAGUES.items()]
        notify_opts = [SelectOptionDict(value=NONE, label="(no notifications — HA event only)")] + [
            SelectOptionDict(value=f"notify.{svc}", label=f"notify.{svc}")
            for svc in sorted(self.hass.services.async_services().get("notify", {}))]
        schema = vol.Schema({
            vol.Optional(CONF_FAVORITES, default=", ".join(opts.get(CONF_FAVORITES, []))): str,
            vol.Optional(CONF_LOCAL_TEAMS, default=", ".join(opts.get(CONF_LOCAL_TEAMS, []))): str,
            vol.Required(CONF_LEAGUES, default=opts.get(CONF_LEAGUES, DEFAULT_LEAGUES)):
                SelectSelector(SelectSelectorConfig(options=league_opts, multiple=True)),
            vol.Optional(CONF_TOP_EVENTS, default=opts.get(CONF_TOP_EVENTS, DEFAULT_TOP_EVENTS)): bool,
            vol.Optional(CONF_POWER_ENTITY, description={"suggested_value": opts.get(CONF_POWER_ENTITY)}):
                EntitySelector(EntitySelectorConfig(domain="media_player")),
            vol.Optional(CONF_ALERT_SCOPE, default=opts.get(CONF_ALERT_SCOPE, DEFAULT_ALERT_SCOPE)):
                SelectSelector(SelectSelectorConfig(options=ALERT_SCOPES, multiple=True,
                                                    translation_key=CONF_ALERT_SCOPE)),
            vol.Optional(CONF_ALERT_NOTIFY, default=opts.get(CONF_ALERT_NOTIFY) or NONE):
                SelectSelector(SelectSelectorConfig(options=notify_opts, mode=SelectSelectorMode.DROPDOWN)),
        })
        return self.async_show_form(step_id="general", data_schema=schema)

    async def async_step_lineup(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            lineup = parse_lineup(user_input[CONF_LINEUP])
            if lineup:
                locals_ = {b: c for b, c in self.config_entry.options.get(CONF_LOCALS, {}).items() if c in lineup}
                return self._save(**{CONF_LINEUP: lineup, CONF_LOCALS: locals_ or guess_locals(lineup)})
            errors["base"] = "bad_lineup"
        return self.async_show_form(step_id="lineup", data_schema=LINEUP_SCHEMA, errors=errors,
                                    description_placeholders=LINEUP_HELP)

    async def async_step_locals(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        lineup = self.config_entry.options.get(CONF_LINEUP, {})
        if user_input is not None:
            return self._save(**{CONF_LOCALS: _clean_locals(user_input)})
        current = self.config_entry.options.get(CONF_LOCALS) or guess_locals(lineup)
        return self.async_show_form(step_id="locals", data_schema=locals_schema(lineup, current),
                                    description_placeholders={"count": str(len(lineup))})
