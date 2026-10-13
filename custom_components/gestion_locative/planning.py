"""Calcul pur (sans Home Assistant) : plages de marche, états, mode, bornes.

Toutes les dates sont des datetime avec fuseau.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Iterable

from .const import (
    ETAT_INTER,
    ETAT_OCCUPE,
    ETAT_PRECHAUFFAGE,
    ETAT_VIDE,
    HEURES_PREVISION_CLIM,
    MODE_CHAUFFAGE,
    MODE_CLIM,
)


@dataclass(frozen=True)
class Sejour:
    """Séjour validé ou blocage propriétaire renvoyé par l'outil."""

    id: int
    arrivee: datetime
    depart: datetime
    personnes: int | None = None
    type: str = "sejour"


@dataclass(frozen=True)
class Plage:
    """Période de marche : [arrivée − avance, départ + décalage], séjours proches réunis."""

    debut: datetime
    fin: datetime
    arrivee: datetime  # première arrivée de la plage
    depart: datetime  # dernier départ de la plage
    sejours: tuple[Sejour, ...] = field(default_factory=tuple)

    @property
    def cle(self) -> str:
        return self.debut.isoformat()


@dataclass(frozen=True)
class Phase:
    """Situation d'une cible à un instant donné, pour les thermostats."""

    etat: str
    cle: str  # change à chaque changement de phase, donc à chaque arrivée (remise à la consigne)
    plage: Plage | None = None  # plage en cours (préchauffage / occupé)
    suivante: Plage | None = None  # prochaine plage à venir
    arrivee: datetime | None = None  # arrivée qui décide du mode : en cours, sinon prochaine


def lire_sejours(liste: Iterable[dict[str, Any]]) -> list[Sejour]:
    """Convertit les séjours de api/occupation ; ignore les lignes mal formées."""
    sejours = []
    for s in liste or []:
        try:
            arrivee = datetime.fromisoformat(s["arrivee"])
            depart = datetime.fromisoformat(s["depart"])
        except (KeyError, TypeError, ValueError):
            continue
        if arrivee.tzinfo is None or depart.tzinfo is None or depart <= arrivee:
            continue
        sejours.append(
            Sejour(
                id=int(s.get("id") or 0),
                arrivee=arrivee,
                depart=depart,
                personnes=s.get("personnes"),
                type=s.get("type") or "sejour",
            )
        )
    sejours.sort(key=lambda x: (x.arrivee, x.id))
    return sejours


def plages(
    sejours: Iterable[Sejour],
    avance: timedelta,
    decalage: timedelta,
    maintien: timedelta = timedelta(0),
) -> list[Plage]:
    """Plages de marche.

    avance : mise en route avant l'arrivée ; decalage : signé, appliqué au départ
    (négatif = avant le départ) ; maintien : deux plages séparées de moins que cela
    sont réunies (0 = seulement si elles se touchent ou se chevauchent).
    """
    resultat: list[Plage] = []
    for s in sorted(sejours, key=lambda x: (x.arrivee, x.id)):
        debut = s.arrivee - avance
        fin = s.depart + decalage
        if fin <= debut:  # décalage négatif plus long que le séjour : rien à faire
            continue
        if resultat and debut - resultat[-1].fin <= maintien:
            p = resultat[-1]
            resultat[-1] = Plage(
                debut=p.debut,
                fin=max(p.fin, fin),
                arrivee=p.arrivee,
                depart=max(p.depart, s.depart),
                sejours=p.sejours + (s,),
            )
        else:
            resultat.append(Plage(debut, fin, s.arrivee, s.depart, (s,)))
    return resultat


def en_marche(liste: list[Plage], maintenant: datetime) -> Plage | None:
    """Plage qui contient l'instant, ou None."""
    for p in liste:
        if p.debut <= maintenant < p.fin:
            return p
    return None


def prochaine(liste: list[Plage], maintenant: datetime) -> Plage | None:
    for p in liste:
        if p.debut > maintenant:
            return p
    return None


