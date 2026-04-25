"""Stub de Tourbo (aéreos). Si TOURBO_API_URL/KEY están en env, hace request real.
Si no, devuelve resultados mock realistas para que el demo funcione.
"""
from __future__ import annotations

import os
from typing import Any, Dict, List

import httpx

API_URL = os.getenv("TOURBO_API_URL", "").strip()
API_KEY = os.getenv("TOURBO_API_KEY", "").strip()


def is_configured() -> bool:
    return bool(API_URL and API_KEY)


def search_flights(destinos: List[str], extracted: Dict[str, Any]) -> Dict[str, Any]:
    if not is_configured():
        return mock_flights(destinos, extracted)
    try:
        with httpx.Client(timeout=8.0) as client:
            r = client.get(
                API_URL.rstrip("/") + "/flights/search",
                params={
                    "destinations": ",".join(destinos),
                    "pax": extracted.get("pax") or 1,
                    "month": extracted.get("fecha_aprox"),
                },
                headers={"Authorization": f"Bearer {API_KEY}"},
            )
            r.raise_for_status()
            return {"source": "Tourbo", "ok": True, "data": r.json()}
    except Exception as exc:  # noqa: BLE001
        return {"source": "Tourbo", "ok": False, "error": str(exc), "data": mock_flights(destinos, extracted)["data"]}


def mock_flights(destinos: List[str], extracted: Dict[str, Any]) -> Dict[str, Any]:
    pax = extracted.get("pax") or 2
    options = []
    for d in destinos[:2]:
        options.append(
            {
                "destino": d,
                "aerolinea": "LATAM",
                "precio_usd": 850 + len(d) * 7,
                "escalas": 1,
                "duracion_aprox": "12h",
                "pax": pax,
            }
        )
    return {"source": "Tourbo (mock)", "ok": True, "mocked": True, "data": {"options": options}}
