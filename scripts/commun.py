"""Outils partagés : base de suivi, journal de production, changements de statut."""
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

import config

SCHEMA = Path(__file__).parent / "sql" / "schema_production.sql"


def base_production():
    cx = sqlite3.connect(config.PRODUCTION_DB)
    cx.row_factory = sqlite3.Row
    cx.execute("PRAGMA foreign_keys = ON")
    cx.executescript(SCHEMA.read_text(encoding="utf-8"))
    return cx


def journal(cx, niveau, etape, message, objet=None, date_production=None):
    """Écrit dans la table journal et affiche la ligne (lue par l'astreinte)."""
    if isinstance(date_production, datetime):
        date_production = date_production.strftime("%Y-%m-%dT%H:%M:%S")
    cx.execute("INSERT INTO journal (date_production, niveau, etape, objet, message) VALUES (?,?,?,?,?)",
               (date_production, niveau, etape, objet, message))
    cx.commit()
    ligne = f"[{niveau:6}] {etape:<14} {objet or '':<24} {message}"
    print(ligne, file=sys.stderr if niveau != "INFO" else sys.stdout, flush=True)


def statut(cx, table, objet_id, nouveau, **autres):
    """Change le statut d'un objet (les déclencheurs SQL historisent le changement)."""
    champs = {"statut": nouveau, **autres}
    if table == "lots":
        champs.setdefault("date_maj", datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S"))
    affect = ", ".join(f"{k} = ?" for k in champs)
    cx.execute(f"UPDATE {table} SET {affect} WHERE id = ?", (*champs.values(), objet_id))
    cx.commit()


def iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")
