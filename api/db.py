"""B1 · Cliente Supabase con service_role (salta RLS) y comprobación de usuario.

OJO: como la service_role salta todas las reglas RLS, cada endpoint
comprueba él mismo los permisos (moderador o autor).
"""
import os
import uuid

from fastapi import Depends, Header, HTTPException
from supabase import create_client

_URL = os.environ["SUPABASE_URL"]
_KEY = os.environ["SUPABASE_SERVICE_ROLE_KEY"]

# Límite de tiempo para que una consulta lenta no deje el servidor colgado
try:
    from supabase import ClientOptions
    sb = create_client(_URL, _KEY, options=ClientOptions(postgrest_client_timeout=10,
                                                         storage_client_timeout=10))
except Exception:  # versión de supabase-py sin esas opciones: cliente normal
    sb = create_client(_URL, _KEY)


def get_user(token: str):
    """Devuelve el usuario de Supabase Auth dueño del token, o None."""
    try:
        return sb.auth.get_user(token).user
    except Exception:
        return None


def require_user(authorization: str = Header(None)):
    """Dependencia de FastAPI para los endpoints 🔒 (cualquier usuario con sesión)."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Falta token")
    user = get_user(authorization[7:])
    if not user:
        raise HTTPException(401, "Token no válido")
    return user


def require_moderador(user=Depends(require_user)):
    """Dependencia de FastAPI para los endpoints 🔒 solo de moderador."""
    if (perfil(user.id) or {}).get("rol") != "moderador":
        raise HTTPException(403, "Solo el moderador puede hacer esto")
    return user


def perfil(user_id: str) -> dict | None:
    """Fila de profiles o None si no existe."""
    try:
        uuid.UUID(str(user_id))
    except ValueError:
        return None
    filas = sb.table("profiles").select("*").eq("id", user_id).limit(1).execute().data
    return filas[0] if filas else None


def evento(event_id: str) -> dict | None:
    """Fila de eventos + nombre del municipio (en evento['municipio']), o None."""
    try:
        uuid.UUID(str(event_id))
    except ValueError:
        return None  # id mal escrito -> como si no existiera (evita error 500)
    filas = (
        sb.table("eventos")
        .select("*, municipios(nombre)")
        .eq("id", event_id)
        .limit(1)
        .execute()
        .data
    )
    if not filas:
        return None
    ev = filas[0]
    ev["municipio"] = (ev.pop("municipios", None) or {}).get("nombre")
    return ev
