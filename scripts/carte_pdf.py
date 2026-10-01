"""Carte de synthèse d'une livraison (mise en page QGIS, export PDF et PNG).

Même charte que l'atlas du projet QGIS : bandeau, carroyage WGS84, flèche du nord, échelles
graphique et numérique, carte de situation, chiffres clés, légende, pied de page.
"""
import os

from qgis.core import (QgsApplication, QgsCategorizedSymbolRenderer, QgsCoordinateReferenceSystem,
                       QgsCoordinateTransform, QgsFillSymbol, QgsLayoutExporter, QgsLayoutItemLabel,
                       QgsLayoutItemLegend, QgsLayoutItemMap, QgsLayoutItemMapGrid, QgsLayoutItemPicture,
                       QgsLayoutItemScaleBar, QgsLayoutItemShape, QgsLayoutPoint, QgsLayoutSize, QgsLegendStyle,
                       QgsMarkerSymbol, QgsPrintLayout, QgsProject, QgsRendererCategory, QgsScaleBarSettings,
                       QgsTextFormat, QgsUnitTypes, QgsVectorLayer)
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QColor, QFont

import config

BLEU_NUIT, GRIS, GRIS_CLAIR, FILET, ROUGE = "#1f3a5f", "#4f5b67", "#6b7785", "#d4dae1", "#d1495b"
ECHELLES = [50000, 75000, 100000, 150000, 200000, 250000, 300000, 400000, 500000, 750000]
SOURCES = ("Sources : AIS École navale (Zenodo 10.5281/zenodo.1167595, CC BY-NC-SA 4.0) ; côte AEE ; ports Région "
           "Bretagne. Limites 12/24 M calculées depuis le trait de côte. Projection UTM 30N (EPSG:32630), carroyage WGS84.")
_app = {}


def demarrer():
    if "app" not in _app:
        QgsApplication.setPrefixPath(os.environ.get("QGIS_PREFIX_PATH", r"C:\Program Files\QGIS 3.34.12\apps\qgis-ltr"), True)
        _app["app"] = QgsApplication([], False)
        _app["app"].initQgis()
    return _app["app"]


def couche(chemin, nom_couche, titre):
    c = QgsVectorLayer(f"{chemin}|layername={nom_couche}", titre, "ogr")
    if not c.isValid():
        raise RuntimeError(f"couche invalide : {chemin} {nom_couche}")
    return c


def remplissage(couleur, contour, transparence=1.0, largeur=0.2, style="solid"):
    s = QgsFillSymbol.createSimple({"color": couleur, "outline_color": contour, "outline_width": str(largeur),
                                    "style": style})
    s.setOpacity(transparence)
    return s


def format_texte(taille, gras=False, couleur="#1d2733"):
    tf = QgsTextFormat()
    f = QFont("Segoe UI")
    f.setBold(gras)
    tf.setFont(f)
    tf.setSize(taille)
    tf.setColor(QColor(couleur))
    return tf


