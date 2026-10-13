"""Ajout de l'intégration (adresse + clé, choix des logements) et réglages (options)."""
from __future__ import annotations

from collections.abc import Mapping
import copy
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    BooleanSelector,
    EntitySelector,
    EntitySelectorConfig,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .api import ClientGestionLocative, CleRefusee, ErreurApi, normaliser_url
from .const import (
    BATIMENT,
    CONF_CIBLES,
    CONF_CLE,
    CONF_COMPTE,
    CONF_CONSIGNE_CHAMBRE,
    CONF_CONSIGNE_VIVRE,
    CONF_DECALAGE_DEPART,
    CONF_ENTITE,
    CONF_EQUIPEMENTS,
    CONF_HEURES_AVANT,
    CONF_JOURS_INTER,
    CONF_JOURS_MAINTIEN,
    CONF_LOGEMENTS,
    CONF_METEO,
    CONF_SEUIL_CLIM,
    CONF_TEMP_INTER,
    CONF_TEMP_MAX,
    CONF_TEMP_MIN,
    CONF_THERMOSTATS_CHAMBRE,
    CONF_THERMOSTATS_VIVRE,
    CONF_URL,
    DEFAUTS_CIBLE,
    DEFAUTS_EQUIPEMENT,
    DOMAINES_EQUIPEMENT,
    DOMAIN,
)

GENERAL = "general"
LOGEMENTS = "logements"
TERMINER = "terminer"


def _temperature() -> NumberSelector:
    return NumberSelector(NumberSelectorConfig(min=5, max=35, step=0.5, unit_of_measurement="°C", mode=NumberSelectorMode.BOX))


def _nombre(mini: float, maxi: float, unite: str, pas: float = 0.5) -> NumberSelector:
    return NumberSelector(NumberSelectorConfig(min=mini, max=maxi, step=pas, unit_of_measurement=unite, mode=NumberSelectorMode.BOX))


async def _lire_logements(hass, url: str, cle: str) -> dict[str, Any]:
    client = ClientGestionLocative(async_get_clientsession(hass), url, cle)
    return await client.logements()


class GestionLocativeConfigFlow(ConfigFlow, domain=DOMAIN):
    """Un compte peut ajouter autant d'intégrations qu'il veut (une par bâtiment)."""

    VERSION = 1

    def __init__(self) -> None:
        self._url = ""
        self._cle = ""
        self._compte: dict[str, Any] = {}
        self._logements: list[dict[str, Any]] = []

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        erreurs: dict[str, str] = {}
        if user_input is not None:
            self._url = normaliser_url(user_input[CONF_URL])
            self._cle = user_input[CONF_CLE].strip()
            try:
                rep = await _lire_logements(self.hass, self._url, self._cle)
            except CleRefusee:
                erreurs["base"] = "cle_refusee"
            except ErreurApi:
                erreurs["base"] = "injoignable"
            else:
                self._compte = rep.get("compte") or {}
                self._logements = rep.get("logements") or []
                if not self._logements:
                    erreurs["base"] = "aucun_logement"
                else:
                    return await self.async_step_logements()
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_URL, default=self._url or "https://"): TextSelector(TextSelectorConfig(type=TextSelectorType.URL)),
                    vol.Required(CONF_CLE): TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD)),
                }
            ),
            errors=erreurs,
        )

    async def async_step_logements(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        erreurs: dict[str, str] = {}
        if user_input is not None:
            ids = sorted(int(i) for i in user_input[CONF_LOGEMENTS])
            if not ids:
                erreurs["base"] = "aucun_choix"
            else:
                await self.async_set_unique_id(f"{self._compte.get('id', 0)}_{'_'.join(map(str, ids))}")
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=user_input["nom"].strip() or "Bâtiment",
                    data={
                        CONF_URL: self._url,
                        CONF_CLE: self._cle,
                        CONF_LOGEMENTS: ids,
                        CONF_COMPTE: self._compte.get("id", 0),
                    },
                    options={},
                )
        choix = [
            SelectOptionDict(
                value=str(l["id"]),
                label=l["nom"] + (" (mandat)" if l.get("role") == "mandataire" else ""),
            )
            for l in self._logements
        ]
        return self.async_show_form(
            step_id="logements",
            data_schema=vol.Schema(
                {
                    vol.Required("nom", default="Bâtiment"): str,
                    vol.Required(CONF_LOGEMENTS): SelectSelector(
                        SelectSelectorConfig(options=choix, multiple=True, mode=SelectSelectorMode.LIST)
                    ),
                }
            ),
            errors=erreurs,
        )

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        entree = self._get_reauth_entry()
        erreurs: dict[str, str] = {}
        if user_input is not None:
            try:
                await _lire_logements(self.hass, entree.data[CONF_URL], user_input[CONF_CLE].strip())
            except CleRefusee:
                erreurs["base"] = "cle_refusee"
            except ErreurApi:
                erreurs["base"] = "injoignable"
            else:
                return self.async_update_reload_and_abort(entree, data_updates={CONF_CLE: user_input[CONF_CLE].strip()})
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_CLE): TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD))}),
            errors=erreurs,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return GestionLocativeOptionsFlow()


