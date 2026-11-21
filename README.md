# Reactiva tu pueblo

Hackathon UAL 2026 · Reto 1 "Reactiva tu pueblo". Una agenda y un mapa comunes para los pueblos de la Mancomunidad: Lucainena de las Torres, Velefique, Senés, Uleila del Campo y Tahal.

Hoy, lo que pasa en estos pueblos está repartido entre Facebook, carteles y grupos de mensajería. La app recoge lo que ya publican las asociaciones (también desde Facebook, con IA), lo confirma con quien lo publicó por Telegram y avisa a los vecinos a los que les puede interesar. **Las entidades no tienen que publicar dos veces.**

- **App:** https://TU-APP.vercel.app
- **API:** https://reactiva-api.onrender.com/health
- **Bot de Telegram:** https://t.me/TU_BOT

## Arquitectura (3 piezas)

| Pieza | Tecnología | Dónde |
|---|---|---|
| App + panel del moderador | Ionic + Angular (Standalone), Leaflet + OpenStreetMap | Vercel |
| Datos, login, carteles y tiempo real | Supabase (PostgreSQL + Auth + Storage + Realtime) con las reglas en la propia BD | Supabase Cloud |
| Servidor | Python FastAPI: bot de Telegram, lector de Facebook, IA (Gemini Flash), avisos y calendario `.ics` | Render |

**Reglas antes que IA:** plazas, lista de espera, reputación, quién publica directamente y el recomendador son reglas SQL (gratis e instantáneas). La IA solo extrae datos de carteles, filtra el spam dudoso, redacta mensajes y entiende las notas de voz. Si Gemini falla, el sistema sigue funcionando sin IA.

## Cómo arrancarlo

### 1. Base de datos
En Supabase: activa la extensión **pg_cron** (Database → Extensions) y ejecuta `supabase/schema.sql` en SQL Editor.

**Estados automáticos de los eventos:** la base de datos los calcula sola según la hora, las plazas y la fecha límite de inscripción (`abierto → completo → inscripcion_cerrada → en_curso → finalizado`). El moderador solo pone los estados manuales (`pendiente_moderacion`, `pospuesto`, `cancelado`, `rechazado`).

### 2. Servidor (`api/`)
```bash
cd api
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env        # y rellena los valores
uvicorn main:app --reload   # http://localhost:8000/docs
python demo_seed.py         # opcional: datos de demo
```

### 3. App (`app/`)
```bash
cd app
npm i
ionic serve                 # http://localhost:8100
```

## Variables de entorno
Están en `api/.env.example`. **Ninguna clave está en el código ni en el repositorio.**

## Endpoints principales
🔒 = requiere sesión (`Authorization: Bearer`). 🛡️ = solo moderador.

`GET /health` · `POST /telegram/webhook` · `POST /ingesta/facebook` · `POST /ia/extraer` 🔒 · `GET /eventos/{id}.ics` · `POST /eventos/{id}/avisar` 🔒 (moderador o autor) · `POST /eventos/{id}/chat-ia` 🔒 · `POST /recomendaciones/enviar` 🛡️ · `POST /resenas/pedir/{event_id}` 🔒 (moderador o autor) · `POST /moderacion/promociones/revisar` 🛡️ · `POST /moderacion/promociones/decidir` 🛡️ · `GET /ia/consumo`

**Automático en segundo plano** (`api/tareas.py`, cada 5 min): estados de los eventos, petición de reseñas al terminar un evento y revisión con IA de los candidatos a Publicador. Si la IA aprueba con seguridad, el candidato se promueve solo; si duda, lo decide el moderador.

También: avisar al autor cuando un moderador aprueba o rechaza su propuesta y, cada 6 horas, el agente de eventos reales.

## Eventos reales: agente de Facebook y noticias (arranque en 5 minutos)
La agenda se llena con eventos **reales** de los pueblos, nunca inventados. La IA propone; el sistema valida; una persona decide.

1. En `api/.env` pon `APIFY_TOKEN=` (apify.com → Settings → API & Integrations) y al menos una clave de visión (`GEMINI_API_KEY` vale).
2. En `api/scraper/fuentes_eventos.json` pon las URL de las páginas **públicas** de Facebook (ayuntamientos, asociaciones, negocios; nunca perfiles personales ni grupos privados) y, si quieres, más noticias en `articulos`.
3. Desde `api/`:
```bash
python scraper/agente_eventos.py --dry-run      # qué haría, sin escribir nada
python scraper/agente_eventos.py                # guarda ingestas y eventos en Supabase
python scraper/agente_eventos.py --archivo pruebas/apify_XXXX.json   # repetir sin gastar crédito
```
Cómo decide: cada post pasa por reglas, spam e IA (texto con freellmpool → Gemini; carteles con `extractor_vlm.py`). Las fechas relativas ("este sábado") se calculan desde la fecha de publicación. Los recordatorios del mismo evento no se duplican. Fuente oficial y la IA segura → **publicado**; si falta algo o la IA duda → **borrador_ia**; completo → **pendiente_moderacion**. Las webs de los ayuntamientos bloquean los programas automáticos y no se intenta saltar esa protección.

**Extractor de carteles** (`api/extractor_vlm.py`): cascada de modelos de visión gratuitos (Gemini, NVIDIA, Mistral, Groq, OpenRouter) con segunda opinión si la confianza es baja; nunca inventa (lo que no sale en el cartel va a `missing`). Registra cada llamada en `api/pruebas/ia_llamadas.csv`.
```bash
python extractor_vlm.py --healthcheck              # qué proveedores responden y cuánto tardan
python extractor_vlm.py --listar-modelos nvidia    # los catálogos gratuitos cambian: compruébalos
python extractor_vlm.py cartel.jpg --municipio Senés
python tests/test_extractor.py                     # 8 carteles de prueba vs tests/golden.json (o: python -m pytest tests/)
```

## Proponer y gestionar planes por Telegram
Proponer (texto, nota de voz o foto del cartel) → resumen → «Sí, publícalo». **Usuario** → siempre lo revisa un moderador. **Publicador** → se publica solo si su reputación por reseñas es buena y la IA no duda; si no, al moderador. Spam y planes de fuera de la Mancomunidad se rechazan explicando el porqué.
`/mis_propuestas` (estado, ✏️ editar fecha/lugar/precio/plazas, ❌ cancelar; se avisa a los apuntados) · `/moderar` (moderadores: ✅ aprobar / 🚫 rechazar con motivo / ✏️ editar; el autor recibe el aviso, también si se decide en el panel web).

**Robustez:** todas las llamadas a Telegram (10 s) y a la IA (15 s) tienen límite de tiempo. Si fallan, el servidor sigue con un plan B sin IA y nunca se queda esperando. Sin IA, los candidatos esperan al moderador en vez de promoverse.

Documentación interactiva: `/docs` en la URL de la API.

## Equipo
A1 · A2 · B1 · B2 · C1 (poned los nombres)

## Créditos
Mapas © colaboradores de OpenStreetMap. IA: Google Gemini. Mensajería: Telegram Bot API.
