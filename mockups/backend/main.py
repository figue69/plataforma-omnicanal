"""
FastAPI — Plataforma Omnicanal.
Persistencia: Supabase (PostgreSQL via psycopg2).
Auth: JWT Bearer (login con email + password).

Run local:
    uvicorn main:app --reload --port 8000

Deploy Railway:
    Configurar variables: DATABASE_URL, JWT_SECRET, ANTHROPIC_API_KEY
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

load_dotenv()

import ai
import auth
import channel_rules as channel_rules_mod
import db
import reminders as reminders_mod
import tuning as tuning_mod
from providers import gmail as gmail_provider
from providers import serpapi as serpapi_provider
from providers import telegram_bot as tg_provider
from providers import tourbo as tourbo_provider
from providers import whatsapp as wa

ROOT = Path(__file__).resolve().parent   # absoluto siempre, aunque __file__ sea relativo
MOCKUPS = ROOT.parent  # mockups/

# ──────────────────────────────────────────────────────────────────────────────
# Helpers (operan sobre dicts cargados de DB, sin cambios respecto al original)
# ──────────────────────────────────────────────────────────────────────────────

def find_contact(crm: Dict[str, Any], contact_id: str) -> Optional[Dict[str, Any]]:
    for a in crm.get("agencias", []):
        if a["id"] == contact_id:
            return a
    for p in crm.get("pasajeros", []):
        if p["id"] == contact_id:
            return p
    return None


def find_case(storage: Dict[str, Any], case_id: str) -> Optional[Dict[str, Any]]:
    for c in storage.get("cases", []):
        if c["id"] == case_id:
            return c
    return None


def open_cases_for_contact(storage: Dict[str, Any], contact_id: str) -> List[Dict[str, Any]]:
    return [c for c in storage.get("cases", [])
            if c.get("contact_id") == contact_id and c.get("status") == "abierto"]


def messages_for_case(storage: Dict[str, Any], case_id: str) -> List[Dict[str, Any]]:
    return [m for m in storage.get("messages", []) if m.get("case_id") == case_id]


# ──────────────────────────────────────────────────────────────────────────────
# Modelos Pydantic
# ──────────────────────────────────────────────────────────────────────────────

class LoginRequest(BaseModel):
    email: str
    password: str


class IncomingMessage(BaseModel):
    contact_id: str
    channel: str
    text: str
    subject: Optional[str] = None
    attachments: List[str] = []
    vendedor_id: Optional[str] = None


class ReplyPayload(BaseModel):
    agent_id: str
    body: str
    channel: Optional[str] = None
    based_on_suggestion_label: Optional[str] = None
    edited_from_ai: bool = True


class ChannelRuleUpdate(BaseModel):
    rule: Dict[str, Any]


class TuningGenerateRequest(BaseModel):
    target_type: str
    target_id: str


class TuningDecision(BaseModel):
    decision: str
    edited_prompt: Optional[str] = None
    decided_by: str


class CustomSuggestionRequest(BaseModel):
    instruction: str
    agent_id: Optional[str] = None


class NewContactPayload(BaseModel):
    tipo: str
    nombre: str
    email: Optional[str] = None
    whatsapp: Optional[str] = None
    contacto_principal: Optional[str] = None
    idioma: str = "es"


class ChannelCreatePayload(BaseModel):
    type: str                           # "gmail" | "telegram" | "whatsapp"
    nombre: str                         # nombre descriptivo, ej. "Ventas Principal"
    assigned_agents: List[str] = []
    bot_token: Optional[str] = None     # solo Telegram
    # WhatsApp usa env vars globales por ahora


class ChannelUpdatePayload(BaseModel):
    nombre: Optional[str] = None
    assigned_agents: Optional[List[str]] = None
    active: Optional[bool] = None


# ──────────────────────────────────────────────────────────────────────────────
# App
# ──────────────────────────────────────────────────────────────────────────────

app = FastAPI(title="Plataforma Omnicanal", version="0.2.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Servir frontend estático desde /producto y /cliente-test
# (solo cuando los directorios existen — en local puede no existir la ruta relativa)
_PRODUTO = MOCKUPS / "producto"
_CLIENTE = MOCKUPS / "cliente-test"

if _PRODUTO.exists():
    app.mount("/producto", StaticFiles(directory=str(_PRODUTO), html=True), name="produto")
else:
    print(f"[WARN] Static dir not found: {_PRODUTO}")
if _CLIENTE.exists():
    app.mount("/cliente-test", StaticFiles(directory=str(_CLIENTE), html=True), name="cliente")


@app.get("/", include_in_schema=False)
def root():
    return RedirectResponse("/producto/01-login.html")


# ──────────────────────────────────────────────────────────────────────────────
# Auth (público)
# ──────────────────────────────────────────────────────────────────────────────

@app.get("/health")
def health() -> Dict[str, Any]:
    return {
        "ok": True,
        "ai_real": ai.USE_REAL,
        "tourbo_configured": tourbo_provider.is_configured(),
        "tourbo_url": tourbo_provider.API_URL if tourbo_provider.is_configured() else None,
        "serpapi_configured": serpapi_provider.is_configured(),
        "whatsapp_configured": wa.is_configured(),
    }


@app.post("/auth/login")
def login(req: LoginRequest) -> Dict[str, Any]:
    agent = db.get_agent_with_hash(req.email)
    if not agent or not auth.verify_password(req.password, agent["password_hash"]):
        raise HTTPException(status_code=401, detail="Email o contraseña incorrectos")
    token = auth.create_token(agent["id"])
    # Devolver datos del agente sin el hash
    agent_out = {k: v for k, v in agent.items() if k != "password_hash"}
    return {"token": token, "agent": agent_out}


@app.get("/auth/me")
def me(agent_id: str = Depends(auth.get_current_agent_id)) -> Dict[str, Any]:
    agent = db.get_agent_by_id(agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Agente no encontrado")
    return agent


# ──────────────────────────────────────────────────────────────────────────────
# CRM — lectura (protegido)
# ──────────────────────────────────────────────────────────────────────────────

@app.get("/crm")
def get_crm(_: str = Depends(auth.get_current_agent_id)) -> Dict[str, Any]:
    return db.load_crm()


@app.get("/crm/contacts")
def list_contacts() -> List[Dict[str, Any]]:
    crm = db.load_crm()
    return crm.get("agencias", []) + crm.get("pasajeros", [])


@app.get("/crm/contacts/{contact_id}")
def get_contact(contact_id: str) -> Dict[str, Any]:
    crm = db.load_crm()
    c = find_contact(crm, contact_id)
    if not c:
        raise HTTPException(404, "Contacto no encontrado")
    return c


@app.put("/crm/contacts/{contact_id}/system_prompt")
def update_contact_prompt(
    contact_id: str,
    payload: Dict[str, str],
    _: str = Depends(auth.get_current_agent_id),
) -> Dict[str, Any]:
    crm = db.load_crm()
    c = find_contact(crm, contact_id)
    if not c:
        raise HTTPException(404, "Contacto no encontrado")
    c["system_prompt"] = payload.get("system_prompt", "")
    db.save_crm(crm)
    return c


@app.post("/crm/contacts")
def create_contact(payload: NewContactPayload) -> Dict[str, Any]:
    crm = db.load_crm()
    new_id = f"px-{uuid.uuid4().hex[:8]}" if payload.tipo == "pasajero" else f"ag-{uuid.uuid4().hex[:8]}"
    contact: Dict[str, Any] = {
        "id": new_id,
        "tipo": payload.tipo,
        "nombre": payload.nombre,
        "email": payload.email,
        "whatsapp": payload.whatsapp,
        "idioma": payload.idioma,
        "system_prompt": "",
        "historial_viajes": [],
        "created_at": datetime.utcnow().isoformat(),
    }
    if payload.tipo == "pasajero":
        contact["contacto_principal"] = payload.contacto_principal or payload.nombre
        crm.setdefault("pasajeros", []).append(contact)
    else:
        contact["ubicacion"] = ""
        contact["vendedores"] = []
        contact["datos_comerciales_informativos"] = {}
        contact["tasa_conversion_pct"] = 0
        contact["ticket_promedio_usd"] = 0
        contact["consentimiento_whatsapp"] = False
        crm.setdefault("agencias", []).append(contact)
    db.save_crm(crm)
    return contact


@app.get("/crm/agents")
def list_agents(_: str = Depends(auth.get_current_agent_id)) -> List[Dict[str, Any]]:
    return db.list_agents()


@app.get("/crm/agents/{agent_id}")
def get_agent(agent_id: str, _: str = Depends(auth.get_current_agent_id)) -> Dict[str, Any]:
    a = db.get_agent_by_id(agent_id)
    if not a:
        raise HTTPException(404, "Agente no encontrado")
    return a


@app.put("/crm/agents/{agent_id}/system_prompt")
def update_agent_prompt(
    agent_id: str,
    payload: Dict[str, str],
    _: str = Depends(auth.get_current_agent_id),
) -> Dict[str, Any]:
    a = db.update_agent_system_prompt(agent_id, payload.get("system_prompt", ""))
    if not a:
        raise HTTPException(404, "Agente no encontrado")
    return a


@app.get("/crm/tags")
def list_tags(_: str = Depends(auth.get_current_agent_id)) -> List[Dict[str, Any]]:
    return db.load_crm().get("tags_workspace", [])


@app.post("/crm/tags")
def create_tag(
    tag: Dict[str, str],
    _: str = Depends(auth.get_current_agent_id),
) -> Dict[str, Any]:
    crm = db.load_crm()
    new_tag = {
        "id": tag.get("id") or f"t-{uuid.uuid4().hex[:8]}",
        "nombre": tag["nombre"],
        "color": tag.get("color", "#64748B"),
    }
    crm.setdefault("tags_workspace", []).append(new_tag)
    db.save_crm(crm)
    return new_tag


# ──────────────────────────────────────────────────────────────────────────────
# Tags en casos
# ──────────────────────────────────────────────────────────────────────────────

@app.post("/cases/{case_id}/tags/{tag_id}")
def add_tag_to_case(
    case_id: str, tag_id: str, _: str = Depends(auth.get_current_agent_id)
) -> Dict[str, Any]:
    storage = db.load_storage()
    crm = db.load_crm()
    case = find_case(storage, case_id)
    if not case:
        raise HTTPException(404, "Caso no encontrado")
    known = {t["id"] for t in crm.get("tags_workspace", [])}
    if tag_id not in known:
        raise HTTPException(400, f"Tag '{tag_id}' no existe en el workspace")
    tags = case.setdefault("tags", [])
    if tag_id not in tags:
        tags.append(tag_id)
        case["updated_at"] = datetime.utcnow().isoformat()
        db.save_storage(storage)
    return {"ok": True, "tags": tags}


@app.delete("/cases/{case_id}/tags/{tag_id}")
def remove_tag_from_case(
    case_id: str, tag_id: str, _: str = Depends(auth.get_current_agent_id)
) -> Dict[str, Any]:
    storage = db.load_storage()
    case = find_case(storage, case_id)
    if not case:
        raise HTTPException(404, "Caso no encontrado")
    tags = case.get("tags", [])
    if tag_id in tags:
        tags.remove(tag_id)
        case["updated_at"] = datetime.utcnow().isoformat()
        db.save_storage(storage)
    return {"ok": True, "tags": tags}


# ──────────────────────────────────────────────────────────────────────────────
# Channel rules
# ──────────────────────────────────────────────────────────────────────────────

@app.get("/channel-rules")
def get_channel_rules(_: str = Depends(auth.get_current_agent_id)) -> Dict[str, Any]:
    return channel_rules_mod.get_all(db.load_crm())


@app.put("/channel-rules/{channel}")
def update_channel_rule(
    channel: str,
    payload: ChannelRuleUpdate,
    _: str = Depends(auth.get_current_agent_id),
) -> Dict[str, Any]:
    crm = db.load_crm()
    updated = channel_rules_mod.update(crm, channel, payload.rule)
    db.save_crm(crm)
    return updated


# ──────────────────────────────────────────────────────────────────────────────
# Inbox + Casos
# ──────────────────────────────────────────────────────────────────────────────

@app.get("/inbox")
def get_inbox(
    include_closed: bool = False,
    _: str = Depends(auth.get_current_agent_id),
) -> List[Dict[str, Any]]:
    storage = db.load_storage()
    crm = db.load_crm()
    out: List[Dict[str, Any]] = []
    for case in storage.get("cases", []):
        if not include_closed and case.get("status") == "cerrado":
            continue
        msgs = messages_for_case(storage, case["id"])
        last = msgs[-1] if msgs else None
        contact = find_contact(crm, case.get("contact_id", "")) or {}
        out.append({
            "case": case,
            "contact": {
                "id": contact.get("id"),
                "nombre": contact.get("nombre"),
                "tipo": contact.get("tipo"),
            },
            "last_message": last,
            "unread": sum(1 for m in msgs if m.get("direction") == "in" and not m.get("read")),
            "message_count": len(msgs),
        })
    out.sort(
        key=lambda x: (x["last_message"] or {}).get("ts", x["case"].get("updated_at", "")),
        reverse=True,
    )
    return out


@app.get("/cases/{case_id}")
def get_case_detail(
    case_id: str, _: str = Depends(auth.get_current_agent_id)
) -> Dict[str, Any]:
    storage = db.load_storage()
    crm = db.load_crm()
    case = find_case(storage, case_id)
    if not case:
        raise HTTPException(404, "Caso no encontrado")
    contact = find_contact(crm, case.get("contact_id", ""))
    msgs = messages_for_case(storage, case_id)
    return {
        "case": case,
        "contact": contact,
        "messages": msgs,
        "suggestions": case.get("last_suggestions", []),
    }


@app.post("/cases/{case_id}/suggestions")
def regenerate_suggestions(
    case_id: str,
    req: CustomSuggestionRequest,
    _: str = Depends(auth.get_current_agent_id),
) -> Dict[str, Any]:
    storage = db.load_storage()
    crm = db.load_crm()
    case = find_case(storage, case_id)
    if not case:
        raise HTTPException(404, "Caso no encontrado")
    msgs = messages_for_case(storage, case_id)
    last_in = next((m for m in reversed(msgs) if m.get("direction") == "in"), None)
    if not last_in:
        raise HTTPException(400, "Sin mensaje entrante en este caso")

    contact = find_contact(crm, case.get("contact_id", "")) or {}
    agent = db.get_agent_by_id(req.agent_id or case.get("assigned_agent", "ag-ana")) or {}
    rule = channel_rules_mod.get_one(crm, last_in.get("channel", "whatsapp"))

    augmented_text = f"[Instrucción del agente: {req.instruction}]\n\n{last_in.get('text','')}"
    suggestions = ai.generate_suggestions(
        message_text=augmented_text,
        channel=last_in.get("channel", "whatsapp"),
        case=case,
        contact=contact,
        agent=agent,
        extracted=last_in.get("extracted") or {},
        classification=last_in.get("classification") or {},
        provider_results=case.get("last_provider_results") or [],
        channel_rule=rule,
        storage=storage,
    )
    case["last_suggestions"] = suggestions
    db.save_storage(storage)
    return {"ok": True, "suggestions": suggestions}


@app.put("/cases/{case_id}/extracted")
def update_case_extracted(
    case_id: str,
    payload: Dict[str, Any],
    _: str = Depends(auth.get_current_agent_id),
) -> Dict[str, Any]:
    storage = db.load_storage()
    case = find_case(storage, case_id)
    if not case:
        raise HTTPException(404, "Caso no encontrado")
    case["extracted_data"] = payload
    case["updated_at"] = datetime.utcnow().isoformat()
    db.save_storage(storage)
    return {"ok": True, "extracted_data": payload}


@app.put("/cases/{case_id}/status")
def update_case_status(
    case_id: str,
    payload: Dict[str, Any],
    _: str = Depends(auth.get_current_agent_id),
) -> Dict[str, Any]:
    """Actualiza el status de un caso (abierto / cerrado / pendiente)."""
    storage = db.load_storage()
    case = find_case(storage, case_id)
    if not case:
        raise HTTPException(404, "Caso no encontrado")
    new_status = payload.get("status", "cerrado")
    case["status"] = new_status
    case["updated_at"] = datetime.utcnow().isoformat()
    if new_status == "cerrado":
        case["closed_at"] = datetime.utcnow().isoformat()
    db.save_storage(storage)
    return {"ok": True, "case_id": case_id, "status": new_status}


@app.post("/cases/{case_id}/mark_read")
def mark_case_read(
    case_id: str, _: str = Depends(auth.get_current_agent_id)
) -> Dict[str, Any]:
    storage = db.load_storage()
    changed = 0
    for m in storage.get("messages", []):
        if m.get("case_id") == case_id and m.get("direction") == "in" and not m.get("read"):
            m["read"] = True
            changed += 1
    db.save_storage(storage)
    return {"ok": True, "marked": changed}


# ──────────────────────────────────────────────────────────────────────────────
# Mensaje entrante (desde cliente-test — SIN auth para facilitar el testing)
# ──────────────────────────────────────────────────────────────────────────────

@app.post("/messages")
def post_incoming_message(msg: IncomingMessage) -> Dict[str, Any]:
    storage = db.load_storage()
    crm = db.load_crm()

    contact = find_contact(crm, msg.contact_id)
    if not contact:
        raise HTTPException(404, f"Contacto {msg.contact_id} no encontrado")

    classification = ai.classify_message(msg.text, msg.channel, storage)
    extracted = ai.extract_structured(msg.text, storage)

    open_cases = open_cases_for_contact(storage, msg.contact_id)
    case_id, match_reason = ai.match_message_to_case(
        text=msg.text,
        contact=contact,
        open_cases=open_cases,
        extracted=extracted,
        storage=storage,
    )

    if not case_id:
        new_case = {
            "id": f"c-{uuid.uuid4().hex[:8]}",
            "contact_id": contact["id"],
            "contact_type": contact.get("tipo", "pasajero"),
            "vendedor_id": msg.vendedor_id,
            "titulo": _build_case_title(contact, extracted, classification),
            "destino_principal": (extracted.get("destinos") or [None])[0],
            "tags": [],
            "stage": "consulta",
            "status": "abierto",
            "assigned_agent": "ag-ana",
            "urgency": "alta" if classification.get("severity") == "critica" else "normal",
            "created_at": datetime.utcnow().isoformat(),
            "updated_at": datetime.utcnow().isoformat(),
            "extracted_data": {k: v for k, v in extracted.items() if k != "mocked"},
            "match_reason": match_reason,
        }
        storage["cases"].append(new_case)
        case = new_case
    else:
        case = find_case(storage, case_id)
        case["updated_at"] = datetime.utcnow().isoformat()
        case["match_reason"] = match_reason
        if classification.get("severity") == "critica":
            case["urgency"] = "alta"
        if extracted.get("destinos") and not case.get("destino_principal"):
            case["destino_principal"] = extracted["destinos"][0]

    message_id = f"m-{uuid.uuid4().hex[:10]}"
    stored_msg = {
        "id": message_id,
        "case_id": case["id"],
        "contact_id": contact["id"],
        "direction": "in",
        "channel": msg.channel,
        "subject": msg.subject,
        "text": msg.text,
        "attachments": msg.attachments,
        "ts": datetime.utcnow().isoformat(),
        "read": False,
        "classification": classification,
        "extracted": extracted,
        "vendedor_id": msg.vendedor_id,
    }
    storage["messages"].append(stored_msg)

    # Enriquecimiento por proveedores (se dispara cuando hay destino + fecha)
    provider_results: List[Dict[str, Any]] = []
    destinos = extracted.get("destinos") or []
    has_fecha = bool(
        extracted.get("fecha_salida")
        or extracted.get("fecha_aprox")
        or extracted.get("fecha_retorno")
    )
    should_search = (
        destinos
        and classification.get("type") in {"cotizacion", "consulta", "reserva"}
        and (has_fecha or classification.get("type") == "cotizacion")
    )
    if should_search:
        # Tourbo GDS — tarifas reales (o mock) — tiene prioridad en el display
        tourbo_result = tourbo_provider.search_flights(destinos, extracted)
        provider_results.append(tourbo_result)
        # SerpAPI Google Flights — referencia de precios y flexibilidad de fechas
        if serpapi_provider.is_configured():
            provider_results.append(serpapi_provider.search_flights(destinos, extracted))

    agent = db.get_agent_by_id(case.get("assigned_agent", "ag-ana")) or {}
    rule = channel_rules_mod.get_one(crm, msg.channel)
    suggestions = ai.generate_suggestions(
        message_text=msg.text,
        channel=msg.channel,
        case=case,
        contact=contact,
        agent=agent,
        extracted=extracted,
        classification=classification,
        provider_results=provider_results,
        channel_rule=rule,
        storage=storage,
    )

    case["last_suggestions"] = suggestions
    case["last_provider_results"] = provider_results
    db.save_storage(storage)

    return {
        "ok": True,
        "message_id": message_id,
        "case_id": case["id"],
        "case": case,
        "classification": classification,
        "extracted": extracted,
        "suggestions": suggestions,
        "match_reason": match_reason,
    }


def _build_case_title(
    contact: Dict[str, Any], extracted: Dict[str, Any], classification: Dict[str, Any]
) -> str:
    nombre = contact.get("nombre", "Cliente")
    destinos = extracted.get("destinos") or []
    pax = extracted.get("pax")
    base = f"{classification.get('type', 'consulta').title()} — {nombre}"
    extras: List[str] = []
    if destinos:
        extras.append(", ".join(destinos))
    if pax:
        extras.append(f"{pax} pax")
    if extras:
        base += " — " + " · ".join(extras)
    return base


# ──────────────────────────────────────────────────────────────────────────────
# Reply
# ──────────────────────────────────────────────────────────────────────────────

@app.post("/cases/{case_id}/reply")
def post_reply(
    case_id: str,
    payload: ReplyPayload,
    _: str = Depends(auth.get_current_agent_id),
) -> Dict[str, Any]:
    storage = db.load_storage()
    case = find_case(storage, case_id)
    if not case:
        raise HTTPException(404, "Caso no encontrado")

    msgs = messages_for_case(storage, case_id)
    last_in = next((m for m in reversed(msgs) if m.get("direction") == "in"), None)
    channel = payload.channel or (last_in.get("channel") if last_in else "whatsapp")

    out_msg = {
        "id": f"m-{uuid.uuid4().hex[:10]}",
        "case_id": case_id,
        "contact_id": case.get("contact_id"),
        "direction": "out",
        "channel": channel,
        "text": payload.body,
        "ts": datetime.utcnow().isoformat(),
        "agent_id": payload.agent_id,
        "based_on_suggestion_label": payload.based_on_suggestion_label,
        "edited_from_ai": payload.edited_from_ai,
    }

    crm = db.load_crm()
    contact = find_contact(crm, case.get("contact_id", ""))

    send_result = None

    if channel == "whatsapp":
        if wa.is_configured():
            phone = (contact or {}).get("whatsapp", "")
            if phone:
                send_result = wa.send_text(phone, payload.body)
            else:
                send_result = {"ok": False, "error": f"Contacto {case.get('contact_id')} no tiene número de WhatsApp"}
        else:
            send_result = {"ok": False, "error": "WHATSAPP_TOKEN o WHATSAPP_PHONE_NUMBER_ID no configurados"}

    elif channel == "gmail":
        channel_id = case.get("channel_id") or (last_in or {}).get("channel_id")
        ch_obj = _find_channel(crm, channel_id) if channel_id else None
        if ch_obj and ch_obj.get("type") == "gmail" and ch_obj.get("credentials"):
            to_email = (contact or {}).get("email", "")
            subject = case.get("subject", "(sin asunto)")
            thread_id = (last_in or {}).get("gmail_thread_id")
            reply_msg_id = (last_in or {}).get("gmail_message_id")
            if to_email:
                send_result = gmail_provider.send_reply(
                    ch_obj, to_email, subject, payload.body,
                    thread_id=thread_id, reply_to_message_id=reply_msg_id,
                )
                # Persist updated token if refreshed
                db.save_crm(crm)
            else:
                send_result = {"ok": False, "error": "Contacto sin email registrado"}
        else:
            send_result = {"ok": False, "error": "Canal Gmail no configurado o sin credenciales OAuth"}

    elif channel == "telegram":
        channel_id = case.get("channel_id") or (last_in or {}).get("channel_id")
        ch_obj = _find_channel(crm, channel_id) if channel_id else None
        if ch_obj and ch_obj.get("type") == "telegram" and ch_obj.get("bot_token"):
            tg_chat_id = (last_in or {}).get("tg_chat_id") or case.get("tg_chat_id")
            reply_to = (last_in or {}).get("tg_message_id")
            if tg_chat_id:
                send_result = tg_provider.send_message(
                    ch_obj["bot_token"], tg_chat_id, payload.body,
                    reply_to_message_id=reply_to,
                )
            else:
                send_result = {"ok": False, "error": "No se encontró tg_chat_id para este caso"}
        else:
            send_result = {"ok": False, "error": "Canal Telegram no configurado"}

    if send_result:
        out_msg["send_result"] = send_result

    storage["messages"].append(out_msg)
    case["updated_at"] = datetime.utcnow().isoformat()
    db.save_storage(storage)

    if send_result and not send_result.get("ok"):
        return {"ok": False, "message": out_msg, "send_result": send_result, "error": send_result.get("error")}

    return {"ok": True, "message": out_msg, "send_result": send_result}


@app.get("/messages")
def list_messages(
    contact_id: Optional[str] = None,
    since: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Usado por el cliente-test — sin auth para facilitar el testing."""
    storage = db.load_storage()
    out = storage.get("messages", [])
    if contact_id:
        out = [m for m in out if m.get("contact_id") == contact_id]
    if since:
        out = [m for m in out if m.get("ts", "") > since]
    return out


