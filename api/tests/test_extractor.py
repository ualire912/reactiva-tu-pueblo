"""
Calidad del extractor de carteles (extractor_vlm) sobre el set sintético de tests/carteles.

Mide el acierto por campo frente a golden.json y FALLA si:
  - la fecha o el título aciertan menos del 85 %,
  - el extractor inventa un dato que el cartel no trae (hora o precio),
  - el cartel de spam no se marca,
  - al desconectar un proveedor (clave inválida) la cascada no sigue con el siguiente.

Llama a la IA de verdad (gasta unas 10 llamadas gratuitas). Sin claves -> se salta.
Uso (desde la carpeta api):
    python -m pytest tests/ -v          (si tienes pytest)
    python tests/test_extractor.py      (sin pytest: mismo resultado)
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime

AQUI = os.path.dirname(os.path.abspath(__file__))
API = os.path.dirname(AQUI)
sys.path.insert(0, API)
sys.path.insert(0, AQUI)

import extractor_vlm  # noqa: E402
import ia  # noqa: E402

try:
    import pytest
except ImportError:  # se puede ejecutar también sin pytest
    pytest = None

FECHA_PUB = "2026-11-20"
GOLDEN = json.load(open(os.path.join(AQUI, "golden.json"), encoding="utf-8"))
_CACHE: dict[str, dict] = {}


def _saltar(motivo: str):
    if pytest:
        pytest.skip(motivo)
    raise SystemExit(f"SALTADO: {motivo}")


def resultados() -> dict[str, dict]:
    """Extrae cada cartel una sola vez (las llamadas a la IA son lentas y con cuota)."""
    if _CACHE:
        return _CACHE
    if not extractor_vlm.proveedores_disponibles():
        _saltar("no hay ninguna clave de proveedor de visión en api/.env")
    carpeta = os.path.join(AQUI, "carteles")
    if not os.path.isdir(carpeta):
        import generar_carteles
        generar_carteles.main()
    for nombre in GOLDEN:
        if nombre.startswith("_"):
            continue
        with open(os.path.join(carpeta, f"{nombre}.jpg"), "rb") as f:
            img = f.read()
        try:
            _CACHE[nombre] = extractor_vlm.extraer_cartel([img], "", None, FECHA_PUB)
        except Exception as e:
            _CACHE[nombre] = {"_error": str(e)}
    return _CACHE


def _local(iso: str | None) -> datetime | None:
    if not iso:
        return None
    return datetime.fromisoformat(iso).astimezone(ia.TZ)


def _eventos():
    return [(n, g) for n, g in GOLDEN.items() if not n.startswith("_") and g.get("es_evento")]


def test_fecha_y_titulo_al_menos_85():
    r = resultados()
    ok_fecha = ok_titulo = 0
    eventos = _eventos()
    for nombre, g in eventos:
        d = r[nombre]
        ini = _local(d.get("starts_at"))
        if ini and ini.date().isoformat() == g["fecha"]:
            ok_fecha += 1
        titulo = ia._norm(d.get("title") or "")
        if all(p in titulo for p in g["titulo_contiene"]):
            ok_titulo += 1
    print(f"\nfecha: {ok_fecha}/{len(eventos)} · título: {ok_titulo}/{len(eventos)}")
    assert ok_fecha / len(eventos) >= 0.85, f"fecha {ok_fecha}/{len(eventos)}"
    assert ok_titulo / len(eventos) >= 0.85, f"título {ok_titulo}/{len(eventos)}"


def test_no_inventa_datos():
    """Lo que el cartel no dice (golden = null) debe salir vacío o marcado como que falta."""
    r = resultados()
    inventos = []
    for nombre, g in _eventos():
        d = r[nombre]
        if "hora" in g and g["hora"] is None:
            ini = _local(d.get("starts_at"))
            hora_marcada = "hora" in d.get("missing", []) or "starts_at" in d.get("missing", [])
            if ini and not hora_marcada and (ini.hour, ini.minute) != (0, 0):
                inventos.append(f"{nombre}: hora {ini:%H:%M}")
        if "price" in g and g["price"] is None and d.get("price") not in (None,):
            inventos.append(f"{nombre}: precio {d.get('price')}")
    assert not inventos, "Ha inventado: " + "; ".join(inventos)


def test_acierto_por_campo():
    """Informe por campo (hora, precio, plazas, inscripción, municipio). Falla si baja del 70 %."""
    r = resultados()
    aciertos: dict[str, list[bool]] = {}
    for nombre, g in _eventos():
        d = r[nombre]
        ini = _local(d.get("starts_at"))
        if g.get("hora"):
            aciertos.setdefault("hora", []).append(bool(ini) and f"{ini:%H:%M}" == g["hora"])
        if g.get("price") is not None:
            aciertos.setdefault("precio", []).append(d.get("price") == g["price"])
        if "max_capacity" in g:
            aciertos.setdefault("plazas", []).append(d.get("max_capacity") == g["max_capacity"])
        if "inscripcion_hasta" in g:
            ins = _local(d.get("inscripcion_hasta"))
            aciertos.setdefault("inscripcion", []).append(bool(ins) and ins.date().isoformat() == g["inscripcion_hasta"])
        if g.get("municipio"):
            aciertos.setdefault("municipio", []).append(d.get("municipio") == g["municipio"])
    total = [x for v in aciertos.values() for x in v]
    print("\n" + " · ".join(f"{k}: {sum(v)}/{len(v)}" for k, v in aciertos.items()))
    assert sum(total) / len(total) >= 0.7


def test_spam_marcado():
    d = resultados()["cartel_07_spam"]
    assert d.get("_error") or (d.get("spam_score") or 0) >= 0.7 or d.get("es_evento") is False, d


def test_sigue_si_un_proveedor_falla():
    """Clave inválida en el primero -> la cascada responde con el siguiente."""
    disp = extractor_vlm.proveedores_disponibles()
    if len(disp) < 2:
        _saltar("hace falta tener al menos 2 proveedores con clave")
    primero = disp[0]
    clave_env = extractor_vlm.PROVEEDORES[primero]["key"]
    real = os.environ[clave_env]
    os.environ[clave_env] = "clave-invalida-de-prueba"
    try:
        with open(os.path.join(AQUI, "carteles", "cartel_04_gratis.jpg"), "rb") as f:
            d = extractor_vlm.extraer_cartel([f.read()], "", None, FECHA_PUB)
    finally:
        os.environ[clave_env] = real
        extractor_vlm._bloqueado.pop(primero, None)
    assert d["_proveedor"] != primero and any(e.startswith(primero) for e in d["_errores_previos"])


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    fallos = 0
    for nombre, fn in list(globals().items()):
        if nombre.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"OK    {nombre}")
            except SystemExit as e:
                print(f"SALTA {nombre}: {e}")
            except AssertionError as e:
                fallos += 1
                print(f"FALLA {nombre}: {e}")
    for n, d in _CACHE.items():
        print(f"  {n}: {d.get('title')} · {d.get('starts_at')} · precio={d.get('price')} · "
              f"falta={d.get('missing')} · spam={d.get('spam_score')} · {d.get('_proveedor')} {d.get('_error', '')[:60]}")
    sys.exit(1 if fallos else 0)
