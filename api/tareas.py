"""B1 · Tareas automáticas en segundo plano (cada 5 minutos, sin que nadie pulse nada).

  1. Estados de eventos por tiempo/plazas/fecha límite  (plan B de pg_cron, que lo hace cada minuto)
  2. Pedir reseña de los eventos que acaban de terminar (últimas 24 h, una sola vez)
  3. Reputación de quien organiza (puntos por las reseñas) y paso a Publicador con 4 puntos
  4. Indexar para el asistente (RAG de C1) los eventos nuevos o con contenido cambiado
  5. Avisar al autor cuando un moderador aprueba o rechaza su propuesta (también desde el panel web)
  6. Recordatorio por Telegram el día antes a cada apuntado
  7. Recomendaciones de planes parecidos a los que ha ido (como mucho 1 mensaje al día)
  8. Cada 6 horas: el agente de eventos reales (Facebook con Apify + noticias)

Cada paso va en su propio try/except: si uno falla, los demás siguen.
Render gratuito se duerme sin visitas: cron-job.org a /health cada 10 min lo mantiene despierto.
"""
import logging
import threading
import os
import time
from datetime import datetime, timedelta, timezone

log = logging.getLogger("reactiva")

INTERVALO = 300       # segundos entre ciclos
ESPERA_INICIAL = 30   # deja arrancar el servidor antes del primer ciclo


def _paso_estados():
    from db import sb
    n = sb.rpc("actualizar_estados_eventos", {}).execute().data
    if n:
        log.info("[tareas] %s eventos cambiaron de estado", n)


def _paso_resenas():
    from db import sb
    from routers.resenas import pedir_resenas_evento

    fmt = "%Y-%m-%dT%H:%M:%SZ"
    ahora = datetime.now(timezone.utc)
    desde = ahora - timedelta(hours=24)
    # Con fecha de fin: terminó en las últimas 24 h
    eventos = sb.table("eventos").select("id, title") \
        .eq("status", "finalizado") \
        .gte("ends_at", desde.strftime(fmt)).lte("ends_at", ahora.strftime(fmt)) \
        .execute().data
    # Sin fecha de fin: se da por terminado 2 h después de empezar (igual que la BD)
    eventos += sb.table("eventos").select("id, title") \
        .eq("status", "finalizado").is_("ends_at", "null") \
        .gte("starts_at", (desde - timedelta(hours=2)).strftime(fmt)) \
        .lte("starts_at", (ahora - timedelta(hours=2)).strftime(fmt)) \
        .execute().data
    for ev in eventos:
        ya = sb.table("notificaciones").select("id").eq("event_id", ev["id"]) \
            .eq("tipo", "resena").limit(1).execute().data
        if not ya:
            n = pedir_resenas_evento(ev)
            log.info("[tareas] reseñas pedidas para «%s»: %s", ev["title"], n)


def _paso_promociones():
    from routers.moderacion import revisar_candidatos
    r = revisar_candidatos(solo_nuevos=True)
    if r["promovidos"] or r["a_revisar"]:
        log.info("[tareas] promovidos=%s a_revisar=%s", len(r["promovidos"]), len(r["a_revisar"]))


def _paso_rag():
    """C1: indexar (embeddings) los eventos nuevos o con contenido cambiado."""
    import rag  # si rag.py aún no está en main, el ImportError lo recoge ciclo()
    n = rag.indexar_eventos_pendientes(100)
    if n:
        log.info("[tareas] %s eventos indexados para el asistente", n)


def _paso_reputacion():
    """Puntos de reputación de quien organiza (media por evento -> -1 / 0 / +0,5, y se suman)
    y paso automático a publicador al llegar a 4. Sustituye a la promoción por IA anterior."""
    import reputacion
    n = reputacion.recalcular_todos()
    if n:
        log.debug("[tareas] reputación recalculada para %s organizadores", n)


