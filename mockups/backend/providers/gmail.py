"""
Gmail API — integración multi-casilla via OAuth2 (httpx, sin librerías Google).

Flujo de conexión de una casilla nueva:
  1. Admin abre GET /channels/{id}/gmail/auth  → redirige a Google OAuth consent
  2. Google redirige a GET /channels/gmail/callback?code=...&state={channel_id}
  3. Backend intercambia `code` por tokens y los persiste en channel.credentials

Polling de mensajes:
  POST /poll/gmail  (o cron externo)
  → Para cada canal Gmail activo: busca unread, parsea, procesa igual que WhatsApp

Env vars:
  GOOGLE_CLIENT_ID
  GOOGLE_CLIENT_SECRET
  PUBLIC_URL      (ej. https://plataforma.railway.app — para el redirect_uri)
"""
from __future__ import annotations

import base64
import email as email_lib
import os
import re
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any, Dict, List, Optional

import httpx

CLIENT_ID     = os.getenv("GOOGLE_CLIENT_ID", "").strip()
CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "").strip()
PUBLIC_URL    = os.getenv("PUBLIC_URL", "http://localhost:8000").strip().rstrip("/")

SCOPES = " ".join([
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/userinfo.email",
])
REDIRECT_URI  = f"{PUBLIC_URL}/channels/gmail/callback"
TOKEN_URL     = "https://oauth2.googleapis.com/token"
GMAIL_API     = "https://gmail.googleapis.com/gmail/v1/users/me"
TIMEOUT       = 15.0


def is_configured() -> bool:
    return bool(CLIENT_ID and CLIENT_SECRET)


# ── OAuth2 ────────────────────────────────────────────────────────────────────

def get_auth_url(channel_id: str) -> str:
    """Genera la URL de consent de Google OAuth2."""
    params = {
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "response_type": "code",
        "scope": SCOPES,
        "access_type": "offline",
        "prompt": "consent",
        "state": channel_id,
    }
    qs = "&".join(f"{k}={httpx.QueryParams({k: v})[k]}" for k, v in params.items())
    return f"https://accounts.google.com/o/oauth2/v2/auth?{qs}"


def exchange_code(code: str) -> Dict[str, Any]:
    """Intercambia el authorization code por access_token + refresh_token."""
    r = httpx.post(TOKEN_URL, data={
        "code": code,
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "redirect_uri": REDIRECT_URI,
        "grant_type": "authorization_code",
    }, timeout=TIMEOUT)
    r.raise_for_status()
    return r.json()


def refresh_token(channel: Dict[str, Any]) -> str:
    """Renueva el access_token usando el refresh_token almacenado. Actualiza channel in-place."""
    creds = channel.get("credentials") or {}
    rt = creds.get("refresh_token")
    if not rt:
        raise ValueError(f"Canal {channel['id']} no tiene refresh_token")
    r = httpx.post(TOKEN_URL, data={
        "grant_type": "refresh_token",
        "refresh_token": rt,
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
    }, timeout=TIMEOUT)
    r.raise_for_status()
    data = r.json()
    creds["access_token"] = data["access_token"]
    # Google no siempre devuelve nuevo refresh_token
    if data.get("refresh_token"):
        creds["refresh_token"] = data["refresh_token"]
    expiry = datetime.now(timezone.utc).timestamp() + data.get("expires_in", 3600)
    creds["token_expiry"] = expiry
    channel["credentials"] = creds
    return creds["access_token"]


def _get_token(channel: Dict[str, Any]) -> str:
    """Devuelve un access_token válido, renovándolo si expiró."""
    creds = channel.get("credentials") or {}
    expiry = creds.get("token_expiry", 0)
    if datetime.now(timezone.utc).timestamp() > expiry - 60:
        return refresh_token(channel)
    return creds.get("access_token", "")


def get_email_address(channel: Dict[str, Any]) -> str:
    """Consulta la dirección de email asociada al token (para mostrar en UI)."""
    token = _get_token(channel)
    r = httpx.get("https://www.googleapis.com/oauth2/v2/userinfo",
                  headers={"Authorization": f"Bearer {token}"}, timeout=TIMEOUT)
    r.raise_for_status()
    return r.json().get("email", "")


# ── Polling de mensajes ───────────────────────────────────────────────────────

