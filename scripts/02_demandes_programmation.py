"""02. Dépôt des demandes clients et programmation des acquisitions.

1. Enregistre les clients et leurs demandes (zone, période, cibles, formats, priorité).
2. Contrôle de recevabilité : zone dans l'emprise de production et période couverte.
3. Génère le catalogue des passages de la constellation simulée sur la zone.
4. Programme chaque demande recevable, par ordre de priorité : au plus N acquisitions
   par jour, chaque passage ayant une capacité limitée (arbitrage entre demandes).
"""
import random
from datetime import datetime, timedelta, timezone

from shapely.geometry import box

import config
from commun import base_production, iso, journal, statut

rng = random.Random(config.GRAINE)
cx = base_production()
for t in ("livraisons", "tickets", "lots", "programmations", "passages", "demandes", "clients",
          "historique_statuts", "journal"):
    if t != "journal":
        cx.execute(f"DELETE FROM {t}")
cx.execute("DELETE FROM journal WHERE etape <> 'preparation'")
cx.commit()

CLIENTS = [
    ("C01", "Direction régionale de contrôle des pêches (fictif)", "Contrôle des pêches", "controle.peches@exemple.fr"),
    ("C02", "Armor Risques Maritimes (fictif)", "Assurance maritime", "sinistres@exemple.fr"),
    ("C03", "Observatoire du milieu marin d'Iroise (fictif)", "Environnement", "observatoire@exemple.fr"),
    ("C04", "Ponant Offshore (fictif)", "Énergies marines", "projets@exemple.fr"),
]
cx.executemany("INSERT INTO clients VALUES (?,?,?,?)", CLIENTS)

# id, client, intitulé, zone (lon_min, lat_min, lon_max, lat_max), début, fin, cibles, formats, max/jour, priorité, dépôt, créneau UTC
DEMANDES = [
    ("D001", "C01", "Activité de pêche au large de Camaret et en mer d'Iroise",
     (-5.30, 48.00, -4.40, 48.45), "2015-11-02", "2015-11-15", "Pêche", "GPKG,CSV,KML,PDF", 2, 1, "2015-10-20", "06:00-20:00"),
    ("D002", "C02", "Trafic marchand dans les approches de Brest (Goulet et Iroise nord)",
     (-5.20, 48.15, -4.45, 48.45), "2015-11-10", "2015-11-20", "Cargo,Pétrolier/chimiquier", "GPKG,CSV,PDF", 3, 2, "2015-10-28", None),
    ("D003", "C03", "Pression des activités maritimes en baie de Douarnenez",
     (-4.90, 47.95, -4.20, 48.30), "2016-02-01", "2016-02-29", "Tous", "GPKG,CSV,KML,PDF", 2, 3, "2016-01-15", "07:00-19:00"),
    ("D004", "C04", "Surveillance d'un site éolien au large de Groix",
     (-3.70, 47.50, -3.30, 47.80), "2016-02-01", "2016-02-14", "Tous", "GPKG,PDF", 2, 2, "2016-01-12", None),
    ("D005", "C01", "Contrôle renforcé du secteur de Camaret (interdiction coquillages)",
     (-4.95, 48.15, -4.45, 48.40), "2016-01-05", "2016-01-19", "Pêche", "GPKG,CSV,KML,PDF", 3, 1, "2016-01-04", "06:00-20:00"),
]
Z = config.ZONE
emprise = box(Z["lon_min"], Z["lat_min"], Z["lon_max"], Z["lat_max"])
periode = ("2015-10-01", "2016-03-31")

