"""Traitements unitaires d'un lot : mise en forme et contrôle qualité."""
from datetime import datetime, timedelta, timezone

import geopandas as gpd
import numpy as np
import pandas as pd
from shapely import affinity, wkt
from shapely.geometry import Point

import config

SCHEMA_BRUT = {"DET_ID": "id_detection", "EPOCH_MS": "horodatage", "LAT": "lat", "LON": "lon",
               "SMA_M": "demi_grand_axe_m", "SMI_M": "demi_petit_axe_m", "ORIENT_DEG": "orientation_deg",
               "FREQ_MHZ": "frequence_mhz", "BAND": "bande", "PULSE_US": "impulsion_us", "SNR_DB": "snr_db"}
LIBELLE_BANDE = {"X": "Radar bande X (9,4 GHz)", "S": "Radar bande S (3 GHz)", "VHF": "Radio VHF marine"}
SEUIL_SNR = 6.0
MARGE_ZONE_M = 5000
MARGE_TEMPS_S = 60

_terre = {}


def terre():
    if "g" not in _terre:
        _terre["g"] = gpd.read_file(config.REFERENTIEL, layer="terre").to_crs(config.CRS_METRE).geometry.iloc[0]
    return _terre["g"]


def ellipse(x, y, a, b, orientation):
    """Ellipse en mètres : grand axe a orienté de `orientation` degrés depuis le nord (sens horaire)."""
    e = affinity.scale(Point(0, 0).buffer(1, quad_segs=16), xfact=b, yfact=a)
    return affinity.translate(affinity.rotate(e, -orientation, origin=(0, 0)), x, y)