def carte(livrable, referentiel, sortie_pdf, titre, sous_titre, chiffres, sortie_png=None):
    """chiffres : liste de 3 (valeur, libellé) ; le dernier est mis en rouge (navires sans AIS)."""
    demarrer()
    projet = QgsProject.instance()
    projet.clear()
    projet.setCrs(QgsCoordinateReferenceSystem(config.CRS_METRE))

    terre = couche(referentiel, "terre", "Terre")
    terre.renderer().setSymbol(remplissage("#e4e1da", "#b9b4a8", largeur=0.15))
    zones = couche(referentiel, "zones_maritimes", "Limites 12 et 24 milles")
    zones.renderer().setSymbol(remplissage("0,0,0,0", "#7c8a99", largeur=0.25, style="no"))
    zones.renderer().symbol().symbolLayer(0).setStrokeStyle(Qt.DashLine)
    peche = couche(referentiel, "interdictions_peche", "Interdiction de pêche (coquillages)")
    peche.renderer().setSymbol(remplissage("#c0392b", "#c0392b", 0.3, 0.4))
    ports = couche(referentiel, "ports", "Ports")
    ports.renderer().setSymbol(QgsMarkerSymbol.createSimple({"name": "square", "color": "#3d4652", "size": "1.6",
                                                             "outline_style": "no"}))
    zone_etude = couche(referentiel, "zone_etude", "Zone d'étude")
    zone_etude.renderer().setSymbol(remplissage("0,0,0,0", "#9aa6b2", largeur=0.3, style="no"))
    zone_dem = couche(livrable, "zone_demandee", "Zone demandée")
    zone_dem.renderer().setSymbol(remplissage("0,0,0,0", "#1d2733", largeur=0.6, style="no"))
    ell = couche(livrable, "ellipses_95", "Ellipses d'incertitude (95 %)")
    ell.setRenderer(QgsCategorizedSymbolRenderer("correlation", [
        QgsRendererCategory("non corrélée : navire sombre potentiel", remplissage(ROUGE, ROUGE, 0.18, 0.15),
                            "Ellipse, navire sombre"),
        QgsRendererCategory("corrélée AIS", remplissage("#2b6cb0", "#2b6cb0", 0.10, 0.1), "Ellipse, corrélée AIS")]))
    det = couche(livrable, "detections", "Détections RF")
    det.setRenderer(QgsCategorizedSymbolRenderer("correlation", [
        QgsRendererCategory("non corrélée : navire sombre potentiel",
                            QgsMarkerSymbol.createSimple({"name": "triangle", "color": ROUGE, "size": "3.2",
                                                          "outline_color": "#4a0d16", "outline_width": "0.3"}),
                            "Navire sombre potentiel (sans AIS)"),
        QgsRendererCategory("corrélée AIS",
                            QgsMarkerSymbol.createSimple({"name": "circle", "color": "#2b6cb0", "size": "1.8",
                                                          "outline_color": "white", "outline_width": "0.2"}),
                            "Corrélée à un navire AIS")]))
    # ajoutées du fond vers le dessus : la légende liste ainsi les détections en premier
    for c in (zone_etude, terre, zones, peche, ports, ell, zone_dem, det):
        projet.addMapLayer(c)

    mise = QgsPrintLayout(projet)
    mise.initializeDefaults()
    page = mise.pageCollection().page(0)
    page.setPageSize("A4", page.Landscape)
    mise.renderContext().setPredefinedScales(ECHELLES)

    def texte(t, x, y, w, h, taille=9, gras=False, couleur="#1d2733", alignement=Qt.AlignLeft):
        e = QgsLayoutItemLabel(mise)
        e.setText(t)
        e.setTextFormat(format_texte(taille, gras, couleur))
        e.setHAlign(alignement)
        e.setVAlign(Qt.AlignVCenter)
        e.attemptMove(QgsLayoutPoint(x, y))
        e.attemptResize(QgsLayoutSize(w, h))
        mise.addLayoutItem(e)
        return e

    def cadre(x, y, w, h, fond, contour=None, epaisseur=0.0):
        r = QgsLayoutItemShape(mise)
        r.setShapeType(QgsLayoutItemShape.Rectangle)
        r.setSymbol(QgsFillSymbol.createSimple({"color": fond, "outline_color": contour or fond,
                                                "outline_width": str(epaisseur),
                                                "outline_style": "solid" if contour else "no"}))
        r.attemptMove(QgsLayoutPoint(x, y))
        r.attemptResize(QgsLayoutSize(w, h))
        mise.addLayoutItem(r)

    # Bandeau
    cadre(0, 0, 297, 22, BLEU_NUIT)
    texte(titre, 10, 3, 225, 10, 15, True, "white")
    texte(sous_titre, 10, 12.5, 225, 6, 9, couleur="#c9d6e5")
    texte("CARTE DE LIVRAISON", 237, 4, 50, 6, 7, True, "#c9d6e5", Qt.AlignRight)
    texte(os.path.basename(str(sortie_pdf)).split("_")[0], 237, 11, 50, 6, 11, True, "white", Qt.AlignRight)

    # Carte principale : plus petite échelle ronde contenant la zone demandée
    m = QgsLayoutItemMap(mise)
    m.attemptMove(QgsLayoutPoint(17, 30))
    m.attemptResize(QgsLayoutSize(182, 150))
    m.setLayers([det, zone_dem, ell, ports, terre, peche, zones])  # la terre masque la partie terrestre des zones
    m.setFrameEnabled(True)
    m.setFrameStrokeColor(QColor(GRIS))
    m.setBackgroundColor(QColor("#f4f8fb"))
    mise.addLayoutItem(m)
    tr = QgsCoordinateTransform(zone_dem.crs(), projet.crs(), projet)
    r = tr.transformBoundingBox(zone_dem.extent())
    r.grow(max(r.width(), r.height()) * 0.06)
    m.zoomToExtent(r)
    m.setScale(next((e for e in ECHELLES if e >= m.scale()), ECHELLES[-1]))

    grille = QgsLayoutItemMapGrid("Carroyage WGS84", m)
    m.grids().addGrid(grille)
    grille.setCrs(QgsCoordinateReferenceSystem("EPSG:4326"))
    grille.setIntervalX(0.1)
    grille.setIntervalY(0.1)
    grille.setStyle(QgsLayoutItemMapGrid.Solid)
    grille.setGridLineColor(QColor(120, 135, 150, 70))
    grille.setGridLineWidth(0.08)
    grille.setFrameStyle(QgsLayoutItemMapGrid.Zebra)
    grille.setFrameWidth(1.2)
    grille.setFramePenSize(0.2)
    grille.setFrameFillColor1(QColor("white"))
    grille.setFrameFillColor2(QColor(GRIS))
    grille.setAnnotationEnabled(True)
    grille.setAnnotationFormat(QgsLayoutItemMapGrid.DegreeMinute)
    grille.setAnnotationPrecision(0)
    grille.setAnnotationTextFormat(format_texte(6, couleur=GRIS))
    for bord, sens in ((QgsLayoutItemMapGrid.Left, QgsLayoutItemMapGrid.Vertical),
                       (QgsLayoutItemMapGrid.Bottom, QgsLayoutItemMapGrid.Horizontal)):
        grille.setAnnotationPosition(QgsLayoutItemMapGrid.OutsideMapFrame, bord)
        grille.setAnnotationDirection(sens, bord)
    for bord in (QgsLayoutItemMapGrid.Right, QgsLayoutItemMapGrid.Top):
        grille.setAnnotationDisplay(QgsLayoutItemMapGrid.HideAll, bord)

    # Flèche du nord
    cadre(187.5, 32.5, 9.5, 14, "255,255,255,215", FILET, 0.15)
    nord = QgsLayoutItemPicture(mise)
    nord.setPicturePath(os.path.join(QgsApplication.svgPaths()[0], "arrows", "NorthArrow_11.svg"))
    nord.setLinkedMap(m)
    nord.attemptMove(QgsLayoutPoint(188.75, 33.2))
    nord.attemptResize(QgsLayoutSize(7, 9))
    mise.addLayoutItem(nord)
    texte("N", 187.5, 42.2, 9.5, 3.8, 7, True, BLEU_NUIT, Qt.AlignHCenter)

    # Échelles graphique et numérique
    cadre(19.5, 163.5, 50, 14.5, "255,255,255,215", FILET, 0.15)
    barre = QgsLayoutItemScaleBar(mise)
    barre.setLinkedMap(m)
    barre.setStyle("Single Box")
    barre.setUnits(QgsUnitTypes.DistanceKilometers)
    barre.setUnitLabel("km")
    barre.setNumberOfSegments(2)
    barre.setNumberOfSegmentsLeft(0)
    barre.setSegmentSizeMode(QgsScaleBarSettings.SegmentSizeFitWidth)
    barre.setMinimumBarWidth(28)
    barre.setMaximumBarWidth(40)
    barre.setHeight(1.6)
    barre.setTextFormat(format_texte(7))
    barre.setBackgroundEnabled(False)
    barre.attemptMove(QgsLayoutPoint(21.5, 164.5))
    mise.addLayoutItem(barre)
    numerique = QgsLayoutItemScaleBar(mise)
    numerique.setLinkedMap(m)
    numerique.setStyle("Numeric")
    numerique.setTextFormat(format_texte(7))
    numerique.setBackgroundEnabled(False)
    numerique.attemptMove(QgsLayoutPoint(21.5, 172.5))
    mise.addLayoutItem(numerique)

    # Colonne de droite
    X, L = 206, 81
    texte("SITUATION", X, 28, L, 4, 7, True, BLEU_NUIT)
    situation = QgsLayoutItemMap(mise)
    situation.attemptMove(QgsLayoutPoint(X, 33))
    situation.attemptResize(QgsLayoutSize(L, 42))
    situation.setFrameEnabled(True)
    situation.setFrameStrokeColor(QColor(GRIS))
    situation.setBackgroundColor(QColor("#e8f1f8"))
    situation.setLayers([zone_etude, terre])
    mise.addLayoutItem(situation)
    etendue = QgsCoordinateTransform(zone_etude.crs(), projet.crs(), projet).transformBoundingBox(zone_etude.extent())
    etendue.grow(8000)
    situation.zoomToExtent(etendue)
    apercu = situation.overview()
    apercu.setLinkedMap(m)
    apercu.setFrameSymbol(QgsFillSymbol.createSimple({"color": "209,73,91,60", "outline_color": ROUGE,
                                                      "outline_width": "0.5"}))

    texte("CHIFFRES CLÉS", X, 79, L, 4, 7, True, BLEU_NUIT)
    for k, (valeur, libelle) in enumerate(chiffres):
        x = X + k * 27.5
        cadre(x, 84, 26, 17, "#eef3f8", FILET, 0.15)
        texte(str(valeur), x, 85, 26, 9, 15, True, ROUGE if k == len(chiffres) - 1 else BLEU_NUIT, Qt.AlignHCenter)
        texte(libelle, x, 94, 26, 5, 7, couleur=GRIS, alignement=Qt.AlignHCenter)

    leg = QgsLayoutItemLegend(mise)
    leg.setLinkedMap(m)
    leg.setTitle("LÉGENDE")
    leg.setAutoUpdateModel(False)
    modele = leg.model().rootGroup()
    noeud = modele.findLayer(zone_etude.id())
    noeud.parent().removeChildNode(noeud)
    # pas de filtre « selon le contenu de la carte » : il bloque l'export image hors atlas
    leg.setLegendFilterByMapEnabled(False)
    for style, taille, gras, couleur in ((QgsLegendStyle.Title, 7, True, BLEU_NUIT),
                                         (QgsLegendStyle.Subgroup, 7, True, "#1d2733"),
                                         (QgsLegendStyle.SymbolLabel, 6.5, False, "#1d2733")):
        leg.rstyle(style).setTextFormat(format_texte(taille, gras, couleur))
    leg.rstyle(QgsLegendStyle.Subgroup).setMargin(QgsLegendStyle.Top, 1.5)
    leg.setSymbolHeight(3)
    leg.setSymbolWidth(5.5)
    leg.setBoxSpace(0)
    leg.setBackgroundEnabled(False)
    leg.attemptMove(QgsLayoutPoint(X, 105))
    mise.addLayoutItem(leg)

    # Pied de page
    cadre(10, 189, 277, 0.3, FILET)
    texte(SOURCES, 10, 191, 190, 7, 6, couleur=GRIS_CLAIR)
    texte(config.MENTION, 10, 198, 190, 5, 6, True, "#a86500")
    texte("Réalisation : Jaurès Daa-Hingbanon · QGIS 3.34\nProduction automatisée (scripts/07_livraison.py)",
          206, 191, 81, 10, 6.5, couleur=GRIS, alignement=Qt.AlignRight)

    exp = QgsLayoutExporter(mise)
    exp.exportToPdf(str(sortie_pdf), QgsLayoutExporter.PdfExportSettings())
    if sortie_png:
        reglages = QgsLayoutExporter.ImageExportSettings()
        reglages.dpi = 110
        exp.exportToImage(str(sortie_png), reglages)
