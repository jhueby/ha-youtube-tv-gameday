"""Find TVs running YouTube TV (DIAL) and tune them to a live channel (YouTube Lounge API).

This is the same mechanism the YouTube TV phone app uses for "play on TV": the TV's
YouTube TV app advertises a lounge `screenId` over DIAL; we exchange it for a lounge token,
bind a remote-control session and send `setPlaylist` with the channel's live video id.
Unofficial and undocumented: it may change without notice.
"""
from __future__ import annotations

import asyncio
import logging
import re
import socket
import uuid
from dataclasses import dataclass
from urllib.parse import urlencode

import aiohttp

_LOGGER = logging.getLogger(__name__)
APP = "YouTubeTV"
LOUNGE = "https://www.youtube.com/api/lounge"
TIMEOUT = aiohttp.ClientTimeout(total=15)
# The TV refuses DIAL launches that don't claim to come from YouTube (Samsung returns 403).
DIAL_HEADERS = {"Origin": "https://tv.youtube.com", "Content-Type": "text/plain; charset=utf-8"}


class TuneError(Exception):
    """Tuning failed."""


@dataclass
class DialDevice:
    name: str
    host: str
    app_url: str          # DIAL Application-URL, e.g. http://192.168.1.50:8080/ws/apps/


def app_endpoint(app_url: str) -> str:
    return app_url.rstrip("/") + "/" + APP


async def probe(session: aiohttp.ClientSession, app_url: str) -> bool:
    """True if this DIAL server knows the YouTube TV app."""
    try:
        async with session.get(app_endpoint(app_url), timeout=TIMEOUT) as resp:
            return resp.status == 200 and f"<name>{APP}</name>" in await resp.text()
    except (aiohttp.ClientError, asyncio.TimeoutError):
        return False


async def guess_app_urls(session: aiohttp.ClientSession, host: str) -> str | None:
    """Manual entry: try the DIAL app URLs common TVs/streamers use."""
    for url in (f"http://{host}:8080/ws/apps/",   # Samsung Tizen
                f"http://{host}:8008/apps/",      # Google Cast / Android TV
                f"http://{host}:8060/dial/",      # Roku
                f"http://{host}:56789/apps/"):    # LG webOS
        if await probe(session, url):
            return url
    return None


async def discover(session: aiohttp.ClientSession, wait: float = 4.0) -> list[DialDevice]:
    """SSDP M-SEARCH for DIAL servers, keep the ones that have the YouTube TV app."""
    locations: dict[str, str] = {}

    class _Proto(asyncio.DatagramProtocol):
        def datagram_received(self, data: bytes, addr) -> None:
            m = re.search(rb"(?im)^location:\s*(\S+)", data)
            if m:
                locations[m.group(1).decode(errors="ignore")] = addr[0]

    loop = asyncio.get_running_loop()
    transport, _ = await loop.create_datagram_endpoint(_Proto, local_addr=("0.0.0.0", 0), family=socket.AF_INET)
    query = ("M-SEARCH * HTTP/1.1\r\nHOST: 239.255.255.250:1900\r\nMAN: \"ssdp:discover\"\r\nMX: 2\r\n"
             "ST: urn:dial-multiscreen-org:service:dial:1\r\n\r\n").encode()
    try:
        for _ in range(3):
            transport.sendto(query, ("239.255.255.250", 1900))
            await asyncio.sleep(0.4)
        await asyncio.sleep(wait)
    finally:
        transport.close()

    async def check(location: str, host: str) -> DialDevice | None:
        try:
            async with session.get(location, timeout=TIMEOUT) as resp:
                app_url = resp.headers.get("Application-URL")
                body = await resp.text()
        except (aiohttp.ClientError, asyncio.TimeoutError):
            return None
        if not app_url or not await probe(session, app_url):
            return None
        m = re.search(r"<friendlyName>([^<]+)</friendlyName>", body)
        name = (m.group(1) if m else host).replace("&quot;", '"').replace("&amp;", "&")
        return DialDevice(name=name, host=host, app_url=app_url)

    found = await asyncio.gather(*(check(loc, host) for loc, host in locations.items()))
    unique: dict[str, DialDevice] = {}
    for dev in found:
        if dev:
            unique.setdefault(dev.app_url, dev)
    return list(unique.values())


