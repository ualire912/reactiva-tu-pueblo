"""Diagnóstico de WhatsApp (Cloud API). Lee WHATSAPP_TOKEN, WHATSAPP_PHONE_NUMBER_ID y
WHATSAPP_WABA_ID de api/.env. NUNCA imprime el token.

    python diagnostico_whatsapp.py            -> solo mira
    python diagnostico_whatsapp.py --arreglar -> además suscribe la cuenta de WhatsApp a la app
"""
import os
import sys

import httpx
from dotenv import load_dotenv

sys.stdout.reconfigure(encoding="utf-8")
load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))
G = "https://graph.facebook.com/v21.0"
TOKEN = os.getenv("WHATSAPP_TOKEN", "").strip()
PHONE = os.getenv("WHATSAPP_PHONE_NUMBER_ID", "").strip()
WABA = os.getenv("WHATSAPP_WABA_ID", "").strip()
H = {"Authorization": f"Bearer {TOKEN}"}

if not TOKEN:
    sys.exit("Falta WHATSAPP_TOKEN en api/.env")


def ver(nombre, r):
    try:
        d = r.json()
    except ValueError:
        d = r.text[:300]
    estado = "OK " if r.status_code < 400 else "ERR"
    print(f"[{estado}] {nombre}: {d}")
    return d


# 1. El token
d = httpx.get(f"{G}/debug_token", params={"input_token": TOKEN, "access_token": TOKEN}).json().get("data") or {}
print("Token válido:", d.get("is_valid"), "· app:", d.get("app_id"), "· permisos:", d.get("scopes"))

# 2. La cuenta de WhatsApp Business: si no se dio, se busca en los permisos del token
if not WABA:
    d = httpx.get(f"{G}/debug_token", params={"input_token": TOKEN, "access_token": TOKEN}).json()
    for s in (d.get("data") or {}).get("granular_scopes") or []:
        if s.get("scope") in ("whatsapp_business_management", "whatsapp_business_messaging") and s.get("target_ids"):
            WABA = s["target_ids"][0]
            break
    print("Cuenta de WhatsApp Business (WABA) encontrada en el token:", WABA or "NO")
if not WABA:
    sys.exit("Añade WHATSAPP_WABA_ID en api/.env (Identificador de la cuenta de WhatsApp Business, en 'Probar')")

# 2b. Números de esa cuenta (el identificador que hay que poner en Render)
d = ver("Números de la cuenta", httpx.get(f"{G}/{WABA}/phone_numbers", headers=H,
                                          params={"fields": "id,display_phone_number,verified_name"}))
if not PHONE and isinstance(d, dict) and d.get("data"):
    PHONE = d["data"][0]["id"]
print("=> WHATSAPP_PHONE_NUMBER_ID =", PHONE or "NO ENCONTRADO")

# 3. ¿Está la cuenta suscrita a la app? (si no, los mensajes reales NO llegan al webhook)
d = ver("Apps suscritas a la cuenta", httpx.get(f"{G}/{WABA}/subscribed_apps", headers=H))
APP = (httpx.get(f"{G}/debug_token", params={"input_token": TOKEN, "access_token": TOKEN}).json()
       .get("data") or {}).get("app_id")
suscrita = isinstance(d, dict) and any(
    (x.get("whatsapp_business_api_data") or {}).get("id") == APP for x in d.get("data") or [])
print("=> La cuenta", "SÍ" if suscrita else "NO", f"está suscrita a NUESTRA app ({APP})")

if "--arreglar" in sys.argv and not suscrita:
    ver("Suscribir la cuenta a la app", httpx.post(f"{G}/{WABA}/subscribed_apps", headers=H))
    ver("Comprobación", httpx.get(f"{G}/{WABA}/subscribed_apps", headers=H))
