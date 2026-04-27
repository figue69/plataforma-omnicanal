"""
SerpAPI — Google Flights Engine
Búsqueda inteligente con flexibilidad de fechas, opciones de equipaje y mapeo IATA.

Estrategia de llamadas (máx 9 por búsqueda):
  Fase 1 — 4 llamadas paralelas: D, D-1, D-2, D-3 con retorno fijo
  Fase 2 — 4 llamadas paralelas: mejor salida con R, R-1, R-2, R-3
  Fase 3 — 1 llamada: opción más barata con equipaje
"""
from __future__ import annotations

import asyncio
import os
from datetime import date, timedelta
from typing import Any, Dict, List, Optional, Tuple

import httpx

API_KEY = os.getenv("SERPAPI_KEY", "").strip()
BASE_URL = "https://serpapi.com/search.json"

# ── Mapeo destino (texto libre) → código IATA ────────────────────────────────
AIRPORT_MAP: Dict[str, str] = {
    # Argentina
    "buenos aires": "EZE", "ezeiza": "EZE", "aeroparque": "AEP",
    "bariloche": "BRC", "mendoza": "MDZ", "cordoba": "COR", "córdoba": "COR",
    "rosario": "ROS", "salta": "SLA", "iguazu": "IGR", "iguazú": "IGR",
    "cataratas": "IGR", "tucuman": "TUC", "tucumán": "TUC",
    "mar del plata": "MDQ", "ushuaia": "USH", "neuquen": "NQN", "neuquén": "NQN",
    "puerto madryn": "PMY", "el calafate": "FTE", "calafate": "FTE",
    "jujuy": "JUJ", "posadas": "PSS", "resistencia": "RES",
    # Europa
    "madrid": "MAD", "españa": "MAD",
    "barcelona": "BCN",
    "paris": "CDG", "parís": "CDG", "france": "CDG", "francia": "CDG",
    "roma": "FCO", "rome": "FCO", "italia": "FCO",
    "milan": "MXP", "milán": "MXP", "milano": "MXP",
    "venecia": "VCE", "venice": "VCE",
    "amsterdam": "AMS", "holanda": "AMS", "países bajos": "AMS",
    "london": "LHR", "londres": "LHR", "england": "LHR", "reino unido": "LHR",
    "frankfurt": "FRA", "alemania": "FRA",
    "zurich": "ZRH", "zürich": "ZRH", "suiza": "ZRH",
    "lisboa": "LIS", "portugal": "LIS",
    "dublin": "DUB", "dublín": "DUB", "irlanda": "DUB",
    "atenas": "ATH", "athens": "ATH", "grecia": "ATH",
    "estambul": "IST", "istanbul": "IST", "turquia": "IST", "turquía": "IST",
    "viena": "VIE", "austria": "VIE",
    "bruselas": "BRU", "belgica": "BRU", "bélgica": "BRU",
    "praga": "PRG", "republica checa": "PRG",
    "budapest": "BUD", "hungria": "BUD", "hungría": "BUD",
    "varsovia": "WAW", "polonia": "WAW",
    "zagreb": "ZAG", "croacia": "ZAG",
    "niza": "NCE", "marsella": "MRS",
    "sevilla": "SVQ", "bilbao": "BIO",
    "palma": "PMI", "mallorca": "PMI",
    "tenerife": "TFS", "canarias": "TFS",
    "porto": "OPO",
    "oslo": "OSL", "noruega": "OSL",
    "estocolmo": "ARN", "suecia": "ARN",
    "copenhague": "CPH", "dinamarca": "CPH",
    "helsinki": "HEL", "finlandia": "HEL",
    "munich": "MUC", "múnich": "MUC",
    "dusseldorf": "DUS", "düsseldorf": "DUS",
    "hamburgo": "HAM",
    "berlin": "BER", "berlín": "BER",
    "varsobia": "WAW",
    "bucarest": "OTP", "rumania": "OTP",
    "sofia": "SOF", "bulgaria": "SOF",
    "belgrado": "BEG", "serbia": "BEG",
    # EE.UU.
    "miami": "MIA", "florida": "MIA",
    "nueva york": "JFK", "new york": "JFK", "nyc": "JFK",
    "los angeles": "LAX", "la": "LAX", "california": "LAX",
    "orlando": "MCO",
    "chicago": "ORD",
    "boston": "BOS",
    "san francisco": "SFO",
    "las vegas": "LAS",
    "washington": "DCA",
    "atlanta": "ATL",
    "fort lauderdale": "FLL",
    "houston": "IAH",
    "dallas": "DFW",
    "seattle": "SEA",
    "denver": "DEN",
    "nueva orleans": "MSY",
    "tampa": "TPA",
    "phoenix": "PHX",
    "nueva york newark": "EWR",
    # Canadá
    "toronto": "YYZ", "canada": "YYZ", "canadá": "YYZ",
    "montreal": "YUL",
    "vancouver": "YVR",
    # México / Centroamérica
    "ciudad de mexico": "MEX", "mexico": "MEX", "cdmx": "MEX", "méxico": "MEX",
    "cancun": "CUN", "cancún": "CUN",
    "panama": "PTY", "panamá": "PTY",
    "san jose": "SJO", "san josé": "SJO", "costa rica": "SJO",
    "guatemala": "GUA",
    "san salvador": "SAL", "el salvador": "SAL",
    "managua": "MGA", "nicaragua": "MGA",
    "tegucigalpa": "TGU", "honduras": "TGU",
    "belize": "BZE",
    # Caribe
    "punta cana": "PUJ", "republica dominicana": "PUJ", "república dominicana": "PUJ",
    "santo domingo": "SDQ",
    "habana": "HAV", "la habana": "HAV", "cuba": "HAV",
    "jamaica": "KIN",
    "aruba": "AUA",
    "curacao": "CUR", "curaçao": "CUR",
    "trinidad": "POS",
    "barbados": "BGI",
    "nassau": "NAS", "bahamas": "NAS",
    "san juan": "SJU", "puerto rico": "SJU",
    # Sudamérica
    "lima": "LIM", "peru": "LIM", "perú": "LIM",
    "santiago": "SCL", "chile": "SCL",
    "sao paulo": "GRU", "são paulo": "GRU", "brasil": "GRU", "brazil": "GRU",
    "rio de janeiro": "GIG", "rio": "GIG",
    "bogota": "BOG", "bogotá": "BOG", "colombia": "BOG",
    "medellin": "MDE", "medellín": "MDE",
    "quito": "UIO", "ecuador": "UIO",
    "guayaquil": "GYE",
    "caracas": "CCS", "venezuela": "CCS",
    "montevideo": "MVD", "uruguay": "MVD",
    "asuncion": "ASU", "asunción": "ASU", "paraguay": "ASU",
    "la paz": "LPB", "bolivia": "LPB",
    "santa cruz": "VVI",
    "cali": "CLO",
    "cartagena": "CTG",
    # Medio Oriente
    "dubai": "DXB", "emiratos": "DXB", "uae": "DXB",
    "doha": "DOH", "qatar": "DOH",
    "abu dhabi": "AUH",
    "tel aviv": "TLV", "israel": "TLV",
    "amman": "AMM", "jordania": "AMM",
    "beirut": "BEY", "libano": "BEY", "líbano": "BEY",
    # Asia
    "bangkok": "BKK", "tailandia": "BKK",
    "tokio": "NRT", "tokyo": "NRT", "japon": "NRT", "japón": "NRT",
    "singapur": "SIN", "singapore": "SIN",
    "bali": "DPS", "indonesia": "DPS",
    "phuket": "HKT",
    "hong kong": "HKG",
    "shanghai": "PVG",
    "beijing": "PEK", "pekin": "PEK", "pekín": "PEK",
    "seoul": "ICN", "seul": "ICN", "seúl": "ICN", "corea": "ICN",
    "kuala lumpur": "KUL", "malasia": "KUL",
    "manila": "MNL", "filipinas": "MNL",
    "colombo": "CMB", "sri lanka": "CMB",
    # África
    "johannesburgo": "JNB", "sudafrica": "JNB", "sudáfrica": "JNB",
    "nairobi": "NBO", "kenia": "NBO",
    "cairo": "CAI", "el cairo": "CAI", "egipto": "CAI",
    "casablanca": "CMN", "marruecos": "CMN",
    # Oceanía
    "sydney": "SYD", "australia": "SYD",
    "melbourne": "MEL",
    "auckland": "AKL", "nueva zelanda": "AKL",
}


