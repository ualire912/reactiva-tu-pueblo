-- ════════════════════════════════════════════════════════════════
--  supabase/rag.sql  ·  C1 + B1  ·  Búsqueda semántica (embeddings) para el asistente
--  Ejecutar en Supabase → SQL Editor → Run, DESPUÉS de schema.sql y permisos.sql.
--  Se puede ejecutar varias veces sin romper nada.
--
--  Cambio de B1 respecto a la versión original:
--  los embeddings van en una TABLA APARTE (eventos_embeddings), no en eventos:
--    - la app no descarga 768 números por evento en la agenda/mapa/Realtime;
--    - guardar un embedding no toca eventos (ni updated_at, ni sequence, ni Realtime);
--    - solo se reindexa si cambia el CONTENIDO (huella), no cada inscripción.
-- ════════════════════════════════════════════════════════════════

-- 1. Extensión de vectores (pgvector)
create extension if not exists vector with schema extensions;

-- 0. Limpieza por si se ejecutó la versión anterior (columnas en eventos)
drop view if exists public.v_eventos_sin_indexar;
alter table public.eventos drop column if exists embedding;
alter table public.eventos drop column if exists embedding_at;

-- 2. Embedding de cada evento (768 números que "resumen" su significado)
create table if not exists public.eventos_embeddings (
  event_id      uuid primary key references public.eventos(id) on delete cascade,
  embedding     extensions.vector(768) not null,
  huella        text not null,                    -- md5 del contenido indexado
  embedding_at  timestamptz not null default now()
);
alter table public.eventos_embeddings enable row level security;   -- solo el servidor

-- 3. Base de conocimiento general (cómo funciona la app, Telegram, inscripciones...)
create table if not exists public.conocimiento (
  id          bigserial primary key,
  tema        text unique not null,
  contenido   text not null,
  embedding   extensions.vector(768),
  updated_at  timestamptz not null default now()
);
alter table public.conocimiento enable row level security;   -- solo el servidor

-- 4. Huella del contenido que se indexa. Si no cambia, no se reindexa.
--    (NO incluye estado, plazas ni inscritos: eso se lee en vivo al buscar)
create or replace function public.huella_evento(e public.eventos) returns text
language sql stable set search_path = public as $$
  select md5(concat_ws('|', e.title, e.description, e.category, e.location_name,
                       e.municipio_id::text, e.starts_at::text, e.ends_at::text,
                       e.price::text, e.min_age::text, e.max_age::text,
                       e.is_accessible::text, e.inscripcion_hasta::text));
$$;

-- 5. Eventos publicados que hay que (re)indexar: sin embedding o con contenido cambiado
create or replace view public.v_eventos_sin_indexar with (security_invoker = true) as
select e.id, huella_evento(e) as huella
from public.eventos e
left join public.eventos_embeddings x on x.event_id = e.id
where e.status in ('abierto','completo','inscripcion_cerrada','en_curso',
                   'finalizado','pospuesto','cancelado')
  and (x.event_id is null or x.huella is distinct from huella_evento(e));

-- 6. Guardar el embedding de un evento (lo llama el servidor por RPC)
--    p_huella: la que devolvió la vista al leer el evento (si no se pasa, se calcula ahora)
create or replace function public.guardar_embedding_evento(
  p_event_id  uuid,
  p_embedding extensions.vector(768),
  p_huella    text default null
) returns void
language sql security definer set search_path = public, extensions as $$
  insert into eventos_embeddings (event_id, embedding, huella, embedding_at)
  select e.id, p_embedding, coalesce(p_huella, huella_evento(e)), now()
    from eventos e where e.id = p_event_id
  on conflict (event_id) do update
     set embedding = excluded.embedding, huella = excluded.huella, embedding_at = now();
$$;

