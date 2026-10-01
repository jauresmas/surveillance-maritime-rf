"""06. Croisement des détections RF avec l'AIS et le référentiel géographique.

Corrélation : pour chaque détection, positions AIS interpolées à l'instant de la détection,
distance de Mahalanobis tenant compte de l'ellipse d'incertitude, porte à 99 % (chi2 2 ddl = 9,21),
affectation globale par algorithme hongrois (un navire ne peut expliquer qu'une détection),
cohérence de signature (bande S réservée aux grands navires de commerce).
Une détection sans correspondance est un « navire sombre potentiel ».

Enrichissement : zone maritime, interdiction de pêche en vigueur, distance à la côte, port le
plus proche, couverture du récepteur AIS. Évaluation interne contre la vérité de simulation.
"""
import math
import sqlite3
from datetime import datetime, timedelta

import geopandas as gpd
import numpy as np
import pandas as pd
from scipy.optimize import linear_sum_assignment

import config
from commun import base_production, journal, statut
from simulateur import K95

PORTE = 9.21
# Cohérence de signature : un radar en bande S équipe les grands navires de commerce
GRANDS = {"Cargo", "Pétrolier/chimiquier", "Passagers", "Inconnu"}
cx = base_production()
cx.execute("DELETE FROM journal WHERE etape = 'croisement'")
cx.commit()
ais = sqlite3.connect(config.AIS_DB)
navires = pd.read_sql("SELECT mmsi, nom, type, pavillon, longueur, indicatif, imo FROM navires", ais).set_index("mmsi")
coupures = pd.read_sql("SELECT * FROM coupures_ais", ais)

R = config.REFERENTIEL
zones_mer = gpd.read_file(R, layer="zones_maritimes").to_crs(config.CRS_METRE)
peche = gpd.read_file(R, layer="interdictions_peche").to_crs(config.CRS_METRE)
ports = gpd.read_file(R, layer="ports").to_crs(config.CRS_METRE)
terre = gpd.read_file(R, layer="terre").to_crs(config.CRS_METRE).geometry.iloc[0]
cote = terre.boundary
couverture = gpd.read_file(R, layer="couverture_recepteur").to_crs(config.CRS_METRE).geometry.iloc[0]
to_m = lambda lon, lat: gpd.GeoSeries(gpd.points_from_xy(lon, lat), crs=config.CRS_GEO).to_crs(config.CRS_METRE)


def flux_ais(t0, t1, bbox):
    """Flux AIS tel que reçu : les navires en coupure n'émettent pas."""
    xmin, ymin, xmax, ymax = bbox
    df = pd.read_sql("""SELECT mmsi, t, lon, lat, sog FROM positions WHERE t BETWEEN ? AND ?
                        AND lon BETWEEN ? AND ? AND lat BETWEEN ? AND ?""", ais,
                     params=(t0, t1, xmin - 0.1, xmax + 0.1, ymin - 0.1, ymax + 0.1))
    c = coupures[(coupures.t_fin >= t0) & (coupures.t_debut <= t1)]
    for r in c.itertuples():
        df = df[~((df.mmsi == r.mmsi) & df.t.between(r.t_debut, r.t_fin))]
    return df


def interpoler(df, t):
    """Position de chaque navire à l'instant t (même règle que le segment sol : 15 min max)."""
    res = []
    for mmsi, g in df.groupby("mmsi"):
        av, ap = g[g.t <= t], g[g.t > t]
        if len(av) and len(ap):
            a, b = av.iloc[-1], ap.iloc[0]
            k = (t - a.t) / (b.t - a.t)
            res.append((mmsi, a.lon + k * (b.lon - a.lon), a.lat + k * (b.lat - a.lat), min(t - a.t, b.t - t)))
        else:
            r = av.iloc[-1] if len(av) else ap.iloc[0]
            if abs(r.t - t) <= 300:
                res.append((mmsi, r.lon, r.lat, abs(r.t - t)))
    return pd.DataFrame(res, columns=["mmsi", "lon", "lat", "ecart_s"])


def mahalanobis2(dx, dy, a, b, orientation):
    th = math.radians(orientation)
    ua, ub = (math.sin(th), math.cos(th)), (math.cos(th), -math.sin(th))   # axes (est, nord)
    pa, pb = dx * ua[0] + dy * ua[1], dx * ub[0] + dy * ub[1]
    return (pa / (a / K95)) ** 2 + (pb / (b / K95)) ** 2


