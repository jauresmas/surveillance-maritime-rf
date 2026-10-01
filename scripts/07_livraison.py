"""07. Livraison aux clients.

Pour chaque demande en production : sélection des détections selon les cibles du client,
paquet aux formats commandés (GPKG, CSV, KML, PDF), bordereau de livraison, empreintes SHA-256,
archive ZIP. La livraison est enregistrée dans la base de suivi et la demande passe « livrée ».
"""
import hashlib
import html
import shutil
import zipfile
from datetime import datetime, timedelta

import geopandas as gpd
import pandas as pd
from shapely import wkt

import config
from carte_pdf import carte
from commun import base_production, journal, statut

cx = base_production()
cx.execute("DELETE FROM livraisons")
cx.execute("DELETE FROM journal WHERE etape = 'livraison'")
cx.execute("UPDATE demandes SET statut = 'en_production' WHERE statut IN ('livree','cloturee')")
cx.commit()
shutil.rmtree(config.LIVRAISONS, ignore_errors=True)
config.LIVRAISONS.mkdir(parents=True)

src = config.PRODUITES / "detections_correlees.gpkg"
toutes = gpd.read_file(src, layer="detections")
ellipses = gpd.read_file(src, layer="ellipses_95")
ref = config.REFERENTIEL
CAPTURES = config.SORTIES / "captures"
CAPTURES.mkdir(exist_ok=True)

COLONNES_CLIENT = {
    "id_detection": "id_detection", "horodatage": "horodatage_utc", "lon": "lon", "lat": "lat",
    "demi_grand_axe_m": "incert_grand_axe_m", "demi_petit_axe_m": "incert_petit_axe_m",
    "orientation_deg": "incert_orientation_deg", "bande_libelle": "signal", "frequence_mhz": "frequence_mhz",
    "snr_db": "snr_db", "qc": "qualite", "correlation": "correlation", "mmsi": "mmsi", "ais_nom": "nom_navire",
    "ais_type": "type_navire", "ais_pavillon": "pavillon", "ais_longueur": "longueur_m", "ecart_ais_m": "ecart_ais_m",
    "zone_maritime": "zone_maritime", "interdiction_peche": "interdiction_peche", "distance_cote_m": "distance_cote_m",
    "port_proche": "port_proche", "distance_port_km": "distance_port_km", "interet": "interet",
    "lot_id": "lot_id", "satellite": "satellite",
}


def sha256(chemin):
    h = hashlib.sha256()
    with open(chemin, "rb") as f:
        for bloc in iter(lambda: f.read(1 << 20), b""):
            h.update(bloc)
    return h.hexdigest()


demandes = cx.execute("""SELECT d.*, c.nom AS client, c.contact FROM demandes d JOIN clients c ON c.id = d.client_id
                         WHERE d.statut = 'en_production' ORDER BY d.id""").fetchall()
