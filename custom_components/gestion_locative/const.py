"""Constantes de l'intégration Gestion locative."""
from __future__ import annotations

from datetime import timedelta

DOMAIN = "gestion_locative"

# données de l'entrée (fixées à l'ajout)
CONF_URL = "url"
CONF_CLE = "cle"
CONF_LOGEMENTS = "logements"
CONF_COMPTE = "compte"

# options
CONF_METEO = "meteo"
CONF_CIBLES = "cibles"

BATIMENT = "batiment"

# réglages d'une cible (logement ou bâtiment)
CONF_CONSIGNE_CHAMBRE = "consigne_chambre"
CONF_CONSIGNE_VIVRE = "consigne_vivre"
CONF_TEMP_MIN = "temp_min"
CONF_TEMP_MAX = "temp_max"
CONF_TEMP_INTER = "temp_inter"
CONF_JOURS_INTER = "jours_inter"
CONF_SEUIL_CLIM = "seuil_clim"
CONF_HEURES_AVANT = "heures_avant"
CONF_DECALAGE_DEPART = "decalage_depart"
CONF_THERMOSTATS_CHAMBRE = "thermostats_chambre"
CONF_THERMOSTATS_VIVRE = "thermostats_vivre"
CONF_EQUIPEMENTS = "equipements"

# réglages d'un équipement
CONF_ENTITE = "entite"
CONF_JOURS_MAINTIEN = "jours_maintien"

DEFAUTS_CIBLE = {
    CONF_CONSIGNE_CHAMBRE: 19.0,
    CONF_CONSIGNE_VIVRE: 20.0,
    CONF_TEMP_MIN: 14.0,
    CONF_TEMP_MAX: 24.0,
    CONF_TEMP_INTER: 16.0,
    CONF_JOURS_INTER: 3.0,
    CONF_SEUIL_CLIM: 30.0,
    CONF_HEURES_AVANT: 6.0,
    CONF_DECALAGE_DEPART: 0.0,
    CONF_THERMOSTATS_CHAMBRE: [],
    CONF_THERMOSTATS_VIVRE: [],
    CONF_EQUIPEMENTS: [],
}

DEFAUTS_EQUIPEMENT = {
    CONF_HEURES_AVANT: 6.0,
    CONF_DECALAGE_DEPART: 0.0,
    CONF_JOURS_MAINTIEN: 0.0,
}

# domaines d'entités pilotables comme équipement
DOMAINES_EQUIPEMENT = [
    "switch",
    "light",
    "fan",
    "input_boolean",
    "humidifier",
    "water_heater",
    "script",
    "automation",
]

INTERVALLE_SYNCHRO = timedelta(minutes=15)
INTERVALLE_PILOTAGE = timedelta(minutes=5)
# attente avant de ramener le réglage du client dans les bornes : laisse le thermostat
# et le client finir leur réglage (sinon ils se renvoient la valeur)
DELAI_BORNE = timedelta(minutes=1)
JOURS_HORIZON = 60

# heures de la prévision regardées pour décider de la climatisation
HEURES_PREVISION_CLIM = (14, 15, 16)

ETAT_VIDE = "vide"
ETAT_PRECHAUFFAGE = "prechauffage"
ETAT_OCCUPE = "occupe"
ETAT_INTER = "inter_sejour"
ETATS = [ETAT_VIDE, ETAT_PRECHAUFFAGE, ETAT_OCCUPE, ETAT_INTER]

MODE_CHAUFFAGE = "heat"
MODE_CLIM = "cool"

STOCKAGE_VERSION = 1
