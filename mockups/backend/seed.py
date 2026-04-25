"""
Carga inicial de datos en Supabase.
Crea las tablas (si no existen) y pobla:
  - agents       → agentes reales con passwords hasheados
  - crm_blob     → agencias, pasajeros, tags, channel_rules

Uso:
    pip install -r requirements.txt
    DATABASE_URL="postgresql://..." python seed.py

    O con .env:
    python seed.py
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

import db
import auth

ROOT = Path(__file__).parent


def run_schema():
    schema_sql = (ROOT / "schema.sql").read_text()
    with db.get_conn() as conn:
        cur = conn.cursor()
        cur.execute(schema_sql)
    print("✓ Schema aplicado")


def seed_agents():
    """
    Agentes iniciales con password temporal 'plataforma2026'.
    Cada agente debe cambiar su password (funcionalidad a agregar en v1).
    """
    agents = [
        {
            "id": "ag-ana",
            "nombre": "Ana López",
            "rol": "agente",
            "area": "ventas",
            "email": "ana@empresa.com.ar",
            "system_prompt": (
                "Soy agente de ventas senior. Prefiero respuestas con estructura: "
                "saludo breve, 2-3 opciones con precios, próximo paso claro. "
                "Evitar emojis en email. En WhatsApp aceptar 1 emoji al final."
            ),
            "permisos_modelo": ["haiku", "sonnet"],
            "limite_tokens_dia": 200000,
        },
        {
            "id": "ag-carlos",
            "nombre": "Carlos Rivero",
            "rol": "agente",
            "area": "guardia",
            "email": "carlos@empresa.com.ar",
            "system_prompt": (
                "Agente de guardia 24/7. Prioridad: tranquilizar al pasajero, "
                "confirmar que recibí, dar plan de acción concreto en menos de 3 frases. "
                "Sin saludos largos."
            ),
            "permisos_modelo": ["haiku", "sonnet"],
            "limite_tokens_dia": 100000,
        },
        {
            "id": "sup-maria",
            "nombre": "María Vázquez",
            "rol": "supervisor",
            "area": "ventas",
            "email": "maria@empresa.com.ar",
            "system_prompt": (
                "Supervisora de Ventas. Aprueba tunings de prompts y revisa cuentas grandes."
            ),
            "permisos_modelo": ["haiku", "sonnet", "opus"],
            "limite_tokens_dia": 500000,
        },
    ]

    default_password = "plataforma2026"
    hashed = auth.hash_password(default_password)

    for a in agents:
        db.create_agent(
            id=a["id"],
            nombre=a["nombre"],
            rol=a["rol"],
            area=a["area"],
            email=a["email"],
            password_hash=hashed,
            system_prompt=a["system_prompt"],
            permisos_modelo=a["permisos_modelo"],
            limite_tokens_dia=a["limite_tokens_dia"],
        )
        print(f"  ✓ Agente {a['nombre']} ({a['email']})")

    print(f"  → Password inicial de todos: {default_password}")


def seed_crm():
    crm = {
        "agencias": [
            {
                "id": "ag-polo",
                "tipo": "agencia",
                "nombre": "Polo Viajes",
                "ubicacion": "Buenos Aires, AR",
                "vendedores": [
                    {"id": "v-laura", "nombre": "Laura González",
                     "email": "laura@poloviajes.com.ar", "whatsapp": "+5491140000001"},
                    {"id": "v-miguel", "nombre": "Miguel Sosa",
                     "email": "miguel@poloviajes.com.ar", "whatsapp": "+5491140000002"},
                ],
                "datos_comerciales_informativos": {
                    "categoria": "PREMIUM",
                    "comision_base_pct": 12,
                    "credito_disponible_usd": 25000,
                    "forma_de_pago_preferida": "transferencia",
                },
                "tasa_conversion_pct": 38,
                "ticket_promedio_usd": 4200,
                "system_prompt": (
                    "La Agencia Polo Viajes es premium y prioriza claridad y velocidad. "
                    "Prefiere recibir 2 opciones con precio total en lugar de 1. "
                    "Tono profesional pero cálido. Siempre incluir referencia de cotización al final."
                ),
                "consentimiento_whatsapp": True,
                "consentimiento_fecha": "2026-01-15",
            },
            {
                "id": "ag-sur",
                "tipo": "agencia",
                "nombre": "Sur Aventura",
                "ubicacion": "Bariloche, AR",
                "vendedores": [
                    {"id": "v-pedro", "nombre": "Pedro Iturriaga",
                     "email": "pedro@suraventura.com", "whatsapp": "+5492944000003"},
                ],
                "datos_comerciales_informativos": {
                    "categoria": "STANDARD",
                    "comision_base_pct": 10,
                    "credito_disponible_usd": 8000,
                    "forma_de_pago_preferida": "tarjeta",
                },
                "tasa_conversion_pct": 22,
                "ticket_promedio_usd": 1800,
                "system_prompt": (
                    "Agencia chica, enfoque en aventura y nieve. "
                    "Tono más informal está OK. "
                    "Suelen preguntar mucho por incluido/no incluido — siempre dejar eso explícito."
                ),
                "consentimiento_whatsapp": True,
                "consentimiento_fecha": "2025-11-02",
            },
        ],
        "pasajeros": [
            {
                "id": "px-perez",
                "tipo": "pasajero",
                "nombre": "Familia Pérez",
                "contacto_principal": "Juan Pérez",
                "email": "juan.perez@gmail.com",
                "whatsapp": "+5491150000010",
                "idioma": "es",
                "system_prompt": (
                    "Familia con 2 menores de edad. Buscan destinos seguros, all-inclusive. "
                    "Tono familiar, evitar jerga técnica. "
                    "Confirmar siempre incluido (comidas, traslados, equipaje)."
                ),
                "historial_viajes": [
                    {"destino": "Punta Cana", "fecha": "2024-07", "monto_usd": 6500}
                ],
            },
            {
                "id": "px-martinez",
                "tipo": "pasajero",
                "nombre": "Ana Martínez",
                "contacto_principal": "Ana Martínez",
                "email": "anam@hotmail.com",
                "whatsapp": "+5491150000011",
                "idioma": "es",
                "system_prompt": (
                    "Viaja sola, 38 años, profesional. Prefiere boutique hotels y experiencias culturales. "
                    "Tono moderno, conciso. Ofrecer opciones premium sin abrumar con info."
                ),
                "historial_viajes": [
                    {"destino": "Madrid + Barcelona", "fecha": "2025-04", "monto_usd": 3200}
                ],
            },
        ],
        "tags_workspace": [
            {"id": "t-europa", "nombre": "Europa", "color": "#3B82F6"},
            {"id": "t-caribe", "nombre": "Caribe", "color": "#06B6D4"},
            {"id": "t-grupo", "nombre": "Grupo", "color": "#8B5CF6"},
            {"id": "t-vip", "nombre": "VIP", "color": "#F59E0B"},
            {"id": "t-cotizacion-perdida", "nombre": "Cotización perdida", "color": "#EF4444"},
            {"id": "t-urgente", "nombre": "Urgente", "color": "#DC2626"},
        ],
        "channel_rules_default": {
            "whatsapp": {
                "tono": "informal y conciso",
                "longitud_max_chars": 600,
                "saludo": "corto o ninguno si la conversación está activa",
                "emojis": "permitidos con moderación, máximo 1 al final",
                "estructura": "párrafos cortos, máximo 2-3 frases por bloque",
                "links": "acortados o directos, evitar markdown",
            },
            "email": {
                "tono": "semi-formal, profesional pero cálido",
                "longitud_max_chars": 3000,
                "saludo": "Hola {nombre}, completo",
                "despedida": "Saludos,\\n{firma_agente}",
                "estructura": "saludo + contexto + 2-3 opciones con precios + próximo paso + despedida",
                "emojis": "no",
                "formato": "listas y tablas para itinerarios; markdown OK",
            },
            "instagram": {
                "tono": "muy casual",
                "longitud_max_chars": 400,
                "saludo": "Hola! / Hola {nombre}!",
                "estructura": "respuesta breve + invitación a seguir por mail/whatsapp si la consulta es compleja",
                "emojis": "permitidos",
                "links": "evitar (Instagram no los hace clicables en DMs)",
            },
            "telegram": {
                "tono": "informal pero profesional",
                "longitud_max_chars": 800,
                "estructura": "como WhatsApp pero un poco más estructurado",
                "emojis": "moderados",
                "formato": "markdown soportado",
            },
        },
    }
    db.save_crm(crm)
    print(f"  ✓ CRM: {len(crm['agencias'])} agencias, {len(crm['pasajeros'])} pasajeros, "
          f"{len(crm['tags_workspace'])} tags")


def seed_storage():
    # Solo inicializa si está vacío
    s = db.load_storage()
    if not s.get("cases"):
        db.save_storage(s)
        print("  ✓ Storage inicializado (vacío)")
    else:
        print(f"  ✓ Storage ya tiene {len(s['cases'])} casos — no se sobreescribe")


if __name__ == "__main__":
    db_url = os.getenv("DATABASE_URL")
    if not db_url:
        print("ERROR: Falta DATABASE_URL en .env")
        sys.exit(1)

    print("=== Seed Supabase ===")
    print(f"DB: {db_url[:50]}...")

    print("\n[1/4] Aplicando schema...")
    run_schema()

    print("\n[2/4] Creando agentes...")
    seed_agents()

    print("\n[3/4] Cargando CRM mock...")
    seed_crm()

    print("\n[4/4] Inicializando storage...")
    seed_storage()

    print("\n✅ Seed completo. Podés levantar el servidor con:")
    print("   uvicorn main:app --reload --port 8000")