# ──────────────────────────────────────────────────────────────────────────────
# Reminders / "A seguir"
# ──────────────────────────────────────────────────────────────────────────────

@app.get("/reminders")
def get_reminders(
    agent_id: Optional[str] = None,
    _: str = Depends(auth.get_current_agent_id),
) -> List[Dict[str, Any]]:
    storage = db.load_storage()
    items = storage.get("reminders", [])
    if agent_id:
        items = [r for r in items if r.get("agent_id") == agent_id or r.get("agent_id") is None]
    return [r for r in items if r.get("status") == "abierto"]


@app.post("/reminders/generate")
def generate_reminders(
    agent_id: Optional[str] = None,
    _: str = Depends(auth.get_current_agent_id),
) -> Dict[str, Any]:
    storage = db.load_storage()
    crm = db.load_crm()
    new_items = reminders_mod.generate(storage, crm, agent_id=agent_id)
    storage.setdefault("reminders", []).extend(new_items)
    db.save_storage(storage)
    return {"ok": True, "generated": len(new_items), "items": new_items}


@app.post("/reminders/{reminder_id}/dismiss")
def dismiss_reminder(
    reminder_id: str, _: str = Depends(auth.get_current_agent_id)
) -> Dict[str, Any]:
    storage = db.load_storage()
    for r in storage.get("reminders", []):
        if r["id"] == reminder_id:
            r["status"] = "descartado"
            db.save_storage(storage)
            return {"ok": True}
    raise HTTPException(404, "Recordatorio no encontrado")


