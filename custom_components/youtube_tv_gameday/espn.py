"""Scoreboards from ESPN's public site API, and matching games to YouTube TV channels."""
from __future__ import annotations

import asyncio
import logging
import re
import unicodedata
from datetime import datetime
from typing import Any

import aiohttp

from .const import ESPN, ESPN_HEADER, LEAGUES, LOCAL_NETWORKS, NETWORKS, PREFERENCE, STREAMING, TOP_SPORTS

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
    """Resolve networks to channels in one user's YouTube TV lineup (and streaming apps on the TV)."""

    def __init__(self, lineup: dict[str, str], locals_: dict[str, str], apps: dict[str, str] | None = None) -> None:
        self.lineup = lineup
        self.apps = apps or {}            # streaming service -> DIAL app name found on the TV
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

    def app_for(self, networks: list[str]) -> str | None:
        """Streaming service (installed on the TV) that carries the game, if any."""
        for n in networks:
            key = norm(n)
            for service, (aliases, _) in STREAMING.items():
                if key in aliases and service in self.apps:
                    return service
        return None

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


def _game(league_key: str, label: str, sport: str, slug: str, event: dict[str, Any],
          channels: ChannelMap) -> dict[str, Any]:
    comp = (event.get("competitions") or [{}])[0]
    st = comp.get("status") or event.get("status") or {}
    status = st.get("type") or {}
    situation = comp.get("situation") or {}
    cards: dict[str, list[int]] = {}           # soccer: team id -> [yellow, red], from the match details
    if sport == "soccer":
        for det in comp.get("details") or []:
            c = cards.setdefault(str((det.get("team") or {}).get("id")), [0, 0])
            if det.get("redCard"):
                c[1] += 1
            elif det.get("yellowCard"):
                c[0] += 1
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
            **({"yc": cards.get(str(team.get("id")), [0, 0])[0], "rc": cards.get(str(team.get("id")), [0, 0])[1]}
               if sport == "soccer" else {}),
        })
    live = status.get("state") == "in"
    sit: dict[str, Any] = {}
    if live and sport == "baseball":
        sit = {k: situation.get(k) for k in ("balls", "strikes", "outs", "onFirst", "onSecond", "onThird")}
        for who in ("pitcher", "batter"):                 # current at-bat (names + today's line + id)
            info = situation.get(who) or {}
            ath = info.get("athlete") or {}
            if ath:
                sit[who] = {"name": ath.get("shortName") or ath.get("displayName"),
                            "summary": info.get("summary"), "pid": ath.get("id")}
    elif live and sport == "football":
        sit = {"down": situation.get("shortDownDistanceText") or situation.get("downDistanceText"),
               "redzone": situation.get("isRedZone")}
    nets = _networks(comp)
    state = status.get("state", "pre")
    period, clock = st.get("period") or 0, st.get("clock")
    return {
        "id": event.get("id"), "league_key": league_key, "league": label, "sport": sport, "slug": slug,
        "state": state, "detail": status.get("shortDetail") or status.get("detail"),
        "alert": game_alert(sport, league_key, state, period, clock, teams),
        "start": event.get("date"), "tbd": comp.get("timeValid", True) is False,
        "teams": teams, "situation": sit, "nets": nets, "channel": (chan := channels.channel_for(nets)),
        "app": None if chan else channels.app_for(nets),
        "note": ((comp.get("notes") or [{}])[0]).get("headline"),
    }


def _ordinal(n: int) -> str:
    return f"{n}{'th' if 11 <= n % 100 <= 13 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"


