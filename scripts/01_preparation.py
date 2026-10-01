"""01. Préparation des données de référence et de l'AIS.

- referentiel.gpkg : zone d'étude, terre, mer territoriale (12 M), zone contiguë (24 M),
  ports, zones d'interdiction de pêche (datées), couverture du récepteur AIS.
- ais.sqlite : positions AIS filtrées (zone, MMSI de navire, coordonnées valides),
  ramenées à 1 position par navire et par minute, et table navires (identité, type, pavillon,
  complétée par le registre ANFR).
"""
import sqlite3
import time

import geopandas as gpd
import numpy as np
import pandas as pd
from shapely.geometry import box

import config
from commun import base_production, journal

B = config.BRUTES
Z = config.ZONE
cx = base_production()
t0 = time.time()

# --- Référentiel géographique -------------------------------------------------------------
zone = gpd.GeoDataFrame({"nom": ["Mer d'Iroise et approches de Brest"]},
                        geometry=[box(Z["lon_min"], Z["lat_min"], Z["lon_max"], Z["lat_max"])],
                        crs=config.CRS_GEO)
zone_m = zone.to_crs(config.CRS_METRE)
# emprise élargie de 30 milles pour que les zones de 24 milles soient justes en bord de zone
large_m = zone_m.buffer(30 * config.MILLE)

cote_src = B / "C2_European_Coastline" / "Europe Coastline (Polygone).shp"
emprise_3035 = large_m.to_crs("EPSG:3035").total_bounds
terre = gpd.read_file(cote_src, bbox=tuple(emprise_3035)).to_crs(config.CRS_METRE)
terre = gpd.clip(terre, large_m.iloc[0])
terre_u = terre.union_all()
terre_gdf = gpd.GeoDataFrame({"nom": ["Terre (côte AEE)"]}, geometry=[terre_u], crs=config.CRS_METRE)

# Approximation : distances mesurées depuis le trait de côte, pas depuis les lignes de base droites
b12 = terre_u.buffer(12 * config.MILLE)
b24 = terre_u.buffer(24 * config.MILLE)
cadre = zone_m.iloc[0].geometry
zones_mer = gpd.GeoDataFrame({
    "zone": ["Mer territoriale (0-12 M)", "Zone contiguë (12-24 M)", "Au-delà de 24 M"],
    "code": ["MT", "ZC", "ZEE"],
}, geometry=[b12.difference(terre_u).intersection(cadre),
             b24.difference(b12).intersection(cadre),
             cadre.difference(b24).difference(terre_u)], crs=config.CRS_METRE)

ports = gpd.read_file(B / "C1_Ports_of_Brittany" / "port.shp")[["por_id", "libelle_po", "insee_comm", "geometry"]]
ports = ports.rename(columns={"por_id": "id_port", "libelle_po": "nom", "insee_comm": "insee"})
ports = ports[ports.to_crs(config.CRS_METRE).intersects(large_m.iloc[0])]

peche = gpd.read_file(B / "C5_Fishing_Constraints" / "fishing_interdiction.shp")
# README : zone nord (Camaret) valable du 05/02/2015 au 21/01/2016, zone sud (Concarneau) du 03/09/2015 au 15/10/2015
nord = peche.to_crs(config.CRS_METRE).geometry.centroid.y.argmax()
peche["nom"] = ["Interdiction pêche coquillages, secteur Camaret" if i == nord
                else "Interdiction pêche coquillages, secteur Concarneau" for i in range(len(peche))]
peche["debut"] = ["2015-02-05" if i == nord else "2015-09-03" for i in range(len(peche))]
peche["fin"] = ["2016-01-21" if i == nord else "2015-10-15" for i in range(len(peche))]
peche = peche[["nom", "debut", "fin", "geometry"]]

couverture = gpd.read_file(B / "P1_Brest_Receiver" / "Theoretical Coverage.shp")[["geometry"]]
couverture["nom"] = "Couverture théorique du récepteur AIS de Brest"

ref = config.REFERENTIEL
if ref.exists():
    ref.unlink()
zone.to_file(ref, layer="zone_etude")
terre_gdf.to_crs(config.CRS_GEO).to_file(ref, layer="terre")
zones_mer.to_crs(config.CRS_GEO).to_file(ref, layer="zones_maritimes")
ports.to_file(ref, layer="ports")
peche.to_file(ref, layer="interdictions_peche")
couverture.to_file(ref, layer="couverture_recepteur")
journal(cx, "INFO", "preparation", f"référentiel écrit : {len(ports)} ports, {len(peche)} zones de pêche", "referentiel.gpkg")

# --- AIS dynamique ----------------------------------------------------------------------------
if config.AIS_DB.exists():
    config.AIS_DB.unlink()
ais = sqlite3.connect(config.AIS_DB)
ais.execute("""CREATE TABLE positions (mmsi INTEGER, t INTEGER, lon REAL, lat REAL,
               sog REAL, cog REAL, statut INTEGER)""")