@app.post("/reminders/{reminder_id}/draft")
def draft_reminder_message(
    reminder_id: str, _: str = Depends(auth.get_current_agent_id)
) -> Dict[str, Any]:
    storage = db.load_storage()
    crm = db.load_crm()
    reminder = next((r for r in storage.get("reminders", []) if r["id"] == reminder_id), None)
    if not reminder:
        raise HTTPException(404, "Recordatorio no encontrado")
    draft = reminders_mod.draft_message(storage, crm, reminder)
    return {"ok": True, "draft": draft}


# ──────────────────────────────────────────────────────────────────────────────
# Auto-tuning de prompts
# ──────────────────────────────────────────────────────────────────────────────

@app.get("/tuning-reviews")
def list_tuning_reviews(_: str = Depends(auth.get_current_agent_id)) -> List[Dict[str, Any]]:
    return db.load_storage().get("tuning_reviews", [])


@app.post("/tuning-reviews/generate")
def generate_tuning_review(
    req: TuningGenerateRequest, _: str = Depends(auth.get_current_agent_id)
) -> Dict[str, Any]:
    storage = db.load_storage()
    crm = db.load_crm()
    review = tuning_mod.generate(storage, crm, req.target_type, req.target_id)
    storage.setdefault("tuning_reviews", []).append(review)
    db.save_storage(storage)
    return review