def _paso_propuestas_atascadas():
    """Propuestas que se quedaron a medias (p. ej. el servidor se reinició al desplegar justo
    mientras se procesaban): se reintentan y se le contesta al autor. Lo que llegó por
    Facebook o noticias se reprocesa sin avisar a nadie."""
    from db import sb
    from puentes import _con_limite

    ahora = datetime.now(timezone.utc)
    filas = sb.table("ingestas").select("id,origen,estado,created_at") \
        .in_("estado", ["nueva", "error"]) \
        .lte("created_at", (ahora - timedelta(minutes=3)).isoformat()) \
        .gte("created_at", (ahora - timedelta(days=2)).isoformat()).limit(10).execute().data or []
    for f in filas:
        # los "error" solo durante la primera hora (p. ej. la IA estaba caída); luego, al panel
        if f["estado"] == "error" and f["created_at"] < (ahora - timedelta(hours=1)).isoformat():
            continue
        try:
            if f["origen"] == "telegram":
                from bot import reanudar_propuesta
                _con_limite(120, reanudar_propuesta, f["id"])
            else:
                from ingesta import procesar_ingesta
                procesar_ingesta(f["id"])
            log.info("[tareas] propuesta atascada reintentada: %s (%s)", f["id"], f["origen"])
        except Exception as e:
            log.error("[tareas] no se pudo reintentar %s: %r", f["id"], e)


def _paso_avisos_moderacion():
    """El moderador aprobó o rechazó una propuesta (en el panel web o por Telegram):
    se avisa al autor UNA vez (registro en 'notificaciones', tipo 'moderacion')."""
    from db import sb
    from puentes import avisar

    desde = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    evs = sb.table("eventos").select("id,title,status,created_by,moderation_note,ai_flags") \
        .not_.is_("created_by", "null").gte("created_at", desde) \
        .in_("status", ["abierto", "completo", "inscripcion_cerrada", "rechazado"]).execute().data or []
    if not evs:
        return
    autores = {p["id"]: p.get("rol") for p in sb.table("profiles").select("id,rol")
               .in_("id", list({e["created_by"] for e in evs})).execute().data or []}
    for ev in evs:
        revisado = isinstance(ev.get("ai_flags"), dict) and ev["ai_flags"].get("revision")
        if not revisado and autores.get(ev["created_by"]) != "usuario":
            continue  # lo publicó él mismo (publicador/moderador): no hay nada que avisar
        ya = sb.table("notificaciones").select("id").eq("user_id", ev["created_by"]) \
            .eq("event_id", ev["id"]).eq("tipo", "moderacion").limit(1).execute().data
        if ya:
            continue
        if ev["status"] == "rechazado":
            texto = (f"🚫 Tu plan «{ev['title']}» no se ha publicado.\n"
                     f"Motivo: {ev.get('moderation_note') or 'no cumple las normas de la agenda'}")
        else:
            texto = f"🎉 ¡Tu plan «{ev['title']}» ya está publicado! Los vecinos ya pueden apuntarse."
        if avisar(ev["created_by"], texto):
            sb.table("notificaciones").upsert(
                {"user_id": ev["created_by"], "event_id": ev["id"], "tipo": "moderacion",
                 "canal": "telegram", "motivo": ev["status"]},
                on_conflict="user_id,event_id,tipo").execute()
            log.info("[tareas] avisado el autor de «%s» (%s)", ev["title"], ev["status"])


def _fecha_txt(iso: str) -> str:
    try:
        from bot import _format_fecha_es
        return _format_fecha_es(iso)
    except Exception:
        return str(iso)[:16].replace("T", " ")


