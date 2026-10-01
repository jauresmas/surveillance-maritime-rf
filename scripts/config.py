"""Paramètres communs de la chaîne de production (chemins, zone, projections, capteur simulé)."""
import os
from pathlib import Path

# Les variables PROJ de PostgreSQL pointent vers un proj.db trop ancien : on les retire.
for v in ("PROJ_LIB", "PROJ_DATA"):
    if v in os.environ and "PostgreSQL" in os.environ[v]:
        del os.environ[v]

RACINE = Path(__file__).resolve().parents[1]
BRUTES = RACINE / "donnees" / "brutes"
PRODUITES = RACINE / "donnees" / "produites"
SORTIES = RACINE / "sorties"
LOTS_BRUTS = PRODUITES / "lots_bruts"          # ce que « livre » le segment sol
LOTS_FORMES = PRODUITES / "lots_formes"        # lots mis en forme (GeoPackage par lot)
RAPPORTS_QC = SORTIES / "rapports_qc"
LIVRAISONS = SORTIES / "livraisons"
for d in (PRODUITES, SORTIES, LOTS_BRUTS, LOTS_FORMES, RAPPORTS_QC, LIVRAISONS):
    d.mkdir(parents=True, exist_ok=True)

AIS_DB = PRODUITES / "ais.sqlite"               # AIS nettoyé, 1 position par navire et par minute
REFERENTIEL = PRODUITES / "referentiel.gpkg"    # côte, zones maritimes, ports, contraintes pêche
PRODUCTION_DB = PRODUITES / "production.sqlite" # suivi : demandes, passages, lots, journal
VERITE = PRODUITES / "verite_terrain.sqlite"    # vérité de la simulation (jamais livrée au client)

# Zone d'étude : mer d'Iroise et approches de Brest (97 % des messages AIS du jeu)
ZONE = dict(lon_min=-5.6, lon_max=-4.2, lat_min=47.8, lat_max=48.8)
CRS_GEO = "EPSG:4326"
CRS_METRE = "EPSG:32630"  # UTM 30N, adapté au calcul de distances en mer d'Iroise

MILLE = 1852.0  # mètres

# Capteur simulé (constellation fictive, aucun lien avec un opérateur réel)
SATELLITES = [f"SIM-{i:02d}" for i in range(1, 13)]
PASSAGES_PAR_JOUR = (4, 8)          # nombre de passages utiles sur la zone, tiré par jour
DUREE_ACQUISITION_S = 480           # fenêtre d'écoute d'un passage
PROBA_ECHEC_ACQUISITION = 0.04      # anomalie segment sol
GRAINE = 2015                       # reproductibilité de la simulation

SOURCE_AIS = ("Ray C., Dréo R., Camossi E., Jousselme A.-L., Iphar C. (2018). Heterogeneous "
              "Integrated Dataset for Maritime Intelligence, Surveillance, and Reconnaissance. "
              "Zenodo, doi:10.5281/zenodo.1167595, licence CC BY-NC-SA 4.0")
MENTION = "DÉMONSTRATEUR DE COMPÉTENCES, NON OFFICIEL : détections RF simulées à partir d'AIS réel"
