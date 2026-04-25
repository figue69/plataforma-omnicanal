"""Stub de SerpAPI (Google Flights / Hotels). Sólo se usa si SERPAPI_KEY está seteada.
"""
from __future__ import annotations

import os
from typing import Any, Dict, List

import httpx

API_KEY = os.getenv("SERPAPI_KEY", "").strip()


def is_configured() -> bool:
    return bool(API_KEY)


def search_flights(destinos: List[str], extracted: Dict[str, Any]) -> Dict[str, Any]:
    if not is_configured():
        return {"source": "SerpAPI", "ok": False, "error": "no configurado"}
    try:
        with httpx.Client(timeout=10.0) as client:
            r = client.get(
                "https://serpapi.com/search.json",
                params={
                    "engine": "google_flights",
                    "departure_id": "EZE",
                    "arrival_id": destinos[0][:3].upper() if destinos else "MIA",
                    "outbound_date": (extracted.get("fecha_aprox") or "2026-07") + "-15",
                    "currency": "USD",
                    "hl": "es",
                    "api_key": API_KEY,
                },
            )
            r.raise_for_status()
            return {"source": "SerpAPI", "ok": True, "data": r.json().get("best_flights", [])[:3]}
    except Exception as exc:  # noqa: BLE001
        return {"source": "SerpAPI", "ok": False, "error": str(exc)}
