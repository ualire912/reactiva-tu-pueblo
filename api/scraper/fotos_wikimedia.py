"""
Descarga fotos LIBRES de los pueblos desde Wikimedia Commons (con autor y licencia)
para los carteles automáticos. Se ejecuta en tu portátil, una vez.

Uso (desde la carpeta api):
    python scraper/fotos_wikimedia.py            -> 1 foto por pueblo en api/fotos/ + creditos.json
    python scraper/fotos_wikimedia.py Tahal 2    -> cambia la foto de Tahal por la 2.ª opción

Revisa las fotos después: si alguna no te gusta, pide otra opción con el número.
"""
import json
import os
import re
import sys
import unicodedata

import httpx

sys.stdout.reconfigure(encoding="utf-8")
AQUI = os.path.dirname(os.path.abspath(__file__))
FOTOS = os.path.join(AQUI, "..", "fotos")
PUEBLOS = ["Lucainena de las Torres", "Velefique", "Senés", "Uleila del Campo", "Tahal"]
API = "https://commons.wikimedia.org/w/api.php"
# Wikimedia pide identificarse con un User-Agent descriptivo
# (con una URL de contacto; si no, Wikimedia responde con un error en vez de JSON)
CABECERAS = {"User-Agent": "ReactivaTuPueblo/1.0 (https://github.com/ualire912/reactiva-tu-pueblo; "
                           "hackathon UAL 2026) python-httpx",
             "Accept": "application/json"}
LICENCIAS_OK = ("cc by", "cc-by", "cc0", "public domain", "dominio público", "pd")


def slug(t):
    t = unicodedata.normalize("NFD", t)
    t = "".join(c for c in t if unicodedata.category(c) != "Mn").lower()
    return "-".join("".join(c if c.isalnum() else " " for c in t).split())


def limpiar_html(t):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", t or "")).strip()


def buscar(texto):
    r = httpx.get(API, headers=CABECERAS, timeout=30, follow_redirects=True, params={
        "action": "query", "format": "json", "generator": "search",
        "gsrsearch": f"{texto} filetype:bitmap", "gsrnamespace": 6, "gsrlimit": 25,
        "prop": "imageinfo", "iiprop": "url|size|extmetadata", "iiurlwidth": 1400})
    try:
        datos = r.json()
    except ValueError:
        raise RuntimeError(f"Wikimedia respondió {r.status_code} y no es JSON: {r.text[:200]!r}")
    return (datos.get("query") or {}).get("pages", {}).values()


def candidatas(pueblo):
    paginas = list(buscar(f'"{pueblo}" Almería')) or list(buscar(f'"{pueblo}"'))
    salida = []
    for p in sorted(paginas, key=lambda x: x.get("index", 99)):
        ii = (p.get("imageinfo") or [{}])[0]
        meta = ii.get("extmetadata") or {}
        licencia = limpiar_html((meta.get("LicenseShortName") or {}).get("value"))
        autor = limpiar_html((meta.get("Artist") or {}).get("value")) or "autor desconocido"
        horizontal = ii.get("width", 0) > ii.get("height", 1) and ii.get("width", 0) >= 1000
        if horizontal and any(l in licencia.lower() for l in LICENCIAS_OK) and ii.get("thumburl"):
            salida.append({"url": ii["thumburl"], "autor": autor[:60], "licencia": licencia,
                           "pagina": ii.get("descriptionurl")})
    return salida


def main():
    os.makedirs(FOTOS, exist_ok=True)
    ruta_cred = os.path.join(FOTOS, "creditos.json")
    try:
        with open(ruta_cred, encoding="utf-8") as f:
            creditos = json.load(f)
    except Exception:
        creditos = {}

    pueblos, opcion = PUEBLOS, 1
    if len(sys.argv) > 1:
        pueblos = [p for p in PUEBLOS if slug(p) == slug(sys.argv[1])] or [sys.argv[1]]
        opcion = int(sys.argv[2]) if len(sys.argv) > 2 else 1

    for pueblo in pueblos:
        try:
            lista = candidatas(pueblo)
        except Exception as e:
            print(f"✗ {pueblo}: {e}")
            continue
        if len(lista) < opcion:
            print(f"✗ {pueblo}: no hay foto libre n.º {opcion} (encontradas: {len(lista)})")
            continue
        foto = lista[opcion - 1]
        resp = httpx.get(foto["url"], headers=CABECERAS, timeout=60, follow_redirects=True)
        if resp.status_code != 200 or not resp.headers.get("content-type", "").startswith("image"):
            print(f"✗ {pueblo}: no se pudo descargar la imagen ({resp.status_code})")
            continue
        img = resp.content
        nombre = f"{slug(pueblo)}.jpg"
        with open(os.path.join(FOTOS, nombre), "wb") as f:
            f.write(img)
        creditos[nombre] = f"Foto: {foto['autor']} · {foto['licencia']} · Wikimedia Commons"
        print(f"✓ {pueblo}: {nombre} ({len(img)//1024} KB) — {creditos[nombre]}")
        print(f"    {foto['pagina']}   ({len(lista)} opciones disponibles)")

    with open(ruta_cred, "w", encoding="utf-8") as f:
        json.dump(creditos, f, ensure_ascii=False, indent=2)
    print("\nCréditos guardados en fotos/creditos.json. Revisa las fotos antes de subirlas.")


if __name__ == "__main__":
    main()
