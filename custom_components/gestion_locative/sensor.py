"""État, mode prévu, prochaine arrivée / départ, personnes, dernière synchronisation."""
from __future__ import annotations

from datetime import datetime

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import EntreeGestionLocative
from .const import BATIMENT, ETATS, MODE_CHAUFFAGE, MODE_CLIM
from .entite import EntiteCible


async def async_setup_entry(
    hass: HomeAssistant, entry: EntreeGestionLocative, async_add_entities: AddEntitiesCallback
) -> None:
    pilote = entry.runtime_data.pilote
    entites: list[SensorEntity] = []
    for c in pilote.cibles:
        entites += [
            Etat(pilote, c),
            ModePrevu(pilote, c),
            ProchaineArrivee(pilote, c),
            ProchainDepart(pilote, c),
            Personnes(pilote, c),
        ]
    entites.append(DerniereSynchro(pilote, BATIMENT))
    async_add_entities(entites)


class Etat(EntiteCible, SensorEntity):
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = ETATS

    def __init__(self, pilote, cible) -> None:
        super().__init__(pilote, cible, "etat")

    @property
    def native_value(self) -> str | None:
        return self.resume.etat if self.resume else None


class ModePrevu(EntiteCible, SensorEntity):
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = [MODE_CHAUFFAGE, MODE_CLIM]

    def __init__(self, pilote, cible) -> None:
        super().__init__(pilote, cible, "mode_prevu")

    @property
    def native_value(self) -> str | None:
        return self.resume.mode_prevu if self.resume else None


class ProchaineArrivee(EntiteCible, SensorEntity):
    _attr_device_class = SensorDeviceClass.TIMESTAMP

    def __init__(self, pilote, cible) -> None:
        super().__init__(pilote, cible, "prochaine_arrivee")

    @property
    def native_value(self) -> datetime | None:
        return self.resume.prochaine_arrivee if self.resume else None


class ProchainDepart(EntiteCible, SensorEntity):
    _attr_device_class = SensorDeviceClass.TIMESTAMP

    def __init__(self, pilote, cible) -> None:
        super().__init__(pilote, cible, "prochain_depart")

    @property
    def native_value(self) -> datetime | None:
        return self.resume.prochain_depart if self.resume else None


class Personnes(EntiteCible, SensorEntity):
    _attr_icon = "mdi:account-group"

    def __init__(self, pilote, cible) -> None:
        super().__init__(pilote, cible, "personnes")

    @property
    def native_value(self) -> int | None:
        return self.resume.personnes if self.resume else None


class DerniereSynchro(EntiteCible, SensorEntity):
    """Date du planning utilisé ; attribut a_jour = False si l'outil est injoignable."""

    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, pilote, cible) -> None:
        super().__init__(pilote, cible, "derniere_synchro")

    @property
    def native_value(self) -> datetime | None:
        d = self.pilote.coordinateur.data
        return d.recu_le if d else None

    @property
    def extra_state_attributes(self) -> dict:
        d = self.pilote.coordinateur.data
        return {
            "a_jour": bool(d and d.a_jour),
            "erreur": self.pilote.coordinateur.derniere_erreur,
        }
