-- Base de suivi de production (SQLite). Même modèle que sql/schema_postgis.sql.
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS clients (
    id          TEXT PRIMARY KEY,
    nom         TEXT NOT NULL,
    secteur     TEXT NOT NULL,
    contact     TEXT
);

CREATE TABLE IF NOT EXISTS demandes (
    id              TEXT PRIMARY KEY,               -- D001, D002...
    client_id       TEXT NOT NULL REFERENCES clients(id),
    intitule        TEXT NOT NULL,
    zone_wkt        TEXT NOT NULL,                  -- emprise demandée, WGS84
    date_debut      TEXT NOT NULL,                  -- UTC ISO 8601
    date_fin        TEXT NOT NULL,
    cibles          TEXT NOT NULL,                  -- types de navires d'intérêt
    formats         TEXT NOT NULL,                  -- ex. GPKG,CSV,KML,PDF
    max_acq_jour    INTEGER NOT NULL DEFAULT 2,
    creneau         TEXT,                           -- plage horaire UTC souhaitée, ex. 06:00-20:00
    priorite        INTEGER NOT NULL CHECK (priorite BETWEEN 1 AND 3),
    date_depot      TEXT NOT NULL,
    statut          TEXT NOT NULL DEFAULT 'deposee'
        CHECK (statut IN ('deposee','refusee','programmee','en_production','livree','cloturee')),
    motif           TEXT
);

CREATE TABLE IF NOT EXISTS passages (
    id          TEXT PRIMARY KEY,                   -- P20151101-01
    satellite   TEXT NOT NULL,
    debut_utc   TEXT NOT NULL,
    fin_utc     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS programmations (
    id          TEXT PRIMARY KEY,                   -- D001-P20151101-01
    demande_id  TEXT NOT NULL REFERENCES demandes(id),
    passage_id  TEXT NOT NULL REFERENCES passages(id),
    statut      TEXT NOT NULL DEFAULT 'programmee'
        CHECK (statut IN ('programmee','acquise','echec_acquisition','annulee')),
    UNIQUE (demande_id, passage_id)
);

CREATE TABLE IF NOT EXISTS lots (
    id                  TEXT PRIMARY KEY,           -- L-D001-P20151101-01
    programmation_id    TEXT NOT NULL REFERENCES programmations(id),
    fichier_brut        TEXT,
    date_reception      TEXT,                       -- horodatage simulé
    nb_brut             INTEGER,
    nb_valide           INTEGER,
    score_qc            REAL,
    verdict_qc          TEXT CHECK (verdict_qc IN ('qc_ok','qc_reserve','qc_ko')),
    version             INTEGER NOT NULL DEFAULT 1,
    statut              TEXT NOT NULL DEFAULT 'recu'
        CHECK (statut IN ('recu','mis_en_forme','qc_ok','qc_reserve','qc_ko','correle','livre')),
    date_maj            TEXT
);

CREATE TABLE IF NOT EXISTS livraisons (
    id              TEXT PRIMARY KEY,               -- LIV-D001-01
    demande_id      TEXT NOT NULL REFERENCES demandes(id),
    date_livraison  TEXT NOT NULL,
    archive         TEXT NOT NULL,
    sha256          TEXT NOT NULL,
    nb_lots         INTEGER NOT NULL,
    nb_detections   INTEGER NOT NULL,
    nb_sombres      INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS tickets (
    id              TEXT PRIMARY KEY,               -- T001
    demande_id      TEXT REFERENCES demandes(id),
    date_ouverture  TEXT NOT NULL,
    question        TEXT NOT NULL,
    categorie       TEXT NOT NULL,
    reponse         TEXT,
    escalade        TEXT,                           -- service sollicité si niveau 2
    statut          TEXT NOT NULL DEFAULT 'ouvert' CHECK (statut IN ('ouvert','resolu','escalade'))
);

CREATE TABLE IF NOT EXISTS journal (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    execution       TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S','now')),
    date_production TEXT,                           -- horodatage simulé de l'événement
    niveau          TEXT NOT NULL CHECK (niveau IN ('INFO','ALERTE','ERREUR')),
    etape           TEXT NOT NULL,
    objet           TEXT,
    message         TEXT NOT NULL
);

-- Historique automatique des changements de statut
CREATE TABLE IF NOT EXISTS historique_statuts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    objet       TEXT NOT NULL,
    objet_id    TEXT NOT NULL,
    ancien      TEXT,
    nouveau     TEXT NOT NULL,
    horodatage  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S','now'))
);

CREATE TRIGGER IF NOT EXISTS trg_demandes_statut AFTER UPDATE OF statut ON demandes
WHEN OLD.statut <> NEW.statut
BEGIN
    INSERT INTO historique_statuts (objet, objet_id, ancien, nouveau)
    VALUES ('demande', NEW.id, OLD.statut, NEW.statut);
END;

CREATE TRIGGER IF NOT EXISTS trg_programmations_statut AFTER UPDATE OF statut ON programmations
WHEN OLD.statut <> NEW.statut
BEGIN
    INSERT INTO historique_statuts (objet, objet_id, ancien, nouveau)
    VALUES ('programmation', NEW.id, OLD.statut, NEW.statut);
END;

CREATE TRIGGER IF NOT EXISTS trg_lots_statut AFTER UPDATE OF statut ON lots
WHEN OLD.statut <> NEW.statut
BEGIN
    INSERT INTO historique_statuts (objet, objet_id, ancien, nouveau)
    VALUES ('lot', NEW.id, OLD.statut, NEW.statut);
END;

-- Vue de pilotage : avancement de chaque demande
CREATE VIEW IF NOT EXISTS v_avancement AS
SELECT d.id AS demande, c.nom AS client, d.statut,
       COUNT(DISTINCT p.id)                                              AS nb_programmees,
       SUM(p.statut = 'acquise')                                         AS nb_acquises,
       SUM(p.statut = 'echec_acquisition')                               AS nb_echecs,
       COUNT(DISTINCT l.id)                                              AS nb_lots,
       COUNT(DISTINCT CASE WHEN l.verdict_qc IN ('qc_ok','qc_reserve') THEN l.id END) AS nb_lots_valides
FROM demandes d
JOIN clients c ON c.id = d.client_id
LEFT JOIN programmations p ON p.demande_id = d.id
LEFT JOIN lots l ON l.programmation_id = p.id
GROUP BY d.id;
