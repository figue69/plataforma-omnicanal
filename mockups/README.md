# Mockup — Plataforma Omnicanal de Ventas Asistida por IA

Demo funcional para mostrar al equipo el flujo end-to-end:
**cliente escribe → IA lee/clasifica/extrae/sugiere → agente edita → respuesta vuelve al cliente.**

## Estructura

```
mockups/
├── backend/         # FastAPI + Claude (Haiku/Sonnet) + Tourbo + SerpAPI
├── cliente-test/    # Simulador del cliente (otra pestaña)
└── producto/        # Plataforma del agente (lo que verá el equipo)
```

## Setup (3 pasos)

```bash
cd mockups/backend
pip install -r requirements.txt
cp .env.example .env   # opcional: cargar ANTHROPIC_API_KEY para IA real
uvicorn main:app --reload --port 8000
```

Luego abrí en el browser, en pestañas separadas:
- `mockups/cliente-test/index.html` — el cliente
- `mockups/producto/index.html` — el agente

> Sin `ANTHROPIC_API_KEY` el sistema funciona en **modo mock determinístico** (clasificación y sugerencias por reglas/keywords). El demo se ve igual; sólo no usa Claude.

## Flujo de prueba

1. Abrí el simulador de cliente, elegí "Polo Viajes" (B2B) + canal WhatsApp.
2. Pegá un caso de ejemplo o escribí libre: "Necesito Madrid + Barcelona 15 días, familia de 4, julio".
3. En la pestaña del agente, andá a **Inbox** → ya aparece el mensaje.
4. Abrí el caso → el copiloto te muestra **3 sugerencias ranqueadas** (mejor cierre / cálida / concisa).
5. Click en una sugerencia → cae al composer → editá y dale **Enviar respuesta al cliente**.
6. Volvé al simulador → la respuesta aparece en la conversación.

## Pantallas implementadas en este sprint

- 01 Login / selección de agente
- 02 Inbox omnicanal
- 03 Detalle de caso + sugerencias IA
- 04 Panel "A seguir" (recordatorios proactivos)
- 05 Mi perfil (system prompt del agente)

Pendientes (próximas iteraciones):
06 CRM Agencia · 06b CRM Pasajero · 07 Pipeline · 08 Proveedores · 09 AFK ·
10 Guardia 24/7 · 11 Auto-tuning · 12 Admin (canales / reglas / permisos / tags) · 13 Dashboard.

## Endpoints clave del backend

| Método | Ruta | Uso |
|---|---|---|
| POST | `/messages` | Recibe mensaje del cliente, corre IA, asigna a Caso |
| GET  | `/inbox` | Lista de casos para la bandeja |
| GET  | `/cases/{id}` | Timeline + sugerencias del caso |
| POST | `/cases/{id}/reply` | Agente responde |
| POST | `/reminders/generate` | Recalcula recordatorios |
| POST | `/tuning-reviews/generate` | Genera revisión de prompt (humano confirma) |
| GET  | `/metrics` | Tokens, costo y aceptación IA |

## Persistencia

JSON local: `backend/storage.json` (mensajes, casos, recordatorios, tuning, métricas) y `backend/mock_crm.json` (agencias, pasajeros, agentes, tags, reglas por canal). Para reiniciar el demo: `POST /admin/reset`.
