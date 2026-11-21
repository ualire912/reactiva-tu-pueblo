"""
Transcribe un audio (mp3, ogg, m4a, wav) con Gemini, palabra por palabra.

Uso (desde la carpeta api):
    python transcribir.py ruta/del/audio.mp3
Guarda el texto en el mismo sitio con extensión .txt
"""
import os
import sys

from dotenv import load_dotenv
from google import genai
from google.genai import types

sys.stdout.reconfigure(encoding="utf-8")
load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))

if len(sys.argv) < 2:
    sys.exit("Uso: python transcribir.py ruta/del/audio.mp3")

ruta = sys.argv[1]
mime = {".mp3": "audio/mpeg", ".ogg": "audio/ogg", ".m4a": "audio/mp4", ".wav": "audio/wav",
        ".opus": "audio/ogg"}.get(os.path.splitext(ruta)[1].lower(), "audio/mpeg")
with open(ruta, "rb") as f:
    audio = f.read()
print(f"Audio: {len(audio) // 1024} KB ({mime}). Transcribiendo…\n")

INSTRUCCION = ("Transcribe este audio en español palabra por palabra, tal cual se dice. "
               "No resumas, no corrijas, no añadas nada. Separa en párrafos cuando cambie de tema. "
               "Si hay partes que no se entienden, escribe [no se entiende].")

modelos = [m.strip() for m in os.getenv("GEMINI_MODEL", "gemini-3.5-flash").split(",") if m.strip()]
for nombre_clave in ("GEMINI_API_KEY", "GEMINI_API_KEY_2"):
    clave = os.getenv(nombre_clave)
    if not clave:
        continue
    cliente = genai.Client(api_key=clave, http_options=types.HttpOptions(timeout=180_000))
    for modelo in modelos:
        try:
            r = cliente.models.generate_content(
                model=modelo,
                contents=[INSTRUCCION, types.Part.from_bytes(data=audio, mime_type=mime)],
                config=types.GenerateContentConfig(temperature=0))
            texto = (r.text or "").strip()
            if texto:
                salida = os.path.splitext(ruta)[0] + ".txt"
                with open(salida, "w", encoding="utf-8") as f:
                    f.write(texto)
                print(texto)
                print(f"\n--- Transcrito con {modelo}. Guardado en {salida}")
                sys.exit()
        except Exception as e:
            print(f"[{modelo} con {nombre_clave}] falló: {str(e)[:150]}")
print("No se pudo transcribir con ningún modelo.")
