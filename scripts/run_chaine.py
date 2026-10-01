"""Lance la chaîne complète, étape par étape, et s'arrête à la première erreur.

Usage : python-qgis-ltr.bat run_chaine.py [--depuis 04]
En cas d'échec, l'étape fautive est inscrite au journal (niveau ERREUR) pour l'astreinte :
corriger, puis relancer avec --depuis <étape>. Chaque étape est rejouable.
"""
import argparse
import subprocess
import sys
import time
from pathlib import Path

ICI = Path(__file__).parent
ETAPES = ["01_preparation", "02_demandes_programmation", "03_acquisitions", "04_mise_en_forme",
          "05_controle_qualite", "06_croisement", "07_livraison", "08_supervision", "10_projet_qgis"]

p = argparse.ArgumentParser()
p.add_argument("--depuis", default="01", help="numéro de l'étape de reprise (ex. 04)")
args = p.parse_args()

for etape in [e for e in ETAPES if e[:2] >= args.depuis]:
    t0 = time.time()
    print(f"\n=== {etape} ===", flush=True)
    r = subprocess.run([sys.executable, str(ICI / f"{etape}.py")], cwd=ICI)
    if r.returncode:
        sys.path.insert(0, str(ICI))
        from commun import base_production, journal
        journal(base_production(), "ERREUR", "orchestration",
                f"échec de {etape} (code {r.returncode}) : relancer avec --depuis {etape[:2]} après correction")
        sys.exit(r.returncode)
    print(f"--- {etape} terminé en {time.time() - t0:.0f} s", flush=True)
print("\nChaîne terminée : voir sorties/tableau_de_bord.html")
