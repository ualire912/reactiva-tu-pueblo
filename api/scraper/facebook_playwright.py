"""
Lector de la página PÚBLICA de Facebook de un ayuntamiento/asociación con un navegador
automático (Playwright), SIN iniciar sesión. Lee los posts reales visibles y manda los que
parecen eventos a POST {API_URL}/ingesta/facebook (ficha IA -> spam/no-evento -> Telegram).

Solo en el portátil (no en Render). Instalar una vez:
    python -m pip install playwright
    python -m playwright install chromium

Uso (desde la carpeta api):
    python scraper/facebook_playwright.py ver        -> abre el navegador y SOLO muestra lo que lee
    python scraper/facebook_playwright.py            -> lee y envía los posts al servidor
    python scraper/facebook_playwright.py vigilar    -> repite cada 10 minutos (para la demo)

Si no encuentra posts, guarda pruebas/facebook_captura.png para ver qué ha mostrado Facebook.
"""
import hashlib
import os
import re
import sys
import time

import httpx
from dotenv import load_dotenv

sys.stdout.reconfigure(encoding="utf-8")
AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, AQUI)
load_dotenv(os.path.join(AQUI, "..", ".env"))

from facebook_graph import parece_evento  # noqa: E402  (mismo filtro previo, gratis)

PAGE_URL = os.getenv("FB_PAGE_URL", "https://www.facebook.com/profile.php?id=100064636603636")
API_URL = os.getenv("API_URL", "http://localhost:8000").rstrip("/")
MAX_POSTS = 10
SCROLLS = 6
CADA_S = 600  # 10 minutos: pocas visitas, como una persona
CAPTURA = os.path.join(AQUI, "..", "pruebas", "facebook_captura.png")

TEXTOS_COOKIES = ["Rechazar cookies opcionales", "Decline optional cookies",
                  "Permitir todas las cookies", "Allow all cookies", "Solo permitir cookies esenciales"]

# JavaScript que se ejecuta dentro de la página: saca texto, enlace y foto de cada post
EXTRAER_JS = r"""
() => {
  const posts = [];
  const vistos = new Set();
  const bloques = document.querySelectorAll('div[role="article"], div[data-pagelet^="TimelineFeedUnit"]');
  for (const b of bloques) {
    // Los comentarios también son role="article", pero van DENTRO de otro post: fuera
    if (b.parentElement && b.parentElement.closest('div[role="article"]')) continue;
    // Texto del post (el bloque del mensaje, si existe; si no, todo el texto)
    const msg = b.querySelector('div[data-ad-preview="message"], div[data-ad-comet-preview="message"]');
    let texto = (msg ? msg.innerText : b.innerText || '').trim();
    if (texto.length < 15) continue;
    // Enlace permanente del post
    let enlace = null;
    for (const a of b.querySelectorAll('a[href]')) {
      const h = a.href;
      if (h.includes('comment_id')) continue;
      if (/\/posts\/|story_fbid|permalink|\/photos?\/|pfbid|\/videos\//.test(h)) {
        enlace = h.split('?__cft__')[0].split('&__cft__')[0]; break;
      }
    }
    // Foto del post (la más grande de scontent)
    let foto = null, area = 0;
    for (const img of b.querySelectorAll('img[src*="scontent"], img[src*="fbcdn"]')) {
      const a2 = (img.naturalWidth || img.width) * (img.naturalHeight || img.height);
      if (a2 > area && (img.width > 150 || img.naturalWidth > 150)) { area = a2; foto = img.src; }
    }
    if (!enlace && b.querySelector('a[href*="comment_id"]') && !msg) continue;
    const clave = enlace || texto.slice(0, 120);
    if (vistos.has(clave)) continue;
    vistos.add(clave);
    posts.push({texto, enlace, foto});
  }
  return posts;
}
"""


_MESES = {m: k for k, m in enumerate(["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
                                       "agosto", "septiembre", "octubre", "noviembre", "diciembre"], 1)}
_RE_PUBLICADO = re.compile(r"(?:lunes|martes|miércoles|jueves|viernes|sábado|domingo), (\d{1,2}) de "
                           r"([a-záéíóú]+) de (\d{4})", re.I)
_RUIDO = re.compile(
    r"^(Me gusta|Comentar|Compartir|Enviar|Responder|Ver más|Ver menos|Ver traducción|Seguir|"
    r"Todas las reacciones:?|\d+ comentarios?|\d+ veces compartido|Like|Comment|Share|See more|·|"
    r"Hace \d+ (minutos?|horas?|días?|semanas?)|\d+ ?(min|h|d|sem)|\d+|"
    r"(lunes|martes|miércoles|jueves|viernes|sábado|domingo), \d{1,2} de .* a las \d{1,2}:\d{2}|"
    r"Las cuentas con una insignia de verificación.*|Publicación de .*|Destacado)$", re.I)


