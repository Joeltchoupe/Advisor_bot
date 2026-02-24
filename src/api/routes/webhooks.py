# api/routes/webhooks.py

import hashlib
import hmac
import logging
import os
from typing import Optional

from fastapi import APIRouter, Request, HTTPException, BackgroundTasks
from fastapi.responses import JSONResponse

router = APIRouter()
logger = logging.getLogger(__name__)


# ─────────────────────────────────────────
# HUBSPOT WEBHOOK
# ─────────────────────────────────────────

@router.post("/hubspot")
async def hubspot_webhook(
    request: Request,
    background_tasks: BackgroundTasks
) -> JSONResponse:
    """
    Reçoit les webhooks HubSpot.

    Events gérés :
    → contact.creation  → scoring immédiat du lead
    → deal.creation     → initialisation du deal dans Supabase
    → deal.propertyChange (stage) → recalcul forecast

    HubSpot envoie un tableau d'events.
    On traite chaque event indépendamment.
    """
    # Vérification de la signature HubSpot
    body = await request.body()

    if not _verify_hubspot_signature(request, body):
        logger.warning("Webhook HubSpot — signature invalide")
        raise HTTPException(status_code=401, detail="Signature invalide")

    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Payload invalide")

    # HubSpot envoie une liste d'events
    events = payload if isinstance(payload, list) else [payload]

    # Répondre 200 immédiatement
    # Traitement en arrière-plan
    background_tasks.add_task(_process_hubspot_events, events)

    return JSONResponse(
        status_code=200,
        content={"received": len(events)}
    )


def _verify_hubspot_signature(request: Request, body: bytes) -> bool:
    """
    HubSpot signe les webhooks avec HMAC-SHA256.
    On vérifie avant de traiter.

    En développement : si le secret n'est pas configuré,
    on accepte tout (pour les tests).
    """
    secret = os.environ.get("HUBSPOT_WEBHOOK_SECRET", "")

    if not secret:
        logger.warning("HUBSPOT_WEBHOOK_SECRET non configuré — webhook accepté sans vérification")
        return True

    signature_header = request.headers.get("X-HubSpot-Signature-v3", "")
    if not signature_header:
        return False

    expected = hmac.new(
        secret.encode("utf-8"),
        body,
        hashlib.sha256
    ).hexdigest()

    return hmac.compare_digest(expected, signature_header)


def _process_hubspot_events(events: list) -> None:
    """
    Traite les events HubSpot en arrière-plan.
    Appelé par BackgroundTasks — pas de retour.
    """
    for event in events:
        event_type = event.get("subscriptionType", "")
        portal_id  = str(event.get("portalId", ""))
        object_id  = str(event.get("objectId", ""))

        # Retrouver le company_id Kuria depuis le portal HubSpot
        company_id = _get_company_id_from_portal(portal_id)
        if not company_id:
            logger.warning(
                f"Portal HubSpot inconnu : {portal_id}"
            )
            continue

        logger.info(
            f"[webhook/hubspot] {event_type} — "
            f"object {object_id} — company {company_id}"
        )

        try:
            if event_type == "contact.creation":
                _handle_new_contact(company_id, object_id, "hubspot")

            elif event_type == "deal.creation":
                _handle_new_deal(company_id, object_id, "hubspot")

            elif event_type == "deal.propertyChange":
                prop = event.get("propertyName", "")
                if prop == "dealstage":
                    _handle_deal_stage_change(company_id, object_id)

        except Exception as e:
            logger.error(
                f"[webhook/hubspot] Erreur {event_type} "
                f"object {object_id} : {e}"
            )


# ─────────────────────────────────────────
# PIPEDRIVE WEBHOOK
# ─────────────────────────────────────────

@router.post("/pipedrive")
async def pipedrive_webhook(
    request: Request,
    background_tasks: BackgroundTasks
) -> JSONResponse:
    """
    Reçoit les webhooks Pipedrive.

    Pipedrive envoie un event à la fois (pas un tableau).
    """
    body = await request.body()

    if not _verify_pipedrive_signature(request, body):
        raise HTTPException(status_code=401, detail="Signature invalide")

    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Payload invalide")

    background_tasks.add_task(_process_pipedrive_event, payload)

    return JSONResponse(status_code=200, content={"received": True})