for d in DEMANDES:
    ident, client, intitule, bb, debut, fin, cibles, formats, maxj, prio, depot, creneau = d
    zone = box(*bb)
    cx.execute("""INSERT INTO demandes (id, client_id, intitule, zone_wkt, date_debut, date_fin, cibles, formats,
                  max_acq_jour, priorite, date_depot, creneau) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
               (ident, client, intitule, zone.wkt, debut + "T00:00:00Z", fin + "T23:59:59Z",
                cibles, formats, maxj, prio, depot, creneau))
    journal(cx, "INFO", "demande", f"demande déposée : {intitule}", ident, depot + "T09:00:00")
    # Recevabilité
    if not emprise.contains(zone):
        part = zone.intersection(emprise).area / zone.area
        motif = f"zone hors emprise de production ({part:.0%} couverte) : réorienter vers l'offre globale"
        statut(cx, "demandes", ident, "refusee", motif=motif)
        journal(cx, "ALERTE", "demande", f"refusée, {motif}", ident, depot + "T10:00:00")
    elif not (periode[0] <= debut and fin <= periode[1]):
        statut(cx, "demandes", ident, "refusee", motif="période hors fenêtre de production")
        journal(cx, "ALERTE", "demande", "refusée, période hors fenêtre de production", ident, depot + "T10:00:00")
cx.commit()

# --- Catalogue des passages (constellation simulée) ------------------------------------------
jour = datetime(2015, 10, 1, tzinfo=timezone.utc)
fin_cat = datetime(2016, 3, 31, tzinfo=timezone.utc)
passages = []
while jour <= fin_cat:
    n = rng.randint(*config.PASSAGES_PAR_JOUR)
    heures = sorted(rng.uniform(0.5, 23.5) for _ in range(n))
    for i, h in enumerate(heures, 1):
        debut = jour + timedelta(hours=h)
        debut = debut.replace(second=0, microsecond=0)
        pid = f"P{jour:%Y%m%d}-{i:02d}"
        passages.append((pid, rng.choice(config.SATELLITES), iso(debut),
                         iso(debut + timedelta(seconds=config.DUREE_ACQUISITION_S))))
    jour += timedelta(days=1)
cx.executemany("INSERT INTO passages VALUES (?,?,?,?)", passages)
cx.commit()
journal(cx, "INFO", "programmation", f"catalogue : {len(passages)} passages simulés du 01/10/2015 au 31/03/2016")

# --- Programmation par priorité ---------------------------------------------------------------
CAPACITE = 1   # une seule zone écoutée par passage : les demandes concurrentes sont arbitrées
occupation = {}
recevables = cx.execute("""SELECT * FROM demandes WHERE statut = 'deposee'
                           ORDER BY priorite, date_depot""").fetchall()
for d in recevables:
    retenus, conflits = [], 0
    j = datetime.fromisoformat(d["date_debut"][:10]).replace(tzinfo=timezone.utc)
    fin = datetime.fromisoformat(d["date_fin"][:10]).replace(tzinfo=timezone.utc)
    while j <= fin:
        cand = [p for p in passages if p[0].startswith(f"P{j:%Y%m%d}")]
        if d["creneau"]:
            h0, h1 = d["creneau"].split("-")
            cand = [p for p in cand if h0 <= p[2][11:16] <= h1]
        libres = [p for p in cand if occupation.get(p[0], 0) < CAPACITE]
        conflits += len(cand) - len(libres)
        # répartir les acquisitions dans la journée
        if len(libres) > d["max_acq_jour"]:
            pas = len(libres) / d["max_acq_jour"]
            libres = [libres[int(k * pas)] for k in range(d["max_acq_jour"])]
        for p in libres:
            occupation[p[0]] = occupation.get(p[0], 0) + 1
            retenus.append(p)
        j += timedelta(days=1)
    cx.executemany("INSERT INTO programmations (id, demande_id, passage_id) VALUES (?,?,?)",
                   [(f"{d['id']}-{p[0]}", d["id"], p[0]) for p in retenus])
    statut(cx, "demandes", d["id"], "programmee")
    msg = f"{len(retenus)} acquisitions programmées"
    if conflits:
        msg += f", {conflits} passages déjà attribués à une demande plus prioritaire"
    journal(cx, "ALERTE" if conflits else "INFO", "programmation", msg, d["id"], d["date_depot"] + "T11:00:00")

for r in cx.execute("SELECT demande, client, statut, nb_programmees FROM v_avancement"):
    print(dict(r))
