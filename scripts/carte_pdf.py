"""Carte de synthèse d'une livraison (mise en page QGIS, export PDF)."""
import os

from qgis.core import (QgsApplication, QgsCategorizedSymbolRenderer, QgsCoordinateReferenceSystem,
                       QgsCoordinateTransform, QgsFillSymbol, QgsLayoutExporter, QgsLayoutItemLabel,
                       QgsLayoutItemLegend, QgsLayoutItemMap, QgsLayoutItemPicture, QgsLayoutItemScaleBar,
                       QgsLayoutPoint, QgsLayoutSize, QgsLegendStyle, QgsMarkerSymbol, QgsPrintLayout, QgsProject,
                       QgsRendererCategory, QgsUnitTypes, QgsVectorLayer)
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QColor, QFont

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


def carte(livrable, referentiel, sortie_pdf, titre, sous_titre, synthese, sources, sortie_png=None):
    demarrer()
    projet = QgsProject.instance()
    projet.clear()
    projet.setCrs(QgsCoordinateReferenceSystem("EPSG:32630"))

    terre = couche(referentiel, "terre", "Terre")
    terre.renderer().setSymbol(remplissage("#e4e1da", "#b9b4a8", largeur=0.15))
    zones = couche(referentiel, "zones_maritimes", "Limites 12 et 24 milles (depuis la côte)")
    zones.renderer().setSymbol(remplissage("0,0,0,0", "#7c8a99", largeur=0.25, style="no"))
    zones.renderer().symbol().symbolLayer(0).setStrokeStyle(Qt.DashLine)
    peche = couche(referentiel, "interdictions_peche", "Interdiction de pêche aux coquillages")
    peche.renderer().setSymbol(remplissage("#c0392b", "#c0392b", 0.25, 0.4))
    ports = couche(referentiel, "ports", "Ports")
    ports.renderer().setSymbol(QgsMarkerSymbol.createSimple({"name": "square", "color": "#3d4652", "size": "1.4",
                                                             "outline_style": "no"}))
    zone_dem = couche(livrable, "zone_demandee", "Zone demandée")
    zone_dem.renderer().setSymbol(remplissage("0,0,0,0", "#1d2733", largeur=0.6, style="no"))
    ell = couche(livrable, "ellipses_95", "Ellipse d'incertitude (95 %)")
    ell.renderer().setSymbol(remplissage("#8a6fbf", "#8a6fbf", 0.12, 0.1))
    det = couche(livrable, "detections", "Détections RF")
    cats = [("corrélée AIS", QgsMarkerSymbol.createSimple({"name": "circle", "color": "#2b6cb0", "size": "1.6",
                                                            "outline_color": "white", "outline_width": "0.2"}),
             "Corrélée à un navire AIS"),
            ("non corrélée : navire sombre potentiel",
             QgsMarkerSymbol.createSimple({"name": "triangle", "color": "#d1495b", "size": "3.0",
                                           "outline_color": "#4a0d16", "outline_width": "0.3"}),
             "Sans AIS : navire sombre potentiel")]
    det.setRenderer(QgsCategorizedSymbolRenderer("correlation", [QgsRendererCategory(v, s, l) for v, s, l in cats]))
    for c in (terre, zones, peche, ports, ell, zone_dem, det):
        projet.addMapLayer(c)

    mise = QgsPrintLayout(projet)
    mise.initializeDefaults()
    page = mise.pageCollection().page(0)
    page.setPageSize("A4", page.Landscape)

    def texte(t, x, y, w, h, taille=9, gras=False, couleur="#1d2733"):
        e = QgsLayoutItemLabel(mise)
        e.setText(t)
        f = QFont("Segoe UI", taille)
        f.setBold(gras)
        e.setFont(f)
        e.setFontColor(QColor(couleur))
        e.attemptMove(QgsLayoutPoint(x, y))
        e.attemptResize(QgsLayoutSize(w, h))
        mise.addLayoutItem(e)
        return e

    texte(titre, 10, 8, 270, 10, 16, True)
    texte(sous_titre, 10, 17, 270, 6, 9, couleur="#4f5b67")
    m = QgsLayoutItemMap(mise)
    m.attemptMove(QgsLayoutPoint(10, 25))
    m.attemptResize(QgsLayoutSize(190, 160))
    m.setLayers([det, zone_dem, ell, ports, terre, peche, zones])  # la terre masque la partie terrestre des zones
    m.setBackgroundColor(QColor("#f4f8fb"))
    m.setFrameEnabled(True)
    mise.addLayoutItem(m)
    emprise = projet.mapLayersByName("Zone demandée")[0]
    tr = QgsCoordinateTransform(emprise.crs(), projet.crs(), projet)
    r = tr.transformBoundingBox(emprise.extent())
    r.grow(3000)
    m.zoomToExtent(r)

    barre = QgsLayoutItemScaleBar(mise)
    barre.setLinkedMap(m)
    barre.setStyle("Single Box")
    barre.setUnits(QgsUnitTypes.DistanceKilometers)
    barre.setUnitLabel("km")
    barre.setNumberOfSegments(2)
    barre.setNumberOfSegmentsLeft(0)
    barre.setUnitsPerSegment(5)
    barre.attemptMove(QgsLayoutPoint(14, 175))
    mise.addLayoutItem(barre)
    nord = QgsLayoutItemPicture(mise)
    nord.setPicturePath(os.path.join(QgsApplication.svgPaths()[0], "arrows", "NorthArrow_02.svg"))
    nord.attemptMove(QgsLayoutPoint(188, 28))
    nord.attemptResize(QgsLayoutSize(9, 12))
    mise.addLayoutItem(nord)

    leg = QgsLayoutItemLegend(mise)
    leg.setLinkedMap(m)
    leg.setTitle("Légende")
    leg.setLegendFilterByMapEnabled(False)
    leg.attemptMove(QgsLayoutPoint(206, 25))
    leg.rstyle(QgsLegendStyle.Title).setFont(QFont("Segoe UI", 10, QFont.Bold))
    leg.rstyle(QgsLegendStyle.SymbolLabel).setFont(QFont("Segoe UI", 8))
    leg.setColumnSpace(0)
    leg.setWrapString("|")
    mise.addLayoutItem(leg)
    texte(synthese, 206, 100, 82, 62, 8)
    texte(sources, 206, 166, 82, 30, 6, couleur="#6b7785")
    exp = QgsLayoutExporter(mise)
    exp.exportToPdf(str(sortie_pdf), QgsLayoutExporter.PdfExportSettings())
    if sortie_png:
        reglages = QgsLayoutExporter.ImageExportSettings()
        reglages.dpi = 110
        exp.exportToImage(str(sortie_png), reglages)
