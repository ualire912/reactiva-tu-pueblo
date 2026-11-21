"""
api/ia.py  ·  C1  ·  Las 6 funciones de IA (VERSIÓN STUB 10:00)

Ahora mismo devuelven DATOS FIJOS con la forma exacta del contrato
(sección 6), para que B1, B2 y A1 puedan integrar sin esperar.
Más tarde se sustituye el interior de cada función por Gemini,
SIN cambiar firmas ni formato de salida.

No necesita GEMINI_API_KEY para importarse.
"""
from __future__ import annotations

CATEGORIAS = [
    "senderismo", "deporte", "cultura", "gastronomia",
    "fiestas", "taller", "voluntariado", "otro",
]

# Contador de consumo (lo expone GET /ia/consumo)
CONSUMO = {"llamadas": 0, "tokens": 0}

NO_LO_SE = "No lo sé, se lo pregunto al organizador"


# 1 ─────────────────────────────────────────────────────────────
def extraer_evento(text: str | None, image_bytes: bytes | None = None) -> dict:
    """Texto (+ cartel) -> EventoExtraido."""
    return {
        "title": "Ruta de las minas",
        "category": "senderismo",
        "starts_at": "2026-11-29T09:30:00+01:00",
        "ends_at": None,
        "duration_minutes": 180,
        "municipio": "Lucainena de las Torres",
        "location_name": "Plaza del Ayuntamiento",
        "price": None,
        "max_capacity": 30,
        "min_age": None,
        "max_age": None,
        "description": (text or "Ruta guiada por las antiguas minas.")[:500],
        "is_accessible": False,
        "confidence": 0.82,
        "missing": ["price"],
    }


# 2 ─────────────────────────────────────────────────────────────
def clasificar_spam(text: str | None, entidad: dict | None = None) -> dict:
    """-> {"spam_score": 0..1, "motivo": str}"""
    if entidad and entidad.get("auto_approve"):
        return {"spam_score": 0.0, "motivo": "entidad con aprobación automática"}
    return {"spam_score": 0.1, "motivo": "stub: sin señales de spam"}


# 3 ─────────────────────────────────────────────────────────────
def entender_mensaje(text: str | None = None, audio_bytes: bytes | None = None) -> dict:
    """Frase libre o nota de voz -> intención + filtros."""
    return {
        "intencion": "buscar",  # buscar | proponer | apuntarse | pregunta | otro
        "filtros": {"municipio": "Tahal", "categoria": "senderismo", "fecha": None},
        "texto": text or "¿Qué rutas hay este fin de semana en Tahal?",
    }


# 4 ─────────────────────────────────────────────────────────────
def redactar_recomendacion(nombre: str, eventos: list[dict]) -> str:
    """eventos = filas de rpc('recomendar_eventos'): event_id, title, starts_at, municipio, puntos, motivos."""
    if not eventos:
        return f"Hola {nombre}, esta semana no hemos encontrado planes para ti."
    e = eventos[0]
    motivos = ", ".join(e.get("motivos") or [])
    return (
        f"Hola {nombre}, te puede gustar «{e.get('title')}» en {e.get('municipio')}"
        + (f" ({motivos})" if motivos else "")
        + ". ¿Te apuntas?"
    )


# 5 ─────────────────────────────────────────────────────────────
def responder_chat(evento: dict, hilo: list[dict], pregunta: str) -> str:
    """Respuesta usando SOLO la ficha + hilo; si no lo sabe, NO_LO_SE."""
    return NO_LO_SE


# 6 ─────────────────────────────────────────────────────────────
def revisar_confianza(perfil: dict, eventos: list[dict], resenas: list[dict]) -> dict:
    """-> {"aprobar": bool, "motivo": str}"""
    return {"aprobar": False, "motivo": "revisión manual"}
