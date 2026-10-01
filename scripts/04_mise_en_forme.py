"""04. Mise en forme des lots reçus : schéma normalisé, types, unités, géoréférencement.

Entrée : fichiers capteur bruts (CSV ;). Sortie : lots_formes/<lot>_v<n>.gpkg, couche mise_en_forme.
"""
from datetime import datetime, timedelta

import config
from commun import base_production, journal, statut
from production import mettre_en_forme

cx = base_production()
# Rejeu : tous les lots repartent de leur version 1 reçue
cx.execute("DELETE FROM journal WHERE etape IN ('mise_en_forme','controle_qualite','retraitement')")
cx.execute("""UPDATE lots SET statut = 'recu', version = 1, nb_valide = NULL, score_qc = NULL, verdict_qc = NULL,
              fichier_brut = replace(fichier_brut, '_v2.csv', '_v1.csv')""")
cx.commit()
for f in list(config.LOTS_FORMES.glob("*.gpkg")) + list(config.LOTS_BRUTS.glob("*_v2.csv")):
    f.unlink()

lots = cx.execute("""SELECT l.*, p.demande_id, pa.satellite FROM lots l
                     JOIN programmations p ON p.id = l.programmation_id
                     JOIN passages pa ON pa.id = p.passage_id
                     WHERE l.statut = 'recu' ORDER BY l.date_reception""").fetchall()
ok = 0
for lot in lots:
    t = datetime.fromisoformat(lot["date_reception"].replace("Z", "")) + timedelta(minutes=5)
    try:
        gdf = mettre_en_forme(config.LOTS_BRUTS / lot["fichier_brut"], dict(lot))
    except Exception as e:  # fichier illisible : le lot reste « reçu » et l'astreinte est alertée
        journal(cx, "ERREUR", "mise_en_forme", f"échec de lecture : {e}", lot["id"], t)
        continue
    sortie = config.LOTS_FORMES / f"{lot['id']}_v{lot['version']}.gpkg"
    g = gdf.copy()
    g["horodatage"] = g.horodatage.dt.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    g.to_file(sortie, layer="mise_en_forme")
    statut(cx, "lots", lot["id"], "mis_en_forme")
    ok += 1
journal(cx, "INFO", "mise_en_forme", f"{ok}/{len(lots)} lots mis en forme (schéma normalisé, WGS84)")
