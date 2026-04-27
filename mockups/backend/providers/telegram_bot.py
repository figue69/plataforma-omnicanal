"""
Telegram Bot API — integración via webhooks (httpx, sin librería python-telegram-bot).

Cada canal Telegram es un bot independiente con su propio token.
Flujo:
  1. Admin crea canal tipo "telegram" con bot_token en /channels
  2. Admin llama POST /channels/{id}/telegram/register-webhook
     → registra la URL https://{PUBLIC_URL}/webhook/telegram/{channel_id}
  3. Telegram envía updates a esa URL
  4. El backend los procesa igual que mensajes WhatsApp/email

Env vars:
  PUBLIC_URL   (mismo que para Gmail — ej. https://plataforma.railway.app)
"""
from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

import httpx

PUBLIC_URL = os.getenv("PUBLIC_URL", "http://localhost:8000").strip().rstrip("/")
TIMEOUT    = 10.0


def _api(token: str, method: str) -> str:
    return f"https://api.telegram.org/bot{token}/{method}"


def is_token_valid(bot_token: str) -> Dict[str, Any]:
    """Verifica que el token sea válido llamando getMe."""
    try:
        r = httpx.get(_api(bot_token, "getMe"), timeout=TIMEOUT)
        data = r.json()
        if data.get("ok"):
            bot = data.get("result", {})
            return {
                "ok": True,
                "bot_id": bot.get("id"),
                "bot_username": bot.get("username"),
                "bot_name": bot.get("first_name"),
            }
        return {"ok": False, "error": data.get("description", "Token inválido")}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def register_webhook(bot_token: str, channel_id: str) -> Dict[str, Any]:
    """Registra el webhook de Telegram para este canal."""
    webhook_url = f"{PUBLIC_URL}/webhook/telegram/{channel_id}"
    try:
        r = httpx.post(_api(bot_token, "setWebhook"),
                       json={"url": webhook_url, "allowed_updates": ["message", "callback_query"]},
                       timeout=TIMEOUT)
        data = r.json()
        if data.get("ok"):
            return {"ok": True, "webhook_url": webhook_url, "description": data.get("description")}
        return {"ok": False, "error": data.get("description")}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def delete_webhook(bot_token: str) -> Dict[str, Any]:
    """Elimina el webhook (útil al desactivar un canal)."""
    try:
        r = httpx.post(_api(bot_token, "deleteWebhook"), timeout=TIMEOUT)
        return {"ok": r.json().get("ok", False)}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def get_webhook_info(bot_token: str) -> Dict[str, Any]:
    """Devuelve el estado actual del webhook registrado."""
    try:
        r = httpx.get(_api(bot_token, "getWebhookInfo"), timeout=TIMEOUT)
        data = r.json()
        return data.get("result", {})
    except Exception as exc:
        return {"error": str(exc)}


def parse_update(body: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    Parsea un update de Telegram y devuelve un dict normalizado:
      { tg_update_id, tg_chat_id, tg_user_id, from_name, from_username,
        text, ts, message_id }
    Retorna None para updates que no son mensajes de texto.
    """
    msg = body.get("message") or body.get("edited_message")
    if not msg:
        return None

    text = msg.get("text") or msg.get("caption")
    if not text:
        return None  # fotos, stickers, etc. sin caption — ignorar por ahora

    frm = msg.get("from") or {}
    chat = msg.get("chat") or {}
    first = frm.get("first_name", "")
    last  = frm.get("last_name", "")
    full_name = f"{first} {last}".strip() or frm.get("username", "Usuario Telegram")

    return {
        "tg_update_id":  body.get("update_id"),
        "tg_chat_id":    chat.get("id"),
        "tg_message_id": msg.get("message_id"),
        "tg_user_id":    frm.get("id"),
        "from_name":     full_name,
        "from_username": frm.get("username", ""),
        "text":          text,
        "ts":            str(msg.get("date", "")),
    }


def send_message(
    bot_token: str,
    chat_id: int,
    text: str,
    parse_mode: str = "Markdown",
    reply_to_message_id: Optional[int] = None,
) -> Dict[str, Any]:
    """Envía un mensaje a un chat de Telegram."""
    payload: Dict[str, Any] = {
        "chat_id": chat_id,
        "text": text[:4096],  # límite de Telegram
        "parse_mode": parse_mode,
    }
    if reply_to_message_id:
        payload["reply_to_message_id"] = reply_to_message_id

    try:
        r = httpx.post(_api(bot_token, "sendMessage"), json=payload, timeout=TIMEOUT)
        data = r.json()
        if data.get("ok"):
            return {"ok": True, "tg_message_id": data.get("result", {}).get("message_id")}
        return {"ok": False, "error": data.get("description")}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