def is_configured() -> bool:
    return bool(API_KEY)


def resolve_airport(text: str) -> Optional[str]:
    """Mapea texto libre a código IATA. Retorna None si no hay match."""
    key = text.lower().strip()
    # Match exacto
    if key in AIRPORT_MAP:
        return AIRPORT_MAP[key]
    # Match parcial (el texto contiene una clave conocida)
    for k, v in AIRPORT_MAP.items():
        if k in key or key in k:
            return v
    # Si parece un código IATA de 3 letras, usarlo directamente
    if len(key) == 3 and key.isalpha():
        return key.upper()
    return None


def _fmt_duration(minutes: int) -> str:
    h, m = divmod(minutes, 60)
    return f"{h}h {m:02d}min"


def _fmt_flight(flight_data: Dict[str, Any]) -> Dict[str, Any]:
    """Convierte un item de best_flights/other_flights a formato limpio."""
    segments = flight_data.get("flights", [])
    layovers = flight_data.get("layovers", [])
    total_min = flight_data.get("total_duration", 0)
    price = flight_data.get("price")

    seg_list = []
    for s in segments:
        dep = s.get("departure_airport", {})
        arr = s.get("arrival_airport", {})
        seg_list.append({
            "airline": s.get("airline", ""),
            "flight_number": s.get("flight_number", ""),
            "from": dep.get("id", ""),
            "from_time": dep.get("time", ""),
            "to": arr.get("id", ""),
            "to_time": arr.get("time", ""),
            "duration_min": s.get("duration", 0),
            "duration_fmt": _fmt_duration(s.get("duration", 0)),
        })

    lay_list = []
    for lay in layovers:
        lay_list.append({
            "airport": lay.get("id", ""),
            "airport_name": lay.get("name", ""),
            "duration_min": lay.get("duration", 0),
            "duration_fmt": _fmt_duration(lay.get("duration", 0)),
            "overnight": lay.get("overnight", False),
        })

    stops = len(segments) - 1
    stop_label = "Directo" if stops == 0 else f"{stops} escala{'s' if stops > 1 else ''}"

    return {
        "price": price,
        "total_duration_min": total_min,
        "total_duration_fmt": _fmt_duration(total_min),
        "stops": stops,
        "stop_label": stop_label,
        "segments": seg_list,
        "layovers": lay_list,
        "airlines": list({s["airline"] for s in seg_list}),
    }


