"""
Capa de IA del mockup. Si hay ANTHROPIC_API_KEY, usa Claude (Haiku para
clasificación/extracción/severidad y Sonnet para generar sugerencias).
Si no, cae a un mock determinístico por keywords para que el demo funcione igual.

Todas las funciones devuelven un dict serializable y registran consumo de tokens
en el storage para alimentar el dashboard.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

try:
    from anthropic import Anthropic  # type: ignore

    _ANTHROPIC_AVAILABLE = True
except ImportError:
    _ANTHROPIC_AVAILABLE = False

API_KEY = os.getenv("ANTHROPIC_API_KEY", "").strip()
HAIKU = os.getenv("HAIKU_MODEL", "claude-haiku-4-5-20251001")
SONNET = os.getenv("SONNET_MODEL", "claude-sonnet-4-6")

USE_REAL = bool(API_KEY) and _ANTHROPIC_AVAILABLE
_client: Optional["Anthropic"] = None
if USE_REAL:
    _client = Anthropic(api_key=API_KEY)


# Precios USD por millón de tokens (aproximados, sólo para visualizar costo en el dashboard)
PRICING = {
    HAIKU: {"in": 1.0, "out": 5.0},
    SONNET: {"in": 3.0, "out": 15.0},
}


# --------------------------------------------------------------------------------------
# Logging de tokens (compartido)
# --------------------------------------------------------------------------------------

def _log_usage(
    storage: Dict[str, Any],
    model: str,
    in_tokens: int,
    out_tokens: int,
    task: str,
    agent_id: Optional[str] = None,
    case_id: Optional[str] = None,
) -> None:
    pricing = PRICING.get(model, {"in": 1.0, "out": 5.0})
    cost = (in_tokens / 1_000_000) * pricing["in"] + (out_tokens / 1_000_000) * pricing["out"]
    storage.setdefault("metrics", {}).setdefault("token_usage", []).append(
        {
            "ts": datetime.utcnow().isoformat() + "Z",
            "model": model,
            "task": task,
            "in_tokens": in_tokens,
            "out_tokens": out_tokens,
            "cost_usd": round(cost, 6),
            "mocked": not USE_REAL,
            "agent_id": agent_id,
            "case_id": case_id,
        }
    )


# --------------------------------------------------------------------------------------
# Helpers de Claude
# --------------------------------------------------------------------------------------

def _claude_json(
    model: str,
    system: str,
    user: str,
    storage: Dict[str, Any],
    task: str,
    max_tokens: int = 800,
    agent_id: Optional[str] = None,
    case_id: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Llama a Claude pidiéndole que responda JSON puro. None si falla."""
    if not USE_REAL or _client is None:
        return None
    try:
        resp = _client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=system + "\n\nResponde EXCLUSIVAMENTE con JSON válido, sin texto antes ni después.",
            messages=[{"role": "user", "content": user}],
        )
        text = resp.content[0].text if resp.content else ""
        _log_usage(
            storage,
            model,
            getattr(resp.usage, "input_tokens", 0),
            getattr(resp.usage, "output_tokens", 0),
            task,
            agent_id,
            case_id,
        )
        # rescatar JSON aunque venga con ```json
        match = re.search(r"\{[\s\S]*\}", text)
        if match:
            return json.loads(match.group(0))
    except Exception as exc:  # noqa: BLE001
        print(f"[ai] Claude {task} fallback a mock: {exc}")
    return None


def _claude_text(
    model: str,
    system: str,
    user: str,
    storage: Dict[str, Any],
    task: str,
    max_tokens: int = 800,
    agent_id: Optional[str] = None,
    case_id: Optional[str] = None,
) -> Optional[str]:
    if not USE_REAL or _client is None:
        return None
    try:
        resp = _client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        text = resp.content[0].text if resp.content else ""
        _log_usage(
            storage,
            model,
            getattr(resp.usage, "input_tokens", 0),
            getattr(resp.usage, "output_tokens", 0),
            task,
            agent_id,
            case_id,
        )
        return text
    except Exception as exc:  # noqa: BLE001
        print(f"[ai] Claude {task} fallback a mock: {exc}")
    return None