def game_alert(sport: str, league_key: str, state: str, period: int, clock: float | None,
               teams: list[dict[str, Any]]) -> dict[str, str] | None:
    """Is this live game worth interrupting someone for? -> {"kind": close|ot|upset, "text": ...}."""
    if state != "in" or len(teams) != 2:
        return None
    try:
        away, home = (int(float(t.get("score") or 0)) for t in teams)
    except (TypeError, ValueError):
        return None
    margin = abs(away - home)
    secs = float(clock) if clock is not None else None
    left = f"{int(secs // 60)}:{int(secs % 60):02d} left" if secs is not None else ""
    leader = teams[0] if away > home else teams[1]
    tied = "Tied" if margin == 0 else f"{leader.get('name')} by {margin}"

    if sport == "football":
        if period >= 5:
            return {"kind": "ot", "text": f"Overtime · {tied}"}
        if period == 4 and secs is not None and secs <= 300 and margin <= 8:
            return {"kind": "close", "text": f"One-score game · {left} in the 4th"}
    elif sport == "basketball":
        final = 2 if league_key == "ncaab" else 4
        if period > final:
            return {"kind": "ot", "text": f"Overtime · {tied}"}
        if period == final and secs is not None and secs <= 300 and margin <= 6:
            return {"kind": "close", "text": f"{tied} · {left}"}
    elif sport == "hockey":
        if period > 3:
            return {"kind": "ot", "text": f"Overtime · {tied}"}
        if period == 3 and margin <= 1:
            return {"kind": "close", "text": f"{'Tied' if margin == 0 else 'One-goal game'} · {left} in the 3rd"}
    elif sport == "baseball":
        if period > 9 and margin <= 2:
            return {"kind": "ot", "text": f"Extra innings · {tied}"}
        if period >= 8 and margin <= 2:
            return {"kind": "close", "text": f"{tied} in the {_ordinal(period)}"}
    elif sport == "soccer":
        if period >= 3:
            return {"kind": "ot", "text": f"Extra time · {tied}"}
        if period == 2 and secs is not None and secs >= 75 * 60 and margin <= 1:
            return {"kind": "close", "text": f"{tied} · {int(secs // 60)}'"}

    # Upset watch: a ranked team trailing an unranked / lower-ranked one in the second half
    late = period >= {"football": 3, "basketball": 2 if league_key == "ncaab" else 3}.get(sport, 99)
    if late and margin > 0:
        trailing = teams[1] if away > home else teams[0]
        lr, tr = leader.get("rank") or 99, trailing.get("rank")
        if tr and tr < lr:
            return {"kind": "upset", "text": f"Upset alert · #{tr} {trailing.get('name')} trails by {margin}"}
    return None


def _boxscore_line(boxscore: dict[str, Any], player_id: Any, kind: str) -> tuple[str | None, dict[str, str]]:
    """One player's batting/pitching line from the summary boxscore, as (name, {label: value})."""
    pid = str(player_id)
    for team in boxscore.get("players") or []:
        for block in team.get("statistics") or []:
            if block.get("type") != kind:
                continue
            labels = block.get("labels") or []
            for ath in block.get("athletes") or []:
                a = ath.get("athlete") or {}
                if str(a.get("id")) == pid:
                    return (a.get("shortName") or a.get("displayName")), dict(zip(labels, ath.get("stats") or []))
    return None, {}


def _at_bat(summary: dict[str, Any], pitcher_id: Any = None, batter_id: Any = None) -> dict[str, Any] | None:
    """Baseball: who's pitching/batting right now, with pitch count and season AVG/OBP/ERA.
    The summary's situation is blank between innings, so the current players come from the
    scoreboard (pitcher_id/batter_id); the summary boxscore supplies their season stats."""
    sit = summary.get("situation") or {}
    box = summary.get("boxscore") or {}
    pp = (sit.get("pitcher") or {}).get("playerId") or pitcher_id
    bp = (sit.get("batter") or {}).get("playerId") or batter_id
    pname, p = _boxscore_line(box, pp, "pitching") if pp else (None, {})
    bname, b = _boxscore_line(box, bp, "batting") if bp else (None, {})
    pcst = p.get("PC-ST")
    out: dict[str, Any] = {k: sit.get(k) for k in ("balls", "strikes", "outs")}
    out["pitcher"] = {"name": pname, "ip": p.get("IP"), "pc": p.get("PC") or (pcst.split("-")[0] if pcst else None),
                      "pcst": pcst, "era": p.get("ERA")} if pname else None
    out["batter"] = {"name": bname, "today": b.get("H-AB"), "avg": b.get("AVG"), "obp": b.get("OBP")} if bname else None
    return out if (out["pitcher"] or out["batter"]) else None


