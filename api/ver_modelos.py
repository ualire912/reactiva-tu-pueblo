"""Lista los modelos Gemini "flash" que tu clave puede usar.
Uso (desde la carpeta api):  python ver_modelos.py
"""
import os
import sys

from dotenv import load_dotenv
from google import genai

sys.stdout.reconfigure(encoding="utf-8")
load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))

for nombre in ("GEMINI_API_KEY", "GEMINI_API_KEY_2"):
    clave = os.getenv(nombre)
    if not clave:
        continue
    print(f"\n== Modelos disponibles con {nombre} ==")
    try:
        cliente = genai.Client(api_key=clave)  # guardarlo en una variable para que no se cierre
        for m in cliente.models.list():
            acciones = getattr(m, "supported_actions", None) or []
            if "flash" in m.name and "generateContent" in acciones:
                print("  ", m.name.replace("models/", ""))
    except Exception as e:
        print("  Error:", e)


# ── NVIDIA ──────────────────────────────────────────────────────
import httpx  # noqa: E402

clave = os.getenv("NVIDIA_API_KEY")
if clave:
    print("\n== Modelos de chat en NVIDIA (los que suenan útiles) ==")
    try:
        r = httpx.get("https://integrate.api.nvidia.com/v1/models",
                      headers={"Authorization": f"Bearer {clave}"}, timeout=20)
        ids = sorted(m["id"] for m in r.json().get("data", []))
        utiles = [i for i in ids if any(p in i.lower() for p in ("instruct", "chat", "nemotron", "qwen", "llama", "mistral", "gemma"))
                  and not any(p in i.lower() for p in ("embed", "rerank", "guard", "safety", "vision", "vl", "coder", "reward", "parse"))]
        for i in utiles:
            print("  ", i)
        print(f"  ({len(ids)} modelos en total)")
    except Exception as e:
        print("  Error:", e)
