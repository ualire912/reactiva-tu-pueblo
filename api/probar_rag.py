"""
Prueba el asistente de la agenda (embeddings).

Uso (desde la carpeta api):
    python probar_rag.py indexar            -> crea los embeddings de eventos y conocimiento
    python probar_rag.py                    -> hace las preguntas de ejemplo
    python probar_rag.py "tu pregunta"      -> hace una pregunta concreta
"""
import sys

sys.stdout.reconfigure(encoding="utf-8")
import ia  # noqa: E402
import rag  # noqa: E402

if len(sys.argv) > 1 and sys.argv[1] == "indexar":
    print("Conocimiento:", rag.indexar_conocimiento(), "temas")
    total = 0
    while True:
        n = rag.indexar_eventos_pendientes(100)
        total += n
        if n == 0:
            break
        print(f"  … {total} eventos indexados")
    print("Eventos indexados:", total)
    print("CONSUMO:", ia.CONSUMO)
    sys.exit()

preguntas = sys.argv[1:] or [
    "¿Qué hay este finde en Tahal?",
    "¿Qué fiestas hay en Velefique?",
    "¿Cuántas veces se ha celebrado la Romería de la Virgen del Rosario?",
    "¿Qué rutas de senderismo hubo el año pasado?",
    "Quiero algo tranquilo para hacer con mis nietos",
    "¿Cómo vinculo Telegram?",
    "¿Qué pasa si un evento está completo?",
]
for p in preguntas:
    print("=" * 70)
    print("PREGUNTA:", p)
    f = rag.extraer_filtros(p)
    print("  filtros:", {k: (v.date().isoformat() if hasattr(v, "date") else v)
                         for k, v in f.items() if v not in (None, False)})
    r = rag.preguntar_agenda(p)
    print("  RESPUESTA:", r["respuesta"])
print("=" * 70)
print("CONSUMO:", ia.CONSUMO)
