"""Base commune des entités : un appareil par logement et un pour le bâtiment."""
from __future__ import annotations

from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity

from .const import BATIMENT, DOMAIN
from .pilote import Pilote, Resume


class EntiteCible(Entity):
    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, pilote: Pilote, cible: str, cle: str) -> None:
        self.pilote = pilote
        self.cible = cible
        self._attr_translation_key = cle
        self._attr_unique_id = f"{pilote.entry.entry_id}_{cible}_{cle}"
        ident = (DOMAIN, f"{pilote.entry.entry_id}_{cible}")
        self._attr_device_info = DeviceInfo(
            identifiers={ident},
            name=pilote.nom(cible),
            manufacturer="Gestion locative",
            model="Bâtiment" if cible == BATIMENT else "Logement",
        )

    @property
    def resume(self) -> Resume | None:
        return self.pilote.resumes.get(self.cible)

    @property
    def available(self) -> bool:
        return self.resume is not None

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(self.pilote.ecouter(self._maj))

    @callback
    def _maj(self) -> None:
        self.async_write_ha_state()