async def _call(params: Dict[str, Any], client: httpx.AsyncClient) -> Dict[str, Any]:
    """Llamada individual a SerpAPI."""
    try:
        r = await client.get(BASE_URL, params={**params, "api_key": API_KEY}, timeout=15)
        r.raise_for_status()
        data = r.json()
        best = data.get("best_flights", []) + data.get("other_flights", [])
        if not best:
            return {"ok": False, "error": "Sin resultados", "params": params}
        return {"ok": True, "flights": [_fmt_flight(f) for f in best[:3]], "params": params}
    except Exception as e:
        return {"ok": False, "error": str(e), "params": params}


async def search_flexible_async(
    origin: str,
    destination: str,
    dep_date: str,          # YYYY-MM-DD
    ret_date: Optional[str],# YYYY-MM-DD o None si es ida
    adults: int = 1,
    currency: str = "USD",
    flex_days: int = 3,
) -> Dict[str, Any]:
    """
    Búsqueda con flexibilidad de fechas.
    - flex_days días hacia atrás en salida (D, D-1 ... D-flex)
    - flex_days días hacia atrás en retorno (R, R-1 ... R-flex)
    - Opción más barata repetida con 1 valija facturada
    """
    dep = date.fromisoformat(dep_date)
    dep_dates = [dep - timedelta(days=i) for i in range(flex_days + 1)]

    base_params = {
        "engine": "google_flights",
        "departure_id": origin.upper(),
        "arrival_id": destination.upper(),
        "adults": adults,
        "currency": currency,
        "hl": "es",
        "type": "1" if ret_date else "2",
        "bags": 0,
    }
    if ret_date:
        base_params["return_date"] = ret_date

    async with httpx.AsyncClient() as client:
        # ── Fase 1: variaciones de salida (paralelo) ─────────────────────────
        phase1_tasks = [
            _call({**base_params, "outbound_date": d.isoformat()}, client)
            for d in dep_dates
        ]
        phase1 = await asyncio.gather(*phase1_tasks)

        # ── Fase 2: variaciones de retorno con la mejor salida (paralelo) ────
        phase2: List[Dict] = []
        if ret_date:
            ret = date.fromisoformat(ret_date)
            ret_dates = [ret - timedelta(days=i) for i in range(1, flex_days + 1)]

            # Mejor fecha de salida (menor precio)
            best_dep_result = _cheapest_result(phase1)
            best_dep_date = (best_dep_result or {}).get("params", {}).get("outbound_date", dep_date)

            phase2_tasks = [
                _call({**base_params, "outbound_date": best_dep_date, "return_date": r.isoformat()}, client)
                for r in ret_dates
            ]
            phase2 = await asyncio.gather(*phase2_tasks)

        # ── Fase 3: opción más barata con equipaje ───────────────────────────
        all_results = list(phase1) + list(phase2)
        cheapest = _cheapest_result(all_results)
        bags_result: Optional[Dict] = None
        if cheapest:
            bags_result = await _call({
                **base_params,
                "outbound_date": cheapest["params"].get("outbound_date", dep_date),
                "return_date": cheapest["params"].get("return_date", ret_date or ""),
                "bags": 1,
            }, client)

    return _build_summary(
        dep_date=dep_date,
        ret_date=ret_date,
        phase1=list(phase1),
        phase2=list(phase2),
        bags_result=bags_result,
        currency=currency,
        origin=origin,
        destination=destination,
    )


