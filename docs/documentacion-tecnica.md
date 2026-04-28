# Documentacion Tecnica — Plataforma Omnicanal Asistida por IA

**Version:** 1.0 — Mockup funcional / Base para produccion  
**Fecha:** Abril 2026  
**Mantenido por:** Equipo de desarrollo  
**Estado:** Mockup funcional con integraciones reales activas. Listo para prototipo con trafico real.

---

## Tabla de Contenidos

1. [Contexto del Negocio](#1-contexto-del-negocio)
2. [Filosofia de Diseno](#2-filosofia-de-diseno)
3. [Stack Tecnologico y Por Que](#3-stack-tecnologico-y-por-que)
4. [Estructura de Archivos](#4-estructura-de-archivos)
5. [Base de Datos](#5-base-de-datos)
6. [Autenticacion](#6-autenticacion)
7. [Capa de IA](#7-capa-de-ia)
8. [Canales e Integraciones](#8-canales-e-integraciones)
9. [Pipeline de Mensaje Entrante](#9-pipeline-de-mensaje-entrante)
10. [Frontend](#10-frontend)
11. [Endpoints de la API](#11-endpoints-de-la-api)
12. [Variables de Entorno](#12-variables-de-entorno)
13. [Setup Local](#13-setup-local)
14. [Deploy en Railway](#14-deploy-en-railway)
15. [Decisiones Arquitectonicas Clave](#15-decisiones-arquitectonicas-clave)
16. [Estado de Features](#16-estado-de-features)
17. [Guia para Nuevas Desarrolladoras](#17-guia-para-nuevas-desarrolladoras)
18. [Roadmap a Produccion Final](#18-roadmap-a-produccion-final)

---

## 1. Contexto del Negocio

### Quien es el cliente

Un turoperador B2B y B2C con 32 personas distribuidas en 7 areas:

| Area | Rol en el sistema |
|------|------------------|
| Ventas | Usuarios principales de la plataforma. Gestionan la mayoria de las conversaciones. |
| Operaciones Terrestres | Reciben casos asignados cuando el pedido involucra hoteles, traslados o paquetes. |
| Operaciones Aereas | Reciben casos con componente de vuelo. |
| Operaciones Grupales | Casos de grupos (10+ pax) con logistica compleja. |
| Administracion | Acceso de solo lectura a metricas y facturacion. |
| Promocion | Accede al pipeline de ventas para medir efectividad de campanas. |
| Producto/Marketing | Revisan el auto-tuning y los patrones de consulta para informar estrategia. |

### El problema que resuelve

El equipo recibia mensajes por **WhatsApp, Email, Instagram y Telegram** de forma completamente desorganizada: cada agente tenia su propio WhatsApp personal, los emails llegaban a una casilla compartida sin asignacion, y no habia historial unificado por cliente.

Los sintomas concretos que motivaron este proyecto:

- **Lentitud de respuesta:** Las OTAs (Online Travel Agencies) con chatbots de IA responden en segundos. El equipo tardaba horas. En turismo, la velocidad cierra o pierde ventas.
- **Perdida de contexto entre canales:** Un cliente que mandaba un email y luego escribia por WhatsApp era tratado como dos contactos distintos. El agente no sabia que ya habia una conversacion en curso.
- **Falta de memoria conversacional:** Un agente retomaba una conversacion y tenia que releer 50 mensajes para entender donde estaban. La IA extrae y acumula los datos clave (destino, fechas, pax, presupuesto) automaticamente.
- **Inconsistencia de tono:** Distintos agentes respondia con distintos estilos, sin guidelines claras. Las reglas de canal y el system prompt por agente atacan esto.
- **Cero visibilidad de pipeline:** No sabian cuantas cotizaciones habia en curso, cuales estaban perdiendo, o cuales necesitaban seguimiento.

### La solucion

Una plataforma tipo Zendesk con un copiloto de IA integrado. Los puntos clave de la propuesta de valor:

1. **Unificacion omnicanal:** todos los canales en un inbox unico. Un caso por cliente, no uno por canal.
2. **Copiloto, no piloto automatico:** la IA sugiere, el agente decide y envia. Esto es un requisito de negocio no negociable (ver seccion de decisiones arquitectonicas).
3. **Acceso a proveedores en tiempo real:** cuando llega una consulta de vuelos, la IA busca disponibilidad en el GDS real (Tourbo) y presenta los resultados junto con la sugerencia de respuesta. El agente tiene la informacion antes de responder.
4. **Contexto acumulado:** los datos extraidos por IA (destino, fechas, pax, presupuesto) se van guardando y enriqueciendo a lo largo de toda la conversacion. El agente nunca pierde el hilo.

---

## 2. Filosofia de Diseno

### Economia de tokens: IA para lo que la IA hace bien

La IA es cara. Una llamada a Claude Sonnet con contexto completo puede costar entre USD 0.01 y USD 0.05. Si el sistema hace 500 llamadas por dia, son USD 5-25/dia solo en tokens, sin contar la infraestructura.

La filosofia adoptada es: **la IA solo lee, clasifica, extrae y sugiere**. Todo lo demas es codigo determinista.

| Tarea | Quien la hace | Por que |
|-------|--------------|---------|
| Recibir webhook de WhatsApp | FastAPI endpoint | Es parsing de JSON. No necesita IA. |
| Buscar el contacto en el CRM | Codigo Python | Es una busqueda por campo. Haiku seria desperdicio. |
| Routing a agente disponible | Codigo Python con reglas | Las reglas son deterministicas y auditables. |
| Clasificar tipo de mensaje | Haiku | Tarea mecanica, Haiku es suficiente y 5x mas barato que Sonnet. |
| Extraer destinos/fechas/pax | Haiku | Idem. La extraccion estructurada no requiere razonamiento fino. |
| Detectar severidad/urgencia | Haiku | Clasificacion binaria o escalar simple. |
| Generar 3 sugerencias de respuesta | Sonnet | Aqui si se necesita razonamiento fino, coherencia de tono, y creatividad. |
| Proponer cambios al system prompt (auto-tuning) | Sonnet | Analisis de patrones de conversacion. Requiere capacidad de razonamiento. |
| Persistir mensajes en DB | FastAPI + Supabase | SQL. No hay lugar para IA aqui. |
| Evaluar si una sugerencia fue buena | Agente humano | Human-in-the-loop por diseno. |

### Human-in-the-loop es arquitectura, no feature

La IA puede confundir destinos (Buenos Aires vs. Bariloche ambos en Argentina), inventar precios que no son reales, o no conocer excepciones de politica comercial que el agente maneja de memoria. Por esto, **el agente siempre es el punto final de decision**. La plataforma reduce el tiempo de respuesta y mejora la consistencia, pero nunca reemplaza al agente.

Consecuencia de diseno: no hay endpoints de "auto-reply" activos en produccion. El flujo es siempre: mensaje entrante → sugerencias generadas → agente revisa → agente aprueba y envia.

---

## 3. Stack Tecnologico y Por Que

### Backend: FastAPI + Python 3.12

**Por que FastAPI y no Django, Flask, o Rails:**

- **Async nativo:** FastAPI esta construido sobre Starlette y soporta async/await desde el primer dia. Las llamadas a Claude (que pueden tardar 2-5 segundos) no bloquean el event loop. Django ORM es sincrono por defecto; agregar async a Django es posible pero mas complejo.
- **Validacion automatica con Pydantic:** los request bodies se validan automaticamente. Un `POST /inbox` con un campo mal tipado devuelve un 422 claro sin escribir validacion manual.
- **Documentacion automatica:** FastAPI genera `/docs` (Swagger UI) y `/redoc` sin configuracion adicional. Las desarrolladoras pueden explorar y probar la API sin Postman.
- **Velocidad de setup:** comparado con Django, FastAPI requiere muchos menos archivos de configuracion inicial. Para un mockup que necesitaba iterar rapido, esto importa.
- **Python para IA:** el SDK de Anthropic tiene soporte de primera clase para Python. No hay friction entre el backend y la capa de IA.

**Por que Python 3.12 especificamente:** soporte de `match/case` (pattern matching), mejoras de performance en el runtime, y es la version LTS actual con soporte hasta 2028.

### Base de datos: Supabase (PostgreSQL)

**Por que Supabase y no RDS, PlanetScale, o SQLite:**

- **PostgreSQL con JSONB:** la combinacion de una base relacional con soporte nativo de JSON permite tener tablas estructuradas para lo que es fijo (agentes, permisos) y blobs para lo que cambia rapidamente (mensajes, casos, reglas).
- **Gratis en tier inicial:** el tier gratuito de Supabase es suficiente para el mockup y el prototipo inicial. No hay costo de DB hasta que el volumen lo justifique.
- **SDK de Python:** `supabase-py` es mantenido por el equipo de Supabase y funciona bien. Alternativa: usar `asyncpg` directamente para mejor performance en produccion.
- **Dashboard integrado:** el equipo puede ver y editar datos directamente en el dashboard de Supabase sin necesidad de herramientas externas. Util en la fase de desarrollo y debug.
- **Separado de Railway:** la base de datos esta en Supabase, no en Railway Postgres. Esto significa que si se redeploya o reinicia el proceso en Railway, los datos persisten. Un redeploy no es un wipe de datos.

### IA: Anthropic Claude (Haiku + Sonnet)

**Por que Claude y no GPT-4 u otros:**

- Calidad de seguimiento de instrucciones estructuradas: Claude es consistentemente mejor para generar JSON estructurado dentro de prompts complejos (extraer datos de un mensaje de WhatsApp en JSON valido).
- El equipo ya tenia experiencia con la API de Anthropic.
- El modelo Haiku es significativamente mas barato que GPT-3.5-turbo para calidad comparable en tareas de clasificacion.

**Por que dos modelos (Haiku y Sonnet) y no uno:**

Haiku cuesta aproximadamente 5 veces menos por token que Sonnet. La clasificacion y extraccion son tareas mecanicas donde Haiku es suficiente. La generacion de sugerencias requiere razonamiento mas fino. Usar Haiku para todo seria sacrificar calidad en sugerencias; usar Sonnet para todo seria gastar 5x mas en clasificacion. El split es la decision economicamente optima.

### Auth: JWT + bcrypt

**Por que JWT y no sessions con base de datos:**

Para el mockup, JWT permite autenticacion sin estado: el servidor no necesita guardar la sesion en ninguna parte. El token contiene el `agent_id` y se verifica con la clave secreta. Esto simplifica el deploy (no hay Redis o tabla de sesiones que mantener).

**Limitacion conocida:** sin refresh tokens y sin blacklist, no se puede cerrar una sesion remotamente. Si un token de 60 dias es robado, el atacante tiene acceso por 60 dias. Para produccion esto debe resolverse (ver seccion de roadmap).

### Frontend: HTML + Tailwind CSS + Vanilla JS

**Por que sin framework y no React/Vue/Next.js:**

La decision fue pragmatica: el mockup fue desarrollado iterativamente por el CTO con asistencia de IA (Claude). Sin herramientas de build (Webpack, Vite), sin TypeScript, sin JSX. Agregar una pantalla nueva era copiar un HTML existente y modificarlo. Esto permitio tener 14 pantallas funcionales en semanas en lugar de meses.

Para produccion final, esta decision debe revisarse. Con vanilla JS y HTML puro, el codigo se vuelve dificil de mantener a medida que crece. Se recomienda migrar a React con TypeScript (ver roadmap).

**Tailwind via CDN:** en el mockup se usa el CDN de Tailwind. En produccion, compilar Tailwind para eliminar clases no usadas y reducir el tamano del CSS.

### Deploy: Railway.app

**Por que Railway y no Heroku, Fly.io, o AWS:**

- **Setup en minutos:** conectar el repo de GitHub y configurar las variables de entorno es todo lo que se necesita.
- **HTTPS automatico:** Railway provee certificado SSL automaticamente. Los webhooks de WhatsApp y Telegram requieren HTTPS — esto viene resuelto.
- **Precio predecible:** Railway cobra por uso de CPU/RAM, no por dynos fijos como Heroku. Para un mockup con trafico bajo, el costo es minimal.
- **Sin configuracion de servidor:** no hay que mantener Nginx, no hay que configurar reverse proxies, no hay que gestionar certificados.

---

## 4. Estructura de Archivos

```
mockups/
├── README.md
├── cliente-test/
│   ├── index.html          # Simulador del cliente — herramienta de prueba interna,
│   │                       # NO es el frontend de produccion. Permite enviar mensajes
│   │                       # simulados para probar el pipeline sin usar canales reales.
│   └── assets/
│
├── producto/               # Frontend del agente — esta es la plataforma real
│   ├── index.html          # Landing/hub — lista todas las pantallas con links
│   ├── assets/
│   │   ├── app.js          # API client global (window.api), helpers de UI,
│   │   │                   # renderizado del sidebar. Incluido en TODAS las pantallas.
│   │   └── mock-data.js    # Datos mock de contexto — solo para desarrollo sin backend
│   │
│   ├── 01-login.html       # Auth — formulario de login, guarda JWT en localStorage
│   ├── 02-inbox.html       # Inbox omnicanal — lista de casos con filtros
│   ├── 03-caso-detalle.html # Detalle de caso + panel de copiloto IA
│   ├── 04-a-seguir.html    # Panel "A seguir" — recordatorios proactivos por IA
│   ├── 05-mi-perfil.html   # Perfil del agente + editor de system prompt personal
│   ├── 06-crm-agencia.html # CRM de agencias (clientes B2B)
│   ├── 06b-crm-pasajero.html # CRM de pasajeros (clientes B2C)
│   ├── 07-pipeline.html    # Pipeline de ventas estilo Kanban
│   ├── 08-proveedores.html # Panel de proveedores integrados y estado
│   ├── 09-afk-reglas.html  # Reglas AFK y auto-respuesta cuando agente no disponible
│   ├── 10-guardia.html     # Guardia 24/7 — escalamiento fuera de horario
│   ├── 11-auto-tuning.html # Revision humana de propuestas de mejora de prompts
│   ├── 12-admin.html       # Administracion — usuarios, canales, permisos
│   ├── 13-dashboard.html   # Metricas de uso, tokens, SLA, conversion
│   └── 14-settings.html    # Settings — canales, workspace, integraciones
│
└── backend/
    ├── main.py             # FastAPI — todos los endpoints (~1854 lineas)
    │                       # NOTA: para produccion, dividir en routers por dominio
    ├── ai.py               # Capa de IA — todas las funciones que llaman a Claude
    │                       # + fallback mock si no hay ANTHROPIC_API_KEY
    ├── db.py               # Acceso a Supabase — get/set del storage_blob y crm_blob
    ├── auth.py             # JWT creation/verification + bcrypt password hashing
    ├── reminders.py        # Logica de generacion de recordatorios proactivos
    ├── tuning.py           # Auto-tuning: analiza conversaciones y propone mejoras
    │                       # al system prompt del agente
    ├── channel_rules.py    # Reglas de tono y formato por canal (WhatsApp/Email/Telegram)
    ├── seed.py             # Script de inicializacion: crea tablas y datos de prueba
    ├── schema.sql          # DDL completo de Supabase — fuente de verdad del esquema
    ├── requirements.txt    # Dependencias Python
    ├── .env.example        # Template de variables de entorno
    │
    └── providers/          # Integraciones con canales y proveedores externos
        ├── gmail.py        # Gmail OAuth2 — auth, polling, envio con threading
        ├── whatsapp.py     # WhatsApp Cloud API (Meta) — webhook, envio
        ├── telegram_bot.py # Telegram Bot API — webhook, envio, registro
        ├── tourbo.py       # GDS Flaptek/Tourbo — AVAIL/PRICING/BOOKING de vuelos
        └── serpapi.py      # Google Flights via SerpAPI — fallback de busqueda
```

### Por que `main.py` tiene 1854 lineas

Fue una decision deliberada para el mockup: tener todo en un archivo facilita la navegacion rapida cuando se itera con IA (Claude puede ver el archivo completo en contexto). Para produccion, esto debe refactorizarse en routers de FastAPI separados por dominio:

```
backend/
└── routers/
    ├── auth.py
    ├── inbox.py
    ├── cases.py
    ├── crm.py
    ├── channels.py
    ├── admin.py
    ├── ai_endpoints.py
    └── metrics.py
```

---

## 5. Base de Datos

### Esquema de Supabase

```sql
-- Agentes del sistema (usuarios de la plataforma)
-- Esta tabla SI esta normalizada porque los agentes tienen estructura fija
-- y necesitan queries eficientes (login, permisos, perfil)
CREATE TABLE agents (
    id TEXT PRIMARY KEY,          -- UUID generado en Python (uuid4())
    nombre TEXT NOT NULL,
    rol TEXT NOT NULL,            -- "agente" | "supervisor" | "admin"
                                  -- El rol controla que endpoints puede llamar
    area TEXT NOT NULL,           -- "ventas" | "ops_terrestres" | "ops_aereas" | etc.
    email TEXT UNIQUE NOT NULL,   -- Se usa para login
    password_hash TEXT NOT NULL,  -- bcrypt hash. NUNCA se guarda la password en texto
    system_prompt TEXT DEFAULT '',-- Instrucciones personales del agente para la IA
                                  -- Define su tono, estilo, preferencias
    permisos_modelo TEXT[] DEFAULT '{haiku,sonnet}',
                                  -- Admin puede revocar acceso a Sonnet/Opus por agente
                                  -- para controlar costos
    limite_tokens_dia INTEGER DEFAULT 200000
                                  -- Soft limit de tokens por agente por dia
                                  -- Se verifica en ai.py antes de llamar a Claude
);

-- Blob principal: mensajes, casos, metricas, reminders, propuestas de tuning
-- Ver estructura detallada en la seccion siguiente
CREATE TABLE storage_blob (
    id INTEGER PRIMARY KEY,      -- SIEMPRE id=1. Un solo registro.
    data JSONB NOT NULL          -- Todo el estado operacional del sistema
);

-- Blob del CRM: agencias, pasajeros, canales, tags, reglas de tono
-- Separado de storage_blob para hacer las lecturas/escrituras mas acotadas
CREATE TABLE crm_blob (
    id INTEGER PRIMARY KEY,      -- SIEMPRE id=1. Un solo registro.
    data JSONB NOT NULL
);
```

### Por que un "blob" JSONB y no tablas normalizadas

Esta es probablemente la decision que mas preguntas genera. La razon es practica:

**En el mockup, el esquema de datos cambio en cada sprint.** Un caso nuevo que necesitaba un campo `booking_reference` se agregaba directamente al JSON sin ningun `ALTER TABLE`. Un mensaje que necesitaba guardar `serpapi_results` junto a sus metadatos se extendia sin migracion. Esto permitio iterar el esquema literalmente en minutos.

**El costo de esta decision:**
- No hay indices en campos internos del blob. Una query para encontrar todos los casos de un contacto requiere leer todo el blob y filtrar en Python.
- No hay constraints de integridad referencial dentro del JSON. Si se borra un contacto, sus mensajes en `storage_blob.messages` quedan huerfanos.
- El blob crece sin limites. En produccion con alto volumen, leer y escribir el blob completo en cada operacion es ineficiente.

**Para produccion, la migracion es:**

```sql
-- Tablas propias con indices apropiados
CREATE TABLE messages (
    id TEXT PRIMARY KEY,
    case_id TEXT NOT NULL REFERENCES cases(id),
    contact_id TEXT NOT NULL REFERENCES contacts(id),
    direction TEXT NOT NULL,  -- "inbound" | "outbound"
    channel TEXT NOT NULL,
    text TEXT,
    ts TIMESTAMPTZ NOT NULL,
    agent_id TEXT REFERENCES agents(id),
    metadata JSONB            -- Para datos variables por canal
);

CREATE TABLE cases (
    id TEXT PRIMARY KEY,
    contact_id TEXT NOT NULL REFERENCES contacts(id),
    channel TEXT NOT NULL,
    channel_id TEXT,
    status TEXT NOT NULL DEFAULT 'open',
    subject TEXT,
    extracted_data JSONB,     -- Datos acumulados por IA
    last_suggestions JSONB,   -- Ultimas 3 sugerencias generadas
    assigned_agent TEXT REFERENCES agents(id),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Indices criticos
CREATE INDEX idx_cases_contact_id ON cases(contact_id);
CREATE INDEX idx_cases_status ON cases(status);
CREATE INDEX idx_cases_assigned_agent ON cases(assigned_agent);
CREATE INDEX idx_messages_case_id ON messages(case_id);
CREATE INDEX idx_messages_ts ON messages(ts DESC);
```

### Estructura del storage_blob

```json
{
  "messages": [
    {
      "id": "msg_uuid",
      "case_id": "case_uuid",
      "contact_id": "contact_uuid",
      "direction": "inbound",
      "channel": "whatsapp",
      "text": "Hola, quiero cotizar un vuelo a Madrid",
      "ts": "2026-04-27T14:30:00Z",
      "agent_id": null,
      "suggestion_used": null
    }
  ],
  "cases": [
    {
      "id": "case_uuid",
      "contact_id": "contact_uuid",
      "channel": "whatsapp",
      "channel_id": "+5491155551234",
      "status": "open",
      "subject": "Consulta vuelo BUE-MAD",
      "extracted_data": {
        "destino": "Madrid",
        "origen": "Buenos Aires",
        "fecha_ida": "2026-07-15",
        "fecha_vuelta": "2026-07-30",
        "pax_adultos": 2,
        "presupuesto_usd": null
      },
      "last_suggestions": [
        {
          "label": "Mejor tasa de cierre",
          "text": "Hola! Ya estoy viendo los vuelos para Madrid..."
        }
      ],
      "assigned_agent": "agent_uuid",
      "created_at": "2026-04-27T14:30:00Z",
      "updated_at": "2026-04-27T14:31:00Z"
    }
  ],
  "tuning_reviews": [
    {
      "id": "review_uuid",
      "agent_id": "agent_uuid",
      "proposed_diff": "Agregar instruccion sobre mencionar seguro de viaje",
      "justification": "En 3 de las ultimas 5 conversaciones el cliente pregunto...",
      "status": "pending"
    }
  ],
  "metrics": {
    "token_usage": [
      {
        "ts": "2026-04-27T14:31:00Z",
        "model": "claude-haiku-4-5-20251001",
        "input_tokens": 450,
        "output_tokens": 120,
        "cost_usd": 0.00018,
        "agent_id": "agent_uuid",
        "action": "classify_message"
      }
    ]
  },
  "reminders": [
    {
      "id": "reminder_uuid",
      "case_id": "case_uuid",
      "agent_id": "agent_uuid",
      "type": "follow_up",
      "message": "Han pasado 24 horas sin respuesta del cliente. Considerar seguimiento.",
      "status": "pending",
      "created_at": "2026-04-27T14:30:00Z"
    }
  ]
}
```

### Estructura del crm_blob

```json
{
  "agencias": [
    {
      "id": "agency_uuid",
      "nombre": "Viajes del Sur S.A.",
      "contacto": "Maria Garcia",
      "email": "maria@viajasdelsur.com",
      "whatsapp": "+5491155559999",
      "tags": ["vip", "b2b"],
      "system_prompt": "Es una agencia con foco en cruceros. Siempre mencionar opciones maritimas."
    }
  ],
  "pasajeros": [
    {
      "id": "passenger_uuid",
      "nombre": "Juan Perez",
      "email": "juan@email.com",
      "whatsapp": "+5491155551234",
      "telegram_user_id": "123456789",
      "tags": ["frecuente", "luna-de-miel"],
      "system_prompt": "Prefiere opciones premium. Sensible al precio pero no lo dice directamente."
    }
  ],
  "tags_workspace": [
    { "id": "tag_uuid", "nombre": "vip", "color": "#FFD700" }
  ],
  "channel_rules_default": {
    "whatsapp": {
      "tono": "informal",
      "longitud": "corto",
      "usar_emojis": true,
      "instrucciones": "Respuestas cortas y directas. Maximo 3 oraciones."
    },
    "email": {
      "tono": "formal",
      "longitud": "estructurado",
      "usar_emojis": false,
      "instrucciones": "Incluir saludo formal, desarrollo y cierre. Usar parrafos."
    },
    "telegram": {
      "tono": "semiformal",
      "longitud": "medio",
      "usar_emojis": true,
      "instrucciones": "Tono amigable pero profesional."
    }
  },
  "channels": [
    {
      "id": "channel_uuid",
      "type": "whatsapp",
      "nombre": "WhatsApp Ventas",
      "active": true,
      "assigned_agents": ["agent_uuid_1", "agent_uuid_2"],
      "credentials": {},
      "bot_token": null,
      "webhook_registered": false
    }
  ]
}
```

**Nota importante:** los agentes (`agents`) NO se persisten en el `crm_blob`. Siempre se leen de la tabla `agents` de Supabase. El `crm_blob` tiene una seccion `agentes` que fue usada en versiones tempranas del mockup pero fue deprecada. Si se ve esa seccion en el blob, ignorarla y leer siempre de la tabla `agents`.

---

## 6. Autenticacion

### Flujo completo

```
Cliente (browser)                    FastAPI Backend                 Supabase
     |                                     |                             |
     | POST /auth/login                    |                             |
     | { email, password }                 |                             |
     |------------------------------------>|                             |
     |                                     | SELECT * FROM agents        |
     |                                     | WHERE email = ?             |
     |                                     |---------------------------->|
     |                                     |                   agent row |
     |                                     |<----------------------------|
     |                                     |                             |
     |                                     | bcrypt.checkpw(             |
     |                                     |   password,                 |
     |                                     |   agent.password_hash       |
     |                                     | )                           |
     |                                     |                             |
     |                                     | jwt.encode({                |
     |                                     |   sub: agent.id,            |
     |                                     |   exp: now + 60 dias        |
     |                                     | }, JWT_SECRET)              |
     |                                     |                             |
     |         { token: "eyJ..." }         |                             |
     |<------------------------------------|                             |
     |                                     |                             |
     | Guarda token en localStorage        |                             |
     | como "jwt_token"                    |                             |
     |                                     |                             |
     | GET /inbox                          |                             |
     | Authorization: Bearer eyJ...        |                             |
     |------------------------------------>|                             |
     |                                     | Extrae agent_id del token   |
     |                                     | via Depends(get_current_    |
     |                                     |   agent_id)                 |
     |                                     |                             |
```

### Implementacion en FastAPI

Todos los endpoints protegidos usan la dependencia `Depends(auth.get_current_agent_id)`:

```python
@app.get("/inbox")
async def get_inbox(agent_id: str = Depends(auth.get_current_agent_id)):
    # agent_id ya esta verificado y extraido del JWT
    ...
```

La funcion `get_current_agent_id` en `auth.py`:
1. Lee el header `Authorization: Bearer <token>`
2. Decodifica el JWT con `JWT_SECRET`
3. Verifica que no este expirado
4. Retorna el `sub` (agent_id) del payload
5. Si algo falla: lanza `HTTPException(401)` — el frontend intercepta esto y redirige a login

### Seguridad: lo que falta para produccion

| Vulnerabilidad | Riesgo | Solucion en produccion |
|---------------|--------|------------------------|
| Sin refresh tokens | Si el access token expira, el usuario debe loguearse de nuevo. Con 60 dias de expiracion esto es poco frecuente, pero hace que los tokens comprometidos sean peligrosos por 60 dias. | Agregar refresh tokens de larga duracion (6 meses) + access tokens de corta duracion (1 hora). |
| Sin blacklist/revocacion | No se puede cerrar la sesion de un usuario comprometido de forma remota. | Tabla `token_blacklist` con los tokens revocados. Verificar en cada request. |
| Sin MFA | Solo password para autenticar. | Agregar TOTP (Google Authenticator compatible). |
| JWT_SECRET por defecto | Si se usa el valor del `.env.example` en produccion, el sistema es trivialmente atacable. | Generar un secret aleatorio de 256 bits para produccion. Rotar periodicamente. |

---

## 7. Capa de IA

### Archivo: `backend/ai.py`

Este es el archivo mas importante de entender. Toda la interaccion con Claude pasa por aqui.

### Funciones principales

| Funcion | Modelo | Input | Output | Costo estimado |
|---------|--------|-------|--------|----------------|
| `classify_message(text)` | Haiku | Texto del mensaje | `{tipo, subtipo, severidad}` | ~USD 0.0001 |
| `extract_data(text)` | Haiku | Texto del mensaje | `{destino, origen, fecha_ida, fecha_vuelta, pax, presupuesto}` | ~USD 0.0002 |
| `detect_severity(text)` | Haiku | Texto del mensaje | `{nivel: 1-5, motivo}` | ~USD 0.0001 |
| `generate_suggestions(context)` | Sonnet | Ver detalle abajo | Lista de 3 sugerencias etiquetadas | ~USD 0.01-0.05 |
| `generate_reminders(cases)` | Haiku | Lista de casos activos | Lista de recordatorios | ~USD 0.001 |
| `generate_tuning_proposal(agent, history)` | Sonnet | Historial de conversaciones | Propuesta de cambio al system prompt | ~USD 0.02-0.10 |

### Contexto que recibe `generate_suggestions()`

Esta es la llamada mas costosa y la mas importante. Sonnet recibe:

```python
{
    # El mensaje actual que hay que responder
    "mensaje_actual": "Che, me confirmas el precio del vuelo a Miami para julio?",
    
    # Historial de los ultimos 12 mensajes del caso
    # (con indicador de si la sugerencia fue usada o no — para aprendizaje)
    "historial": [
        {"role": "inbound", "text": "...", "ts": "...", "suggestion_used": "label_o_null"},
        {"role": "outbound", "text": "...", "ts": "...", "agent_id": "..."},
    ],
    
    # Datos extraidos acumulados a lo largo de toda la conversacion
    # Merge de todos los extract_data() del hilo
    "datos_extraidos": {
        "destino": "Miami",
        "origen": "Buenos Aires",
        "fecha_ida": "2026-07-10",
        "pax_adultos": 2
    },
    
    # Resultados de proveedores (vuelos disponibles)
    "resultados_tourbo": [...],  # Vuelos reales del GDS o mock
    "resultados_serpapi": [...], # Google Flights (si disponible)
    
    # System prompt del agente (tono y preferencias personales)
    "agent_system_prompt": "Soy vendedora senior. Prefiero ser directa con los precios...",
    
    # System prompt del contacto (comportamiento especifico del cliente)
    "contact_system_prompt": "Es un cliente frecuente. Es muy sensible a demoras en respuesta.",
    
    # Regla de tono del canal actual
    "channel_rule": {
        "tono": "informal",
        "longitud": "corto",
        "usar_emojis": true,
        "instrucciones": "Respuestas cortas y directas. Maximo 3 oraciones."
    },
    
    # Sugerencias ya mostradas (para que Sonnet no repita)
    "previous_suggestions": ["Hola Juan! Ya vi los vuelos...", ...]
}
```

### Las 3 etiquetas de sugerencias y por que

Las 3 sugerencias no son variaciones aleatorias. Cada una optimiza para un objetivo diferente:

| Etiqueta | Que optimiza | Cuando el agente la elige |
|----------|-------------|--------------------------|
| "Mejor tasa de cierre historica" | Conversion — basada en patrones de conversaciones que terminaron en venta | Cuando el objetivo es cerrar la venta ahora |
| "Tono mas calido" | Relacion — mas empatica, mas personal | Cuando el cliente parece dudar o el vinculo no esta consolidado |
| "Mas concisa" | Eficiencia — respuesta minima necesaria | Cuando el cliente claramente quiere solo el dato, sin vueltas |

Los datos de cual etiqueta elige cada agente en cada contexto se guardan en `message.suggestion_used`. Esto alimenta el sistema de auto-tuning para ajustar los prompts.

### Fallback mock

Si `ANTHROPIC_API_KEY` no esta configurada, todas las funciones retornan respuestas mock deterministas. El mock detecta keywords:

- "vuelo", "volar", "aerolinea" → clasifica como `cotizacion` de tipo `aereo`
- "hotel", "alojamiento" → clasifica como `cotizacion` de tipo `hotelero`
- "urgente", "!!", "problema" → severidad alta
- Etc.

Esto permite ejecutar la plataforma completa en modo demo sin ningun costo de tokens. Util para onboarding, demos a clientes, y testing de UI.

### Registro de consumo de tokens

Cada llamada a Claude registra automaticamente en `metrics.token_usage`:

```python
{
    "ts": "2026-04-27T14:31:00Z",
    "model": "claude-haiku-4-5-20251001",
    "input_tokens": 450,
    "output_tokens": 120,
    "cost_usd": 0.00018,  # Calculado con precios actuales de Anthropic
    "agent_id": "agent_uuid",
    "action": "classify_message"  # classify | extract | suggestions | tuning | reminders
}
```

Esto alimenta el dashboard de observabilidad (`13-dashboard.html`) con graficos de costo por dia, por agente, y por tipo de accion.

### Optimizacion de costos en produccion: prompt caching

Actualmente, el system prompt y el contexto estático se envian en cada llamada. Anthropic hace caching automatico de prefijos estables, pero para maximizar cache hits:

```python
# En produccion, marcar el system prompt con cache_control
messages = [
    {
        "role": "user",
        "content": [
            {
                "type": "text",
                "text": SYSTEM_PROMPT_ESTATICO,
                "cache_control": {"type": "ephemeral"}  # Marca para caching
            },
            {
                "type": "text",
                "text": contexto_dinamico  # Esto no se cachea
            }
        ]
    }
]
```

El caching puede reducir el costo de tokens de input hasta un 90% en llamadas repetidas con contexto estático similar.

---

## 8. Canales e Integraciones

### WhatsApp Cloud API (Meta)

**Archivo:** `backend/providers/whatsapp.py`

**Por que Meta Cloud API y no un BSP (Business Service Provider) como Twilio o MessageBird:**
Los BSPs agregan una capa intermedia que cuesta entre USD 0.005 y USD 0.02 por mensaje adicional al costo de Meta. Con Meta Cloud API directamente, solo se paga el costo de Meta (gratis para mensajes dentro de la ventana de 24h, templates tienen costo). Para el volumen de un turoperador de 32 personas, el ahorro es significativo.

**Variables de entorno requeridas:**
```bash
WHATSAPP_TOKEN=EAAxxxxxxxxx          # Token permanente de la app de Meta
WHATSAPP_PHONE_NUMBER_ID=114675...   # ID del numero registrado en Meta
WHATSAPP_VERIFY_TOKEN=plataforma-... # Token para verificar el webhook
```

**Flujo de webhook:**

```
WhatsApp (Meta) → POST /webhook/whatsapp → Parsear payload
                                         → Buscar contacto en CRM
                                         → Crear o abrir caso
                                         → Pipeline de IA
                                         → Guardar resultado
                                         → Responder 200 OK a Meta
                                           (Meta requiere 200 en < 20 segundos
                                            o reintenta el webhook)
```

**Estrategia de ventana de 24 horas:**
Los mensajes de Marketing/Utility de WhatsApp (templates) cuestan entre USD 0.01 y USD 0.07 por mensaje enviado. En cambio, si el cliente inicia la conversacion, se abre una ventana de 24 horas donde la empresa puede responder libremente sin costo adicional. La estrategia es: el sitio web y los emails de confirmacion tienen links `wa.me/+54911...` para que el cliente inicie el. Para reactivaciones (cliente inactivo mas de 24 horas), ahi si se necesitan templates aprobados.

**Consideracion para produccion — multi-numero:**
Actualmente hay un solo numero de WhatsApp. Si se quiere un numero por area (Ventas, Ops Aereas, Ops Terrestres), cada numero requiere su propio `WHATSAPP_PHONE_NUMBER_ID` y su propio webhook o logica de routing. El schema de `channels` en el CRM ya soporta multiples canales de tipo `whatsapp`.

---

### Gmail OAuth2

**Archivo:** `backend/providers/gmail.py`

**Por que OAuth2 y no password de aplicacion:**
Google depreco la autenticacion por password en 2022. OAuth2 es el unico metodo soportado para acceder a Gmail via API. Adicionalmente, OAuth2 permite revocar acceso sin cambiar la password de la cuenta.

**Variables de entorno requeridas:**
```bash
GOOGLE_CLIENT_ID=xxxxxxx.apps.googleusercontent.com
GOOGLE_CLIENT_SECRET=GOCSPX-xxxxxxx
PUBLIC_URL=https://tu-app.railway.app  # Para construir el redirect_uri
```

**Flujo completo de autenticacion OAuth2:**

```
Admin en Settings → Click "Conectar Gmail"
        ↓
GET /channels/{id}/gmail/auth
        ↓
Redirige a https://accounts.google.com/o/oauth2/v2/auth?
  client_id=...
  redirect_uri=https://tu-app.railway.app/channels/gmail/callback
  scope=gmail.readonly gmail.send gmail.modify
  state={channel_id}    ← Para recuperar el canal en el callback
  access_type=offline   ← Para recibir refresh_token
        ↓
Usuario aprueba en Google
        ↓
GET /channels/gmail/callback?code=...&state={channel_id}
        ↓
Intercambiar code por access_token + refresh_token
        ↓
Guardar en channel.credentials en crm_blob
        ↓
Redirige a 14-settings.html con mensaje de exito
```

**Polling de mensajes:**
Gmail no tiene webhooks push en la version gratuita de la API (si tiene Google Pub/Sub). El mockup usa polling manual via `POST /poll/gmail`. En produccion, reemplazar por Gmail Push Notifications:

```python
# En produccion: usar Gmail Push Notifications
# 1. Crear topic en Google Pub/Sub
# 2. Dar permisos a gmail-api-push@system.gserviceaccount.com
# 3. Llamar gmail.users().watch() para suscribir la casilla
# 4. Google envia POST a tu endpoint cuando llega un email nuevo
# Ventaja: latencia en tiempo real vs. polling cada X minutos
```

**Threading de Gmail:**
Cuando el agente responde un email, la respuesta se envia con los headers `In-Reply-To` y `References` para que Gmail agrupe todos los mensajes en el mismo hilo. El `gmail_thread_id` se guarda en el mensaje para mantener esta asociacion.

---

### Telegram Bot API

**Archivo:** `backend/providers/telegram_bot.py`

**Modelo:** un bot por canal. Si la empresa quiere un bot para Ventas y otro para Soporte, son dos bots distintos con dos tokens distintos, cada uno como un canal separado en el sistema.

**Variables de entorno:**
```bash
PUBLIC_URL=https://tu-app.railway.app  # Para construir la URL del webhook de Telegram
```
El token del bot se guarda por canal en `channel.bot_token` en el crm_blob.

**Registro de webhook:**
Telegram requiere que la aplicacion registre su URL de webhook. Esto no es automatico. El flujo es:

```
Admin en Settings → "Registrar Webhook" para un canal Telegram
        ↓
POST /channels/{id}/telegram/register-webhook
        ↓
Llama a https://api.telegram.org/bot{token}/setWebhook
  con url = https://tu-app.railway.app/webhook/telegram/{channel_id}
        ↓
Telegram confirma el registro
        ↓
A partir de ahora, Telegram hace POST a esa URL por cada mensaje
```

**Matching de contactos:**
Cuando llega un mensaje de Telegram, el sistema busca en el CRM por `telegram_user_id` (el ID numerico que Telegram asigna a cada usuario, diferente del username que puede cambiar). Si no encuentra el contacto, lo crea automaticamente con el nombre del usuario de Telegram.

---

### Tourbo GDS (vuelos)

**Archivo:** `backend/providers/tourbo.py`

**Que es Tourbo:** Flaptek Tourbo es el GDS (Global Distribution System) que usa el turoperador para buscar y reservar vuelos. Tiene inventario real de aerolineas y permite booking directo.

**Variables de entorno:**
```bash
TOURBO_API_URL=https://api.dev.gateway.tourboplus.com  # DEV; en prod cambiar
TOURBO_USERNAME=user_xxxxxxx
TOURBO_PASSWORD=xxxxxxxxx
TOURBO_API_KEY=xxxxxxx
```

**Auth:** HTTP Basic (username:password en Base64) mas header `X-API-KEY`.

**Flujo stateful de reserva:**

```
AVAIL (buscar disponibilidad)
    → Retorna lista de vuelos con IDs de sesion
         ↓
PRICING (cotizar vuelo especifico)
    → Usa IDs del paso anterior
    → Retorna precio actualizado y condiciones
         ↓
BOOKING (reservar)
    → Usa IDs de los pasos anteriores
    → Requiere datos completos de pasajeros (nombre, documento, fecha de nacimiento)
    → Retorna PNR (codigo de reserva)
```

**Bug conocido en el entorno DEV:**
El endpoint DEV de Tourbo tiene un NPE (NullPointerException en el servidor de Flaptek) en la validacion `presentOrFutureDatesValidation`. El codigo Python es correcto. Cuando la API retorna error, el sistema hace fallback automatico a datos mock. Este bug se resuelve cuando Flaptek corrija su entorno DEV o cuando se pase al entorno de produccion de Tourbo.

**Schema de busqueda:**
```json
{
  "searchKind": "ROUND_TRIP",
  "journeys": [
    {"origin": "BUE", "destination": "MAD", "departureDate": "2026-07-15"},
    {"origin": "MAD", "destination": "BUE", "departureDate": "2026-07-30"}
  ],
  "pax": {"adults": 2, "children": 0, "infants": 0},
  "cabin": "Y"
}
```

Los codigos IATA se extraen del texto del mensaje por la IA. `BUE` = Buenos Aires (Ezeiza + Aeroparque), `MAD` = Madrid, etc.

---

### SerpAPI — Google Flights (fallback)

**Archivo:** `backend/providers/serpapi.py`

**Por que SerpAPI y no la API de Google Flights directamente:**
Google no tiene una API publica de Google Flights con precios reales. SerpAPI es un scraper de resultados de Google que expone una API limpia con los mismos resultados que veria un usuario en google.com/flights. No es bookable (no se puede reservar a traves de SerpAPI), pero los precios son orientativos y muy precisos.

**Variables de entorno:**
```bash
SERPAPI_KEY=xxxxxxxxx
```

**Estrategia de fechas flexibles:**
Cuando la IA extrae una fecha aproximada ("julio", "primer quincena de agosto"), SerpAPI busca con ±3 dias de flexibilidad y retorna el rango de precios. Esto permite mostrar al cliente un rango de referencia aun cuando no tiene fechas exactas.

**Mapeo IATA hardcodeado:**
El modulo tiene una tabla de ~20 ciudades principales con sus codigos IATA. Si la ciudad no esta en la tabla, retorna `None` y se saltea la busqueda de SerpAPI. Para produccion, reemplazar con una base de datos de aeropuertos completa (hay datasets publicos de IATA).

---

## 9. Pipeline de Mensaje Entrante

Este es el corazon del sistema. Cuando llega cualquier mensaje (WhatsApp, Telegram, Gmail), se ejecuta la siguiente secuencia:

```
┌─────────────────────────────────────────────────────────────────┐
│                    MENSAJE ENTRANTE                              │
│            (WhatsApp / Telegram / Gmail)                        │
└──────────────────────────┬──────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────────┐
│ 1. PARSEO DEL PAYLOAD                                           │
│    Normalizar el formato especifico del canal a un dict         │
│    estandar: {text, contact_id, channel, channel_id, ts}        │
│    CODIGO DETERMINISTICO — sin IA                               │
└──────────────────────────┬──────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────────┐
│ 2. MATCHING DE CONTACTO EN CRM                                  │
│    Buscar por numero de telefono, email, o telegram_user_id     │
│    Si no existe: crear nuevo pasajero automaticamente           │
│    CODIGO DETERMINISTICO — sin IA                               │
└──────────────────────────┬──────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────────┐
│ 3. MATCHING DE CASO ACTIVO                                      │
│    Buscar caso abierto para ese contact_id                      │
│    Si no existe: crear nuevo caso                               │
│    CODIGO DETERMINISTICO — sin IA                               │
└──────────────────────────┬──────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────────┐
│ 4. CLASIFICACION CON HAIKU                                      │
│    Input: texto del mensaje                                     │
│    Output: {tipo, subtipo, severidad}                           │
│    Tipos: cotizacion | consulta | reserva | reclamo |           │
│           seguimiento | otro                                    │
│    IA — Haiku (~USD 0.0001)                                     │
└──────────────────────────┬──────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────────┐
│ 5. EXTRACCION DE DATOS CON HAIKU                                │
│    Input: texto del mensaje                                     │
│    Output: {destino, origen, fecha_ida, fecha_vuelta,           │
│             pax_adultos, pax_menores, presupuesto_usd}          │
│    MERGE con extracted_data acumulado del caso:                 │
│    Los nuevos valores sobreescriben SOLO si no estan vacios     │
│    (preserva datos de mensajes anteriores del hilo)             │
│    IA — Haiku (~USD 0.0002)                                     │
└──────────────────────────┬──────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────────┐
│ 6. BUSQUEDA EN PROVEEDORES                                      │
│    Solo si: destinos extraidos presentes                        │
│             AND tipo en {cotizacion, consulta, reserva}         │
│    Tourbo AVAIL → resultados reales (o mock si falla)           │
│    SerpAPI → Google Flights (si SERPAPI_KEY configurado)        │
│    LLAMADAS HTTP EXTERNAS — sin IA                              │
└──────────────────────────┬──────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────────┐
│ 7. GENERACION DE 3 SUGERENCIAS CON SONNET                       │
│    Input: historial (12 msgs) + datos acumulados +              │
│           resultados proveedores + system prompts +             │
│           regla de canal + sugerencias previas                  │
│    Output: 3 sugerencias con etiquetas                          │
│    IA — Sonnet (~USD 0.01-0.05)                                 │
└──────────────────────────┬──────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────────┐
│ 8. PERSISTENCIA                                                 │
│    Guardar mensaje en storage_blob.messages                     │
│    Actualizar caso con extracted_data y last_suggestions        │
│    Registrar uso de tokens en metrics.token_usage               │
│    ESCRITURA EN SUPABASE — sin IA                               │
└──────────────────────────┬──────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────────┐
│ 9. RESPONSE                                                     │
│    {ok: true, case_id: "...", message_id: "..."}                │
│    El agente ve el caso actualizado con las sugerencias         │
│    en el inbox                                                  │
└─────────────────────────────────────────────────────────────────┘
```

### Manejo de errores en el pipeline

Si cualquier paso falla, el sistema no debe perder el mensaje. El patron es:

```python
try:
    classification = await ai.classify_message(text)
except Exception as e:
    logger.error(f"Clasificacion fallo: {e}")
    classification = {"tipo": "otro", "severidad": 1}  # Fallback seguro

# El pipeline continua con el fallback
```

El mensaje siempre se guarda, aunque la IA falle. Es mejor tener un mensaje sin sugerencias que perder el mensaje.

---

## 10. Frontend

### Arquitectura del frontend

El frontend es vanilla HTML + Tailwind CSS + JavaScript. No hay framework, no hay bundler, no hay transpilacion. Cada pantalla es un archivo HTML independiente que:

1. Incluye `assets/app.js` via `<script src="../assets/app.js">`
2. Llama a `ui.sidebar("nombre-seccion")` para renderizar el sidebar
3. Hace llamadas a la API via `window.api`
4. Maneja su propio estado de UI

### API client: `window.api`

Definido en `assets/app.js`. Todos los metodos son `async` y retornan el body parseado como JSON.

```javascript
// Uso correcto
const inbox = await api.get('/inbox');
const result = await api.post('/cases/123/reply', { text: '...' });
await api.put('/cases/123/status', { status: 'closed' });
await api.del('/channels/456');  // IMPORTANTE: es api.del, NO api.delete
```

**El error mas comun:** llamar `api.delete()` en lugar de `api.del()`. `delete` es palabra reservada en JavaScript y no funciona como nombre de metodo en algunos contextos. El metodo correcto es siempre `api.del`.

**Manejo de 401:** si cualquier llamada retorna 401, `api._handle()` automaticamente redirige a `01-login.html`. No hay que manejar este caso en cada pantalla.

### Helpers globales

| Helper | Uso |
|--------|-----|
| `ui.sidebar(active)` | Renderiza el sidebar con el item `active` resaltado. Llamar al inicio de cada pantalla. |
| `ui.wireHealth()` | Inicia polling a `/health` y muestra indicador de conexion en la UI. |
| `fmt.avatar(nombre)` | Genera un `<div>` con las iniciales del nombre y un color determinisitco basado en el string. |
| `window.escapeHtml(str)` | Escapa `<`, `>`, `&`, `"` para evitar XSS en interpolaciones de strings. **SIEMPRE usar cuando se inserta contenido de usuario en el DOM.** |

### Seguridad XSS

El patron correcto para insertar texto de usuario en el DOM:

```javascript
// INCORRECTO — vulnerable a XSS
element.innerHTML = `<p>${mensaje.text}</p>`;

// CORRECTO
element.innerHTML = `<p>${escapeHtml(mensaje.text)}</p>`;
```

Si el texto proviene de un usuario externo (mensaje de WhatsApp, email), siempre usar `escapeHtml`. Si proviene de la propia aplicacion (labels fijos, nombres de agentes del sistema), es opcional.

### Endpoints de archivos estaticos

FastAPI sirve los archivos del frontend via `StaticFiles`. La configuracion en `main.py`:

```python
from fastapi.staticfiles import StaticFiles
app.mount("/producto", StaticFiles(directory="producto", html=True), name="producto")
app.mount("/cliente-test", StaticFiles(directory="cliente-test", html=True), name="cliente-test")
```

Esto significa que la URL `https://app.railway.app/producto/02-inbox.html` sirve el archivo `mockups/producto/02-inbox.html`.

### Descripcion de cada pantalla

| Archivo | Ruta en app | Funcion |
|---------|------------|---------|
| `01-login.html` | `/producto/01-login.html` | Formulario de email/password. Guarda JWT en localStorage. Redirige a inbox. |
| `02-inbox.html` | `/producto/02-inbox.html` | Lista de casos con filtros por canal, status, agente asignado. Click en caso → abre detalle. |
| `03-caso-detalle.html` | `/producto/03-caso-detalle.html` | Timeline de mensajes + panel lateral con copiloto IA (sugerencias, datos extraidos, resultados de vuelos). |
| `04-a-seguir.html` | `/producto/04-a-seguir.html` | Lista de recordatorios proactivos generados por IA. El agente puede marcarlos como completados. |
| `05-mi-perfil.html` | `/producto/05-mi-perfil.html` | Perfil del agente + editor de system prompt personal. Permite personalizar el tono de las sugerencias. |
| `06-crm-agencia.html` | `/producto/06-crm-agencia.html` | CRUD de agencias (B2B). Vista de ficha con historial de casos. |
| `06b-crm-pasajero.html` | `/producto/06b-crm-pasajero.html` | CRUD de pasajeros (B2C). Incluye campo de system prompt por contacto. |
| `07-pipeline.html` | `/producto/07-pipeline.html` | Vista Kanban del pipeline de ventas. Columnas: Lead → Cotizado → Negociacion → Cerrado/Perdido. |
| `08-proveedores.html` | `/producto/08-proveedores.html` | Estado de proveedores integrados. Test de conexion. |
| `09-afk-reglas.html` | `/producto/09-afk-reglas.html` | Configuracion de respuestas automaticas cuando el agente esta ausente (AFK). |
| `10-guardia.html` | `/producto/10-guardia.html` | Panel de guardia 24/7. Configuracion de escalamiento fuera de horario. |
| `11-auto-tuning.html` | `/producto/11-auto-tuning.html` | Lista de propuestas de mejora de system prompts generadas por Sonnet. Permite aprobar, editar o rechazar. |
| `12-admin.html` | `/producto/12-admin.html` | Administracion completa: usuarios, roles, permisos de LLM, limites de tokens. Solo para rol admin/supervisor. |
| `13-dashboard.html` | `/producto/13-dashboard.html` | Metricas: tokens consumidos, costo total, SLA de respuesta, tasa de conversion, casos por agente. |
| `14-settings.html` | `/producto/14-settings.html` | Configuracion de canales (agregar/quitar Gmail, Telegram, WhatsApp), workspace, integraciones. |

---

## 11. Endpoints de la API

### Auth

| Metodo | Ruta | Auth | Descripcion |
|--------|------|------|-------------|
| POST | `/auth/login` | No | Email + password → JWT de 60 dias |
| GET | `/auth/me` | Bearer | Datos del agente autenticado (nombre, rol, area, system_prompt) |

### Inbox y Casos

| Metodo | Ruta | Auth | Descripcion |
|--------|------|------|-------------|
| GET | `/inbox` | Bearer | Lista casos activos. Query params: `channel`, `status`, `agent_id` |
| GET | `/cases/{id}` | Bearer | Detalle de caso con mensajes ordenados por `ts` |
| POST | `/cases/{id}/reply` | Bearer | Enviar respuesta. Detecta el canal del caso y usa el provider correcto |
| PUT | `/cases/{id}/status` | Bearer | Cambiar status: `open` \| `closed` \| `pending` |
| PUT | `/cases/{id}/tags` | Bearer | Actualizar lista de tags del caso |
| GET | `/cases/{id}/suggestions` | Bearer | Pedir nuevas sugerencias IA para el caso (llama a Sonnet) |
| POST | `/inbox` | Bearer | Crear mensaje entrante simulado (usado por cliente-test) |

### CRM

| Metodo | Ruta | Auth | Descripcion |
|--------|------|------|-------------|
| GET | `/crm` | Bearer | CRM completo: agencias, pasajeros, agentes, tags, canales |
| GET | `/crm/contacts` | Bearer | Lista de contactos (agencias + pasajeros unificados) |
| POST | `/crm/contacts` | Bearer | Crear contacto. Body: `{tipo, nombre, email, whatsapp, ...}` |
| PUT | `/crm/contacts/{id}` | Bearer | Editar contacto |
| GET | `/crm/agents` | Bearer | Lista de agentes del workspace |
| PUT | `/crm/agents/{id}/system_prompt` | Bearer | Actualizar system prompt personal de un agente |
| POST | `/crm/tags` | Bearer | Crear tag en el workspace |
| DELETE | `/crm/tags/{id}` | Bearer | Eliminar tag |

### Canales

| Metodo | Ruta | Auth | Descripcion |
|--------|------|------|-------------|
| GET | `/channels` | Bearer | Lista de canales. **Sin incluir tokens ni secrets.** |
| POST | `/channels` | Bearer | Crear canal. Body: `{type, nombre, assigned_agents, [bot_token]}` |
| PUT | `/channels/{id}` | Bearer | Editar nombre y agentes asignados |
| DELETE | `/channels/{id}` | Bearer | Eliminar canal. Si es Telegram, desregistra el webhook. |
| GET | `/channels/{id}/gmail/auth` | Bearer | Inicia OAuth2 de Google. Redirige al browser a Google. |
| GET | `/channels/gmail/callback` | No | Callback de Google OAuth2. Recibe `code` y `state`. Sin auth porque Google no sabe el JWT. |
| POST | `/poll/gmail` | Bearer | Polling manual de emails no leidos en todos los canales Gmail activos |
| POST | `/channels/telegram/validate-token` | Bearer | Verifica que un bot token de Telegram es valido antes de crear el canal |
| POST | `/channels/{id}/telegram/register-webhook` | Bearer | Registra la URL del webhook en Telegram |
| POST | `/webhook/telegram/{channel_id}` | **No** | Recibe updates de Telegram. Sin auth — Telegram no envia JWT. |
| POST | `/webhook/whatsapp` | **No** | Recibe mensajes de WhatsApp. Sin auth — Meta no envia JWT. |
| GET | `/webhook/whatsapp` | **No** | Verificacion de webhook Meta. Sin auth. |

**Nota sobre endpoints publicos:** Los webhooks de WhatsApp y Telegram no tienen autenticacion JWT porque los llamantes externos (Meta, Telegram) no conocen los tokens de los agentes. La seguridad se implementa de otra forma:
- WhatsApp: verifica el `X-Hub-Signature-256` header (HMAC de Meta)
- Telegram: verifica que el `channel_id` en la URL existe y tiene un bot_token configurado

### Busqueda de vuelos

| Metodo | Ruta | Auth | Descripcion |
|--------|------|------|-------------|
| POST | `/search/tourbo` | Bearer | Buscar vuelos en Tourbo GDS. Body: schema de AVAIL |
| POST | `/search/flights` | Bearer | Buscar vuelos via SerpAPI. Body: `{origen, destino, fecha, ...}` |

### Admin

| Metodo | Ruta | Auth | Rol minimo |
|--------|------|------|------------|
| GET | `/admin/users` | Bearer | supervisor |
| POST | `/admin/users` | Bearer | admin |
| PUT | `/admin/users/{id}` | Bearer | supervisor |
| PUT | `/admin/users/{id}/password` | Bearer | admin |
| DELETE | `/admin/users/{id}` | Bearer | admin |

### IA y automatizacion

| Metodo | Ruta | Auth | Descripcion |
|--------|------|------|-------------|
| POST | `/reminders/generate` | Bearer | Genera recordatorios proactivos para todos los casos activos usando Haiku |
| GET | `/reminders` | Bearer | Lista recordatorios del agente autenticado |
| PUT | `/reminders/{id}` | Bearer | Actualizar status: `completed` \| `dismissed` |
| GET | `/tuning/reviews` | Bearer | Lista de propuestas de auto-tuning pendientes de revision |
| POST | `/tuning/generate` | Bearer | Genera propuesta de tuning para el agente autenticado usando Sonnet |
| PUT | `/tuning/reviews/{id}` | Bearer | Aprobar/rechazar/editar propuesta. Si se aprueba, actualiza el system_prompt del agente. |
| GET | `/channel-rules` | Bearer | Obtener reglas de tono actuales por canal |
| PUT | `/channel-rules/{channel}` | Bearer | Actualizar regla de tono. `{channel}` = `whatsapp` \| `email` \| `telegram` |
| GET | `/metrics` | Bearer | Metricas de uso (tokens, costo, SLA, conversion) |
| GET | `/health` | No | Estado de todas las integraciones. Usado por `ui.wireHealth()` para el indicador de conexion. |

---

## 12. Variables de Entorno

El archivo `.env.example` tiene todas las variables documentadas. El `.env` real **nunca se commitea al repositorio** (esta en `.gitignore`).

### Variables criticas (sin estas, el sistema no arranca en produccion)

```bash
DATABASE_URL=postgresql://user:password@host:5432/dbname
# Supabase: ir a Settings → Database → Connection String
# Usar el "Session mode" (port 5432), no el "Transaction mode" (port 6543)
# para compatibilidad con el ORM

JWT_SECRET=un-string-aleatorio-de-al-menos-64-caracteres
# Generar con: python -c "import secrets; print(secrets.token_hex(32))"
# NUNCA usar el valor del .env.example en produccion
```

### Variables de IA

```bash
ANTHROPIC_API_KEY=sk-ant-api03-...
# Sin esta variable, el sistema usa mocks deterministicos.
# Util para demos y testing de UI.
# Para produccion con clientes reales, es obligatoria.

# Modelos por defecto (opcionales — el codigo tiene defaults)
HAIKU_MODEL=claude-haiku-4-5-20251001
SONNET_MODEL=claude-sonnet-4-6
# Para cambiar a Opus para supervisores: SONNET_MODEL=claude-opus-4-7
# (verificar pricing actual en console.anthropic.com antes de hacerlo)
```

### Variables por canal

```bash
# WhatsApp — obligatorio si se usa WhatsApp
WHATSAPP_TOKEN=EAAxxxxxxxxx
WHATSAPP_PHONE_NUMBER_ID=114675982518xxxx
WHATSAPP_VERIFY_TOKEN=plataforma-webhook-2026
# El verify token puede ser cualquier string. Se configura identico
# en el dashboard de Meta Developers.

# Gmail — obligatorio si se usa Gmail
GOOGLE_CLIENT_ID=xxxxxxx.apps.googleusercontent.com
GOOGLE_CLIENT_SECRET=GOCSPX-xxxxxxx
# Obtener en: console.cloud.google.com → APIs → Credentials

# URL publica — obligatorio para Gmail OAuth2 callback y webhooks de Telegram
PUBLIC_URL=https://tu-app.railway.app
# En Railway: Settings → Domains → tu dominio
# IMPORTANTE: sin trailing slash

# Tourbo GDS — opcional (usa mock si no esta)
TOURBO_API_URL=https://api.dev.gateway.tourboplus.com
TOURBO_USERNAME=user_xxxxxxx
TOURBO_PASSWORD=xxxxxxxxx
TOURBO_API_KEY=xxxxxxx

# SerpAPI — opcional (se saltea si no esta)
SERPAPI_KEY=xxxxxxxxx
```

---

## 13. Setup Local

### Prerequisitos

- Python 3.12 (`python --version`)
- pip o uv
- Cuenta en Supabase (gratis)
- Git

### Pasos

```bash
# 1. Clonar el repositorio
git clone https://github.com/figue69/plataforma-omnicanal.git
cd plataforma-omnicanal/mockups/backend

# 2. Crear entorno virtual (recomendado)
python -m venv venv
source venv/bin/activate  # En Windows: venv\Scripts\activate

# 3. Instalar dependencias
pip install -r requirements.txt

# 4. Configurar variables de entorno
cp .env.example .env
# Editar .env con tu editor preferido
# El minimo para correr localmente: DATABASE_URL + JWT_SECRET
# Sin ANTHROPIC_API_KEY el sistema usa mocks (perfecto para empezar)

# 5. Inicializar la base de datos en Supabase
# Primero: ejecutar schema.sql en el editor SQL de Supabase
# Despues: correr el seed con datos de prueba
python seed.py
# seed.py crea:
# - 3 agentes de prueba (admin, supervisor, agente)
# - storage_blob vacio
# - crm_blob con canales y reglas por defecto

# 6. Levantar el backend
uvicorn main:app --reload --port 8000
# --reload: reinicia automaticamente cuando se edita un .py

# 7. Verificar que funciona
curl http://localhost:8000/health
# Debe retornar: {"status": "ok", "integrations": {...}}

# 8. Abrir el frontend
# El backend sirve los archivos estaticos — abrir directamente en el browser:
open http://localhost:8000/producto/01-login.html
# Credenciales de prueba (creadas por seed.py):
# Email: admin@turoperador.com / Password: admin123
# Email: agente@turoperador.com / Password: agente123

# 9. Simulador de cliente (para probar el pipeline sin canales reales)
open http://localhost:8000/cliente-test/index.html
# Permite enviar mensajes simulados y ver como el backend los procesa
```

### Troubleshooting comun en setup local

| Error | Causa | Solucion |
|-------|-------|----------|
| `ModuleNotFoundError: No module named 'fastapi'` | No se activo el venv o no se instalo requirements | `source venv/bin/activate && pip install -r requirements.txt` |
| `401 Unauthorized` en cualquier endpoint | JWT_SECRET en `.env` diferente al usado para generar el token | Borrar el `jwt_token` del localStorage del browser y hacer login de nuevo |
| `Connection refused` a Supabase | `DATABASE_URL` incorrecto o Supabase bloqueando la IP | Verificar DATABASE_URL. En Supabase → Settings → Database → verificar que la IP local no este bloqueada (o deshabilitar IP allowlist para desarrollo) |
| Las sugerencias IA retornan texto generico | `ANTHROPIC_API_KEY` no configurada | Normal — es el fallback mock. Agregar la key al `.env` si se necesita IA real. |
| `uvicorn: command not found` | No se instalo requirements o no se activo el venv | `pip install uvicorn` o activar el venv |

---

## 14. Deploy en Railway

### Estructura del proyecto en Railway

Railway detecta automaticamente que es una app Python por la presencia de `requirements.txt`. La configuracion adicional esta en `railway.toml`:

```toml
[build]
builder = "nixpacks"

[deploy]
startCommand = "uvicorn main:app --host 0.0.0.0 --port $PORT"
# Railway inyecta $PORT automaticamente (generalmente 8080 o similar)
# --host 0.0.0.0 es obligatorio para que Railway pueda rutear trafico al proceso
```

### Checklist de deploy

```bash
# 1. Conectar repositorio
# Railway.app → New Project → Deploy from GitHub repo → Seleccionar repo
# Railway detecta el proyecto y configura el primer deploy automaticamente

# 2. Configurar variables de entorno en Railway
# Railway → Proyecto → Variables → Add All
# Variables obligatorias:
# - DATABASE_URL
# - JWT_SECRET
# - PUBLIC_URL (la URL que Railway asigna, ej. https://app.up.railway.app)
# - Resto segun canales activos

# 3. Verificar que el deploy fue exitoso
# Railway → Proyecto → Deployments → ver logs
# Debe aparecer: "Uvicorn running on http://0.0.0.0:PORT"

# 4. Verificar health
curl https://tu-app.up.railway.app/health

# 5. Configurar webhook de WhatsApp en Meta Developers
# Meta Developers → WhatsApp → Configuration → Webhook
# Callback URL: https://tu-app.up.railway.app/webhook/whatsapp
# Verify Token: el mismo que WHATSAPP_VERIFY_TOKEN en las env vars

# 6. Registrar webhooks de Telegram para cada canal
# Ir a 14-settings.html → cada canal Telegram → "Registrar Webhook"
# Esto llama a POST /channels/{id}/telegram/register-webhook
```

### Consideraciones importantes de Railway

**Persistencia de datos:** el proceso de Railway puede reiniciarse. Si hubiera datos en memoria o en disco del servidor, se perderian. Por esto la DB es Supabase externa: los datos sobreviven cualquier reinicio o redeploy.

**HTTPS automatico:** Railway asigna un certificado SSL automaticamente. Los webhooks de WhatsApp y Telegram requieren HTTPS — esto ya viene resuelto.

**Costos de Railway:** Railway cobra por CPU y RAM usadas. Un proceso de FastAPI en idle consume muy poco. Con trafico moderado (50-100 conversaciones/dia), el costo de Railway es menor a USD 10/mes.

**Variables en Railway vs. .env:** en Railway, las variables de entorno se configuran en el dashboard y se inyectan en el proceso. No se usa el archivo `.env` en produccion — ese archivo es solo para desarrollo local.

---

## 15. Decisiones Arquitectonicas Clave

Esta seccion explica el "por que" de las decisiones mas importantes. Es critica para que las desarrolladoras que se sumen entiendan las restricciones y no "arreglen" cosas que fueron decisiones intencionales.

### Por que HTML + vanilla JS y no React/Vue/Next.js

**Contexto:** El mockup fue desarrollado iterativamente por el CTO con asistencia de Claude. La prioridad era velocidad de iteracion, no mantenibilidad a largo plazo.

**Decision:** Sin herramientas de build, sin TypeScript, sin JSX. Agregar una pantalla era copiar un HTML y modificarlo. Con React + TypeScript + Vite, solo configurar el proyecto base lleva horas.

**Para produccion:** esta decision DEBE revisarse. Vanilla JS con 14 pantallas ya empieza a ser dificil de mantener. Para el siguiente sprint de desarrollo:
- Evaluar Next.js 14 con App Router + TypeScript
- O React + Vite + TypeScript como alternativa mas liviana
- El API client (`window.api`) se puede portar directamente a un modulo TypeScript

### Por que FastAPI y no Django/Rails

**Decision:** FastAPI tiene async nativo, validacion Pydantic, docs automaticas, y setup minimo. Con Python el CTO puede iterar rapido en la logica de IA sin cambiar de lenguaje.

**Para produccion:** FastAPI escala bien. No hay razon fuerte para migrar a Django o Rails. Si el equipo nuevo tiene experiencia solida en Rails o Django, podria evaluarse una migracion, pero no es prioritario.

### Por que JSONB blobs y no tablas normalizadas

**Decision:** el esquema cambio en cada sprint. Con JSONB, agregar un campo nuevo no requiere migration. El costo es que no hay indices ni constraints de integridad.

**Para produccion:** normalizar en tablas reales. El blob actual se puede migrar con un script de Python que lea el blob y lo inserte en las tablas nuevas. La migracion es un MVP de produccion por si sola.

### Por que el agente siempre revisa las sugerencias

**Decision:** Human-in-the-loop es un requisito de negocio no negociable. La IA puede confundir destinos, inventar precios, o no conocer excepciones de politica comercial.

**Esto NO es una limitacion tecnica.** Es una decision de negocio deliberada. No "arreglar" esto agregando un auto-reply sin revision humana, independientemente de cuan buenas sean las sugerencias.

La unica excepcion contemplada para el futuro: respuestas automaticas fuera de horario para mensajes de severidad baja (tipo `saludo` o `consulta_horarios`) con un template fijo preaprobado por el equipo. Esto se gestiona en `09-afk-reglas.html`.

### Por que 3 sugerencias y no 1

**Decision:** el estilo optimo varia por tipo de cliente, tipo de consulta, y preferencia del agente. Con 3 alternativas etiquetadas, el agente elige en segundos.

**El valor a largo plazo:** los datos de cual etiqueta elige cada agente en cada contexto alimentan el auto-tuning. Con suficientes datos, el sistema puede predecir cual alternativa elegira el agente y presentarla primera (o incluso ajustar el prompt para que las 3 alternativas sean mas relevantes para ese agente especifico).

### Por que wa.me deep links en lugar de templates de WhatsApp

**Decision:** los templates de Meta requieren aprobacion previa (proceso de dias a semanas), tienen costo por mensaje enviado, y tienen formato rigido. Con `wa.me` links en el sitio web y emails de confirmacion, el cliente inicia el la conversacion — abre la ventana gratuita de 24 horas.

**Cuando si se necesitan templates:** para reactivaciones (cliente inactivo mas de 24 horas), recordatorios de pago, o confirmaciones de reserva. Para esto, registrar templates en Meta Developers y usar el endpoint de template messages de WhatsApp Cloud API.

### Por que Tourbo + SerpAPI y no solo uno

**Decision:** Tourbo es el GDS real del turoperador (inventario real, bookable). SerpAPI (Google Flights) es mas amplio geograficamente pero no bookable. Son complementarios:
- Tourbo primero para todo lo que cubre (rutas donde el operador tiene acuerdo)
- SerpAPI como fallback para orientar precios en rutas que Tourbo no tiene
- Mostrar ambos al agente da una vision mas completa

### Por que Railway y no AWS/GCP

**Decision:** Railway provee HTTPS automatico, deploy automatico desde GitHub, y zero-config. Para la fase de mockup y prototipo, el overhead operacional de AWS (IAM, VPC, EC2, RDS, ALB, etc.) no esta justificado.

**Para produccion escalada:** cuando el volumen lo justifique, evaluar migracion a AWS (ECS Fargate + RDS Aurora) o GCP (Cloud Run + Cloud SQL). Railway tiene limitaciones en CPU burstable y no tiene SLAs de enterprise. Para un turoperador de 32 personas con trafico moderado, Railway es suficiente por al menos 1-2 anos.

---

## 16. Estado de Features

### Implementadas y funcionales en el mockup

- [x] Auth JWT + bcrypt + roles (agente/supervisor/admin)
- [x] Inbox omnicanal unificado con filtros por canal, status y agente
- [x] Casos con timeline de mensajes y contexto acumulado
- [x] Copiloto IA: clasificacion con Haiku, extraccion de datos, 3 sugerencias rankeadas con Sonnet
- [x] Memoria de contexto: historial de hilo (12 mensajes) + datos acumulados + resultados de proveedores previos
- [x] System prompt por agente (editable desde Mi Perfil)
- [x] System prompt por contacto (editable desde ficha del CRM)
- [x] Reglas de tono y formato por canal (WhatsApp/Email/Telegram) — editables
- [x] Panel "A seguir" con recordatorios proactivos generados por Haiku
- [x] Auto-tuning de system prompts con Sonnet (propuesta → revision humana → aprobacion)
- [x] Pipeline de ventas Kanban (Lead → Cotizado → Negociacion → Cerrado/Perdido)
- [x] CRM de Agencias (B2B) y Pasajeros (B2C) con tags y notas
- [x] Gestion dinamica de canales (agregar/quitar Gmail, Telegram, WhatsApp desde UI)
- [x] WhatsApp Cloud API (Meta) — webhook real + envio de mensajes
- [x] Gmail OAuth2 — autenticacion + polling + envio con threading de hilo
- [x] Telegram Bot — webhook + envio + registro dinamico de webhook
- [x] Tourbo GDS — AVAIL/PRICING/BOOKING con fallback automatico a mock
- [x] SerpAPI Google Flights con flexibilidad de fechas (±3 dias)
- [x] Dashboard de metricas (tokens consumidos, costo, SLA, conversion)
- [x] Guardia 24/7 con escalamiento
- [x] AFK y reglas de auto-respuesta
- [x] Admin: usuarios, canales, permisos de LLM por agente, limites de tokens

### Pendientes para Fase 1 (prototipo con trafico real)

Estas son las features necesarias para pasar de mockup a prototipo funcional con clientes reales. Ordenadas por prioridad:

#### Criticas (bloquean lanzamiento)

- [ ] **Gmail Push Notifications:** reemplazar el polling manual por tiempo real via Google Pub/Sub. El polling actual puede tardar minutos en detectar un email nuevo. Para clientes reales esto es inaceptable.
- [ ] **Templates WhatsApp:** para reactivar conversaciones de clientes inactivos (> 24 horas). Sin templates, solo se puede responder a conversaciones iniciadas por el cliente.
- [ ] **Normalizacion de DB:** migrar de JSONB blobs a tablas reales con indices. Sin esto, el sistema sera lento con mas de 500 casos activos.
- [ ] **MFA para auth:** passwords solos son insuficientes para una plataforma que accede a datos de clientes.

#### Importantes (primer mes post-lanzamiento)

- [ ] **Busqueda global en inbox:** actualmente no hay busqueda full-text en conversaciones. Critico para operaciones.
- [ ] **Notas internas privadas en casos:** los agentes necesitan dejar notas que el cliente no ve.
- [ ] **Quick replies con variables:** templates de respuesta rapida del equipo con variables (`{{nombre}}`, `{{destino}}`).
- [ ] **SLA automatico + escalamiento:** si un caso no tiene respuesta en X minutos, escalar a supervisor.
- [ ] **Unificacion de identidades cross-canal:** si el mismo cliente escribe por WhatsApp y por email, unificarlos en un solo contacto. Requiere logica de matching por nombre + numero o nombre + email.

#### Deseables (segundo mes)

- [ ] **RAG sobre tarifarios PDF:** muchos proveedores no tienen API. Cargar sus tarifarios en PDF y hacer RAG para que la IA pueda responder preguntas de precio sin que el agente busque manualmente.
- [ ] **Proveedor de hoteles:** integrar HotelBeds o Expedia Rapid API para cotizaciones de alojamiento.
- [ ] **Multi-numero WhatsApp:** un numero por area de negocio (Ventas, Ops Aereas, Ops Terrestres).
- [ ] **Exportacion de conversaciones:** PDF o CSV para auditoria o compliance.
- [ ] **Indices PostgreSQL:** correlato de la normalizacion. Sin indices, las queries son full-scan del blob.

---

## 17. Guia para Nuevas Desarrolladoras

### Como entender el codebase en 2 horas

1. **Leer `schema.sql`** (10 minutos): entender las 3 tablas y la estructura de los blobs. El resto del sistema es operaciones sobre estas estructuras.

2. **Leer `ai.py` completo** (30 minutos): entender que hace cada funcion, que input espera, que output produce. Este archivo es el corazon del valor diferencial.

3. **Leer el pipeline en `main.py`** (20 minutos): buscar el endpoint `POST /inbox` o `POST /webhook/whatsapp` y seguir el codigo paso a paso. Ver como cada paso del pipeline de 9 etapas se implementa.

4. **Correr el proyecto localmente** (20 minutos): seguir la seccion de Setup Local. Abrir el frontend, hacer login, abrir el cliente-test y enviar un mensaje simulado. Ver el caso aparecer en el inbox con sugerencias.

5. **Explorar `assets/app.js`** (20 minutos): entender el API client y los helpers. Buscar como una pantalla (ej. `02-inbox.html`) usa `api.get()` y renderiza los resultados.

### Como agregar una nueva pantalla al frontend

```bash
# 1. Copiar una pantalla similar como base
cp mockups/producto/02-inbox.html mockups/producto/15-nueva-pantalla.html

# 2. Editar el HTML — cambiar:
#    - El titulo en <title> y en el sidebar
#    - El contenido del <main>
#    - Las llamadas a api.get/post en el <script>

# 3. Agregar al sidebar en assets/app.js
# Buscar la funcion ui.sidebar() y agregar el nuevo item al array de links
```

### Como agregar un nuevo endpoint al backend

```python
# En main.py, siguiendo el patron existente:

@app.get("/mi-nuevo-endpoint")
async def mi_nuevo_endpoint(agent_id: str = Depends(auth.get_current_agent_id)):
    """
    Descripcion clara del endpoint.
    Returns: descripcion del response
    """
    # 1. Leer datos necesarios
    blob = await db.get_storage_blob()
    
    # 2. Logica de negocio (sin IA si es posible)
    resultado = [item for item in blob["cases"] if item["status"] == "open"]
    
    # 3. Si se necesita IA, llamar a ai.py
    # (solo si el codigo deterministico no alcanza)
    
    # 4. Persistir cambios si los hay
    # await db.save_storage_blob(blob)
    
    return {"resultado": resultado}
```

### Como agregar una nueva integracion de canal (ej. Instagram DM)

1. **Crear `providers/instagram.py`** siguiendo el patron de `telegram_bot.py`:
   - `parse_update(payload) → dict` — normaliza el payload de Instagram al formato estandar
   - `send_message(channel, to, text) → bool` — envia un mensaje por Instagram
   - `register_webhook(channel) → bool` — registra la URL del webhook en Instagram
   - `is_configured() → bool` — verifica que las env vars estan presentes

2. **Agregar endpoints en `main.py`:**
   ```python
   @app.post("/webhook/instagram/{channel_id}")
   async def instagram_webhook(channel_id: str, payload: dict = Body(...)):
       # Parsear, buscar contacto, crear caso, pipeline de IA
       ...
   
   # En POST /channels, agregar manejo para type == "instagram"
   ```

3. **Actualizar el frontend en `14-settings.html`:**
   - Agregar "Instagram DM" al dropdown de tipo de canal en el modal "Agregar canal"
   - Agregar los campos especificos de Instagram (app token, page ID)

4. **Agregar al `channel_rules_default` en el crm_blob** (via seed o directamente en Supabase):
   ```json
   "instagram": {
     "tono": "informal",
     "longitud": "corto",
     "usar_emojis": true,
     "instrucciones": "Respuestas cortas. Tono visual y dinamico."
   }
   ```

### Como agregar un nuevo proveedor de viajes (ej. HotelBeds)

1. **Crear `providers/hotelbeds.py`:**
   ```python
   def is_configured() -> bool:
       return bool(os.getenv("HOTELBEDS_API_KEY"))
   
   async def search(destino: str, checkin: str, checkout: str, pax: int) -> list[dict]:
       if not is_configured():
           return []  # No lanzar error, retornar lista vacia
       # Llamar a la API de HotelBeds
       # Retornar lista de hoteles en formato normalizado
       ...
   ```

2. **En el pipeline de mensaje entrante** (en `main.py`), agregar la llamada cuando el tipo de consulta incluye hotel:
   ```python
   if "hotel" in extracted_data.get("tipo_servicio", "").lower():
       hotel_results = await hotelbeds.search(
           extracted_data.get("destino"),
           extracted_data.get("fecha_checkin"),
           extracted_data.get("fecha_checkout"),
           extracted_data.get("pax_adultos", 2)
       )
   ```

3. **En `generate_suggestions()`** en `ai.py`, agregar `hotel_results` al contexto de Sonnet.

4. **En `03-caso-detalle.html`**, agregar una seccion "Hoteles" al panel lateral junto a la de vuelos.

### Como cambiar el modelo Claude por defecto

En `.env`:
```bash
# Para usar Opus para sugerencias (mayor calidad, mayor costo):
SONNET_MODEL=claude-opus-4-7

# Para usar una version mas nueva de Haiku:
HAIKU_MODEL=claude-haiku-4-5-20251001
```

El codigo en `ai.py` lee estas variables al startup:
```python
HAIKU_MODEL = os.getenv("HAIKU_MODEL", "claude-haiku-4-5-20251001")
SONNET_MODEL = os.getenv("SONNET_MODEL", "claude-sonnet-4-6")
```

### Como testear el pipeline sin canales reales

Usar el simulador de cliente (`cliente-test/index.html`):

1. Abrir `http://localhost:8000/cliente-test/index.html`
2. Seleccionar el canal (WhatsApp simulado, Email simulado, Telegram simulado)
3. Escribir un mensaje (ej: "Quiero cotizar vuelos a Cancun para 2 personas en agosto")
4. El simulador llama a `POST /inbox` con el mensaje
5. El pipeline completo corre: clasificacion → extraccion → busqueda de vuelos → sugerencias
6. Abrir el inbox en `http://localhost:8000/producto/02-inbox.html` y ver el caso nuevo

### Como correr los tests (cuando existan)

No hay test suite formal en el mockup — fue una decision deliberada para velocidad de iteracion. Para el prototipo, agregar pytest:

```bash
pip install pytest pytest-asyncio httpx

# Estructura recomendada:
tests/
├── conftest.py       # Fixtures: cliente HTTP, DB de test, agente de prueba
├── test_auth.py      # Login, tokens, permisos por rol
├── test_pipeline.py  # Flujo completo: mensaje → caso → sugerencias
├── test_channels.py  # WhatsApp webhook, Gmail polling, Telegram webhook
└── test_crm.py       # CRUD de contactos, tags, canales
```

El flujo minimo a testear para cada PR:
1. Login → JWT valido
2. Crear caso via `/inbox` → caso aparece en GET `/inbox`
3. GET `/cases/{id}/suggestions` → retorna 3 sugerencias
4. POST `/cases/{id}/reply` → mensaje guardado, caso actualizado

---

## 18. Roadmap a Produccion Final

### Fase 1: Prototipo con trafico real (1-2 meses)

El objetivo de esta fase es lanzar con un subconjunto del equipo (3-5 agentes de Ventas) y trafico real de clientes.

**Tareas tecnicas criticas:**

| Tarea | Esfuerzo estimado | Prioridad |
|-------|------------------|-----------|
| Normalizacion DB (JSONB → tablas reales) | 2-3 semanas | Critica |
| Gmail Push Notifications (reemplazar polling) | 3-5 dias | Critica |
| Templates WhatsApp para reactivaciones | 2-3 dias | Critica |
| MFA para auth | 1 semana | Critica |
| Refresh tokens + blacklist JWT | 3-5 dias | Critica |
| Tests basicos (auth + pipeline + channels) | 1 semana | Alta |
| Busqueda global en inbox | 3-5 dias | Alta |
| Notas internas privadas en casos | 2-3 dias | Alta |
| Migracion frontend a React + TypeScript | 3-4 semanas | Media (paralelizable) |

**Tareas de negocio:**
- Registrar templates de WhatsApp en Meta Developers
- Conseguir acceso al entorno de produccion de Tourbo (resolver bug DEV con Flaptek)
- Capacitacion del equipo en uso de la plataforma
- Definicion del SLA de respuesta que se quiere alcanzar

### Fase 2: Escala completa (3-4 meses)

Con el prototipo validado y el equipo completo usando la plataforma:

| Tarea | Descripcion |
|-------|-------------|
| RAG sobre tarifarios PDF | Subir PDFs de proveedores → embeddings → busqueda semantica por la IA |
| Integracion de hoteles | HotelBeds o Expedia Rapid API |
| Multi-numero WhatsApp | Un numero por area de negocio |
| SLA automatico + escalamiento | Alertas si un caso sin respuesta supera el umbral |
| Unificacion cross-canal | Matching de identidades: mismo cliente en WhatsApp y Email |
| Analytics avanzados | Funnel de conversion, analisis de sentimiento, prediccion de cierre |

### Consideraciones de escala tecnica

Cuando el volumen supere 1000 casos activos simultaneos o 50 agentes:

1. **Cola de mensajes:** los webhooks actuales procesan el pipeline de IA de forma sincrona. Esto puede causar timeouts si Claude tarda. En escala, mover el pipeline a una cola (Celery + Redis, o FastAPI BackgroundTasks + Redis) para responder 200 al webhook inmediatamente y procesar de forma asincrona.

2. **Cache de resultados de proveedores:** si multiples agentes consultan el mismo ruta en el mismo dia, cachear los resultados de Tourbo y SerpAPI en Redis para reducir llamadas y latencia.

3. **Rate limiting:** la API de Claude tiene limites de tokens por minuto. En picos de trafico, implementar rate limiting en el cliente de Anthropic para hacer queue de las llamadas.

4. **Indices de PostgreSQL:** con tablas normalizadas, los indices criticos son:
   - `cases(contact_id)`, `cases(status)`, `cases(assigned_agent)`, `cases(updated_at DESC)`
   - `messages(case_id)`, `messages(ts DESC)`
   - `agents(email)` — unico, ya lo tiene

5. **Migracion de Railway a AWS/GCP:** para SLAs de enterprise o compliance de datos, considerar la migracion. Un punto de partida seria AWS: ECS Fargate (backend), RDS Aurora PostgreSQL (DB), CloudFront (frontend estatico), Secrets Manager (env vars).

---

*Documento generado: Abril 2026*  
*Contacto tecnico: francisco.f@fya.com.ar*