lots = cx.execute("""SELECT l.id, l.version, p.demande_id, pa.debut_utc, pa.fin_utc, d.zone_wkt
                     FROM lots l JOIN programmations p ON p.id = l.programmation_id
                     JOIN passages pa ON pa.id = p.passage_id JOIN demandes d ON d.id = p.demande_id
                     WHERE l.verdict_qc IN ('qc_ok','qc_reserve') ORDER BY pa.debut_utc""").fetchall()
toutes, ais_non_detectes = [], []
for lot in lots:
    det = gpd.read_file(config.LOTS_FORMES / f"{lot['id']}_v{lot['version']}.gpkg", layer="detections")
    statut(cx, "lots", lot["id"], "correle")
    if det.empty:
        continue
    det["t"] = pd.to_datetime(det.horodatage, utc=True).astype("int64") // 10**9
    t0, t1 = int(det.t.min()) - 900, int(det.t.max()) + 900
    flux = flux_ais(t0, t1, det.total_bounds)
    dm = to_m(det.lon, det.lat)
    # matrice de coûts : détections x navires AIS
    cand = sorted(flux.mmsi.unique())
    cout = np.full((len(det), max(1, len(cand))), 1e6)
    pos_cache = {}
    for i, d in enumerate(det.itertuples()):
        pos = interpoler(flux, d.t)
        if pos.empty:
            continue
        pm = to_m(pos.lon, pos.lat)
        for (mmsi, x, y) in zip(pos.mmsi, pm.x, pm.y):
            if d.bande == "S" and navires.type.get(mmsi, "Inconnu") not in GRANDS:
                continue
            d2 = mahalanobis2(x - dm.iloc[i].x, y - dm.iloc[i].y, d.demi_grand_axe_m, d.demi_petit_axe_m, d.orientation_deg)
            if d2 <= PORTE:
                cout[i, cand.index(mmsi)] = d2
                pos_cache[(i, mmsi)] = math.hypot(x - dm.iloc[i].x, y - dm.iloc[i].y)
    lignes, colonnes = linear_sum_assignment(cout)
    det["mmsi"] = pd.array([pd.NA] * len(det), dtype="Int64")
    det["mahalanobis2"] = np.nan
    det["ecart_ais_m"] = np.nan
    for i, j in zip(lignes, colonnes):
        if cout[i, j] < 1e6:
            det.loc[det.index[i], ["mahalanobis2", "ecart_ais_m"]] = [round(cout[i, j], 2), round(pos_cache[(i, cand[j])])]
            det.loc[det.index[i], "mmsi"] = cand[j]
    # navires émettant de l'AIS dans la zone mais sans détection RF associée
    t_mid = int(det.t.median())
    pos = interpoler(flux, t_mid)
    zone = gpd.GeoSeries.from_wkt([lot["zone_wkt"]], crs=config.CRS_GEO).iloc[0]
    pos = pos[gpd.points_from_xy(pos.lon, pos.lat).within(zone)]
    pos = pos[~pos.mmsi.isin(det.mmsi.dropna())]
    ais_non_detectes.append(pos.assign(lot_id=lot["id"], demande_id=lot["demande_id"],
                                       horodatage=datetime.utcfromtimestamp(t_mid).strftime("%Y-%m-%dT%H:%M:%SZ")))
    toutes.append(det)

det = pd.concat(toutes, ignore_index=True)
det = gpd.GeoDataFrame(det, geometry="geometry", crs=config.CRS_GEO)
det["correlation"] = np.where(det.mmsi.notna(), "corrélée AIS", "non corrélée : navire sombre potentiel")
ident = navires.reindex(det.mmsi.astype("float").fillna(-1).astype(int))
for c in ("nom", "type", "pavillon", "longueur", "indicatif", "imo"):
    det[f"ais_{c}"] = ident[c].values

# --- Enrichissement géographique --------------------------------------------------------------
pm = det.to_crs(config.CRS_METRE)
j = gpd.sjoin(pm[["geometry"]], zones_mer[["zone", "geometry"]], how="left", predicate="within")
det["zone_maritime"] = j.groupby(level=0).zone.first().reindex(det.index).fillna("À terre ou hors zone").values
jour = det.horodatage.str[:10]
det["interdiction_peche"] = None
for z in peche.itertuples():
    dedans = pm.within(z.geometry) & (jour >= z.debut) & (jour <= z.fin)
    det.loc[dedans, "interdiction_peche"] = z.nom