def _cheapest_result(results: List[Dict]) -> Optional[Dict]:
    valid = [r for r in results if r.get("ok") and r.get("flights")]
    if not valid:
        return None
    return min(valid, key=lambda r: r["flights"][0].get("price") or 9999)


def _build_summary(
    dep_date: str,
    ret_date: Optional[str],
    phase1: List[Dict],
    phase2: List[Dict],
    bags_result: Optional[Dict],
    currency: str,
    origin: str,
    destination: str,
) -> Dict[str, Any]:
    """Construye el resumen final con las mejores opciones para mostrar al agente."""

    def best_flight(result: Dict) -> Optional[Dict]:
        if not result.get("ok") or not result.get("flights"):
            return None
        f = result["flights"][0]
        f["outbound_date"] = result["params"].get("outbound_date", dep_date)
        f["return_date"] = result["params"].get("return_date", ret_date)
        return f

    # Vuelo de fecha exacta (primer item de phase1 = fecha D exacta)
    exact = best_flight(phase1[0]) if phase1 else None

    # Opción más barata de las variaciones de salida
    cheapest_dep = _cheapest_result(phase1)
    best_dep = best_flight(cheapest_dep) if cheapest_dep else None

    # Opción más barata de las variaciones de retorno
    cheapest_ret = _cheapest_result(phase2)
    best_ret = best_flight(cheapest_ret) if cheapest_ret else None

    # Con equipaje
    bags = best_flight(bags_result) if bags_result and bags_result.get("ok") else None

    options = []
    seen_dates: set = set()

    for label, f in [
        ("Fecha exacta solicitada", exact),
        ("Mejor precio — salida anticipada", best_dep),
        ("Mejor precio — retorno anticipado", best_ret),
    ]:
        if not f:
            continue
        key = (f.get("outbound_date"), f.get("return_date"))
        if key in seen_dates:
            continue
        seen_dates.add(key)
        options.append({"label": label, "flight": f, "bags": False})

    # Precio con equipaje sobre la opción más barata
    if bags:
        cheapest_no_bag = min(
            (o["flight"]["price"] for o in options if o["flight"].get("price")),
            default=None,
        )
        bag_price = bags.get("price")
        delta = (bag_price - cheapest_no_bag) if (bag_price and cheapest_no_bag) else None
        options.append({
            "label": "Opción más barata + 1 valija 23kg",
            "flight": bags,
            "bags": True,
            "bag_delta": delta,
        })

    # Texto formateado para incluir en el contexto IA
    summary_text = _format_for_ai(options, currency, origin, destination)

    return {
        "source": "SerpAPI/GoogleFlights",
        "ok": bool(options),
        "currency": currency,
        "origin": origin,
        "destination": destination,
        "options": options,
        "summary_text": summary_text,
        "calls_made": len(phase1) + len(phase2) + (1 if bags_result else 0),
    }


