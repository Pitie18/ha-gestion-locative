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
    # appareil du bâtiment créé d'abord : les logements y sont rattachés
    dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, f"{entry.entry_id}_{BATIMENT}")},
        name=entry.title,
        manufacturer="Gestion locative",
        model="Bâtiment",
    )
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    await pilote.async_demarrer()
    entry.async_on_unload(pilote.async_arreter)
    entry.async_on_unload(entry.add_update_listener(_options_modifiees))
    hass.async_create_task(pilote.async_executer())
    return True


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
