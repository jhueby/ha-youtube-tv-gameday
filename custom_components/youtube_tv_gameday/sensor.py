"""Sensors: what the TV was last tuned to, and how many games are live."""
from __future__ import annotations

from homeassistant.components.sensor import SensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import GameDayConfigEntry
from .const import DOMAIN
from .coordinator import GameDayCoordinator


async def async_setup_entry(hass: HomeAssistant, entry: GameDayConfigEntry,
                            async_add_entities: AddEntitiesCallback) -> None:
    coord = entry.runtime_data
    async_add_entities([NowOnSensor(coord, entry), LiveGamesSensor(coord, entry)])


class _Base(CoordinatorEntity[GameDayCoordinator], SensorEntity):
    _attr_has_entity_name = True

    def __init__(self, coord: GameDayCoordinator, entry: GameDayConfigEntry, key: str) -> None:
        super().__init__(coord)
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_translation_key = key
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, entry.entry_id)}, name="YouTube TV Gameday",
                                            model=entry.title, entry_type=DeviceEntryType.SERVICE)


class NowOnSensor(_Base):
    """Channel last tuned by this integration (it can't see changes made with the remote)."""

    _attr_icon = "mdi:television-play"

    def __init__(self, coord, entry) -> None:
        super().__init__(coord, entry, "now_on")

    @property
    def native_value(self) -> str | None:
        return self.coordinator.now_channel

    @property
    def extra_state_attributes(self) -> dict:
        c = self.coordinator
        return {"game": c.now_label, "tuned_at": c.tuned_at.isoformat() if c.tuned_at else None}


class LiveGamesSensor(_Base):
    """Number of games live right now (attribute lists your favorites that are live)."""

    _attr_icon = "mdi:scoreboard"
    _attr_native_unit_of_measurement = "games"

    def __init__(self, coord, entry) -> None:
        super().__init__(coord, entry, "live_games")

    @property
    def native_value(self) -> int:
        return sum(1 for g in self.coordinator.data or [] if g["state"] == "in")

    @property
    def extra_state_attributes(self) -> dict:
        favs = {f.lower() for f in self.coordinator.favorites}
        live = [g for g in self.coordinator.data or [] if g["state"] == "in"]
        mine = [g for g in live if any(str(t.get(k) or "").lower() in favs
                                       for t in g["teams"] for k in ("name", "full", "abbr"))]
        return {"favorites_live": [
            {"game": " vs ".join(t["name"] or "" for t in g["teams"]), "score": " - ".join(str(t["score"]) for t in g["teams"]),
             "detail": g["detail"], "channel": g["channel"]} for g in mine]}
