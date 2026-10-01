"""08. Supervision de la production : support client, indicateurs, tableau de bord.

- tickets : questions clients reçues après livraison, réponse de premier niveau ou escalade ;
- indicateurs : délais, taux d'échec, qualité, volumes ;
- sorties/tableau_de_bord.html : page autonome (Leaflet) pour le suivi quotidien et l'astreinte.
"""
import json
import sqlite3
from datetime import datetime

import geopandas as gpd
import pandas as pd

import config
from commun import base_production, journal

cx = base_production()
cx.execute("DELETE FROM tickets")
cx.execute("DELETE FROM journal WHERE etape = 'support'")
cx.commit()

# --- Support de premier niveau ----------------------------------------------------------------
TICKETS = [
    ("T001", "D001", "2015-11-16T14:20:00", "Format",
     "Le fichier KML ne montre pas les ellipses d'incertitude dans Google Earth.",
     "Le KML ne porte que les centres. Les ellipses à 95 % sont dans le GeoPackage (couche ellipses_95), "
     "lisible dans QGIS ou ArcGIS Pro.", None, "resolu"),
    ("T002", "D001", "2015-11-17T09:05:00", "Interprétation",
     "Pourquoi certaines détections tombent-elles à terre près de Brest ?",
     "Le point livré est le centre de l'ellipse d'incertitude. Près des quais, le centre peut tomber à terre alors "
     "que le navire est à flot : ces détections portent la mention reserve_position_a_terre (voir bordereau).", None, "resolu"),
    ("T003", "D002", "2015-11-21T16:40:00", "Intégrité",
     "Le zip reçu semble corrompu, impossible d'ouvrir le GeoPackage.",
     "Empreinte SHA-256 de l'archive contrôlée côté production : identique à celle du bordereau. Téléchargement "
     "incomplet côté client ; nouveau lien envoyé, vérification avec SHA256SUMS.txt.", None, "resolu"),
    ("T004", "D005", "2016-01-20T11:15:00", "Donnée manquante",
     "Il manque l'acquisition du 8 janvier au matin.",
     "Acquisition non réalisée (anomalie segment sol, pas de données reçues) : elle figure dans la rubrique "
     "« Acquisitions non réalisées » du bordereau. Geste commercial à étudier.", "Commerce", "escalade"),
    ("T005", "D003", "2016-03-01T15:30:00", "Interprétation",
     "Pouvez-vous identifier le navire sans AIS détecté le 10/02 vers 14 h au large de Morgat ?",
     "Hors périmètre du premier niveau : l'identification d'un émetteur sans AIS relève de l'analyse. Ticket "
     "transmis avec la référence de la détection.", "Analyse", "escalade"),
    ("T006", "D003", "2016-03-02T08:50:00", "Format",
     "Nous travaillons en Lambert 93, pouvez-vous fournir les données dans cette projection ?",
     "Les livrables sont en WGS84 (EPSG:4326), standard du produit. Reprojection en EPSG:2154 dans QGIS : "
     "clic droit sur la couche, Exporter, Sauvegarder les entités sous, SCR EPSG:2154.", None, "resolu"),
]
cx.executemany("INSERT INTO tickets VALUES (?,?,?,?,?,?,?,?)",
               [(t[0], t[1], t[2], t[4], t[3], t[5], t[6], t[7]) for t in TICKETS])
for t in TICKETS:
    journal(cx, "ALERTE" if t[7] == "escalade" else "INFO", "support",
            f"ticket {t[0]} ({t[3]}) : {'escaladé vers ' + t[6] if t[6] else 'résolu au premier niveau'}", t[1], t[2])
cx.commit()

# --- Indicateurs ------------------------------------------------------------------------------
q = lambda sql: pd.read_sql(sql, cx)
lots = q("""SELECT l.*, p.demande_id, pa.fin_utc FROM lots l JOIN programmations p ON p.id = l.programmation_id
            JOIN passages pa ON pa.id = p.passage_id""")
