"""B1 · GET /eventos/{id}.ics y POST /eventos/{id}/avisar 🔒"""
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel

from db import evento, perfil, require_user, sb
from ics import fecha_es, generar_ics
from puentes import avisar

router = APIRouter(tags=["eventos"])

# Estados que puede ver cualquiera (igual que la regla RLS de la BD)
VISIBLES = {"abierto", "completo", "inscripcion_cerrada", "en_curso",
            "pospuesto", "cancelado", "finalizado"}


@router.get("/eventos/{event_id}.ics")
def descargar_ics(event_id: str):
    ev = evento(event_id)
    if not ev or ev.get("status") not in VISIBLES:
        raise HTTPException(404, "Evento no encontrado")
    return Response(
        content=generar_ics(ev),
        media_type="text/calendar; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="evento-{event_id}.ics"'},
    )


class AvisarIn(BaseModel):
    tipo: Literal["aprobado", "rechazado", "cambio_hora", "cancelacion"]


def _texto(tipo: str, ev: dict) -> str:
    titulo = ev["title"]
    cuando = fecha_es(ev["starts_at"])
    donde = ", ".join(x for x in [ev.get("location_name"), ev.get("municipio")] if x)
    if tipo == "aprobado":
        return f"✅ Tu plan «{titulo}» ya está publicado. Es el {cuando} en {donde}."
    if tipo == "rechazado":
        nota = f"\nMotivo: {ev['moderation_note']}" if ev.get("moderation_note") else ""
        return f"❌ Tu plan «{titulo}» no se ha publicado.{nota}"
    if tipo == "cambio_hora":
        return (f"🕒 Cambio en «{titulo}»: ahora es el {cuando} en {donde}.\n"
                f"Pulsa Calendario para actualizarlo en tu móvil.")
    return f"🚫 Se ha cancelado «{titulo}» (era el {cuando}). Pulsa Calendario para quitarlo."


@router.post("/eventos/{event_id}/avisar")
def avisar_evento(event_id: str, body: AvisarIn, user=Depends(require_user)):
    ev = evento(event_id)
    if not ev:
        raise HTTPException(404, "Evento no encontrado")

    # La service_role salta RLS: compruebo yo que es moderador o el autor
    yo = perfil(user.id) or {}
    if yo.get("rol") != "moderador" and ev.get("created_by") != user.id:
        raise HTTPException(403, "Solo el moderador o el autor pueden avisar")

    if body.tipo in ("aprobado", "rechazado"):
        destinatarios = [ev["created_by"]] if ev.get("created_by") else []
    else:
        filas = (
            sb.table("inscripciones").select("user_id")
            .eq("event_id", event_id)
            .in_("status", ["confirmada", "lista_de_espera"])
            .execute().data
        )
        destinatarios = list({f["user_id"] for f in filas})

    texto = _texto(body.tipo, ev)
    botones = None if body.tipo == "rechazado" else [[("📅 Calendario", f"ics:{event_id}")]]
    ahora = datetime.now(timezone.utc).isoformat()

    enviados = 0
    for uid in destinatarios:
        try:
            if not avisar(uid, texto, botones):
                continue
            enviados += 1
            # upsert: un segundo cambio de hora también queda registrado (y avisa)
            sb.table("notificaciones").upsert(
                {"user_id": uid, "event_id": event_id, "tipo": body.tipo,
                 "canal": "telegram", "motivo": body.tipo, "sent_at": ahora},
                on_conflict="user_id,event_id,tipo",
            ).execute()
        except Exception as e:  # si uno falla, siguen los demás
            print(f"[avisar] error con {uid}: {e}")

    return {"enviados": enviados}
