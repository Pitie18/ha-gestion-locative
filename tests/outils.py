"""Chargement du code de l'intégration sans Home Assistant installé.

- charger_planning() : module de calcul pur (planning.py, const.py).
- charger_tout() : remplace les modules de Home Assistant utilisés par pilote.py et
  coordinator.py par de petites imitations, pour tester le pilotage sans HA.
"""
from __future__ import annotations

import asyncio
from datetime import datetime
import importlib
import json
import os
import sys
import types
from typing import Any
from zoneinfo import ZoneInfo

RACINE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "custom_components", "gestion_locative")
PARIS = ZoneInfo("Europe/Paris")
PAQUET = "gl_test"


def _paquet() -> None:
    if PAQUET not in sys.modules:
        p = types.ModuleType(PAQUET)
        p.__path__ = [RACINE]
        sys.modules[PAQUET] = p


def charger_planning():
    _paquet()
    return importlib.import_module(f"{PAQUET}.planning")


def fixture() -> dict[str, Any]:
    with open(os.path.join(os.path.dirname(__file__), "occupation.json"), encoding="utf-8") as f:
        return json.load(f)


def heure(texte: str) -> datetime:
    """'2026-10-13 11:15' → datetime à Paris."""
    return datetime.fromisoformat(texte).replace(tzinfo=PARIS)


# ---------- imitation de Home Assistant ----------

class Horloge:
    maintenant = heure("2026-10-13 11:15")


class HomeAssistantError(Exception):
    pass


class Etat:
    def __init__(self, entity_id: str, state: str, attributes: dict | None = None) -> None:
        self.entity_id = entity_id
        self.state = state
        self.attributes = attributes or {}


class Etats:
    def __init__(self) -> None:
        self._e: dict[str, Etat] = {}

    def get(self, entity_id: str):
        return self._e.get(entity_id)

    def set(self, entity_id: str, state: str, attributes: dict | None = None) -> None:
        self._e[entity_id] = Etat(entity_id, state, attributes)


class Services:
    """Enregistre les appels ; applique set_temperature / turn_on / turn_off aux états."""

    def __init__(self, hass: "FauxHass") -> None:
        self.hass = hass
        self.appels: list[tuple[str, str, dict]] = []
        self.en_panne: set[str] = set()
        self.prevision: list[dict] | None = None

    async def async_call(self, domaine, service, donnees, blocking=False, return_response=False):
        if domaine == "weather":
            if self.prevision is None:
                raise HomeAssistantError("pas de prévision")
            return {donnees["entity_id"]: {"forecast": self.prevision}}
        entite = donnees.get("entity_id")
        if entite in self.en_panne:
            raise HomeAssistantError("appareil injoignable")
        self.appels.append((domaine, service, dict(donnees)))
        e = self.hass.states.get(entite)
        if domaine == "climate" and service == "set_temperature" and e:
            attrs = dict(e.attributes)
            attrs["temperature"] = donnees["temperature"]
            self.hass.states.set(entite, donnees.get("hvac_mode", e.state), attrs)
        elif service in ("turn_on", "turn_off") and e and domaine != "script":
            self.hass.states.set(entite, "on" if service == "turn_on" else "off", e.attributes)
        return None

    def vider(self) -> None:
        self.appels.clear()


class FauxHass:
    def __init__(self) -> None:
        self.states = Etats()
        self.services = Services(self)
        self.taches: list = []

    def async_create_task(self, coro):
        t = asyncio.ensure_future(coro)
        self.taches.append(t)
        return t

    async def finir(self) -> None:
        while self.taches:
            t = self.taches.pop(0)
            await t


class Store:
    """Stockage en mémoire, partagé par clé (simule le disque)."""

    disque: dict[str, Any] = {}

    def __init__(self, hass, version, cle) -> None:
        self.cle = cle

    async def async_load(self):
        return json.loads(json.dumps(Store.disque.get(self.cle))) if self.cle in Store.disque else None

    async def async_save(self, donnees) -> None:
        Store.disque[self.cle] = json.loads(json.dumps(donnees, default=str))

    async def async_remove(self) -> None:
        Store.disque.pop(self.cle, None)


class DataUpdateCoordinator:
    def __class_getitem__(cls, _):
        return cls

    def __init__(self, hass, logger, config_entry=None, name=None, update_interval=None) -> None:
        self.hass = hass
        self.data = None
        self._ecouteurs: list = []

    def async_add_listener(self, f):
        self._ecouteurs.append(f)
        return lambda: self._ecouteurs.remove(f)

    async def async_refresh(self) -> None:
        self.data = await self._async_update_data()


class UpdateFailed(Exception):
    pass


class ConfigEntryAuthFailed(Exception):
    pass


class Entree:
    def __init__(self, ids: list[int], options: dict | None = None) -> None:
        self.entry_id = "entree1"
        self.title = "Bâtiment test"
        self.data = {"url": "https://x/", "cle": "pl_x", "logements": ids, "compte": 1}
        self.options = options or {}


def _module(nom: str, **attrs) -> types.ModuleType:
    m = types.ModuleType(nom)
    m.__dict__.update(attrs)
    sys.modules[nom] = m
    return m


def charger_tout():
    """Installe les imitations puis importe pilote et coordinator."""
    _paquet()
    if f"{PAQUET}.pilote" in sys.modules:
        return sys.modules[f"{PAQUET}.pilote"], sys.modules[f"{PAQUET}.coordinator"], sys.modules[f"{PAQUET}.api"]
    for n in ("homeassistant", "homeassistant.components", "homeassistant.helpers", "homeassistant.util"):
        _module(n)
    _module("homeassistant.components.climate", ATTR_HVAC_MODES="hvac_modes")
    _module("homeassistant.config_entries", ConfigEntry=object)
    _module(
        "homeassistant.const",
        ATTR_ENTITY_ID="entity_id",
        ATTR_TEMPERATURE="temperature",
        STATE_UNAVAILABLE="unavailable",
        STATE_UNKNOWN="unknown",
    )
    _module("homeassistant.core", CALLBACK_TYPE=object, Event=object, HomeAssistant=object, callback=lambda f: f)
    _module("homeassistant.exceptions", HomeAssistantError=HomeAssistantError, ConfigEntryAuthFailed=ConfigEntryAuthFailed)
    _module(
        "homeassistant.helpers.event",
        async_track_state_change_event=lambda hass, entites, f: (lambda: None),
        async_track_time_interval=lambda hass, f, intervalle: (lambda: None),
    )
    _module("homeassistant.helpers.storage", Store=Store)
    _module("homeassistant.helpers.update_coordinator", DataUpdateCoordinator=DataUpdateCoordinator, UpdateFailed=UpdateFailed)
    dt = _module(
        "homeassistant.util.dt",
        now=lambda: Horloge.maintenant,
        parse_datetime=lambda s: datetime.fromisoformat(s) if s else None,
    )
    sys.modules["homeassistant.util"].dt = dt

    class ClientError(Exception):
        pass

    _module("aiohttp", ClientError=ClientError, ClientSession=object)
    pilote = importlib.import_module(f"{PAQUET}.pilote")
    coordinator = importlib.import_module(f"{PAQUET}.coordinator")
    api = importlib.import_module(f"{PAQUET}.api")
    return pilote, coordinator, api
