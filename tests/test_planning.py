"""Calcul pur : plages, phases, mode selon la prévision, bornes du client."""
from datetime import timedelta
import unittest

from outils import charger_planning, fixture, heure

P = charger_planning()
H = timedelta(hours=1)


def sejours_du(logement_id: int):
    for l in fixture()["logements"]:
        if l["id"] == logement_id:
            return P.lire_sejours(l["sejours"])
    raise KeyError(logement_id)


class Lecture(unittest.TestCase):
    def test_fixture_reelle(self):
        s = sejours_du(13)
        self.assertEqual([x.id for x in s], [8514, 8545, 8364, 8406])
        self.assertEqual(s[0].arrivee, heure("2026-10-12 16:00"))
        # départ après le passage à l'heure d'hiver : +01:00
        self.assertEqual(s[2].depart.utcoffset(), timedelta(hours=1))
        self.assertEqual(s[2].depart, heure("2026-10-31 10:00"))

    def test_lignes_invalides_ignorees(self):
        s = P.lire_sejours([
            {"id": 1, "arrivee": "pas une date", "depart": "2026-10-14T10:00:00+02:00"},
            {"id": 2, "arrivee": "2026-10-14T16:00:00", "depart": "2026-10-15T10:00:00"},  # sans fuseau
            {"id": 3, "arrivee": "2026-10-15T16:00:00+02:00", "depart": "2026-10-14T10:00:00+02:00"},  # à l'envers
            {"id": 4, "arrivee": "2026-10-14T16:00:00+02:00", "depart": "2026-10-15T10:00:00+02:00", "personnes": 2},
            {"arrivee": "2026-10-14T16:00:00+02:00"},
        ])
        self.assertEqual([x.id for x in s], [4])


class Plages(unittest.TestCase):
    def test_avance_et_decalage(self):
        s = sejours_du(9)  # 30/10 16:00 → 01/11 10:00
        p = P.plages(s, 6 * H, -2 * H)
        self.assertEqual(len(p), 1)
        self.assertEqual(p[0].debut, heure("2026-10-30 10:00"))
        self.assertEqual(p[0].fin, heure("2026-11-01 08:00"))

    def test_reunion_selon_maintien(self):
        s = sejours_du(13)  # 12→15, 16→21, 21→31, 11/11→14/11
        sans = P.plages(s, 6 * H, 0 * H)
        # 21/10 : départ 10 h, arrivée 16 h, mise en route 10 h → se touchent → réunies
        self.assertEqual(len(sans), 3)
        self.assertEqual([len(x.sejours) for x in sans], [1, 2, 1])
        deux_jours = P.plages(s, 6 * H, 0 * H, timedelta(days=2))
        self.assertEqual([len(x.sejours) for x in deux_jours], [3, 1])
        self.assertEqual(deux_jours[0].arrivee, heure("2026-10-12 16:00"))
        self.assertEqual(deux_jours[0].depart, heure("2026-10-31 10:00"))

    def test_decalage_negatif_plus_long_que_le_sejour(self):
        s = P.lire_sejours([{"id": 1, "arrivee": "2026-10-14T16:00:00+02:00", "depart": "2026-10-14T18:00:00+02:00"}])
        self.assertEqual(P.plages(s, 0 * H, -3 * H), [])


