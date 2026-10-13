"""Pilotage avec un Home Assistant simulé, sur le planning réel anonymisé (tests/occupation.json)."""
import asyncio
import unittest

from datetime import timedelta

from outils import Entree, FauxHass, Horloge, Minuteries, Store, charger_tout, fixture, heure

pilote_mod, coord_mod, api_mod = charger_tout()

OPTIONS = {
    "meteo": "weather.maison",
    "cibles": {
        "13": {
            "thermostats_chambre": ["climate.chambre"],
            "thermostats_vivre": ["climate.sejour"],
            "consigne_chambre": 19,
            "consigne_vivre": 21,
            "temp_min": 14,
            "temp_max": 24,
            "temp_inter": 16,
            "jours_inter": 3,
            "seuil_clim": 30,
            "heures_avant": 6,
            "decalage_depart": 0,
            "equipements": [
                {"entite": "switch.vmc", "heures_avant": 2, "decalage_depart": 1, "jours_maintien": 2},
                {"entite": "script.ballon", "heures_avant": 12},
                {"entite": "automation.eclairage", "heures_avant": 0},
            ],
        },
        "14": {"thermostats_vivre": ["climate.vide"]},
        "batiment": {"equipements": [{"entite": "switch.couloir", "heures_avant": 1}]},
    },
}


class FauxClient:
    def __init__(self):
        self.panne = False
        self.cle_refusee = False

    async def occupation(self, ids, jours=60):
        if self.cle_refusee:
            raise api_mod.CleRefusee("401")
        if self.panne:
            raise api_mod.ErreurApi("réseau")
        return fixture()


def run(coro):
    return asyncio.run(coro)


