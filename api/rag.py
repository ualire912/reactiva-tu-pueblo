"""
api/rag.py  ·  C1  ·  Asistente que responde preguntas sobre la agenda y la app (embeddings)

Como recomendó el profesor, en DOS FASES para no pasarle miles de eventos al modelo:
  Fase 1 · FILTRAR: de la pregunta se sacan categoría, pueblo, fechas y palabras clave
           (primero reglas, gratis; IA solo si las reglas no sacan nada).
  Fase 2 · BUSCAR: solo entre los eventos filtrados, los más parecidos a la pregunta
           (embedding de la pregunta vs. embedding de cada evento, en Supabase/pgvector).
  Luego se responde: lista directa (0 tokens) o, si hace falta razonar ("¿cuántas veces…?"),
  la IA redacta la respuesta SOLO con esos eventos.

Dos "bases" vectoriales: eventos (tabla eventos_embeddings) y conocimiento general (tabla conocimiento).

Endpoint:  POST /ia/preguntar  {pregunta}  ->  {respuesta, eventos: [...], tipo}
Funciones: preguntar_agenda(pregunta) -> dict      (la puede usar B2 en el bot)
           indexar_eventos_pendientes(limite) -> int (B1 la llama cada 5 min en tareas.py)
           indexar_conocimiento() -> int
Plan B:    sin embeddings -> filtros + búsqueda por palabras (ILIKE). Nunca se rompe.
"""
from __future__ import annotations

import difflib
import hashlib
import json
import logging
import os
import re
import time
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

import ia
from db import require_moderador, sb

log = logging.getLogger("reactiva.rag")
router = APIRouter(tags=["ia-rag"])

EMBED_MODEL = os.getenv("GEMINI_EMBED_MODEL", "gemini-embedding-001")
DIMENSIONES = 768            # tiene que coincidir con vector(768) de rag.sql
_CACHE_EMB: dict[str, list[float]] = {}


# ══════════════════════════════════════════════════════════════
#  Embeddings (Gemini, con las dos claves)
# ══════════════════════════════════════════════════════════════
def embeber(textos: list[str], para_pregunta: bool = False) -> list[list[float]] | None:
    """Convierte textos en vectores de 768 números. None si no hay forma (Plan B)."""
    if not textos:
        return []
    from google.genai import types
    tipo = "RETRIEVAL_QUERY" if para_pregunta else "RETRIEVAL_DOCUMENT"
    for nombre in ("GEMINI_API_KEY", "GEMINI_API_KEY_2"):
        clave = os.getenv(nombre)
        if not clave:
            continue
        try:
            r = ia._cliente_gemini(clave).models.embed_content(
                model=EMBED_MODEL, contents=textos,
                config=types.EmbedContentConfig(task_type=tipo, output_dimensionality=DIMENSIONES))
            vectores = [list(e.values) for e in r.embeddings]
            if any(len(v) != DIMENSIONES for v in vectores):
                log.error("El modelo %s no devolvió %d dimensiones", EMBED_MODEL, DIMENSIONES)
                return None
            ia.CONSUMO["llamadas"] += 1
            ia.CONSUMO["tokens"] += sum(len(t) for t in textos) // 4  # estimación
            return vectores
        except Exception as e:
            log.warning("Embedding con %s falló: %s", nombre, str(e)[:150])
    return None


def _vector_pregunta(pregunta: str) -> list[float] | None:
    clave = hashlib.sha256(pregunta.lower().encode()).hexdigest()
    if clave not in _CACHE_EMB:
        v = embeber([pregunta], para_pregunta=True)
        if not v:
            return None
        _CACHE_EMB[clave] = v[0]
    return _CACHE_EMB[clave]


def _pg(v: list[float] | None) -> str | None:
    """Formato que entiende pgvector: '[0.1,0.2,...]'."""
    return None if v is None else "[" + ",".join(f"{x:.6f}" for x in v) + "]"


# ══════════════════════════════════════════════════════════════
#  Indexar (crear los embeddings de eventos y conocimiento)
# ══════════════════════════════════════════════════════════════
def texto_evento(ev: dict) -> str:
    """Todo lo que describe el evento en una frase, para el embedding."""
    partes = [ev.get("title"), f"Categoría: {ev.get('category')}"]
    if ev.get("municipio"):
        partes.append(f"Pueblo: {ev['municipio']}")
    if ev.get("location_name"):
        partes.append(f"Lugar: {ev['location_name']}")
    if ev.get("starts_at"):
        d = datetime.fromisoformat(str(ev["starts_at"]).replace("Z", "+00:00")).astimezone(ia.TZ)
        partes.append(f"Fecha: {ia._DIAS_ES[d.weekday()]} {d.day} de "
                      f"{ia._MESES_ES[d.month - 1]} de {d.year}")
    if ev.get("price") is not None:
        partes.append("Gratis" if float(ev["price"]) == 0 else f"Precio: {ev['price']} euros")
    if ev.get("is_accessible"):
        partes.append("Accesible")
    if ev.get("min_age") or ev.get("max_age"):
        partes.append(f"Edad: {ev.get('min_age') or 0}-{ev.get('max_age') or 'sin máximo'}")
    if ev.get("description"):
        partes.append(str(ev["description"])[:600])
    return ". ".join(str(p) for p in partes if p)


