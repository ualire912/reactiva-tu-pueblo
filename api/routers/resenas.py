"""B1 · Petición de reseñas.

AUTOMÁTICO: tareas.py pide reseña de cada evento que termina (en las 24 h siguientes).
MANUAL:     POST /resenas/pedir/{event_id} 🔒 moderador o autor del evento -> {enviados: n}
            (para repetir la petición o para la demo).

El evento pasa a 'finalizado' SOLO, por tiempo (lo hace la BD). Aquí no se toca el estado.
Los botones rate:{event_id}:{1-5} los recoge B2, que debe comprobar que el
usuario estaba inscrito y confirmado antes de insertar en resenas.
"""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException

from db import evento, perfil, require_user, sb
from puentes import avisar

router = APIRouter(tags=["resenas"])


def pedir_resenas_evento(ev: dict) -> int:
    """Manda la petición de estrellas a los inscritos confirmados. Devuelve cuántos."""
    inscritos = sb.table("inscripciones").select("user_id") \
        .eq("event_id", ev["id"]).eq("status", "confirmada").execute().data

    texto = f"⭐ ¿Qué tal fue «{ev['title']}»? Puntúalo de 1 a 5 estrellas:"
    botones = [[(f"{n}⭐", f"rate:{ev['id']}:{n}") for n in range(1, 6)]]

    enviados = 0
    for f in inscritos:
        uid = f["user_id"]
        try:
            if not avisar(uid, texto, botones):
                continue
            enviados += 1
            sb.table("notificaciones").upsert(
                {"user_id": uid, "event_id": ev["id"], "tipo": "resena", "canal": "telegram",
                 "motivo": "petición de reseña", "sent_at": datetime.now(timezone.utc).isoformat()},
                on_conflict="user_id,event_id,tipo",
            ).execute()
        except Exception as e:  # si uno falla, siguen los demás
            print(f"[resenas] error con {uid}: {e!r}")
    return enviados


@router.post("/resenas/pedir/{event_id}")
def pedir_resenas(event_id: str, user=Depends(require_user)):
    ev = evento(event_id)
    if not ev:
        raise HTTPException(404, "Evento no encontrado")
    if (perfil(user.id) or {}).get("rol") != "moderador" and ev.get("created_by") != user.id:
        raise HTTPException(403, "Solo el moderador o el autor pueden pedir reseñas")

    if ev["status"] != "finalizado":
        # por si el estado aún no se ha recalculado (pg_cron cada minuto)
        try:
            sb.rpc("actualizar_estados_eventos", {}).execute()
            ev = evento(event_id) or ev
        except Exception as e:
            print(f"[resenas] no se pudieron actualizar estados: {e!r}")
    if ev["status"] != "finalizado":
        raise HTTPException(409, "El evento aún no ha terminado")

    return {"enviados": pedir_resenas_evento(ev)}
