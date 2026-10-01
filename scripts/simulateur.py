"""Simulateur du segment sol : transforme l'AIS réel en détections RF brutes.

Le monde simulé :
- chaque navire présent à l'instant du passage émet (radar, VHF) avec une probabilité qui
  dépend de son type et de son activité (au mouillage ou à quai, les radars sont souvent éteints) ;
- certains navires coupent leur AIS autour du passage (« navires sombres ») : plus souvent les
  pêcheurs, et beaucoup plus souvent dans une zone d'interdiction de pêche en vigueur ;
- quelques émetteurs n'ont pas d'AIS du tout (petites unités) ;
- la géolocalisation RF est entachée d'une erreur elliptique (ellipse à 95 %).

Le fichier brut reproduit un format de sortie capteur (séparateur ;, temps en ms, colonnes
capteur) avec des défauts réalistes que la mise en forme et le contrôle qualité doivent traiter.
La vérité (quel navire derrière quelle détection) est écrite à part et n'est jamais livrée.
"""
import math
import sqlite3
from datetime import datetime, timedelta

import geopandas as gpd
import numpy as np
import pandas as pd
from shapely import wkt
from shapely.geometry import Point

import config

K95 = math.sqrt(5.991)  # facteur entre écart-type et demi-axe de l'ellipse à 95 % (loi du chi2 à 2 ddl)

P_EMISSION = {"Cargo": 0.95, "Pétrolier/chimiquier": 0.95, "Passagers": 0.95, "Pêche": 0.85,
              "Remorqueur": 0.8, "Navire de servitude": 0.8, "Sauvetage": 0.8, "Grande vitesse": 0.9,
              "Voilier": 0.35, "Plaisance": 0.4, "Autre": 0.6, "Inconnu": 0.6}
BANDES = {"Cargo": ["X", "S"], "Pétrolier/chimiquier": ["X", "S"], "Passagers": ["X", "S"],
          "Pêche": ["X", "VHF"], "Voilier": ["X"], "Plaisance": ["X", "VHF"]}
FREQ = {"X": (9380, 9440), "S": (3020, 3080), "VHF": (156.0, 162.0)}

_ref = {}


def referentiel():
    if not _ref:
        _ref["terre"] = gpd.read_file(config.REFERENTIEL, layer="terre").to_crs(config.CRS_METRE).geometry.iloc[0]
        _ref["peche"] = gpd.read_file(config.REFERENTIEL, layer="interdictions_peche").to_crs(config.CRS_METRE)
    return _ref


def positions_a(ais, t, zone, fenetre=900):
    """Position de chaque navire à l'instant t (interpolation linéaire entre les messages encadrants)."""
    xmin, ymin, xmax, ymax = zone.buffer(0.05).bounds
    df = pd.read_sql("""SELECT p.mmsi, p.t, p.lon, p.lat, p.sog, p.cog FROM positions p
                        WHERE p.t BETWEEN ? AND ? AND p.lon BETWEEN ? AND ? AND p.lat BETWEEN ? AND ?""",
                     ais, params=(t - fenetre, t + fenetre, xmin, xmax, ymin, ymax))
    lignes = []
    for mmsi, g in df.groupby("mmsi"):
        av, ap = g[g.t <= t], g[g.t > t]
        if len(av) and len(ap):
            a, b = av.iloc[-1], ap.iloc[0]
            k = (t - a.t) / (b.t - a.t)
            lon, lat = a.lon + k * (b.lon - a.lon), a.lat + k * (b.lat - a.lat)
            sog = np.nanmean([a.sog, b.sog])
        else:
            r = (av.iloc[-1] if len(av) else ap.iloc[0])
            if abs(r.t - t) > 300:
                continue
            lon, lat, sog = r.lon, r.lat, r.sog
        lignes.append((mmsi, lon, lat, sog))
    pos = pd.DataFrame(lignes, columns=["mmsi", "lon", "lat", "sog"])
    pos = pos[[zone.contains(Point(x, y)) for x, y in zip(pos.lon, pos.lat)]] if len(pos) else pos
    pos.attrs["flux"] = df
    return pos


def position_navire(flux, mmsi, t):
    """Position d'un navire à l'instant t, interpolée dans le flux AIS (repli : message le plus proche)."""
    g = flux[flux.mmsi == mmsi]
    av, ap = g[g.t <= t], g[g.t > t]
    if len(av) and len(ap):
        a, b = av.iloc[-1], ap.iloc[0]
        k = (t - a.t) / (b.t - a.t)
        return a.lon + k * (b.lon - a.lon), a.lat + k * (b.lat - a.lat)
    r = av.iloc[-1] if len(av) else ap.iloc[0]
    return r.lon, r.lat