progs = q("SELECT * FROM programmations")
delai = (pd.to_datetime(lots.date_reception) - pd.to_datetime(lots.fin_utc)).dt.total_seconds() / 60
liv = q("SELECT l.*, d.date_fin FROM livraisons l JOIN demandes d ON d.id = l.demande_id")
delai_liv = (pd.to_datetime(liv.date_livraison) - pd.to_datetime(liv.date_fin.str[:19])).dt.total_seconds() / 3600
det = gpd.read_file(config.PRODUITES / "detections_correlees.gpkg", layer="detections")
evaluation = (config.SORTIES / "evaluation_correlation.txt").read_text(encoding="utf-8")
ind = {
    "demandes": int(q("SELECT COUNT(*) n FROM demandes").n[0]),
    "refusees": int(q("SELECT COUNT(*) n FROM demandes WHERE statut='refusee'").n[0]),
    "programmees": len(progs),
    "echecs": int((progs.statut == "echec_acquisition").sum()),
    "lots": len(lots),
    "lots_ko": int(q("SELECT COUNT(DISTINCT objet_id) n FROM historique_statuts WHERE nouveau='qc_ko'").n[0]),
    "reserves": int((lots.verdict_qc == "qc_reserve").sum()),
    "delai_reception_min": round(float(delai.median())),
    "delai_livraison_h": round(float(delai_liv.mean()), 1),
    "detections": len(det),
    "sombres": int(det.mmsi.isna().sum()),
    "haute": int((det.interet == "haute").sum()),
    "livraisons": len(liv),
    "tickets": len(TICKETS),
    "escalades": sum(1 for t in TICKETS if t[7] == "escalade"),
}

# --- Données de la page -----------------------------------------------------------------------
ref = config.REFERENTIEL
geo = lambda g: json.loads(g.to_json(drop_id=True))
det_web = det[["id_detection", "demande_id", "horodatage", "correlation", "ais_nom", "ais_type", "ais_pavillon",
               "zone_maritime", "interdiction_peche", "interet", "qc", "satellite", "geometry"]].copy()
det_web["geometry"] = det_web.geometry.set_precision(1e-5)
dem = q("SELECT d.id, d.intitule, d.statut, d.zone_wkt, c.nom client, d.date_debut, d.date_fin, d.cibles, d.priorite, d.motif "
        "FROM demandes d JOIN clients c ON c.id = d.client_id")
dem_g = gpd.GeoDataFrame(dem.drop(columns="zone_wkt"), geometry=gpd.GeoSeries.from_wkt(dem.zone_wkt), crs=config.CRS_GEO)
avancement = q("SELECT * FROM v_avancement").fillna(0).to_dict("records")
jrn = q("""SELECT date_production, niveau, etape, objet, message FROM journal
           WHERE niveau <> 'INFO' OR etape IN ('livraison','demande') ORDER BY date_production""").fillna("").to_dict("records")
par_jour = lots.assign(jour=lots.date_reception.str[:10]).groupby(["jour", "verdict_qc"]).size().unstack(fill_value=0)
par_jour = par_jour.reindex(columns=["qc_ok", "qc_reserve"], fill_value=0).reset_index().to_dict("records")
tickets = q("SELECT * FROM tickets ORDER BY date_ouverture").fillna("").to_dict("records")
donnees = {
    "detections": geo(det_web),
    "demandes": geo(dem_g),
    "interdictions": geo(gpd.read_file(ref, layer="interdictions_peche")),
    "couverture": geo(gpd.read_file(ref, layer="couverture_recepteur")),
    "avancement": avancement, "journal": jrn, "par_jour": par_jour, "tickets": tickets, "ind": ind,
    "evaluation": evaluation, "mention": config.MENTION, "source": config.SOURCE_AIS,
    "genere": datetime.now().strftime("%d/%m/%Y %H:%M"),
}
gabarit = (config.RACINE / "scripts" / "gabarit_tableau_de_bord.html").read_text(encoding="utf-8")
page = gabarit.replace("/*__DONNEES__*/null", json.dumps(donnees, ensure_ascii=False, default=str))
(config.SORTIES / "tableau_de_bord.html").write_text(page, encoding="utf-8")
journal(cx, "INFO", "supervision", f"tableau de bord généré : {ind}")
