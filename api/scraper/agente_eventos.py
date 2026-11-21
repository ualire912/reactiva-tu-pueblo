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
from html.parser import HTMLParser
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


def cargar_fuentes(ruta: str = FUENTES) -> tuple[list[dict], list[dict]]:
    """-> (páginas de Facebook con URL real, artículos)."""
    with open(ruta, encoding="utf-8") as f:
        datos = json.load(f)
    paginas = [p for p in datos.get("facebook", []) if p.get("url") and "RELLENAR" not in p["url"]]
    return paginas, datos.get("articulos", [])


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


# ── 1b. Leer artículos de prensa / agendas públicas ──────────
class _LectorHTML(HTMLParser):
    """Saca de una noticia: título, fecha de publicación, imagen y el texto de los párrafos."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.meta: dict[str, str] = {}
        self.titulo = ""
        self.parrafos: list[str] = []
        self._en: list[str] = []
        self._buf: list[str] = []
        self._ignorar = 0

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "meta":
            clave = (a.get("property") or a.get("name") or a.get("itemprop") or "").lower()
            if clave and a.get("content"):
                self.meta.setdefault(clave, a["content"])
        elif tag in ("script", "style", "nav", "footer", "aside", "form"):
            self._ignorar += 1
        elif tag == "time" and a.get("datetime"):
            self.meta.setdefault("time", a["datetime"])
        elif tag in ("p", "li", "h1", "h2", "h3", "title"):
            self._en.append(tag)
            self._buf = []

    def handle_endtag(self, tag):
        if tag in ("script", "style", "nav", "footer", "aside", "form") and self._ignorar:
            self._ignorar -= 1
        elif self._en and tag == self._en[-1]:
            self._en.pop()
            texto = re.sub(r"\s+", " ", "".join(self._buf)).strip()
            if tag == "title":
                self.titulo = texto
            elif len(texto) > 25:
                self.parrafos.append(texto)

    def handle_data(self, data):
        if self._en and not self._ignorar:
            self._buf.append(data)


def leer_articulo(url: str) -> dict | None:
    """Descarga la noticia como un navegador normal. Si la web no la sirve (bloqueo,
    captcha, 403...), se respeta y se salta: no se intenta saltar ninguna protección."""
    try:
        r = httpx.get(url, timeout=30, follow_redirects=True,
                      headers={"User-Agent": "Mozilla/5.0 (compatible; ReactivaTuPuebloBot/1.0; "
                                             "agenda de eventos de la Mancomunidad)",
                               "Accept-Language": "es-ES,es;q=0.9"})
    except httpx.HTTPError as e:
        print(f"   (no se pudo leer {url}: {type(e).__name__})")
        return None
    if r.status_code != 200 or "captcha" in r.text[:20000].lower():
        print(f"   (la web no lo sirve: HTTP {r.status_code}) {url}")
        return None
    lector = _LectorHTML()
    lector.feed(r.text)
    m = lector.meta
    titulo = m.get("og:title") or lector.titulo
    vistos, cuerpo = set(), []
    for p in lector.parrafos:  # sin repetir párrafos (menús, pies...)
        if p not in vistos:
            vistos.add(p)
            cuerpo.append(p)
    texto = "\n".join([titulo, m.get("og:description", ""), *cuerpo]).strip()[:6000]
    fecha = (m.get("article:published_time") or m.get("datepublished") or m.get("time") or "")
    return {"url": url, "text": texto, "time": fecha,
            "_imagen": m.get("og:image") or m.get("twitter:image")}


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


def corregir_anio(ext: dict, publicado: str | None, texto: str) -> bool:
    """La IA a veces pone 'la próxima vez' contando desde HOY en un post antiguo
    (noticia de mayo de 2026 -> 2027). Si la fecha cae más de 10 meses después de la
    publicación y el texto no dice ese año, se vuelve al año de la publicación."""
    try:
        pub = datetime.fromisoformat(str(publicado).replace("Z", "+00:00"))
        ini = datetime.fromisoformat(str(ext.get("starts_at")))
    except (TypeError, ValueError):
        return False
    if pub.tzinfo is None:
        pub = pub.replace(tzinfo=ia.TZ)
    if ini.tzinfo is None:
        ini = ini.replace(tzinfo=ia.TZ)
    if (ini - pub).days <= 300 or str(ini.year) in (texto or ""):
        return False
    try:
        nuevo = ini.replace(year=pub.year)
        if nuevo < pub - timedelta(days=30):  # p. ej. publicado en diciembre para enero
            nuevo = nuevo.replace(year=pub.year + 1)
    except ValueError:  # 29 de febrero
        return False
    salto = nuevo - ini
    ext["starts_at"] = nuevo.isoformat()
    for campo in ("ends_at", "inscripcion_hasta"):
        try:
            if ext.get(campo):
                ext[campo] = (datetime.fromisoformat(str(ext[campo])) + salto).isoformat()
        except ValueError:
            ext[campo] = None
    return True


# ── 3. Un post -> ingesta -> evento ──────────────────────────
def procesar_post(post: dict, fuente: dict, dry: bool, origen: str = "facebook") -> str:
    """Un post de Facebook o un artículo (origen='web') -> ingesta -> evento."""
    texto = (post.get("text") or "").strip()
    url = post.get("url") or post.get("postUrl")
    img = post.get("_imagen") or imagen_del_post(post)
    pagina = fuente.get("url") if origen == "facebook" else None
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
    donde = "Página de Facebook" if origen == "facebook" else "Noticia/agenda publicada en"
    nota = ""
    if origen == "web":  # se carga el historial real: un evento ya celebrado también cuenta
        nota = ("(Es una noticia sobre un evento concreto del pueblo. Aunque ya se haya celebrado, "
                "si describe un evento con fecha es un evento real: es_evento=true. El año es el "
                "de la fecha de publicación salvo que el texto diga otro.)\n")
    texto_ia = f"{cabecera}{nota}{donde}: {fuente['nombre']} ({fuente['municipio']})\n\n{texto}"

    if not parece_evento(texto, bool(img), pagina_de_pueblo=True):
        if not dry:
            fila, nueva = ingesta.registrar_post(url, pagina, texto_ia, img, origen=origen)
            if nueva:
                ingesta._actualizar_ingesta(fila["id"], estado="descartada", spam_score=0,
                                            spam_reason="no parece un evento (reglas)")
        return "descartado_reglas"

    if dry:  # solo se mira qué entendería la IA (del texto; el cartel lo lee al guardar)
        ext = ia.extraer_evento(texto_ia)
        corregir_anio(ext, post.get("time"), texto)
        print(f"     IA -> es_evento={ext.get('es_evento')} · {ext.get('title')} · "
              f"{ext.get('starts_at')} · {ext.get('municipio')} · conf={ext.get('confidence')} "
              f"· falta={ext.get('missing')}")
        return "evento?" if ext.get("es_evento") else "no_evento"

    fila, nueva = ingesta.registrar_post(url, pagina, texto_ia, img, origen=origen)
    if not nueva and fila.get("estado") != "error":
        return "ya_visto"
    ingesta.procesar_ingesta(fila["id"])
    ing = sb.table("ingestas").select("*").eq("id", fila["id"]).execute().data[0]
    ext0 = ing.get("extracted") or {}
    if (origen == "web" and ing["estado"] == "descartada" and ext0.get("title")
            and ext0.get("starts_at") and (ing.get("spam_score") or 0) < 0.5):
        # Noticia revisada de un evento ya celebrado: la IA la toma por "noticia", pero
        # tiene título y fecha reales -> se recupera (el historial también cuenta)
        ingesta._actualizar_ingesta(fila["id"], estado="extraida",
                                    spam_reason="evento real (noticia revisada)")
        ing["estado"] = "extraida"
    if ing["estado"] not in ("extraida", "esperando_confirmacion"):
        return ing["estado"]  # spam / descartada / error

    ext = ing.get("extracted") or {}
    muni_id = municipio_id(ext.get("municipio") or "") or municipio_id(fuente["municipio"])
    cambiado = corregir_anio(ext, post.get("time"), texto)
    if not ext.get("municipio"):  # el post es de una página del pueblo: ese es el pueblo
        ext["municipio"] = fuente["municipio"]
        cambiado = True
    if cambiado:
        sb.table("ingestas").update({"extracted": ext}).eq("id", fila["id"]).execute()
    repe = evento_parecido(ext, muni_id)
    if repe:
        ingesta._actualizar_ingesta(fila["id"], estado="confirmada", event_id=repe["id"])
        return "duplicado"

    segura = (float(ext.get("confidence") or 0) >= UMBRAL_CONFIANZA and not ext.get("dudas")
              and "hora" not in (ext.get("missing") or []))
    # Fuente oficial/verificada y la IA segura -> publicado. Si duda -> moderador.
    publicar = bool(fuente.get("oficial") and segura and not fuente.get("dudoso"))
    ev = ingesta.crear_evento_desde_ingesta(fila["id"], publicar=publicar)
    if "error" in ev:
        return f"falta: {','.join(ev.get('missing') or []) or ev['error'][:40]}"
    return f"evento_{ev.get('status')}"


def ejecutar(dry: bool = False, semanas: int = 6, archivo: str | None = None,
             fuentes: str = FUENTES, solo: str | None = None) -> dict[str, int]:
    """Una pasada completa. La usa también la tarea programada de Render (tareas.py).
    solo='facebook' | 'articulos' | None (las dos)."""
    paginas, articulos = cargar_fuentes(fuentes)
    resumen: dict[str, int] = {}

    def anotar(res: str, municipio: str, fecha: str, texto: str) -> None:
        resumen[res] = resumen.get(res, 0) + 1
        print(f"  [{res}] {municipio} · {fecha[:10]} · {texto[:70]!r}")

    # ── Facebook (Apify)
    if solo != "articulos" and (archivo or (paginas and os.getenv("APIFY_TOKEN"))):
        if not dry:
            for p in paginas:
                asegurar_entidad(p)
        if archivo:
            with open(archivo, encoding="utf-8") as f:
                posts = json.load(f)
        else:
            posts = leer_facebook(paginas, semanas)
            try:  # copia para repetir con --archivo sin gastar crédito
                os.makedirs(os.path.join(API_DIR, "pruebas"), exist_ok=True)
                copia = os.path.join(API_DIR, "pruebas", f"apify_{datetime.now():%Y%m%d_%H%M}.json")
                with open(copia, "w", encoding="utf-8") as f:
                    json.dump(posts, f, ensure_ascii=False, indent=1)
            except OSError:
                pass
        print(f"Facebook: {len(posts)} posts{' (SIMULACIÓN)' if dry else ''}")
        for post in posts:
            fuente = fuente_del_post(post, paginas)
            if not fuente:
                continue
            try:
                res = procesar_post(post, fuente, dry)
            except Exception as e:  # un post raro no para a los demás
                res = f"error: {type(e).__name__}"
            anotar(res, fuente["municipio"], post.get("time") or "", post.get("text") or "(solo cartel)")
    elif solo != "articulos":
        print("Facebook: sin APIFY_TOKEN o sin páginas con URL real -> se salta")

    # ── Artículos de prensa / agendas públicas
    if solo != "facebook" and articulos:
        print(f"Artículos: {len(articulos)}{' (SIMULACIÓN)' if dry else ''}")
        for art in articulos:
            if not dry and sb.table("ingestas").select("id").eq("source_url", art["url"]).execute().data:
                anotar("ya_visto", art["municipio"], "", art["url"])
                continue
            leido = leer_articulo(art["url"])
            if not leido and art.get("resumen"):
                # La web no deja leerla a programas: se usan los datos de la noticia que se
                # recogieron a mano (con el enlace original como fuente)
                leido = {"url": art["url"], "time": art.get("publicado"), "_imagen": None,
                         "text": "Resumen de la noticia: " + art["resumen"]}
            if not leido:
                anotar("web_no_disponible", art["municipio"], "", art["url"])
                continue
            dominio = re.sub(r"^www\.", "", httpx.URL(art["url"]).host)
            fuente = {"municipio": art["municipio"], "nombre": dominio,
                      "oficial": True, "dudoso": bool(art.get("dudoso"))}
            try:
                res = procesar_post(leido, fuente, dry, origen="web")
            except Exception as e:
                res = f"error: {type(e).__name__}"
            anotar(res, art["municipio"], leido.get("time") or "", leido["text"].split("\n")[0])

    print("\nResumen:", ", ".join(f"{k}: {v}" for k, v in sorted(resumen.items())) or "nada")
    return resumen


def main():
    ap = argparse.ArgumentParser(description="Facebook y prensa -> eventos reales")
    ap.add_argument("--dry-run", action="store_true", help="no escribe nada en la BD")
    ap.add_argument("--semanas", type=int, default=6, help="antigüedad máxima de los posts")
    ap.add_argument("--archivo", help="JSON con posts ya descargados de Apify")
    ap.add_argument("--fuentes", default=FUENTES, help="otro archivo de fuentes (para pruebas)")
    ap.add_argument("--solo", choices=["facebook", "articulos"], help="solo una de las fuentes")
    a = ap.parse_args()
    ejecutar(a.dry_run, a.semanas, a.archivo, a.fuentes, a.solo)


if __name__ == "__main__":
    main()