def phase(
    sejours: Iterable[Sejour],
    maintenant: datetime,
    avance: timedelta,
    decalage: timedelta,
    jours_inter: float,
) -> Phase:
    """État des thermostats : préchauffage, occupé, inter-séjour ou vide."""
    liste = plages(sejours, avance, decalage)
    p = en_marche(liste, maintenant)
    suivante = prochaine(liste, maintenant)
    if p is not None:
        # séjours enchaînés dans une même plage : chaque arrivée ouvre une nouvelle phase
        courant = p.sejours[0]
        for s in p.sejours:
            if s.arrivee - avance <= maintenant:
                courant = s
        sur_place = any(s.arrivee <= maintenant < s.depart for s in p.sejours)
        etat = ETAT_PRECHAUFFAGE if maintenant < courant.arrivee and not sur_place else ETAT_OCCUPE
        cle = "sejour:" + (courant.arrivee - avance).isoformat()
        return Phase(etat, cle, p, suivante, courant.arrivee)
    precedente = None
    for q in liste:
        if q.fin <= maintenant:
            precedente = q
    if (
        precedente is not None
        and suivante is not None
        and suivante.debut - precedente.fin < timedelta(days=jours_inter)
    ):
        return Phase(ETAT_INTER, "inter:" + precedente.fin.isoformat(), None, suivante, suivante.arrivee)
    cle = "vide:" + (precedente.fin.isoformat() if precedente else "debut")
    return Phase(ETAT_VIDE, cle, None, suivante, suivante.arrivee if suivante else None)


def present(sejours: Iterable[Sejour], maintenant: datetime) -> list[Sejour]:
    """Séjours en cours (client ou propriétaire sur place)."""
    return [s for s in sejours if s.arrivee <= maintenant < s.depart]


def mode_selon_prevision(
    prevision: Iterable[dict[str, Any]] | None,
    arrivee: datetime,
    seuil: float,
) -> str:
    """Climatisation seulement si la prévision du jour d'arrivée, à 14 h, 15 h ou 16 h,
    dépasse le seuil ; chauffage sinon (et sans prévision)."""
    jour = arrivee.date()
    tz = arrivee.tzinfo
    maxi = None
    for p in prevision or []:
        try:
            quand = p["datetime"]
            if isinstance(quand, str):
                quand = datetime.fromisoformat(quand.replace("Z", "+00:00"))
            if quand.tzinfo is None:
                continue
            quand = quand.astimezone(tz)
            temp = float(p["temperature"])
        except (KeyError, TypeError, ValueError):
            continue
        if quand.date() == jour and quand.hour in HEURES_PREVISION_CLIM:
            maxi = temp if maxi is None else max(maxi, temp)
    if maxi is not None and maxi > seuil:
        return MODE_CLIM
    return MODE_CHAUFFAGE


def borne_client(
    mode: str | None, actuelle: float | None, tmin: float, tmax: float
) -> float | None:
    """Consigne à imposer si le réglage du client sort des bornes, sinon None.

    Chauffage : entre min et max. Climatisation : pas en dessous de la max.
    """
    if actuelle is None:
        return None
    if mode == MODE_CLIM:
        return tmax if actuelle < tmax else None
    if mode == MODE_CHAUFFAGE:
        if actuelle < tmin:
            return tmin
        if actuelle > tmax:
            return tmax
    return None


def consigne(etat: str, mode: str, role: str, reglages: dict[str, Any]) -> tuple[str, float]:
    """(mode, température) à appliquer à un thermostat au début d'une phase."""
    from .const import (  # import local : évite un cycle à la lecture du module
        CONF_CONSIGNE_CHAMBRE,
        CONF_CONSIGNE_VIVRE,
        CONF_TEMP_INTER,
        CONF_TEMP_MAX,
        CONF_TEMP_MIN,
    )

    if etat in (ETAT_PRECHAUFFAGE, ETAT_OCCUPE):
        if mode == MODE_CLIM:
            return MODE_CLIM, float(reglages[CONF_TEMP_MAX])
        cle = CONF_CONSIGNE_CHAMBRE if role == "chambre" else CONF_CONSIGNE_VIVRE
        return MODE_CHAUFFAGE, float(reglages[cle])
    if etat == ETAT_INTER:
        return MODE_CHAUFFAGE, float(reglages[CONF_TEMP_INTER])
    return MODE_CHAUFFAGE, float(reglages[CONF_TEMP_MIN])