for d in demandes:
    lots = pd.read_sql("""SELECT l.id, l.version, l.verdict_qc, l.nb_brut, l.nb_valide, pa.satellite, pa.debut_utc
                          FROM lots l JOIN programmations p ON p.id = l.programmation_id
                          JOIN passages pa ON pa.id = p.passage_id WHERE p.demande_id = ? ORDER BY pa.debut_utc""",
                       cx, params=(d["id"],))
    echecs = pd.read_sql("""SELECT p.id, pa.satellite, pa.debut_utc FROM programmations p JOIN passages pa ON pa.id = p.passage_id
                            WHERE p.demande_id = ? AND p.statut = 'echec_acquisition'""", cx, params=(d["id"],))
    det = toutes[toutes.demande_id == d["id"]].copy()
    # cibles du client : on garde les navires des types demandés et toutes les détections sans AIS
    if d["cibles"] != "Tous":
        cibles = d["cibles"].split(",")
        det = det[det.mmsi.isna() | det.ais_type.isin(cibles)]
    det = det[list(COLONNES_CLIENT)].rename(columns=COLONNES_CLIENT)
    det = gpd.GeoDataFrame(det, geometry=toutes.loc[det.index, "geometry"], crs=config.CRS_GEO)
    ell = ellipses[ellipses.id_detection.isin(det.id_detection)][["id_detection", "correlation", "geometry"]]

    date_liv = datetime.fromisoformat(d["date_fin"][:10]) + timedelta(days=1, hours=10)
    dossier = config.LIVRAISONS / f"{d['id']}_{date_liv:%Y%m%d}"
    dossier.mkdir()
    base = f"{d['id']}_detections_rf"
    formats = d["formats"].split(",")
    gpkg = dossier / f"{base}.gpkg"
    det.to_file(gpkg, layer="detections")
    ell.to_file(gpkg, layer="ellipses_95")
    gpd.GeoDataFrame({"demande": [d["id"]], "intitule": [d["intitule"]]}, geometry=[wkt.loads(d["zone_wkt"])],
                     crs=config.CRS_GEO).to_file(gpkg, layer="zone_demandee")
    lots.to_csv(dossier / f"{d['id']}_acquisitions.csv", sep=";", index=False, encoding="utf-8-sig")
    if "CSV" in formats:
        pd.DataFrame(det.drop(columns="geometry")).to_csv(dossier / f"{base}.csv", sep=";", index=False,
                                                          encoding="utf-8-sig")
    if "KML" in formats:
        k = det[["id_detection", "horodatage_utc", "correlation", "nom_navire", "type_navire", "interet", "geometry"]].copy()
        k["Name"] = k.nom_navire.fillna("Sans AIS")
        k["Description"] = k.apply(lambda r: f"{r.correlation} | {r.horodatage_utc} | intérêt {r.interet}", axis=1)
        k[["Name", "Description", "geometry"]].to_file(dossier / f"{base}.kml", driver="KML")

    n_s = int(det.mmsi.isna().sum())
    n_haute = int((det.interet == "haute").sum())
    acq_ok = int(lots.verdict_qc.isin(["qc_ok", "qc_reserve"]).sum())
    if "PDF" in formats:
        debut, fin = (datetime.fromisoformat(d[k][:10]).strftime("%d/%m/%Y") for k in ("date_debut", "date_fin"))
        carte(gpkg, ref, dossier / f"{d['id']}_carte_synthese.pdf", f"{d['id']} · {d['intitule']}",
              f"{d['client']}  ·  du {debut} au {fin}  ·  cibles : {d['cibles']}  ·  livré le {date_liv:%d/%m/%Y}",
              [(acq_ok, "acquisitions"), (len(det), "détections"), (n_s, "sans AIS")],
              sortie_png=CAPTURES / f"carte_{d['id']}.png")

    # bordereau
    lignes_lots = "".join(f"<tr><td>{r.id}</td><td>{r.satellite}</td><td>{r.debut_utc}</td><td>v{r.version}</td>"
                          f"<td>{r.verdict_qc}</td><td>{r.nb_valide}</td></tr>" for r in lots.itertuples())
    lignes_echecs = "".join(f"<li>{r.debut_utc} ({r.satellite}) : pas de données reçues</li>" for r in echecs.itertuples()) \
        or "<li>aucun</li>"
    reserves = lots[lots.verdict_qc == "qc_reserve"]
    retraites = lots[lots.version > 1]
    fichiers = sorted(p for p in dossier.iterdir() if p.is_file())
    empreintes = {p.name: sha256(p) for p in fichiers}
    (dossier / "SHA256SUMS.txt").write_text("".join(f"{h}  {n}\n" for n, h in empreintes.items()), encoding="utf-8")
    contenu = "".join(f"<tr><td>{n}</td><td>{(dossier / n).stat().st_size / 1024:.0f} Ko</td><td><code>{h[:16]}…</code></td></tr>"
                      for n, h in empreintes.items())
    (dossier / "LISEZMOI_bordereau.html").write_text(f"""<!doctype html><html lang="fr"><meta charset="utf-8">
<title>Bordereau {d['id']}</title><style>body{{font-family:Segoe UI,Arial,sans-serif;max-width:860px;margin:24px;color:#1d2733}}
table{{border-collapse:collapse;width:100%;font-size:13px}}td,th{{border:1px solid #d4dae1;padding:4px 8px;text-align:left}}
th{{background:#eef2f6}}.m{{font-size:11px;color:#6b7785}}</style>
<h1>Bordereau de livraison {d['id']}</h1>
<p><b>{html.escape(d['client'])}</b> · {html.escape(d['intitule'])}<br>
Période {d['date_debut'][:10]} au {d['date_fin'][:10]} · cibles : {d['cibles']} · livré le {date_liv:%d/%m/%Y à %H:%M} UTC</p>
<h2>Synthèse</h2><ul><li>{acq_ok} acquisitions exploitées sur {len(lots) + len(echecs)} programmées</li>
<li>{len(det)} détections livrées, dont {n_s} sans correspondance AIS (navires sombres potentiels)
et {n_haute} en zone d'interdiction de pêche en vigueur</li>
<li>{len(reserves)} lot(s) livré(s) avec réserve qualité ; {len(retraites)} lot(s) retraité(s) par le segment sol</li></ul>
<h2>Acquisitions non réalisées</h2><ul>{lignes_echecs}</ul>
<h2>Contenu</h2><table><tr><th>Fichier</th><th>Taille</th><th>SHA-256</th></tr>{contenu}</table>
<p>Vérifier l'intégrité : <code>sha256sum -c SHA256SUMS.txt</code> (Linux) ou
<code>Get-FileHash -Algorithm SHA256 fichier</code> (Windows).</p>
<h2>Lecture des données</h2><ul>
<li><b>horodatage_utc</b> : instant de captation du signal ; <b>lon/lat</b> : centre de l'ellipse à 95 %.</li>
<li><b>correlation</b> : « corrélée AIS » si un navire AIS est compatible avec l'ellipse (distance de Mahalanobis,
porte à 99 %) ; sinon « navire sombre potentiel ».</li>
<li><b>qualite</b> : « reserve_position_a_terre » si le centre tombe à terre (l'ellipse touche la mer).</li>
<li><b>interet</b> : haute (sans AIS en zone d'interdiction), moyenne (sans AIS en mer territoriale), à vérifier, faible.</li>
<li>Près des ports, plusieurs navires peuvent tenir dans une même ellipse : l'identité proposée y est moins sûre.</li></ul>
<h2>Acquisitions</h2><table><tr><th>Lot</th><th>Satellite</th><th>Passage</th><th>Version</th><th>QC</th><th>Détections</th></tr>
{lignes_lots}</table>
<h2>Support</h2><p>Une question sur cette livraison : {html.escape(d['contact'])} → support production, en rappelant
la référence {d['id']}.</p><p class="m">{config.MENTION}. AIS : {config.SOURCE_AIS}.</p></html>""", encoding="utf-8")
    (dossier / "SHA256SUMS.txt").write_text(
        "".join(f"{sha256(p)}  {p.name}\n" for p in sorted(dossier.iterdir()) if p.name != "SHA256SUMS.txt"),
        encoding="utf-8")

    archive = config.LIVRAISONS / f"{dossier.name}.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as z:
        for p in sorted(dossier.iterdir()):
            z.write(p, f"{dossier.name}/{p.name}")
    liv_id = f"LIV-{d['id']}-01"
    cx.execute("INSERT INTO livraisons VALUES (?,?,?,?,?,?,?,?)",
               (liv_id, d["id"], f"{date_liv:%Y-%m-%dT%H:%M:%S}", archive.name, sha256(archive), len(lots), len(det), n_s))
    cx.execute("""UPDATE lots SET statut = 'livre' WHERE verdict_qc IN ('qc_ok','qc_reserve') AND programmation_id IN
                  (SELECT id FROM programmations WHERE demande_id = ?)""", (d["id"],))
    statut(cx, "demandes", d["id"], "livree")
    journal(cx, "INFO", "livraison", f"{archive.name} : {len(det)} détections, {n_s} navires sombres potentiels, "
            f"formats {d['formats']}", d["id"], date_liv)
