"""Binary sensor: is a live game on the TV right now (so a wall tablet can hold the board up)?"""
from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import GameDayConfigEntry
from .const import CONF_POWER_ENTITY, DOMAIN
from .coordinator import GameDayCoordinator


async def async_setup_entry(hass: HomeAssistant, entry: GameDayConfigEntry,
                            async_add_entities: AddEntitiesCallback) -> None:
    async_add_entities([GameOnBinarySensor(entry.runtime_data, entry)])


class GameOnBinarySensor(CoordinatorEntity[GameDayCoordinator], BinarySensorEntity):
    """On while a live game is on the TV (until it ends, the channel changes, or the TV is turned off)."""

    _attr_has_entity_name = True
    _attr_translation_key = "game_on"
    _attr_icon = "mdi:television-play"

    def __init__(self, coord: GameDayCoordinator, entry: GameDayConfigEntry) -> None:
        super().__init__(coord)
        self._attr_unique_id = f"{entry.entry_id}_game_on"
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, entry.entry_id)}, name="YouTube TV Gameday",
                                            model=entry.title, entry_type=DeviceEntryType.SERVICE)

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        # Follow the TV's power entity too, so turning the TV off clears this without waiting for a poll.
        power = self.coordinator.config_entry.options.get(CONF_POWER_ENTITY)
        if power:
            self.async_on_remove(async_track_state_change_event(self.hass, [power], self._power_changed))

    @callback
    def _power_changed(self, _event) -> None:
        self.async_write_ha_state()

    @property
    def is_on(self) -> bool:
        return self.coordinator.game_on()

    @property
    def extra_state_attributes(self) -> dict:
        g = self.coordinator.now_game()
        if not g:
            return {"game": None, "channel": None, "detail": None}
        return {
            "game": self.coordinator.now_label or " @ ".join(str(t.get("abbr") or t.get("name") or "") for t in g["teams"]),
            "channel": self.coordinator.now_channel,
            "detail": g.get("detail"),
            "game_id": g["id"],
        }
