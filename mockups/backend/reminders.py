"""Generación de recordatorios / call-to-action proactivos para el panel 'A seguir'.

Heurística determinística sobre los Casos abiertos (sin pegarle a Claude por cada uno):
- Cotización sin movimiento > 3 días → follow-up.
- Reserva tentativa > 5 días sin pago → recordar seña.
- Documentación pendiente > 7 días → pedir documento.
- Agencia sin consultas > 20 días → reactivación.

Cada recordatorio puede generar un draft con IA cuando el agente lo pide ('Generar mensaje').
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

import ai

DAY = 86400


def _age_days(iso: str) -> float:
    try:
        ts = datetime.fromisoformat(iso.replace("Z", ""))
    except Exception:
        return 0.0
    return (datetime.utcnow() - ts).total_seconds() / DAY


def generate(storage: Dict[str, Any], crm: Dict[str, Any], agent_id: Optional[str] = None) -> List[Dict[str, Any]]:
    existing_keys = {(r.get("case_id"), r.get("kind")) for r in storage.get("reminders", []) if r.get("status") == "abierto"}
    new_items: List[Dict[str, Any]] = []

    for case in storage.get("cases", []):
        if case.get("status") != "abierto":
            continue
        if agent_id and case.get("assigned_agent") != agent_id:
            continue

        age = _age_days(case.get("updated_at", case.get("created_at", "")))
        stage = case.get("stage")
        kind: Optional[str] = None
        title = ""
        body_hint = ""

        if stage == "cotizacion" and age >= 3:
            kind = "cotizacion_sin_movimiento"
            title = f"Cotización sin movimiento hace {int(age)} días"
            body_hint = "Pedir si necesita ajuste de precio, otra fecha o más opciones."
        elif stage == "reserva_tentativa" and age >= 5:
            kind = "sena_pendiente"
            title = f"Reserva tentativa hace {int(age)} días — recordar seña"
            body_hint = "Recordar que la reserva tentativa caduca y pedir confirmación + pago de seña."
        elif stage == "documentacion" and age >= 7:
            kind = "doc_pendiente"
            title = f"Documentación pendiente hace {int(age)} días"
            body_hint = "Pedir el documento (pasaporte/visa/voucher) que falte para no demorar el viaje."

        if kind and (case["id"], kind) not in existing_keys:
            new_items.append(
                {
                    "id": f"r-{uuid.uuid4().hex[:8]}",
                    "kind": kind,
                    "case_id": case["id"],
                    "agent_id": case.get("assigned_agent"),
                    "title": title,
                    "body_hint": body_hint,
                    "status": "abierto",
                    "created_at": datetime.utcnow().isoformat(),
                }
            )

    # Reactivación de agencias inactivas
    msgs = storage.get("messages", [])
    last_in_by_contact: Dict[str, str] = {}
    for m in msgs:
        if m.get("direction") == "in":
            last_in_by_contact[m["contact_id"]] = max(last_in_by_contact.get(m["contact_id"], ""), m.get("ts", ""))

    for ag in crm.get("agencias", []):
        last = last_in_by_contact.get(ag["id"])
        if not last:
            continue
        if _age_days(last) >= 20 and (ag["id"], "agencia_inactiva") not in existing_keys:
            new_items.append(
                {
                    "id": f"r-{uuid.uuid4().hex[:8]}",
                    "kind": "agencia_inactiva",
                    "case_id": None,
                    "contact_id": ag["id"],
                    "agent_id": agent_id,
                    "title": f"{ag['nombre']} no consulta hace {int(_age_days(last))} días",
                    "body_hint": "Mandar follow-up suave preguntando si tienen pedidos pendientes.",
                    "status": "abierto",
                    "created_at": datetime.utcnow().isoformat(),
                }
            )

    return new_items


def draft_message(storage: Dict[str, Any], crm: Dict[str, Any], reminder: Dict[str, Any]) -> str:
    """Le pide a Claude (o al mock) un borrador del mensaje del recordatorio."""
    case = next((c for c in storage.get("cases", []) if c["id"] == reminder.get("case_id")), None)
    contact_id = (case or {}).get("contact_id") or reminder.get("contact_id")
    contact = None
    for a in crm.get("agencias", []) + crm.get("pasajeros", []):
        if a["id"] == contact_id:
            contact = a
            break
    if not contact:
        return reminder.get("body_hint", "")

    agent = next((a for a in crm.get("agentes", []) if a["id"] == reminder.get("agent_id")), {}) or {}
    channel = "whatsapp"
    rule = crm.get("channel_rules_default", {}).get(channel, {})

    fake_message = reminder.get("body_hint", "")
    classification = {"type": "consulta", "severity": "informativa", "language": "es"}
    extracted = (case or {}).get("extracted_data", {})
    suggestions = ai.generate_suggestions(
        message_text=fake_message,
        channel=channel,
        case=case or {"id": "n/a", "titulo": reminder.get("title", "")},
        contact=contact,
        agent=agent,
        extracted=extracted,
        classification=classification,
        provider_results=[],
        channel_rule=rule,
        storage=storage,
    )
    return suggestions[0]["body"] if suggestions else fake_message