@app.post("/tuning-reviews/{review_id}/decide")
def decide_tuning_review(
    review_id: str,
    decision: TuningDecision,
    _: str = Depends(auth.get_current_agent_id),
) -> Dict[str, Any]:
    storage = db.load_storage()
    crm = db.load_crm()
    review = next((r for r in storage.get("tuning_reviews", []) if r["id"] == review_id), None)
    if not review:
        raise HTTPException(404, "Review no encontrada")
    result = tuning_mod.decide(storage, crm, review, decision.dict())
    db.save_storage(storage)
    db.save_crm(crm)
    return result


# ──────────────────────────────────────────────────────────────────────────────
# Métricas
# ──────────────────────────────────────────────────────────────────────────────

@app.get("/metrics")
def get_metrics(_: str = Depends(auth.get_current_agent_id)) -> Dict[str, Any]:
    storage = db.load_storage()
    usage = storage.get("metrics", {}).get("token_usage", [])

    by_model: Dict[str, Any] = {}
    by_task: Dict[str, Any] = {}
    by_agent: Dict[str, Any] = {}
    total_in = total_out = total_cost = 0.0

    for u in usage:
        for bucket, key in (
            (by_model, u.get("model", "?")),
            (by_task, u.get("task", "?")),
            (by_agent, u.get("agent_id") or "n/a"),
        ):
            b = bucket.setdefault(key, {"in": 0, "out": 0, "cost_usd": 0.0, "calls": 0})
            b["in"] += u.get("in_tokens", 0)
            b["out"] += u.get("out_tokens", 0)
            b["cost_usd"] += u.get("cost_usd", 0.0)
            b["calls"] += 1
        total_in += u.get("in_tokens", 0)
        total_out += u.get("out_tokens", 0)
        total_cost += u.get("cost_usd", 0.0)

    msgs = storage.get("messages", [])
    cases = storage.get("cases", [])
    out_msgs = [m for m in msgs if m.get("direction") == "out"]
    sent_unedited = sum(1 for m in out_msgs if m.get("based_on_suggestion_label") and not m.get("edited_from_ai", True))
    sent_edited = sum(1 for m in out_msgs if m.get("based_on_suggestion_label") and m.get("edited_from_ai", True))
    sent_other = sum(1 for m in out_msgs if not m.get("based_on_suggestion_label"))

    return {
        "tokens": {
            "total_in": total_in,
            "total_out": total_out,
            "total_cost_usd": round(total_cost, 4),
            "by_model": by_model,
            "by_task": by_task,
            "by_agent": by_agent,
            "calls": len(usage),
        },
        "operativo": {
            "casos_abiertos": sum(1 for c in cases if c.get("status") == "abierto"),
            "casos_total": len(cases),
            "mensajes_in": sum(1 for m in msgs if m.get("direction") == "in"),
            "mensajes_out": len(out_msgs),
            "aceptacion_ia": {
                "tal_cual": sent_unedited,
                "editadas": sent_edited,
                "otro": sent_other,
            },
        },
        "ai_real": ai.USE_REAL,
    }


# ──────────────────────────────────────────────────────────────────────────────
# Admin — ABM de usuarios
# ──────────────────────────────────────────────────────────────────────────────

class CreateUserPayload(BaseModel):
    nombre: str
    email: str
    password: str
    rol: str = "agente"
    area: str = "ventas"
    permisos_modelo: List[str] = ["haiku", "sonnet"]
    limite_tokens_dia: int = 200000
    system_prompt: str = ""


class UpdateUserPayload(BaseModel):
    nombre: Optional[str] = None
    email: Optional[str] = None
    rol: Optional[str] = None
    area: Optional[str] = None
    permisos_modelo: Optional[List[str]] = None
    limite_tokens_dia: Optional[int] = None
    system_prompt: Optional[str] = None


class ChangePasswordPayload(BaseModel):
    password: str


