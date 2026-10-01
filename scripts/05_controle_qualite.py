"""05. Contrôle qualité des lots mis en forme, retraitement des lots KO, rapports.

Pour chaque lot : contrôles ligne à ligne (coordonnées, doublons, fenêtre temporelle, zone,
SNR, ellipse, position à terre) puis diagnostic de lot. Un lot KO (défaut systématique) fait
l'objet d'une demande de retraitement au segment sol ; la version 2 repasse toute la chaîne.
"""
import html
import zlib
from datetime import datetime, timedelta

import geopandas as gpd
import numpy as np
import pandas as pd

import config
from commun import base_production, journal, statut
from production import controler, ecrire_lot, mettre_en_forme
from simulateur import simuler_lot

cx = base_production()
for f in config.RAPPORTS_QC.glob("*.html"):
    f.unlink()

REQ = """SELECT l.*, p.demande_id, pa.satellite, pa.debut_utc, pa.fin_utc, d.zone_wkt, d.id AS did
         FROM lots l JOIN programmations p ON p.id = l.programmation_id
         JOIN passages pa ON pa.id = p.passage_id JOIN demandes d ON d.id = p.demande_id"""
CSS = """body{font-family:Segoe UI,Arial,sans-serif;margin:24px;color:#1d2733;max-width:900px}
h1{font-size:20px}h2{font-size:16px;margin-top:24px}table{border-collapse:collapse;width:100%;font-size:13px}
td,th{border:1px solid #d4dae1;padding:4px 8px;text-align:left}th{background:#eef2f6}
.qc_ok{color:#1b7f3b;font-weight:600}.qc_reserve{color:#a86500;font-weight:600}.qc_ko{color:#b3261e;font-weight:600}
.mention{font-size:11px;color:#6b7785;margin-top:32px}"""


def rapport_lot(lot, bilan, rejets, version):
    lignes = "".join(f"<tr><td>{html.escape(k)}</td><td>{v}</td></tr>" for k, v in bilan["controles"].items())
    rej = "".join(f"<tr><td>{r.id_detection}</td><td>{html.escape(str(r.motif))}</td></tr>"
                  for r in rejets.head(40).itertuples())
    diag = "".join(f"<li>{html.escape(d)}</li>" for d in bilan["diagnostic"]) or "<li>aucun défaut systématique</li>"
    page = f"""<!doctype html><html lang="fr"><meta charset="utf-8"><title>QC {lot['id']} v{version}</title>
<style>{CSS}</style><h1>Rapport de contrôle qualité : {lot['id']} (version {version})</h1>
<p>Demande {lot['did']} · satellite {lot['satellite']} · passage {lot['debut_utc']} → {lot['fin_utc']}</p>
<p>Statut : <span class="{bilan['statut']}">{bilan['statut'].upper()}</span> · {bilan['n_valide']} détections
retenues sur {bilan['n_brut']} lignes brutes (rejet {bilan['taux_rejet']:.0%}) · {bilan['n_reserve']} avec réserve</p>
<h2>Diagnostic du lot</h2><ul>{diag}</ul>
<h2>Contrôles ligne à ligne (lignes rejetées)</h2><table><tr><th>Contrôle</th><th>Rejets</th></tr>{lignes}</table>
<h2>Détail des rejets (40 premiers)</h2><table><tr><th>Détection</th><th>Motif</th></tr>{rej}</table>
<p class="mention">{config.MENTION}</p></html>"""
    (config.RAPPORTS_QC / f"{lot['id']}_v{version}.html").write_text(page, encoding="utf-8")


def charger(lot, version):
    g = gpd.read_file(config.LOTS_FORMES / f"{lot['id']}_v{version}.gpkg", layer="mise_en_forme")
    g["horodatage"] = pd.to_datetime(g.horodatage, utc=True)
    return g