def limpiar_texto(t: str, autor: str | None = None) -> tuple[str, str | None]:
    """Quita la cabecera (página, 'Hace 3 días', fecha), botones y contadores.
    Devuelve (texto, fecha de publicación 'DD/MM/AAAA' o None)."""
    publicado = None
    m = _RE_PUBLICADO.search(t)
    if m and m.group(2).lower() in _MESES:
        publicado = f"{int(m.group(1)):02d}/{_MESES[m.group(2).lower()]:02d}/{m.group(3)}"
    lineas = [l.strip() for l in t.splitlines()]
    limpias = []
    for l in lineas:
        l = re.sub(r"\s*(Ver menos|Ver más)$", "", l).strip()
        if not l or _RUIDO.match(l) or (autor and l == autor):
            continue
        limpias.append(l)
    return "\n".join(limpias).strip()[:3000], publicado


def leer_pagina(mostrar: bool = False) -> list[dict]:
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        nav = p.chromium.launch(headless=not mostrar)
        ctx = nav.new_context(locale="es-ES", timezone_id="Europe/Madrid",
                              viewport={"width": 1280, "height": 1800})
        pag = ctx.new_page()
        pag.goto(PAGE_URL, wait_until="domcontentloaded", timeout=45000)
        pag.wait_for_timeout(3000)

        for txt in TEXTOS_COOKIES:  # banner de cookies
            try:
                pag.get_by_role("button", name=txt).first.click(timeout=1500)
                break
            except Exception:
                pass
        for _ in range(2):  # ventana "Inicia sesión": se cierra sin iniciar sesión
            try:
                pag.locator('div[role="dialog"] [aria-label="Cerrar"], '
                            'div[role="dialog"] [aria-label="Close"]').first.click(timeout=1500)
            except Exception:
                pag.keyboard.press("Escape")
            pag.wait_for_timeout(800)

        for _ in range(SCROLLS):  # bajar para cargar más posts
            pag.mouse.wheel(0, 2500)
            pag.wait_for_timeout(1500)
            try:  # desplegar "Ver más" para tener el texto completo
                for b in pag.get_by_role("button", name=re.compile(r"^Ver más$|^See more$")).all()[:10]:
                    b.click(timeout=500)
            except Exception:
                pass

        posts = pag.evaluate(EXTRAER_JS)
        if not posts:
            pag.screenshot(path=CAPTURA, full_page=False)
            print(f"⚠️ No he encontrado posts. Captura guardada en {CAPTURA}")
        if mostrar:
            pag.wait_for_timeout(3000)
        nav.close()

    autor = posts[0]["texto"].splitlines()[0].strip() if posts else None  # nombre de la página
    salida = []
    for post in posts[:MAX_POSTS]:
        texto, publicado = limpiar_texto(post["texto"], autor)
        if not texto and not post.get("foto"):
            continue
        if publicado:  # para que la IA entienda "el próximo sábado" desde la fecha del post
            texto = f"[Publicado el {publicado}]\n{texto}"
        enlace = post.get("enlace") or \
            f"{PAGE_URL}#post-{hashlib.sha1(texto.encode()).hexdigest()[:16]}"
        salida.append({"source_url": enlace, "text": texto, "image_url": post.get("foto")})
    return salida


def enviar(posts: list[dict]):
    for p in posts:
        sin_cabecera = re.sub(r"^\[Publicado el [^\]]+\]\s*", "", p["text"])
        if not parece_evento(sin_cabecera, bool(p["image_url"])):
            print(f"  · saltado (no parece evento): {p['text'][:70]!r}")
            continue
        try:
            r = httpx.post(f"{API_URL}/ingesta/facebook", timeout=30,
                           json={**p, "page_url": PAGE_URL})
            if r.status_code == 200:
                print(f"  → {r.json()['estado']:<12} {p['text'][:70]!r}")
            else:
                print(f"  ✗ {r.status_code}: {r.text[:150]}")
        except Exception as e:
            print(f"  ✗ no se pudo enviar ({e}). ¿Está el servidor encendido en {API_URL}?")


def main():
    modo = sys.argv[1] if len(sys.argv) > 1 else ""
    print(f"Leyendo {PAGE_URL} …")
    while True:
        try:
            posts = leer_pagina(mostrar=(modo == "ver"))
            print(f"Encontrados {len(posts)} posts.\n")
            if modo == "ver":
                for k, p in enumerate(posts, 1):
                    print(f"── Post {k} ─────────────────────────")
                    print("  enlace:", p["source_url"])
                    print("  foto  :", (p["image_url"] or "-")[:90])
                    sin_cab = re.sub(r"^\[Publicado el [^\]]+\]\s*", "", p["text"])
                    print("  evento:", "sí" if parece_evento(sin_cab, bool(p["image_url"])) else "no")
                    print("  texto :", p["text"][:300].replace("\n", " / "))
                return
            enviar(posts)
        except Exception as e:
            print("✗ Error leyendo Facebook:", e)
        if modo != "vigilar":
            break
        print(f"\nSiguiente lectura en {CADA_S // 60} minutos…")
        time.sleep(CADA_S)


if __name__ == "__main__":
    main()