def indexar_eventos_pendientes(limite: int = 100) -> int:
    """Crea/actualiza el embedding de los eventos nuevos o con contenido cambiado.
    Los embeddings van en la tabla eventos_embeddings (diseño de B1): guardar uno no toca
    eventos (ni updated_at, ni sequence, ni Realtime). Devuelve cuántos se han indexado."""
    try:
        pendientes = (sb.table("v_eventos_sin_indexar").select("id, huella")
                      .limit(limite).execute().data)
        if not pendientes:
            return 0
        huellas = {p["id"]: p["huella"] for p in pendientes}
        eventos = (sb.table("eventos").select("*, municipios(nombre)")
                   .in_("id", list(huellas)).execute().data)
        for ev in eventos:
            ev["municipio"] = (ev.pop("municipios", None) or {}).get("nombre")
        hechos = 0
        for i in range(0, len(eventos), 50):  # en lotes de 50 (1 llamada por lote)
            lote = eventos[i:i + 50]
            vectores = embeber([texto_evento(e) for e in lote])
            if not vectores:
                break
            for ev, v in zip(lote, vectores):
                if len(v) != DIMENSIONES:  # la columna es vector(768): si no, fallaría
                    log.error("Embedding con %d dimensiones (esperaba %d)", len(v), DIMENSIONES)
                    return hechos
                sb.rpc("guardar_embedding_evento", {"p_event_id": ev["id"], "p_embedding": _pg(v),
                                                    "p_huella": huellas.get(ev["id"])}).execute()
                hechos += 1
        log.info("Indexados %d eventos", hechos)
        return hechos
    except Exception:
        log.exception("indexar_eventos_pendientes falló")
        return 0


def indexar_conocimiento() -> int:
    """Carga api/conocimiento.json en la tabla conocimiento con sus embeddings."""
    ruta = os.path.join(os.path.dirname(os.path.abspath(__file__)), "conocimiento.json")
    with open(ruta, encoding="utf-8") as f:
        temas = json.load(f)
    vectores = embeber([f"{t['tema']}. {t['contenido']}" for t in temas])
    filas = [{"tema": t["tema"], "contenido": t["contenido"],
              "embedding": _pg(vectores[k]) if vectores else None,
              "updated_at": datetime.now(ia.TZ).isoformat()}
             for k, t in enumerate(temas)]
    sb.table("conocimiento").upsert(filas, on_conflict="tema").execute()
    return len(filas)


# ══════════════════════════════════════════════════════════════
#  Fase 1 · Sacar filtros de la pregunta
# ══════════════════════════════════════════════════════════════
_GENERAL = re.compile(r"iniciar sesion|registr|cuenta|contrasena|telegram|vincular|como funciona|"
                      r"la app|aplicacion|publicador|resena|valorar|incidencia|lista de espera|"
                      r"borrarme|cancelar mi|desapuntar|calendario|audio|notificacion|aviso|"
                      r"compartir coche|como me apunto|como apuntarme|como propongo|como proponer")
_PASADO = re.compile(r"\b(hubo|ha habido|habia|pasad[oa]s?|se hizo|se hicieron|se celebro|se celebraron|"
                     r"celebrad[oa]s|fue|fueron|cuantas veces|historial|anteriores|antiguos?|"
                     r"desde cuando|finalizad[oa]s?|terminad[oa]s?|acabad[oa]s?|ya pasaron|"
                     r"que ya han|que se han hecho|ultimos eventos|realizad[oa]s?|se realizo|"
                     r"se realizaron|hechos|organizad[oa]s|tuvieron lugar|ha habido)\b")


_NUM_PALABRA = {"un": 1, "una": 1, "dos": 2, "tres": 3, "cuatro": 4, "cinco": 5, "seis": 6}
_FINALIZADOS = re.compile(r"\b(finalizad|terminad|acabad|celebrad|realizad)[oa]s?\b")

