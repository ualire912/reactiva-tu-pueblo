"""B1 · Servidor FastAPI de Reactiva tu pueblo.

Local:   uvicorn main:app --reload        (desde la carpeta api/)
Render:  uvicorn main:app --host 0.0.0.0 --port $PORT
"""
from dotenv import load_dotenv

load_dotenv()  # en local lee api/.env; en Render las variables ya existen (antes de importar db)

import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse

import tareas
from routers import acceso, eventos, moderacion, recomendaciones, resenas, traduccion

logging.basicConfig(level=logging.INFO)
# httpx registra cada URL que llama y la de Telegram LLEVA EL TOKEN del bot: no a los logs
for _ruidoso in ("httpx", "httpcore", "hpack"):
    logging.getLogger(_ruidoso).setLevel(logging.WARNING)
log = logging.getLogger("reactiva")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    tareas.arrancar()  # estados, reseñas y promociones automáticas cada 5 min
    yield


app = FastAPI(title="Reactiva tu pueblo API", lifespan=lifespan)

origenes = ["http://localhost:8100", "http://localhost:4200"]
if os.environ.get("FRONTEND_URL"):
    origenes.append(os.environ["FRONTEND_URL"].rstrip("/"))

app.add_middleware(
    CORSMiddleware,
    allow_origins=origenes,
    # Solo las previews de NUESTRO proyecto en Vercel (no cualquier web *.vercel.app)
    allow_origin_regex=r"https://reactiva-tu-pueblo[a-z0-9-]*\.vercel\.app",
    allow_credentials=False,  # usamos 'Authorization: Bearer', no cookies
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)


@app.api_route("/health", methods=["GET", "HEAD"])
def health():
    return {"ok": True}


_PRIVACIDAD = """<!doctype html><html lang="es"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Privacidad · Reactiva tu pueblo</title>
<style>body{font-family:system-ui,sans-serif;max-width:760px;margin:0 auto;padding:24px 16px;
line-height:1.6;color:#1c2b22;background:#fbfaf6}h1{color:#1f4d3a}h2{color:#1f4d3a;margin-top:28px}</style>
</head><body>
<h1>Política de privacidad · Reactiva tu pueblo</h1>
<p>Reactiva tu pueblo es un prototipo de la Hackathon UAL para el desarrollo del interior (Universidad de
Almería y Diputación de Almería). Reúne en una agenda común los planes de Lucainena de las Torres, Senés,
Tahal, Uleila del Campo y Velefique, y se usa desde la web, Telegram y WhatsApp.</p>
<h2>Qué datos guardamos</h2>
<ul>
<li>Tu nombre (el que muestra Telegram o WhatsApp) y el identificador de tu chat o tu número de WhatsApp,
para poder contestarte y avisarte.</li>
<li>Tus preferencias: pueblos y tipos de planes que te interesan, idioma y si quieres respuestas en audio.</li>
<li>Los planes a los que te apuntas, los que propones y las valoraciones que dejas.</li>
<li>Si das de alta una asociación: su nombre, su pueblo y la dirección de su página pública.</li>
</ul>
<h2>Para qué los usamos</h2>
<p>Solo para que la aplicación funcione: enseñarte planes, apuntarte, avisarte de cambios, recomendarte
planes que te pueden gustar y que los moderadores revisen las propuestas. No vendemos ni cedemos tus datos.
Los mensajes y notas de voz se procesan con servicios de inteligencia artificial únicamente para entender
lo que pides.</p>
<h2>Publicaciones de Facebook</h2>
<p>Leemos publicaciones públicas de páginas de ayuntamientos y asociaciones de estos pueblos para detectar
planes. De las publicaciones de particulares que no anuncian un plan no se guarda el texto.</p>
<h2 id="borrar-datos">Cómo borrar tus datos</h2>
<p>Puedes borrar tu cuenta desde Ajustes en la web, o escribir al bot «hablar con una persona» y pedir
que se borren tus datos. Se eliminan tu perfil, tus inscripciones y tus preferencias.</p>
<h2>Dónde se guardan</h2>
<p>En una base de datos Supabase con acceso restringido por roles. Las claves de los servicios no están en
el código público.</p>
</body></html>"""


@app.get("/privacidad", response_class=HTMLResponse, include_in_schema=False)
def privacidad():
    """Política de privacidad (la pide Meta para publicar la app de WhatsApp)."""
    return _PRIVACIDAD


# Mis routers (B1)
app.include_router(eventos.router)
app.include_router(recomendaciones.router)
app.include_router(resenas.router)
app.include_router(moderacion.router)
app.include_router(acceso.router)
app.include_router(traduccion.router)

# Routers de B2 y C1: si aún no están listos, el servidor arranca igual
for modulo in ("bot", "ingesta", "whatsapp"):
    try:
        app.include_router(__import__(modulo).router)
        log.info("Router %s incluido", modulo)
    except Exception as e:
        log.warning("Router %s no disponible todavía: %s", modulo, e)
