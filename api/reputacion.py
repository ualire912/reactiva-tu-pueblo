"""
api/reputacion.py · Reputación de quien organiza eventos y paso a PUBLICADOR.

Sistema de puntos (decidido por el equipo):
  1. De cada evento que ha creado se hace la MEDIA de sus reseñas y se redondea a estrellas:
        1 o 2 estrellas -> -1 punto · 3 estrellas -> 0 · 4 o 5 estrellas -> +0,5 puntos
  2. La reputación es la SUMA de esos puntos (aquí ya no se hace media).
  3. Al llegar a 4 puntos, un usuario normal pasa a ser PUBLICADOR: sus eventos se
     publican sin pasar por el moderador (la IA sigue revisando cada mensaje).

Se recalcula cada vez que alguien valora (bot y web) y en las tareas de cada 5 minutos.
La función de la BD (recalcular_reputacion) usa otra fórmula hasta que se ejecute
supabase/migracion_reputacion.sql; mientras, esto la corrige a los pocos minutos.
"""
from __future__ import annotations

import logging
import math

from db import sb

log = logging.getLogger("reactiva")

PUNTOS_PARA_PUBLICADOR = 4.0


def estrellas_del_evento(media: float) -> int:
    """Media de las reseñas -> estrellas (redondeo normal: 3,5 -> 4; 2,5 -> 3)."""
    return int(math.floor(media + 0.5))


def puntos_del_evento(media: float) -> float:
    e = estrellas_del_evento(media)
    if e <= 2:
        return -1.0
    if e == 3:
        return 0.0
    return 0.5


def calcular(user_id: str) -> tuple[float, list[dict]]:
    """-> (reputación, detalle por evento) de quien creó los eventos."""
    evs = sb.table("eventos").select("id,title").eq("created_by", user_id).execute().data or []
    if not evs:
        return 0.0, []
    ids = [e["id"] for e in evs]
    res = sb.table("resenas").select("event_id,estrellas").in_("event_id", ids).execute().data or []
    por_evento: dict[str, list[int]] = {}
    for r in res:
        por_evento.setdefault(r["event_id"], []).append(int(r["estrellas"]))
    detalle, total = [], 0.0
    for e in evs:
        notas = por_evento.get(e["id"])
        if not notas:
            continue  # sin reseñas no suma ni resta
        media = sum(notas) / len(notas)
        p = puntos_del_evento(media)
        total += p
        detalle.append({"evento": e["title"], "media": round(media, 2),
                        "estrellas": estrellas_del_evento(media), "puntos": p})
    return round(total, 2), detalle


def recalcular(user_id: str | None, avisar_si_sube: bool = True) -> float | None:
    """Guarda la reputación y, si llega a 4 puntos, lo hace publicador (y se lo dice)."""
    if not user_id:
        return None
    try:
        total, _ = calcular(user_id)
        p = (sb.table("profiles").select("rol,reputation_score").eq("id", user_id)
             .execute().data or [{}])[0]
        cambios: dict = {}
        if float(p.get("reputation_score") or 0) != total:
            cambios["reputation_score"] = total
        sube = p.get("rol") == "usuario" and total >= PUNTOS_PARA_PUBLICADOR
        if sube:
            cambios.update({"rol": "publicador", "promocion_pendiente": False,
                            "promocion_motivo": f"Reputación de {total:g} puntos (mínimo "
                                                f"{PUNTOS_PARA_PUBLICADOR:g}) por las reseñas de sus eventos"})
        if cambios:
            sb.table("profiles").update(cambios).eq("id", user_id).execute()
        if sube:
            log.info("[reputacion] %s pasa a publicador (%s puntos)", user_id, total)
            if avisar_si_sube:
                try:
                    from puentes import avisar
                    avisar(user_id, f"🎉 ¡Enhorabuena! Las reseñas de tus planes te han dado {total:g} "
                                    "puntos de reputación y ya eres PUBLICADOR: a partir de ahora tus "
                                    "planes se publican directamente, sin esperar al moderador.")
                except Exception as e:
                    log.warning("[reputacion] no se pudo avisar: %r", e)
        return total
    except Exception as e:
        log.error("[reputacion] recalcular falló para %s: %r", user_id, e)
        return None


def recalcular_del_evento(event_id: str) -> None:
    """Tras una reseña: recalcula a quien creó ese evento."""
    ev = sb.table("eventos").select("created_by").eq("id", event_id).execute().data
    if ev and ev[0].get("created_by"):
        recalcular(ev[0]["created_by"])


def recalcular_todos() -> int:
    """Todos los que han creado algún evento con reseñas. -> cuántos."""
    res = sb.table("resenas").select("event_id").execute().data or []
    ids = list({r["event_id"] for r in res})
    if not ids:
        return 0
    autores = set()
    for i in range(0, len(ids), 100):
        for e in sb.table("eventos").select("created_by").in_("id", ids[i:i + 100]) \
                .not_.is_("created_by", "null").execute().data or []:
            autores.add(e["created_by"])
    for a in autores:
        recalcular(a)
    return len(autores)
