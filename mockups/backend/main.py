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
from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

load_dotenv()

import ai
import auth
import channel_rules as channel_rules_mod
import db
import reminders as reminders_mod
import tuning as tuning_mod
from providers import serpapi as serpapi_provider
from providers import tourbo as tourbo_provider

ROOT = Path(__file__).parent
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

# Servir frontend estático desde /produto y /cliente-test
# (solo cuando los directorios existen — en local puede no existir la ruta relativa)
_PRODUTO = MOCKUPS / "producto"
_CLIENTE = MOCKUPS / "cliente-test"

if _PRODUTO.exists():
    app.mount("/producto", StaticFiles(directory=str(_PRODUTO), html=True), name="produto")
if _CLIENTE.exists():
    app.mount("/cliente-test", StaticFiles(directory=str(_CLIENTE), html=True), name="cliente")


@app.get("/", include_in_schema=False)
def root():
    return RedirectResponse("/produto/01-login.html")


# ──────────────────────────────────────────────────────────────────────────────
# Auth (público)
# ──────────────────────────────────────────────────────────────────────────────

@app.get("/health")
def health() -> Dict[str, Any]:
    return {
        "ok": True,
        "ai_real": ai.USE_REAL,
        "tourbo_configured": tourbo_provider.is_configured(),
        "serpapi_configured": serpapi_provider.is_configured(),
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
def get_inbox(_: str = Depends(auth.get_current_agent_id)) -> List[Dict[str, Any]]:
    storage = db.load_storage()
    crm = db.load_crm()
    out: List[Dict[str, Any]] = []
    for case in storage.get("cases", []):
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

    # Enriquecimiento por proveedores
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
    storage["messages"].append(out_msg)
    case["updated_at"] = datetime.utcnow().isoformat()
    db.save_storage(storage)
    return {"ok": True, "message": out_msg}


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
