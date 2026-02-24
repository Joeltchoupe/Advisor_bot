# services/database.py

import os
from typing import Any, Optional
from supabase import create_client, Client
from models import Deal, Contact, Invoice, Task, Expense
from dataclasses import asdict
import logging

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────
# CONNEXION
# ─────────────────────────────────────────

def get_client() -> Client:
    url = os.environ["SUPABASE_URL"]
    key = os.environ["SUPABASE_SERVICE_KEY"]
    return create_client(url, key)


# ─────────────────────────────────────────
# ÉCRITURE
# ─────────────────────────────────────────

def save(table: str, data: Any) -> dict:
    """
    Sauvegarde un modèle dans Supabase.
    Upsert : insert si nouveau, update si existant.
    La clé de déduplication est (company_id, raw_id, connector_source).
    """
    client = get_client()

    if hasattr(data, '__dataclass_fields__'):
        record = _serialize(data)
    else:
        record = data

    result = (
        client.table(table)
        .upsert(record, on_conflict="company_id,raw_id,connector_source")
        .execute()
    )

    return result.data[0] if result.data else {}


def save_many(table: str, items: list) -> list:
    """
    Sauvegarde une liste de modèles.
    Plus efficace qu'appeler save() en boucle.
    """
    if not items:
        return []

    client = get_client()
    records = [_serialize(item) if hasattr(item, '__dataclass_fields__')
               else item for item in items]

    result = (
        client.table(table)
        .upsert(records, on_conflict="company_id,raw_id,connector_source")
        .execute()
    )

    return result.data or []


# ─────────────────────────────────────────
# LECTURE
# ─────────────────────────────────────────

def get(table: str, company_id: str, filters: Optional[dict] = None) -> list:
    """
    Récupère des enregistrements pour un client donné.
    filters : dict optionnel de conditions supplémentaires
              ex: {"status": "active", "owner_id": "abc"}
    """
    client = get_client()

    query = client.table(table).select("*").eq("company_id", company_id)

    if filters:
        for key, value in filters.items():
            query = query.eq(key, value)

    result = query.execute()
    return result.data or []


def get_one(table: str, company_id: str, record_id: str) -> Optional[dict]:
    """
    Récupère un enregistrement unique par son id Kuria.
    """
    client = get_client()

    result = (
        client.table(table)
        .select("*")
        .eq("company_id", company_id)
        .eq("id", record_id)
        .limit(1)
        .execute()
    )

    return result.data[0] if result.data else None


# ─────────────────────────────────────────
# ÉVÉNEMENTS (pour le router)
# ─────────────────────────────────────────

def publish_event(event_type: str, company_id: str, payload: dict) -> dict:
    """
    Publie un événement dans la table events.
    Le router lit cette table pour déclencher les actions inter-agents.
    """
    client = get_client()

    event = {
        "event_type": event_type,
        "company_id": company_id,
        "payload": payload,
        "processed": False,
        "created_at": datetime.utcnow().isoformat()
    }

    result = client.table("events").insert(event).execute()
    return result.data[0] if result.data else {}


def get_unprocessed_events(company_id: str) -> list:
    """
    Récupère les événements non traités pour un client.
    Utilisé par le router.
    """
    client = get_client()

    result = (
        client.table("events")
        .select("*")
        .eq("company_id", company_id)
        .eq("processed", False)
        .order("created_at")
        .execute()
    )

    return result.data or []


def mark_event_processed(event_id: str) -> None:
    client = get_client()
    client.table("events").update({"processed": True}).eq("id", event_id).execute()


# ─────────────────────────────────────────
# UTILITAIRE INTERNE
# ─────────────────────────────────────────

def _serialize(dataclass_instance) -> dict:
    """
    Convertit un dataclass en dict compatible JSON/Supabase.
    Gère les datetime → str et les Enum → valeur string.
    """
    from datetime import datetime
    from enum import Enum

    raw = asdict(dataclass_instance)

    def clean(obj):
        if isinstance(obj, datetime):
            return obj.isoformat()
        if isinstance(obj, Enum):
            return obj.value
        if isinstance(obj, dict):
            return {k: clean(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [clean(i) for i in obj]
        return obj

    return clean(raw)