def mettre_en_forme(chemin_brut, lot):
    """Fichier capteur brut -> table normalisée, typée, géoréférencée (WGS84)."""
    df = pd.read_csv(chemin_brut, sep=";", dtype={"DET_ID": str, "BAND": str})
    manquantes = set(SCHEMA_BRUT) - set(df.columns)
    if manquantes:
        raise ValueError(f"colonnes absentes du fichier brut : {sorted(manquantes)}")
    df = df.rename(columns=SCHEMA_BRUT)
    df["horodatage"] = pd.to_datetime(df.horodatage, unit="ms", utc=True)
    for c in ("lat", "lon", "demi_grand_axe_m", "demi_petit_axe_m", "orientation_deg", "frequence_mhz",
              "impulsion_us", "snr_db"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df["bande_libelle"] = df.bande.map(LIBELLE_BANDE)
    df.insert(1, "lot_id", lot["id"])
    df.insert(2, "demande_id", lot["demande_id"])
    df.insert(3, "satellite", lot["satellite"])
    geom = [Point(x, y) if pd.notna(x) and pd.notna(y) else None for x, y in zip(df.lon, df.lat)]
    return gpd.GeoDataFrame(df, geometry=geom, crs=config.CRS_GEO)


def controler(gdf, lot):
    """Contrôle qualité d'un lot mis en forme.

    Retourne (valides, rejets, bilan) ; bilan["statut"] vaut qc_ok, qc_reserve ou qc_ko.
    """
    zone = wkt.loads(lot["zone_wkt"])
    zone_m = gpd.GeoSeries([zone], crs=config.CRS_GEO).to_crs(config.CRS_METRE).iloc[0]
    zone_tol = zone_m.buffer(MARGE_ZONE_M)
    debut = datetime.fromisoformat(lot["debut_utc"].replace("Z", "+00:00")) - timedelta(seconds=MARGE_TEMPS_S)
    fin = datetime.fromisoformat(lot["fin_utc"].replace("Z", "+00:00")) + timedelta(seconds=MARGE_TEMPS_S)
    g = gdf.copy()
    g["motif"] = None
    g["qc"] = "valide"
    n = len(g)

    def rejeter(masque, motif):
        m = masque & g.motif.isna()
        g.loc[m, "motif"] = motif
        return int(m.sum())

    compte = {}
    compte["coordonnées manquantes"] = rejeter(g.geometry.isna(), "coordonnées manquantes")
    compte["doublons"] = rejeter(g.duplicated("id_detection"), "doublon d'identifiant")
    hors_temps = ~g.horodatage.between(debut, fin)
    compte["hors fenêtre d'acquisition"] = rejeter(hors_temps, "horodatage hors fenêtre du passage")
    pts_m = g.to_crs(config.CRS_METRE)
    hors_zone = pd.Series([p is None or not zone_tol.contains(p) for p in pts_m.geometry], index=g.index, dtype=bool)
    compte["hors zone demandée"] = rejeter(hors_zone, "hors zone demandée (+5 km)")
    compte["SNR faible"] = rejeter(g.snr_db < SEUIL_SNR, f"SNR < {SEUIL_SNR} dB (fausse alarme probable)")
    incoherent = (g.demi_petit_axe_m > g.demi_grand_axe_m) | (g.demi_grand_axe_m > 10_000) | g.demi_grand_axe_m.isna()
    compte["ellipse incohérente"] = rejeter(incoherent, "ellipse d'incertitude incohérente")

    # Sur terre : rejet si toute l'ellipse est sur terre, réserve si l'ellipse touche la mer
    ellipses = [ellipse(p.x, p.y, a, b, o) if p is not None and pd.notna(a) else None
                for p, a, b, o in zip(pts_m.geometry, g.demi_grand_axe_m, g.demi_petit_axe_m, g.orientation_deg)]
    t = terre()
    sur_terre = pd.Series([e is not None and t.contains(Point(e.centroid)) for e in ellipses], index=g.index)
    tout_terre = pd.Series([e is not None and t.contains(e) for e in ellipses], index=g.index)
    compte["ellipse entièrement à terre"] = rejeter(tout_terre, "ellipse entièrement à terre")
    reserve = sur_terre & ~tout_terre & g.motif.isna()
    g.loc[reserve, "qc"] = "reserve_position_a_terre"

    rejets = g[g.motif.notna()].copy()
    valides = g[g.motif.isna()].drop(columns="motif")
    valides["geom_ellipse"] = [ellipses[i] for i in np.flatnonzero(g.motif.isna().values)]

    # Diagnostic de lot : défaut systématique ?
    diag = []
    lignes_avec_coord = max(1, int(g.geometry.notna().sum()))
    if hors_temps.sum() > 0.5 * n and n:
        ecart = (g.horodatage.median() - (debut + (fin - debut) / 2)).total_seconds() / 3600
        diag.append(f"dérive d'horloge probable ({ecart:+.1f} h sur l'horodatage médian)")
    if hors_zone.sum() > 0.5 * lignes_avec_coord and n:
        inv = g.dropna(subset=["lat", "lon"])
        inv = gpd.GeoSeries(gpd.points_from_xy(inv.lat, inv.lon), crs=config.CRS_GEO).to_crs(config.CRS_METRE)
        if inv.within(zone_tol).mean() > 0.8:
            diag.append("colonnes latitude et longitude inversées")
        else:
            diag.append("positions massivement hors zone")
    taux_rejet = len(rejets) / n if n else 0
    # doublons et fausses alarmes à faible SNR sont un filtrage nominal ; le reste est une anomalie
    nominaux = compte["doublons"] + compte["SNR faible"]
    taux_anomalie = (len(rejets) - nominaux) / n if n else 0
    taux_terre = (valides.qc != "valide").mean() if len(valides) else 0
    if diag:
        statut = "qc_ko"
    elif n == 0 or len(valides) == 0 or taux_anomalie > 0.10 or taux_terre > 0.40:
        statut = "qc_reserve"
        diag_res = "lot vide (aucun émetteur détecté)" if len(valides) == 0 else (
            f"anomalies {taux_anomalie:.0%}" if taux_anomalie > 0.10 else f"positions à terre {taux_terre:.0%}")
    else:
        statut = "qc_ok"
    if statut != "qc_reserve":
        diag_res = None
    bilan = {"statut": statut, "n_brut": n, "n_valide": len(valides), "n_reserve": int((valides.qc != "valide").sum()),
             "taux_rejet": taux_rejet, "taux_anomalie": taux_anomalie, "controles": compte, "diagnostic": diag,
             "reserve": diag_res if statut == "qc_reserve" else None,
             "score": round(len(valides) / n, 3) if n else None}
    return valides, rejets, bilan


def ecrire_lot(chemin, valides, rejets):
    """GeoPackage du lot mis en forme : détections, ellipses d'incertitude, rejets."""
    if chemin.exists():
        chemin.unlink()
    pts = valides.drop(columns="geom_ellipse")
    pts["horodatage"] = pts.horodatage.dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    pts.to_file(chemin, layer="detections")
    ell = gpd.GeoDataFrame(pts[["id_detection", "lot_id", "demi_grand_axe_m", "demi_petit_axe_m", "orientation_deg"]],
                           geometry=list(valides.geom_ellipse), crs=config.CRS_METRE).to_crs(config.CRS_GEO)
    ell.to_file(chemin, layer="ellipses_95")
    r = pd.DataFrame(rejets.drop(columns="geometry"))
    r["horodatage"] = r.horodatage.astype(str)
    gpd.GeoDataFrame(r, geometry=list(rejets.geometry), crs=config.CRS_GEO).to_file(chemin, layer="rejets")
