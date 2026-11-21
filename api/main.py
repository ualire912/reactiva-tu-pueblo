"""B1 · Servidor FastAPI de Reactiva tu pueblo.

Local:   uvicorn main:app --reload        (desde la carpeta api/)
Render:  uvicorn main:app --host 0.0.0.0 --port $PORT
"""
from dotenv import load_dotenv

load_dotenv()  # en local lee api/.env; en Render las variables ya existen (antes de importar db)

import logging
import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from routers import eventos, moderacion, recomendaciones, resenas

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("reactiva")

app = FastAPI(title="Reactiva tu pueblo API")

origenes = ["http://localhost:8100", "http://localhost:4200"]
if os.environ.get("FRONTEND_URL"):
    origenes.append(os.environ["FRONTEND_URL"].rstrip("/"))

app.add_middleware(
    CORSMiddleware,
    allow_origins=origenes,
    allow_origin_regex=r"https://.*\.vercel\.app",  # previews de Vercel
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.api_route("/health", methods=["GET", "HEAD"])
def health():
    return {"ok": True}


# Mis routers (B1)
app.include_router(eventos.router)
app.include_router(recomendaciones.router)
app.include_router(resenas.router)
app.include_router(moderacion.router)

# Routers de B2 y C1: si aún no están listos, el servidor arranca igual
for modulo in ("bot", "ingesta"):
    try:
        app.include_router(__import__(modulo).router)
        log.info("Router %s incluido", modulo)
    except Exception as e:
        log.warning("Router %s no disponible todavía: %s", modulo, e)
