"""Capture du tableau de bord pour le README (Microsoft Edge sans interface)."""
import subprocess
import time

import config

EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
sortie = config.SORTIES / "captures" / "tableau_de_bord.png"
sortie.parent.mkdir(exist_ok=True)
if sortie.exists():
    sortie.unlink()
page = (config.SORTIES / "tableau_de_bord.html").as_uri()
subprocess.run([EDGE, "--headless=new", "--disable-gpu", "--hide-scrollbars", "--force-color-profile=srgb",
                "--window-size=1400,1180", "--virtual-time-budget=10000", f"--screenshot={sortie}", page])
# Edge rend la main avant d'avoir écrit l'image : on attend le fichier
for _ in range(60):
    if sortie.exists() and sortie.stat().st_size > 0:
        break
    time.sleep(0.5)
print(sortie, sortie.stat().st_size if sortie.exists() else "absent")