class Phases(unittest.TestCase):
    def setUp(self):
        self.s = sejours_du(13)

    def phase(self, quand, jours_inter=3):
        return P.phase(self.s, heure(quand), 6 * H, 0 * H, jours_inter)

    def test_occupe(self):
        ph = self.phase("2026-10-13 11:15")
        self.assertEqual(ph.etat, "occupe")
        self.assertEqual(ph.plage.arrivee, heure("2026-10-12 16:00"))

    def test_inter_sejour_puis_prechauffage(self):
        self.assertEqual(self.phase("2026-10-15 12:00").etat, "inter_sejour")
        self.assertEqual(self.phase("2026-10-15 12:00", jours_inter=1).etat, "vide")  # écart 15/10 10h → 16/10 10h = 1 j
        ph = self.phase("2026-10-16 11:00")
        self.assertEqual(ph.etat, "prechauffage")
        self.assertEqual(self.phase("2026-10-16 16:00").etat, "occupe")

    def test_cle_change_a_chaque_phase(self):
        cles = {self.phase(q).cle for q in ("2026-10-13 11:00", "2026-10-14 11:00")}
        self.assertEqual(len(cles), 1)
        self.assertNotEqual(self.phase("2026-10-13 11:00").cle, self.phase("2026-10-15 12:00").cle)
        self.assertNotEqual(self.phase("2026-10-15 12:00").cle, self.phase("2026-10-16 11:00").cle)

    def test_arrivee_le_jour_d_un_depart(self):
        # 21/10 : départ 10 h et arrivée 16 h, plage réunie mais nouvelle phase à 10 h (16 h − 6 h)
        avant = self.phase("2026-10-21 09:00")
        pendant = self.phase("2026-10-21 10:30")
        self.assertEqual(avant.etat, "occupe")
        self.assertEqual(pendant.etat, "prechauffage")
        self.assertNotEqual(avant.cle, pendant.cle)
        self.assertEqual(pendant.arrivee, heure("2026-10-21 16:00"))
        self.assertEqual(self.phase("2026-10-21 16:00").cle, pendant.cle)

    def test_chevauchement_reste_occupe(self):
        s = P.lire_sejours([
            {"id": 1, "arrivee": "2026-10-14T16:00:00+02:00", "depart": "2026-10-16T10:00:00+02:00"},
            {"id": 2, "arrivee": "2026-10-16T08:00:00+02:00", "depart": "2026-10-18T10:00:00+02:00"},
        ])
        ph = P.phase(s, heure("2026-10-16 07:00"), 6 * H, 0 * H, 3)
        self.assertEqual(ph.etat, "occupe")  # client 1 encore là pendant la mise en route du 2
        self.assertEqual(ph.arrivee, heure("2026-10-16 08:00"))

    def test_vide_et_suivante(self):
        ph = self.phase("2026-11-03 12:00")  # 31/10 → 11/11 : 11 jours
        self.assertEqual(ph.etat, "vide")
        self.assertEqual(ph.suivante.arrivee, heure("2026-11-11 16:00"))
        self.assertEqual(P.phase([], heure("2026-11-03 12:00"), 6 * H, 0 * H, 3).etat, "vide")

    def test_present(self):
        self.assertEqual([x.id for x in P.present(self.s, heure("2026-10-13 12:00"))], [8514])
        self.assertEqual(P.present(self.s, heure("2026-10-15 12:00")), [])


class Mode(unittest.TestCase):
    arrivee = heure("2026-07-20 16:00")

    def prev(self, *valeurs):
        return [{"datetime": f"2026-07-20T{h:02d}:00:00+02:00", "temperature": t} for h, t in valeurs]

    def test_chauffage_par_defaut(self):
        self.assertEqual(P.mode_selon_prevision(None, self.arrivee, 30), "heat")
        self.assertEqual(P.mode_selon_prevision([], self.arrivee, 30), "heat")
        self.assertEqual(P.mode_selon_prevision(self.prev((15, 30)), self.arrivee, 30), "heat")  # égal : pas de clim

    def test_clim_si_14h_16h_depasse(self):
        self.assertEqual(P.mode_selon_prevision(self.prev((14, 25), (15, 31), (16, 29)), self.arrivee, 30), "cool")

    def test_autres_heures_ignorees(self):
        self.assertEqual(P.mode_selon_prevision(self.prev((13, 35), (17, 35)), self.arrivee, 30), "heat")
        lendemain = [{"datetime": "2026-07-21T15:00:00+02:00", "temperature": 40}]
        self.assertEqual(P.mode_selon_prevision(lendemain, self.arrivee, 30), "heat")

    def test_prevision_en_utc(self):
        # 13:00 UTC = 15:00 à Paris en été
        p = [{"datetime": "2026-07-20T13:00:00Z", "temperature": 33}]
        self.assertEqual(P.mode_selon_prevision(p, self.arrivee, 30), "cool")


class Bornes(unittest.TestCase):
    def test_chauffage(self):
        self.assertEqual(P.borne_client("heat", 28, 14, 24), 24)
        self.assertEqual(P.borne_client("heat", 10, 14, 24), 14)
        self.assertIsNone(P.borne_client("heat", 21, 14, 24))

    def test_climatisation_max_devient_min(self):
        self.assertEqual(P.borne_client("cool", 20, 14, 24), 24)
        self.assertIsNone(P.borne_client("cool", 26, 14, 24))

    def test_autres_modes_libres(self):
        self.assertIsNone(P.borne_client("off", 5, 14, 24))
        self.assertIsNone(P.borne_client("heat", None, 14, 24))


class Consignes(unittest.TestCase):
    r = {"consigne_chambre": 19, "consigne_vivre": 21, "temp_min": 14, "temp_max": 24, "temp_inter": 16}

    def test_toutes(self):
        self.assertEqual(P.consigne("occupe", "heat", "chambre", self.r), ("heat", 19))
        self.assertEqual(P.consigne("prechauffage", "heat", "vivre", self.r), ("heat", 21))
        self.assertEqual(P.consigne("occupe", "cool", "chambre", self.r), ("cool", 24))
        self.assertEqual(P.consigne("inter_sejour", "cool", "vivre", self.r), ("heat", 16))
        self.assertEqual(P.consigne("vide", "cool", "vivre", self.r), ("heat", 14))


if __name__ == "__main__":
    unittest.main()
