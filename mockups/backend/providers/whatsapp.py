"""
WhatsApp Cloud API (Meta direct).
Env vars requeridas:
  WHATSAPP_TOKEN            — permanent access token de Meta
  WHATSAPP_PHONE_NUMBER_ID  — Phone Number ID de la cuenta de negocio
  WHATSAPP_VERIFY_TOKEN     — token arbitrario para validar el webhook con Meta
"""

from __future__ import annotations

import os
from typing import Any, Dict, Optional

import httpx

_TOKEN = os.getenv("WHATSAPP_TOKEN", "")
_PHONE_ID = os.getenv("WHATSAPP_PHONE_NUMBER_ID", "")
_VERIFY_TOKEN = os.getenv("WHATSAPP_VERIFY_TOKEN", "plataforma-webhook-2026")
_API_URL = "https://graph.facebook.com/v19.0"


def is_configured() -> bool:
    return bool(_TOKEN and _PHONE_ID)


def send_text(to_phone: str, body: str) -> Dict[str, Any]:
    """Envía un mensaje de texto vía WhatsApp Cloud API.
    to_phone: número en formato internacional sin + (ej. 5491140000000).
    """
    if not is_configured():
        return {"ok": False, "error": "WhatsApp no configurado (faltan env vars)"}

    phone = to_phone.lstrip("+").replace(" ", "").replace("-", "")
    url = f"{_API_URL}/{_PHONE_ID}/messages"
    payload = {
        "messaging_product": "whatsapp",
        "to": phone,
        "type": "text",
        "text": {"body": body},
    }
    try:
        r = httpx.post(
            url,
            json=payload,
            headers={"Authorization": f"Bearer {_TOKEN}", "Content-Type": "application/json"},
            timeout=15,
        )
        data = r.json()
        if r.status_code == 200:
            return {"ok": True, "wa_message_id": data.get("messages", [{}])[0].get("id")}
        return {"ok": False, "error": data}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def verify_webhook(mode: str, token: str, challenge: str) -> Optional[str]:
    """Verifica el desafío GET de Meta al configurar el webhook.
    Devuelve el challenge si el token coincide, None si falla.
    """
    if mode == "subscribe" and token == _VERIFY_TOKEN:
        return challenge
    return None


def parse_webhook(body: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Extrae el mensaje relevante del payload JSON de Meta.
    Devuelve None si no hay mensaje de texto (ej. delivery receipts, etc.).
    """
    try:
        entry = body.get("entry", [])[0]
        change = entry.get("changes", [])[0]
        value = change.get("value", {})

        messages = value.get("messages", [])
        if not messages:
            return None  # evento de status, no mensaje

        msg = messages[0]
        if msg.get("type") != "text":
            return None  # imagen, audio, etc. — ignorar por ahora

        contacts = value.get("contacts", [])
        contact_info = contacts[0] if contacts else {}

        return {
            "wa_message_id": msg.get("id"),
            "from_phone": msg.get("from"),               # número del remitente
            "from_name": contact_info.get("profile", {}).get("name", ""),
            "text": msg.get("text", {}).get("body", ""),
            "timestamp": msg.get("timestamp"),
            "phone_number_id": value.get("metadata", {}).get("phone_number_id"),
        }
    except (IndexError, KeyError, TypeError):
        return None
