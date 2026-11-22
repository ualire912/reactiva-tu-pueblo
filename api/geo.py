"""Del texto del lugar ("Plaza Mayor", "C/ Almería 5", "la iglesia") a latitud y longitud.

Siempre dentro del término municipal del pueblo (nunca cae en otro pueblo):
  1. OpenStreetMap (Nominatim), acotado a la zona del pueblo.
  2. Photon (tolera faltas y nombres aproximados), también acotado; solo si el nombre
     encontrado coincide en alguna palabra con lo escrito.
  3. Si no se encuentra: None (la base de datos pone el centro del pueblo).
La misma lógica está en la web (app/src/app/core/geocoding.service.ts).
"""
from __future__ import annotations

import json
import os
import re
import time
import unicodedata

import httpx

UA = {"User-Agent": "ReactivaTuPueblo/1.0 (agenda de la comarca)"}

# Zona de cada pueblo (oeste, sur, este, norte), sacada de los límites de OpenStreetMap
ZONAS = {
    "lucainena de las torres": (-2.2502, 36.9767, -2.0155, 37.1338),
    "velefique": (-2.4563, 37.1362, -2.3622, 37.2646),
    "senes": (-2.3744, 37.1427, -2.2919, 37.2592),
    "uleila del campo": (-2.2272, 37.1338, -2.1525, 37.2167),
    "tahal": (-2.3630, 37.1085, -2.2097, 37.2764),
}
_ABREVIATURAS = [
    (r"\bc/\s*", "calle "), (r"\bcl\.?\s+", "calle "), (r"\bavda?\.?\s+", "avenida "),
    (r"\bav\.\s*", "avenida "), (r"\bpza?\.?\s+", "plaza "), (r"\bpl\.\s*", "plaza "),
    (r"\bctra\.?\s+", "carretera "), (r"\bpje\.?\s+", "pasaje "), (r"\bbda\.?\s+", "barriada "),
]
_VACIAS = {"de", "del", "la", "el", "los", "las", "en", "y", "a", "al", "junto", "frente", "lugar",
           "por", "confirmar", "municipal", "calle", "plaza", "avenida"}
_ultima_llamada = [0.0]


def _norm(t: str) -> str:
    t = unicodedata.normalize("NFD", t or "")
    return "".join(c for c in t if unicodedata.category(c) != "Mn").lower().strip()


def limpiar_lugar(lugar: str, pueblo: str = "") -> str:
    """'C/ Almería, 5 (Senés)' -> 'calle Almería' (sin número, sin el pueblo, sin s/n)."""
    t = (lugar or "").strip()
    for patron, cambio in _ABREVIATURAS:
        t = re.sub(patron, cambio, t, flags=re.I)
    t = re.sub(r"\b(s/n|sin numero|nº?\s*\d+|n\.?\s*\d+|\d+)\b", " ", t, flags=re.I)
    if pueblo:
        t = re.sub(re.escape(pueblo), " ", t, flags=re.I)
    t = re.sub(r"\b(almeria|espana|españa)\b", " ", t, flags=re.I)
    return re.sub(r"[\s,;()]+", " ", t).strip(" .,-")


def _espera():
    # Política de uso de OpenStreetMap: como mucho 1 petición por segundo
    falta = 1.1 - (time.time() - _ultima_llamada[0])
    if falta > 0:
        time.sleep(falta)
    _ultima_llamada[0] = time.time()


def _palabras(t: str) -> set[str]:
    return {p for p in re.findall(r"[a-z0-9]+", _norm(t)) if len(p) > 2 and p not in _VACIAS}


_CONTORNOS: dict[str, list] = {}


def _contornos() -> dict[str, list]:
    """Contornos reales de los términos municipales (OpenStreetMap, limites_pueblos.geojson)."""
    if not _CONTORNOS:
        try:
            ruta = os.path.join(os.path.dirname(os.path.abspath(__file__)), "limites_pueblos.geojson")
            with open(ruta, encoding="utf-8") as f:
                for feat in json.load(f)["features"]:
                    if feat["properties"].get("tipo") == "pueblo":
                        g = feat["geometry"]
                        anillos = [g["coordinates"][0]] if g["type"] == "Polygon" else [p[0] for p in g["coordinates"]]
                        _CONTORNOS[_norm(feat["properties"]["nombre"])] = anillos
        except Exception:
            pass
    return _CONTORNOS


def _en_anillo(lat: float, lng: float, anillo: list) -> bool:
    dentro = False
    j = len(anillo) - 1
    for i in range(len(anillo)):
        xi, yi = anillo[i][0], anillo[i][1]
        xj, yj = anillo[j][0], anillo[j][1]
        if (yi > lat) != (yj > lat) and lng < (xj - xi) * (lat - yi) / ((yj - yi) or 1e-12) + xi:
            dentro = not dentro
        j = i
    return dentro


def _dentro(lat: float, lng: float, zona, pueblo: str = "") -> bool:
    """Dentro del término municipal (contorno real); si no se tiene, de su rectángulo."""
    anillos = _contornos().get(_norm(pueblo))
    if anillos:
        return any(_en_anillo(lat, lng, a) for a in anillos)
    return zona[0] <= lng <= zona[2] and zona[1] <= lat <= zona[3]


def geocodificar(lugar: str | None, pueblo: str | None) -> tuple[float, float] | None:
    """(lat, lng) del lugar dentro del pueblo, o None si no se encuentra con seguridad."""
    zona = ZONAS.get(_norm(pueblo or ""))
    texto = limpiar_lugar(lugar or "", pueblo or "")
    if not zona or not texto or _norm(texto) in ("por confirmar", "lugar por confirmar"):
        return None
    oeste, sur, este, norte = zona
    try:
        _espera()
        r = httpx.get("https://nominatim.openstreetmap.org/search", headers=UA, timeout=12, params={
            "format": "jsonv2", "limit": 3, "q": f"{texto}, {pueblo}", "countrycodes": "es",
            "viewbox": f"{oeste},{norte},{este},{sur}", "bounded": 1})
        datos = r.json() if r.status_code == 200 else []
        for d in datos or []:
            lat, lng = float(d["lat"]), float(d["lon"])
            if _dentro(lat, lng, zona, pueblo or ""):
                return round(lat, 6), round(lng, 6)
    except Exception:
        pass
    try:
        r = httpx.get("https://photon.komoot.io/api/", headers=UA, timeout=12, params={
            "q": f"{texto} {pueblo}", "limit": 5, "bbox": f"{oeste},{sur},{este},{norte}"})
        buscadas = _palabras(texto)
        for f in (r.json().get("features") or []) if r.status_code == 200 else []:
            props = f.get("properties") or {}
            nombre = " ".join(str(props.get(k) or "") for k in ("name", "street"))
            lng, lat = f["geometry"]["coordinates"][:2]
            # Tienen que coincidir todas las palabras importantes ("salón cultural" no es
            # "asociación cultural"): si no, mejor el centro del pueblo que un sitio equivocado
            if buscadas and buscadas <= _palabras(nombre) and _dentro(lat, lng, zona, pueblo or ""):
                return round(lat, 6), round(lng, 6)
    except Exception:
        pass
    return None
