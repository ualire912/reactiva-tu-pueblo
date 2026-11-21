"""B1 · Tareas automáticas en segundo plano (cada 5 minutos, sin que nadie pulse nada).

  1. Estados de eventos por tiempo/plazas/fecha límite  (plan B de pg_cron, que lo hace cada minuto)
  2. Pedir reseña de los eventos que acaban de terminar (últimas 24 h, una sola vez)
  3. Revisar con IA a los candidatos a Publicador nuevos (los dudosos van al moderador)
  4. Indexar para el asistente (RAG de C1) los eventos nuevos o con contenido cambiado
  5. Avisar al autor cuando un moderador aprueba o rechaza su propuesta (también desde el panel web)
  6. Cada 6 horas: el agente de eventos reales (Facebook con Apify + noticias)

Cada paso va en su propio try/except: si uno falla, los demás siguen.
Render gratuito se duerme sin visitas: cron-job.org a /health cada 10 min lo mantiene despierto.
"""
import logging
import threading
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


_AGENTE = {"ultimo": 0.0}
CADA_AGENTE = 6 * 3600  # el agente de eventos reales, cada 6 horas


def _paso_agente():
    """Facebook (si hay APIFY_TOKEN y páginas) + noticias -> eventos reales. Cada 6 h."""
    if time.time() - _AGENTE["ultimo"] < CADA_AGENTE:
        return
    _AGENTE["ultimo"] = time.time()
    import os
    import sys
    carpeta = os.path.join(os.path.dirname(os.path.abspath(__file__)), "scraper")
    if carpeta not in sys.path:
        sys.path.insert(0, carpeta)
    import agente_eventos
    r = agente_eventos.ejecutar()
    log.info("[tareas] agente de eventos: %s", r)


def ciclo():
    for paso in (_paso_estados, _paso_resenas, _paso_promociones, _paso_rag,
                 _paso_avisos_moderacion, _paso_agente):
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
