"""B1 · POST /resenas/pedir/{event_id} -> {enviados: n}

Pone el evento en 'finalizado' si ya pasó y pide estrellas a los confirmados.
Los botones rate:{event_id}:{1-5} los recoge B2, que debe comprobar que el
usuario estaba inscrito antes de insertar en resenas (la service_role salta esa regla).
"""
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException

from db import evento, sb
from ics import parse_fecha
from puentes import avisar

router = APIRouter(tags=["resenas"])


@router.post("/resenas/pedir/{event_id}")
def pedir_resenas(event_id: str):
    ev = evento(event_id)
    if not ev:
        raise HTTPException(404, "Evento no encontrado")

    fin = parse_fecha(ev.get("ends_at")) or parse_fecha(ev["starts_at"])
    if fin < datetime.now(timezone.utc) and ev["status"] in ("abierto", "pospuesto"):
        sb.table("eventos").update({"status": "finalizado"}).eq("id", event_id).execute()

    inscritos = sb.table("inscripciones").select("user_id") \
        .eq("event_id", event_id).eq("status", "confirmada").execute().data

    texto = f"⭐ ¿Qué tal fue «{ev['title']}»? Puntúalo de 1 a 5 estrellas:"
    botones = [[(f"{n}⭐", f"rate:{event_id}:{n}") for n in range(1, 6)]]

    enviados = 0
    for f in inscritos:
        uid = f["user_id"]
        try:
            if not avisar(uid, texto, botones):
                continue
            enviados += 1
            sb.table("notificaciones").upsert(
                {"user_id": uid, "event_id": event_id, "tipo": "resena", "canal": "telegram",
                 "motivo": "petición de reseña", "sent_at": datetime.now(timezone.utc).isoformat()},
                on_conflict="user_id,event_id,tipo",
            ).execute()
        except Exception as e:
            print(f"[resenas] error con {uid}: {e}")

    return {"enviados": enviados}