# --------------------------------------------------------------------------------------
# 1) Clasificar mensaje (Haiku)
# --------------------------------------------------------------------------------------

CLASSIFY_TYPES = [
    "consulta",
    "cotizacion",
    "reserva",
    "pago",
    "documentacion",
    "reclamo",
    "post_venta",
]


def classify_message(
    text: str,
    channel: str,
    storage: Dict[str, Any],
    case_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Clasifica tipo, severidad y idioma. Devuelve dict listo para guardar."""
    system = (
        "Sos un clasificador de mensajes para un turoperador. Recibís un mensaje "
        "entrante de un cliente y devolvés:\n"
        f"- type: uno de {CLASSIFY_TYPES}\n"
        "- severity: 'informativa' | 'alta' | 'critica' (alta/critica = urgencias en destino, "
        "pasaporte perdido, vuelo cancelado, problemas de salud, etc.)\n"
        "- language: ISO 639-1 (es, en, pt, ...)\n"
        "- confidence: 0.0-1.0"
    )
    user = f"Canal: {channel}\nMensaje:\n{text}"
    parsed = _claude_json(HAIKU, system, user, storage, "classify", max_tokens=200, case_id=case_id)
    if parsed:
        parsed.setdefault("type", "consulta")
        parsed.setdefault("severity", "informativa")
        parsed.setdefault("language", "es")
        parsed.setdefault("confidence", 0.7)
        return parsed
    # Mock determinístico
    return _mock_classify(text)


def _mock_classify(text: str) -> Dict[str, Any]:
    t = text.lower()
    if any(k in t for k in ["pasaporte", "perdí", "robo", "robaron", "hospital", "emergencia", "urgente", "ayuda"]):
        return {"type": "reclamo", "severity": "critica", "language": "es", "confidence": 0.9, "mocked": True}
    if any(k in t for k in ["pago", "transferencia", "factura", "comprobante"]):
        return {"type": "pago", "severity": "informativa", "language": "es", "confidence": 0.85, "mocked": True}
    if any(k in t for k in ["pasaje", "voucher", "documentación", "documentacion", "pdf"]):
        return {"type": "documentacion", "severity": "informativa", "language": "es", "confidence": 0.8, "mocked": True}
    if any(k in t for k in ["reserva", "reservar", "confirmar", "ok dale", "sí"]):
        return {"type": "reserva", "severity": "informativa", "language": "es", "confidence": 0.75, "mocked": True}
    if any(k in t for k in ["cotiz", "presupuesto", "precio", "cuanto", "cuánto", "armar", "itinerario", "días", "dias"]):
        return {"type": "cotizacion", "severity": "informativa", "language": "es", "confidence": 0.85, "mocked": True}
    return {"type": "consulta", "severity": "informativa", "language": "es", "confidence": 0.6, "mocked": True}


# --------------------------------------------------------------------------------------
# 2) Extraer datos estructurados (Haiku)
# --------------------------------------------------------------------------------------

def extract_structured(
    text: str,
    storage: Dict[str, Any],
    case_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Extrae destinos, fechas, pax, duración, presupuesto si los menciona el mensaje."""
    system = (
        "Extraés información de viaje del mensaje del cliente. Devolvés JSON con:\n"
        "- destinos: lista de strings (ciudades/países mencionados)\n"
        "- pax: número entero o null (cantidad de pasajeros)\n"
        "- duracion_dias: número entero o null\n"
        "- fecha_aprox: 'YYYY-MM' o 'YYYY-MM-DD' o null\n"
        "- presupuesto_usd: número o null\n"
        "- referencia_reserva: string o null (ej: REF-1234)\n"
        "Si un dato no se menciona explícitamente, usar null."
    )
    parsed = _claude_json(HAIKU, system, text, storage, "extract", max_tokens=300, case_id=case_id)
    if parsed:
        return parsed
    return _mock_extract(text)


def _mock_extract(text: str) -> Dict[str, Any]:
    t = text.lower()
    destinos: List[str] = []
    candidates = [
        ("madrid", "Madrid"), ("barcelona", "Barcelona"), ("españa", "España"), ("espana", "España"),
        ("miami", "Miami"), ("orlando", "Orlando"), ("cancún", "Cancún"), ("cancun", "Cancún"),
        ("punta cana", "Punta Cana"), ("río de janeiro", "Río de Janeiro"), ("rio", "Río de Janeiro"),
        ("brasil", "Brasil"), ("europa", "Europa"), ("bariloche", "Bariloche"), ("paris", "París"),
        ("parís", "París"), ("londres", "Londres"), ("italia", "Italia"), ("roma", "Roma"),
    ]
    for needle, label in candidates:
        if needle in t and label not in destinos:
            destinos.append(label)

    pax = None
    m = re.search(r"(\d+)\s*(pax|pas|persona|personas|adult)", t)
    if m:
        pax = int(m.group(1))
    elif "familia de" in t:
        m2 = re.search(r"familia de\s*(\d+)", t)
        if m2:
            pax = int(m2.group(1))

    dur = None
    m = re.search(r"(\d+)\s*(día|dias|días|noches|noche)", t)
    if m:
        dur = int(m.group(1))

    fecha = None
    months = {
        "enero": "01", "febrero": "02", "marzo": "03", "abril": "04", "mayo": "05",
        "junio": "06", "julio": "07", "agosto": "08", "septiembre": "09",
        "octubre": "10", "noviembre": "11", "diciembre": "12",
    }
    for name, num in months.items():
        if name in t:
            fecha = f"2026-{num}"
            break

    ref = None
    m = re.search(r"REF-?(\d{3,6})", text, re.IGNORECASE)
    if m:
        ref = f"REF-{m.group(1)}"

    return {
        "destinos": destinos,
        "pax": pax,
        "duracion_dias": dur,
        "fecha_aprox": fecha,
        "presupuesto_usd": None,
        "referencia_reserva": ref,
        "mocked": True,
    }


# --------------------------------------------------------------------------------------
# 3) Generar 2-3 sugerencias ranqueadas (Sonnet)
# --------------------------------------------------------------------------------------

def generate_suggestions(
    *,
    message_text: str,
    channel: str,
    case: Dict[str, Any],
    contact: Dict[str, Any],
    agent: Dict[str, Any],
    extracted: Dict[str, Any],
    classification: Dict[str, Any],
    provider_results: Optional[List[Dict[str, Any]]] = None,
    channel_rule: Optional[Dict[str, Any]] = None,
    storage: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """Devuelve hasta 3 sugerencias con etiqueta y body editable.
    Cada sugerencia: {label, body, sources: [...]}.
    """
    storage = storage if storage is not None else {}

    sources_for_badges: List[str] = []
    if provider_results:
        for pr in provider_results:
            sources_for_badges.append(pr.get("source", "Proveedor"))
    sources_for_badges.append(f"CRM: {contact.get('nombre', 'cliente')}")

    system = _build_suggestion_system_prompt(
        channel=channel,
        agent=agent,
        contact=contact,
        channel_rule=channel_rule or {},
    )
    user = json.dumps(
        {
            "mensaje_cliente": message_text,
            "canal": channel,
            "clasificacion": classification,
            "datos_extraidos": extracted,
            "caso": {
                "id": case.get("id"),
                "titulo": case.get("titulo"),
                "stage": case.get("stage"),
                "destino_principal": case.get("destino_principal"),
                "tags": case.get("tags", []),
            },
            "resultados_proveedores": provider_results or [],
            "instruccion": (
                "Devolvé JSON con la clave 'suggestions' que es una lista de 2 o 3 objetos. "
                "Cada objeto tiene: label (ej. 'Mejor tasa de cierre histórica', 'Más cálida', "
                "'Más concisa'), body (el texto a enviar al cliente, listo para usar, respetando "
                "la regla del canal), y rationale (1 frase de por qué esta opción)."
            ),
        },
        ensure_ascii=False,
    )
    parsed = _claude_json(
        SONNET,
        system,
        user,
        storage,
        "suggestions",
        max_tokens=1500,
        agent_id=agent.get("id"),
        case_id=case.get("id"),
    )
    if parsed and isinstance(parsed.get("suggestions"), list):
        out = []
        for s in parsed["suggestions"][:3]:
            out.append(
                {
                    "label": s.get("label", "Opción"),
                    "body": s.get("body", ""),
                    "rationale": s.get("rationale", ""),
                    "sources": sources_for_badges,
                }
            )
        return out

    # Mock con 3 plantillas según canal
    return _mock_suggestions(
        message_text=message_text,
        channel=channel,
        contact=contact,
        agent=agent,
        extracted=extracted,
        classification=classification,
        sources=sources_for_badges,
    )


def _build_suggestion_system_prompt(
    *, channel: str, agent: Dict[str, Any], contact: Dict[str, Any], channel_rule: Dict[str, Any]
) -> str:
    parts = [
        "Sos un copiloto IA para agentes de un turoperador B2B+B2C. Tu trabajo es proponer "
        "respuestas que el agente va a revisar antes de enviar.",
        "",
        f"### Agente que está respondiendo: {agent.get('nombre', 'agente')} ({agent.get('rol', 'agente')})",
        f"System prompt del agente: {agent.get('system_prompt', '(sin preferencias)')}",
        "",
        f"### Cliente al que respondemos: {contact.get('nombre', 'cliente')} ({contact.get('tipo', 'contacto')})",
        f"System prompt del cliente: {contact.get('system_prompt', '(sin preferencias)')}",
        "",
        f"### Canal: {channel}",
        f"Reglas de tono/formato del canal: {json.dumps(channel_rule, ensure_ascii=False) if channel_rule else '(usar buenas prácticas estándar)'}",
        "",
        "### Reglas duras",
        "- NUNCA inventes precios ni disponibilidad si no aparece en 'resultados_proveedores'. Si no hay datos, usá frases como 'estoy chequeando disponibilidad y te confirmo'.",
        "- Si el caso tiene una referencia de reserva, mencionala.",
        "- Respetá el tono del canal y las preferencias del agente y del cliente.",
        "- Generá 2 o 3 alternativas claramente distintas (no las hagas idénticas).",
    ]
    return "\n".join(parts)


def _mock_suggestions(
    *,
    message_text: str,
    channel: str,
    contact: Dict[str, Any],
    agent: Dict[str, Any],
    extracted: Dict[str, Any],
    classification: Dict[str, Any],
    sources: List[str],
) -> List[Dict[str, Any]]:
    nombre = contact.get("nombre", "cliente")
    primer_nombre = nombre.split()[0] if nombre else ""
    destinos = ", ".join(extracted.get("destinos") or []) or "el destino que mencionaste"
    tipo = classification.get("type", "consulta")
    severidad = classification.get("severity", "informativa")

    if severidad == "critica":
        opt_a = (
            f"Hola {primer_nombre}, recibimos tu mensaje y ya estamos atendiéndolo como "
            "prioridad. Mantenete tranquilo/a, en menos de 15 minutos un agente de guardia "
            "te llama para coordinar los próximos pasos."
        )
        opt_b = (
            f"{primer_nombre}, recibido. Te activamos asistencia inmediata: pasame por favor "
            "tu ubicación actual y un teléfono de contacto y te llamo en minutos."
        )
        opt_c = (
            f"Hola, vimos tu urgencia. Quedate tranquilo/a, ya te derivo con guardia. ¿Podés "
            "confirmarme dónde estás y un número donde puedan llamarte ahora?"
        )
    elif tipo == "cotizacion":
        opt_a = _email_or_wa(
            channel,
            primer_nombre,
            agent_name=agent.get("nombre", "el equipo"),
            destinos=destinos,
            extracted=extracted,
            tone="formal_estructurada",
        )
        opt_b = _email_or_wa(
            channel,
            primer_nombre,
            agent_name=agent.get("nombre", "el equipo"),
            destinos=destinos,
            extracted=extracted,
            tone="calida",
        )
        opt_c = _email_or_wa(
            channel,
            primer_nombre,
            agent_name=agent.get("nombre", "el equipo"),
            destinos=destinos,
            extracted=extracted,
            tone="concisa",
        )
    elif tipo == "pago":
        opt_a = "Recibido el comprobante, lo cargo al sistema y te confirmo apenas lo procesemos."
        opt_b = "Hola! Confirmado, ya queda acreditado en tu cuenta. Te aviso cuando esté impactado."
        opt_c = "Genial, gracias. Lo veo y te respondo con el comprobante de imputación."
    else:
        opt_a = f"Hola {primer_nombre}, recibimos tu mensaje sobre {destinos}. Te respondo con info concreta en el día."
        opt_b = f"Hola! Tomé nota de tu consulta sobre {destinos}, lo veo y te vuelvo en breve."
        opt_c = f"Hola {primer_nombre}, te confirmo apenas tenga novedades. ¿Hay alguna fecha tope que necesites?"

    return [
        {
            "label": "Mejor tasa de cierre histórica",
            "body": opt_a,
            "rationale": "Estructurada y clara, alinea con el patrón que más cierra en este tipo de cliente.",
            "sources": sources,
        },
        {
            "label": "Tono más cálido",
            "body": opt_b,
            "rationale": "Empática y personalizada, útil cuando el cliente parece dubitativo.",
            "sources": sources,
        },
        {
            "label": "Más concisa",
            "body": opt_c,
            "rationale": "Directa al grano, ideal para WhatsApp o clientes que prefieren brevedad.",
            "sources": sources,
        },
    ]


def _email_or_wa(
    channel: str,
    primer_nombre: str,
    agent_name: str,
    destinos: str,
    extracted: Dict[str, Any],
    tone: str,
) -> str:
    pax = extracted.get("pax")
    dur = extracted.get("duracion_dias")
    fecha = extracted.get("fecha_aprox")
    contexto = []
    if pax:
        contexto.append(f"{pax} pax")
    if dur:
        contexto.append(f"{dur} días")
    if fecha:
        contexto.append(fecha)
    contexto_str = ", ".join(contexto) if contexto else "los datos que me pasaste"

    if channel == "email":
        if tone == "formal_estructurada":
            return (
                f"Hola {primer_nombre},\n\n"
                f"Gracias por tu consulta. Para {destinos} ({contexto_str}) preparamos 2 opciones:\n\n"
                f"OPCIÓN A — Hoteles 4* céntricos\n"
                f"• Vuelos directos\n• Traslados privados\n• Desayuno incluido\n• USD —– (a confirmar disponibilidad)\n\n"
                f"OPCIÓN B — Hoteles 5* boutique\n"
                f"• Vuelos directos\n• Tours guiados incluidos\n• USD —– (a confirmar disponibilidad)\n\n"
                f"Apenas confirmes el perfil que te interesa cierro tarifas con disponibilidad real.\n\n"
                f"Saludos,\n{agent_name}"
            )
        if tone == "calida":
            return (
                f"Hola {primer_nombre}!\n\n"
                f"Qué lindo que estés pensando en {destinos} :) Tomo tus datos ({contexto_str}) y te armo 2 alternativas, "
                f"una más relajada y otra con más experiencias incluidas, así elegís la que más vibre con el grupo.\n\n"
                f"Te lo mando hoy con precios firmes. Cualquier preferencia (zona, tipo de hotel, vuelo) decime y lo sumo.\n\n"
                f"Un abrazo,\n{agent_name}"
            )
        # concisa
        return (
            f"Hola {primer_nombre},\n\n"
            f"Tomo nota: {destinos}, {contexto_str}. Te paso 2 opciones con precios hoy mismo.\n\n"
            f"Saludos,\n{agent_name}"
        )

    # WhatsApp
    if tone == "formal_estructurada":
        return (
            f"Hola {primer_nombre}! Tomo tu consulta para {destinos} ({contexto_str}). "
            f"Te armo 2 opciones (4* y 5*) con precios y te las paso hoy."
        )
    if tone == "calida":
        return (
            f"Hola {primer_nombre}! Genial el plan de {destinos} 😊 te armo 2 opciones según vibra del viaje "
            f"({contexto_str}) y te las mando hoy."
        )
    # concisa
    return f"Hola! Recibido — {destinos}, {contexto_str}. Te paso opciones hoy."


# --------------------------------------------------------------------------------------
# 4) Match de mensaje a Caso (Haiku)
# --------------------------------------------------------------------------------------

def match_message_to_case(
    *,
    text: str,
    contact: Dict[str, Any],
    open_cases: List[Dict[str, Any]],
    extracted: Dict[str, Any],
    storage: Dict[str, Any],
) -> Tuple[Optional[str], str]:
    """Devuelve (case_id, reason). Si no matchea, case_id=None.
    Para B2B: hay que distinguir entre los N Casos abiertos del contacto.
    """
    if not open_cases:
        return None, "No hay casos abiertos del contacto, se abrirá uno nuevo."

    if len(open_cases) == 1:
        return open_cases[0]["id"], "Único caso abierto del contacto."

    # Heurística rápida primero
    ref = (extracted or {}).get("referencia_reserva")
    if ref:
        for c in open_cases:
            if ref.lower() in (c.get("titulo", "").lower() + " " + c.get("id", "").lower()):
                return c["id"], f"Coincide referencia {ref}."

    destinos = [d.lower() for d in (extracted or {}).get("destinos") or []]
    if destinos:
        for c in open_cases:
            d_caso = (c.get("destino_principal") or "").lower()
            if any(d in d_caso or d_caso in d for d in destinos if d):
                return c["id"], f"Coincide destino ({d_caso})."

    # Fallback Claude
    system = (
        "Recibís un mensaje nuevo de un contacto que tiene varios Casos abiertos. "
        "Devolvé JSON con 'case_id' (uno de los provistos o null si no podés decidir) "
        "y 'reason' (1 frase)."
    )
    user = json.dumps(
        {
            "mensaje": text,
            "datos_extraidos": extracted,
            "casos_abiertos": [
                {"id": c["id"], "titulo": c.get("titulo"), "destino": c.get("destino_principal"), "stage": c.get("stage")}
                for c in open_cases
            ],
        },
        ensure_ascii=False,
    )
    parsed = _claude_json(HAIKU, system, user, storage, "match_case", max_tokens=200)
    if parsed and parsed.get("case_id") in {c["id"] for c in open_cases}:
        return parsed["case_id"], parsed.get("reason", "Match por IA.")
    return None, "Sin match claro entre los casos abiertos."


# --------------------------------------------------------------------------------------
# 5) Auto-tuning de prompts (Sonnet) — sólo genera la propuesta de diff, humano confirma
# --------------------------------------------------------------------------------------

def generate_tuning_review(
    *,
    target_type: str,  # "agente" | "cliente"
    target_id: str,
    current_prompt: str,
    sample_messages: List[Dict[str, Any]],
    storage: Dict[str, Any],
) -> Dict[str, Any]:
    """Devuelve {proposed_prompt, diff_summary, justification, evidence:[...]}."""
    system = (
        "Sos un revisor de system prompts. Te damos un prompt actual y una muestra de respuestas "
        "(mensajes enviados por el agente, sugerencias IA, ediciones, outcomes). Devolvés JSON con:\n"
        "- proposed_prompt: el prompt sugerido (puede ser idéntico si no hay mejora clara)\n"
        "- diff_summary: 1-3 bullets de los cambios concretos\n"
        "- justification: por qué\n"
        "- evidence: 2-3 ejemplos que apoyan el cambio (con id de mensaje)\n"
        "Sé conservador: no propongas cambios sin evidencia clara."
    )
    user = json.dumps(
        {
            "target_type": target_type,
            "target_id": target_id,
            "current_prompt": current_prompt,
            "sample_size": len(sample_messages),
            "sample": sample_messages[:30],
        },
        ensure_ascii=False,
    )
    parsed = _claude_json(SONNET, system, user, storage, "tuning_review", max_tokens=1200)
    if parsed:
        parsed.setdefault("proposed_prompt", current_prompt)
        parsed.setdefault("diff_summary", [])
        parsed.setdefault("justification", "")
        parsed.setdefault("evidence", [])
        return parsed

    # Mock con propuesta razonable
    return {
        "proposed_prompt": current_prompt
        + "\n\nNota agregada por auto-tuning: priorizar respuestas con 2 opciones de precio y referencia de cotización al final.",
        "diff_summary": [
            "Agregar: priorizar siempre 2 opciones con precio total.",
            "Agregar: incluir referencia de cotización al final.",
        ],
        "justification": "En 8 de las últimas 12 respuestas aceptadas el agente añadió manualmente la referencia y reescribió a 2 opciones.",
        "evidence": [{"message_id": "mock-1", "snippet": "Agregaste 'REF-1234' al final"}],
        "mocked": True,
    }
