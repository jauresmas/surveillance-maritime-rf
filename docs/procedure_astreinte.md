# Procédure d'astreinte production

Ce document décrit la conduite à tenir quand la chaîne signale une anomalie. Les alertes sont dans la table `journal` de `production.sqlite` (niveaux `ALERTE` et `ERREUR`) et dans le bloc « Journal d'astreinte » du tableau de bord.

## Où regarder en premier

```sql
-- alertes et erreurs des dernières 24 h de production
SELECT date_production, niveau, etape, objet, message
FROM journal
WHERE niveau <> 'INFO'
  AND date_production >= datetime((SELECT max(date_production) FROM journal), '-1 day')
ORDER BY date_production;

-- avancement par demande
SELECT * FROM v_avancement;

-- cycle de vie d'un lot
SELECT * FROM historique_statuts WHERE objet_id = 'L-D001-P20151104-03' ORDER BY id;
```

## Cas traités

| Signal | Étape | Cause probable | Action |
|---|---|---|---|
| `pas de données reçues (anomalie segment sol)` | acquisition | Passage non exploité, défaut station sol | Vérifier qu'aucun fichier n'est arrivé en retard dans `lots_bruts`. Si l'acquisition est perdue : la noter pour le bordereau, prévenir le commerce si la demande est prioritaire, étudier une reprogrammation sur un passage libre. |
| `échec de lecture` | mise en forme | Fichier tronqué ou colonnes manquantes | Ouvrir le fichier brut, comparer l'en-tête au schéma attendu (`production.SCHEMA_BRUT`). Redemander le fichier au segment sol. |
| `QC_KO : dérive d'horloge probable` | contrôle qualité | Horodatage décalé sur tout le lot | Retraitement automatique demandé. Contrôler que la v2 passe en `qc_ok`. Si la dérive persiste, escalader au segment sol avec l'écart mesuré. |
| `QC_KO : colonnes latitude et longitude inversées` | contrôle qualité | Erreur de formatage en sortie capteur | Idem : retraitement automatique, vérifier la v2. Ne jamais corriger à la main un lot livré sans trace. |
| `QC_RESERVE : positions à terre` | contrôle qualité | Navires à quai, centre d'ellipse sur la côte | Normal près des ports. Le lot est livrable ; la réserve figure au bordereau. |
| `QC_RESERVE : lot vide` | contrôle qualité | Aucun émetteur dans la zone au moment du passage | Vérifier la cohérence avec l'AIS de la même heure. Livrable avec réserve. |
| `passages déjà attribués à une demande plus prioritaire` | programmation | Conflit de capacité | Informer le client si le nombre d'acquisitions tombe sous son besoin. |
| `échec de <étape>` | orchestration | Erreur de script | Lire la trace, corriger, puis `run_chaine.py --depuis <étape>`. Chaque étape est rejouable. |

## Reprise

```bash
# relancer toute la chaîne
"C:\Program Files\QGIS 3.34.12\bin\python-qgis-ltr.bat" scripts/run_chaine.py
# reprendre à partir du contrôle qualité
"C:\Program Files\QGIS 3.34.12\bin\python-qgis-ltr.bat" scripts/run_chaine.py --depuis 05
```

## Avant toute livraison

1. Tous les lots de la demande ont un verdict `qc_ok` ou `qc_reserve` (aucun `qc_ko` non retraité).
2. Les acquisitions perdues sont listées dans le bordereau.
3. `SHA256SUMS.txt` est présent et l'empreinte de l'archive est enregistrée dans la table `livraisons`.