def _verify_pipedrive_signature(request: Request, body: bytes) -> bool:
    """
    Pipedrive utilise HTTP Basic Auth pour sécuriser les webhooks.
    On vérifie le header Authorization.
    """
    expected_token = os.environ.get("PIPEDRIVE_WEBHOOK_TOKEN", "")
    if not expected_token:
        return True

    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Basic "):
        return False

    import base64
    try:
        decoded = base64.b64decode(
            auth_header.replace("Basic ", "")
        ).decode("utf-8")
        token = decoded.split(":")[1]
        return hmac.compare_digest(token, expected_token)
    except Exception:
        return False


def _process_pipedrive_event(payload: dict) -> None:
    event = payload.get("event", "")
    meta  = payload.get("meta", {})
    data  = payload.get("current", {})

    # Pipedrive n'a pas de portal_id
    # On identifie le client via son compte Pipedrive
    company_domain = meta.get("host", "")
    company_id = _get_company_id_from_domain(company_domain, "pipedrive")

    if not company_id:
        return

    object_id = str(data.get("id", ""))

    try:
        if event == "added.person":
            _handle_new_contact(company_id, object_id, "pipedrive")

        elif event == "added.deal":
            _handle_new_deal(company_id, object_id, "pipedrive")

        elif event == "updated.deal" and "stage_id" in (payload.get("previous") or {}):
            _handle_deal_stage_change(company_id, object_id)

    except Exception as e:
        logger.error(f"[webhook/pipedrive] Erreur {event} : {e}")


# ─────────────────────────────────────────
# SALESFORCE WEBHOOK
# Salesforce utilise des "Outbound Messages"
# Format XML (pas JSON)
# ─────────────────────────────────────────

@router.post("/salesforce")
async def salesforce_webhook(
    request: Request,
    background_tasks: BackgroundTasks
) -> str:
    """
    Reçoit les Outbound Messages Salesforce.
    Salesforce envoie du XML et attend une confirmation XML.
    """
    body = await request.body()

    try:
        sf_data = _parse_salesforce_xml(body)
    except Exception as e:
        logger.error(f"[webhook/salesforce] Erreur parsing XML : {e}")
        raise HTTPException(status_code=400, detail="XML invalide")

    if sf_data:
        background_tasks.add_task(_process_salesforce_message, sf_data)

    # Salesforce attend un ACK XML spécifique
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/">'
        '<soapenv:Body>'
        '<notificationsResponse xmlns="http://soap.sforce.com/2005/09/outbound">'
        '<Ack>true</Ack>'
        '</notificationsResponse>'
        '</soapenv:Body>'
        '</soapenv:Envelope>'
    )


def _parse_salesforce_xml(body: bytes) -> Optional[dict]:
    """Parse le XML Salesforce en dict simple."""
    import xml.etree.ElementTree as ET

    try:
        root = ET.fromstring(body)
        ns = {
            "sf": "http://soap.sforce.com/2005/09/outbound",
            "soapenv": "http://schemas.xmlsoap.org/soap/envelope/"
        }

        notifications = root.findall(".//sf:Notification", ns)
        if not notifications:
            return None

        first = notifications[0]
        sobject = first.find("sf:sObject", ns)

        if sobject is None:
            return None

        data = {}
        for child in sobject:
            tag = child.tag.split("}")[-1] if "}" in child.tag else child.tag
            data[tag] = child.text

        return {
            "object_type": sobject.get(
                "{http://www.w3.org/2001/XMLSchema-instance}type", ""
            ).split(":")[-1],
            "data": data
        }

    except Exception:
        return None


def _process_salesforce_message(sf_data: dict) -> None:
    object_type = sf_data.get("object_type", "")
    data        = sf_data.get("data", {})
    object_id   = data.get("Id", "")

    org_id     = data.get("OwnerId", "")[:15]
    company_id = _get_company_id_from_portal(org_id, source="salesforce")

    if not company_id or not object_id:
        return

    try:
        if "Contact" in object_type or "Lead" in object_type:
            _handle_new_contact(company_id, object_id, "salesforce")
        elif "Opportunity" in object_type:
            _handle_new_deal(company_id, object_id, "salesforce")
    except Exception as e:
        logger.error(f"[webhook/salesforce] Erreur : {e}")


# ─────────────────────────────────────────
# HANDLERS PARTAGÉS
# Appelés par tous les webhooks CRM
# ─────────────────────────────────────────

