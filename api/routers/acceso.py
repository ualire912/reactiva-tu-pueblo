"""Acceso a la web: crear cuenta con teléfono (correo opcional) y entrar con Telegram.

POST /auth/registro              {telefono, password, nombre, email?} -> {email_acceso}
     Crea la cuenta sin necesidad de correo ni SMS. El teléfono es el usuario:
     por dentro, Supabase usa un correo técnico tel<número>@reactiva.local
     (no se envía nada ahí); el correo real, si lo da, queda en su perfil.
POST /auth/telegram/iniciar      -> {codigo, enlace}
     La web abre t.me/<bot>?start=ENTRAR-<codigo>; al pulsar "Iniciar" el bot
     confirma quién es (api/bot.py) y deja un acceso de un solo uso.
GET  /auth/telegram/estado/{codigo} -> {listo: false} | {listo: true, token_hash}
     La web pregunta cada pocos segundos; con token_hash abre la sesión
     (supabase.auth.verifyOtp({token_hash, type: 'magiclink'})).
"""
import os
import re
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from fastapi import Depends

from db import require_user, sb

router = APIRouter(prefix="/auth", tags=["acceso"])

CADUCIDAD_LOGIN = timedelta(minutes=10)


# ── Teléfonos ─────────────────────────────────────────────────
def normalizar_telefono(texto: str | None) -> str | None:
    """'600 11 22 33' -> '+34600112233'. None si no parece un teléfono."""
    if not texto:
        return None
    t = texto.strip()
    digitos = re.sub(r"\D", "", t)
    if t.startswith("00"):
        digitos = digitos[2:]
    elif not t.startswith("+") and len(digitos) == 9 and digitos[0] in "6789":
        digitos = "34" + digitos  # número español sin prefijo
    if not 8 <= len(digitos) <= 15:
        return None
    return "+" + digitos


def email_de_acceso(telefono: str) -> str:
    """Correo técnico con el que Supabase identifica la cuenta (no recibe correos)."""
    return f"tel{telefono.lstrip('+')}@reactiva.local"


# ── Crear cuenta con teléfono ─────────────────────────────────
class Registro(BaseModel):
    telefono: str
    password: str
    nombre: str
    email: str | None = None


@router.post("/registro")
def registro(body: Registro):
    telefono = normalizar_telefono(body.telefono)
    if not telefono:
        raise HTTPException(400, "Escribe un número de teléfono válido (por ejemplo, 600 11 22 33).")
    if len(body.password or "") < 6:
        raise HTTPException(400, "La contraseña debe tener al menos 6 caracteres.")
    nombre = (body.nombre or "").strip()[:80]
    if not nombre:
        raise HTTPException(400, "Escribe tu nombre.")
    email = (body.email or "").strip().lower() or None
    if email and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        raise HTTPException(400, "Ese correo no parece válido. Puedes dejarlo vacío.")

    if sb.table("profiles").select("id").eq("telefono", telefono).limit(1).execute().data:
        raise HTTPException(409, "Ya hay una cuenta con ese teléfono. Inicia sesión.")

    acceso = email_de_acceso(telefono)
    try:
        res = sb.auth.admin.create_user({
            "email": acceso,
            "password": body.password,
            "email_confirm": True,  # no hay correo que confirmar
            "user_metadata": {"full_name": nombre, "telefono": telefono},
        })
    except Exception as e:
        if re.search(r"already|registered|exists", str(e), re.I):
            raise HTTPException(409, "Ya hay una cuenta con ese teléfono. Inicia sesión.")
        raise HTTPException(500, "No hemos podido crear la cuenta. Inténtalo de nuevo.")

    # El perfil lo crea la BD (trigger); aquí se completa con el teléfono y el correo real
    sb.table("profiles").update({"telefono": telefono, "full_name": nombre, "email": email}) \
        .eq("id", res.user.id).execute()
    return {"email_acceso": acceso}


# ── Entrar con Telegram ───────────────────────────────────────
@router.post("/telegram/iniciar")
def telegram_iniciar():
    codigo = secrets.token_hex(8).upper()
    sb.table("logins_telegram").insert({"codigo": codigo}).execute()
    # Limpieza de códigos viejos
    viejo = (datetime.now(timezone.utc) - CADUCIDAD_LOGIN).isoformat()
    try:
        sb.table("logins_telegram").delete().lt("created_at", viejo).execute()
    except Exception:
        pass
    bot = os.environ.get("TELEGRAM_BOT_USERNAME", "ReactivaPuebloBot").lstrip("@")
    return {"codigo": codigo, "enlace": f"https://t.me/{bot}?start=ENTRAR-{codigo}"}


@router.get("/telegram/estado/{codigo}")
def telegram_estado(codigo: str):
    filas = sb.table("logins_telegram").select("*").eq("codigo", codigo.upper()).limit(1).execute().data
    if not filas:
        raise HTTPException(404, "Código no válido. Vuelve a pulsar «Entrar con Telegram».")
    fila = filas[0]
    creado = datetime.fromisoformat(fila["created_at"].replace("Z", "+00:00"))
    if datetime.now(timezone.utc) - creado > CADUCIDAD_LOGIN:
        sb.table("logins_telegram").delete().eq("codigo", fila["codigo"]).execute()
        raise HTTPException(410, "El código ha caducado. Vuelve a pulsar «Entrar con Telegram».")
    if not fila.get("token_hash"):
        return {"listo": False}
    # Acceso de un solo uso: se borra al entregarlo
    sb.table("logins_telegram").delete().eq("codigo", fila["codigo"]).execute()
    return {"listo": True, "token_hash": fila["token_hash"]}


def preparar_login_telegram(codigo: str, user_id: str) -> bool:
    """La llama el bot cuando la persona pulsa "Iniciar" con ENTRAR-<codigo>.
    Deja en la fila un token de acceso de un solo uso. True si todo ha ido bien."""
    filas = sb.table("logins_telegram").select("*").eq("codigo", codigo.upper()).limit(1).execute().data
    if not filas:
        return False
    creado = datetime.fromisoformat(filas[0]["created_at"].replace("Z", "+00:00"))
    if datetime.now(timezone.utc) - creado > CADUCIDAD_LOGIN:
        return False
    usuario = sb.auth.admin.get_user_by_id(user_id).user
    email = usuario.email
    if not email:  # cuenta sin correo: se le pone uno técnico para poder abrir sesión
        email = f"u{user_id.replace('-', '')}@reactiva.local"
        sb.auth.admin.update_user_by_id(user_id, {"email": email, "email_confirm": True})
    enlace = sb.auth.admin.generate_link({"type": "magiclink", "email": email})
    token_hash = enlace.properties.hashed_token
    sb.table("logins_telegram").update({"token_hash": token_hash, "user_id": user_id}) \
        .eq("codigo", filas[0]["codigo"]).execute()
    return True


# ── Borrar mi cuenta ──────────────────────────────────────────
@router.delete("/cuenta")
def borrar_cuenta(user=Depends(require_user)):
    """Borra la cuenta y sus datos personales (perfil, inscripciones, opiniones, avisos).
    Los planes que organizó se quedan publicados, pero sin su nombre."""
    try:
        sb.auth.admin.delete_user(user.id)
    except Exception:
        raise HTTPException(500, "No hemos podido borrar la cuenta. Inténtalo de nuevo.")
    return {"ok": True}
