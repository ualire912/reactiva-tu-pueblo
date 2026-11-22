"""
Webs de los ayuntamientos -> eventos (scraping dirigido con IA, ScrapeGraphAI + Gemini).

Las cinco webs municipales son de la Diputación de Almería (gestor "cmsdipro") y se leen
tal cual, sin saltarse ninguna protección. De cada una se miran la portada, Noticias y
Fiestas; la IA saca la lista de eventos con fecha y cada uno entra en el circuito de
siempre (agente_eventos.procesar_post: spam, duplicados y publicación o moderador).

Motor:
  1. ScrapeGraphAI (SmartScraperGraph) con Gemini por su API compatible con OpenAI.
     Se le pasa el HTML ya descargado: no hace falta abrir un navegador (Playwright).
  2. Si ScrapeGraphAI no está instalado o falla: la IA de la app (ia._llamar), igual.

Variables: GEMINI_API_KEY (la de siempre) y, opcional, GEMINI_MODEL.
Uso (desde la carpeta api):
    python scraper/webs_aytos.py --dry-run     -> muestra qué eventos encuentra, sin guardar
"""
from __future__ import annotations

import html as _html
import json
import os
import re
import sys
from datetime import datetime, timezone

import httpx

AQUI = os.path.dirname(os.path.abspath(__file__))
API_DIR = os.path.dirname(AQUI)
for _r in (AQUI, API_DIR):
    if _r not in sys.path:
        sys.path.insert(0, _r)

import ia  # noqa: E402  (carga api/.env)

UA = {"User-Agent": "Mozilla/5.0 (compatible; ReactivaTuPueblo/1.0; agenda de la comarca)",
      "Accept-Language": "es-ES,es;q=0.9"}
MAX_TEXTO = 60000  # caracteres de HTML limpio que se le pasan a la IA

PROMPT = """Eres un lector de webs de ayuntamientos de pueblos de Almería.
De esta página saca SOLO los eventos o actividades para el público con una fecha concreta
(fiestas, conciertos, rutas, talleres, ferias, mercadillos, deportes, cultura...).
NO son eventos: plenos, bandos, convocatorias de empleo, licitaciones, cortes de luz o agua,
avisos administrativos.
Devuelve JSON: {"eventos": [{"titulo": "...", "fecha": "AAAA-MM-DD", "hora": "HH:MM o null",
"lugar": "... o null", "precio": "... o null", "descripcion": "resumen corto", "enlace": "url o null"}]}
Si no hay ninguno: {"eventos": []}. No inventes nada que no esté en la página."""


def _limpiar_html(html: str) -> str:
    """Quita scripts, estilos y menús: menos texto para la IA (y menos coste)."""
    h = re.sub(r"<(script|style|noscript|svg|head)[\s\S]*?</\1>", " ", html, flags=re.I)
    h = re.sub(r"<!--[\s\S]*?-->", " ", h)
    h = re.sub(r"\s+", " ", h)
    return h[:MAX_TEXTO]


def _con_scrapegraph(html: str) -> list[dict] | None:
    """None si ScrapeGraphAI no está disponible o falla (se usa el plan B)."""
    clave = os.environ.get("GEMINI_API_KEY", "").strip()
    if not clave:
        return None
    try:
        from scrapegraphai.graphs import SmartScraperGraph
    except Exception:
        return None
    modelo = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash").strip() or "gemini-2.5-flash"
    config = {
        "llm": {"model": f"openai/{modelo}", "api_key": clave,
                "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
                "model_tokens": 200000, "temperature": 0},
        "verbose": False,
        "headless": True,
    }
    try:
        res = SmartScraperGraph(prompt=PROMPT, source=html, config=config).run()
    except Exception as e:
        print(f"[webs] ScrapeGraphAI falló ({type(e).__name__}): uso la IA de la app")
        return None
    if isinstance(res, dict):
        res = res.get("eventos", res.get("content", []))
    return res if isinstance(res, list) else []


def _con_ia_propia(html: str) -> list[dict]:
    texto = _html.unescape(re.sub(r"<[^>]+>", " ", html))
    texto = re.sub(r"\s+", " ", texto)[:20000]
    try:
        bruto = ia._llamar(f"{PROMPT}\n\nPÁGINA:\n{texto}", como_json=True)
        datos = ia._json(bruto) or {}
    except Exception as e:
        print(f"[webs] la IA no ha podido leer la página: {e!r}")
        return []
    return datos.get("eventos") or []


def eventos_de_pagina(url: str) -> list[dict]:
    """Descarga una página y devuelve los eventos que encuentra la IA."""
    try:
        r = httpx.get(url, headers=UA, timeout=40, follow_redirects=True)
        r.raise_for_status()
    except Exception as e:
        print(f"[webs] no se puede leer {url}: {type(e).__name__}")
        return []
    html = _limpiar_html(r.text)
    eventos = _con_scrapegraph(html)
    if eventos is None:
        eventos = _con_ia_propia(html)
    return [e for e in eventos if isinstance(e, dict) and e.get("titulo") and e.get("fecha")]


def _slug(t: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", ia._norm(t or ""))[:60].strip("-")


def como_post(ev: dict, pagina: str) -> dict:
    """Un evento de la web -> 'post' para agente_eventos.procesar_post (origen web)."""
    partes = [ev["titulo"], f"Fecha: {ev['fecha']}" + (f" a las {ev['hora']}" if ev.get("hora") else "")]
    if ev.get("lugar"):
        partes.append(f"Lugar: {ev['lugar']}")
    if ev.get("precio"):
        partes.append(f"Precio: {ev['precio']}")
    if ev.get("descripcion"):
        partes.append(str(ev["descripcion"]))
    enlace = ev.get("enlace") if str(ev.get("enlace") or "").startswith("http") else pagina
    # Dirección única por evento (una página puede anunciar varios)
    url = f"{enlace}#evento-{_slug(ev['titulo'])}-{ev['fecha']}"
    return {"text": "\n".join(partes), "url": url, "time": datetime.now(timezone.utc).isoformat(),
            "_imagen": None}


def futuros(eventos: list[dict]) -> list[dict]:
    hoy = datetime.now(ia.TZ).date().isoformat()
    return [e for e in eventos if str(e.get("fecha"))[:10] >= hoy]


def leer_webs(webs: list[dict], dry: bool, procesar_post) -> dict[str, int]:
    """Recorre las webs de fuentes_eventos.json ('webs_ayuntamientos')."""
    resumen: dict[str, int] = {}
    for web in webs:
        fuente = {"municipio": web["municipio"], "nombre": web.get("nombre") or f"Ayuntamiento de {web['municipio']}",
                  "oficial": True}
        vistos: set[str] = set()
        for pagina in web.get("paginas", []):
            for ev in futuros(eventos_de_pagina(pagina)):
                post = como_post(ev, pagina)
                if post["url"] in vistos:
                    continue
                vistos.add(post["url"])
                if dry:
                    res = "evento?"
                    print(f"  [web] {web['municipio']} · {ev['fecha']} · {ev['titulo'][:60]}")
                else:
                    try:
                        res = procesar_post(post, fuente, dry, origen="web")
                    except Exception as e:
                        res = f"error: {type(e).__name__}"
                resumen[res] = resumen.get(res, 0) + 1
    return resumen


if __name__ == "__main__":
    import agente_eventos
    with open(agente_eventos.FUENTES, encoding="utf-8") as f:
        webs = json.load(f).get("webs_ayuntamientos", [])
    print(leer_webs(webs, "--dry-run" in sys.argv, agente_eventos.procesar_post))