@app.post("/admin/users")
def create_user(
    payload: CreateUserPayload,
    _: str = Depends(auth.get_current_agent_id),
) -> Dict[str, Any]:
    new_id = f"ag-{uuid.uuid4().hex[:8]}"
    user = db.create_agent(
        id=new_id,
        nombre=payload.nombre,
        rol=payload.rol,
        area=payload.area,
        email=payload.email,
        password_hash=auth.hash_password(payload.password),
        system_prompt=payload.system_prompt,
        permisos_modelo=payload.permisos_modelo,
        limite_tokens_dia=payload.limite_tokens_dia,
    )
    return user


@app.put("/admin/users/{user_id}")
def update_user(
    user_id: str,
    payload: UpdateUserPayload,
    _: str = Depends(auth.get_current_agent_id),
) -> Dict[str, Any]:
    existing = db.get_agent_by_id(user_id)
    if not existing:
        raise HTTPException(404, "Usuario no encontrado")
    with db.get_conn() as conn:
        cur = conn.cursor()
        if payload.nombre is not None:
            cur.execute("UPDATE agents SET nombre = %s WHERE id = %s", (payload.nombre, user_id))
        if payload.email is not None:
            cur.execute("UPDATE agents SET email = %s WHERE id = %s", (payload.email.lower().strip(), user_id))
        if payload.rol is not None:
            cur.execute("UPDATE agents SET rol = %s WHERE id = %s", (payload.rol, user_id))
        if payload.area is not None:
            cur.execute("UPDATE agents SET area = %s WHERE id = %s", (payload.area, user_id))
        if payload.permisos_modelo is not None:
            cur.execute("UPDATE agents SET permisos_modelo = %s WHERE id = %s", (payload.permisos_modelo, user_id))
        if payload.limite_tokens_dia is not None:
            cur.execute("UPDATE agents SET limite_tokens_dia = %s WHERE id = %s", (payload.limite_tokens_dia, user_id))
        if payload.system_prompt is not None:
            cur.execute("UPDATE agents SET system_prompt = %s WHERE id = %s", (payload.system_prompt, user_id))
    return db.get_agent_by_id(user_id)


@app.put("/admin/users/{user_id}/password")
def change_password(
    user_id: str,
    payload: ChangePasswordPayload,
    _: str = Depends(auth.get_current_agent_id),
) -> Dict[str, Any]:
    if not db.get_agent_by_id(user_id):
        raise HTTPException(404, "Usuario no encontrado")
    with db.get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            "UPDATE agents SET password_hash = %s WHERE id = %s",
            (auth.hash_password(payload.password), user_id),
        )
    return {"ok": True}


@app.delete("/admin/users/{user_id}")
def delete_user(
    user_id: str,
    current_agent_id: str = Depends(auth.get_current_agent_id),
) -> Dict[str, Any]:
    if user_id == current_agent_id:
        raise HTTPException(400, "No podés eliminar tu propio usuario")
    if not db.get_agent_by_id(user_id):
        raise HTTPException(404, "Usuario no encontrado")
    with db.get_conn() as conn:
        cur = conn.cursor()
        cur.execute("DELETE FROM agents WHERE id = %s", (user_id,))
    return {"ok": True}


# ──────────────────────────────────────────────────────────────────────────────
# WhatsApp Webhook (Meta Cloud API)
# ──────────────────────────────────────────────────────────────────────────────

@app.get("/webhook/whatsapp", response_class=PlainTextResponse)
def whatsapp_verify(request: Request) -> str:
    """Meta llama este endpoint al configurar el webhook para verificar la URL."""
    params = dict(request.query_params)
    challenge = wa.verify_webhook(
        mode=params.get("hub.mode", ""),
        token=params.get("hub.verify_token", ""),
        challenge=params.get("hub.challenge", ""),
    )
    if challenge is None:
        raise HTTPException(403, "Token de verificación inválido")
    return challenge


@app.post("/webhook/whatsapp")
async def whatsapp_incoming(request: Request) -> Dict[str, Any]:
    """Recibe mensajes entrantes desde Meta y los procesa igual que /messages."""
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(400, "Payload inválido")

    parsed = wa.parse_webhook(body)
    if not parsed:
        # delivery receipt, read receipt, etc. — responder 200 para que Meta no reintente
        return {"ok": True, "skipped": True}

    from_phone = parsed["from_phone"]
    text = parsed["text"]
    wa_msg_id = parsed.get("wa_message_id", "")

    storage = db.load_storage()
    crm = db.load_crm()

    # Buscar contacto por número de WhatsApp
    contact = None
    all_contacts = crm.get("agencias", []) + crm.get("pasajeros", [])
    for c in all_contacts:
        phone = (c.get("whatsapp") or "").lstrip("+").replace(" ", "").replace("-", "")
        if phone and phone == from_phone:
            contact = c
            break

    if not contact:
        # Contacto desconocido — crear uno temporal
        new_id = f"px-wa-{uuid.uuid4().hex[:8]}"
        contact = {
            "id": new_id,
            "tipo": "pasajero",
            "nombre": parsed.get("from_name") or f"WA {from_phone}",
            "whatsapp": f"+{from_phone}",
            "email": None,
            "idioma": "es",
            "system_prompt": "",
            "historial_viajes": [],
            "created_at": datetime.utcnow().isoformat(),
        }
        crm.setdefault("pasajeros", []).append(contact)
        db.save_crm(crm)

    # Reusar la misma lógica que /messages
    classification = ai.classify_message(text, "whatsapp", storage)
    extracted = ai.extract_structured(text, storage)

    open_cases = open_cases_for_contact(storage, contact["id"])
    case_id, match_reason = ai.match_message_to_case(
        text=text,
        contact=contact,
        open_cases=open_cases,
        extracted=extracted,
        storage=storage,
    )

    if not case_id:
        new_case: Dict[str, Any] = {
            "id": f"c-{uuid.uuid4().hex[:8]}",
            "contact_id": contact["id"],
            "contact_type": contact.get("tipo", "pasajero"),
            "vendedor_id": None,
            "titulo": _build_case_title(contact, extracted, classification),
            "destino_principal": (extracted.get("destinos") or [None])[0],
            "tags": [],
            "stage": "consulta",
            "status": "abierto",
            "assigned_agent": "ag-ana",
            "urgency": "alta" if classification.get("severity") == "critica" else "normal",
            "created_at": datetime.utcnow().isoformat(),
            "updated_at": datetime.utcnow().isoformat(),
            "extracted_data": {k: v for k, v in extracted.items() if k != "mocked"},
            "match_reason": match_reason,
        }
        storage["cases"].append(new_case)
        case = new_case
    else:
        case = find_case(storage, case_id)
        case["updated_at"] = datetime.utcnow().isoformat()
        if classification.get("severity") == "critica":
            case["urgency"] = "alta"
        if extracted.get("destinos") and not case.get("destino_principal"):
            case["destino_principal"] = extracted["destinos"][0]

    message_id = f"m-{uuid.uuid4().hex[:10]}"
    stored_msg = {
        "id": message_id,
        "case_id": case["id"],
        "contact_id": contact["id"],
        "direction": "in",
        "channel": "whatsapp",
        "text": text,
        "ts": datetime.utcnow().isoformat(),
        "read": False,
        "classification": classification,
        "extracted": extracted,
        "wa_message_id": wa_msg_id,
    }
    storage["messages"].append(stored_msg)

    # Sugerencias IA
    provider_results: List[Dict[str, Any]] = []
    destinos = extracted.get("destinos") or []
    if destinos and classification.get("type") in {"cotizacion", "consulta", "reserva"}:
        if tourbo_provider.is_configured():
            provider_results.append(tourbo_provider.search_flights(destinos, extracted))
        if serpapi_provider.is_configured():
            provider_results.append(serpapi_provider.search_flights(destinos, extracted))
        if not provider_results:
            provider_results.append(tourbo_provider.mock_flights(destinos, extracted))

    agent = db.get_agent_by_id(case.get("assigned_agent", "ag-ana")) or {}
    rule = channel_rules_mod.get_one(crm, "whatsapp")
    suggestions = ai.generate_suggestions(
        message_text=text,
        channel="whatsapp",
        case=case,
        contact=contact,
        agent=agent,
        extracted=extracted,
        classification=classification,
        provider_results=provider_results,
        channel_rule=rule,
        storage=storage,
    )

    case["last_suggestions"] = suggestions
    case["last_provider_results"] = provider_results
    db.save_storage(storage)

    return {"ok": True, "case_id": case["id"], "message_id": message_id}


