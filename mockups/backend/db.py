"""
Capa de acceso a Supabase (PostgreSQL directo via psycopg2).
Reemplaza la persistencia JSON con tres objetos en DB:
  - agents      → tabla normalizada con password_hash
  - storage_blob → JSONB blob (casos, mensajes, reminders, métricas, tuning)
  - crm_blob     → JSONB blob (agencias, pasajeros, tags, channel_rules)
"""
from __future__ import annotations

import json
import os
from contextlib import contextmanager
from typing import Any, Dict, List, Optional

import psycopg2
import psycopg2.extras

DATABASE_URL = os.getenv("DATABASE_URL")


@contextmanager
def get_conn():
    conn = psycopg2.connect(DATABASE_URL)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ── Storage blob ──────────────────────────────────────────────────────────────

def load_storage() -> Dict[str, Any]:
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT data FROM storage_blob WHERE id = 1")
        row = cur.fetchone()
    if not row:
        return _empty_storage()
    data: Dict[str, Any] = row[0]
    data.setdefault("messages", [])
    data.setdefault("cases", [])
    data.setdefault("tuning_reviews", [])
    data.setdefault("metrics", {"token_usage": []})
    data.setdefault("reminders", [])
    return data


def save_storage(s: Dict[str, Any]) -> None:
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            """INSERT INTO storage_blob (id, data) VALUES (1, %s)
               ON CONFLICT (id) DO UPDATE SET data = EXCLUDED.data""",
            (json.dumps(s, ensure_ascii=False),),
        )


def _empty_storage() -> Dict[str, Any]:
    return {"messages": [], "cases": [], "tuning_reviews": [],
            "metrics": {"token_usage": []}, "reminders": []}


# ── CRM blob ──────────────────────────────────────────────────────────────────

def load_crm() -> Dict[str, Any]:
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT data FROM crm_blob WHERE id = 1")
        row = cur.fetchone()
    crm: Dict[str, Any] = row[0] if row else {}
    crm.setdefault("agencias", [])
    crm.setdefault("pasajeros", [])
    crm.setdefault("tags_workspace", [])
    crm.setdefault("channel_rules_default", {})
    # Agents always come from the agents table, not from the blob
    crm["agentes"] = list_agents()
    return crm


def save_crm(crm: Dict[str, Any]) -> None:
    # Don't persist agentes inside the blob — they live in the agents table
    data = {k: v for k, v in crm.items() if k != "agentes"}
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            """INSERT INTO crm_blob (id, data) VALUES (1, %s)
               ON CONFLICT (id) DO UPDATE SET data = EXCLUDED.data""",
            (json.dumps(data, ensure_ascii=False),),
        )


# ── Agents table ──────────────────────────────────────────────────────────────

_AGENT_COLS = "id, nombre, rol, area, email, system_prompt, permisos_modelo, limite_tokens_dia"


def list_agents() -> List[Dict[str, Any]]:
    with get_conn() as conn:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(f"SELECT {_AGENT_COLS} FROM agents ORDER BY nombre")
        return [dict(r) for r in cur.fetchall()]


def get_agent_by_id(agent_id: str) -> Optional[Dict[str, Any]]:
    with get_conn() as conn:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(f"SELECT {_AGENT_COLS} FROM agents WHERE id = %s", (agent_id,))
        row = cur.fetchone()
    return dict(row) if row else None


def get_agent_with_hash(email: str) -> Optional[Dict[str, Any]]:
    """Devuelve el agente completo incluyendo password_hash — solo para /auth/login."""
    with get_conn() as conn:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("SELECT * FROM agents WHERE email = %s", (email.lower().strip(),))
        row = cur.fetchone()
    return dict(row) if row else None


def update_agent_system_prompt(agent_id: str, system_prompt: str) -> Optional[Dict[str, Any]]:
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            "UPDATE agents SET system_prompt = %s WHERE id = %s",
            (system_prompt, agent_id),
        )
    return get_agent_by_id(agent_id)


def create_agent(
    id: str, nombre: str, rol: str, area: str,
    email: str, password_hash: str,
    system_prompt: str = "",
    permisos_modelo: List[str] = None,
    limite_tokens_dia: int = 200000,
) -> Dict[str, Any]:
    if permisos_modelo is None:
        permisos_modelo = ["haiku", "sonnet"]
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            """INSERT INTO agents (id, nombre, rol, area, email, password_hash,
               system_prompt, permisos_modelo, limite_tokens_dia)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
               ON CONFLICT (id) DO NOTHING""",
            (id, nombre, rol, area, email.lower().strip(), password_hash,
             system_prompt, permisos_modelo, limite_tokens_dia),
        )
    return get_agent_by_id(id)
