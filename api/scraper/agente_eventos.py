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


def paginas_descubiertas() -> list[dict]:
    """Páginas de Facebook de las organizaciones que la búsqueda ya encontró (o que el
    moderador dio de alta). Se leen una vez al día."""
    municipios = {m["id"]: m["nombre"] for m in sb.table("municipios").select("id,nombre").execute().data or []}
    paginas = []
    for e in sb.table("entidades").select("*").not_.is_("facebook_page_url", "null").execute().data or []:
        url = (e.get("facebook_page_url") or "").strip()
        if not url.startswith("http") or "RELLENAR" in url:
            continue
        paginas.append({"municipio": municipios.get(e.get("municipio_id")) or "",
                        "nombre": e.get("nombre") or "Organización", "url": url,
                        "oficial": entidad_de_confianza(e)})
    return paginas


def entidad_de_confianza(entidad: dict | None) -> bool:
    """Una organización publica sola si el moderador la marcó (auto_approve) o si su
    responsable es Publicador/a o moderador/a."""
    if not entidad:
        return False
    if entidad.get("auto_approve"):
        return True
    dueno = entidad.get("owner_user_id")
    if not dueno:
        return False
    filas = sb.table("profiles").select("rol").eq("id", dueno).limit(1).execute().data
    return bool(filas and filas[0].get("rol") in ("publicador", "moderador"))


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
def leer_facebook(paginas: list[dict], semanas: int, por_pagina: int = POSTS_POR_PAGINA) -> list[dict]:
    token = os.getenv("APIFY_TOKEN", "").strip()
    if not token or not paginas:
        return []
    entrada = {
        "startUrls": [{"url": p["url"]} for p in paginas],
        "resultsLimit": por_pagina,
        "onlyPostsNewerThan": f"{semanas} weeks",
    }
    print(f"Apify: leyendo {len(paginas)} páginas (puede tardar 1-3 minutos)...")
    r = httpx.post(f"https://api.apify.com/v2/acts/{ACTOR}/run-sync-get-dataset-items",
                   headers={"Authorization": f"Bearer {token}"}, json=entrada, timeout=360)
    if r.status_code >= 400:
        print(f"Apify respondió {r.status_code}: {r.text[:300]}")
        return []
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


# ── 1a. Buscar publicaciones en Facebook por palabras clave ────
# Organizaciones: ayuntamientos, asociaciones, clubes... (lo demás se trata como una persona)
_ORGANIZACION = re.compile(r"\b(ayuntamiento|ayto|asociacion|club|hermandad|cofradia|pena|diputacion|"
                           r"mancomunidad|parroquia|colegio|biblioteca|banda|ampa|cooperativa|"
                           r"comision de fiestas|junta|centro de dia|guadalinfo|escuela|fundacion)\b")


def _epoca_a_iso(v) -> str:
    if isinstance(v, (int, float)):
        segundos = v / 1000 if v > 1e11 else v
        return datetime.fromtimestamp(segundos, timezone.utc).isoformat()
    return str(v or "")


def normalizar_resultado(r: dict) -> dict:
    """Cada actor de búsqueda de Apify devuelve los campos con nombres distintos: se unifican."""
    autor = r.get("author") or r.get("user") or r.get("owner") or {}
    if not isinstance(autor, dict):
        autor = {"name": str(autor)}
    nombre = (autor.get("name") or r.get("pageName") or r.get("authorName") or r.get("userName") or "")
    tipo = str(autor.get("type") or r.get("authorType") or r.get("profileType") or "").lower()
    imagen = r.get("image") or r.get("imageUrl") or r.get("thumbnail")
    if isinstance(imagen, dict):
        imagen = imagen.get("uri") or imagen.get("url")
    post = {
        "text": r.get("text") or r.get("message") or r.get("postText") or r.get("content") or "",
        "url": r.get("url") or r.get("postUrl") or r.get("post_url") or r.get("link"),
        "time": _epoca_a_iso(r.get("time") or r.get("date") or r.get("timestamp")
                             or r.get("publishedAt") or r.get("creation_time")),
        "media": r.get("media") or [],
        "_imagen": imagen if isinstance(imagen, str) and imagen.startswith("http") else None,
    }
    es_org = bool(autor.get("isPage") or r.get("isPage") or tipo in ("page", "pagina")
                  or _ORGANIZACION.search(_norm(nombre)))
    return {"post": post, "autor_url": autor.get("url") or autor.get("profileUrl") or r.get("pageUrl"),
            "autor_nombre": nombre, "es_organizacion": es_org,
            "consulta": r.get("searchQuery") or r.get("query") or ""}


