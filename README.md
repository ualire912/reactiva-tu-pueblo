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
En Supabase, ve a SQL Editor y ejecuta `supabase/schema.sql` y después `supabase/parches.sql`.

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
`GET /health` · `POST /telegram/webhook` · `POST /ingesta/facebook` · `POST /ia/extraer` · `GET /eventos/{id}.ics` · `POST /eventos/{id}/avisar` · `POST /eventos/{id}/chat-ia` · `POST /recomendaciones/enviar` · `POST /resenas/pedir/{event_id}` · `POST /moderacion/promociones/revisar` · `GET /ia/consumo`

Documentación interactiva: `/docs` en la URL de la API.

## Equipo
A1 · A2 · B1 · B2 · C1 (poned los nombres)

## Créditos
Mapas © colaboradores de OpenStreetMap. IA: Google Gemini. Mensajería: Telegram Bot API.
