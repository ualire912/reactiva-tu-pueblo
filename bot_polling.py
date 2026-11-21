import asyncio
import os
from pathlib import Path
import sys
from typing import Any

BASE_DIR = Path(__file__).resolve().parent
# La raíz del repo (para "import api...") y api/ (sus módulos se importan entre sí sin "api.")
for _ruta in (BASE_DIR, BASE_DIR / "api"):
    if str(_ruta) not in sys.path:
        sys.path.insert(0, str(_ruta))

# En Windows la consola puede no aceptar emojis en los print
for _flujo in (sys.stdout, sys.stderr):
    try:
        _flujo.reconfigure(encoding="utf-8")
    except Exception:
        pass

from dotenv import load_dotenv

# Las claves están en api/.env (Supabase, Gemini, Telegram...); un .env en la raíz también vale
load_dotenv(dotenv_path=BASE_DIR / "api" / ".env")
load_dotenv(dotenv_path=BASE_DIR / ".env")

import httpx  # noqa: E402
from api.bot import procesar_update  # noqa: E402
from api.telegram_api import delete_webhook  # noqa: E402


def _api_base() -> str:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    return f"https://api.telegram.org/bot{token}"


async def run_polling() -> None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        raise RuntimeError("Falta TELEGRAM_BOT_TOKEN en api/.env")

    await delete_webhook()
    print("🤖 Polling iniciado en local. Pulsa Ctrl+C para detener.")

    offset = 0
    async with httpx.AsyncClient(timeout=40.0) as client:
        while True:
            try:
                resp = await client.get(
                    f"{_api_base()}/getUpdates",
                    params={
                        "offset": offset,
                        "timeout": 30,
                        "allowed_updates": '["message","callback_query"]',
                    },
                )
                resp.raise_for_status()
                data: dict[str, Any] = resp.json()

                for update in data.get("result") or []:
                    update_id = update.get("update_id")
                    if isinstance(update_id, int):
                        offset = update_id + 1
                    asyncio.create_task(procesar_update(update))

            except asyncio.CancelledError:
                break
            except Exception as exc:
                print(f"⚠️ Error en getUpdates: {exc}")
                await asyncio.sleep(2)


if __name__ == "__main__":
    try:
        asyncio.run(run_polling())
    except KeyboardInterrupt:
        print("\n🛑 Bot detenido.")