# Palabras que no dicen QUÉ evento se busca (solo cuándo, dónde o que se quiere una lista).
# Si la pregunta solo tiene estas, se lista por fecha en vez de por parecido.
_VACIAS = set("""
dame dime muestrame ensename ensena lista listame quiero querria ver saber busca buscame
hay habia hubo han ha que cuales cual cuantos cuantas todos todas los las el la lo un una unos
unas de del al a en para por con y o mi me mis se ya algun alguno alguna
evento eventos plan planes actividad actividades cosa cosas algo hacer
finalizado finalizados finalizadas terminado terminados terminadas pasado pasados pasadas
celebrado celebrados celebradas anteriores antiguo antiguos acabado acabados acabadas hizo
realizado realizados realizadas hechos organizados organizadas
hicieron celebro celebraron fue fueron proximo proximos proxima proximas siguiente
siguientes nuevos futuro futuros hoy manana semana semanas mes meses ano dia dias finde fin
viene este esta esto dentro durante ultimos ultimas uno dos tres cuatro cinco seis pueblo
pueblos zona favor porfa lunes martes miercoles jueves viernes sabado domingo tarde noche
apuntarme apuntar apunto apuntarnos inscribirme inscribir ir vamos
""".split())


def _es_generica(n: str) -> bool:
    """¿La pregunta solo pide una lista (con filtros) sin decir de qué va el evento?"""
    pueblos = {t for p in ia.PUEBLOS for t in ia._norm(p).split()}
    raices = [r for lista in ia.SINONIMOS_CATEGORIA.values() for r in lista] + \
             [c for c in ia.CATEGORIAS]
    for tok in re.findall(r"[a-z0-9]+", n):
        if tok in _VACIAS or tok in pueblos or tok.isdigit():
            continue
        if any(tok.startswith(r) for r in raices):
            continue
        if len(tok) >= 4 and difflib.get_close_matches(tok, _VACIAS, n=1, cutoff=0.75):
            continue  # errata: "evetnos", "finalizdos"...
        return False
    return True


def _rango_fechas(n: str) -> tuple[datetime | None, datetime | None]:
    """Fechas relativas -> (desde, hasta). Texto ya normalizado (sin tildes, minúsculas)."""
    ahora = datetime.now(ia.TZ)
    hoy = ahora.replace(hour=0, minute=0, second=0, microsecond=0)
    m = re.search(r"\b(20\d\d)\b", n)
    if m:
        a = int(m.group(1))
        return hoy.replace(year=a, month=1, day=1), hoy.replace(year=a + 1, month=1, day=1)
    # "dentro de dos semanas", "en las próximas 2 semanas", "los próximos 10 días", "últimos 3 meses"
    m = re.search(r"\b(dentro de|en|durante|proxim[oa]s|siguientes|ultim[oa]s)\s+(?:las\s+|los\s+)?"
                  r"(\d+|una?|dos|tres|cuatro|cinco|seis)\s+(dias?|semanas?|mes(?:es)?)\b", n)
    if m:
        cuantos = int(m.group(2)) if m.group(2).isdigit() else _NUM_PALABRA[m.group(2)]
        dias = cuantos * {"d": 1, "s": 7, "m": 30}[m.group(3)[0]]
        if m.group(1).startswith("ultim"):
            return ahora - timedelta(days=dias), ahora
        return ahora, ahora + timedelta(days=dias)
    if "ano pasado" in n:
        return hoy.replace(year=hoy.year - 1, month=1, day=1), hoy.replace(month=1, day=1)
    if "este ano" in n:
        return hoy.replace(month=1, day=1), hoy.replace(year=hoy.year + 1, month=1, day=1)
    if "mes pasado" in n:
        ini = (hoy.replace(day=1) - timedelta(days=1)).replace(day=1)
        return ini, hoy.replace(day=1)
    if "este mes" in n:
        sig = (hoy.replace(day=28) + timedelta(days=4)).replace(day=1)
        return hoy, sig
    # "semana que viene" aguantando erratas: "semana uqe viene", "semana q viene"...
    if re.search(r"proxima semana|semana proxima|semana entrante|semana\s+\S{1,4}\s+viene", n):
        ini = hoy + timedelta(days=7 - hoy.weekday())
        return ini, ini + timedelta(days=7)
    if "esta semana" in n:
        return hoy, hoy + timedelta(days=7 - hoy.weekday())
    if re.search(r"fin de semana|finde", n):
        sab = hoy + timedelta(days=(5 - hoy.weekday()) % 7)
        return max(hoy, sab), sab + timedelta(days=2)
    f = ia._fecha_de_texto(n)  # hoy, mañana, el sábado...
    if f:
        d = datetime.fromisoformat(f).replace(tzinfo=ia.TZ)
        return d, d + timedelta(days=1)
    return None, None


