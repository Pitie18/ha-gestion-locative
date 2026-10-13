"""Interrupteur « Suspendre » : plus aucune commande sur la cible (travaux, pilotage manuel)."""
from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.const import STATE_ON
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from . import EntreeGestionLocative
from .entite import EntiteCible


async def async_setup_entry(
    hass: HomeAssistant, entry: EntreeGestionLocative, async_add_entities: AddEntitiesCallback
) -> None:
    pilote = entry.runtime_data.pilote
    async_add_entities(Suspendre(pilote, c) for c in pilote.cibles)


class Suspendre(EntiteCible, SwitchEntity, RestoreEntity):
    _attr_icon = "mdi:pause-circle-outline"

    def __init__(self, pilote, cible) -> None:
        super().__init__(pilote, cible, "suspendre")

    @property
    def available(self) -> bool:
        return True

    @property
    def is_on(self) -> bool:
        return self.pilote.suspendu.get(self.cible, False)

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        ancien = await self.async_get_last_state()
        if ancien is not None and ancien.state == STATE_ON:
            self.pilote.suspendu[self.cible] = True

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.pilote.async_suspendre(self.cible, True)
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.pilote.async_suspendre(self.cible, False)
        self.async_write_ha_state()