det["distance_cote_m"] = pm.distance(cote).round().astype(int)
det.loc[pm.within(terre), "distance_cote_m"] *= -1          # négatif : centre de l'ellipse à terre
proche = gpd.sjoin_nearest(pm[["geometry"]], ports[["nom", "geometry"]], how="left", distance_col="d")
proche = proche[~proche.index.duplicated()]
det["port_proche"] = proche.nom.values
det["distance_port_km"] = (proche.d / 1000).round(1).values
det["couverture_ais_terrestre"] = pm.within(couverture).values


def interet(r):
    if r.mmsi is not pd.NA and pd.notna(r.mmsi):
        return "faible"
    if r.interdiction_peche:
        return "haute"
    if r.zone_maritime.startswith("Mer territoriale") and r.distance_cote_m > 1000:
        return "moyenne"
    return "à vérifier"


det["interet"] = det.apply(interet, axis=1)
det = det.drop(columns=["t"])
sortie = config.PRODUITES / "detections_correlees.gpkg"
if sortie.exists():
    sortie.unlink()
det.to_file(sortie, layer="detections")
ell = []
for lot in lots:
    f = config.LOTS_FORMES / f"{lot['id']}_v{lot['version']}.gpkg"
    e = gpd.read_file(f, layer="ellipses_95")
    if len(e):
        ell.append(e)
ell = pd.concat(ell, ignore_index=True).merge(det[["id_detection", "correlation", "mmsi", "interet"]], on="id_detection")
gpd.GeoDataFrame(ell, geometry="geometry", crs=config.CRS_GEO).to_file(sortie, layer="ellipses_95")
nd = pd.concat(ais_non_detectes, ignore_index=True).merge(navires, left_on="mmsi", right_index=True, how="left")
gpd.GeoDataFrame(nd, geometry=gpd.points_from_xy(nd.lon, nd.lat), crs=config.CRS_GEO).to_file(sortie, layer="ais_sans_detection_rf")

n_s = int(det.mmsi.isna().sum())
journal(cx, "INFO", "croisement", f"{len(det)} détections : {len(det) - n_s} corrélées AIS, {n_s} navires sombres potentiels "
        f"(dont {int((det.interet == 'haute').sum())} en zone d'interdiction de pêche)")

# --- Évaluation interne contre la vérité (jamais livrée) -------------------------------------
ver = pd.read_sql("SELECT det_id AS id_detection, mmsi AS mmsi_vrai, categorie FROM verite", sqlite3.connect(config.VERITE))
ev = det[["id_detection", "mmsi", "demande_id", "distance_cote_m"]].merge(ver, on="id_detection", how="left")
ev["predit"] = np.where(ev.mmsi.notna(), "corrélée", "non corrélée")
ev["categorie"] = ev.categorie.fillna("fausse alarme")
mat = pd.crosstab(ev.categorie, ev.predit)
corr = ev[(ev.predit == "corrélée") & (ev.categorie == "ais_actif")]
juste = corr.mmsi.astype(float) == corr.mmsi_vrai.astype(float)
bonne_ident = juste.mean()
milieu = pd.cut(corr.distance_cote_m, [-1e9, 2000, 1e9], labels=["à moins de 2 km de la côte", "au large (plus de 2 km)"])
par_milieu = juste.groupby(milieu, observed=False).agg(["mean", "size"])
sombres_vrais = ev.categorie.isin(["sombre", "sans_ais"])
rappel = (sombres_vrais & (ev.predit == "non corrélée")).sum() / max(1, sombres_vrais.sum())
precision = (sombres_vrais & (ev.predit == "non corrélée")).sum() / max(1, (ev.predit == "non corrélée").sum())
mat.to_csv(config.SORTIES / "evaluation_correlation.csv", sep=";", encoding="utf-8-sig")
with open(config.SORTIES / "evaluation_correlation.txt", "w", encoding="utf-8") as f:
    f.write("Évaluation interne de la corrélation (vérité de simulation)\n\n")
    f.write(mat.to_string() + "\n\n")
    f.write(f"Bonne identification du navire (MMSI exact) parmi les corrélées : {bonne_ident:.1%}\n")
    for m, r in par_milieu.iterrows():
        f.write(f"  - {m} : {r['mean']:.1%} sur {int(r['size'])} détections\n")
    f.write(f"Navires sans AIS (sombres + sans AIS) retrouvés en non corrélé : rappel {rappel:.1%}, précision {precision:.1%}\n")
journal(cx, "INFO", "croisement", f"évaluation : MMSI exact {bonne_ident:.1%}, rappel navires sombres {rappel:.1%}, "
        f"précision {precision:.1%}")
print(mat)
print(par_milieu)