_PROMPT_FILTROS = """Analiza la pregunta de un vecino sobre la agenda de eventos de unos pueblos de Almería.
Hoy es {hoy}. Devuelve SOLO JSON:
{{"tipo": "eventos" (pregunta por eventos/planes) o "general" (cómo funciona la app, cuentas, Telegram...),
  "categoria": una de {categorias} SOLO si la pregunta nombra ese tipo de plan; si no, null,
  "municipio": uno de {pueblos} SOLO si se nombra; si no, null,
  "desde": "YYYY-MM-DD" o null, "hasta": "YYYY-MM-DD" (sin incluir) o null,
  "palabras": "1-3 palabras clave del nombre del evento (p. ej. romería, migas) o null",
  "pasado": true si pregunta por eventos que ya ocurrieron}}
No inventes filtros que no estén en la pregunta.
PREGUNTA: \"\"\"{pregunta}\"\"\"
"""


def extraer_filtros(pregunta: str) -> dict:
    """Fase 1. Reglas primero; IA solo si las reglas no encuentran ningún filtro."""
    n = ia._norm(pregunta)
    base = ia._reglas_mensaje(pregunta)["filtros"]
    desde, hasta = _rango_fechas(n)
    filtros = {
        "tipo": "general" if _GENERAL.search(n) else "eventos",
        "categoria": base.get("categoria"),
        "municipio": base.get("municipio"),
        "desde": desde, "hasta": hasta, "palabras": None,
        "pasado": bool(_PASADO.search(n)),
        "solo_finalizados": bool(_FINALIZADOS.search(n)),
        "generica": _es_generica(n),
        "con_ia": False,
    }
    if filtros["solo_finalizados"]:
        filtros["pasado"] = True
    # Una lista genérica ("dame los eventos finalizados") no necesita IA para entenderse
    hay_algo = (filtros["categoria"] or filtros["municipio"] or desde or filtros["tipo"] == "general"
                or filtros["generica"])
    if not hay_algo and len(n.split()) >= 4:
        datos = ia._json(ia._llamar(_PROMPT_FILTROS.format(
            hoy=ia._hoy_texto(), categorias=ia.CATEGORIAS, pueblos=ia.PUEBLOS,
            pregunta=pregunta[:500])))
        if datos:
            filtros["con_ia"] = True
            if datos.get("tipo") in ("eventos", "general"):
                filtros["tipo"] = datos["tipo"]
            c = ia._norm(str(datos.get("categoria") or ""))
            filtros["categoria"] = c if c in ia.CATEGORIAS else None
            filtros["municipio"] = ia._pueblo(datos.get("municipio"))
            for k in ("desde", "hasta"):
                try:
                    if datos.get(k):
                        filtros[k] = datetime.fromisoformat(str(datos[k])[:10]).replace(tzinfo=ia.TZ)
                except ValueError:
                    pass
            if datos.get("palabras"):
                filtros["palabras"] = str(datos["palabras"])[:40]
            filtros["pasado"] = bool(datos.get("pasado")) or filtros["pasado"]
    # Sin hablar del pasado -> solo eventos de AHORA en adelante (no los que ya
    # terminaron esta mañana), también si el rango empezaba antes ("esta semana")
    if filtros["tipo"] == "eventos" and not filtros["pasado"]:
        ahora = datetime.now(ia.TZ)
        if not filtros["desde"] or (filtros["desde"] < ahora
                                    and (not filtros["hasta"] or filtros["hasta"] > ahora)):
            filtros["desde"] = ahora
    # Hablando del pasado -> nunca eventos futuros
    if filtros["tipo"] == "eventos" and filtros["pasado"]:
        ahora = datetime.now(ia.TZ)
        if not filtros["hasta"] or filtros["hasta"] > ahora:
            filtros["hasta"] = ahora
    return filtros


# ══════════════════════════════════════════════════════════════
#  Fase 2 · Buscar y responder
# ══════════════════════════════════════════════════════════════
def _municipio_id(nombre: str | None) -> int | None:
    if not nombre:
        return None
    for m in sb.table("municipios").select("id, nombre").execute().data:
        if ia._norm(m["nombre"]) == ia._norm(nombre):
            return m["id"]
    return None


def buscar_eventos(pregunta: str, filtros: dict, limite: int = 8) -> list[dict]:
    vector = _vector_pregunta(pregunta)
    params = {
        "p_embedding": _pg(vector),
        "p_categoria": filtros.get("categoria"),
        "p_municipio_id": _municipio_id(filtros.get("municipio")),
        "p_desde": filtros["desde"].isoformat() if filtros.get("desde") else None,
        "p_hasta": filtros["hasta"].isoformat() if filtros.get("hasta") else None,
        # Plan B (sin embeddings): búsqueda por palabra clave
        "p_texto": filtros.get("palabras") if vector is None else None,
        "p_limite": limite,
    }
    return sb.rpc("buscar_eventos_similares", params).execute().data or []


_ESTADOS_VISIBLES = ["abierto", "completo", "inscripcion_cerrada", "en_curso",
                     "finalizado", "pospuesto", "cancelado"]


