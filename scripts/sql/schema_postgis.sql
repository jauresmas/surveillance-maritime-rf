-- Schéma PostGIS de la production (même modèle que schema_production.sql pour SQLite).
CREATE EXTENSION IF NOT EXISTS postgis;
CREATE SCHEMA IF NOT EXISTS production_rf;
SET search_path = production_rf, public;

CREATE TABLE IF NOT EXISTS clients (
    id text PRIMARY KEY, nom text NOT NULL, secteur text NOT NULL, contact text);

CREATE TABLE IF NOT EXISTS demandes (
    id text PRIMARY KEY,
    client_id text NOT NULL REFERENCES clients(id),
    intitule text NOT NULL,
    zone_wkt text NOT NULL,
    zone geometry(Polygon, 4326) GENERATED ALWAYS AS (ST_GeomFromText(zone_wkt, 4326)) STORED,
    date_debut timestamptz NOT NULL,
    date_fin timestamptz NOT NULL,
    cibles text NOT NULL,
    formats text NOT NULL,
    max_acq_jour integer NOT NULL DEFAULT 2,
    creneau text,
    priorite integer NOT NULL CHECK (priorite BETWEEN 1 AND 3),
    date_depot date NOT NULL,
    statut text NOT NULL DEFAULT 'deposee'
        CHECK (statut IN ('deposee','refusee','programmee','en_production','livree','cloturee')),
    motif text);

CREATE TABLE IF NOT EXISTS passages (
    id text PRIMARY KEY, satellite text NOT NULL, debut_utc timestamptz NOT NULL, fin_utc timestamptz NOT NULL);

CREATE TABLE IF NOT EXISTS programmations (
    id text PRIMARY KEY,
    demande_id text NOT NULL REFERENCES demandes(id),
    passage_id text NOT NULL REFERENCES passages(id),
    statut text NOT NULL DEFAULT 'programmee'
        CHECK (statut IN ('programmee','acquise','echec_acquisition','annulee')),
    UNIQUE (demande_id, passage_id));

CREATE TABLE IF NOT EXISTS lots (
    id text PRIMARY KEY,
    programmation_id text NOT NULL REFERENCES programmations(id),
    fichier_brut text, date_reception timestamptz, nb_brut integer, nb_valide integer, score_qc real,
    version integer NOT NULL DEFAULT 1,
    statut text NOT NULL DEFAULT 'recu'
        CHECK (statut IN ('recu','mis_en_forme','qc_ok','qc_reserve','qc_ko','correle','livre')),
    date_maj timestamptz,
    verdict_qc text CHECK (verdict_qc IN ('qc_ok','qc_reserve','qc_ko')));

CREATE TABLE IF NOT EXISTS livraisons (
    id text PRIMARY KEY, demande_id text NOT NULL REFERENCES demandes(id), date_livraison timestamptz NOT NULL,
    archive text NOT NULL, sha256 char(64) NOT NULL, nb_lots integer NOT NULL, nb_detections integer NOT NULL,
    nb_sombres integer NOT NULL);

CREATE TABLE IF NOT EXISTS tickets (
    id text PRIMARY KEY, demande_id text REFERENCES demandes(id), date_ouverture timestamptz NOT NULL,
    question text NOT NULL, categorie text NOT NULL, reponse text, escalade text,
    statut text NOT NULL DEFAULT 'ouvert' CHECK (statut IN ('ouvert','resolu','escalade')));

CREATE TABLE IF NOT EXISTS journal (
    id bigint PRIMARY KEY, execution timestamptz NOT NULL, date_production timestamptz,
    niveau text NOT NULL CHECK (niveau IN ('INFO','ALERTE','ERREUR')), etape text NOT NULL, objet text,
    message text NOT NULL);

CREATE TABLE IF NOT EXISTS historique_statuts (
    id bigint PRIMARY KEY, objet text NOT NULL, objet_id text NOT NULL, ancien text, nouveau text NOT NULL,
    horodatage timestamptz NOT NULL DEFAULT now());

CREATE TABLE IF NOT EXISTS detections (
    id_detection text PRIMARY KEY,
    lot_id text NOT NULL REFERENCES lots(id),
    demande_id text NOT NULL REFERENCES demandes(id),
    horodatage timestamptz NOT NULL,
    correlation text NOT NULL,
    mmsi integer,
    nom_navire text, type_navire text, zone_maritime text, interdiction_peche text, interet text,
    demi_grand_axe_m real, demi_petit_axe_m real, orientation_deg real,
    geom geometry(Point, 4326) NOT NULL,
    -- ellipse à 95 % recalculée en base (UTM 30N), utile pour les requêtes spatiales
    ellipse geometry(Polygon, 32630) GENERATED ALWAYS AS (
        ST_Translate(
            ST_Rotate(ST_Scale(ST_Buffer(ST_MakePoint(0, 0), 1, 16), demi_petit_axe_m, demi_grand_axe_m),
                      -radians(orientation_deg)),
            ST_X(ST_Transform(geom, 32630)), ST_Y(ST_Transform(geom, 32630)))) STORED);
CREATE INDEX IF NOT EXISTS detections_geom_gix ON detections USING gist (geom);
CREATE INDEX IF NOT EXISTS detections_demande_idx ON detections (demande_id, horodatage);

-- Historique automatique des statuts (équivalent des déclencheurs SQLite)
CREATE OR REPLACE FUNCTION historiser_statut() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.statut IS DISTINCT FROM OLD.statut THEN
        INSERT INTO production_rf.historique_statuts (id, objet, objet_id, ancien, nouveau)
        VALUES ((SELECT coalesce(max(id), 0) + 1 FROM production_rf.historique_statuts),
                TG_ARGV[0], NEW.id, OLD.statut, NEW.statut);
    END IF;
    RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS trg_demandes_statut ON demandes;
CREATE TRIGGER trg_demandes_statut AFTER UPDATE OF statut ON demandes
    FOR EACH ROW EXECUTE FUNCTION historiser_statut('demande');
DROP TRIGGER IF EXISTS trg_lots_statut ON lots;
CREATE TRIGGER trg_lots_statut AFTER UPDATE OF statut ON lots
    FOR EACH ROW EXECUTE FUNCTION historiser_statut('lot');

-- Synthèse par demande, rafraîchie après chaque chargement
CREATE MATERIALIZED VIEW IF NOT EXISTS mv_synthese_demandes AS
SELECT d.id, c.nom AS client, d.statut, d.zone,
       count(det.*)                                         AS nb_detections,
       count(det.*) FILTER (WHERE det.mmsi IS NULL)         AS nb_sombres,
       count(det.*) FILTER (WHERE det.interet = 'haute')    AS nb_interet_haut
FROM demandes d
JOIN clients c ON c.id = d.client_id
LEFT JOIN detections det ON det.demande_id = d.id
GROUP BY d.id, c.nom;

CREATE OR REPLACE FUNCTION rafraichir() RETURNS void LANGUAGE sql AS
$$ REFRESH MATERIALIZED VIEW production_rf.mv_synthese_demandes $$;
