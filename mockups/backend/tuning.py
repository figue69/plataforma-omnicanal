"""Auto-tuning de system prompts. Genera propuestas de diff y deja la decisión
en manos del humano (admin/supervisor). Audit log dentro del review.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Dict, List

import ai


def _samples_for_agent(storage: Dict[str, Any], agent_id: str, limit: int = 30) -> List[Dict[str, Any]]:
    out = []
    for m in storage.get("messages", []):
        if m.get("direction") == "out" and m.get("agent_id") == agent_id:
            out.append(
                {
                    "message_id": m.get("id"),
                    "channel": m.get("channel"),
                    "based_on_suggestion_label": m.get("based_on_suggestion_label"),
                    "edited_from_ai": m.get("edited_from_ai"),
                    "text_preview": (m.get("text") or "")[:240],
                }
            )
    return out[-limit:]


def _samples_for_contact(storage: Dict[str, Any], contact_id: str, limit: int = 30) -> List[Dict[str, Any]]:
    out = []
    for m in storage.get("messages", []):
        if m.get("contact_id") == contact_id:
            out.append(
                {
                    "message_id": m.get("id"),
                    "direction": m.get("direction"),
                    "channel": m.get("channel"),
                    "text_preview": (m.get("text") or "")[:240],
                    "classification": m.get("classification"),
                }
            )
    return out[-limit:]


def _find_target(crm: Dict[str, Any], target_type: str, target_id: str) -> Dict[str, Any] | None:
    if target_type == "agente":
        return next((a for a in crm.get("agentes", []) if a["id"] == target_id), None)
    for a in crm.get("agencias", []) + crm.get("pasajeros", []):
        if a["id"] == target_id:
            return a
    return None


def generate(storage: Dict[str, Any], crm: Dict[str, Any], target_type: str, target_id: str) -> Dict[str, Any]:
    target = _find_target(crm, target_type, target_id)
    if not target:
        raise ValueError(f"Target {target_type}:{target_id} no encontrado")

    current_prompt = target.get("system_prompt", "")
    samples = (
        _samples_for_agent(storage, target_id)
        if target_type == "agente"
        else _samples_for_contact(storage, target_id)
    )

    proposal = ai.generate_tuning_review(
        target_type=target_type,
        target_id=target_id,
        current_prompt=current_prompt,
        sample_messages=samples,
        storage=storage,
    )

    review = {
        "id": f"tr-{uuid.uuid4().hex[:8]}",
        "target_type": target_type,
        "target_id": target_id,
        "target_name": target.get("nombre"),
        "current_prompt": current_prompt,
        "proposed_prompt": proposal.get("proposed_prompt", current_prompt),
        "diff_summary": proposal.get("diff_summary", []),
        "justification": proposal.get("justification", ""),
        "evidence": proposal.get("evidence", []),
        "status": "pendiente",
        "created_at": datetime.utcnow().isoformat(),
        "decided_at": None,
        "decided_by": None,
        "decision": None,
        "applied_prompt": None,
    }
    return review


def decide(
    storage: Dict[str, Any],
    crm: Dict[str, Any],
    review: Dict[str, Any],
    decision: Dict[str, Any],
) -> Dict[str, Any]:
    review["decision"] = decision.get("decision")
    review["decided_by"] = decision.get("decided_by")
    review["decided_at"] = datetime.utcnow().isoformat()
    review["status"] = "resuelto"

    if decision.get("decision") in {"approved", "edited"}:
        new_prompt = (
            decision.get("edited_prompt")
            if decision.get("decision") == "edited"
            else review.get("proposed_prompt")
        ) or review.get("current_prompt", "")
        target = _find_target(crm, review["target_type"], review["target_id"])
        if target is not None:
            target["system_prompt"] = new_prompt
        review["applied_prompt"] = new_prompt
    return review