def buscar_por_fecha(filtros: dict, limite: int = 8) -> list[dict]:
    """Lista genérica: mismos filtros que buscar_eventos pero ordenada por fecha
    (lo más reciente primero si se pregunta por el pasado). Mismo formato de filas."""
    q = (sb.table("eventos")
         .select("id,title,category,starts_at,location_name,price,max_capacity,confirmed_count,"
                 "status,description,municipios(nombre)", count="exact"))
    if filtros.get("solo_finalizados"):
        q = q.eq("status", "finalizado")
    elif filtros.get("pasado"):
        q = q.in_("status", _ESTADOS_VISIBLES)
    else:  # futuro: lo que todavía se puede hacer
        q = q.in_("status", ["abierto", "completo", "inscripcion_cerrada", "en_curso", "pospuesto"])
    if filtros.get("categoria"):
        q = q.eq("category", filtros["categoria"])
    mid = _municipio_id(filtros.get("municipio"))
    if mid:
        q = q.eq("municipio_id", mid)
    if filtros.get("desde"):
        q = q.gte("starts_at", filtros["desde"].isoformat())
    if filtros.get("hasta"):
        q = q.lt("starts_at", filtros["hasta"].isoformat())
    r = q.order("starts_at", desc=bool(filtros.get("pasado"))).limit(limite).execute()
    total = r.count if r.count is not None else len(r.data or [])
    return [{"event_id": e["id"], "title": e["title"], "category": e["category"],
             "starts_at": e["starts_at"], "municipio": (e.get("municipios") or {}).get("nombre"),
             "location_name": e.get("location_name"), "price": e.get("price"),
             "max_capacity": e.get("max_capacity"), "confirmed_count": e.get("confirmed_count"),
             "status": e["status"], "description": (e.get("description") or "")[:300],
             "similitud": 0, "total_filtrados": total} for e in (r.data or [])]


def _fecha_corta(d: datetime) -> str:
    return f"{d.day} de {ia._MESES_ES[d.month - 1]}" + (f" de {d.year}" if d.year != datetime.now(ia.TZ).year else "")


def describir_filtros(f: dict) -> str:
    """'de deporte en Senés entre el 1 de octubre y el 31 de octubre' (para cabeceras)."""
    partes = []
    if f.get("categoria"):
        nombres = {"gastronomia": "gastronomía", "otro": "otro tipo"}
        partes.append(f"de {nombres.get(f['categoria'], f['categoria'])}")
    if f.get("municipio"):
        partes.append(f"en {f['municipio']}")
    desde, hasta = f.get("desde"), f.get("hasta")
    if desde and hasta:
        fin = hasta - timedelta(seconds=1)
        empieza_ahora = abs((desde - datetime.now(ia.TZ)).total_seconds()) < 300
        if desde.date() == fin.date():
            partes.append(f"el {_fecha_corta(desde)}")
        elif empieza_ahora:
            partes.append(f"de aquí al {_fecha_corta(fin)}")
        else:
            partes.append(f"entre el {_fecha_corta(desde)} y el {_fecha_corta(fin)}")
    return " ".join(partes)


def _fecha_con_anio(iso) -> str:
    try:
        d = datetime.fromisoformat(str(iso).replace("Z", "+00:00")).astimezone(ia.TZ)
        return (f"el {ia._DIAS_ES[d.weekday()]} {d.day} de {ia._MESES_ES[d.month - 1]} "
                f"de {d.year} a las {d:%H:%M}")
    except Exception:
        return ""


def _linea(e: dict, con_anio: bool = False) -> str:
    precio = "" if e.get("price") is None else (" · gratis" if float(e["price"]) == 0
                                                 else f" · {float(e['price']):g} €")
    estado = {"cancelado": " (cancelado)", "pospuesto": " (aplazado)", "completo": " (completo)",
              "finalizado": " (ya celebrado)", "en_curso": " (en curso)"}.get(e.get("status"), "")
    fecha = _fecha_con_anio(e.get("starts_at")) if con_anio else ia.fecha_humana(e.get("starts_at"))
    return f"• {e['title']} — {e.get('municipio')}, {fecha}{precio}{estado}"


_PROMPT_RESPUESTA = """Eres el asistente de la agenda de unos pueblos de Almería. Responde en español,
en 1-4 frases sencillas y cercanas, como un vecino que conoce la agenda, usando SOLO estos datos.
Responde directamente a la pregunta. No digas "según los datos", "eventos mostrados" ni copies
etiquetas como "(ya celebrado)": si un evento ya pasó, dilo con naturalidad ("se celebró el...").
Si los datos no bastan para responder, dilo. No inventes.
Escribe texto plano: sin Markdown, sin asteriscos ni almohadillas.

{contexto}

PREGUNTA: {pregunta}
"""
_LISTAR = re.compile(r"^(que|hay|cuales|dime|dame|ensena|ensename|muestrame|busca|buscame|algo|planes|"
                     r"eventos|alguna|algun|lista|listame|quiero ver|quiero saber|ver)")


