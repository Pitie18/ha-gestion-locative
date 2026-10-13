"""Pilotage des thermostats et des équipements selon l'occupation."""
from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
import hashlib
import json
import logging
from typing import Any

from homeassistant.components.climate import ATTR_HVAC_MODES
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_ENTITY_ID, ATTR_TEMPERATURE, STATE_UNAVAILABLE
from homeassistant.core import CALLBACK_TYPE, Event, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.event import (
    async_call_later,
    async_track_state_change_event,
    async_track_time_interval,
)
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import (
    BATIMENT,
    CONF_CIBLES,
    CONF_DECALAGE_DEPART,
    CONF_ENTITE,
    CONF_EQUIPEMENTS,
    CONF_HEURES_AVANT,
    CONF_JOURS_INTER,
    CONF_JOURS_MAINTIEN,
    CONF_METEO,
    CONF_SEUIL_CLIM,
    CONF_TEMP_MAX,
    CONF_TEMP_MIN,
    CONF_THERMOSTATS_CHAMBRE,
    CONF_THERMOSTATS_VIVRE,
    DEFAUTS_CIBLE,
    DEFAUTS_EQUIPEMENT,
    DELAI_BORNE,
    DOMAIN,
    ETAT_OCCUPE,
    ETAT_PRECHAUFFAGE,
    INTERVALLE_PILOTAGE,
    MODE_CHAUFFAGE,
    MODE_CLIM,
    STOCKAGE_VERSION,
)
from .coordinator import CoordinateurOccupation
from .planning import (
    Phase,
    Sejour,
    borne_client,
    consigne,
    en_marche,
    mode_selon_prevision,
    phase,
    plages,
    present,
)

_LOGGER = logging.getLogger(__name__)

# « unknown » n'empêche pas de commander : certains radiateurs (Tuya) n'indiquent pas leur
# mode tout en acceptant les ordres ; seul un appareil injoignable est ignoré
ETATS_INDISPONIBLES = (STATE_UNAVAILABLE,)


def reglages_cible(options: dict[str, Any], cible: str) -> dict[str, Any]:
    """Réglages d'une cible, complétés par les valeurs par défaut."""
    r = dict(DEFAUTS_CIBLE)
    r.update((options.get(CONF_CIBLES) or {}).get(cible) or {})
    return r


def reglages_equipement(e: dict[str, Any]) -> dict[str, Any]:
    r = dict(DEFAUTS_EQUIPEMENT)
    r.update(e)
    return r


def heures(v: Any) -> timedelta:
    return timedelta(hours=float(v or 0))


@dataclass
class Resume:
    """Ce que montrent les entités d'une cible."""

    etat: str
    occupe: bool
    mode_prevu: str | None
    prochaine_arrivee: datetime | None
    prochain_depart: datetime | None
    personnes: int | None