_SOCCER_QUIET = {"kickoff", "start-delay", "end-delay", "start-2nd-half", "end-regular-time"}


def _soccer_timeline(summary: dict[str, Any], teams: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Goals, cards and substitutions with their minute and side ("away" / "home"), oldest first.
    ESPN credits an own goal to the team it counts for, and a card to the player's team."""
    side = {str(t.get("id")): ("away" if i == 0 else "home") for i, t in enumerate(teams)}  # teams: away first
    out = []
    for k in summary.get("keyEvents") or []:
        code = (k.get("type") or {}).get("type") or ""
        if k.get("shootout"):
            continue
        if code == "own-goal":
            kind = "own_goal"
        elif code == "penalty---scored":
            kind = "pen_goal"
        elif code.startswith("penalty"):
            kind = "pen_miss"
        elif k.get("scoringPlay") or code.startswith("goal"):
            kind = "goal"
        elif "red" in code:                    # red-card, yellow-red (second yellow)
            kind = "red"
        elif code == "yellow-card":
            kind = "yellow"
        elif code == "substitution":
            kind = "sub"
        else:
            continue
        label = (k.get("clock") or {}).get("displayValue") or ""
        m = re.match(r"(\d+)'?(?:\+(\d+))?", label)
        names = [(p.get("athlete") or {}).get("displayName") for p in k.get("participants") or []]
        out.append({"min": int(m.group(1)) if m else 0, "add": int(m.group(2)) if m and m.group(2) else 0,
                    "label": label, "side": side.get(str((k.get("team") or {}).get("id"))), "kind": kind,
                    "who": names[0] if names else None,
                    "out": names[1] if kind == "sub" and len(names) > 1 else None})   # sub: [on, off]
    return out


async def fetch_summary(session: aiohttp.ClientSession, sport: str, slug: str, event_id: str,
                        pitcher_id: Any = None, batter_id: Any = None) -> dict[str, Any]:
    """One game's detail for the card's long-press sheet."""
    url = f"{ESPN}/{sport}/{slug}/summary?event={event_id}"
    async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
        resp.raise_for_status()
        s = await resp.json(content_type=None)

    comp = ((s.get("header") or {}).get("competitions") or [{}])[0]
    teams = []
    for c in sorted(comp.get("competitors") or [], key=lambda c: c.get("homeAway") != "away"):
        tm = c.get("team") or {}
        teams.append({"id": tm.get("id"), "name": tm.get("displayName"), "abbr": tm.get("abbreviation"),
                      "logo": ((tm.get("logos") or [{}])[0]).get("href") or tm.get("logo"),
                      "color": tm.get("color"), "score": c.get("score"),
                      "periods": [l.get("displayValue") for l in c.get("linescores") or []],
                      "record": ((c.get("record") or [{}])[0]).get("summary") if c.get("record") else None})
    status = (comp.get("status") or {}).get("type") or {}

    # win probability (home %, 0-100), thinned to <= 90 points
    wp = [round(100 * float(p.get("homeWinPercentage") or 0), 1) for p in s.get("winprobability") or []]
    if len(wp) > 90:
        step = len(wp) / 90
        wp = [wp[int(i * step)] for i in range(90)] + [wp[-1]]

    # leaders: top 3 categories per team, best player in each
    leaders = []
    for block in s.get("leaders") or []:
        tm = block.get("team") or {}
        cats = []
        for cat in (block.get("leaders") or [])[:3]:
            top = (cat.get("leaders") or [{}])[0]
            if top.get("displayValue"):
                cats.append({"cat": cat.get("displayName"), "who": (top.get("athlete") or {}).get("shortName"),
                             "line": top.get("displayValue")})
        if cats:
            leaders.append({"team": tm.get("abbreviation") or tm.get("displayName"), "cats": cats})

    # latest plays, newest first
    if sport == "football":
        plays = [p.get("text") for p in ((s.get("drives") or {}).get("current") or {}).get("plays") or []]
    elif sport == "soccer":
        plays = [k.get("text") for k in s.get("keyEvents") or []
                 if (k.get("type") or {}).get("type") not in _SOCCER_QUIET]
    else:
        plays = [p.get("text") for p in s.get("plays") or []]
    plays = [p for p in plays if p][-6:][::-1]

    pick = (s.get("pickcenter") or [{}])[0]
    info = s.get("gameInfo") or {}
    weather = info.get("weather") or {}
    atbat = _at_bat(s, pitcher_id, batter_id) if sport == "baseball" and status.get("state") == "in" else None
    return {
        "id": event_id, "state": status.get("state"), "detail": status.get("shortDetail") or status.get("detail"),
        "teams": teams, "winprob": wp, "leaders": leaders, "plays": plays, "atbat": atbat,
        "timeline": _soccer_timeline(s, teams) if sport == "soccer" else None,
        "odds": {"line": pick.get("details"), "total": pick.get("overUnder")} if pick.get("details") else None,
        "venue": (info.get("venue") or {}).get("fullName"),
        "weather": f"{weather['temperature']}° {weather.get('conditionId') or ''}".strip()
                   if weather.get("temperature") is not None else None,
        "attendance": info.get("attendance"),
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
            jobs.append((key, label, sport, slug, f"{ESPN}/{sport}/{slug}/scoreboard?dates={date}{extra}"))

    async def one(job):
        key, label, sport, slug, url = job
        try:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                if resp.status != 200:
                    return []
                data = await resp.json(content_type=None)
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as err:
            _LOGGER.debug("ESPN %s failed: %s", url, err)
            return []
        return [_game(key, label, sport, slug, e, channels) for e in data.get("events") or []]

    results = await asyncio.gather(*(one(j) for j in jobs))
    seen: set[str] = set()
    games = []
    for game in (g for lst in results for g in lst):
        if game["id"] not in seen:          # a match can be listed under several soccer competitions
            seen.add(game["id"])
            games.append(game)
    return games


async def fetch_top_events(session: aiohttp.ClientSession, day: datetime, channels: ChannelMap,
                           followed: set[tuple[str, str]]) -> list[dict[str, Any]]:
    """ESPN's featured games (its homepage scores bar) from leagues you don't follow, e.g. El Clasico.

    The bar only lists event ids, so each league's own scoreboard is fetched and just those games are kept:
    they then get the same channels, alerts and detail sheet as any other game."""
    try:
        async with session.get(ESPN_HEADER, timeout=aiohttp.ClientTimeout(total=15)) as resp:
            if resp.status != 200:
                return []
            data = await resp.json(content_type=None)
    except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as err:
        _LOGGER.debug("ESPN featured games failed: %s", err)
        return []

    by_slug = {(sport, slug): (key, label, extra)
               for key, (label, sport, slugs, extra) in LEAGUES.items() for slug in slugs}
    wanted: dict[tuple[str, str], tuple[str, str, str, set[str]]] = {}
    for sp in data.get("sports") or []:
        sport = sp.get("slug")
        if sport not in TOP_SPORTS:
            continue
        for lg in sp.get("leagues") or []:
            slug = lg.get("slug")
            if not slug or (sport, slug) in followed:
                continue
            ids = {str(e["id"]) for e in lg.get("events") or [] if e.get("id")}
            if not ids:
                continue
            key, label, extra = by_slug.get((sport, slug)) or (
                "top", lg.get("abbreviation") or lg.get("shortName") or lg.get("name") or slug, "")
            wanted[(sport, slug)] = (key, label, extra, ids)

    date = day.strftime("%Y%m%d")

    async def one(sport: str, slug: str, key: str, label: str, extra: str, ids: set[str]):
        url = f"{ESPN}/{sport}/{slug}/scoreboard?dates={date}{extra}"
        try:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                if resp.status != 200:
                    return []
                board = await resp.json(content_type=None)
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as err:
            _LOGGER.debug("ESPN %s failed: %s", url, err)
            return []
        out = []
        for e in board.get("events") or []:
            if str(e.get("id")) in ids:
                g = _game(key, label, sport, slug, e, channels)
                g["top"] = True
                out.append(g)
        return out

    results = await asyncio.gather(*(one(sp, sl, *v) for (sp, sl), v in wanted.items()))
    return [g for lst in results for g in lst]
