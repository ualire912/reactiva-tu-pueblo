"""B1 · Promoción a Publicador.

Cómo funciona (nadie tiene que pulsar nada):
  1. La BD marca candidatos por reglas (v_candidatos_publicador) con cada reseña
     o incidencia, y deja promocion_revisada_at = null ("pendiente de revisar").
  2. tareas.py llama a revisar_candidatos(solo_nuevos=True) cada 5 minutos.
  3. La IA revisa a cada candidato nuevo:
       - aprueba con confianza   -> pasa a 'publicador' y se le avisa  (AUTOMÁTICO)
       - duda / falla / no hay IA -> sigue como candidato con el motivo -> lo decide el moderador
  4. El moderador ve el motivo en la pestaña Publicadores y decide con /decidir.

Endpoints (🛡️ solo moderador):
  POST /moderacion/promociones/revisar   -> revisa YA a todos los candidatos (botón del panel/demo)
  POST /moderacion/promociones/decidir   {user_id, accion: promover|descartar|revocar}
"""
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from db import perfil, require_moderador, sb
from puentes import avisar, revisar_confianza

router = APIRouter(tags=["moderacion"])

MSG_PROMOVIDO = "🎉 Ya eres Publicador/a: tus planes se publican sin esperar."
MSG_REVOCADO = "ℹ️ A partir de ahora tus planes volverán a revisarse antes de publicarse."


def _ahora() -> str:
    return datetime.now(timezone.utc).isoformat()


def _promover(uid: str, motivo: str):
    sb.table("profiles").update({
        "rol": "publicador", "promocion_pendiente": False,
        "promocion_motivo": motivo, "promocion_revisada_at": _ahora(),
    }).eq("id", uid).execute()
    avisar(uid, MSG_PROMOVIDO)  # si Telegram falla, la promoción ya está hecha


def _marcar_revisado(uid: str, motivo: str):
    sb.table("profiles").update({"promocion_motivo": motivo, "promocion_revisada_at": _ahora()}) \
        .eq("id", uid).execute()


def revisar_candidatos(solo_nuevos: bool = True) -> dict:
    """La usan tareas.py (solo_nuevos=True) y el endpoint (todos). No lanza excepciones."""
    q = sb.table("v_candidatos_publicador").select("id, full_name, promocion_motivo")
    if solo_nuevos:
        q = q.is_("promocion_revisada_at", "null")
    candidatos = q.execute().data

    promovidos, a_revisar = [], []
    for c in candidatos:
        uid = c["id"]
        item = {"user_id": uid, "full_name": c.get("full_name")}
        try:
            # Regla antes que IA: si el moderador ya lo descartó o revocó, la IA no
            # lo promueve sola (y no se gastan tokens). Decide el moderador.
            previo = c.get("promocion_motivo") or ""
            if previo.startswith(("Descartado", "Revocado")):
                motivo = previo.split(" · ")[0] + " · tiene reseñas nuevas: decide el moderador"
                _marcar_revisado(uid, motivo)
                item.update(motivo=motivo, con_ia=False)
                a_revisar.append(item)
                continue

            p = perfil(uid) or {}
            eventos = sb.table("eventos") \
                .select("id, title, category, starts_at, status, municipio_id") \
                .eq("created_by", uid).execute().data
            ids = [e["id"] for e in eventos]
            resenas = sb.table("resenas").select("event_id, estrellas, comentario") \
                .in_("event_id", ids).execute().data if ids else []

            # Solo datos útiles para la IA (sin email, teléfono ni dirección)
            resumen = {k: p.get(k) for k in ("full_name", "rol", "reputation_score", "created_at")}
            r = revisar_confianza(resumen, eventos, resenas)
            item.update(motivo=r["motivo"], con_ia=r["con_ia"])

            if r["aprobar"]:
                _promover(uid, "IA: " + r["motivo"])
                promovidos.append(item)
            else:
                _marcar_revisado(uid, ("IA: " if r["con_ia"] else "") + r["motivo"])
                a_revisar.append(item)
        except Exception as e:
            print(f"[promociones] error con {uid}: {e!r}")
            item.update(motivo="No se pudo revisar automáticamente: decide el moderador", con_ia=False)
            a_revisar.append(item)

    return {"promovidos": promovidos, "a_revisar": a_revisar}


@router.post("/moderacion/promociones/revisar")
def revisar_promociones(_mod=Depends(require_moderador)):
    return revisar_candidatos(solo_nuevos=False)


class DecidirIn(BaseModel):
    user_id: str
    accion: Literal["promover", "descartar", "revocar"]


@router.post("/moderacion/promociones/decidir")
def decidir_promocion(body: DecidirIn, _mod=Depends(require_moderador)):
    p = perfil(body.user_id)
    if not p:
        raise HTTPException(404, "Usuario no encontrado")
    if p["rol"] == "moderador":
        raise HTTPException(400, "No se puede cambiar el rol de un moderador desde aquí")

    if body.accion == "promover":
        if p["rol"] != "publicador":
            _promover(body.user_id, "Aprobado por el moderador")
        return {"ok": True, "rol": "publicador"}

    if body.accion == "descartar":
        # Deja de ser candidato. Si recibe reseñas nuevas la BD puede volver a marcarlo,
        # pero entonces la IA no lo promueve sola: vuelve al moderador.
        sb.table("profiles").update({
            "promocion_pendiente": False,
            "promocion_motivo": "Descartado por el moderador",
            "promocion_revisada_at": _ahora(),
        }).eq("id", body.user_id).execute()
        return {"ok": True, "rol": p["rol"]}

    # revocar
    if p["rol"] == "publicador":
        sb.table("profiles").update({
            "rol": "usuario", "promocion_pendiente": False,
            "promocion_motivo": "Revocado por el moderador", "promocion_revisada_at": _ahora(),
        }).eq("id", body.user_id).execute()
        avisar(body.user_id, MSG_REVOCADO)
    return {"ok": True, "rol": "usuario"}
