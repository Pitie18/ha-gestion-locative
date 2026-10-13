"""Occupé (oui / non) : un client ou le propriétaire est sur place."""
from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import EntreeGestionLocative
from .entite import EntiteCible


async def async_setup_entry(
    hass: HomeAssistant, entry: EntreeGestionLocative, async_add_entities: AddEntitiesCallback
) -> None:
    pilote = entry.runtime_data.pilote
    async_add_entities(Occupe(pilote, c) for c in pilote.cibles)


class Occupe(EntiteCible, BinarySensorEntity):
    _attr_device_class = BinarySensorDeviceClass.OCCUPANCY

    def __init__(self, pilote, cible) -> None:
        super().__init__(pilote, cible, "occupe")

    @property
    def is_on(self) -> bool | None:
        return self.resume.occupe if self.resume else None
