"""10. Projet QGIS de production (surveillance_maritime_rf.qgz, à la racine du projet).

- groupes Production (détections, ellipses, AIS sans détection, zones demandées) et Référentiel ;
- tables de suivi (demandes, lots, livraisons, tickets, journal, historique) ouvertes depuis la base ;
- contrôleur temporel : les détections s'animent au fil des passages ;
- mise en page « Atlas des demandes » : une page par demande livrée.
Chemins relatifs : le dossier du projet peut être copié ou déplacé.
"""
import os

import geopandas as gpd
import pandas as pd
from qgis.core import (Qgis, QgsCategorizedSymbolRenderer, QgsCoordinateReferenceSystem, QgsFillSymbol,
                       QgsLayoutItemLabel, QgsLayoutItemLegend, QgsLayoutItemMap, QgsLayoutItemScaleBar,
                       QgsLayoutPoint, QgsLayoutSize, QgsMarkerSymbol, QgsPalLayerSettings, QgsPrintLayout,
                       QgsProject, QgsRasterLayer, QgsRendererCategory, QgsRuleBasedRenderer, QgsTextFormat,
                       QgsUnitTypes, QgsVectorLayer, QgsVectorLayerSimpleLabeling, QgsLegendStyle,
                       QgsReferencedRectangle, QgsCoordinateTransform, QgsLayoutExporter,
                       QgsLayoutItemMapGrid, QgsLayoutItemPicture, QgsLayoutItemShape, QgsApplication,
                       QgsScaleBarSettings)
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QColor, QFont

import config
from carte_pdf import demarrer
from commun import base_production, journal

demarrer()
cx = base_production()

# Couche des zones demandées avec leur avancement (pour l'atlas et la consultation)
dem = pd.read_sql("""SELECT d.id, d.intitule, c.nom AS client, d.statut, d.date_debut, d.date_fin, d.cibles,
                     d.priorite, d.creneau, d.motif, d.zone_wkt, a.nb_programmees, a.nb_acquises, a.nb_echecs,
                     a.nb_lots_valides, l.nb_detections, l.nb_sombres, l.archive
                     FROM demandes d JOIN clients c ON c.id = d.client_id
                     JOIN v_avancement a ON a.demande = d.id
                     LEFT JOIN livraisons l ON l.demande_id = d.id ORDER BY d.id""", cx)
suivi = config.PRODUITES / "suivi_production.gpkg"
if suivi.exists():
    suivi.unlink()
gpd.GeoDataFrame(dem.drop(columns="zone_wkt"), geometry=gpd.GeoSeries.from_wkt(dem.zone_wkt),
                 crs=config.CRS_GEO).to_file(suivi, layer="zones_demandees")

projet = QgsProject.instance()
projet.clear()
projet.setTitle("Surveillance maritime RF : production")
projet.setCrs(QgsCoordinateReferenceSystem(config.CRS_METRE))
racine = projet.layerTreeRoot()


def vecteur(chemin, couche, titre):
    c = QgsVectorLayer(f"{chemin}|layername={couche}", titre, "ogr")
    if not c.isValid():
        raise RuntimeError(f"couche invalide : {chemin} / {couche}")
    projet.addMapLayer(c, False)
    return c


def remplissage(fond, contour, opacite=1.0, largeur=0.3, style="solid"):
    s = QgsFillSymbol.createSimple({"color": fond, "outline_color": contour, "outline_width": str(largeur),
                                    "style": style})
    s.setOpacity(opacite)
    return s


DET = config.PRODUITES / "detections_correlees.gpkg"
REF = config.REFERENTIEL

# --- Production ---------------------------------------------------------------------------------
g_prod = racine.addGroup("Production")
det = vecteur(DET, "detections", "Détections RF")
FILTRE_ATLAS = "(@atlas_feature IS NULL OR \"demande_id\" = attribute(@atlas_feature, 'id'))"
regles = QgsRuleBasedRenderer.Rule(None)
for libelle, expr, props in [
    ("Navire sombre potentiel (sans AIS)", "\"mmsi\" IS NULL",
     {"name": "triangle", "color": "#d1495b", "size": "3.2", "outline_color": "#4a0d16", "outline_width": "0.3"}),
    ("Corrélée à un navire AIS", "\"mmsi\" IS NOT NULL",
     {"name": "circle", "color": "#2b6cb0", "size": "1.8", "outline_color": "white", "outline_width": "0.2"})]:
    r = QgsRuleBasedRenderer.Rule(QgsMarkerSymbol.createSimple(props), 0, 0, f"{expr} AND {FILTRE_ATLAS}", libelle)
    regles.appendChild(r)
