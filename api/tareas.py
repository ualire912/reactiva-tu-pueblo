"""B1 · Tareas automáticas en segundo plano (cada 5 minutos, sin que nadie pulse nada).

  1. Estados de eventos por tiempo/plazas/fecha límite  (plan B de pg_cron, que lo hace cada minuto)
  2. Pedir reseña de los eventos que acaban de terminar (últimas 24 h, una sola vez)
  3. Revisar con IA a los candidatos a Publicador nuevos (los dudosos van al moderador)

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


def ciclo():
    for paso in (_paso_estados, _paso_resenas, _paso_promociones):
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
