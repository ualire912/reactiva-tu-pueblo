"""
api/voz.py  ·  C1  ·  Texto a voz (para quien prefiere audios, p. ej. Luis, 70 años)

Contrato: texto_a_voz(texto) -> bytes | None   (mp3)
Usa edge-tts (gratis, sin clave). Si falla -> None y B2 manda solo texto (Plan B).
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import hashlib
import re

VOZ = "es-ES-ElviraNeural"
VELOCIDAD = "-10%"          # algo más lento: más fácil de entender
MAX_CARACTERES = 1200       # un audio de ~1 minuto como mucho
TIMEOUT_S = 20

_CACHE: dict[str, bytes] = {}  # mismo texto -> mismo audio, sin volver a generarlo


def _limpiar(texto: str) -> str:
    """Quita emojis, enlaces y símbolos que la voz leería raro."""
    t = re.sub(r"https?://\S+", "", texto or "")
    t = re.sub(r"[*_`#>\[\]]", "", t)                       # markdown
    t = re.sub(r"[\U0001F000-\U0001FAFF☀-➿]", "", t)  # emojis
    t = t.replace("€", " euros").replace("&", " y ")
    return re.sub(r"\s+", " ", t).strip()[:MAX_CARACTERES]


async def _generar(texto: str) -> bytes:
    import edge_tts
    audio = bytearray()
    async for trozo in edge_tts.Communicate(texto, VOZ, rate=VELOCIDAD).stream():
        if trozo["type"] == "audio":
            audio.extend(trozo["data"])
    return bytes(audio)


def texto_a_voz(texto: str) -> bytes | None:
    """Devuelve el mp3 o None si no se puede. Nunca lanza excepciones."""
    try:
        limpio = _limpiar(texto)
        if not limpio:
            return None
        clave = hashlib.sha256(limpio.encode()).hexdigest()
        if clave in _CACHE:
            return _CACHE[clave]
        # En un hilo aparte con su propio bucle asyncio: así funciona igual
        # desde código normal que desde dentro del servidor (que ya tiene uno).
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
            mp3 = ex.submit(asyncio.run, _generar(limpio)).result(timeout=TIMEOUT_S)
        if not mp3:
            return None
        if len(_CACHE) > 200:
            _CACHE.clear()
        _CACHE[clave] = mp3
        return mp3
    except Exception as e:
        print(f"[voz] no se pudo generar el audio: {type(e).__name__}: {e}")
        return None
