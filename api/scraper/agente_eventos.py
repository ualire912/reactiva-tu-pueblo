"""
Agente: Facebook -> eventos REALES (Senés y Lucainena de las Torres)

Basado en el agente de Apify de un compañero, pero metido en el circuito que ya tiene la app,
para no repetir trabajo y que todo pase por los mismos filtros:

  1. Apify lee los posts de las páginas de scraper/fuentes_eventos.json.
  2. Se descartan los posts ya vistos (source_url único en `ingestas`).
  3. Se guarda la ingesta ANTES de llamar a la IA (si algo falla, no se pierde).
  4. Filtro barato con reglas (¿parece anunciar algo con fecha?) -> si no, descartada.
  5. ingesta.procesar_ingesta: spam + IA (freellmpool -> Gemini; el cartel lo lee Gemini).
  6. Se crea el evento:
       - fuente oficial (ayuntamiento/organización) y la IA está segura -> publicado
       - si la IA duda o la fuente no es oficial                         -> al moderador
     Si ese evento ya existe (mismo pueblo, día y título parecido) no se duplica.

Variables en api/.env:
    APIFY_TOKEN=...        (apify.com -> Settings -> API & Integrations; secreto: nunca a GitHub)
    SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY, GEMINI_API_KEY...  (las de siempre)

Uso (desde la carpeta api):
    python scraper/agente_eventos.py --dry-run          -> lee y muestra qué haría, sin escribir
    python scraper/agente_eventos.py                    -> lee y guarda en la BD
    python scraper/agente_eventos.py --semanas 8        -> posts de las últimas 8 semanas (def. 6)
    python scraper/agente_eventos.py --archivo posts.json [--dry-run]
                                                        -> usa posts ya descargados de Apify
                                                           (para probar sin gastar crédito)
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import unicodedata
from datetime import datetime, timedelta, timezone

import httpx

sys.stdout.reconfigure(encoding="utf-8")
AQUI = os.path.dirname(os.path.abspath(__file__))
API_DIR = os.path.dirname(AQUI)
for _r in (AQUI, API_DIR):
    if _r not in sys.path:
        sys.path.insert(0, _r)

import ia  # noqa: E402  (carga api/.env)
import ingesta  # noqa: E402
from db import sb  # noqa: E402
from facebook_graph import parece_evento  # noqa: E402  (mismo filtro previo, gratis)

FUENTES = os.path.join(AQUI, "fuentes_eventos.json")
ACTOR = "apify~facebook-posts-scraper"
POSTS_POR_PAGINA = 30
UMBRAL_CONFIANZA = 0.6  # por debajo, aunque sea oficial, lo revisa el moderador


# ── Utilidades ────────────────────────────────────────────────
def _norm(t: str) -> str:
    t = unicodedata.normalize("NFD", t or "")
    return "".join(c for c in t if unicodedata.category(c) != "Mn").lower()


def _url_limpia(u: str) -> str:
    return (u or "").split("?")[0].rstrip("/").lower()


def cargar_fuentes(ruta: str = FUENTES) -> list[dict]:
    with open(ruta, encoding="utf-8") as f:
        datos = json.load(f)
    paginas = [p for p in datos.get("facebook", []) if p.get("url") and "RELLENAR" not in p["url"]]
    if not paginas:
        sys.exit(f"Rellena las URL de Facebook en {ruta}")
    return paginas


def municipio_id(nombre: str) -> int | None:
    for m in sb.table("municipios").select("id,nombre").execute().data or []:
        if _norm(m["nombre"]) == _norm(nombre):
            return m["id"]
    return None


def asegurar_entidad(fuente: dict) -> dict:
    """La página de Facebook como 'entidad' organizadora. Las oficiales publican solas
    (auto_approve) -> así 'las organizaciones son publicadoras'."""
    url = fuente["url"].rstrip("/")
    filas = sb.table("entidades").select("*").eq("facebook_page_url", url).execute().data
    if filas:
        return filas[0]
    fila = {"nombre": fuente["nombre"], "municipio_id": municipio_id(fuente["municipio"]),
            "facebook_page_url": url, "auto_approve": bool(fuente.get("oficial")),
            "redes_sociales": {"facebook": url}}
    return sb.table("entidades").insert(fila).execute().data[0]


# ── 1. Leer Facebook con Apify ────────────────────────────────
def leer_facebook(paginas: list[dict], semanas: int) -> list[dict]:
    token = os.getenv("APIFY_TOKEN", "").strip()
    if not token:
        sys.exit("Falta APIFY_TOKEN en api/.env (apify.com -> Settings -> API & Integrations)")
    entrada = {
        "startUrls": [{"url": p["url"]} for p in paginas],
        "resultsLimit": POSTS_POR_PAGINA,
        "onlyPostsNewerThan": f"{semanas} weeks",
    }
    print(f"Apify: leyendo {len(paginas)} páginas (puede tardar 1-3 minutos)...")
    r = httpx.post(f"https://api.apify.com/v2/acts/{ACTOR}/run-sync-get-dataset-items",
                   headers={"Authorization": f"Bearer {token}"}, json=entrada, timeout=360)
    if r.status_code >= 400:
        sys.exit(f"Apify respondió {r.status_code}: {r.text[:300]}")
    return r.json()


def imagen_del_post(post: dict) -> str | None:
    """Busca el cartel en 'media' (la estructura de Apify varía según el post)."""
    for m in post.get("media") or []:
        if not isinstance(m, dict):
            continue
        for k in ("photo_image", "image"):
            v = m.get(k)
            if isinstance(v, dict) and v.get("uri"):
                return v["uri"]
        for k in ("thumbnail", "url"):
            if isinstance(m.get(k), str) and m[k].startswith("http") and "video" not in m[k]:
                return m[k]
    return None


def fuente_del_post(post: dict, paginas: list[dict]) -> dict | None:
    origen = _url_limpia(post.get("facebookUrl") or post.get("inputUrl") or "")
    for p in paginas:
        if _url_limpia(p["url"]) == origen:
            return p
    return paginas[0] if len(paginas) == 1 else None


# ── 2. ¿Ya existe ese evento? (mismo pueblo, fecha cercana, título parecido) ──
def evento_parecido(ext: dict, muni_id: int | None) -> dict | None:
    """Un recordatorio ("¡el sábado es el taller!") no debe crear otro evento. Mismo pueblo,
    ±4 días y título muy parecido -> es el mismo (si es el mismo día, basta con parecerse)."""
    try:
        inicio = datetime.fromisoformat(str(ext.get("starts_at")))
    except (TypeError, ValueError):
        return None
    if inicio.tzinfo is None:
        inicio = inicio.replace(tzinfo=ia.TZ)
    q = (sb.table("eventos").select("id,title,starts_at,status")
         .gte("starts_at", (inicio - timedelta(days=4)).isoformat())
         .lte("starts_at", (inicio + timedelta(days=4)).isoformat()))
    if muni_id:
        q = q.eq("municipio_id", muni_id)
    palabras = {w for w in re.findall(r"[a-z0-9]{4,}", _norm(ext.get("title") or ""))}
    mejor, mejor_puntos = None, 0.0
    for e in q.execute().data or []:
        otras = {w for w in re.findall(r"[a-z0-9]{4,}", _norm(e["title"]))}
        if not (palabras and otras):
            continue
        parecido = len(palabras & otras) / min(len(palabras), len(otras))
        mismo_dia = str(e["starts_at"])[:10] == inicio.astimezone(timezone.utc).isoformat()[:10]
        if parecido >= (0.5 if mismo_dia else 0.8) and parecido > mejor_puntos:
            mejor, mejor_puntos = e, parecido
    return mejor


# ── 3. Un post -> ingesta -> evento ──────────────────────────
def procesar_post(post: dict, fuente: dict, dry: bool) -> str:
    texto = (post.get("text") or "").strip()
    url = post.get("url") or post.get("postUrl")
    img = imagen_del_post(post)
    if not url or (not texto and not img):
        return "sin_datos"
    # La fecha de publicación, con el formato exacto que espera el prompt de la IA
    # ("[Publicado el DD/MM/AAAA]"), para que "este sábado" o "mañana" se cuenten desde ahí
    cabecera = ""
    try:
        pub = datetime.fromisoformat((post.get("time") or "").replace("Z", "+00:00")).astimezone(ia.TZ)
        dias = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
        cabecera = f"[Publicado el {pub:%d/%m/%Y}] (un {dias[pub.weekday()]})\n"
    except ValueError:
        pass
    texto_ia = (f"{cabecera}Página de Facebook: {fuente['nombre']} ({fuente['municipio']})\n\n{texto}")

    if not parece_evento(texto, bool(img), pagina_de_pueblo=True):
        if not dry:
            fila, nueva = ingesta.registrar_post(url, fuente["url"], texto_ia, img)
            if nueva:
                ingesta._actualizar_ingesta(fila["id"], estado="descartada", spam_score=0,
                                            spam_reason="no parece un evento (reglas)")
        return "descartado_reglas"

    if dry:  # solo se mira qué entendería la IA (del texto; el cartel lo lee al guardar)
        ext = ia.extraer_evento(texto_ia)
        print(f"     IA -> es_evento={ext.get('es_evento')} · {ext.get('title')} · "
              f"{ext.get('starts_at')} · {ext.get('municipio')} · conf={ext.get('confidence')} "
              f"· falta={ext.get('missing')}")
        return "evento?" if ext.get("es_evento") else "no_evento"

    fila, nueva = ingesta.registrar_post(url, fuente["url"], texto_ia, img)
    if not nueva and fila.get("estado") != "error":
        return "ya_visto"
    ingesta.procesar_ingesta(fila["id"])
    ing = sb.table("ingestas").select("*").eq("id", fila["id"]).execute().data[0]
    if ing["estado"] not in ("extraida", "esperando_confirmacion"):
        return ing["estado"]  # spam / descartada / error

    ext = ing.get("extracted") or {}
    muni_id = municipio_id(ext.get("municipio") or "") or municipio_id(fuente["municipio"])
    if not ext.get("municipio"):  # el post es de una página del pueblo: ese es el pueblo
        ext["municipio"] = fuente["municipio"]
        sb.table("ingestas").update({"extracted": ext}).eq("id", fila["id"]).execute()
    repe = evento_parecido(ext, muni_id)
    if repe:
        ingesta._actualizar_ingesta(fila["id"], estado="confirmada", event_id=repe["id"])
        return "duplicado"

    segura = float(ext.get("confidence") or 0) >= UMBRAL_CONFIANZA
    publicar = None if (fuente.get("oficial") and segura) else False
    ev = ingesta.crear_evento_desde_ingesta(fila["id"], publicar=publicar)
    if "error" in ev:
        return f"falta: {','.join(ev.get('missing') or []) or ev['error'][:40]}"
    return f"evento_{ev.get('status')}"


def main():
    ap = argparse.ArgumentParser(description="Facebook -> eventos reales")
    ap.add_argument("--dry-run", action="store_true", help="no escribe nada en la BD")
    ap.add_argument("--semanas", type=int, default=6, help="antigüedad máxima de los posts")
    ap.add_argument("--archivo", help="JSON con posts ya descargados de Apify")
    ap.add_argument("--fuentes", default=FUENTES, help="otro archivo de páginas (para pruebas)")
    a = ap.parse_args()

    paginas = cargar_fuentes(a.fuentes)
    if not a.dry_run:
        for p in paginas:
            asegurar_entidad(p)
    if a.archivo:
        with open(a.archivo, encoding="utf-8") as f:
            posts = json.load(f)
    else:
        posts = leer_facebook(paginas, a.semanas)
        os.makedirs(os.path.join(API_DIR, "pruebas"), exist_ok=True)
        copia = os.path.join(API_DIR, "pruebas", f"apify_{datetime.now():%Y%m%d_%H%M}.json")
        with open(copia, "w", encoding="utf-8") as f:  # copia para repetir con --archivo
            json.dump(posts, f, ensure_ascii=False, indent=1)
        print(f"Copia de los posts en {copia}")

    print(f"{len(posts)} posts{' (SIMULACIÓN: no se escribe nada)' if a.dry_run else ''}")
    resumen: dict[str, int] = {}
    for post in posts:
        fuente = fuente_del_post(post, paginas)
        if not fuente:
            continue
        try:
            res = procesar_post(post, fuente, a.dry_run)
        except Exception as e:  # un post raro no para a los demás
            res = f"error: {type(e).__name__}"
        resumen[res] = resumen.get(res, 0) + 1
        print(f"  [{res}] {fuente['municipio']} · {(post.get('time') or '')[:10]} · "
              f"{(post.get('text') or '(solo cartel)')[:70]!r}")
    print("\nResumen:", ", ".join(f"{k}: {v}" for k, v in sorted(resumen.items())))


if __name__ == "__main__":
    main()