def _paso_recordatorios():
    """Un día antes del evento: '⏰ Mañana tienes…' a cada apuntado (una sola vez)."""
    from db import sb
    from puentes import avisar

    ahora = datetime.now(timezone.utc)
    evs = sb.table("eventos").select("id,title,starts_at,location_name,municipio_id") \
        .in_("status", ["abierto", "completo", "inscripcion_cerrada"]) \
        .gte("starts_at", (ahora + timedelta(hours=18)).isoformat()) \
        .lte("starts_at", (ahora + timedelta(hours=30)).isoformat()).execute().data or []
    for ev in evs:
        apuntados = sb.table("inscripciones").select("user_id").eq("event_id", ev["id"]) \
            .eq("status", "confirmada").execute().data or []
        ya = {f["user_id"] for f in sb.table("notificaciones").select("user_id")
              .eq("event_id", ev["id"]).eq("tipo", "recordatorio").execute().data or []}
        pueblo = ""
        if ev.get("municipio_id"):
            m = sb.table("municipios").select("nombre").eq("id", ev["municipio_id"]).execute().data
            pueblo = m[0]["nombre"] if m else ""
        donde = ", ".join(x for x in (ev.get("location_name"), pueblo) if x)
        for a in apuntados:
            if a["user_id"] in ya:
                continue
            texto = (f"⏰ ¡Mañana tienes «{ev['title']}»!\n\n📅 {_fecha_txt(ev['starts_at'])}"
                     + (f"\n📍 {donde}" if donde else "") + "\n\n¡Te esperamos! Si al final no puedes ir, "
                     "bórrate para dejar la plaza a otra persona.")
            botones = [[("📅 Calendario", f"ics:{ev['id']}"), ("❌ Borrarme", f"cancel:{ev['id']}")],
                       [("🚗 Llevo coche", f"coche:ofrece:{ev['id']}"),
                        ("🙋 Busco coche", f"coche:busca:{ev['id']}")]]
            if avisar(a["user_id"], texto, botones, tipo="recordatorio"):
                sb.table("notificaciones").upsert(
                    {"user_id": a["user_id"], "event_id": ev["id"], "tipo": "recordatorio",
                     "canal": "telegram", "motivo": "un día antes"},
                    on_conflict="user_id,event_id,tipo").execute()
                log.info("[tareas] recordatorio de «%s» enviado", ev["title"])


_GENERICAS = {"evento", "eventos", "fiesta", "fiestas", "jornada", "jornadas", "edicion", "solidario",
              "solidaria", "municipal", "popular", "lucainena", "torres", "senes", "tahal", "uleila",
              "campo", "velefique", "sierra", "verano", "invierno", "navidad", "navideno", "navidena"}


def _palabras(titulo: str) -> set[str]:
    import re
    import unicodedata
    t = unicodedata.normalize("NFD", titulo or "")
    t = "".join(c for c in t if unicodedata.category(c) != "Mn").lower()
    return {w for w in re.findall(r"[a-z]{5,}", t) if w not in _GENERICAS}


def recomendar_parecidos(user_id: str, limite: int = 2) -> list[dict]:
    """Planes próximos parecidos a los que ha ido o se ha apuntado: mismo tipo de plan en el
    título ("mercadillo" -> "mercadillo"), misma categoría y su pueblo. Con el motivo."""
    from db import sb

    hist = sb.table("inscripciones").select("event_id,eventos(title,category,municipio_id,starts_at)") \
        .eq("user_id", user_id).eq("status", "confirmada").execute().data or []
    if not hist:
        return []
    apuntado = {h["event_id"] for h in sb.table("inscripciones").select("event_id")
                .eq("user_id", user_id).execute().data or []}
    ya = {f["event_id"] for f in sb.table("notificaciones").select("event_id")
          .eq("user_id", user_id).eq("tipo", "recomendacion").execute().data or []}
    perfil = (sb.table("profiles").select("municipio_id").eq("id", user_id).execute().data or [{}])[0]
    ahora = datetime.now(timezone.utc)
    futuros = sb.table("eventos").select("id,title,category,municipio_id,starts_at") \
        .eq("status", "abierto").gte("starts_at", ahora.isoformat()) \
        .lte("starts_at", (ahora + timedelta(days=21)).isoformat()).execute().data or []
    recs = []
    for ev in futuros:
        if ev["id"] in apuntado or ev["id"] in ya:
            continue
        mejor, motivo, puntos = None, "", 0
        for h in hist:
            antes = h.get("eventos") or {}
            comunes = _palabras(ev["title"]) & _palabras(antes.get("title", ""))
            p = 3 * len(comunes) + (1 if ev["category"] == antes.get("category") else 0)
            if p > puntos:
                mejor, puntos = antes, p
                ya_fue = str(antes.get("starts_at") or "") < ahora.isoformat()
                motivo = ((f"como fuiste a «{antes.get('title')}»" if ya_fue
                           else f"como te apuntaste a «{antes.get('title')}»") if comunes
                          else f"te gustan los planes de {ev['category']}")
        if puntos and ev.get("municipio_id") in (perfil.get("municipio_id"),
                                                  (mejor or {}).get("municipio_id")):
            puntos += 1
        if puntos >= 1:
            recs.append({**ev, "puntos": puntos, "motivo": motivo})
    recs.sort(key=lambda r: (-r["puntos"], r["starts_at"]))
    return recs[:limite]