# ──────────────────────────────────────────────────────────────────────────────
# Canales (Gmail · Telegram · WhatsApp) — CRUD + OAuth + Webhooks
# ──────────────────────────────────────────────────────────────────────────────

def _find_channel(crm: Dict[str, Any], channel_id: str) -> Optional[Dict[str, Any]]:
    return next((c for c in crm.get("channels", []) if c["id"] == channel_id), None)


@app.get("/channels")
def list_channels(_: str = Depends(auth.get_current_agent_id)) -> List[Dict[str, Any]]:
    """Lista todos los canales configurados (sin tokens/secrets)."""
    crm = db.load_crm()
    return [_safe_channel(c) for c in crm.get("channels", [])]


@app.post("/channels")
def create_channel(
    payload: ChannelCreatePayload,
    _: str = Depends(auth.get_current_agent_id),
) -> Dict[str, Any]:
    crm = db.load_crm()
    channel_id = f"ch-{uuid.uuid4().hex[:8]}"
    channel: Dict[str, Any] = {
        "id": channel_id,
        "type": payload.type,
        "nombre": payload.nombre,
        "active": True,
        "assigned_agents": payload.assigned_agents,
        "created_at": datetime.utcnow().isoformat(),
        "credentials": {},
    }
    if payload.type == "telegram":
        if not payload.bot_token:
            raise HTTPException(400, "bot_token requerido para canales Telegram")
        info = tg_provider.is_token_valid(payload.bot_token)
        if not info.get("ok"):
            raise HTTPException(400, f"Token Telegram inválido: {info.get('error')}")
        channel["bot_token"]     = payload.bot_token
        channel["bot_username"]  = info.get("bot_username", "")
        channel["bot_name"]      = info.get("bot_name", "")
        channel["webhook_registered"] = False

    elif payload.type == "whatsapp":
        # WhatsApp usa las env vars globales; solo registramos el canal lógico
        channel["phone_number_id"] = os.getenv("WHATSAPP_PHONE_NUMBER_ID", "")
        channel["email_address"]   = ""

    crm.setdefault("channels", []).append(channel)
    db.save_crm(crm)
    return _safe_channel(channel)


@app.put("/channels/{channel_id}")
def update_channel(
    channel_id: str,
    payload: ChannelUpdatePayload,
    _: str = Depends(auth.get_current_agent_id),
) -> Dict[str, Any]:
    crm = db.load_crm()
    ch = _find_channel(crm, channel_id)
    if not ch:
        raise HTTPException(404, "Canal no encontrado")
    if payload.nombre is not None:
        ch["nombre"] = payload.nombre
    if payload.assigned_agents is not None:
        ch["assigned_agents"] = payload.assigned_agents
    if payload.active is not None:
        ch["active"] = payload.active
    db.save_crm(crm)
    return _safe_channel(ch)


@app.delete("/channels/{channel_id}")
def delete_channel(
    channel_id: str,
    _: str = Depends(auth.get_current_agent_id),
) -> Dict[str, Any]:
    crm = db.load_crm()
    ch = _find_channel(crm, channel_id)
    if not ch:
        raise HTTPException(404, "Canal no encontrado")
    # Limpiar webhook Telegram si existía
    if ch.get("type") == "telegram" and ch.get("bot_token") and ch.get("webhook_registered"):
        tg_provider.delete_webhook(ch["bot_token"])
    crm["channels"] = [c for c in crm["channels"] if c["id"] != channel_id]
    db.save_crm(crm)
    return {"ok": True}


def _safe_channel(ch: Dict[str, Any]) -> Dict[str, Any]:
    """Devuelve el canal sin exponer tokens ni credenciales, con flags de estado."""
    safe = {k: v for k, v in ch.items() if k not in ("bot_token", "credentials")}
    safe["credentials_ok"] = bool(ch.get("credentials", {}).get("access_token"))
    safe["webhook_ok"] = bool(ch.get("webhook_registered"))
    return safe


# ── Gmail OAuth ───────────────────────────────────────────────────────────────

from fastapi.responses import RedirectResponse as _Redirect, HTMLResponse as _HTML


@app.get("/channels/{channel_id}/gmail/auth")
def gmail_auth_start(
    channel_id: str,
    _: str = Depends(auth.get_current_agent_id),
):
    """Redirige al consent de Google OAuth2 para conectar una casilla Gmail."""
    crm = db.load_crm()
    ch = _find_channel(crm, channel_id)
    if not ch or ch.get("type") != "gmail":
        raise HTTPException(404, "Canal Gmail no encontrado")
    if not gmail_provider.is_configured():
        raise HTTPException(400, "GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET no configurados")
    url = gmail_provider.get_auth_url(channel_id)
    return _Redirect(url)


@app.get("/channels/gmail/callback", response_class=_HTML)
def gmail_oauth_callback(
    code: Optional[str] = None,
    state: Optional[str] = None,
    error: Optional[str] = None,
):
    """Callback OAuth2 de Google. Recibe el code, intercambia por tokens y los guarda."""
    if error:
        return _HTML(f"<h2>Error OAuth: {error}</h2><p>Cerrá esta ventana y reintentá.</p>")
    if not code or not state:
        return _HTML("<h2>Parámetros faltantes</h2>")

    try:
        tokens = gmail_provider.exchange_code(code)
    except Exception as exc:
        return _HTML(f"<h2>Error al intercambiar code: {exc}</h2>")

    crm = db.load_crm()
    ch = _find_channel(crm, state)
    if not ch:
        return _HTML(f"<h2>Canal {state} no encontrado</h2>")

    import time as _time
    ch["credentials"] = {
        "access_token":  tokens.get("access_token"),
        "refresh_token": tokens.get("refresh_token"),
        "token_expiry":  _time.time() + tokens.get("expires_in", 3600),
    }
    # Obtener email del usuario
    try:
        email_addr = gmail_provider.get_email_address(ch)
        ch["email_address"] = email_addr
    except Exception:
        pass
    ch["oauth_connected"] = True
    db.save_crm(crm)

    return _HTML("""
        <html><body style="font-family:system-ui;text-align:center;padding:60px">
        <h2 style="color:#16a34a">✓ Gmail conectado correctamente</h2>
        <p>Podés cerrar esta ventana. La casilla ya aparece activa en el panel de canales.</p>
        <script>setTimeout(()=>window.close(),3000)</script>
        </body></html>
    """)


# ── Gmail polling ─────────────────────────────────────────────────────────────

@app.post("/poll/gmail")
def poll_gmail_channels(
    channel_id: Optional[str] = None,
    _: str = Depends(auth.get_current_agent_id),
) -> Dict[str, Any]:
    """
    Consulta los emails no leídos de todos los canales Gmail activos
    (o solo uno si se especifica channel_id) y los procesa como mensajes entrantes.
    """
    crm = db.load_crm()
    channels = crm.get("channels", [])
    targets = [c for c in channels
               if c.get("type") == "gmail" and c.get("active") and c.get("oauth_connected")
               and (channel_id is None or c["id"] == channel_id)]

    if not targets:
        return {"ok": True, "processed": 0, "message": "Sin canales Gmail activos con OAuth conectado"}

    total = 0
    results: List[Dict[str, Any]] = []
    for ch in targets:
        try:
            emails = gmail_provider.poll_unread(ch)
            for em in emails:
                if em.get("error"):
                    results.append({"channel": ch["id"], "error": em["error"]})
                    continue
                r = _process_incoming_email(em, ch, crm)
                results.append({"channel": ch["id"], "case_id": r.get("case_id"), "ok": r.get("ok")})
                total += 1
        except Exception as exc:
            results.append({"channel": ch["id"], "error": str(exc)})

    db.save_crm(crm)  # Actualiza last_poll en los canales
    return {"ok": True, "processed": total, "results": results}


