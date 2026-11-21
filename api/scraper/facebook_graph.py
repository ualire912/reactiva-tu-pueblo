"""
Lector de Facebook con la API OFICIAL de Meta (Graph API).

Lee los posts de una página de Facebook que administráis vosotros y manda cada post
nuevo que parezca un evento a POST {API_URL}/ingesta/facebook (el mismo flujo que el simulador:
ficha IA -> spam / no-evento -> confirmación por Telegram -> evento).

Variables en api/.env:
    FB_PAGE_ID=1234567890                 (id numérico de la página)
    FB_PAGE_TOKEN=EAAG...                 (token de PÁGINA, secreto: nunca a GitHub)
    API_URL=http://localhost:8000         (o la URL de Render)
    FB_GRAPH_VERSION=v25.0                (opcional)

Uso (desde la carpeta api):
    python scraper/facebook_graph.py info       -> comprueba el token y muestra la página
    python scraper/facebook_graph.py            -> lee los últimos posts UNA vez
    python scraper/facebook_graph.py vigilar    -> repite cada 60 s (para la demo)
"""
import os
import re
import sys
import time
import unicodedata

import httpx
from dotenv import load_dotenv

sys.stdout.reconfigure(encoding="utf-8")
AQUI = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(AQUI, "..", ".env"))

PAGE_ID = os.getenv("FB_PAGE_ID", "").strip()
TOKEN = os.getenv("FB_PAGE_TOKEN", "").strip()
API_URL = os.getenv("API_URL", "http://localhost:8000").rstrip("/")
VERSION = os.getenv("FB_GRAPH_VERSION", "v25.0")
GRAPH = f"https://graph.facebook.com/{VERSION}"
CADA_S = 60

# Filtro previo (gratis): solo se envían posts con pinta de evento
PUEBLOS = ["lucainena", "velefique", "senes", "uleila", "tahal"]
PALABRAS_EVENTO = ["ruta", "fiesta", "taller", "concierto", "mercadillo", "feria", "romeria",
                   "torneo", "partido", "carrera", "excursion", "charla", "cine", "teatro",
                   "jornada", "convivencia", "verbena", "migas", "degustacion", "inscripcion",
                   "apuntaos", "os esperamos", "plazas", "entrada", "gratis", "voluntari",
                   "merienda", "comida", "cena", "baile", "reunion", "escuela", "curso", "clase",
                   "exposicion", "presentacion", "actividad", "misa", "procesion", "belen",
                   "cabalgata", "navidad", "carnaval", "semana cultural", "homenaje", "encuentro",
                   "lugar:", "lugar :", "hora:", "hora :", "salon", "plaza", "pabellon"]
RE_FECHA = re.compile(r"\b(\d{1,2}[/-]\d{1,2}|\d{1,2} de [a-z]+|lunes|martes|miercoles|jueves|"
                      r"viernes|sabado|domingo|manana|hoy|\d{1,2}[:h]\d{2})\b")


def norm(t):
    t = unicodedata.normalize("NFD", t or "")
    return "".join(c for c in t if unicodedata.category(c) != "Mn").lower()


def parece_evento(texto: str, tiene_foto: bool, pagina_de_pueblo: bool = True) -> bool:
    """Filtro previo gratis. pagina_de_pueblo=True: la página es de un ayuntamiento/asociación
    de la zona, así que no hace falta que el post nombre el pueblo."""
    n = norm(texto)
    if tiene_foto and len(n) < 60:  # cartel con poco texto: que lo mire la IA
        return True
    palabra = any(p in n for p in PALABRAS_EVENTO)
    fecha = bool(RE_FECHA.search(n))
    pueblo = pagina_de_pueblo or any(p in n for p in PUEBLOS)
    return (palabra and (fecha or pueblo)) or (fecha and pueblo)


def graph(ruta: str, **params):
    r = httpx.get(f"{GRAPH}/{ruta}", params={**params, "access_token": TOKEN}, timeout=20)
    datos = r.json()
    if "error" in datos:
        e = datos["error"]
        raise RuntimeError(f"Graph API: {e.get('message')} (código {e.get('code')})")
    return datos


def info():
    p = graph(PAGE_ID, fields="id,name,link")
    print(f"✓ Token válido. Página: {p['name']} (id {p['id']})")
    print(f"  link: {p.get('link')}")
    print("  -> Esta 'link' tiene que estar en entidades.facebook_page_url (díselo a B1).")
    return p


def leer_una_vez(page_url: str):
    datos = graph(f"{PAGE_ID}/posts", fields="id,message,created_time,permalink_url,full_picture",
                  limit=10)
    enviados = 0
    for post in datos.get("data", []):
        texto = post.get("message") or ""
        foto = post.get("full_picture")
        url = post.get("permalink_url") or f"https://www.facebook.com/{post['id']}"
        if not texto and not foto:
            continue
        if not parece_evento(texto, bool(foto)):
            print(f"  · saltado (no parece evento): {texto[:60]!r}")
            continue
        r = httpx.post(f"{API_URL}/ingesta/facebook", timeout=30, json={
            "source_url": url, "page_url": page_url, "text": texto, "image_url": foto})
        if r.status_code == 200:
            res = r.json()
            print(f"  → {res['estado']:<22} {texto[:60]!r}")
            enviados += 1
        else:
            print(f"  ✗ el servidor respondió {r.status_code}: {r.text[:150]}")
    return enviados


def main():
    if not PAGE_ID or not TOKEN:
        sys.exit("Faltan FB_PAGE_ID y FB_PAGE_TOKEN en api/.env")
    modo = sys.argv[1] if len(sys.argv) > 1 else ""
    pagina = info()
    if modo == "info":
        return
    page_url = pagina.get("link") or f"https://www.facebook.com/{PAGE_ID}"
    print(f"\nEnviando a {API_URL}/ingesta/facebook\n")
    while True:
        try:
            leer_una_vez(page_url)
        except Exception as e:
            print("  ✗", e)
        if modo != "vigilar":
            break
        time.sleep(CADA_S)


if __name__ == "__main__":
    main()
