"""
Tourbo / Flaptek GDS — integración de vuelos.

Flujo stateful:
  1. AVAIL  → lista de soluciones, cada una con solution_id
  2. PRICING → precio confirmado para una solución (token expira ~20 min)
  3. BOOKING → reserva efectiva (fuera de alcance del mockup)

Auth:
  HTTP Basic (username:password) + X-API-KEY header

Env vars:
  TOURBO_API_URL      (ej. https://api.dev.gateway.tourboplus.com)
  TOURBO_USERNAME
  TOURBO_PASSWORD
  TOURBO_API_KEY

Si las vars no están configuradas, devuelve mock realista para el demo.
"""
from __future__ import annotations

import base64
import os
from datetime import date, datetime
from typing import Any, Dict, List, Optional, Tuple

import httpx

# ── Config ────────────────────────────────────────────────────────────────────
API_URL      = os.getenv("TOURBO_API_URL",  "https://api.dev.gateway.tourboplus.com").strip().rstrip("/")
USERNAME     = os.getenv("TOURBO_USERNAME", "").strip()
PASSWORD     = os.getenv("TOURBO_PASSWORD", "").strip()
API_KEY      = os.getenv("TOURBO_API_KEY",  "").strip()
TIMEOUT      = 20.0


def is_configured() -> bool:
    return bool(USERNAME and PASSWORD and API_KEY)