def _paso_recomendaciones():
    """Como mucho un mensaje al día por persona con planes parecidos a los suyos."""
    from db import sb
    from puentes import avisar

    usuarios = sb.table("profiles").select("id,full_name").eq("telegram_linked", True) \
        .eq("notificaciones_activas", True).execute().data or []
    hace_un_dia = (datetime.now(timezone.utc) - timedelta(hours=20)).isoformat()
    for u in usuarios:
        reciente = sb.table("notificaciones").select("id").eq("user_id", u["id"]) \
            .eq("tipo", "recomendacion").gte("sent_at", hace_un_dia).limit(1).execute().data
        if reciente:
            continue
        recs = recomendar_parecidos(u["id"])
        if not recs:
            continue
        nombre = (u.get("full_name") or "").split()[0] if u.get("full_name") else ""
        lineas = [f"• «{r['title']}» — {_fecha_txt(r['starts_at'])} ({r['motivo']})" for r in recs]
        texto = (f"✨ {'¡Hola, ' + nombre + '! ' if nombre else ''}Te pueden gustar estos planes:\n\n"
                 + "\n".join(lineas))
        botones = [[(f"✅ Apuntarme: {r['title'][:22]}", f"insc:{r['id']}")] for r in recs]
        if avisar(u["id"], texto, botones, tipo="recomendacion"):
            sb.table("notificaciones").upsert(
                [{"user_id": u["id"], "event_id": r["id"], "tipo": "recomendacion",
                  "canal": "telegram", "motivo": r["motivo"]} for r in recs],
                on_conflict="user_id,event_id,tipo", ignore_duplicates=True).execute()
            log.info("[tareas] recomendación enviada a %s (%s planes)", u["id"][:8], len(recs))


_AGENTE = {"ultimo": 0.0, "completo": 0.0, "descubrir": 0.0, "corriendo": False, "inicio": None, "fin": None,
           "motivo": None, "resultado": None, "error": None}
# Facebook: cada hora se busca el nombre de un pueblo (por turnos: Senés, Lucainena...),
# y una vez al día se leen las páginas de las organizaciones y las webs de los ayuntamientos.
# El moderador puede lanzar la búsqueda al momento desde el panel ("Buscar ahora").
# Más a menudo: AGENTE_CADA_MIN en Render (minutos). Cada búsqueda gasta crédito de Apify.
CADA_AGENTE = int(os.environ.get("AGENTE_CADA_MIN", "60")) * 60
CADA_AGENTE_COMPLETO = 24 * 3600


