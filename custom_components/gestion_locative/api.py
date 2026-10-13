"""Client de l'API de l'outil de gestion locative."""
from __future__ import annotations

import asyncio
from typing import Any

import aiohttp

from .const import JOURS_HORIZON


class ErreurApi(Exception):
    """L'outil n'a pas répondu correctement (réseau, serveur)."""


class CleRefusee(ErreurApi):
    """Clé absente, invalide ou révoquée (401)."""


class LogementInconnu(ErreurApi):
    """Un logement choisi ne fait plus partie du compte (404)."""


def normaliser_url(url: str) -> str:
    url = (url or "").strip()
    if url and "://" not in url:
        url = "https://" + url
    if not url.endswith("/"):
        url += "/"
    return url


class ClientGestionLocative:
    """Appels GET api/logements et api/occupation, clé dans l'en-tête X-Cle-Api."""

    def __init__(self, session: aiohttp.ClientSession, url: str, cle: str) -> None:
        self._session = session
        self._url = normaliser_url(url)
        self._cle = cle.strip()

    async def _get(self, chemin: str, params: dict[str, str] | None = None) -> dict[str, Any]:
        try:
            async with asyncio.timeout(30):
                async with self._session.get(
                    self._url + chemin,
                    params=params,
                    headers={"X-Cle-Api": self._cle, "Accept": "application/json"},
                ) as rep:
                    if rep.status == 401:
                        raise CleRefusee("clé refusée")
                    donnees = await rep.json(content_type=None)
                    if rep.status == 404 and isinstance(donnees, dict) and donnees.get("erreur") == "logement_inconnu":
                        raise LogementInconnu(donnees.get("message", "logement inconnu"))
                    if rep.status != 200 or not isinstance(donnees, dict):
                        raise ErreurApi(f"réponse {rep.status}")
                    return donnees
        except (aiohttp.ClientError, TimeoutError, ValueError) as err:
            raise ErreurApi(str(err) or err.__class__.__name__) from err

    async def logements(self) -> dict[str, Any]:
        """Compte et logements accessibles avec la clé."""
        return await self._get("api/logements")

    async def occupation(self, logements: list[int], jours: int = JOURS_HORIZON) -> dict[str, Any]:
        """Séjours à venir des logements choisis."""
        return await self._get(
            "api/occupation",
            {"logements": ",".join(str(i) for i in logements), "jours": str(jours)},
        )