n_lu = n_garde = 0
src = B / "P1_AIS_Data" / "nari_dynamic.csv"
for c in pd.read_csv(src, chunksize=2_000_000,
                     usecols=["sourcemmsi", "navigationalstatus", "speedoverground",
                              "courseoverground", "lon", "lat", "t"]):
    n_lu += len(c)
    c = c[(c.sourcemmsi.between(200_000_000, 799_999_999))          # navires uniquement
          & c.lon.between(Z["lon_min"], Z["lon_max"]) & c.lat.between(Z["lat_min"], Z["lat_max"])
          & (c.speedoverground.isna() | (c.speedoverground < 102.2))]
    c = c.assign(minute=c.t // 60).drop_duplicates(["sourcemmsi", "minute"])
    c = c.rename(columns={"sourcemmsi": "mmsi", "speedoverground": "sog",
                          "courseoverground": "cog", "navigationalstatus": "statut"})
    c[["mmsi", "t", "lon", "lat", "sog", "cog", "statut"]].to_sql("positions", ais, if_exists="append", index=False)
    n_garde += len(c)
ais.execute("CREATE INDEX idx_pos_t ON positions (t)")
ais.execute("CREATE INDEX idx_pos_mmsi_t ON positions (mmsi, t)")
ais.commit()
journal(cx, "INFO", "preparation", f"AIS : {n_lu:_} messages lus, {n_garde:_} positions gardées (1/min)".replace("_", " "), "ais.sqlite")

# --- Identité des navires ------------------------------------------------------------------
st = pd.read_csv(B / "P1_AIS_Data" / "nari_static.csv",
                 usecols=["sourcemmsi", "imonumber", "callsign", "shipname", "shiptype", "tobow", "tostern", "t"])
st = st[st.sourcemmsi.between(200_000_000, 799_999_999)]
for col in ("callsign", "shipname"):
    st[col] = st[col].astype(str).str.strip().replace({"nan": None, "": None})
st["longueur"] = (st.tobow.fillna(0) + st.tostern.fillna(0)).where(lambda s: s > 0)
st = st.sort_values("t")


def mode(s):
    s = s.dropna()
    return s.mode().iloc[0] if len(s) else None


nav = st.groupby("sourcemmsi").agg(nom=("shipname", mode), indicatif=("callsign", mode),
                                   imo=("imonumber", mode), code_type=("shiptype", mode),
                                   longueur=("longueur", "median")).reset_index().rename(columns={"sourcemmsi": "mmsi"})
nav.loc[nav.imo == 0, "imo"] = None

types = pd.read_csv(B / "P1_AIS_Status,_Codes_and_Types" / "Ship Types List.csv")


def type_resume(code):
    if pd.isna(code):
        return "Inconnu"
    r = types[(types.shiptype_min <= code) & (types.shiptype_max >= code)]
    return r.ais_type_summary.iloc[0] if len(r) else "Inconnu"


TRAD = {"Fishing": "Pêche", "Cargo": "Cargo", "Tanker": "Pétrolier/chimiquier", "Passenger": "Passagers",
        "Tug": "Remorqueur", "Sailing Vessel": "Voilier", "Pleasure Craft": "Plaisance",
        "Special Craft": "Navire de servitude", "Search and Rescue": "Sauvetage", "High-Speed Craft": "Grande vitesse",
        "Other": "Autre", "Unspecified": "Inconnu", "Wing in Grnd": "Autre", "Inconnu": "Inconnu"}
nav["type"] = nav.code_type.map(type_resume).map(TRAD)

mid = pd.read_csv(B / "P1_AIS_Status,_Codes_and_Types" / "MMSI Country Codes.csv", header=None, names=["mid", "pays"])
nav["mid"] = nav.mmsi // 1_000_000
nav = nav.merge(mid, on="mid", how="left").rename(columns={"pays": "pavillon"})

# Registre ANFR : type et longueur déclarés pour les navires français
anfr = pd.read_csv(B / "C6_ANFR_Vessel_List" / "anfr.csv", sep=";", dtype=str)
anfr = anfr[anfr.mmsi.notna() & anfr.mmsi.str.fullmatch(r"\d{9}")]
anfr = anfr.assign(mmsi=anfr.mmsi.astype(int)).drop_duplicates("mmsi", keep="last")
anfr = anfr[["mmsi", "shiptype", "length", "registration_number"]].rename(
    columns={"shiptype": "type_anfr", "length": "longueur_anfr", "registration_number": "immatriculation"})
nav = nav.merge(anfr, on="mmsi", how="left")
nav["longueur_anfr"] = pd.to_numeric(nav.longueur_anfr.str.replace(",", "."), errors="coerce")
# Type manquant dans l'AIS : on reprend celui de l'ANFR
TRAD_ANFR = {"FISHING": "Pêche", "PLEASURE CRAFT": "Plaisance", "SAILING": "Voilier"}
manque = nav.type.eq("Inconnu") & nav.type_anfr.isin(TRAD_ANFR)
nav.loc[manque, "type"] = nav.loc[manque, "type_anfr"].map(TRAD_ANFR)
nav["longueur"] = nav.longueur.fillna(nav.longueur_anfr)

# Navires présents dans les positions sans message statique
presents = pd.read_sql("SELECT DISTINCT mmsi FROM positions", ais)
nav = presents.merge(nav, on="mmsi", how="left")
nav["type"] = nav["type"].fillna("Inconnu")
nav.drop(columns=["mid"]).to_sql("navires", ais, if_exists="replace", index=False)
ais.execute("CREATE UNIQUE INDEX idx_nav ON navires (mmsi)")
ais.commit()

rep = nav.type.value_counts()
journal(cx, "INFO", "preparation",
        f"{len(nav)} navires identifiés ({int(nav.immatriculation.notna().sum())} rapprochés du registre ANFR) ; "
        + ", ".join(f"{k} {v}" for k, v in rep.head(6).items()), "ais.sqlite")
journal(cx, "INFO", "preparation", f"terminé en {time.time() - t0:.0f} s")