det.setRenderer(QgsRuleBasedRenderer(regles))
etiq = QgsPalLayerSettings()
etiq.fieldName = "coalesce(\"ais_nom\", 'Sans AIS')"
etiq.isExpression = True
fmt = QgsTextFormat()
fmt.setFont(QFont("Segoe UI", 7))
fmt.setColor(QColor("#1d2733"))
etiq.setFormat(fmt)
etiq.scaleVisibility = True
etiq.maximumScale = 1
etiq.minimumScale = 60000
det.setLabeling(QgsVectorLayerSimpleLabeling(etiq))
det.setLabelsEnabled(True)
# Animation : chaque détection est visible 10 minutes à partir de son horodatage
tp = det.temporalProperties()
tp.setIsActive(True)
tp.setMode(Qgis.VectorTemporalMode.FeatureDateTimeStartAndEndFromExpressions)
tp.setStartExpression('to_datetime("horodatage")')
tp.setEndExpression('to_datetime("horodatage") + make_interval(minutes:=10)')

ell = vecteur(DET, "ellipses_95", "Ellipses d'incertitude (95 %)")
# lot_id = L-<demande>-<passage> : même filtre d'atlas que les détections
FILTRE_LOT = "(@atlas_feature IS NULL OR \"lot_id\" LIKE 'L-' || attribute(@atlas_feature, 'id') || '-%')"
r_ell = QgsRuleBasedRenderer.Rule(None)
for libelle, expr, symbole in [
        ("Ellipse, navire sombre", "\"mmsi\" IS NULL", remplissage("#d1495b", "#d1495b", 0.18, 0.15)),
        ("Ellipse, corrélée AIS", "\"mmsi\" IS NOT NULL", remplissage("#2b6cb0", "#2b6cb0", 0.10, 0.1))]:
    r_ell.appendChild(QgsRuleBasedRenderer.Rule(symbole, 0, 0, f"{expr} AND {FILTRE_LOT}", libelle))
ell.setRenderer(QgsRuleBasedRenderer(r_ell))
ais_nd = vecteur(DET, "ais_sans_detection_rf", "Navires AIS sans détection RF")
ais_nd.renderer().setSymbol(QgsMarkerSymbol.createSimple({"name": "circle", "color": "0,0,0,0",
                                                          "outline_color": "#5b6775", "outline_width": "0.3",
                                                          "size": "1.6"}))
zones = vecteur(suivi, "zones_demandees", "Zones demandées")
FILTRE_ZONE = "(@atlas_feature IS NULL OR \"id\" = attribute(@atlas_feature, 'id'))"
r_zone = QgsRuleBasedRenderer.Rule(None)
for libelle, expr, symbole in [
        ("Demande livrée", "\"statut\" = 'livree'", remplissage("0,0,0,0", "#1d2733", largeur=0.6, style="no")),
        ("Demande refusée", "\"statut\" = 'refusee'", remplissage("0,0,0,0", "#b3261e", largeur=0.5, style="no"))]:
    r_zone.appendChild(QgsRuleBasedRenderer.Rule(symbole, 0, 0, f"{expr} AND {FILTRE_ZONE}", libelle))
zones.setRenderer(QgsRuleBasedRenderer(r_zone))
zl = QgsPalLayerSettings()
zl.fieldName = "id"
zf = QgsTextFormat()
zf.setFont(QFont("Segoe UI", 9, QFont.Bold))
zl.setFormat(zf)
zl.placement = Qgis.LabelPlacement.Horizontal
zones.setLabeling(QgsVectorLayerSimpleLabeling(zl))
zones.setLabelsEnabled(True)
for c in (det, zones, ell, ais_nd):
    g_prod.addLayer(c)
racine.findLayer(ais_nd.id()).setItemVisibilityChecked(False)

# --- Référentiel ------------------------------------------------------------------------------
g_ref = racine.addGroup("Référentiel")
ports = vecteur(REF, "ports", "Ports de Bretagne")
ports.renderer().setSymbol(QgsMarkerSymbol.createSimple({"name": "square", "color": "#3d4652", "size": "1.6",
                                                         "outline_style": "no"}))
peche = vecteur(REF, "interdictions_peche", "Interdictions de pêche (datées)")
peche.renderer().setSymbol(remplissage("#c0392b", "#c0392b", 0.3, 0.4))
mer = vecteur(REF, "zones_maritimes", "Mer territoriale et zone contiguë")
mer.setRenderer(QgsCategorizedSymbolRenderer("code", [
    QgsRendererCategory("MT", remplissage("#e8f1f8", "#7c8a99", 0.6, 0.25), "Mer territoriale (0-12 M)"),
    QgsRendererCategory("ZC", remplissage("#f3f7fb", "#7c8a99", 0.6, 0.25), "Zone contiguë (12-24 M)"),
    QgsRendererCategory("ZEE", remplissage("#fbfdff", "#7c8a99", 0.6, 0.25), "Au-delà de 24 M")]))
