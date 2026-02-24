# services/notification.py

import os
import logging
import requests
from typing import Optional

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────
# EMAIL (via Resend)
# ─────────────────────────────────────────

def send_email(
    to: str | list[str],
    subject: str,
    body: str,
    from_name: str = "Kuria",
    reply_to: Optional[str] = None
) -> bool:
    """
    Envoie un email via Resend.

    to      : une adresse ou une liste d'adresses
    subject : le sujet
    body    : le corps en texte brut ou HTML
    """
    api_key = os.environ.get("RESEND_API_KEY", "")
    from_email = os.environ.get("RESEND_FROM_EMAIL", "kuria@kuria.ai")

    if not api_key:
        logger.error("RESEND_API_KEY non configuré")
        return False

    recipients = [to] if isinstance(to, str) else to

    payload = {
        "from": f"{from_name} <{from_email}>",
        "to": recipients,
        "subject": subject,
        "text": body if not body.strip().startswith("<") else None,
        "html": body if body.strip().startswith("<") else None
    }

    # Nettoyer les None
    payload = {k: v for k, v in payload.items() if v is not None}

    if reply_to:
        payload["reply_to"] = reply_to

    try:
        response = requests.post(
            "https://api.resend.com/emails",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json"
            },
            json=payload,
            timeout=15
        )
        response.raise_for_status()
        logger.info(f"Email envoyé à {recipients} — sujet : {subject}")
        return True

    except requests.RequestException as e:
        logger.error(f"Erreur envoi email : {e}")
        return False


# ─────────────────────────────────────────
# SLACK (via webhook)
# ─────────────────────────────────────────

def send_slack(
    message: str,
    webhook_url: Optional[str] = None,
    blocks: Optional[list] = None
) -> bool:
    """
    Envoie un message Slack via webhook.

    webhook_url : si None, utilise SLACK_WEBHOOK_URL de l'env
    blocks      : optionnel — Slack Block Kit pour les messages riches
    """
    url = webhook_url or os.environ.get("SLACK_WEBHOOK_URL", "")

    if not url:
        logger.warning("Slack webhook non configuré — notification ignorée")
        return False

    payload: dict = {}

    if blocks:
        payload["blocks"] = blocks
    else:
        payload["text"] = message

    try:
        response = requests.post(
            url,
            json=payload,
            timeout=10
        )
        response.raise_for_status()
        logger.info(f"Slack notification envoyée")
        return True

    except requests.RequestException as e:
        logger.error(f"Erreur Slack notification : {e}")
        return False


# ─────────────────────────────────────────
# FONCTIONS COMPOSÉES
# Pour les cas d'usage fréquents
# ─────────────────────────────────────────

def alert_ceo(
    company_id: str,
    subject: str,
    message: str,
    ceo_email: str,
    slack_webhook: Optional[str] = None,
    urgency: str = "normal"    # "normal" ou "urgent"
) -> None:
    """
    Alerte le CEO par email et optionnellement Slack.
    urgency="urgent" → Slack en plus de l'email.
    """
    send_email(to=ceo_email, subject=subject, body=message)

    if urgency == "urgent" and slack_webhook:
        emoji = "🚨" if urgency == "urgent" else "ℹ️"
        send_slack(
            message=f"{emoji} *{subject}*\n{message}",
            webhook_url=slack_webhook
        )


def notify_commercial(
    name: str,
    email: str,
    subject: str,
    message: str,
    slack_webhook: Optional[str] = None
) -> None:
    """
    Notifie un commercial par email + Slack si configuré.
    """
    send_email(to=email, subject=subject, body=message)

    if slack_webhook:
        send_slack(
            message=f"@{name} — {subject}\n{message}",
            webhook_url=slack_webhook
        )