class GestionLocativeOptionsFlow(OptionsFlow):
    """Réglages : général (météo), puis bâtiment et chaque logement, puis équipements."""

    def __init__(self) -> None:
        self._options: dict[str, Any] | None = None
        self._cible = BATIMENT
        self._ids: list[int] | None = None  # nouvelle liste de logements, appliquée à l'enregistrement

    def _opts(self) -> dict[str, Any]:
        if self._options is None:
            self._options = copy.deepcopy(dict(self.config_entry.options))
            self._options.setdefault(CONF_CIBLES, {})
        return self._options

    def _noms(self) -> dict[str, str]:
        noms = {BATIMENT: f"{self.config_entry.title} (bâtiment, communs)"}
        donnees = getattr(self.config_entry, "runtime_data", None)
        occ = donnees.coordinateur.data if donnees else None
        for i in self._logements_choisis():
            noms[str(i)] = occ.logements[int(i)].nom if occ and int(i) in occ.logements else f"Logement {i}"
        return noms

    def _logements_choisis(self) -> list[int]:
        return self._ids if self._ids is not None else [int(i) for i in self.config_entry.data[CONF_LOGEMENTS]]

    def _reglages(self) -> dict[str, Any]:
        r = copy.deepcopy(DEFAUTS_CIBLE)
        r.update(self._opts()[CONF_CIBLES].get(self._cible) or {})
        return r

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            choix = user_input["quoi"]
            if choix == TERMINER:
                if self._ids is not None:
                    # logements retirés : leurs réglages partent avec eux
                    gardes = {str(i) for i in self._ids} | {BATIMENT}
                    cibles = self._opts()[CONF_CIBLES]
                    for cle in [c for c in cibles if c not in gardes]:
                        del cibles[cle]
                    self.hass.config_entries.async_update_entry(
                        self.config_entry, data={**self.config_entry.data, CONF_LOGEMENTS: self._ids}
                    )
                return self.async_create_entry(data=self._opts())
            if choix == GENERAL:
                return await self.async_step_general()
            if choix == LOGEMENTS:
                return await self.async_step_logements()
            self._cible = choix
            return await self.async_step_cible()
        options = [
            SelectOptionDict(value=GENERAL, label="Général (météo)"),
            SelectOptionDict(value=LOGEMENTS, label="Logements du bâtiment (ajouter / retirer)"),
        ]
        options += [SelectOptionDict(value=k, label=v) for k, v in self._noms().items()]
        options.append(SelectOptionDict(value=TERMINER, label="✔ Terminer et enregistrer"))
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {vol.Required("quoi", default=GENERAL): SelectSelector(SelectSelectorConfig(options=options, mode=SelectSelectorMode.LIST))}
            ),
        )

    async def async_step_logements(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Ajouter ou retirer des logements ; appliqué à « Terminer et enregistrer »."""
        erreurs: dict[str, str] = {}
        if user_input is not None:
            ids = sorted(int(i) for i in user_input[CONF_LOGEMENTS])
            if not ids:
                erreurs["base"] = "aucun_choix"
            else:
                self._ids = ids
                return await self.async_step_init()
        try:
            rep = await _lire_logements(self.hass, self.config_entry.data[CONF_URL], self.config_entry.data[CONF_CLE])
        except CleRefusee:
            return self.async_abort(reason="cle_refusee")
        except ErreurApi:
            return self.async_abort(reason="injoignable")
        choix = [
            SelectOptionDict(
                value=str(l["id"]),
                label=l["nom"] + (" (mandat)" if l.get("role") == "mandataire" else ""),
            )
            for l in rep.get("logements") or []
        ]
        actuels = [str(i) for i in self._logements_choisis()]
        return self.async_show_form(
            step_id="logements",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_LOGEMENTS, default=actuels): SelectSelector(
                        SelectSelectorConfig(options=choix, multiple=True, mode=SelectSelectorMode.LIST)
                    ),
                }
            ),
            errors=erreurs,
        )

    async def async_step_general(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            if user_input.get(CONF_METEO):
                self._opts()[CONF_METEO] = user_input[CONF_METEO]
            else:
                self._opts().pop(CONF_METEO, None)
            return await self.async_step_init()
        meteo = self._opts().get(CONF_METEO)
        return self.async_show_form(
            step_id="general",
            data_schema=vol.Schema(
                {
                    vol.Optional(CONF_METEO, description={"suggested_value": meteo}): EntitySelector(EntitySelectorConfig(domain="weather")),
                }
            ),
        )

    async def async_step_cible(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        r = self._reglages()
        erreurs: dict[str, str] = {}
        if user_input is not None:
            if user_input[CONF_TEMP_MIN] > user_input[CONF_TEMP_MAX]:
                erreurs["base"] = "min_sup_max"
            else:
                r.update(user_input)
                r.setdefault(CONF_THERMOSTATS_CHAMBRE, [])
                r.setdefault(CONF_THERMOSTATS_VIVRE, [])
                self._opts()[CONF_CIBLES][self._cible] = r
                return await self.async_step_equipements()
        schema = vol.Schema(
            {
                vol.Optional(CONF_THERMOSTATS_CHAMBRE, default=r[CONF_THERMOSTATS_CHAMBRE]): EntitySelector(
                    EntitySelectorConfig(domain="climate", multiple=True)
                ),
                vol.Optional(CONF_THERMOSTATS_VIVRE, default=r[CONF_THERMOSTATS_VIVRE]): EntitySelector(
                    EntitySelectorConfig(domain="climate", multiple=True)
                ),
                vol.Required(CONF_CONSIGNE_CHAMBRE, default=r[CONF_CONSIGNE_CHAMBRE]): _temperature(),
                vol.Required(CONF_CONSIGNE_VIVRE, default=r[CONF_CONSIGNE_VIVRE]): _temperature(),
                vol.Required(CONF_TEMP_MIN, default=r[CONF_TEMP_MIN]): _temperature(),
                vol.Required(CONF_TEMP_MAX, default=r[CONF_TEMP_MAX]): _temperature(),
                vol.Required(CONF_TEMP_INTER, default=r[CONF_TEMP_INTER]): _temperature(),
                vol.Required(CONF_JOURS_INTER, default=r[CONF_JOURS_INTER]): _nombre(0, 30, "jours"),
                vol.Required(CONF_SEUIL_CLIM, default=r[CONF_SEUIL_CLIM]): _nombre(15, 50, "°C"),
                vol.Required(CONF_HEURES_AVANT, default=r[CONF_HEURES_AVANT]): _nombre(0, 96, "h"),
                vol.Required(CONF_DECALAGE_DEPART, default=r[CONF_DECALAGE_DEPART]): _nombre(-48, 48, "h"),
            }
        )
        return self.async_show_form(
            step_id="cible",
            data_schema=schema,
            errors=erreurs,
            description_placeholders={"nom": self._noms().get(self._cible, self._cible)},
        )

    async def async_step_equipements(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        r = self._reglages()
        existants: list[dict[str, Any]] = list(r[CONF_EQUIPEMENTS])
        if user_input is not None:
            retirer = set(user_input.get("retirer") or [])
            existants = [e for e in existants if e[CONF_ENTITE] not in retirer]
            nouvelle = user_input.get(CONF_ENTITE)
            if nouvelle:
                existants = [e for e in existants if e[CONF_ENTITE] != nouvelle]
                existants.append(
                    {
                        CONF_ENTITE: nouvelle,
                        CONF_HEURES_AVANT: user_input[CONF_HEURES_AVANT],
                        CONF_DECALAGE_DEPART: user_input[CONF_DECALAGE_DEPART],
                        CONF_JOURS_MAINTIEN: user_input[CONF_JOURS_MAINTIEN],
                    }
                )
            r[CONF_EQUIPEMENTS] = existants
            self._opts()[CONF_CIBLES][self._cible] = r
            if user_input.get("encore"):
                return await self.async_step_equipements()
            return await self.async_step_init()
        liste = [
            SelectOptionDict(
                value=e[CONF_ENTITE],
                label=f"{e[CONF_ENTITE]} — {e[CONF_HEURES_AVANT]:g} h avant, départ {e[CONF_DECALAGE_DEPART]:+g} h, maintenu si < {e[CONF_JOURS_MAINTIEN]:g} j",
            )
            for e in existants
        ]
        champs: dict[Any, Any] = {}
        if liste:
            champs[vol.Optional("retirer", default=[])] = SelectSelector(
                SelectSelectorConfig(options=liste, multiple=True, mode=SelectSelectorMode.LIST)
            )
        champs.update(
            {
                vol.Optional(CONF_ENTITE): EntitySelector(EntitySelectorConfig(domain=DOMAINES_EQUIPEMENT)),
                vol.Required(CONF_HEURES_AVANT, default=DEFAUTS_EQUIPEMENT[CONF_HEURES_AVANT]): _nombre(0, 96, "h"),
                vol.Required(CONF_DECALAGE_DEPART, default=DEFAUTS_EQUIPEMENT[CONF_DECALAGE_DEPART]): _nombre(-48, 48, "h"),
                vol.Required(CONF_JOURS_MAINTIEN, default=DEFAUTS_EQUIPEMENT[CONF_JOURS_MAINTIEN]): _nombre(0, 30, "jours"),
                vol.Optional("encore", default=False): BooleanSelector(),
            }
        )
        return self.async_show_form(
            step_id="equipements",
            data_schema=vol.Schema(champs),
            description_placeholders={"nom": self._noms().get(self._cible, self._cible), "nombre": str(len(existants))},
        )
