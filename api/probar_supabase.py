"""
Comprueba que el servidor puede leer Supabase con la clave secreta del .env.
Uso (desde la carpeta api):  python probar_supabase.py
"""
import os
import sys

from dotenv import load_dotenv
from supabase import create_client

sys.stdout.reconfigure(encoding="utf-8")
load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))

url = os.getenv("SUPABASE_URL")
clave = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
print("URL cargada:", bool(url), "| clave cargada:", bool(clave))
if not url or not clave:
    sys.exit("Faltan SUPABASE_URL o SUPABASE_SERVICE_ROLE_KEY en el .env")

sb = create_client(url, clave)

for tabla, columnas in [
    ("municipios", "id, nombre"),
    ("categorias", "slug"),
    ("entidades", "id, nombre, facebook_page_url, auto_approve, owner_user_id"),
    ("ingestas", "id, estado, source_url"),
    ("eventos", "id, title, status"),
]:
    try:
        filas = sb.table(tabla).select(columnas).limit(10).execute().data
        print(f"\n== {tabla}: {len(filas)} filas (máx. 10) ==")
        for f in filas:
            print("  ", f)
    except Exception as e:
        print(f"\n== {tabla}: ERROR -> {e}")