def consultas_de_la_pasada(cfg: dict, rapida: bool, hora: int | None = None) -> list[str]:
    """Todas las búsquedas (pueblo x palabra). En la pasada rápida (cada hora) solo unas
    pocas, por turnos: a lo largo del día se cubren todas sin gastar de más en Apify."""
    pueblos = cfg.get("pueblos") or ia.PUEBLOS
    plantillas = (cfg.get("consultas_cada_hora") if rapida else None) or cfg.get("consultas", [])
    todas = [c.replace("{pueblo}", p) for c in plantillas for p in pueblos] \
        if "{pueblo}" in "".join(plantillas) else list(plantillas)
    k = int(cfg.get("por_pasada_rapida", 6))
    if not rapida or k <= 0 or k >= len(todas):
        return todas
    turnos = -(-len(todas) // k)
    turno = (hora if hora is not None else int(datetime.now(timezone.utc).timestamp() // 3600)) % turnos
    return todas[turno * k:(turno + 1) * k]


def buscar_en_facebook(cfg: dict, semanas: int, rapida: bool = False,
                       consultas: list[str] | None = None) -> list[dict]:
    """Lanza el actor de búsqueda de Apify con cada consulta. La forma de la entrada se
    configura en fuentes_eventos.json ('entrada'), porque cada actor la pide distinta."""
    token = os.getenv("APIFY_TOKEN", "").strip()
    actor = cfg.get("actor", "scraper_one/facebook-posts-search").replace("/", "~")
    plantilla = cfg.get("entrada") or {"query": "{q}", "maxResults": "{n}"}
    n = int(cfg.get("por_consulta", 15))
    if not token:
        print("Facebook (búsqueda): falta APIFY_TOKEN -> no se busca")
        raise RuntimeError("Falta APIFY_TOKEN en el servidor: no se puede buscar en Facebook")
    consultas = consultas if consultas is not None else consultas_de_la_pasada(cfg, rapida)
    print(f"Facebook (búsqueda): {len(consultas)} consultas{' (pasada rápida)' if rapida else ''}")
    resultados: list[dict] = []
    for q in consultas:
        desde = (datetime.now(timezone.utc) - timedelta(days=int(cfg.get("dias_atras", 7 * semanas)))) \
            .strftime("%Y-%m-%d")
        entrada = json.loads(json.dumps(plantilla).replace("{q}", q).replace('"{n}"', str(n))
                             .replace("{n}", str(n)).replace("{semanas}", str(semanas))
                             .replace("{desde}", desde))
        try:
            r = httpx.post(f"https://api.apify.com/v2/acts/{actor}/run-sync-get-dataset-items",
                           headers={"Authorization": f"Bearer {token}"}, json=entrada, timeout=360)
            if r.status_code >= 400:
                print(f"   Apify ({q}): HTTP {r.status_code} {r.text[:150]}")
                continue
            for item in r.json() or []:
                if isinstance(item, dict):
                    item.setdefault("searchQuery", q)
                    resultados.append(item)
        except httpx.HTTPError as e:
            print(f"   Apify ({q}): {type(e).__name__}")
    print(f"Búsqueda en Facebook: {len(consultas)} consultas -> {len(resultados)} publicaciones")
    return resultados


def procesar_resultado_busqueda(res: dict, dry: bool, vistos: set[str]) -> str:
    """Una publicación encontrada buscando. Solo sigue si nombra uno de los pueblos y
    parece un evento; si no, se ignora SIN guardarla (no se almacenan posts personales
    que no tienen que ver)."""
    post = res["post"]
    texto = post["text"]
    url = post.get("url")
    if not url or url in vistos:
        return "repetido"
    vistos.add(url)
    pueblo = ia._pueblo_en_texto(texto)
    if not pueblo:
        return "ignorado_no_es_de_los_pueblos"
    if not parece_evento(texto, bool(post.get("_imagen")), pagina_de_pueblo=False):
        return "ignorado_no_parece_evento"
    if res["es_organizacion"]:
        entidad = None
        if res.get("autor_url"):
            datos = {"url": str(res["autor_url"]), "municipio": pueblo,
                     "nombre": (res.get("autor_nombre") or "Organización de " + pueblo)[:120]}
            if dry:
                filas = sb.table("entidades").select("*").eq(
                    "facebook_page_url", datos["url"].rstrip("/")).execute().data
                entidad = filas[0] if filas else None
            else:
                # Nueva organización descubierta: se guarda SIN confianza (sus planes van al
                # moderador hasta que él la marque o su responsable sea Publicador/a)
                entidad = asegurar_entidad(datos)
        fuente = {"municipio": pueblo, "nombre": "organización en Facebook",
                  "oficial": entidad_de_confianza(entidad),
                  "url": res.get("autor_url")}
    else:
        fuente = {"municipio": pueblo, "nombre": "publicación de una persona en Facebook",
                  "oficial": False, "persona": True}
    return procesar_post(post, fuente, dry)


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
    if fuente.get("persona"):
        donde = "Publicación de un vecino en Facebook (¿propone un plan?)"
    else:
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
    ext = ing.get("extracted") or {}
    if fuente.get("persona") and ing["estado"] in ("extraida", "esperando_confirmacion") \
            and float(ext.get("confidence") or 0) < 0.6:
        # Una persona cualquiera: al moderador SOLO le llegan propuestas que la IA ve claras
        ingesta._actualizar_ingesta(fila["id"], estado="descartada",
                                    spam_reason="persona: la IA no ve claro que proponga un evento")
        ing["estado"] = "descartada"
    if ing["estado"] not in ("extraida", "esperando_confirmacion"):
        if fuente.get("persona") and ing["estado"] in ("descartada", "spam"):
            # Publicación de un particular que no es un evento: no se guarda su contenido
            # (solo el enlace, para no volver a procesarla)
            ingesta._actualizar_ingesta(fila["id"], raw_text=None, image_url=None, extracted=None)
        return ing["estado"]  # spam / descartada / error
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
    if fuente.get("persona"):
        motivo = "propuesta de un vecino en Facebook: la revisa un moderador"
    elif not fuente.get("oficial"):
        motivo = "publicado por una organización que aún no es de confianza"
    else:
        motivo = "la IA tiene dudas o faltan datos"
    ev = ingesta.crear_evento_desde_ingesta(fila["id"], publicar=publicar, motivo=motivo)
    if "error" in ev:
        return f"falta: {','.join(ev.get('missing') or []) or ev['error'][:40]}"
    return f"evento_{ev.get('status')}"


def cargar_config(ruta: str = FUENTES) -> dict:
    with open(ruta, encoding="utf-8") as f:
        return json.load(f)


def cargar_busqueda(ruta: str = FUENTES) -> dict:
    return cargar_config(ruta).get("busqueda_facebook") or {}


def ejecutar(dry: bool = False, semanas: int = 6, archivo: str | None = None,
             fuentes: str = FUENTES, solo: str | None = None,
             archivo_busqueda: str | None = None, rapida: bool = False,
             busqueda_completa: bool = True, consultas: list[str] | None = None) -> dict[str, int]:
    """Una pasada. La usa también la tarea programada de Render (tareas.py).
    solo='paginas' | 'busqueda' | 'webs' | 'articulos' | None (todo).
    rapida=True (cada hora): solo unas pocas búsquedas por turnos; sin páginas ni artículos."""
    paginas, articulos = cargar_fuentes(fuentes)
    if rapida:
        solo = solo or "busqueda"
    elif not archivo:
        # Además de las páginas del archivo, las organizaciones que ya se descubrieron
        vistas = {_url_limpia(p["url"]) for p in paginas}
        paginas += [p for p in paginas_descubiertas() if _url_limpia(p["url"]) not in vistas]
    resumen: dict[str, int] = {}

    def anotar(res: str, municipio: str, fecha: str, texto: str) -> None:
        resumen[res] = resumen.get(res, 0) + 1
        print(f"  [{res}] {municipio} · {fecha[:10]} · {texto[:70]!r}")

    # ── Facebook: páginas concretas (Apify)
    if solo in (None, "paginas") and (archivo or (paginas and os.getenv("APIFY_TOKEN"))):
        if not dry:
            for p in paginas:
                ent = asegurar_entidad(p)
                p["oficial"] = bool(p.get("oficial")) or entidad_de_confianza(ent)
        if archivo:
            with open(archivo, encoding="utf-8") as f:
                posts = json.load(f)
        else:
            cfg_paginas = cargar_config(fuentes).get("paginas_facebook") or {}
            posts = leer_facebook(paginas, semanas, int(cfg_paginas.get("posts_por_pagina", POSTS_POR_PAGINA)))
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
    elif solo in (None, "paginas"):
        print("Facebook (páginas): sin APIFY_TOKEN o sin páginas con URL real -> se salta")

    # ── Facebook: búsqueda de publicaciones de los pueblos (páginas y vecinos)
    busqueda = cargar_busqueda(fuentes)
    if solo in (None, "busqueda") and busqueda.get("consultas") and (
            archivo_busqueda or os.getenv("APIFY_TOKEN")):
        if archivo_busqueda:
            with open(archivo_busqueda, encoding="utf-8") as f:
                crudos = json.load(f)
        else:
            crudos = buscar_en_facebook(busqueda, semanas, rapida or not busqueda_completa, consultas)
        vistos: set[str] = set()
        for crudo in crudos:
            res = normalizar_resultado(crudo)
            try:
                r = procesar_resultado_busqueda(res, dry, vistos)
            except Exception as e:
                r = f"error: {type(e).__name__}"
            if r != "repetido":
                quien = "org" if res["es_organizacion"] else "persona"
                anotar(r, quien, res["post"].get("time") or "", res["post"]["text"] or "(solo foto)")
    elif solo in (None, "busqueda"):
        print("Facebook (búsqueda): sin APIFY_TOKEN o sin consultas -> se salta")

    # ── Webs de los ayuntamientos: scraping dirigido con IA (ScrapeGraphAI + Gemini)
    webs = cargar_config(fuentes).get("webs_ayuntamientos") or []
    if solo in (None, "webs") and webs:
        import webs_aytos
        print(f"Webs de ayuntamientos: {len(webs)}{' (SIMULACIÓN)' if dry else ''}")
        for res, n in webs_aytos.leer_webs(webs, dry, procesar_post).items():
            resumen[f"web_{res}"] = resumen.get(f"web_{res}", 0) + n

    # ── Artículos de prensa / agendas públicas
    if solo in (None, "articulos") and articulos:
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
    ap.add_argument("--solo", choices=["paginas", "busqueda", "articulos"], help="solo una de las fuentes")
    ap.add_argument("--archivo-busqueda", help="JSON con resultados ya descargados del actor de búsqueda")
    a = ap.parse_args()
    ejecutar(a.dry_run, a.semanas, a.archivo, a.fuentes, a.solo, a.archivo_busqueda)


if __name__ == "__main__":
    main()