def poll_unread(channel: Dict[str, Any], max_results: int = 20) -> List[Dict[str, Any]]:
    """
    Busca mensajes no leídos en INBOX. Marca como leídos al procesar.
    Retorna lista de dicts normalizados:
      { gmail_message_id, gmail_thread_id, from_email, from_name,
        subject, body_plain, ts, attachments[] }
    """
    token = _get_token(channel)
    headers = {"Authorization": f"Bearer {token}"}

    # 1. Listar IDs no leídos
    r = httpx.get(f"{GMAIL_API}/messages",
                  params={"q": "is:unread in:inbox", "maxResults": max_results},
                  headers=headers, timeout=TIMEOUT)
    r.raise_for_status()
    msg_ids = [m["id"] for m in r.json().get("messages", [])]

    results: List[Dict[str, Any]] = []
    for mid in msg_ids:
        try:
            parsed = _fetch_and_parse(mid, headers)
            if parsed:
                results.append(parsed)
                _mark_read(mid, headers)
        except Exception as exc:
            results.append({"error": str(exc), "gmail_message_id": mid})

    channel["last_poll"] = datetime.utcnow().isoformat()
    return results


def _fetch_and_parse(msg_id: str, headers: Dict[str, str]) -> Optional[Dict[str, Any]]:
    """Descarga un mensaje y lo parsea."""
    r = httpx.get(f"{GMAIL_API}/messages/{msg_id}?format=full",
                  headers=headers, timeout=TIMEOUT)
    r.raise_for_status()
    data = r.json()

    raw_headers = {h["name"].lower(): h["value"] for h in data.get("payload", {}).get("headers", [])}
    from_raw  = raw_headers.get("from", "")
    subject   = raw_headers.get("subject", "(sin asunto)")
    ts_ms     = int(data.get("internalDate", 0))
    ts        = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).isoformat()

    from_name, from_email = _parse_from(from_raw)
    body  = _extract_body(data.get("payload", {}))

    return {
        "gmail_message_id": msg_id,
        "gmail_thread_id": data.get("threadId", msg_id),
        "from_email": from_email,
        "from_name": from_name,
        "subject": subject,
        "body_plain": body[:4000],
        "ts": ts,
        "attachments": _list_attachments(data.get("payload", {})),
    }


def _parse_from(raw: str):
    """Extrae nombre y email de un header From."""
    m = re.match(r'^"?([^"<]+)"?\s*<([^>]+)>$', raw.strip())
    if m:
        return m.group(1).strip(), m.group(2).strip().lower()
    m2 = re.match(r'^([^\s@]+@[^\s]+)$', raw.strip())
    if m2:
        return raw.split("@")[0], raw.strip().lower()
    return raw, raw.strip().lower()


def _extract_body(payload: Dict[str, Any]) -> str:
    """Extrae el cuerpo text/plain del payload de Gmail."""
    mime = payload.get("mimeType", "")
    if mime == "text/plain":
        data = payload.get("body", {}).get("data", "")
        return base64.urlsafe_b64decode(data + "==").decode("utf-8", errors="replace")
    if mime == "text/html":
        data = payload.get("body", {}).get("data", "")
        html = base64.urlsafe_b64decode(data + "==").decode("utf-8", errors="replace")
        return re.sub(r"<[^>]+>", " ", html).strip()
    # multipart
    for part in payload.get("parts", []):
        text = _extract_body(part)
        if text:
            return text
    return ""


def _list_attachments(payload: Dict[str, Any]) -> List[str]:
    names: List[str] = []
    filename = payload.get("filename")
    if filename:
        names.append(filename)
    for part in payload.get("parts", []):
        names.extend(_list_attachments(part))
    return names


def _mark_read(msg_id: str, headers: Dict[str, str]) -> None:
    httpx.post(f"{GMAIL_API}/messages/{msg_id}/modify",
               json={"removeLabelIds": ["UNREAD"]},
               headers={**headers, "Content-Type": "application/json"},
               timeout=TIMEOUT)


# ── Envío de respuestas ───────────────────────────────────────────────────────

def send_reply(
    channel: Dict[str, Any],
    to_email: str,
    subject: str,
    body: str,
    thread_id: Optional[str] = None,
    reply_to_message_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Envía un email desde la casilla del canal.
    Si se provee thread_id, lo agrega al mismo hilo de Gmail.
    """
    token = _get_token(channel)
    from_email = channel.get("email_address", "")

    msg = MIMEMultipart("alternative")
    msg["From"]    = from_email
    msg["To"]      = to_email
    msg["Subject"] = subject if subject.startswith("Re:") else f"Re: {subject}"
    if reply_to_message_id:
        msg["In-Reply-To"] = reply_to_message_id
        msg["References"]  = reply_to_message_id
    msg.attach(MIMEText(body, "plain", "utf-8"))

    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    payload: Dict[str, Any] = {"raw": raw}
    if thread_id:
        payload["threadId"] = thread_id

    try:
        r = httpx.post(f"{GMAIL_API}/messages/send",
                       json=payload,
                       headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                       timeout=TIMEOUT)
        r.raise_for_status()
        return {"ok": True, "gmail_message_id": r.json().get("id")}
    except httpx.HTTPStatusError as exc:
        return {"ok": False, "error": f"HTTP {exc.response.status_code}: {exc.response.text[:300]}"}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