couv = vecteur(REF, "couverture_recepteur", "Couverture du récepteur AIS")
couv.renderer().setSymbol(remplissage("0,0,0,0", "#7c8a99", largeur=0.4, style="no"))
couv.renderer().symbol().symbolLayer(0).setStrokeStyle(Qt.DashLine)
terre = vecteur(REF, "terre", "Terre")
terre.renderer().setSymbol(remplissage("#e4e1da", "#b9b4a8", largeur=0.15))
zone_etude = vecteur(REF, "zone_etude", "Zone d'étude")
zone_etude.renderer().setSymbol(remplissage("0,0,0,0", "#9aa6b2", largeur=0.3, style="no"))
for c in (ports, terre, peche, couv, zone_etude, mer):
    g_ref.addLayer(c)
racine.findLayer(couv.id()).setItemVisibilityChecked(False)

fond = QgsRasterLayer("type=xyz&zmin=0&zmax=13&url=https://server.arcgisonline.com/ArcGIS/rest/services/"
                      "Ocean/World_Ocean_Base/MapServer/tile/%7Bz%7D/%7By%7D/%7Bx%7D", "Fond océan (Esri)", "wms")
projet.addMapLayer(fond, False)
racine.addLayer(fond).setItemVisibilityChecked(False)

# --- Tables de suivi de production -------------------------------------------------------------
g_tab = racine.addGroup("Suivi de production (tables)")
for table, titre in [("demandes", "Demandes"), ("programmations", "Programmations"), ("passages", "Passages"),
                     ("lots", "Lots"), ("livraisons", "Livraisons"), ("tickets", "Tickets support"),
                     ("journal", "Journal d'astreinte"), ("historique_statuts", "Historique des statuts"),
                     ("v_avancement", "Avancement (vue)")]:
    t = QgsVectorLayer(f"{config.PRODUCTION_DB}|layername={table}", titre, "ogr")
    if t.isValid():
        projet.addMapLayer(t, False)
        g_tab.addLayer(t)
g_tab.setExpanded(False)

# --- Atlas des demandes livrées -------------------------------------------------------------------
BLEU_NUIT, GRIS, GRIS_CLAIR, FILET = "#1f3a5f", "#4f5b67", "#6b7785", "#d4dae1"
mise = QgsPrintLayout(projet)
mise.initializeDefaults()
mise.setName("Atlas des demandes")
page = mise.pageCollection().page(0)
page.setPageSize("A4", page.Landscape)
atlas = mise.atlas()
atlas.setCoverageLayer(zones)
atlas.setFilterFeatures(True)
atlas.setFilterExpression("\"statut\" = 'livree'")
atlas.setPageNameExpression("\"id\"")
atlas.setEnabled(True)


def texte(t, x, y, w, h, taille=9, gras=False, couleur="#1d2733", alignement=Qt.AlignLeft):
    e = QgsLayoutItemLabel(mise)
    e.setText(t)
    tf = QgsTextFormat()
    f = QFont("Segoe UI")
    f.setBold(gras)
    tf.setFont(f)
    tf.setSize(taille)
    tf.setColor(QColor(couleur))
    e.setTextFormat(tf)
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
    return r


# Bandeau de titre
cadre(0, 0, 297, 22, BLEU_NUIT)
texte("[% \"id\" %] · [% \"intitule\" %]", 10, 3, 225, 10, 15, True, "white")
texte("[% \"client\" %]  ·  du [% format_date(to_date(left(\"date_debut\", 10)), 'dd/MM/yyyy') %] au "
      "[% format_date(to_date(left(\"date_fin\", 10)), 'dd/MM/yyyy') %]  ·  cibles : [% \"cibles\" %]",
      10, 12.5, 225, 6, 9, couleur="#c9d6e5")
texte("ATLAS DES DEMANDES", 237, 4, 50, 6, 7, True, "#c9d6e5", Qt.AlignRight)
texte("page [% @atlas_featurenumber %] / [% @atlas_totalfeatures %]", 237, 11, 50, 6, 9, couleur="white",
      alignement=Qt.AlignRight)