class Pilote:
    """Calcule l'état de chaque cible et commande thermostats et équipements."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, coordinateur: CoordinateurOccupation) -> None:
        self.hass = hass
        self.entry = entry
        self.coordinateur = coordinateur
        self.options: dict[str, Any] = dict(entry.options)
        self.cibles: list[str] = [BATIMENT] + [str(i) for i in coordinateur.ids]
        self.resumes: dict[str, Resume] = {}
        self.suspendu: dict[str, bool] = {c: False for c in self.cibles}
        self._store: Store[dict[str, Any]] = Store(hass, STOCKAGE_VERSION, f"{DOMAIN}.{entry.entry_id}.pilote")
        # memoire : {"thermostats": {entité: clé appliquée}, "equipements": {"cible|entité": bool},
        #            "modes": {cible: {clé de plage: mode}}}
        self._memoire: dict[str, Any] = {"thermostats": {}, "equipements": {}, "modes": {}}
        self._ecouteurs: list[Callable[[], None]] = []
        self._desabonner: list[CALLBACK_TYPE] = []
        self._verrou = asyncio.Lock()
        self._prevision: list[dict[str, Any]] | None = None
        self._attentes: dict[str, CALLBACK_TYPE] = {}  # bornes en attente, par thermostat

    # ---------- démarrage / arrêt ----------

    async def async_demarrer(self) -> None:
        self._memoire.update(await self._store.async_load() or {})
        self._desabonner.append(
            async_track_time_interval(self.hass, self._sur_minuterie, INTERVALLE_PILOTAGE)
        )
        self._desabonner.append(self.coordinateur.async_add_listener(self._sur_synchro))
        thermostats = sorted({t for c in self.cibles for t, _ in self._thermostats(c)})
        if thermostats:
            self._desabonner.append(
                async_track_state_change_event(self.hass, thermostats, self._sur_thermostat)
            )

    @callback
    def async_arreter(self) -> None:
        for d in self._desabonner:
            d()
        self._desabonner.clear()
        for annuler in self._attentes.values():
            annuler()
        self._attentes.clear()

    @callback
    def ecouter(self, fonction: Callable[[], None]) -> Callable[[], None]:
        self._ecouteurs.append(fonction)
        return lambda: self._ecouteurs.remove(fonction)

    @callback
    def _sur_minuterie(self, _now: datetime) -> None:
        self.hass.async_create_task(self.async_executer())

    @callback
    def _sur_synchro(self) -> None:
        self.hass.async_create_task(self.async_executer())

    async def async_suspendre(self, cible: str, oui: bool) -> None:
        self.suspendu[cible] = oui
        if not oui:  # reprise : on réapplique l'état du moment
            self._oublier(cible)
            await self.async_executer()

    def _oublier(self, cible: str) -> None:
        for entite, _ in self._thermostats(cible):
            self._memoire["thermostats"].pop(entite, None)
        for e in reglages_cible(self.options, cible)[CONF_EQUIPEMENTS]:
            self._memoire["equipements"].pop(f"{cible}|{e.get(CONF_ENTITE)}", None)

    # ---------- données ----------

    def _thermostats(self, cible: str) -> list[tuple[str, str]]:
        r = reglages_cible(self.options, cible)
        return [(t, "chambre") for t in r[CONF_THERMOSTATS_CHAMBRE]] + [
            (t, "vivre") for t in r[CONF_THERMOSTATS_VIVRE]
        ]

    def sejours(self, cible: str) -> list[Sejour]:
        donnees = self.coordinateur.data
        if donnees is None:
            return []
        if cible == BATIMENT:  # le bâtiment est occupé dès qu'un logement l'est
            tous = [s for l in donnees.logements.values() for s in l.sejours]
            return sorted(tous, key=lambda s: (s.arrivee, s.id))
        logement = donnees.logements.get(int(cible))
        return list(logement.sejours) if logement else []

    def nom(self, cible: str) -> str:
        if cible == BATIMENT:
            return self.entry.title
        donnees = self.coordinateur.data
        if donnees and int(cible) in donnees.logements:
            return donnees.logements[int(cible)].nom
        return f"Logement {cible}"

    async def _lire_prevision(self) -> list[dict[str, Any]] | None:
        meteo = self.options.get(CONF_METEO)
        if not meteo or self.hass.states.get(meteo) is None:
            return None
        try:
            rep = await self.hass.services.async_call(
                "weather",
                "get_forecasts",
                {ATTR_ENTITY_ID: meteo, "type": "hourly"},
                blocking=True,
                return_response=True,
            )
        except HomeAssistantError as err:
            _LOGGER.debug("Prévision horaire indisponible (%s) : chauffage par défaut", err)
            return None
        return ((rep or {}).get(meteo) or {}).get("forecast")

    def _mode(self, cible: str, ph: Phase, reglages: dict[str, Any], memoriser: bool) -> str | None:
        """Mode d'une arrivée : décidé à la mise en route, puis gardé jusqu'à l'arrivée suivante."""
        if ph.arrivee is None:
            return None
        modes = self._memoire["modes"].setdefault(cible, {})
        en_cours = ph.plage is not None
        if en_cours and ph.cle in modes:
            return modes[ph.cle]
        mode = mode_selon_prevision(self._prevision, ph.arrivee, float(reglages[CONF_SEUIL_CLIM]))
        if en_cours and memoriser:
            modes.clear()  # seule l'arrivée en cours compte
            modes[ph.cle] = mode
        return mode

    # ---------- exécution ----------

    async def async_executer(self) -> None:
        if self.coordinateur.data is None:
            return
        async with self._verrou:
            maintenant = dt_util.now()
            self._prevision = await self._lire_prevision()
            for cible in self.cibles:
                try:
                    await self._executer_cible(cible, maintenant)
                except Exception:  # une cible en erreur ne doit pas bloquer les autres
                    _LOGGER.exception("Pilotage de %s en erreur", self.nom(cible))
            await self._store.async_save(self._memoire)
        for f in list(self._ecouteurs):
            f()

    async def _executer_cible(self, cible: str, maintenant: datetime) -> None:
        r = reglages_cible(self.options, cible)
        sejours = self.sejours(cible)
        ph = phase(sejours, maintenant, heures(r[CONF_HEURES_AVANT]), heures(r[CONF_DECALAGE_DEPART]), float(r[CONF_JOURS_INTER]))
        mode = self._mode(cible, ph, r, memoriser=not self.suspendu[cible])
        ici = present(sejours, maintenant)
        a_venir = [s for s in sejours if s.arrivee > maintenant]
        self.resumes[cible] = Resume(
            etat=ph.etat,
            occupe=bool(ici),
            mode_prevu=mode,
            prochaine_arrivee=a_venir[0].arrivee if a_venir else None,
            prochain_depart=max(s.depart for s in ici) if ici else (a_venir[0].depart if a_venir else None),
            personnes=(sum(s.personnes or 0 for s in ici) or None) if ici else (a_venir[0].personnes if a_venir else None),
        )
        if self.suspendu[cible]:
            return
        await self._piloter_thermostats(cible, ph, mode, r)
        await self._piloter_equipements(cible, sejours, maintenant, r)

    async def _piloter_thermostats(self, cible: str, ph: Phase, mode: str | None, r: dict[str, Any]) -> None:
        empreinte = hashlib.sha1(
            json.dumps({k: v for k, v in r.items() if k != CONF_EQUIPEMENTS}, sort_keys=True, default=str).encode()
        ).hexdigest()[:10]
        for entite, role in self._thermostats(cible):
            etat = self.hass.states.get(entite)
            if etat is None or etat.state in ETATS_INDISPONIBLES:
                continue  # réessayé au prochain passage
            # hors séjour, le mode prévu (prévision de la prochaine arrivée) ne change rien
            mode_cle = mode if ph.etat in (ETAT_PRECHAUFFAGE, ETAT_OCCUPE) else "-"
            cle = f"{ph.cle}|{mode_cle}|{empreinte}"
            if self._memoire["thermostats"].get(entite) != cle:
                voulu_mode, voulue = consigne(ph.etat, mode or MODE_CHAUFFAGE, role, r)
                if await self._regler(entite, etat, voulu_mode, voulue):
                    self._memoire["thermostats"][entite] = cle
            else:
                # bornes min / max tenues en permanence (séjour ou non) ; réglage en cours (moins d'une minute) : la borne attendue s'en chargera
                if entite in self._attentes or dt_util.now() - etat.last_updated < DELAI_BORNE:
                    continue
                await self._borner(entite, r)

    async def _regler(self, entite: str, etat: Any, mode: str, temperature: float) -> bool:
        modes = etat.attributes.get(ATTR_HVAC_MODES) or []
        if mode == MODE_CLIM and MODE_CLIM not in modes:
            return True  # thermostat sans climatisation : on ne le touche pas
        donnees: dict[str, Any] = {ATTR_ENTITY_ID: entite, ATTR_TEMPERATURE: temperature}
        if mode in modes:
            donnees["hvac_mode"] = mode
        try:
            await self.hass.services.async_call("climate", "set_temperature", donnees, blocking=True)
        except HomeAssistantError as err:
            _LOGGER.warning("%s : réglage impossible (%s), nouvel essai plus tard", entite, err)
            return False
        _LOGGER.debug("%s : %s à %s °C", entite, mode, temperature)
        return True

    async def _borner(self, entite: str, r: dict[str, Any]) -> None:
        etat = self.hass.states.get(entite)
        if etat is None or etat.state in ETATS_INDISPONIBLES:
            return
        actuelle = etat.attributes.get(ATTR_TEMPERATURE)
        try:
            actuelle = float(actuelle) if actuelle is not None else None
        except (TypeError, ValueError):
            return
        nouvelle = borne_client(etat.state, actuelle, float(r[CONF_TEMP_MIN]), float(r[CONF_TEMP_MAX]))
        if nouvelle is None:
            return
        try:
            await self.hass.services.async_call(
                "climate", "set_temperature", {ATTR_ENTITY_ID: entite, ATTR_TEMPERATURE: nouvelle}, blocking=True
            )
            _LOGGER.debug("%s : réglage du client ramené à %s °C", entite, nouvelle)
        except HomeAssistantError as err:
            _LOGGER.warning("%s : impossible de ramener la consigne (%s)", entite, err)

    @callback
    def _sur_thermostat(self, event: Event) -> None:
        """Réglage changé (client, personnel) : bornes min / max appliquées une minute après
        le dernier changement, comme une automatisation en mode « restart », que le logement
        soit occupé ou non."""
        entite = event.data["entity_id"]
        for cible in self.cibles:
            if self.suspendu[cible] or entite not in [t for t, _ in self._thermostats(cible)]:
                continue
            resume = self.resumes.get(cible)
            if not resume:
                continue
            if entite in self._attentes:
                self._attentes.pop(entite)()

            @callback
            def _echeance(_now: Any, entite: str = entite, cible: str = cible) -> None:
                self._attentes.pop(entite, None)
                resume = self.resumes.get(cible)
                if self.suspendu[cible] or not resume:
                    return
                self.hass.async_create_task(self._borner(entite, reglages_cible(self.options, cible)))

            self._attentes[entite] = async_call_later(self.hass, DELAI_BORNE, _echeance)
            return

    async def _piloter_equipements(self, cible: str, sejours: list[Sejour], maintenant: datetime, r: dict[str, Any]) -> None:
        for brut in r[CONF_EQUIPEMENTS]:
            e = reglages_equipement(brut)
            entite = e.get(CONF_ENTITE)
            if not entite:
                continue
            liste = plages(
                sejours,
                heures(e[CONF_HEURES_AVANT]),
                heures(e[CONF_DECALAGE_DEPART]),
                timedelta(days=float(e[CONF_JOURS_MAINTIEN] or 0)),
            )
            voulu = en_marche(liste, maintenant) is not None
            cle = f"{cible}|{entite}"
            if self._memoire["equipements"].get(cle) == voulu:
                continue
            if self.hass.states.get(entite) is None:
                continue
            if await self._commander(entite, voulu):
                self._memoire["equipements"][cle] = voulu

    async def _commander(self, entite: str, marche: bool) -> bool:
        domaine = entite.split(".", 1)[0]
        if domaine == "script":
            if not marche:
                return True  # un script se lance à la mise en route, rien à l'arrêt
            service = ("script", "turn_on")
        elif domaine == "automation":
            service = ("automation", "turn_on" if marche else "turn_off")
        else:
            service = ("homeassistant", "turn_on" if marche else "turn_off")
        try:
            await self.hass.services.async_call(*service, {ATTR_ENTITY_ID: entite}, blocking=True)
        except HomeAssistantError as err:
            _LOGGER.warning("%s : commande impossible (%s), nouvel essai plus tard", entite, err)
            return False
        _LOGGER.debug("%s : %s", entite, "marche" if marche else "arrêt")
        return True