def preguntar_agenda(pregunta: str) -> dict:
    """-> {"respuesta": str, "eventos": [...], "tipo": "eventos"|"general"}. Nunca falla."""
    pregunta = (pregunta or "").strip()[:500]
    if not pregunta:
        return {"respuesta": "¿Qué quieres saber?", "eventos": [], "tipo": "general"}
    try:
        f = extraer_filtros(pregunta)

        # ── Conocimiento general de la app
        if f["tipo"] == "general":
            vector = _vector_pregunta(pregunta)
            trozos = (sb.rpc("buscar_conocimiento", {"p_embedding": _pg(vector), "p_limite": 3})
                      .execute().data if vector else []) or []
            if not trozos:  # Plan B: por palabras
                ruta = os.path.join(os.path.dirname(os.path.abspath(__file__)), "conocimiento.json")
                with open(ruta, encoding="utf-8") as fh:
                    temas = json.load(fh)
                n = set(ia._norm(pregunta).split())
                temas.sort(key=lambda t: -len(n & set(ia._norm(t["tema"] + " " + t["contenido"]).split())))
                trozos = [{"tema": t["tema"], "contenido": t["contenido"], "similitud": 0} for t in temas[:2]]
            if trozos and trozos[0].get("similitud", 0) >= 0.80:
                return {"respuesta": trozos[0]["contenido"], "eventos": [], "tipo": "general"}  # 0 tokens
            contexto = "\n".join(f"- {t['tema']}: {t['contenido']}" for t in trozos)
            texto = ia._llamar(_PROMPT_RESPUESTA.format(contexto=contexto, pregunta=pregunta),
                               como_json=False)
            return {"respuesta": (texto or (trozos[0]["contenido"] if trozos else ia.NO_LO_SE)).strip(),
                    "eventos": [], "tipo": "general"}

        # ── Eventos: fase 2
        if f.get("generica"):
            # "dame los eventos finalizados", "eventos de Senés dentro de dos semanas":
            # no hay nada que comparar por parecido -> por fecha (lo más cercano a hoy primero)
            eventos = buscar_por_fecha(f)
        else:
            eventos = buscar_eventos(pregunta, f)
            if not eventos and f.get("con_ia") and (f.get("categoria") or f.get("municipio")):
                # La IA eligió filtros demasiado estrechos ("algo con mis nietos" -> "otro"):
                # se repite solo con las fechas y que decida el parecido (embedding)
                eventos = buscar_eventos(pregunta, {**f, "categoria": None, "municipio": None})
            if f.get("solo_finalizados"):
                eventos = [e for e in eventos if e.get("status") == "finalizado"]
        total = eventos[0]["total_filtrados"] if eventos else 0
        salida = [{"id": e["event_id"], "title": e["title"], "starts_at": e["starts_at"],
                   "municipio": e["municipio"], "status": e["status"]} for e in eventos]
        que = describir_filtros(f)
        if not eventos:
            texto = (f"No hubo eventos {que}." if f["pasado"] and que else
                     f"No he encontrado eventos {que}." if que else
                     "No he encontrado eventos que encajen con lo que buscas.")
            return {"respuesta": texto, "cabecera": texto, "eventos": [], "tipo": "eventos", "total": 0}

        n = re.sub(r"^[¿¡\s]+", "", ia._norm(pregunta))
        if f.get("generica") or _LISTAR.match(n):  # lista directa, 0 tokens
            if f["pasado"]:
                eventos.sort(key=lambda e: e.get("starts_at") or "", reverse=True)
                nombre = "eventos ya celebrados" if f.get("solo_finalizados") else "eventos"
                cab = (f"Hay {total} {nombre} {que}".rstrip() + ". Los más recientes:" if f.get("generica")
                       else f"Se celebraron {total} eventos así. Los más relacionados:")
                if total <= 6:
                    cab = (f"Estos son los {nombre} {que}".rstrip() + f" ({total}):")
                lineas = [_linea(e, con_anio=True).replace(" (ya celebrado)", "") for e in eventos[:6]]
            else:
                eventos.sort(key=lambda e: e.get("starts_at") or "")
                if total <= 6:
                    cab = f"Estos son los planes {que}".rstrip() + f" ({total}):"
                else:
                    cab = f"Hay {total} planes {que}".rstrip() + "; estos son los más próximos:"
                lineas = [_linea(e) for e in eventos[:6]]
            salida = [{"id": e["event_id"], "title": e["title"], "starts_at": e["starts_at"],
                       "municipio": e["municipio"], "status": e["status"]} for e in eventos]
            return {"respuesta": cab + "\n" + "\n".join(lineas), "cabecera": cab,
                    "eventos": salida[:6], "tipo": "eventos", "total": total}

        contexto = (f"HOY ES {ia._hoy_texto()}.\n"
                    f"EVENTOS QUE CUMPLEN LOS FILTROS: {total} en total. "
                    f"Los {len(eventos)} más relacionados con la pregunta:\n"
                    + "\n".join(_linea(e, con_anio=True)
                                + (f" — {e['description'][:150]}" if e.get("description") else "")
                                for e in eventos))
        texto = ia._llamar(_PROMPT_RESPUESTA.format(contexto=contexto, pregunta=pregunta),
                           como_json=False)
        if not texto:  # Plan B: lista
            texto = "Esto es lo que he encontrado:\n" + "\n".join(_linea(e) for e in eventos[:5])
        texto = texto.strip()[:1200]
        return {"respuesta": texto, "cabecera": texto, "eventos": salida, "tipo": "eventos",
                "total": total}
    except Exception:
        log.exception("preguntar_agenda falló")
        return {"respuesta": "Ahora mismo no puedo buscar. Mira la agenda en la app.",
                "eventos": [], "tipo": "eventos"}