class Pilotage(unittest.TestCase):
    def setUp(self):
        Store.disque.clear()
        Minuteries.en_attente.clear()
        Horloge.maintenant = heure("2026-10-13 11:15")

    async def monter(self, options=OPTIONS, ids=(13, 14)):
        hass = FauxHass()
        st = hass.states
        st.set("climate.chambre", "heat", {"hvac_modes": ["off", "heat", "cool"], "temperature": 17})
        st.set("climate.sejour", "heat", {"hvac_modes": ["off", "heat"], "temperature": 17})
        st.set("climate.vide", "cool", {"hvac_modes": ["off", "heat", "cool"], "temperature": 22})
        st.set("weather.maison", "sunny")
        for e in ("switch.vmc", "automation.eclairage", "switch.couloir"):
            st.set(e, "off")
        st.set("script.ballon", "off")
        entree = Entree(list(ids), options)
        client = FauxClient()
        coord = coord_mod.CoordinateurOccupation(hass, entree, client)
        await coord.async_refresh()
        p = pilote_mod.Pilote(hass, entree, coord)
        await p.async_demarrer()
        return hass, p, coord, client

    def appels(self, hass, entite):
        return [(d, s, x) for d, s, x in hass.services.appels if x.get("entity_id") == entite]

    def test_sejour_en_cours(self):
        async def t():
            hass, p, *_ = await self.monter()
            await p.async_executer()
            st = hass.states
            self.assertEqual(p.resumes["13"].etat, "occupe")
            self.assertTrue(p.resumes["13"].occupe)
            self.assertEqual(p.resumes["13"].personnes, 2)
            self.assertEqual(p.resumes["13"].prochain_depart, heure("2026-10-15 10:00"))
            self.assertEqual(p.resumes["13"].prochaine_arrivee, heure("2026-10-16 16:00"))
            self.assertEqual((st.get("climate.chambre").state, st.get("climate.chambre").attributes["temperature"]), ("heat", 19))
            self.assertEqual(st.get("climate.sejour").attributes["temperature"], 21)
            # logement vide : chauffage à la min, la clim est arrêtée
            self.assertEqual((st.get("climate.vide").state, st.get("climate.vide").attributes["temperature"]), ("heat", 14))
            self.assertEqual(p.resumes["14"].etat, "vide")
            self.assertEqual(st.get("switch.vmc").state, "on")
            self.assertEqual(st.get("automation.eclairage").state, "on")
            self.assertEqual(self.appels(hass, "script.ballon")[0][:2], ("script", "turn_on"))
            # bâtiment occupé (logement 13)
            self.assertTrue(p.resumes["batiment"].occupe)
            self.assertEqual(st.get("switch.couloir").state, "on")
            # second passage : rien à refaire
            hass.services.vider()
            await p.async_executer()
            self.assertEqual(hass.services.appels, [])
        run(t())

    def test_reglage_du_client_borne_apres_une_minute(self):
        async def t():
            hass, p, *_ = await self.monter()
            await p.async_executer()
            hass.services.vider()
            evt = type("E", (), {"data": {"entity_id": "climate.chambre"}})()
            modes = {"hvac_modes": ["off", "heat", "cool"]}
            # le client monte à 26 puis 28 : rien tant qu'il règle
            hass.states.set("climate.chambre", "heat", {**modes, "temperature": 26})
            p._sur_thermostat(evt)
            Horloge.maintenant += timedelta(seconds=40)
            hass.states.set("climate.chambre", "heat", {**modes, "temperature": 28})
            p._sur_thermostat(evt)
            Horloge.maintenant += timedelta(seconds=40)
            Minuteries.declencher()
            await p.async_executer()  # passage périodique pendant l'attente : rien non plus
            await hass.finir()
            self.assertEqual(hass.services.appels, [])
            self.assertEqual(len(Minuteries.en_attente), 1)  # une seule attente, relancée
            # une minute après le dernier changement : ramené à 24
            Horloge.maintenant += timedelta(seconds=25)
            Minuteries.declencher()
            await hass.finir()
            self.assertEqual(hass.states.get("climate.chambre").attributes["temperature"], 24)
            # à 22 : laissé
            hass.states.set("climate.chambre", "heat", {**modes, "temperature": 22})
            p._sur_thermostat(evt)
            Horloge.maintenant += timedelta(minutes=2)
            Minuteries.declencher()
            hass.services.vider()
            await hass.finir()
            await p.async_executer()
            self.assertEqual(hass.services.appels, [])
            # le client éteint : on respecte
            hass.states.set("climate.chambre", "off", {**modes, "temperature": 5})
            Horloge.maintenant += timedelta(minutes=2)
            await p.async_executer()
            self.assertEqual(hass.states.get("climate.chambre").state, "off")
        run(t())

    def test_borne_au_passage_periodique_si_reglage_ancien(self):
        async def t():
            hass, p, *_ = await self.monter()
            await p.async_executer()
            # réglage changé sans événement reçu (HA redémarré, par exemple)
            hass.states.set("climate.chambre", "heat", {"hvac_modes": ["off", "heat", "cool"], "temperature": 30})
            await p.async_executer()
            self.assertEqual(hass.states.get("climate.chambre").attributes["temperature"], 30)  # trop récent
            Horloge.maintenant += timedelta(minutes=5)
            await p.async_executer()
            self.assertEqual(hass.states.get("climate.chambre").attributes["temperature"], 24)
        run(t())

    def test_bornes_aussi_hors_sejour(self):
        async def t():
            hass, p, *_ = await self.monter()
            Horloge.maintenant = heure("2026-10-15 12:00")  # inter-séjour
            await p.async_executer()
            evt = type("E", (), {"data": {"entity_id": "climate.chambre"}})()
            modes = {"hvac_modes": ["off", "heat", "cool"]}
            hass.states.set("climate.chambre", "heat", {**modes, "temperature": 30})
            p._sur_thermostat(evt)
            Horloge.maintenant += timedelta(minutes=2)
            Minuteries.declencher()
            await hass.finir()
            self.assertEqual(hass.states.get("climate.chambre").attributes["temperature"], 24)
            # vacance : pas en dessous de la min (hors-gel)
            Horloge.maintenant = heure("2026-11-03 12:00")
            await p.async_executer()
            self.assertEqual(hass.states.get("climate.chambre").attributes["temperature"], 14)
            hass.states.set("climate.chambre", "heat", {**modes, "temperature": 5})
            p._sur_thermostat(evt)
            Horloge.maintenant += timedelta(minutes=2)
            Minuteries.declencher()
            await hass.finir()
            self.assertEqual(hass.states.get("climate.chambre").attributes["temperature"], 14)
            # suspendu : libre
            p.suspendu["13"] = True
            hass.states.set("climate.chambre", "heat", {**modes, "temperature": 30})
            p._sur_thermostat(evt)
            Horloge.maintenant += timedelta(minutes=2)
            Minuteries.declencher()
            await p.async_executer()
            await hass.finir()
            self.assertEqual(hass.states.get("climate.chambre").attributes["temperature"], 30)
        run(t())

    def test_enchainement_inter_sejour_puis_arrivee(self):
        async def t():
            hass, p, *_ = await self.monter()
            await p.async_executer()
            Horloge.maintenant = heure("2026-10-15 12:00")  # parti à 10 h, prochain le 16 à 16 h
            await p.async_executer()
            self.assertEqual(p.resumes["13"].etat, "inter_sejour")
            self.assertEqual(hass.states.get("climate.chambre").attributes["temperature"], 16)
            self.assertEqual(hass.states.get("switch.vmc").state, "on")  # maintien 2 j
            self.assertEqual(hass.states.get("automation.eclairage").state, "off")
            self.assertEqual(self.appels(hass, "script.ballon")[-1][:2], ("script", "turn_on"))  # rien à l'arrêt
            # le client précédent avait réglé 22 : la mise en route repart de la consigne
            hass.states.set("climate.chambre", "heat", {"hvac_modes": ["off", "heat", "cool"], "temperature": 22})
            Horloge.maintenant = heure("2026-10-16 10:05")
            await p.async_executer()
            self.assertEqual(p.resumes["13"].etat, "prechauffage")
            self.assertEqual(hass.states.get("climate.chambre").attributes["temperature"], 19)
        run(t())

    def test_arrivee_le_jour_du_depart_remet_la_consigne(self):
        async def t():
            hass, p, *_ = await self.monter()
            Horloge.maintenant = heure("2026-10-20 20:00")
            await p.async_executer()
            hass.states.set("climate.chambre", "heat", {"hvac_modes": ["off", "heat", "cool"], "temperature": 23})
            Horloge.maintenant = heure("2026-10-21 09:00")
            await p.async_executer()
            self.assertEqual(hass.states.get("climate.chambre").attributes["temperature"], 23)
            Horloge.maintenant = heure("2026-10-21 10:05")  # départ 10 h, arrivée 16 h, mise en route 10 h
            await p.async_executer()
            self.assertEqual(hass.states.get("climate.chambre").attributes["temperature"], 19)
        run(t())

    def test_climatisation_si_prevision_chaude(self):
        async def t():
            hass, p, *_ = await self.monter()
            hass.services.prevision = [
                {"datetime": "2026-10-16T15:00:00+02:00", "temperature": 31},
                {"datetime": "2026-10-16T12:00:00+02:00", "temperature": 35},
            ]
            Horloge.maintenant = heure("2026-10-15 12:00")
            await p.async_executer()
            self.assertEqual(p.resumes["13"].mode_prevu, "cool")  # prévision affichée
            self.assertEqual(hass.states.get("climate.chambre").state, "heat")  # pas de clim entre deux séjours
            Horloge.maintenant = heure("2026-10-16 10:05")
            await p.async_executer()
            ch, sej = hass.states.get("climate.chambre"), hass.states.get("climate.sejour")
            self.assertEqual((ch.state, ch.attributes["temperature"]), ("cool", 24))
            self.assertEqual(sej.attributes["temperature"], 16)  # sans clim : non touché
            # la prévision baisse ensuite : le mode décidé à la mise en route reste
            hass.services.prevision = []
            Horloge.maintenant = heure("2026-10-16 18:00")
            await p.async_executer()
            self.assertEqual(p.resumes["13"].mode_prevu, "cool")
            # en clim, le client ne descend pas sous la max
            hass.states.set("climate.chambre", "cool", {"hvac_modes": ["off", "heat", "cool"], "temperature": 20})
            Horloge.maintenant += timedelta(minutes=2)
            await p.async_executer()
            self.assertEqual(hass.states.get("climate.chambre").attributes["temperature"], 24)
        run(t())

    def test_appareil_injoignable_reessaye(self):
        async def t():
            hass, p, *_ = await self.monter()
            hass.services.en_panne.add("climate.chambre")
            hass.states.set("switch.vmc", "unavailable")
            await p.async_executer()
            self.assertEqual(hass.states.get("climate.chambre").attributes["temperature"], 17)
            hass.services.en_panne.clear()
            await p.async_executer()
            self.assertEqual(hass.states.get("climate.chambre").attributes["temperature"], 19)
        run(t())

    def test_thermostat_inconnu_commande_injoignable_ignore(self):
        async def t():
            hass, p, *_ = await self.monter()
            hass.states.set("climate.chambre", "unknown", {"hvac_modes": ["off", "heat"], "temperature": 17})
            hass.states.set("climate.sejour", "unavailable", {})
            await p.async_executer()
            ch = hass.states.get("climate.chambre")
            self.assertEqual((ch.state, ch.attributes["temperature"]), ("heat", 19))
            self.assertEqual(self.appels(hass, "climate.sejour"), [])
        run(t())

    def test_suspendre(self):
        async def t():
            hass, p, *_ = await self.monter()
            p.suspendu["13"] = True
            await p.async_executer()
            self.assertEqual(self.appels(hass, "climate.chambre"), [])
            self.assertEqual(p.resumes["13"].etat, "occupe")  # l'état reste affiché
            await p.async_suspendre("13", False)
            self.assertEqual(hass.states.get("climate.chambre").attributes["temperature"], 19)
        run(t())

    def test_memoire_apres_redemarrage(self):
        async def t():
            hass, p, *_ = await self.monter()
            await p.async_executer()
            hass.states.set("climate.chambre", "heat", {"hvac_modes": ["off", "heat", "cool"], "temperature": 22})
            p2 = pilote_mod.Pilote(hass, p.entry, p.coordinateur)
            await p2.async_demarrer()
            hass.services.vider()
            await p2.async_executer()
            self.assertEqual(self.appels(hass, "climate.chambre"), [])  # réglage du client gardé
        run(t())

    def test_reglages_modifies_reappliques(self):
        async def t():
            hass, p, *_ = await self.monter()
            await p.async_executer()
            import copy
            opts = copy.deepcopy(OPTIONS)
            opts["cibles"]["13"]["consigne_chambre"] = 20
            p.entry.options = opts
            p2 = pilote_mod.Pilote(hass, p.entry, p.coordinateur)
            await p2.async_demarrer()
            await p2.async_executer()
            self.assertEqual(hass.states.get("climate.chambre").attributes["temperature"], 20)
        run(t())

    def test_panne_reseau_garde_le_planning(self):
        async def t():
            hass, p, coord, client = await self.monter()
            client.panne = True
            await coord.async_refresh()
            self.assertFalse(coord.data.a_jour)
            self.assertEqual(len(coord.data.logements[13].sejours), 4)
            # redémarrage de HA pendant la panne : le planning vient du disque
            coord2 = coord_mod.CoordinateurOccupation(hass, p.entry, client)
            await coord2.async_refresh()
            self.assertEqual(len(coord2.data.logements[13].sejours), 4)
            # sans planning en mémoire : échec
            Store.disque.clear()
            coord3 = coord_mod.CoordinateurOccupation(hass, p.entry, client)
            with self.assertRaises(coord_mod.UpdateFailed):
                await coord3.async_refresh()
            client.panne, client.cle_refusee = False, True
            with self.assertRaises(coord_mod.ConfigEntryAuthFailed):
                await coord3.async_refresh()
        run(t())

    def test_batiment_mieux_disant(self):
        async def t():
            hass, p, *_ = await self.monter(ids=(11, 12, 14))
            Horloge.maintenant = heure("2026-10-19 12:00")  # 11 : 16→18 et 20→22 ; 12 : 26/10
            await p.async_executer()
            self.assertEqual(p.resumes["batiment"].etat, "inter_sejour")  # 18 → 20/10 : 2 j < 3
            Horloge.maintenant = heure("2026-10-20 15:30")
            await p.async_executer()
            self.assertEqual(hass.states.get("switch.couloir").state, "on")
            self.assertEqual(p.resumes["batiment"].prochain_depart, heure("2026-10-22 10:00"))
        run(t())


if __name__ == "__main__":
    unittest.main()
