"""Gestion locative : chauffage et équipements pilotés par les réservations de l'outil."""
from __future__ import annotations

from dataclasses import dataclass

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import ClientGestionLocative
from .const import BATIMENT, CONF_CLE, CONF_URL, DOMAIN
from .coordinator import CoordinateurOccupation
from .pilote import Pilote

PLATFORMS = [Platform.BINARY_SENSOR, Platform.SENSOR, Platform.SWITCH]


@dataclass
class Donnees:
    coordinateur: CoordinateurOccupation
    pilote: Pilote


type EntreeGestionLocative = ConfigEntry[Donnees]


async def async_setup_entry(hass: HomeAssistant, entry: EntreeGestionLocative) -> bool:
    client = ClientGestionLocative(async_get_clientsession(hass), entry.data[CONF_URL], entry.data[CONF_CLE])
    coordinateur = CoordinateurOccupation(hass, entry, client)
    await coordinateur.async_config_entry_first_refresh()
    pilote = Pilote(hass, entry, coordinateur)
    entry.runtime_data = Donnees(coordinateur, pilote)
    registre = dr.async_get(hass)
    batiment = registre.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, f"{entry.entry_id}_{BATIMENT}")},
        name=entry.title,
        manufacturer="Gestion locative",
        model="Bâtiment",
    )
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    # appareils des logements retirés de l'intégration : supprimés
    gardes = {f"{entry.entry_id}_{c}" for c in pilote.cibles}
    for appareil in dr.async_entries_for_config_entry(registre, entry.entry_id):
        if not any(i[0] == DOMAIN and i[1] in gardes for i in appareil.identifiers):
            registre.async_remove_device(appareil.id)
    # logements rattachés à l'appareil du bâtiment
    for lid in coordinateur.ids:
        appareil = registre.async_get_device(identifiers={(DOMAIN, f"{entry.entry_id}_{lid}")})
        if appareil and appareil.via_device_id != batiment.id:
            registre.async_update_device(appareil.id, via_device_id=batiment.id)
    await pilote.async_demarrer()
    entry.async_on_unload(pilote.async_arreter)
    entry.async_on_unload(entry.add_update_listener(_options_modifiees))
    hass.async_create_task(pilote.async_executer())
    return True


async def async_remove_config_entry_device(
    hass: HomeAssistant, entry: EntreeGestionLocative, appareil: dr.DeviceEntry
) -> bool:
    """Suppression manuelle d'un appareil : seulement s'il n'est plus dans l'intégration."""
    gardes = {f"{entry.entry_id}_{c}" for c in entry.runtime_data.pilote.cibles}
    return not any(i[0] == DOMAIN and i[1] in gardes for i in appareil.identifiers)


async def _options_modifiees(hass: HomeAssistant, entry: EntreeGestionLocative) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: EntreeGestionLocative) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Supprime le planning et la mémoire gardés sur disque."""
    from homeassistant.helpers.storage import Store

    from .const import DOMAIN, STOCKAGE_VERSION

    for nom in ("planning", "pilote"):
        await Store(hass, STOCKAGE_VERSION, f"{DOMAIN}.{entry.entry_id}.{nom}").async_remove()