def _auth_headers() -> Dict[str, str]:
    """Flaptek requiere Basic Auth + X-API-KEY en simultáneo."""
    raw = f"{USERNAME}:{PASSWORD}"
    encoded = base64.b64encode(raw.encode()).decode()
    return {
        "Authorization": f"Basic {encoded}",
        "X-API-KEY": API_KEY,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


# ── AVAIL ─────────────────────────────────────────────────────────────────────

def avail(
    origin: str,
    destination: str,
    dep_date: str,
    ret_date: Optional[str] = None,
    adults: int = 2,
    children: int = 0,
    infants: int = 0,
    cabin: str = "Y",
    currency: str = "USD",
) -> Dict[str, Any]:
    """
    Busca vuelos disponibles en Tourbo/Flaptek GDS.

    Schema descubierto (DEV):
      POST /v2/api/transports/avail?currency=USD|ARS
      Auth: Basic (base64) + X-API-KEY header
      Body: {
        searchKind: "ONE_WAY" | "ROUND_TRIP" | "OPEN_JAW",
        journeys: [{ origin, destination, departureDate }],
        pax: { adults, children, infants },
        cabin: "Y" | "C" | "F",
      }

    Nota DEV: el validador `presentOrFutureDatesValidation` tiene un NPE en el
    ambiente DEV (localDate null) que impide obtener respuestas reales. El
    mock es activado automáticamente cuando la API falla. Cuando el DEV env
    esté fixeado o se use PROD, el código ya tiene el schema correcto.
    """
    if not is_configured():
        return _mock_avail(origin, destination, dep_date, ret_date, adults)

    search_kind = "ROUND_TRIP" if ret_date else "ONE_WAY"
    journeys: List[Dict[str, Any]] = [
        {"origin": origin.upper(), "destination": destination.upper(), "departureDate": dep_date},
    ]
    if ret_date:
        journeys.append(
            {"origin": destination.upper(), "destination": origin.upper(), "departureDate": ret_date}
        )

    payload: Dict[str, Any] = {
        "searchKind": search_kind,
        "journeys": journeys,
        "pax": {"adults": adults, "children": children, "infants": infants},
        "cabin": cabin,
    }

    # currency es query param en Flaptek v2 (enum: USD, ARS)
    curr_param = currency.upper() if currency.upper() in ("USD", "ARS") else "USD"

    try:
        with httpx.Client(timeout=TIMEOUT) as client:
            r = client.post(
                f"{API_URL}/v2/api/transports/avail",
                params={"currency": curr_param},
                json=payload,
                headers=_auth_headers(),
            )
            if r.status_code == 200:
                data = r.json()
                options = _normalize_solutions(data, curr_param)
                return {
                    "ok": True,
                    "source": "Tourbo",
                    "origin": origin.upper(),
                    "destination": destination.upper(),
                    "dep_date": dep_date,
                    "ret_date": ret_date,
                    "options": options,
                    "raw": data,
                }
            # API respondió pero con error — log y fallback a mock
            err_text = ""
            try:
                err_data = r.json()
                err_text = err_data.get("detail") or err_data.get("title") or r.text[:300]
            except Exception:
                err_text = r.text[:300]
            mock = _mock_avail(origin, destination, dep_date, ret_date, adults)
            mock["api_error"] = f"HTTP {r.status_code}: {err_text[:200]}"
            return mock
    except Exception as exc:
        mock = _mock_avail(origin, destination, dep_date, ret_date, adults)
        mock["api_error"] = str(exc)
        return mock


def _normalize_solutions(raw: Dict[str, Any], currency: str) -> List[Dict[str, Any]]:
    """
    Convierte la respuesta cruda de Flaptek a una lista uniforme que el frontend entiende.
    Soporta los esquemas conocidos de Flaptek v1 y v2.
    """
    solutions = (
        raw.get("solutions")
        or raw.get("data", {}).get("solutions")
        or raw.get("results")
        or []
    )

    out: List[Dict[str, Any]] = []
    for sol in solutions:
        # ── precio ──
        price_block = sol.get("price") or sol.get("pricing") or {}
        total = (
            price_block.get("total")
            or price_block.get("total_amount")
            or price_block.get("amount")
            or sol.get("total_price")
            or sol.get("price")
            or 0
        )
        fare_currency = price_block.get("currency") or currency

        # ── segmentos de vuelo ──
        segs = (
            sol.get("segments")
            or sol.get("itinerary")
            or sol.get("legs")
            or []
        )

        # ── aerolínea (primera del primer segmento) ──
        airline = (
            sol.get("airline")
            or sol.get("carrier")
            or (segs[0].get("airline") or segs[0].get("carrier") if segs else "")
            or "—"
        )

        # ── paradas ──
        stops = sol.get("stops", len(segs) - 1 if len(segs) > 1 else 0)

        # ── duración ──
        duration = (
            sol.get("duration")
            or sol.get("total_duration")
            or _calc_duration(segs)
            or "—"
        )

        # ── horarios ──
        dep_time = ret_time = ""
        if segs:
            dep_time = segs[0].get("departure_time") or segs[0].get("dep_time") or ""
            ret_time = segs[-1].get("arrival_time") or segs[-1].get("arr_time") or ""

        out.append({
            "solution_id": sol.get("solution_id") or sol.get("id") or "",
            "airline": airline,
            "total_price": float(total),
            "currency": fare_currency,
            "stops": stops,
            "duration": duration,
            "dep_time": dep_time,
            "arr_time": ret_time,
            "segments": segs,
            "bookable": True,
            "source": "Tourbo",
        })

    # ordenar por precio
    out.sort(key=lambda x: x["total_price"])
    return out


def _calc_duration(segs: List[Dict[str, Any]]) -> str:
    """Calcula duración total de una lista de segmentos si están disponibles."""
    try:
        dep = segs[0].get("departure_datetime") or segs[0].get("dep_datetime") or ""
        arr = segs[-1].get("arrival_datetime") or segs[-1].get("arr_datetime") or ""
        if dep and arr:
            fmt = "%Y-%m-%dT%H:%M:%S"
            d = datetime.strptime(dep[:19], fmt)
            a = datetime.strptime(arr[:19], fmt)
            mins = int((a - d).total_seconds() / 60)
            return f"{mins // 60}h {mins % 60}m"
    except Exception:
        pass
    return ""


# ── PRICING ───────────────────────────────────────────────────────────────────

def pricing(solution_id: str, pax_count: int = 2, currency: str = "USD") -> Dict[str, Any]:
    """
    Confirma el precio de una solución antes de reservar.
    Devuelve dict con pricing_token y precio confirmado.
    """
    if not is_configured():
        return {
            "ok": True,
            "source": "Tourbo (mock)",
            "solution_id": solution_id,
            "pricing_token": f"mock-token-{solution_id[:8]}",
            "confirmed_price": 950.00,
            "currency": currency,
            "mocked": True,
        }
    try:
        with httpx.Client(timeout=TIMEOUT) as client:
            r = client.post(
                f"{API_URL}/v2/api/transports/pricing",
                json={
                    "solution_id": solution_id,
                    "passengers": {"adults": pax_count},
                    "currency": currency,
                },
                headers=_auth_headers(),
            )
            r.raise_for_status()
            data = r.json()
        token = (
            data.get("pricing_token")
            or data.get("token")
            or data.get("booking_token")
            or ""
        )
        price = (
            (data.get("price") or data.get("pricing") or {}).get("total")
            or data.get("total_price")
            or 0
        )
        curr = (
            (data.get("price") or data.get("pricing") or {}).get("currency")
            or currency
        )
        return {
            "ok": True,
            "source": "Tourbo",
            "solution_id": solution_id,
            "pricing_token": token,
            "confirmed_price": float(price),
            "currency": curr,
            "raw": data,
        }
    except httpx.HTTPStatusError as exc:
        return {
            "ok": False,
            "source": "Tourbo",
            "error": f"HTTP {exc.response.status_code}: {exc.response.text[:300]}",
        }
    except Exception as exc:
        return {"ok": False, "source": "Tourbo", "error": str(exc)}


# ── BOOKING (stub — fuera de alcance del mockup pero lista la interfaz) ────────

def booking(
    pricing_token: str,
    passengers: List[Dict[str, Any]],
    contact_email: str,
    currency: str = "USD",
) -> Dict[str, Any]:
    """
    Reserva efectiva. El mockup no la ejecuta (requiere datos reales de pasajeros).
    Devuelve siempre un stub de éxito para testing.
    """
    return {
        "ok": True,
        "source": "Tourbo (mock-booking)",
        "booking_ref": f"TRB-{pricing_token[:6].upper()}",
        "status": "confirmed",
        "mocked": True,
        "note": "Booking real pendiente de integración en prototipo fase 1",
    }


# ── Search helper (para el flujo automático del copiloto) ─────────────────────

def search_flights(destinos: List[str], extracted: Dict[str, Any]) -> Dict[str, Any]:
    """
    Wrapper de alto nivel para el copiloto.
    Acepta destinos (lista) + dict de datos extraídos y ejecuta AVAIL.
    """
    if not destinos:
        return {"source": "Tourbo", "ok": False, "error": "Sin destinos", "options": []}

    destination = destinos[0]
    dep_date = (
        extracted.get("fecha_salida")
        or extracted.get("fecha_aprox")
        or _next_month_str()
    )
    ret_date = extracted.get("fecha_retorno") or extracted.get("fecha_vuelta")
    adults = int(extracted.get("pax") or 2)
    currency = extracted.get("moneda") or "USD"

    from providers.serpapi import resolve_airport
    origin_iata = "EZE"
    dest_iata = resolve_airport(destination) or destination.upper()[:3]

    result = avail(
        origin=origin_iata,
        destination=dest_iata,
        dep_date=dep_date,
        ret_date=ret_date,
        adults=adults,
        currency=currency,
    )
    result["destino_texto"] = destination
    return result


def _next_month_str() -> str:
    from datetime import date, timedelta
    d = date.today() + timedelta(days=35)
    return d.strftime("%Y-%m-%d")


# ── Mock ──────────────────────────────────────────────────────────────────────

def _mock_avail(
    origin: str,
    destination: str,
    dep_date: str,
    ret_date: Optional[str] = None,
    adults: int = 2,
) -> Dict[str, Any]:
    """Mock de AVAIL con datos realistas para demo sin credenciales o cuando la API falla."""
    dest_upper = destination.upper()
    base_prices = {"MAD": 1150, "BCN": 1200, "MIA": 890, "JFK": 980, "GRU": 520,
                   "SCL": 380, "LIM": 420, "CDG": 1250, "FCO": 1180, "LHR": 1300}
    base = base_prices.get(dest_upper, 850)
    airlines_by_route = {
        "MAD": ["Iberia", "LATAM", "Air Europa"],
        "BCN": ["Vueling", "LATAM", "Iberia"],
        "MIA": ["American Airlines", "LATAM", "Copa"],
        "JFK": ["American Airlines", "Delta", "LATAM"],
        "GRU": ["LATAM", "Gol", "Aerolíneas AR"],
        "SCL": ["LATAM", "Sky", "Aerolíneas AR"],
    }
    airlines = airlines_by_route.get(dest_upper, ["LATAM", "Aerolíneas Argentinas", "Copa"])
    options = []
    for i, airline in enumerate(airlines[:3]):
        mult = 1 + i * 0.08
        total = round(base * adults * mult, 2)
        stops = i % 2
        hours = 12 + i * 2 if dest_upper in ("MAD","BCN","CDG","FCO","LHR") else (10 + i)
        options.append({
            "solution_id": f"mock-{dest_upper}-{i}",
            "airline": airline,
            "total_price": total,
            "currency": "USD",
            "stops": stops,
            "duration": f"{hours}h {15*i}m",
            "dep_time": f"{dep_date}T{8+i*2:02d}:30:00",
            "arr_time": f"{ret_date or dep_date}T{22-i:02d}:45:00",
            "segments": [],
            "bookable": True,
            "source": "Tourbo (mock)",
        })
    return {
        "ok": True,
        "source": "Tourbo (mock)",
        "mocked": True,
        "origin": origin.upper(),
        "destination": dest_upper,
        "dep_date": dep_date,
        "ret_date": ret_date,
        "options": options,
    }


def mock_flights(destinos: List[str], extracted: Dict[str, Any]) -> Dict[str, Any]:
    """Datos ficticios realistas para cuando no hay credenciales configuradas."""
    pax = int(extracted.get("pax") or 2)
    options: List[Dict[str, Any]] = []
    airlines = ["LATAM", "Aerolíneas Argentinas", "Iberia", "Air Europa"]
    bases = {"MAD": 1150, "BCN": 1200, "MIA": 890, "JFK": 950, "GRU": 520, "SCL": 380}
    for i, d in enumerate(destinos[:3]):
        from providers.serpapi import resolve_airport
        iata = resolve_airport(d) or d.upper()[:3]
        base = bases.get(iata, 800) + i * 30
        options.append({
            "solution_id": f"mock-{iata}-{i}",
            "airline": airlines[i % len(airlines)],
            "total_price": float(base * pax),
            "currency": "USD",
            "stops": i % 2,
            "duration": f"{10 + i}h {15 * i}m",
            "dep_time": "08:30",
            "arr_time": "22:45",
            "segments": [],
            "bookable": True,
            "source": "Tourbo (mock)",
        })
    return {
        "ok": True,
        "source": "Tourbo (mock)",
        "mocked": True,
        "options": options,
    }
