"""09 (optionnel). Publication des tables de production et des détections dans PostGIS.

La connexion se règle par les variables d'environnement standard de PostgreSQL
(PGHOST, PGPORT, PGDATABASE, PGUSER, PGPASSWORD) ou un fichier pgpass : aucun identifiant
n'est écrit dans le code. Le schéma cible est créé par sql/schema_postgis.sql.
"""
import os
import sqlite3
from pathlib import Path

import geopandas as gpd
import pandas as pd
import psycopg2
from psycopg2.extras import execute_values

import config

SCHEMA = "production_rf"
cx = psycopg2.connect(dbname=os.environ.get("PGDATABASE", "postgres"))
cx.autocommit = True
cur = cx.cursor()
cur.execute((Path(__file__).parent / "sql" / "schema_postgis.sql").read_text(encoding="utf-8"))

src = sqlite3.connect(config.PRODUCTION_DB)
for table in ("clients", "demandes", "passages", "programmations", "lots", "livraisons", "tickets",
              "journal", "historique_statuts"):
    df = pd.read_sql(f"SELECT * FROM {table}", src)
    cols = list(df.columns)
    cur.execute(f"TRUNCATE {SCHEMA}.{table} CASCADE")
    if len(df):
        execute_values(cur, f"INSERT INTO {SCHEMA}.{table} ({', '.join(cols)}) VALUES %s",
                       [tuple(None if pd.isna(v) else v for v in r) for r in df.itertuples(index=False)])
    print(f"{table:<20} {len(df):>6} lignes")

det = gpd.read_file(config.PRODUITES / "detections_correlees.gpkg", layer="detections")
det = det.rename_geometry("geom")
cur.execute(f"TRUNCATE {SCHEMA}.detections")
execute_values(cur, f"""INSERT INTO {SCHEMA}.detections (id_detection, lot_id, demande_id, horodatage, correlation,
                        mmsi, nom_navire, type_navire, zone_maritime, interdiction_peche, interet, demi_grand_axe_m,
                        demi_petit_axe_m, orientation_deg, geom) VALUES %s""",
               [(r.id_detection, r.lot_id, r.demande_id, r.horodatage, r.correlation,
                 None if pd.isna(r.mmsi) else int(r.mmsi), r.ais_nom, r.ais_type, r.zone_maritime,
                 r.interdiction_peche, r.interet, r.demi_grand_axe_m, r.demi_petit_axe_m, r.orientation_deg,
                 r.geom.wkt) for r in det.itertuples()],
               template="(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s, ST_GeomFromText(%s, 4326))")
print(f"{'detections':<20} {len(det):>6} lignes")
cur.execute(f"SELECT {SCHEMA}.rafraichir()")
print("Vue matérialisée de synthèse rafraîchie.")
