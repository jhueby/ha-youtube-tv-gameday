"""Scoreboards from ESPN's public site API, and matching games to YouTube TV channels."""
from __future__ import annotations

import asyncio
import logging
import re
import unicodedata
from datetime import datetime
from typing import Any

import aiohttp

from .const import ESPN, LEAGUES, LOCAL_NETWORKS, NETWORKS, PREFERENCE

_LOGGER = logging.getLogger(__name__)
_NOT_TV = {"eradm", "radio", "espn radio", "siriusxm", "sirius xm"}


def norm(text: str | None) -> str:
    """Lower-case, ASCII, punctuation to spaces ('+' is kept so "ESPN+" never reads as ESPN)."""
    s = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode().lower()
    s = s.replace("&", " and ")
    s = re.sub(r"[^a-z0-9+ ]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def network_brand(name: str) -> str | None:
    """ESPN broadcast name -> our network brand ("ESPN", "FS1", ...), or None."""
    n = norm(name)
    if "+" in n:          # ESPN+, Paramount+, Peacock+ ... are streaming services, not channels
        return None
    for brand, (_, aliases) in NETWORKS.items():
        if n in aliases:
            return brand
    return None


class ChannelMap:
    """Resolve networks to channels in one user's YouTube TV lineup."""

    def __init__(self, lineup: dict[str, str], locals_: dict[str, str]) -> None:
        self.lineup = lineup
        by_norm = {norm(name): name for name in lineup}
        self.brand_channel: dict[str, str] = {}
        for brand, (_, aliases) in NETWORKS.items():
            if brand in LOCAL_NETWORKS:
                chan = locals_.get(brand)
                if chan in lineup:
                    self.brand_channel[brand] = chan
                continue
            for alias in [norm(brand), *aliases]:
                if alias in by_norm:
                    self.brand_channel[brand] = by_norm[alias]
                    break

    def channel_for(self, networks: list[str]) -> str | None:
        brands = {b for b in (network_brand(n) for n in networks) if b and b in self.brand_channel}
        if not brands:
            return None
        best = min(brands, key=PREFERENCE.index)
        return self.brand_channel[best]

    def quick_channels(self) -> list[dict[str, str]]:
        """Sports channels the user has, in preference order, for the card's channel bar."""
        out = []
        for brand in PREFERENCE:
            if brand in self.brand_channel and brand not in ("CNBC", "Galavision", "ESPNews"):
                out.append({"channel": self.brand_channel[brand], "label": NETWORKS[brand][0]})
        return out


def _networks(comp: dict[str, Any]) -> list[str]:
    names: list[str] = []
    for b in comp.get("broadcasts") or []:
        if not isinstance(b, dict):
            continue
        if norm((b.get("type") or {}).get("shortName")) == "radio":
            continue
        media = b.get("media") or {}
        if media.get("shortName"):
            names.append(media["shortName"])
        names += b.get("names") or []
    for g in comp.get("geoBroadcasts") or []:
        if norm((g.get("type") or {}).get("shortName") or "tv") == "tv":
            names.append((g.get("media") or {}).get("shortName"))
    out: list[str] = []
    for n in names:
        if n and n not in out and norm(n) not in _NOT_TV:
            out.append(n)
    return out


def _game(league_key: str, label: str, sport: str, event: dict[str, Any], channels: ChannelMap) -> dict[str, Any]:
    comp = (event.get("competitions") or [{}])[0]
    status = (comp.get("status") or event.get("status") or {}).get("type") or {}
    situation = comp.get("situation") or {}
    teams = []
    for t in sorted(comp.get("competitors") or [], key=lambda c: c.get("homeAway") != "away"):
        team = t.get("team") or {}
        rank = (t.get("curatedRank") or {}).get("current")
        teams.append({
            "name": team.get("shortDisplayName") or team.get("displayName"),
            "full": team.get("displayName"),
            "abbr": team.get("abbreviation"),
            "logo": team.get("logo") or ((team.get("logos") or [{}])[0].get("href")),
            "color": team.get("color"),
            "score": t.get("score"),
            "winner": t.get("winner"),
            "rank": rank if rank and rank <= 25 else None,
            "record": ((t.get("records") or [{}])[0]).get("summary"),
            "poss": bool(situation.get("possession")) and situation.get("possession") == team.get("id"),
        })
    live = status.get("state") == "in"
    sit: dict[str, Any] = {}
    if live and sport == "baseball":
        sit = {k: situation.get(k) for k in ("balls", "strikes", "outs", "onFirst", "onSecond", "onThird")}
    elif live and sport == "football":
        sit = {"down": situation.get("shortDownDistanceText") or situation.get("downDistanceText"),
               "redzone": situation.get("isRedZone")}
    nets = _networks(comp)
    return {
        "id": event.get("id"), "league_key": league_key, "league": label, "sport": sport,
        "state": status.get("state", "pre"), "detail": status.get("shortDetail") or status.get("detail"),
        "start": event.get("date"), "tbd": comp.get("timeValid", True) is False,
        "teams": teams, "situation": sit, "nets": nets, "channel": channels.channel_for(nets),
        "note": ((comp.get("notes") or [{}])[0]).get("headline"),
    }


async def fetch_scoreboard(session: aiohttp.ClientSession, leagues: list[str], day: datetime,
                           channels: ChannelMap) -> list[dict[str, Any]]:
    """Today's games for the chosen leagues (one ESPN call per league slug, in parallel)."""
    date = day.strftime("%Y%m%d")
    jobs = []
    for key in leagues:
        if key not in LEAGUES:
            continue
        label, sport, slugs, extra = LEAGUES[key]
        for slug in slugs:
            jobs.append((key, label, sport, f"{ESPN}/{sport}/{slug}/scoreboard?dates={date}{extra}"))

    async def one(job):
        key, label, sport, url = job
        try:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                if resp.status != 200:
                    return []
                data = await resp.json(content_type=None)
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as err:
            _LOGGER.debug("ESPN %s failed: %s", url, err)
            return []
        return [_game(key, label, sport, e, channels) for e in data.get("events") or []]

    results = await asyncio.gather(*(one(j) for j in jobs))
    seen: set[str] = set()
    games = []
    for game in (g for lst in results for g in lst):
        if game["id"] not in seen:          # a match can be listed under several soccer competitions
            seen.add(game["id"])
            games.append(game)
    return games