# Carte principale avec carroyage en degrés
carte = QgsLayoutItemMap(mise)
carte.attemptMove(QgsLayoutPoint(17, 30))
carte.attemptResize(QgsLayoutSize(182, 150))
carte.setFrameEnabled(True)
carte.setFrameStrokeColor(QColor(GRIS))
carte.setBackgroundColor(QColor("#f4f8fb"))
mise.addLayoutItem(carte)
carte.zoomToExtent(zones.extent())
carte.setAtlasDriven(True)
# échelles rondes : l'atlas choisit la plus petite qui contient la demande
ECHELLES = [50000, 75000, 100000, 150000, 200000, 250000, 300000, 400000, 500000, 750000]
mise.renderContext().setPredefinedScales(ECHELLES)
projet.viewSettings().setMapScales(ECHELLES)
projet.viewSettings().setUseProjectScales(True)
carte.setAtlasScalingMode(QgsLayoutItemMap.Predefined)
grille = QgsLayoutItemMapGrid("Carroyage WGS84", carte)
carte.grids().addGrid(grille)
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
fa = QgsTextFormat()
fa.setFont(QFont("Segoe UI"))
fa.setSize(6)
fa.setColor(QColor(GRIS))
grille.setAnnotationTextFormat(fa)
for bord, sens in ((QgsLayoutItemMapGrid.Left, QgsLayoutItemMapGrid.Vertical),
                   (QgsLayoutItemMapGrid.Bottom, QgsLayoutItemMapGrid.Horizontal)):
    grille.setAnnotationPosition(QgsLayoutItemMapGrid.OutsideMapFrame, bord)
    grille.setAnnotationDirection(sens, bord)
for bord in (QgsLayoutItemMapGrid.Right, QgsLayoutItemMapGrid.Top):
    grille.setAnnotationDisplay(QgsLayoutItemMapGrid.HideAll, bord)

# Flèche du nord, sur fond blanc, coin haut droit de la carte
cadre(187.5, 32.5, 9.5, 14, "255,255,255,215", FILET, 0.15)
nord = QgsLayoutItemPicture(mise)
nord.setPicturePath(os.path.join(QgsApplication.svgPaths()[0], "arrows", "NorthArrow_11.svg"))
nord.setLinkedMap(carte)
nord.attemptMove(QgsLayoutPoint(188.75, 33.2))
nord.attemptResize(QgsLayoutSize(7, 9))
mise.addLayoutItem(nord)
texte("N", 187.5, 42.2, 9.5, 3.8, 7, True, BLEU_NUIT, Qt.AlignHCenter)

# Échelle graphique et numérique, sur fond blanc, coin bas gauche
fb = QgsTextFormat()
fb.setFont(QFont("Segoe UI"))
fb.setSize(7)
cadre(19.5, 163.5, 50, 14.5, "255,255,255,215", FILET, 0.15)
barre = QgsLayoutItemScaleBar(mise)
barre.setLinkedMap(carte)
barre.setStyle("Single Box")
barre.setUnits(QgsUnitTypes.DistanceKilometers)
barre.setUnitLabel("km")
barre.setNumberOfSegments(2)
barre.setNumberOfSegmentsLeft(0)
barre.setSegmentSizeMode(QgsScaleBarSettings.SegmentSizeFitWidth)
barre.setMinimumBarWidth(28)
barre.setMaximumBarWidth(40)
barre.setHeight(1.6)
barre.setTextFormat(fb)
barre.setBackgroundEnabled(False)
barre.attemptMove(QgsLayoutPoint(21.5, 164.5))
mise.addLayoutItem(barre)
numerique = QgsLayoutItemScaleBar(mise)
numerique.setLinkedMap(carte)
numerique.setStyle("Numeric")
numerique.setTextFormat(fb)
numerique.setBackgroundEnabled(False)
numerique.attemptMove(QgsLayoutPoint(21.5, 172.5))
mise.addLayoutItem(numerique)

# Colonne de droite : situation, chiffres clés, légende
X, L = 206, 81
texte("SITUATION", X, 28, L, 4, 7, True, BLEU_NUIT)
situation = QgsLayoutItemMap(mise)
situation.attemptMove(QgsLayoutPoint(X, 33))
situation.attemptResize(QgsLayoutSize(L, 42))
situation.setFrameEnabled(True)
situation.setFrameStrokeColor(QColor(GRIS))
situation.setBackgroundColor(QColor("#e8f1f8"))
situation.setLayers([zone_etude, terre])
etendue = QgsCoordinateTransform(zone_etude.crs(), projet.crs(), projet).transformBoundingBox(zone_etude.extent())
etendue.grow(8000)
mise.addLayoutItem(situation)
situation.zoomToExtent(etendue)
apercu = situation.overview()
apercu.setLinkedMap(carte)
apercu.setFrameSymbol(QgsFillSymbol.createSimple({"color": "209,73,91,60", "outline_color": "#d1495b",
                                                  "outline_width": "0.5"}))

