import os
from typing import Optional
from dotenv import load_dotenv
from supabase import create_client, Client

load_dotenv()

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_SERVICE_ROLE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")

sb: Client = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)


def perfil(user_id: str) -> Optional[dict]:
    """Obtiene la fila de profiles por user_id."""
    res = sb.table("profiles").select("*").eq("id", user_id).limit(1).execute()
    return res.data[0] if res and res.data else None


def perfil_por_chat(chat_id: int) -> Optional[dict]:
    """Obtiene la fila de profiles vinculada a un telegram_chat_id."""
    res = sb.table("profiles").select("*").eq("telegram_chat_id", chat_id).limit(1).execute()
    return res.data[0] if res and res.data else None