def _process_incoming_email(
    email_data: Dict[str, Any],
    channel: Dict[str, Any],
    crm: Dict[str, Any],
) -> Dict[str, Any]:
    """Procesa un email entrante por el mismo pipeline que los mensajes WhatsApp."""
    from_email = email_data.get("from_email", "")
    from_name  = email_data.get("from_name", from_email)
    subject    = email_data.get("subject", "")
    body       = email_data.get("body_plain", "")
    thread_id  = email_data.get("gmail_thread_id")

    storage = db.load_storage()

    # Buscar contacto por email
    contact = None
    for a in crm.get("agencias", []) + crm.get("pasajeros", []):
        if (a.get("email") or "").lower() == from_email.lower():
            contact = a
            break

    # Crear contacto genérico si no existe
    if not contact:
        new_id = f"px-{uuid.uuid4().hex[:8]}"
        contact = {
            "id": new_id, "tipo": "pasajero",
            "nombre": from_name, "email": from_email,
            "whatsapp": "", "idioma": "es",
            "system_prompt": "", "historial_viajes": [],
            "created_at": datetime.utcnow().isoformat(),
        }
        crm.setdefault("pasajeros", []).append(contact)

    # Pipeline IA
    full_text  = f"Asunto: {subject}\n\n{body}" if subject else body
    classification = ai.classify_message(full_text, "email", storage)
    extracted  = ai.extract_structured(full_text, storage)

    open_cases = open_cases_for_contact(storage, contact["id"])
    # Buscar también por gmail_thread_id para continuar el hilo
    case = next((c for c in open_cases if c.get("gmail_thread_id") == thread_id), None)
    case_id = case["id"] if case else None
    match_reason = "gmail_thread" if case else None

    if not case_id:
        case_id, match_reason = ai.match_message_to_case(
            text=full_text, contact=contact,
            open_cases=open_cases, extracted=extracted, storage=storage,
        )

    if not case_id:
        case = {
            "id": f"c-{uuid.uuid4().hex[:8]}",
            "contact_id": contact["id"],
            "contact_type": contact.get("tipo", "pasajero"),
            "titulo": _build_case_title(contact, extracted, classification),
            "destino_principal": (extracted.get("destinos") or [None])[0],
            "tags": [], "stage": "consulta", "status": "abierto",
            "assigned_agent": _first_assigned_agent(channel),
            "urgency": "alta" if classification.get("severity") == "critica" else "normal",
            "gmail_thread_id": thread_id,
            "channel_id": channel["id"],
            "created_at": datetime.utcnow().isoformat(),
            "updated_at": datetime.utcnow().isoformat(),
            "extracted_data": {k: v for k, v in extracted.items() if k != "mocked"},
        }
        storage["cases"].append(case)
    else:
        case = find_case(storage, case_id)
        case["updated_at"] = datetime.utcnow().isoformat()
        if not case.get("gmail_thread_id") and thread_id:
            case["gmail_thread_id"] = thread_id

    msg_id = f"m-{uuid.uuid4().hex[:10]}"
    storage["messages"].append({
        "id": msg_id,
        "case_id": case["id"],
        "contact_id": contact["id"],
        "direction": "in", "channel": "email",
        "channel_id": channel["id"],
        "subject": subject,
        "text": full_text,
        "attachments": email_data.get("attachments", []),
        "ts": email_data.get("ts") or datetime.utcnow().isoformat(),
        "read": False,
        "classification": classification,
        "extracted": extracted,
        "gmail_message_id": email_data.get("gmail_message_id"),
        "gmail_thread_id": thread_id,
    })

    # Enriquecimiento + sugerencias
    provider_results: List[Dict[str, Any]] = []
    destinos = extracted.get("destinos") or []
    if destinos and classification.get("type") in {"cotizacion", "consulta", "reserva"}:
        provider_results.append(tourbo_provider.search_flights(destinos, extracted))
        if serpapi_provider.is_configured():
            provider_results.append(serpapi_provider.search_flights(destinos, extracted))

    agent = db.get_agent_by_id(case.get("assigned_agent", "")) or {}
    rule  = channel_rules_mod.get_one(crm, "email")
    suggestions = ai.generate_suggestions(
        message_text=full_text, channel="email",
        case=case, contact=contact, agent=agent,
        extracted=extracted, classification=classification,
        provider_results=provider_results, channel_rule=rule, storage=storage,
    )
    case["last_suggestions"] = suggestions
    case["last_provider_results"] = provider_results
    db.save_storage(storage)

    return {"ok": True, "case_id": case["id"], "message_id": msg_id}


def _first_assigned_agent(channel: Dict[str, Any]) -> str:
    agents = channel.get("assigned_agents", [])
    return agents[0] if agents else "ag-ana"


# ── Telegram token validation (pre-create) ────────────────────────────────────

class TgValidatePayload(BaseModel):
    bot_token: str


@app.post("/channels/telegram/validate-token")
def validate_tg_token(
    payload: TgValidatePayload,
    _: str = Depends(auth.get_current_agent_id),
) -> Dict[str, Any]:
    """Verifica que el bot token sea válido antes de crear el canal."""
    return tg_provider.is_token_valid(payload.bot_token)


# ── Telegram Webhook ──────────────────────────────────────────────────────────

@app.post("/channels/{channel_id}/telegram/register-webhook")
def register_tg_webhook(
    channel_id: str,
    _: str = Depends(auth.get_current_agent_id),
) -> Dict[str, Any]:
    """Registra el webhook en Telegram para este canal bot."""
    crm = db.load_crm()
    ch = _find_channel(crm, channel_id)
    if not ch or ch.get("type") != "telegram":
        raise HTTPException(404, "Canal Telegram no encontrado")
    result = tg_provider.register_webhook(ch["bot_token"], channel_id)
    if result.get("ok"):
        ch["webhook_registered"] = True
        ch["webhook_url"] = result["webhook_url"]
        db.save_crm(crm)
    return result


@app.get("/channels/{channel_id}/telegram/webhook-info")
def tg_webhook_info(
    channel_id: str,
    _: str = Depends(auth.get_current_agent_id),
) -> Dict[str, Any]:
    crm = db.load_crm()
    ch = _find_channel(crm, channel_id)
    if not ch or ch.get("type") != "telegram":
        raise HTTPException(404, "Canal Telegram no encontrado")
    return tg_provider.get_webhook_info(ch["bot_token"])


@app.post("/webhook/telegram/{channel_id}")
async def telegram_incoming(channel_id: str, request: Request) -> Dict[str, Any]:
    """Recibe updates de Telegram para el bot del canal indicado."""
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(400, "Payload inválido")

    crm = db.load_crm()
    ch  = _find_channel(crm, channel_id)
    if not ch or not ch.get("active"):
        return {"ok": True, "skipped": True}  # responder 200 siempre a Telegram

    parsed = tg_provider.parse_update(body)
    if not parsed:
        return {"ok": True, "skipped": True}

    storage = db.load_storage()

    # Identificar/crear contacto por tg_user_id
    tg_user_id = str(parsed.get("tg_user_id", ""))
    contact = None
    for p in crm.get("pasajeros", []):
        if p.get("telegram_user_id") == tg_user_id:
            contact = p
            break

    if not contact:
        new_id = f"px-{uuid.uuid4().hex[:8]}"
        contact = {
            "id": new_id, "tipo": "pasajero",
            "nombre": parsed["from_name"],
            "telegram_user_id": tg_user_id,
            "telegram_username": parsed.get("from_username", ""),
            "telegram_chat_id": parsed.get("tg_chat_id"),
            "email": "", "whatsapp": "", "idioma": "es",
            "system_prompt": "", "historial_viajes": [],
            "created_at": datetime.utcnow().isoformat(),
        }
        crm.setdefault("pasajeros", []).append(contact)

    text = parsed["text"]
    classification = ai.classify_message(text, "telegram", storage)
    extracted = ai.extract_structured(text, storage)

    open_cases = open_cases_for_contact(storage, contact["id"])
    # Continuar por chat_id
    tg_chat_id = str(parsed.get("tg_chat_id", ""))
    case = next((c for c in open_cases if str(c.get("tg_chat_id", "")) == tg_chat_id), None)
    case_id = case["id"] if case else None

    if not case_id:
        case_id, match_reason = ai.match_message_to_case(
            text=text, contact=contact,
            open_cases=open_cases, extracted=extracted, storage=storage,
        )
    else:
        match_reason = "tg_chat"

    if not case_id:
        case = {
            "id": f"c-{uuid.uuid4().hex[:8]}",
            "contact_id": contact["id"],
            "contact_type": "pasajero",
            "titulo": _build_case_title(contact, extracted, classification),
            "destino_principal": (extracted.get("destinos") or [None])[0],
            "tags": [], "stage": "consulta", "status": "abierto",
            "assigned_agent": _first_assigned_agent(ch),
            "urgency": "alta" if classification.get("severity") == "critica" else "normal",
            "tg_chat_id": tg_chat_id,
            "channel_id": channel_id,
            "created_at": datetime.utcnow().isoformat(),
            "updated_at": datetime.utcnow().isoformat(),
            "extracted_data": {k: v for k, v in extracted.items() if k != "mocked"},
        }
        storage["cases"].append(case)
    else:
        case = find_case(storage, case_id)
        case["updated_at"] = datetime.utcnow().isoformat()
        if not case.get("tg_chat_id"):
            case["tg_chat_id"] = tg_chat_id

    msg_id = f"m-{uuid.uuid4().hex[:10]}"
    storage["messages"].append({
        "id": msg_id,
        "case_id": case["id"],
        "contact_id": contact["id"],
        "direction": "in", "channel": "telegram",
        "channel_id": channel_id,
        "text": text,
        "attachments": [],
        "ts": datetime.utcnow().isoformat(),
        "read": False,
        "classification": classification,
        "extracted": extracted,
        "tg_update_id": parsed.get("tg_update_id"),
        "tg_chat_id": tg_chat_id,
    })

    # Sugerencias
    agent = db.get_agent_by_id(case.get("assigned_agent", "")) or {}
    rule  = channel_rules_mod.get_one(crm, "telegram")
    suggestions = ai.generate_suggestions(
        message_text=text, channel="telegram",
        case=case, contact=contact, agent=agent,
        extracted=extracted, classification=classification,
        provider_results=[], channel_rule=rule, storage=storage,
    )
    case["last_suggestions"] = suggestions
    db.save_storage(storage)
    db.save_crm(crm)

    return {"ok": True, "case_id": case["id"]}