texte("CHIFFRES CLÉS", X, 79, L, 4, 7, True, BLEU_NUIT)
for k, (expr, libelle) in enumerate([("\"nb_acquises\"", "acquisitions"), ("\"nb_detections\"", "détections"),
                                     ("\"nb_sombres\"", "sans AIS")]):
    x = X + k * 27.5
    cadre(x, 84, 26, 17, "#eef3f8", FILET, 0.15)
    texte(f"[% {expr} %]", x, 85, 26, 9, 15, True, "#d1495b" if k == 2 else BLEU_NUIT, Qt.AlignHCenter)
    texte(libelle, x, 94, 26, 5, 7, couleur=GRIS, alignement=Qt.AlignHCenter)

leg = QgsLayoutItemLegend(mise)
leg.setLinkedMap(carte)
leg.setTitle("LÉGENDE")
leg.setAutoUpdateModel(False)
modele = leg.model().rootGroup()
for n in list(modele.children()):
    if n.name() in ("Suivi de production (tables)", "Fond océan (Esri)"):
        modele.removeChildNode(n)
for c in (zone_etude, mer, couv, ais_nd):
    noeud = modele.findLayer(c.id())
    noeud.parent().removeChildNode(noeud)
leg.setLegendFilterByMapEnabled(True)   # seulement ce qui est dessiné sur la page
for style, taille, gras, couleur in ((QgsLegendStyle.Title, 7, True, BLEU_NUIT),
                                     (QgsLegendStyle.Group, 7.5, True, GRIS),
                                     (QgsLegendStyle.Subgroup, 7, True, "#1d2733"),
                                     (QgsLegendStyle.SymbolLabel, 6.5, False, "#1d2733")):
    tf = QgsTextFormat()
    f = QFont("Segoe UI")
    f.setBold(gras)
    tf.setFont(f)
    tf.setSize(taille)
    tf.setColor(QColor(couleur))
    leg.rstyle(style).setTextFormat(tf)
leg.rstyle(QgsLegendStyle.Group).setMargin(QgsLegendStyle.Top, 2.5)
leg.rstyle(QgsLegendStyle.Subgroup).setMargin(QgsLegendStyle.Top, 1.5)
leg.setSymbolHeight(3)
leg.setSymbolWidth(5.5)
leg.setBoxSpace(0)
leg.setBackgroundEnabled(False)
leg.attemptMove(QgsLayoutPoint(X, 105))
mise.addLayoutItem(leg)

# Pied de page
cadre(10, 189, 277, 0.3, FILET)
texte("Sources : AIS École navale (Zenodo 10.5281/zenodo.1167595, CC BY-NC-SA 4.0) ; côte AEE ; ports Région "
      "Bretagne. Limites 12/24 M calculées depuis le trait de côte. Projection UTM 30N (EPSG:32630), carroyage WGS84.",
      10, 191, 190, 7, 6, couleur=GRIS_CLAIR)
texte(config.MENTION, 10, 198, 190, 5, 6, True, "#a86500")
texte("Réalisation : Jaurès Daa-Hingbanon · QGIS 3.34\nÉdité le [% format_date(now(), 'dd/MM/yyyy') %]",
      206, 191, 81, 10, 6.5, couleur=GRIS, alignement=Qt.AlignRight)
projet.layoutManager().addLayout(mise)

# Vue initiale sur la zone d'étude
vue = projet.viewSettings()
tr = QgsCoordinateTransform(zone_etude.crs(), projet.crs(), projet)
vue.setDefaultViewExtent(QgsReferencedRectangle(tr.transformBoundingBox(zone_etude.extent()), projet.crs()))

# Atlas complet en PDF (une page par demande livrée)
QgsLayoutExporter.exportToPdf(atlas, str(config.SORTIES / "atlas_demandes.pdf"), QgsLayoutExporter.PdfExportSettings())

# Capture de la première page de l'atlas pour le README
atlas.beginRender()
atlas.first()
reglages = QgsLayoutExporter.ImageExportSettings()
reglages.dpi = 110
QgsLayoutExporter(mise).exportToImage(str(config.SORTIES / "captures" / "atlas_page1.png"), reglages)
atlas.endRender()

sortie = config.RACINE / "surveillance_maritime_rf.qgz"
projet.write(str(sortie))
journal(cx, "INFO", "projet_qgis", f"projet QGIS écrit : {sortie.name} ({len(projet.mapLayers())} couches, atlas des demandes)")