# ══════════════════════════════════════════════════════════════
#  Para el bot (B2): UNA sola función para cualquier texto libre o nota de voz
# ══════════════════════════════════════════════════════════════
_SALUDO = re.compile(r"^(hola|buenas|buenos dias|buenas tardes|buenas noches|hey|ey|saludos|"
                     r"que tal|holi)\b")
_GRACIAS = re.compile(r"^(gracias|muchas gracias|vale|ok|perfecto|genial|estupendo)\b")

TEXTO_AYUDA = ("Puedes preguntarme cosas como:\n"
               "• ¿Qué hay este fin de semana en Tahal?\n"
               "• ¿Qué rutas de senderismo hay?\n"
               "• ¿Cómo vinculo Telegram?\n"
               "También puedes mandarme una nota de voz.")


_RE_ESTRELLAS = re.compile(r"\b([1-5]|un[oa]?|dos|tres|cuatro|cinco)\s*(?:de\s*5\s*)?(?:estrellas?|★|⭐)|"
                           r"\b(?:un|una|le pongo un|le doy un|nota de|puntu\w* (?:con )?un?)\s*([1-5])\b")
_NUMEROS = {"un": 1, "uno": 1, "una": 1, "dos": 2, "tres": 3, "cuatro": 4, "cinco": 5}


def _detectar_resena(dicho: str) -> dict | None:
    """'al torneo de fútbol sala le pongo un 4 de 5 y fue muy divertido' ->
    propone valorar ese evento (B2 enseña el botón rate:{id}:{n})."""
    n = ia._norm(dicho)
    if not re.search(r"estrella|puntu|valor|resena|nota|le pongo|le doy", n):
        return None
    m = _RE_ESTRELLAS.search(n)
    if not m:
        return None
    crudo = m.group(1) or m.group(2)
    estrellas = int(crudo) if crudo.isdigit() else _NUMEROS.get(crudo)
    if not estrellas:
        return None
    # Comentario: lo que va detrás de "diciendo que", "comentario", "porque"...
    c = re.search(r"(?:comentario|diciendo que|diciendo|porque|que ha sido|que fue|y que)\s*:?\s*(.+)$",
                  dicho, re.I)
    comentario = c.group(1).strip(" .") if c else None
    if comentario:
        comentario = re.sub(r"^(diciendo que|diciendo|que ha sido|que fue|que)\s+", "", comentario, flags=re.I)
        comentario = comentario[:1].upper() + comentario[1:] if comentario else None
    # Buscar el evento entre los ya celebrados
    f = extraer_filtros(dicho)
    f.update({"tipo": "eventos", "pasado": True, "desde": None,
              "hasta": datetime.now(ia.TZ) + timedelta(days=1)})
    candidatos = [e for e in buscar_eventos(dicho, f, limite=3) if e.get("status") == "finalizado"]
    if not candidatos:
        return {"texto": "Quieres valorar un evento, pero no encuentro cuál. "
                         "Usa el botón de estrellas que te llega cuando termina un evento.",
                "eventos": [], "intencion": "resena", "resena": None}
    e0 = candidatos[0]
    texto = (f"¿Quieres valorar «{e0['title']}» ({e0.get('municipio')}, "
             f"{_fecha_con_anio(e0.get('starts_at'))}) con {estrellas} ★"
             + (f" y el comentario «{comentario}»" if comentario else "") + "? Pulsa para confirmar.")
    return {"texto": texto, "intencion": "resena",
            "eventos": [{"id": c["event_id"], "title": c["title"], "starts_at": c["starts_at"],
                         "municipio": c["municipio"], "status": c["status"]} for c in candidatos],
            "resena": {"event_id": e0["event_id"], "estrellas": estrellas, "comentario": comentario}}