# ── Reply por canal (update del endpoint existente) ───────────────────────────
# El endpoint /cases/{case_id}/reply ya existente maneja WA.
# Para email y telegram, extendemos la lógica dentro del mismo endpoint.


# ──────────────────────────────────────────────────────────────────────────────
# Búsqueda de vuelos — SerpAPI (Google Flights) + Tourbo (GDS real)
# ──────────────────────────────────────────────────────────────────────────────

class FlightSearchRequest(BaseModel):
    origin: str = "EZE"
    destination: str
    dep_date: str                       # YYYY-MM-DD
    ret_date: Optional[str] = None      # None = ida simple
    adults: int = 1
    currency: str = "USD"               # USD o ARS
    flex_days: int = 3                  # 0-3 días de flexibilidad hacia atrás
    case_id: Optional[str] = None       # Si se provee, guarda resultado en el caso


@app.post("/search/flights")
def search_flights_endpoint(
    req: FlightSearchRequest,
    agent_id: str = Depends(auth.get_current_agent_id),
) -> Dict[str, Any]:
    # Resolver IATA si recibimos texto libre
    dest_iata = serpapi_provider.resolve_airport(req.destination) or req.destination.upper()[:3]
    origin_iata = serpapi_provider.resolve_airport(req.origin) or req.origin.upper()[:3]

    result = serpapi_provider.search_flights_flexible(
        origin=origin_iata,
        destination=dest_iata,
        dep_date=req.dep_date,
        ret_date=req.ret_date,
        adults=req.adults,
        currency=req.currency,
        flex_days=req.flex_days,
    )

    # Si viene con case_id guardamos el resultado en el caso para el contexto IA
    if req.case_id and result.get("ok"):
        storage = db.load_storage()
        case = find_case(storage, req.case_id)
        if case:
            pr = case.setdefault("last_provider_results", [])
            # Reemplazar resultado previo de SerpAPI si existe
            pr[:] = [p for p in pr if p.get("source") != "SerpAPI/GoogleFlights"]
            pr.append(result)
            case["updated_at"] = datetime.utcnow().isoformat()
            db.save_storage(storage)

    return result


class TourboSearchRequest(BaseModel):
    origin: str = "EZE"
    destination: str
    dep_date: str                       # YYYY-MM-DD
    ret_date: Optional[str] = None
    adults: int = 2
    children: int = 0
    infants: int = 0
    cabin: str = "Y"                    # Y=Economy, C=Business, F=First
    currency: str = "USD"
    case_id: Optional[str] = None


@app.post("/search/tourbo")
def search_tourbo_endpoint(
    req: TourboSearchRequest,
    agent_id: str = Depends(auth.get_current_agent_id),
) -> Dict[str, Any]:
    """Busca vuelos en Tourbo/Flaptek GDS. Retorna tarifas reales (o mock si no hay credenciales)."""
    dest_iata = serpapi_provider.resolve_airport(req.destination) or req.destination.upper()[:3]
    origin_iata = serpapi_provider.resolve_airport(req.origin) or req.origin.upper()[:3]

    result = tourbo_provider.avail(
        origin=origin_iata,
        destination=dest_iata,
        dep_date=req.dep_date,
        ret_date=req.ret_date,
        adults=req.adults,
        children=req.children,
        infants=req.infants,
        cabin=req.cabin,
        currency=req.currency,
    )

    if req.case_id and result.get("ok"):
        storage = db.load_storage()
        case = find_case(storage, req.case_id)
        if case:
            pr = case.setdefault("last_provider_results", [])
            pr[:] = [p for p in pr if p.get("source") not in ("Tourbo", "Tourbo (mock)")]
            pr.append(result)
            case["updated_at"] = datetime.utcnow().isoformat()
            db.save_storage(storage)

    return result


@app.post("/search/tourbo/{solution_id}/pricing")
def tourbo_pricing_endpoint(
    solution_id: str,
    pax_count: int = 2,
    currency: str = "USD",
    _: str = Depends(auth.get_current_agent_id),
) -> Dict[str, Any]:
    """Confirma precio de una solución Tourbo antes de reservar."""
    return tourbo_provider.pricing(solution_id, pax_count, currency)


# ──────────────────────────────────────────────────────────────────────────────
# Diagnóstico WhatsApp
# ──────────────────────────────────────────────────────────────────────────────

class WaTestPayload(BaseModel):
    phone: str
    message: str = "Hola, este es un mensaje de prueba desde PlatOmIA."


@app.post("/admin/test-whatsapp")
def test_whatsapp_send(
    payload: WaTestPayload,
    _: str = Depends(auth.get_current_agent_id),
) -> Dict[str, Any]:
    """Envía un mensaje de prueba directo para verificar que el token y número funcionan."""
    if not wa.is_configured():
        return {"ok": False, "error": "WHATSAPP_TOKEN o WHATSAPP_PHONE_NUMBER_ID no configurados en env"}
    result = wa.send_text(payload.phone, payload.message)
    return result


@app.get("/admin/whatsapp-info")
def whatsapp_info(_: str = Depends(auth.get_current_agent_id)) -> Dict[str, Any]:
    """Consulta info del Phone Number ID en Meta Graph API para verificar el token."""
    import httpx as _httpx
    phone_id = os.getenv("WHATSAPP_PHONE_NUMBER_ID", "")
    token = os.getenv("WHATSAPP_TOKEN", "")
    if not phone_id or not token:
        return {"ok": False, "error": "Variables de entorno no configuradas"}
    try:
        r = _httpx.get(
            f"https://graph.facebook.com/v19.0/{phone_id}",
            params={"fields": "display_phone_number,verified_name,quality_rating,platform_type"},
            headers={"Authorization": f"Bearer {token}"},
            timeout=10,
        )
        data = r.json()
        if "error" in data:
            return {"ok": False, "meta_error": data["error"]}
        return {"ok": True, "phone_info": data}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ──────────────────────────────────────────────────────────────────────────────
# Reset (útil para demos)
# ──────────────────────────────────────────────────────────────────────────────

@app.post("/admin/reset")
def reset_storage(
    keep_cases: bool = True,
    _: str = Depends(auth.get_current_agent_id),
) -> Dict[str, Any]:
    storage = db.load_storage()
    storage["messages"] = []
    storage["reminders"] = []
    storage["tuning_reviews"] = []
    storage["metrics"] = {"token_usage": []}
    if not keep_cases:
        storage["cases"] = []
    db.save_storage(storage)
    return {"ok": True}
