# FAQ support client (premier niveau)

Réponses types aux questions reçues après une livraison. Ce qui sort de ce cadre est escaladé : commerce (geste commercial, nouvelle demande), analyse (identification d'un émetteur, interprétation d'un comportement), R&D (performance du capteur).

**Que représente un point ?**
Le centre de l'ellipse d'incertitude à 95 % de la géolocalisation radiofréquence, à l'instant `horodatage_utc`. L'ellipse est dans la couche `ellipses_95` du GeoPackage.

**Que veut dire « non corrélée : navire sombre potentiel » ?**
Aucun navire émettant de l'AIS n'était compatible avec l'ellipse au même instant. Causes possibles : AIS éteint, AIS absent (petite unité), panne d'émetteur, ou zone mal couverte par la réception AIS. C'est un signal à examiner, pas une preuve d'infraction.

**L'identité proposée pour un navire est-elle certaine ?**
Au large, oui dans la grande majorité des cas. Près des ports, plusieurs navires peuvent tenir dans une même ellipse ; l'identité y est indicative. Le champ `ecart_ais_m` donne la distance entre la détection et la position AIS retenue.

**Pourquoi des détections à terre ?**
Le centre de l'ellipse peut tomber sur la côte quand le navire est à quai. Ces détections portent `qualite = reserve_position_a_terre` ; celles dont l'ellipse est entièrement à terre sont écartées au contrôle qualité.

**Il manque une acquisition.**
Voir la rubrique « Acquisitions non réalisées » du bordereau (`LISEZMOI_bordereau.html`) et le fichier `<demande>_acquisitions.csv`.

**Comment vérifier que les fichiers sont intacts ?**
Linux et macOS : `sha256sum -c SHA256SUMS.txt`. Windows : `Get-FileHash -Algorithm SHA256 <fichier>` puis comparer à `SHA256SUMS.txt`.

**Les données sont-elles disponibles dans une autre projection ?**
Le standard de livraison est WGS84 (EPSG:4326). Dans QGIS : Exporter, Sauvegarder les entités sous, choisir le SCR voulu (par exemple EPSG:2154).

**Le KML n'affiche pas les ellipses.**
Le KML porte les centres pour un affichage rapide ; les ellipses sont dans le GeoPackage.