def responder_mensaje_libre(text: str | None = None, audio_bytes: bytes | None = None) -> dict:
    """Todo lo que NO sea un comando (/agenda...) ni un botón pasa por aquí.
    -> {"texto": respuesta para enviar,
        "eventos": [{"id","title","starts_at","municipio","status"}] (para botones insc:{id}),
        "intencion": ..., "transcripcion": lo que dijo (si era audio),
        "resena"?: {"event_id","estrellas","comentario"} -> si viene, B2 enseña el botón
                   rate:{event_id}:{estrellas} y, al pulsarlo, guarda también el comentario}
    Nunca lanza excepciones."""
    try:
        e = ia.entender_mensaje(text=text, audio_bytes=audio_bytes)
        dicho = (e.get("texto") or text or "").strip()
        n = re.sub(r"^[¿¡\s]+", "", ia._norm(dicho))
        base = {"eventos": [], "intencion": e.get("intencion"), "transcripcion": dicho}

        if not dicho:
            return {**base, "texto": "No te he entendido bien. " + TEXTO_AYUDA}
        if _SALUDO.match(n) and len(n.split()) <= 4:
            return {**base, "texto": "¡Hola! Soy el asistente de la agenda de los pueblos. " + TEXTO_AYUDA}
        if _GRACIAS.match(n) and len(n.split()) <= 4:
            return {**base, "texto": "¡De nada! Aquí estoy para lo que necesites."}
        resena = _detectar_resena(dicho)
        if resena:
            return {**base, **resena}
        if e.get("intencion") == "proponer":
            return {**base, "texto": "¡Qué buena idea! Para proponer un plan escribe /proponer, "
                                     "o mándame el cartel o una nota de voz con qué, dónde y cuándo."}
        if e.get("intencion") == "otro" and len(n.split()) <= 2:
            return {**base, "texto": "No te he entendido bien. " + TEXTO_AYUDA}

        # buscar / pregunta / apuntarse / otro largo -> asistente de la agenda
        r = preguntar_agenda(dicho)
        texto = r["respuesta"]
        if e.get("intencion") == "apuntarse" and r["eventos"]:
            texto += "\n\nPulsa el botón del que quieras para apuntarte."
        return {**base, "texto": texto, "eventos": r["eventos"][:5]}
    except Exception:
        log.exception("responder_mensaje_libre falló")
        return {"texto": "Ahora mismo no puedo responder. Prueba con /agenda.",
                "eventos": [], "intencion": "otro", "transcripcion": text or ""}


# ══════════════════════════════════════════════════════════════
#  POST /ia/preguntar  (público: también lo pueden usar los visitantes)
# ══════════════════════════════════════════════════════════════
class Pregunta(BaseModel):
    pregunta: str


# Protección de cuota (la ruta es pública):
#  - respuesta guardada 10 min para preguntas repetidas (misma pregunta = 0 llamadas)
#  - límite por IP: 10 preguntas/minuto y 100/día
_RESPUESTAS: dict[str, tuple[float, dict]] = {}
_USO_IP: dict[str, list[float]] = {}
MAX_MINUTO, MAX_DIA, CACHE_S = 10, 100, 600


def _ip(request: Request) -> str:
    reenviada = request.headers.get("x-forwarded-for", "")  # Render va detrás de un proxy
    return reenviada.split(",")[0].strip() or (request.client.host if request.client else "?")


@router.post("/ia/preguntar")
def ia_preguntar(body: Pregunta, request: Request):
    pregunta = (body.pregunta or "").strip()
    if not pregunta:
        raise HTTPException(400, "Pregunta vacía")
    ahora = time.time()

    clave = re.sub(r"[^a-z0-9ñ ]", "", ia._norm(pregunta))
    guardada = _RESPUESTAS.get(clave)
    if guardada and ahora - guardada[0] < CACHE_S:
        return guardada[1]

    usos = [t for t in _USO_IP.get(_ip(request), []) if ahora - t < 86400]
    if len(usos) >= MAX_DIA or sum(1 for t in usos if ahora - t < 60) >= MAX_MINUTO:
        raise HTTPException(429, "Demasiadas preguntas seguidas. Espera un poco, por favor.")
    usos.append(ahora)
    _USO_IP[_ip(request)] = usos

    respuesta = preguntar_agenda(pregunta)
    if len(_RESPUESTAS) > 500:
        _RESPUESTAS.clear()
    _RESPUESTAS[clave] = (ahora, respuesta)
    return respuesta


@router.post("/ia/indexar")
def ia_indexar(user=Depends(require_moderador)):
    """🛡️ Solo moderador (pestaña Demo del panel): indexa ya lo pendiente."""
    return {"eventos": indexar_eventos_pendientes(), "conocimiento": indexar_conocimiento()}