def _handle_new_contact(
    company_id: str, raw_id: str, source: str
) -> None:
    """
    Nouveau contact détecté.
    → Fetch depuis le CRM source
    → Sauvegarder dans Supabase
    → Scorer immédiatement si agent activé
    """
    from orchestrator.profile import get_agent_config, is_agent_enabled
    from services.database import save_many

    # Fetch le contact depuis le CRM
    connector = _get_crm_connector(company_id, source)
    if not connector or not connector.connect():
        return

    contacts = connector.fetch_contacts()
    matching = [c for c in contacts if c.raw_id == raw_id]

    if not matching:
        logger.warning(
            f"Contact {raw_id} introuvable dans {source}"
        )
        return

    # Sauvegarder
    save_many("contacts", matching)

    # Scorer si agent activé
    if not is_agent_enabled(company_id, "revenue_velocity"):
        return

    config = get_agent_config(company_id, "revenue_velocity")

    from agents.revenue_velocity import RevenueVelocityAgent
    agent = RevenueVelocityAgent(company_id, config)

    # On récupère les données depuis Supabase (vient d'être sauvegardé)
    from services.database import get
    won_deals = get("deals", company_id, {"status": "won"})
    contacts_to_score = get(
        "contacts", company_id, {"raw_id": raw_id}
    )

    if contacts_to_score:
        agent._score_leads(contacts_to_score, won_deals)
        logger.info(
            f"[webhook] Contact {raw_id} scoré immédiatement"
        )


def _handle_new_deal(
    company_id: str, raw_id: str, source: str
) -> None:
    """
    Nouveau deal détecté.
    → Fetch + sauvegarder dans Supabase
    → Le prochain run de Revenue Velocity l'intégrera
    """
    from services.database import save_many

    connector = _get_crm_connector(company_id, source)
    if not connector or not connector.connect():
        return

    deals = connector.fetch_deals()
    matching = [d for d in deals if d.raw_id == raw_id]

    if matching:
        save_many("deals", matching)
        logger.info(f"[webhook] Deal {raw_id} sauvegardé")


def _handle_deal_stage_change(
    company_id: str, raw_id: str
) -> None:
    """
    Un deal a changé de stage.
    → Publier un event pour que le router notifie Cash
    """
    from services.database import publish_event

    publish_event(
        event_type="deal_stage_changed",
        company_id=company_id,
        payload={"deal_raw_id": raw_id}
    )
    logger.info(
        f"[webhook] Deal stage change publié pour {raw_id}"
    )


# ─────────────────────────────────────────
# UTILITAIRES
# ─────────────────────────────────────────

def _get_company_id_from_portal(
    portal_id: str, source: str = "hubspot"
) -> Optional[str]:
    """
    Retrouve le company_id Kuria depuis l'identifiant
    du compte CRM source.

    Stocké dans la table "credentials" :
    credentials → {"portal_id": "123456", ...}
    """
    try:
        from services.database import get_client
        client = get_client()

        result = client.table("credentials").select(
            "company_id, credentials"
        ).eq("tool", source).execute()

        for row in (result.data or []):
            creds = row.get("credentials", {})
            stored_portal = str(
                creds.get("portal_id") or
                creds.get("realm_id") or
                creds.get("org_id") or ""
            )
            if stored_portal == portal_id:
                return row["company_id"]

    except Exception as e:
        logger.error(f"_get_company_id_from_portal : {e}")

    return None


def _get_company_id_from_domain(
    domain: str, source: str
) -> Optional[str]:
    """Variante pour Pipedrive qui identifie par domaine."""
    try:
        from services.database import get_client
        client = get_client()

        result = client.table("credentials").select(
            "company_id, credentials"
        ).eq("tool", source).execute()

        for row in (result.data or []):
            creds = row.get("credentials", {})
            if creds.get("domain", "") in domain:
                return row["company_id"]

    except Exception as e:
        logger.error(f"_get_company_id_from_domain : {e}")

    return None


def _get_crm_connector(company_id: str, source: str):
    """Instancie le connecteur CRM selon la source."""
    try:
        from services.database import get_client
        client = get_client()

        result = client.table("credentials").select(
            "credentials"
        ).eq("company_id", company_id).eq("tool", source).limit(1).execute()

        if not result.data:
            return None

        credentials = result.data[0]["credentials"]

        if source == "hubspot":
            from connectors.crm.hubspot import HubSpotConnector
            return HubSpotConnector(company_id, credentials)
        elif source == "salesforce":
            from connectors.crm.salesforce import SalesforceConnector
            return SalesforceConnector(company_id, credentials)
        elif source == "pipedrive":
            from connectors.crm.pipedrive import PipedriveConnector
            return PipedriveConnector(company_id, credentials)
        elif source == "zoho":
            from connectors.crm.zoho import ZohoConnector
            return ZohoConnector(company_id, credentials)

    except Exception as e:
        logger.error(f"_get_crm_connector : {e}")

    return None