def _format_for_ai(options: List[Dict], currency: str, origin: str, destination: str) -> str:
    if not options:
        return f"Sin resultados de vuelos {origin}→{destination}."

    lines = [f"VUELOS {origin} → {destination} ({currency})"]
    lines.append("─" * 45)

    for opt in options:
        label = opt["label"]
        f = opt["flight"]
        dep_d = f.get("outbound_date", "")
        ret_d = f.get("return_date", "")
        price = f.get("price", "?")
        dur = f.get("total_duration_fmt", "")
        stop_lbl = f.get("stop_label", "")
        airlines = " + ".join(f.get("airlines", []))

        date_str = dep_d
        if ret_d:
            date_str += f" → {ret_d}"

        lines.append(f"\n📅 {label}")
        lines.append(f"   {date_str} · {airlines}")
        lines.append(f"   {stop_lbl} · {dur}")

        # Conexiones
        for lay in f.get("layovers", []):
            overnight = " (overnight)" if lay.get("overnight") else ""
            lines.append(f"   ↳ Escala {lay['airport']}: {lay['duration_fmt']}{overnight}")

        bag_note = " (con 1 valija 23kg)" if opt.get("bags") else " (solo carry-on)"
        delta = opt.get("bag_delta")
        delta_note = f" (+{currency} {delta} vs sin valija)" if delta else ""
        lines.append(f"   💰 {currency} {price}/pax{bag_note}{delta_note}")

    return "\n".join(lines)


# ── API pública síncrona (wrapper para main.py) ───────────────────────────────

def search_flights_flexible(
    origin: str,
    destination: str,
    dep_date: str,
    ret_date: Optional[str] = None,
    adults: int = 1,
    currency: str = "USD",
    flex_days: int = 3,
) -> Dict[str, Any]:
    """Wrapper síncrono para llamar desde endpoints FastAPI síncronos."""
    if not is_configured():
        return {"source": "SerpAPI", "ok": False, "error": "SERPAPI_KEY no configurada"}
    try:
        return asyncio.run(search_flexible_async(
            origin=origin,
            destination=destination,
            dep_date=dep_date,
            ret_date=ret_date,
            adults=adults,
            currency=currency,
            flex_days=flex_days,
        ))
    except Exception as e:
        return {"source": "SerpAPI", "ok": False, "error": str(e)}


# ── Compatibilidad con código existente ──────────────────────────────────────

def search_flights(destinos: List[str], extracted: Dict[str, Any]) -> Dict[str, Any]:
    """Mantiene compatibilidad con el código de main.py (POST /messages)."""
    if not is_configured():
        return {"source": "SerpAPI", "ok": False, "error": "no configurado"}
    dest_text = destinos[0] if destinos else ""
    dest_iata = resolve_airport(dest_text) or dest_text[:3].upper()
    dep_date = extracted.get("fecha_salida") or extracted.get("fecha_aprox") or "2026-07-15"
    ret_date = extracted.get("fecha_retorno")
    adults = extracted.get("pax") or 1
    if isinstance(dep_date, str) and len(dep_date) == 7:
        dep_date = dep_date + "-15"
    return search_flights_flexible(
        origin="EZE",
        destination=dest_iata,
        dep_date=dep_date,
        ret_date=ret_date,
        adults=adults,
        currency="USD",
        flex_days=1,  # En el flujo automático usamos flex reducido para no demorar
    )