def simuler_lot(passage, demande, rng, defaut=None, version=1):
    """Produit le fichier brut d'une acquisition et la vérité associée. Retourne (chemin, vérité, t)."""
    ref = referentiel()
    ais = sqlite3.connect(config.AIS_DB)
    debut = datetime.fromisoformat(passage["debut_utc"].replace("Z", "+00:00"))
    t = int((debut + timedelta(seconds=config.DUREE_ACQUISITION_S / 2)).timestamp())
    zone = wkt.loads(demande["zone_wkt"])
    pos = positions_a(ais, t, zone)
    navires = pd.read_sql("SELECT mmsi, type, longueur FROM navires", ais)
    flux = pos.attrs["flux"]
    pos = pos.merge(navires, on="mmsi", how="left").fillna({"type": "Inconnu"})
    pos.attrs["flux"] = flux

    # zones d'interdiction de pêche en vigueur à la date du passage
    jour = debut.strftime("%Y-%m-%d")
    peche = ref["peche"][(ref["peche"].debut <= jour) & (ref["peche"].fin >= jour)]
    pts = gpd.GeoSeries(gpd.points_from_xy(pos.lon, pos.lat), crs=config.CRS_GEO).to_crs(config.CRS_METRE)
    dans_interdiction = pts.apply(lambda p: bool(len(peche)) and peche.contains(p).any()).values if len(pos) else []

    verite, emissions = [], []
    for i, r in enumerate(pos.itertuples()):
        p = P_EMISSION.get(r.type, 0.6) * (1.0 if (r.sog or 0) > 1 else 0.35)
        if rng.random() > p:
            continue
        p_sombre = 0.04
        if r.type == "Pêche":
            p_sombre = 0.8 if dans_interdiction[i] else 0.2
        sombre = rng.random() < p_sombre
        # chaque émission est captée à un instant propre de la fenêtre d'écoute
        t_det = t + int(rng.uniform(-config.DUREE_ACQUISITION_S / 2, config.DUREE_ACQUISITION_S / 2))
        lon, lat = position_navire(pos.attrs["flux"], r.mmsi, t_det)
        emissions.append((r.mmsi, lon, lat, r.type, t_det))
        verite.append({"mmsi": r.mmsi, "categorie": "sombre" if sombre else "ais_actif",
                       "dans_interdiction": bool(dans_interdiction[i])})

    # émetteurs sans AIS : petites unités tirées au hasard en mer
    xmin, ymin, xmax, ymax = zone.bounds
    for _ in range(rng.poisson(0.6)):
        for _essai in range(50):
            x, y = rng.uniform(xmin, xmax), rng.uniform(ymin, ymax)
            pm = gpd.GeoSeries([Point(x, y)], crs=config.CRS_GEO).to_crs(config.CRS_METRE).iloc[0]
            if zone.contains(Point(x, y)) and not ref["terre"].contains(pm):
                break
        t_det = t + int(rng.uniform(-config.DUREE_ACQUISITION_S / 2, config.DUREE_ACQUISITION_S / 2))
        emissions.append((None, x, y, "Pêche", t_det))
        verite.append({"mmsi": None, "categorie": "sans_ais", "dans_interdiction": False})

    # erreur de géolocalisation elliptique
    lignes = []
    for k, (mmsi, lon, lat, typ, t_det) in enumerate(emissions):
        a = rng.uniform(400, 3000)
        b = a * rng.uniform(0.3, 0.9)
        theta = rng.uniform(0, 180)
        u, v = rng.normal(0, a / K95), rng.normal(0, b / K95)
        th = math.radians(theta)  # orientation du grand axe, en degrés depuis le nord, sens horaire
        de, dn = u * math.sin(th) + v * math.cos(th), u * math.cos(th) - v * math.sin(th)
        lat_m = lat + dn / 111_320
        lon_m = lon + de / (111_320 * math.cos(math.radians(lat)))
        bande = rng.choice(BANDES.get(typ, ["X"]))
        f0, f1 = FREQ[bande]
        det_id = f"{passage['satellite']}-{t}-{k:04d}"
        lignes.append([det_id, t_det * 1000, round(lat_m, 6), round(lon_m, 6), round(a), round(b), round(theta, 1),
                       round(rng.uniform(f0, f1), 2), bande, round(rng.uniform(0.1, 1.2), 2),
                       round(rng.uniform(8, 25), 1)])
        verite[k].update(det_id=det_id, lon_vrai=lon, lat_vrai=lat)
    brut = pd.DataFrame(lignes, columns=["DET_ID", "EPOCH_MS", "LAT", "LON", "SMA_M", "SMI_M", "ORIENT_DEG",
                                         "FREQ_MHZ", "BAND", "PULSE_US", "SNR_DB"])

    # défauts ordinaires d'un flux capteur
    n = len(brut)
    if n:
        idx = rng.choice(n, size=max(1, n // 60), replace=False)
        brut = pd.concat([brut, brut.iloc[idx]])                                  # doublons
        brut.loc[brut.sample(frac=0.01, random_state=rng.integers(1e9)).index, "LAT"] = np.nan  # coordonnées vides
        bruit = pd.DataFrame({"DET_ID": [f"{passage['satellite']}-{t}-B{j:03d}" for j in range(rng.poisson(1.5))]})
        if len(bruit):                                                            # fausses alarmes à faible SNR
            bruit = bruit.assign(EPOCH_MS=t * 1000, LAT=[rng.uniform(ymin, ymax) for _ in range(len(bruit))],
                                 LON=[rng.uniform(xmin, xmax) for _ in range(len(bruit))], SMA_M=6000, SMI_M=4000,
                                 ORIENT_DEG=0, FREQ_MHZ=9410.0, BAND="X", PULSE_US=0.5,
                                 SNR_DB=[round(rng.uniform(2, 5.5), 1) for _ in range(len(bruit))])
            brut = pd.concat([brut, bruit])
    # défaut grave imposé (pour démontrer la détection d'un lot KO et son retraitement)
    if defaut == "horloge":
        brut["EPOCH_MS"] = brut.EPOCH_MS + 3 * 3600 * 1000      # dérive d'horloge de 3 h
    elif defaut == "inversion":
        brut[["LAT", "LON"]] = brut[["LON", "LAT"]].values      # colonnes inversées sur tout le lot

    nom = f"{passage['satellite']}_{debut:%Y%m%dT%H%M}_{demande['id']}_v{version}.csv"
    chemin = config.LOTS_BRUTS / nom
    brut.sample(frac=1, random_state=rng.integers(1e9)).to_csv(chemin, sep=";", index=False)
    return chemin, verite, t