def traiter(lot, gdf, version, t):
    valides, rejets, bilan = controler(gdf, dict(lot))
    ecrire_lot(config.LOTS_FORMES / f"{lot['id']}_v{version}.gpkg", valides, rejets)
    rapport_lot(lot, bilan, rejets, version)
    statut(cx, "lots", lot["id"], bilan["statut"], nb_valide=bilan["n_valide"], score_qc=bilan["score"],
           version=version, verdict_qc=bilan["statut"])
    niveau = {"qc_ok": "INFO", "qc_reserve": "ALERTE", "qc_ko": "ERREUR"}[bilan["statut"]]
    detail = "; ".join(bilan["diagnostic"]) or bilan["reserve"] or ", ".join(f"{k} {v}" for k, v in bilan["controles"].items() if v)
    journal(cx, niveau, "controle_qualite",
            f"v{version} {bilan['statut'].upper()} : {bilan['n_valide']}/{bilan['n_brut']} retenues"
            + (f" ({detail})" if detail else ""), lot["id"], t)
    return bilan


synthese = []
for lot in cx.execute(REQ + " WHERE l.statut = 'mis_en_forme' ORDER BY l.date_reception").fetchall():
    t = datetime.fromisoformat(lot["date_reception"].replace("Z", "")) + timedelta(minutes=10)
    bilan = traiter(lot, charger(lot, 1), 1, t)
    version = 1
    if bilan["statut"] == "qc_ko":
        # Astreinte : demande de retraitement au segment sol, puis rejeu de la chaîne sur la v2
        journal(cx, "ALERTE", "retraitement", "demande de retraitement transmise au segment sol", lot["id"], t)
        d = dict(cx.execute("SELECT * FROM demandes WHERE id = ?", (lot["did"],)).fetchone())
        rng = np.random.default_rng(zlib.crc32(lot["id"].encode()))
        chemin, _, _ = simuler_lot(dict(lot), d, rng, defaut=None, version=2)
        t2 = t + timedelta(hours=3)
        cx.execute("UPDATE lots SET fichier_brut = ?, nb_brut = ? WHERE id = ?",
                   (chemin.name, sum(1 for _ in open(chemin, encoding="utf-8")) - 1, lot["id"]))
        journal(cx, "INFO", "retraitement", f"version 2 reçue : {chemin.name}", lot["id"], t2)
        g2 = mettre_en_forme(chemin, dict(lot))
        version = 2
        bilan = traiter(lot, g2, 2, t2 + timedelta(minutes=10))
    synthese.append({"lot": lot["id"], "demande": lot["did"], "version": version, **{k: bilan[k] for k in
                     ("statut", "n_brut", "n_valide", "n_reserve", "taux_rejet")}, **bilan["controles"]})

s = pd.DataFrame(synthese)
s.to_csv(config.RAPPORTS_QC / "synthese_qc.csv", index=False, sep=";", encoding="utf-8-sig")
tot = s.drop(columns=["lot", "demande", "version", "statut", "taux_rejet"]).sum()
par_statut = s.statut.value_counts().to_dict()
lignes = "".join(f"<tr><td><a href='{r.lot}_v{r.version}.html'>{r.lot}</a></td><td>{r.demande}</td><td>v{r.version}</td>"
                 f"<td class='{r.statut}'>{r.statut}</td><td>{r.n_brut}</td><td>{r.n_valide}</td><td>{r.taux_rejet:.0%}</td></tr>"
                 for r in s.itertuples())
ctrl = "".join(f"<tr><td>{html.escape(k)}</td><td>{int(tot[k])}</td></tr>" for k in synthese[0] if k in tot and k not in
               ("n_brut", "n_valide", "n_reserve"))
(config.RAPPORTS_QC / "index.html").write_text(f"""<!doctype html><html lang="fr"><meta charset="utf-8">
<title>Synthèse contrôle qualité</title><style>{CSS}</style><h1>Synthèse du contrôle qualité</h1>
<p>{len(s)} lots · {int(tot.n_brut)} lignes brutes · {int(tot.n_valide)} détections retenues ·
statuts : {', '.join(f'{k} {v}' for k, v in par_statut.items())}</p>
<h2>Rejets par contrôle</h2><table><tr><th>Contrôle</th><th>Lignes</th></tr>{ctrl}</table>
<h2>Lots</h2><table><tr><th>Lot</th><th>Demande</th><th>Version</th><th>Statut</th><th>Brut</th><th>Retenues</th><th>Rejet</th></tr>
{lignes}</table><p class="mention">{config.MENTION}</p></html>""", encoding="utf-8")
journal(cx, "INFO", "controle_qualite", f"{len(s)} lots contrôlés : {par_statut} ; "
        f"{int(tot.n_valide)} détections retenues sur {int(tot.n_brut)}")
