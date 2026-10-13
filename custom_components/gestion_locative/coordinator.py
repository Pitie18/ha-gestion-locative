"""Synchronisation avec l'outil, avec mémoire du dernier planning reçu."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import ClientGestionLocative, CleRefusee, ErreurApi
from .const import CONF_LOGEMENTS, DOMAIN, INTERVALLE_SYNCHRO, STOCKAGE_VERSION
from .planning import Sejour, lire_sejours

_LOGGER = logging.getLogger(__name__)


@dataclass
class Logement:
    id: int
    nom: str
    sejours: list[Sejour] = field(default_factory=list)


@dataclass
class Occupation:
    logements: dict[int, Logement]
    recu_le: datetime | None  # date de la réponse utilisée
    a_jour: bool  # False : dernière synchronisation en échec, planning en mémoire


def lire_reponse(donnees: dict[str, Any], ids: list[int]) -> dict[int, Logement]:
    logements: dict[int, Logement] = {}
    for l in donnees.get("logements") or []:
        try:
            lid = int(l["id"])
        except (KeyError, TypeError, ValueError):
            continue
        if lid in ids:
            logements[lid] = Logement(lid, str(l.get("nom") or lid), lire_sejours(l.get("sejours")))
    for lid in ids:  # logement absent de la réponse : vide
        logements.setdefault(lid, Logement(lid, str(lid)))
    return logements


class CoordinateurOccupation(DataUpdateCoordinator[Occupation]):
    """Interroge api/occupation toutes les 15 minutes.

    En cas d'échec, le dernier planning reçu (gardé sur disque) continue de servir :
    une panne réseau ne doit jamais laisser un client arriver dans un logement froid.
    """

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, client: ClientGestionLocative) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN} {entry.title}",
            update_interval=INTERVALLE_SYNCHRO,
        )
        self.client = client
        self.ids: list[int] = [int(i) for i in entry.data[CONF_LOGEMENTS]]
        self._store: Store[dict[str, Any]] = Store(hass, STOCKAGE_VERSION, f"{DOMAIN}.{entry.entry_id}.planning")
        self._cache: dict[str, Any] | None = None
        self._cache_charge = False
        self.derniere_erreur: str | None = None

    async def _charger_cache(self) -> None:
        if not self._cache_charge:
            self._cache = await self._store.async_load()
            self._cache_charge = True

    def _depuis_cache(self) -> Occupation | None:
        if not self._cache or "reponse" not in self._cache:
            return None
        recu = dt_util.parse_datetime(self._cache.get("recu_le") or "")
        return Occupation(lire_reponse(self._cache["reponse"], self.ids), recu, False)

    async def _async_update_data(self) -> Occupation:
        await self._charger_cache()
        try:
            reponse = await self.client.occupation(self.ids)
        except CleRefusee as err:
            raise ConfigEntryAuthFailed("Clé refusée par l'outil") from err
        except ErreurApi as err:
            self.derniere_erreur = str(err)
            secours = self._depuis_cache()
            if secours is None:
                raise UpdateFailed(f"Outil injoignable : {err}") from err
            _LOGGER.warning(
                "Outil injoignable (%s) : le planning reçu le %s continue de s'appliquer",
                err,
                secours.recu_le,
            )
            return secours
        maintenant = dt_util.now()
        self.derniere_erreur = None
        self._cache = {"reponse": reponse, "recu_le": maintenant.isoformat()}
        await self._store.async_save(self._cache)
        return Occupation(lire_reponse(reponse, self.ids), maintenant, True)
