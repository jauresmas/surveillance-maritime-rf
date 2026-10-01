"""03. Acquisitions : chaque passage programmé produit un lot brut (ou un échec d'acquisition).

Le segment sol simulé dépose les fichiers bruts dans donnees/produites/lots_bruts.
Deux lots reçoivent un défaut grave (dérive d'horloge, colonnes inversées) pour éprouver
le contrôle qualité et la procédure de retraitement.
"""
import shutil
import sqlite3
import zlib
from datetime import datetime, timedelta

import numpy as np

import config
from commun import base_production, iso, journal, statut
from simulateur import simuler_lot

cx = base_production()
cx.execute("DELETE FROM lots")
cx.execute("UPDATE programmations SET statut = 'programmee'")
cx.execute("DELETE FROM journal WHERE etape NOT IN ('preparation','demande','programmation')")
cx.commit()
shutil.rmtree(config.LOTS_BRUTS, ignore_errors=True)
config.LOTS_BRUTS.mkdir(parents=True)

if config.VERITE.exists():
    config.VERITE.unlink()
ver = sqlite3.connect(config.VERITE)
ver.execute("""CREATE TABLE verite (det_id TEXT PRIMARY KEY, lot_id TEXT, mmsi INTEGER, categorie TEXT,
               dans_interdiction INTEGER, lon_vrai REAL, lat_vrai REAL)""")
ais = sqlite3.connect(config.AIS_DB)
ais.execute("DROP TABLE IF EXISTS coupures_ais")
ais.execute("CREATE TABLE coupures_ais (mmsi INTEGER, t_debut INTEGER, t_fin INTEGER)")

progs = cx.execute("""SELECT pr.id, pr.demande_id, pa.id AS passage_id, pa.satellite, pa.debut_utc, pa.fin_utc
                      FROM programmations pr JOIN passages pa ON pa.id = pr.passage_id
                      ORDER BY pa.debut_utc""").fetchall()
# 5e acquisition de D001 : dérive d'horloge ; 6e de D003 : colonnes inversées
def rang(demande, n):
    ids = [p["id"] for p in progs if p["demande_id"] == demande]
    return ids[n - 1]


DEFAUTS = {rang("D001", 5): "horloge", rang("D003", 6): "inversion"}
aleat = np.random.default_rng(config.GRAINE)
n_lots = n_det = 0
for pr in progs:
    d = cx.execute("SELECT * FROM demandes WHERE id = ?", (pr["demande_id"],)).fetchone()
    fin = datetime.fromisoformat(pr["fin_utc"].replace("Z", "+00:00"))
    if pr["id"] not in DEFAUTS and aleat.random() < config.PROBA_ECHEC_ACQUISITION:
        statut(cx, "programmations", pr["id"], "echec_acquisition")
        journal(cx, "ALERTE", "acquisition", f"{pr['satellite']} : pas de données reçues (anomalie segment sol), "
                "reprogrammation à étudier", pr["id"], fin + timedelta(minutes=30))
        continue
    lot_id = "L-" + pr["id"]
    rng = np.random.default_rng(zlib.crc32(lot_id.encode()))
    chemin, verite, t = simuler_lot(dict(pr), dict(d), rng, defaut=DEFAUTS.get(pr["id"]))
    reception = fin + timedelta(minutes=int(aleat.uniform(20, 90)))
    nb_brut = sum(1 for _ in open(chemin, encoding="utf-8")) - 1
    cx.execute("""INSERT INTO lots (id, programmation_id, fichier_brut, date_reception, nb_brut, statut)
                  VALUES (?,?,?,?,?, 'recu')""", (lot_id, pr["id"], chemin.name, iso(reception), nb_brut))
    statut(cx, "programmations", pr["id"], "acquise")
    if d["statut"] == "programmee":
        statut(cx, "demandes", d["id"], "en_production")
    ver.executemany("INSERT INTO verite VALUES (?,?,?,?,?,?,?)",
                    [(v["det_id"], lot_id, v["mmsi"], v["categorie"], v["dans_interdiction"], v["lon_vrai"], v["lat_vrai"])
                     for v in verite])
    ais.executemany("INSERT INTO coupures_ais VALUES (?,?,?)",
                    [(int(v["mmsi"]), t - 7200, t + 7200) for v in verite if v["categorie"] == "sombre"])
    n_lots += 1
    n_det += nb_brut
    journal(cx, "INFO", "acquisition", f"lot reçu : {chemin.name}, {nb_brut} lignes", lot_id, reception)
ver.commit()
ais.commit()
cats = dict(ver.execute("SELECT categorie, COUNT(*) FROM verite GROUP BY categorie").fetchall())
journal(cx, "INFO", "acquisition", f"{n_lots} lots reçus, {n_det} lignes brutes ; vérité : {cats}")