def _ahora_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def lanzar_agente(motivo: str, completo: bool = False, consultas: list[str] | None = None,
                  urls: list[str] | None = None, guardar: bool = False,
                  descubrir: bool = False) -> bool:
    """Arranca una pasada del agente en su propio hilo. False si ya hay una en marcha."""
    if _AGENTE["corriendo"]:
        return False
    import sys
    carpeta = os.path.join(os.path.dirname(os.path.abspath(__file__)), "scraper")
    if carpeta not in sys.path:
        sys.path.insert(0, carpeta)

    def correr():
        try:
            import agente_eventos
            if urls:  # "Buscar ahora" con la dirección de una página o perfil
                r = agente_eventos.leer_direcciones(urls, guardar=guardar)
            elif consultas is not None:  # "Buscar ahora" del panel: solo esas búsquedas
                r = agente_eventos.ejecutar(semanas=2, solo="busqueda", consultas=consultas,
                                            descubrir=descubrir)
            else:
                # La pasada diaria lee además las páginas de las organizaciones, las webs de
                # los ayuntamientos y las noticias
                r = agente_eventos.ejecutar(semanas=6 if completo else 1, rapida=not completo,
                                            busqueda_completa=False, descubrir=descubrir)
            _AGENTE["resultado"] = r
            log.info("[tareas] agente de eventos (%s): %s", motivo, r)
        except BaseException as e:  # nada de lo que pase aquí para las demás tareas
            _AGENTE["error"] = f"{type(e).__name__}: {str(e)[:200]}"
            log.error("[tareas] agente de eventos falló: %r", e)
        finally:
            _AGENTE["fin"] = _ahora_iso()
            _AGENTE["corriendo"] = False

    _AGENTE.update(corriendo=True, inicio=_ahora_iso(), fin=None, motivo=motivo,
                   resultado=None, error=None)
    # En su propio hilo: Apify puede tardar minutos y los recordatorios no deben esperar
    threading.Thread(target=correr, name="agente-eventos", daemon=True).start()
    return True


def estado_agente() -> dict:
    """Para el panel del moderador: qué hizo la última búsqueda y cuándo toca la siguiente."""
    falta = CADA_AGENTE - (time.time() - _AGENTE["ultimo"]) if _AGENTE["ultimo"] else 0
    return {
        "apify_configurado": bool(os.environ.get("APIFY_TOKEN", "").strip()),
        "ia_configurada": bool(os.environ.get("GEMINI_API_KEY", "").strip()),
        "corriendo": _AGENTE["corriendo"],
        "inicio": _AGENTE["inicio"],
        "fin": _AGENTE["fin"],
        "motivo": _AGENTE["motivo"],
        "resultado": _AGENTE["resultado"],
        "error": _AGENTE["error"],
        "cada_minutos": CADA_AGENTE // 60,
        "proxima_en_minutos": max(0, int(falta // 60)),
        "detalle": _detalle(),
    }


def _detalle() -> list[dict]:
    try:
        import sys
        mod = sys.modules.get("agente_eventos")
        return list(getattr(mod, "DETALLE", []))[-200:] if mod else []
    except Exception:
        return []


def _paso_agente():
    """Facebook (si hay APIFY_TOKEN) + webs + noticias -> eventos reales."""
    ahora = time.time()
    if _AGENTE["corriendo"] or ahora - _AGENTE["ultimo"] < CADA_AGENTE:
        return
    completo = ahora - _AGENTE["completo"] >= CADA_AGENTE_COMPLETO
    descubrir = completo and ahora - _AGENTE["descubrir"] >= 7 * 24 * 3600  # páginas: 1 vez/semana
    if lanzar_agente("diaria" if completo else "cada hora", completo=completo, descubrir=descubrir):
        _AGENTE["ultimo"] = ahora
        if completo:
            _AGENTE["completo"] = ahora
        if descubrir:
            _AGENTE["descubrir"] = ahora


def ciclo():
    for paso in (_paso_estados, _paso_resenas, _paso_reputacion, _paso_rag,
                 _paso_propuestas_atascadas, _paso_avisos_moderacion, _paso_recordatorios, _paso_recomendaciones, _paso_agente):
        try:
            paso()
        except Exception as e:
            log.error("[tareas] %s falló: %r", paso.__name__, e)


def _bucle():
    time.sleep(ESPERA_INICIAL)
    while True:
        ciclo()
        time.sleep(INTERVALO)


def arrancar():
    threading.Thread(target=_bucle, name="tareas", daemon=True).start()
    log.info("[tareas] en marcha cada %s s", INTERVALO)