async def tune(session: aiohttp.ClientSession, app_url: str, video_id: str) -> None:
    """Bring YouTube TV to the front and play a live channel. Raises TuneError."""
    endpoint = app_endpoint(app_url)
    dial = None
    for attempt in range(8):   # a TV that just woke up can take a while to answer DIAL
        try:
            async with session.get(endpoint, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                dial = await resp.text()
            break
        except (aiohttp.ClientError, asyncio.TimeoutError) as err:
            if attempt == 7:
                raise TuneError(f"TV not reachable ({err}) — is it on, and is network wake-up enabled?") from err
            await asyncio.sleep(4)
    running = "<state>running</state>" in dial
    try:  # launch / foreground the app
        async with session.post(endpoint, data=b"", headers=DIAL_HEADERS, timeout=TIMEOUT):
            pass
    except (aiohttp.ClientError, asyncio.TimeoutError):
        pass
    m = re.search(r"<screenId>([^<]+)</screenId>", dial) if running else None
    for _ in range(6 if not m else 0):   # app is starting: wait for it to register its lounge screen
        await asyncio.sleep(4)
        try:
            async with session.get(endpoint, timeout=TIMEOUT) as resp:
                dial = await resp.text()
        except (aiohttp.ClientError, asyncio.TimeoutError):
            continue
        m = re.search(r"<screenId>([^<]+)</screenId>", dial)
        if m and "<state>running</state>" in dial:
            await asyncio.sleep(3)          # give the app a moment past "running" before tuning
            break
    if not m:
        raise TuneError("YouTube TV on the TV did not report a screen id (is the app installed and signed in?)")
    screen_id = m.group(1)

    last: Exception | None = None
    for _ in range(3):
        try:
            async with session.post(f"{LOUNGE}/pairing/get_lounge_token_batch",
                                    data={"screen_ids": screen_id}, timeout=TIMEOUT) as resp:
                token = (await resp.json(content_type=None))["screens"][0]["loungeToken"]
            common = {"device": "REMOTE_CONTROL", "app": "youtube-desktop", "name": "Home Assistant",
                      "id": str(uuid.uuid4()), "loungeIdToken": token, "VER": "8", "v": "2",
                      "theme": "up", "mdx-version": "3", "CVER": "1", "zx": "x"}
            async with session.post(f"{LOUNGE}/bc/bind?" + urlencode({**common, "RID": "1"}),
                                    data={"count": "0"}, timeout=TIMEOUT) as resp:
                text = await resp.text()
            sid = re.search(r'"c","([^"]+)"', text).group(1)
            gsession = re.search(r'"S","([^"]+)"', text).group(1)
            query = {**common, "SID": sid, "gsessionid": gsession, "RID": "2", "AID": "5"}
            async with session.post(
                    f"{LOUNGE}/bc/bind?" + urlencode(query),
                    data={"count": "1", "ofs": "0", "req0__sc": "setPlaylist", "req0_videoId": video_id,
                          "req0_currentTime": "0", "req0_currentIndex": "0", "req0_videoIds": video_id,
                          "req0_listId": "", "req0_audioOnly": "false"}, timeout=TIMEOUT) as resp:
                if resp.status >= 400:
                    raise TuneError(f"lounge setPlaylist HTTP {resp.status}")
            return
        except (aiohttp.ClientError, asyncio.TimeoutError, KeyError, IndexError, AttributeError, TuneError) as err:
            last = err
            await asyncio.sleep(4)
    raise TuneError(f"could not tune ({last})")