-- 7. Búsqueda en DOS FASES (como recomendó el profesor):
--    Fase 1: filtrar por categoría / pueblo / fechas / texto (barato)
--    Fase 2: ordenar SOLO los filtrados por parecido con la pregunta (embedding)
create or replace function public.buscar_eventos_similares(
  p_embedding     extensions.vector(768) default null,
  p_categoria     text        default null,
  p_municipio_id  smallint    default null,
  p_desde         timestamptz default null,
  p_hasta         timestamptz default null,
  p_texto         text        default null,
  p_limite        int         default 5
)
returns table (
  event_id uuid, title text, category text, starts_at timestamptz, municipio text,
  location_name text, price numeric, max_capacity int, confirmed_count int, status text,
  description text, similitud double precision, total_filtrados int
)
language sql stable security definer
set search_path = public, extensions
as $$
  with filtrados as (
    select e.id, e.title, e.category, e.starts_at, m.nombre as municipio_nombre,
           e.location_name, e.price, e.max_capacity, e.confirmed_count, e.status,
           e.description, x.embedding
    from public.eventos e
    left join public.municipios m on m.id = e.municipio_id
    left join public.eventos_embeddings x on x.event_id = e.id
    where e.status::text in ('abierto','completo','inscripcion_cerrada','en_curso',
                             'finalizado','pospuesto','cancelado')
      and (p_categoria    is null or e.category = p_categoria)
      and (p_municipio_id is null or e.municipio_id = p_municipio_id)
      and (p_desde        is null or e.starts_at >= p_desde)
      and (p_hasta        is null or e.starts_at <  p_hasta)
      and (p_texto        is null or e.title ilike '%' || p_texto || '%'
                                  or e.description ilike '%' || p_texto || '%')
  )
  select f.id, f.title::text, f.category::text, f.starts_at, f.municipio_nombre::text,
         f.location_name::text, f.price::numeric, f.max_capacity::int, f.confirmed_count::int,
         f.status::text, left(f.description, 300)::text,
         case when p_embedding is null or f.embedding is null then 0
              else 1 - (f.embedding <=> p_embedding) end,
         (select count(*) from filtrados)::int
  from filtrados f
  order by case when p_embedding is null or f.embedding is null then 1
                else f.embedding <=> p_embedding end,
           f.starts_at
  limit greatest(1, least(p_limite, 50));
$$;

create or replace function public.buscar_conocimiento(
  p_embedding extensions.vector(768),
  p_limite    int default 3
)
returns table (tema text, contenido text, similitud double precision)
language sql stable security definer
set search_path = public, extensions
as $$
  select c.tema, c.contenido, 1 - (c.embedding <=> p_embedding)
  from public.conocimiento c
  where c.embedding is not null
  order by c.embedding <=> p_embedding
  limit greatest(1, least(p_limite, 10));
$$;

-- 8. Permisos: SOLO el servidor (la app pregunta a través de POST /ia/preguntar)
revoke all on public.eventos_embeddings, public.conocimiento, public.v_eventos_sin_indexar
  from anon, authenticated;
grant select, insert, update, delete on public.eventos_embeddings, public.conocimiento to service_role;
grant select on public.v_eventos_sin_indexar to service_role;
grant usage, select on sequence public.conocimiento_id_seq to service_role;

revoke execute on function public.buscar_eventos_similares(extensions.vector, text, smallint, timestamptz, timestamptz, text, int) from public, anon, authenticated;
revoke execute on function public.buscar_conocimiento(extensions.vector, int)                from public, anon, authenticated;
revoke execute on function public.guardar_embedding_evento(uuid, extensions.vector, text)     from public, anon, authenticated;
revoke execute on function public.huella_evento(public.eventos)                               from public, anon, authenticated;
grant  execute on function public.buscar_eventos_similares(extensions.vector, text, smallint, timestamptz, timestamptz, text, int) to service_role;
grant  execute on function public.buscar_conocimiento(extensions.vector, int)                to service_role;
grant  execute on function public.guardar_embedding_evento(uuid, extensions.vector, text)     to service_role;
grant  execute on function public.huella_evento(public.eventos)                               to service_role;
