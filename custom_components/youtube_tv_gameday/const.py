"""Constants for YouTube TV Gameday."""
from __future__ import annotations

DOMAIN = "youtube_tv_gameday"
VERSION = "0.5.4"

CONF_APP_URL = "app_url"
CONF_LINEUP = "lineup"            # {channel name as shown in YouTube TV: live watch id}
CONF_LOCALS = "locals"            # {"ABC": "<lineup channel name>", ...}
CONF_FAVORITES = "favorites"      # ["Guardians", "Cavaliers"]
CONF_LEAGUES = "leagues"          # keys of LEAGUES
CONF_POWER_ENTITY = "power_entity"
CONF_LOCAL_TEAMS = "local_teams"      # teams your local CBS/FOX always carry (NFL regional coverage)
CONF_ALERT_SCOPE = "alert_scope"    # which games alert: favorites / ranked / my_channels / all
CONF_ALERT_NOTIFY = "alert_notify"  # notify service (e.g. notify.mobile_app_phone), optional

ALERT_SCOPES = ["favorites", "ranked", "my_channels", "all"]
DEFAULT_ALERT_SCOPE = ["favorites", "ranked"]
EVENT_ALERT = "youtube_tv_gameday_alert"
ACTION_PREFIX = "YTGD_TUNE|"         # mobile_app notification action: YTGD_TUNE|<channel>

CARD_URL = "/youtube_tv_gameday/youtube-tv-gameday-card.js"
ESPN = "https://site.api.espn.com/apis/site/v2/sports"

# key: (label, sport, [league slugs], extra query)
LEAGUES: dict[str, tuple[str, str, list[str], str]] = {
    "nfl": ("NFL", "football", ["nfl"], ""),
    "ncaaf": ("College Football", "football", ["college-football"], "&groups=80&limit=300"),
    "mlb": ("MLB", "baseball", ["mlb"], ""),
    "nba": ("NBA", "basketball", ["nba"], ""),
    "wnba": ("WNBA", "basketball", ["wnba"], ""),
    "ncaab": ("College Basketball", "basketball", ["mens-college-basketball"], "&groups=50&limit=300"),
    "nhl": ("NHL", "hockey", ["nhl"], ""),
    "epl": ("Premier League", "soccer", ["eng.1"], ""),
    "ucl": ("Champions League", "soccer", ["uefa.champions"], ""),
    "mls": ("MLS", "soccer", ["usa.1"], ""),
    "intl": ("International", "soccer", [
        "fifa.friendly", "fifa.world", "fifa.worldq.concacaf", "fifa.worldq.uefa", "fifa.worldq.conmebol",
        "concacaf.nations.league", "concacaf.gold", "conmebol.america", "uefa.euro", "uefa.nations",
        "fifa.friendly.w", "fifa.wwc", "concacaf.w.gold", "uefa.weuro"], ""),
}
DEFAULT_LEAGUES = ["nfl", "ncaaf", "mlb", "nba", "nhl", "epl", "ucl", "intl"]

# Broadcast networks we know how to find in a YouTube TV lineup.
# brand: (short label, [names ESPN uses / names YouTube TV uses, normalised])
NETWORKS: dict[str, tuple[str, list[str]]] = {
    "ESPN": ("ESPN", ["espn"]),
    "ABC": ("ABC", ["abc"]),
    "FOX": ("FOX", ["fox"]),
    "CBS": ("CBS", ["cbs"]),
    "NBC": ("NBC", ["nbc"]),
    "TBS": ("TBS", ["tbs"]),
    "TNT": ("TNT", ["tnt"]),
    "FS1": ("FS1", ["fs1", "fox sports 1"]),
    "ESPN2": ("ESPN2", ["espn2"]),
    "USA Network": ("USA", ["usa net", "usa network", "usa"]),
    "NFL Network": ("NFL Net", ["nfl net", "nfl network"]),
    "NBA TV": ("NBA TV", ["nba tv"]),
    "NHL Network": ("NHL Net", ["nhl network", "nhl net"]),
    "MLB Network": ("MLB Net", ["mlb network", "mlb net"]),
    "Big Ten Network": ("BTN", ["btn", "big ten network"]),
    "SEC Network": ("SECN", ["secn", "sec network"]),
    "ACC Network": ("ACCN", ["accn", "acc network"]),
    "ESPNU": ("ESPNU", ["espnu"]),
    "CBS Sports Network": ("CBSSN", ["cbssn", "cbs sports network"]),
    "FS2": ("FS2", ["fs2", "fox sports 2"]),
    "truTV": ("truTV", ["trutv"]),
    "Golf Channel": ("Golf", ["golf", "golf channel"]),
    "ESPNews": ("ESPNews", ["espnews"]),
    "CW": ("CW", ["cw", "the cw"]),
    "Telemundo": ("Telemundo", ["tele", "telemundo"]),
    "Univision": ("Univision", ["univision"]),
    "TUDN": ("TUDN", ["tudn"]),
    "Galavision": ("Galavision", ["galavision"]),
    "CNBC": ("CNBC", ["cnbc"]),
}
# Streaming services: games only on these can't be tuned, but we can open the app on the TV.
# name: ([normalised ESPN broadcast names], [DIAL app names to try])
STREAMING: dict[str, tuple[list[str], list[str]]] = {
    "Peacock": (["peacock"], ["Peacock"]),
    "Paramount+": (["paramount+", "paramount plus"], ["Paramount", "ParamountPlus", "CBSAllAccess"]),
    "Prime Video": (["prime video", "amazon prime video", "amazon"], ["AmazonInstantVideo", "PrimeVideo"]),
    "ESPN+": (["espn+"], ["ESPN"]),
    "Apple TV": (["apple tv", "apple tv+", "mls season pass"], ["AppleTV", "AppleTVPlus"]),
    "Netflix": (["netflix"], ["Netflix"]),
    "Max": (["max", "hbo max"], ["Max", "HBOMax"]),
}

# Over-the-air networks: the lineup shows the local station ("ABC 7"), so the user maps them.
LOCAL_NETWORKS = ["ABC", "CBS", "NBC", "FOX", "CW", "Telemundo", "Univision"]
# When a game is on several channels the user has, prefer this order (English national first).
PREFERENCE = list(NETWORKS)
