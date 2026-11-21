-- =====================================================================
--  REACTIVA TU PUEBLO · Esquema Supabase (PostgreSQL)
--  Hackathon UAL 2026 · Reto 1
--  Cómo usarlo: Supabase > SQL Editor > New query > pegar TODO > Run.
--  Se puede volver a ejecutar en un proyecto vacío. Si ya existe algo,
--  ejecuta antes el bloque "RESET" del final (comentado).
-- =====================================================================

-- ---------------------------------------------------------------------
-- 0. TIPOS (listas cerradas de valores)
-- ---------------------------------------------------------------------
create type rol_usuario        as enum ('visitante','usuario','publicador','moderador');
create type estado_evento      as enum ('borrador_ia','pendiente_moderacion','abierto','pospuesto','cancelado','finalizado','rechazado');
create type origen_evento      as enum ('web','telegram','facebook','moderador');
create type estado_inscripcion as enum ('confirmada','lista_de_espera','cancelada');
create type tipo_transporte    as enum ('ninguno','ofrece','busca');
create type tipo_mensaje       as enum ('pregunta','respuesta','anuncio_fijado','asistente_ia');
create type estado_incidencia  as enum ('abierta','resuelta');
create type estado_ingesta     as enum ('nueva','extraida','esperando_confirmacion','confirmada','descartada','spam','error');

-- ---------------------------------------------------------------------
-- 1. CATÁLOGOS
-- ---------------------------------------------------------------------
create table municipios (
  id      smallserial primary key,
  nombre  text not null unique,
  lat     double precision not null,
  lng     double precision not null
);

create table categorias (
  slug    text primary key,          -- 'senderismo', 'cultura'...
  nombre  text not null,
  icono   text                        -- nombre de ionicon, p. ej. 'walk-outline'
);

-- ---------------------------------------------------------------------
-- 2. PERFILES (1 fila por usuario de Supabase Auth)
-- ---------------------------------------------------------------------
create table profiles (
  id                      uuid primary key references auth.users(id) on delete cascade,
  full_name               text,
  email                   text,
  rol                     rol_usuario not null default 'usuario',
  telegram_chat_id        bigint unique,
  telefono                text,
  telegram_linked         boolean not null default false,
  link_code               text unique default upper(substr(md5(random()::text), 1, 8)),
  redes_sociales          jsonb not null default '{}'::jsonb,   -- {"facebook":"...","instagram":"..."}
  edad                    smallint check (edad between 0 and 120),
  genero                  text,
  direccion               text,
  municipio_id            smallint references municipios(id),   -- pueblo donde vive (para "cerca de mí")
  preferencias            jsonb not null default '{"categorias":[],"municipios":[],"audio":false}'::jsonb,
  notificaciones_activas  boolean not null default true,
  reputation_score        numeric(6,2) not null default 0,
  promocion_pendiente     boolean not null default false,       -- lo marca la BD; el backend pasa el filtro IA y promueve
  created_at              timestamptz not null default now(),
  updated_at              timestamptz not null default now()
);

-- ---------------------------------------------------------------------
-- 3. ENTIDADES / PUBLICADORES (asociaciones, ayuntamientos, bares...)
-- ---------------------------------------------------------------------
create table entidades (
  id                 uuid primary key default gen_random_uuid(),
  owner_user_id      uuid references profiles(id) on delete set null,
  municipio_id       smallint references municipios(id),
  nombre             text not null,
  redes_sociales     jsonb not null default '{}'::jsonb,
  facebook_page_url  text unique,                 -- la usa el scraper para saber de quién es un post
  auto_approve       boolean not null default false,
  created_at         timestamptz not null default now()
);

-- ---------------------------------------------------------------------
-- 4. EVENTOS
-- ---------------------------------------------------------------------
create table eventos (
  id                       uuid primary key default gen_random_uuid(),
  municipio_id             smallint references municipios(id),
  entidad_organizadora_id  uuid references entidades(id) on delete set null,
  created_by               uuid references profiles(id) on delete set null,
  title                    text not null check (length(title) between 3 and 140),
  category                 text not null default 'otro' references categorias(slug),
  starts_at                timestamptz not null,
  ends_at                  timestamptz,
  duration_minutes         integer check (duration_minutes > 0),
  location_name            text,
  lat                      double precision,
  lng                      double precision,
  min_age                  smallint check (min_age >= 0),
  max_age                  smallint check (max_age >= 0),
  description              text,
  price                    numeric(8,2) not null default 0 check (price >= 0),
  max_capacity             integer check (max_capacity > 0),     -- null = sin límite
  confirmed_count          integer not null default 0,
  poster_url               text,
  status                   estado_evento,                        -- si llega vacío lo decide la BD (ver trigger)
  is_accessible            boolean not null default false,
  sequence                 integer not null default 0,           -- sube en cada cambio importante (.ics)
  origen                   origen_evento not null default 'web',
  source_url               text,                                 -- enlace al post de Facebook, si viene de ahí
  ai_confidence            numeric(3,2),                         -- 0..1 lo seguro que está la IA de la extracción
  ai_flags                 jsonb not null default '[]'::jsonb,   -- ["falta_precio","posible_spam"...]
  moderation_note          text,
  created_at               timestamptz not null default now(),
  updated_at               timestamptz not null default now(),
  check (ends_at is null or ends_at >= starts_at),
  check (max_age is null or min_age is null or max_age >= min_age)
);
create index eventos_starts_idx    on eventos (starts_at);
create index eventos_status_idx    on eventos (status);
create index eventos_municipio_idx on eventos (municipio_id);

-- ---------------------------------------------------------------------
-- 5. INSCRIPCIONES (historial) + COCHE COMPARTIDO
-- ---------------------------------------------------------------------
create table inscripciones (
  id                       uuid primary key default gen_random_uuid(),
  user_id                  uuid not null references profiles(id) on delete cascade,
  event_id                 uuid not null references eventos(id) on delete cascade,
  status                   estado_inscripcion not null default 'confirmada',
  transporte               tipo_transporte not null default 'ninguno',
  transporte_plazas        smallint not null default 0 check (transporte_plazas between 0 and 8),
  transporte_desde_municipio_id smallint references municipios(id),
  asistio                  boolean,
  created_at               timestamptz not null default now(),
  unique (user_id, event_id)
);
create index inscripciones_event_idx on inscripciones (event_id);

-- ---------------------------------------------------------------------
-- 6. RESEÑAS
-- ---------------------------------------------------------------------
create table resenas (
  id          uuid primary key default gen_random_uuid(),
  event_id    uuid not null references eventos(id) on delete cascade,
  user_id     uuid not null references profiles(id) on delete cascade,
  estrellas   smallint not null check (estrellas between 1 and 5),
  comentario  text,
  foto_url    text,
  created_at  timestamptz not null default now(),
  unique (event_id, user_id)
);

-- ---------------------------------------------------------------------
-- 7. INCIDENCIAS / MODERACIÓN
-- ---------------------------------------------------------------------
create table incidencias (
  id          uuid primary key default gen_random_uuid(),
  event_id    uuid references eventos(id) on delete cascade,
  user_id     uuid references profiles(id) on delete set null,
  comentario  text not null,
  estado      estado_incidencia not null default 'abierta',
  created_at  timestamptz not null default now()
);

-- ---------------------------------------------------------------------
-- 8. HILO / CHAT DEL EVENTO
-- ---------------------------------------------------------------------
create table mensajes_evento (
  id          uuid primary key default gen_random_uuid(),
  event_id    uuid not null references eventos(id) on delete cascade,
  user_id     uuid references profiles(id) on delete set null,   -- null cuando escribe el asistente IA
  parent_id   uuid references mensajes_evento(id) on delete cascade,
  tipo        tipo_mensaje not null default 'pregunta',
  contenido   text not null check (length(contenido) between 1 and 2000),
  created_at  timestamptz not null default now()
);
create index mensajes_event_idx on mensajes_evento (event_id, created_at);

-- ---------------------------------------------------------------------
-- 9. INGESTAS (lo que llega de Facebook/Telegram antes de ser evento)
--    Es la "bandeja de entrada" de la IA y del panel del moderador.
-- ---------------------------------------------------------------------
create table ingestas (
  id            uuid primary key default gen_random_uuid(),
  origen        origen_evento not null,
  source_url    text unique,                 -- evita procesar dos veces el mismo post
  entidad_id    uuid references entidades(id) on delete set null,
  user_id       uuid references profiles(id) on delete set null,
  raw_text      text,
  image_url     text,
  extracted     jsonb,                       -- JSON que devuelve la IA (mismos nombres que eventos)
  missing       jsonb not null default '[]'::jsonb,  -- campos que faltan y hay que preguntar
  spam_score    numeric(3,2),                -- 0 = limpio, 1 = spam seguro
  spam_reason   text,
  estado        estado_ingesta not null default 'nueva',
  event_id      uuid references eventos(id) on delete set null,
  created_at    timestamptz not null default now()
);

-- ---------------------------------------------------------------------
-- 10. NOTIFICACIONES (registro: evita avisar dos veces y da métricas)
-- ---------------------------------------------------------------------
create table notificaciones (
  id         uuid primary key default gen_random_uuid(),
  user_id    uuid not null references profiles(id) on delete cascade,
  event_id   uuid references eventos(id) on delete cascade,
  tipo       text not null,                -- 'recomendacion','cambio_hora','cancelacion','recordatorio','resena'
  canal      text not null default 'telegram',
  motivo     text,                         -- "porque te gusta senderismo y es en Tahal"
  sent_at    timestamptz not null default now(),
  unique (user_id, event_id, tipo)
);

-- =====================================================================
-- 11. FUNCIONES DE AYUDA
-- =====================================================================
create or replace function public.mi_rol() returns rol_usuario
language sql stable security definer set search_path = public as $$
  select coalesce((select rol from profiles where id = auth.uid()), 'visitante'::rol_usuario);
$$;

create or replace function public.es_moderador() returns boolean
language sql stable security definer set search_path = public as $$
  select mi_rol() = 'moderador';
$$;

-- updated_at automático
create or replace function public.tg_touch() returns trigger language plpgsql as $$
begin new.updated_at := now(); return new; end $$;
create trigger profiles_touch before update on profiles for each row execute function tg_touch();
create trigger eventos_touch  before update on eventos  for each row execute function tg_touch();

-- Al registrarse alguien en Auth se crea su perfil
create or replace function public.tg_nuevo_usuario() returns trigger
language plpgsql security definer set search_path = public as $$
begin
  insert into profiles (id, email, full_name)
  values (new.id, new.email,
          coalesce(new.raw_user_meta_data->>'full_name', new.raw_user_meta_data->>'name', split_part(new.email,'@',1)))
  on conflict (id) do nothing;
  return new;
end $$;
create trigger on_auth_user_created after insert on auth.users
  for each row execute function tg_nuevo_usuario();

-- Un usuario no puede subirse el rol ni la reputación a sí mismo
create or replace function public.tg_proteger_perfil() returns trigger
language plpgsql set search_path = public as $$
begin
  -- solo aplica a peticiones directas de la app (rol authenticated);
  -- el backend (service_role) y las funciones internas pasan
  if current_user = 'authenticated' and not es_moderador() then
    if new.rol is distinct from old.rol
       or new.reputation_score is distinct from old.reputation_score
       or new.promocion_pendiente is distinct from old.promocion_pendiente
       or new.telegram_chat_id is distinct from old.telegram_chat_id
       or new.telegram_linked is distinct from old.telegram_linked then
      raise exception 'No puedes modificar rol, reputación ni vinculación de Telegram';
    end if;
  end if;
  return new;
end $$;
create trigger profiles_proteger before update on profiles
  for each row execute function tg_proteger_perfil();

-- =====================================================================
-- 12. REGLAS DE EVENTOS (sin IA, gratis y al instante)
-- =====================================================================
create or replace function public.tg_evento_antes() returns trigger
language plpgsql security definer set search_path = public as $$
declare
  v_rol  rol_usuario;
  v_auto boolean := false;
begin
  -- duración <-> fecha fin
  if new.ends_at is null and new.duration_minutes is not null then
    new.ends_at := new.starts_at + make_interval(mins => new.duration_minutes);
  elsif new.ends_at is not null then
    new.duration_minutes := greatest(1, (extract(epoch from (new.ends_at - new.starts_at)) / 60)::int);
  end if;

  -- coordenadas por defecto = centro del pueblo
  if (new.lat is null or new.lng is null) and new.municipio_id is not null then
    select lat, lng into new.lat, new.lng from municipios where id = new.municipio_id;
  end if;

  if tg_op = 'INSERT' then
    -- Petición desde la app (hay usuario logueado): la BD decide el estado.
    -- Petición del backend (service_role, sin auth.uid()): se respeta el estado que envíe.
    if auth.uid() is not null then
      new.created_by := auth.uid();
      v_rol := mi_rol();
      if new.entidad_organizadora_id is not null then
        select auto_approve into v_auto from entidades
         where id = new.entidad_organizadora_id and owner_user_id = auth.uid();
      end if;
      if v_rol in ('publicador','moderador') or coalesce(v_auto,false) then
        new.status := 'abierto';
      else
        new.status := 'pendiente_moderacion';
      end if;
      new.confirmed_count := 0;
      new.sequence := 0;
    elsif new.status is null then
      new.status := 'pendiente_moderacion';
    end if;
  else
    -- un usuario normal no puede autoaprobarse editando
    if auth.uid() is not null and not es_moderador() and mi_rol() <> 'publicador'
       and new.status = 'abierto' and old.status <> 'abierto' then
      new.status := old.status;
    end if;
    -- si cambia algo que afecta al calendario, sube sequence (.ics)
    if new.starts_at is distinct from old.starts_at
       or new.ends_at is distinct from old.ends_at
       or new.location_name is distinct from old.location_name
       or new.status is distinct from old.status then
      new.sequence := old.sequence + 1;
    end if;
  end if;
  return new;
end $$;
create trigger eventos_antes before insert or update on eventos
  for each row execute function tg_evento_antes();

-- =====================================================================
-- 13. INSCRIPCIONES: plazas y lista de espera automáticas
-- =====================================================================
create or replace function public.tg_inscripcion_antes() returns trigger
language plpgsql security definer set search_path = public as $$
declare v_ev eventos%rowtype;
begin
  select * into v_ev from eventos where id = new.event_id for update;
  if v_ev.id is null then raise exception 'Evento no encontrado'; end if;
  if v_ev.status <> 'abierto' then raise exception 'El evento no admite inscripciones (estado: %)', v_ev.status; end if;
  if new.status = 'confirmada' and v_ev.max_capacity is not null
     and v_ev.confirmed_count >= v_ev.max_capacity then
    new.status := 'lista_de_espera';
  end if;
  return new;
end $$;
create trigger inscripciones_antes before insert on inscripciones
  for each row execute function tg_inscripcion_antes();

create or replace function public.tg_inscripcion_despues() returns trigger
language plpgsql security definer set search_path = public as $$
declare v_event uuid := coalesce(new.event_id, old.event_id); v_ev eventos%rowtype;
begin
  -- si alguien cancela, sube el primero de la lista de espera
  if tg_op = 'UPDATE' and old.status = 'confirmada' and new.status = 'cancelada' then
    update inscripciones set status = 'confirmada'
     where id = (select id from inscripciones
                  where event_id = v_event and status = 'lista_de_espera'
                  order by created_at limit 1);
  end if;
  update eventos e set confirmed_count =
    (select count(*) from inscripciones i where i.event_id = v_event and i.status = 'confirmada')
   where e.id = v_event;
  return null;
end $$;
create trigger inscripciones_despues after insert or update or delete on inscripciones
  for each row execute function tg_inscripcion_despues();

-- =====================================================================
-- 14. RESEÑAS -> REPUTACIÓN -> CANDIDATO A PUBLICADOR
--   Regla: reseñas de 4-5 estrellas suman +1, de 1-2 restan -2.
--   Es candidato si: reputación >= 5, media >= 4, >= 2 eventos con
--   reseñas y ninguna incidencia abierta. El backend pasa entonces
--   el filtro de confianza IA y cambia el rol a 'publicador'.
-- =====================================================================
create or replace function public.tg_resena_despues() returns trigger
language plpgsql security definer set search_path = public as $$
declare
  v_org uuid; v_rep numeric; v_media numeric; v_eventos int; v_inc int;
begin
  select created_by into v_org from eventos where id = new.event_id;
  if v_org is null then return null; end if;

  select coalesce(sum(case when r.estrellas >= 4 then 1 when r.estrellas <= 2 then -2 else 0 end),0),
         coalesce(avg(r.estrellas),0),
         count(distinct r.event_id)
    into v_rep, v_media, v_eventos
    from resenas r join eventos e on e.id = r.event_id
   where e.created_by = v_org;

  select count(*) into v_inc
    from incidencias i join eventos e on e.id = i.event_id
   where e.created_by = v_org and i.estado = 'abierta';

  update profiles set
    reputation_score    = v_rep,
    promocion_pendiente = (rol = 'usuario' and v_rep >= 5 and v_media >= 4 and v_eventos >= 2 and v_inc = 0)
  where id = v_org;
  return null;
end $$;
create trigger resenas_despues after insert or update on resenas
  for each row execute function tg_resena_despues();

-- Solo se puede reseñar un evento al que estabas inscrito y que ya pasó
create or replace function public.tg_resena_antes() returns trigger
language plpgsql security definer set search_path = public as $$
begin
  if auth.uid() is not null then
    new.user_id := auth.uid();
    if not exists (select 1 from inscripciones i join eventos e on e.id = i.event_id
                    where i.event_id = new.event_id and i.user_id = new.user_id
                      and i.status = 'confirmada'
                      and coalesce(e.ends_at, e.starts_at) < now()) then
      raise exception 'Solo puedes reseñar eventos terminados a los que te apuntaste';
    end if;
  end if;
  return new;
end $$;
create trigger resenas_antes before insert on resenas
  for each row execute function tg_resena_antes();

-- =====================================================================
-- 15. FUNCIONES QUE LLAMA EL FRONT / BACKEND (supabase.rpc)
-- =====================================================================

-- Recomendador por reglas (la IA solo redacta el mensaje bonito)
create or replace function public.recomendar_eventos(p_user uuid, p_limite int default 5)
returns table (event_id uuid, title text, starts_at timestamptz, municipio text, puntos int, motivos text[])
language sql stable security definer set search_path = public as $$
  with u as (
    select p.*, coalesce(p.preferencias->'categorias','[]'::jsonb) cats,
                coalesce(p.preferencias->'municipios','[]'::jsonb) munis
      from profiles p where p.id = p_user
  ), hist as (
    select distinct e.category from inscripciones i join eventos e on e.id = i.event_id
     where i.user_id = p_user and i.status = 'confirmada'
  ), c as (
    select e.id, e.title, e.starts_at, m.nombre as municipio,
      (case when u.cats ? e.category then 40 else 0 end
     + case when e.category in (select category from hist) then 15 else 0 end
     + case when u.munis ? e.municipio_id::text or e.municipio_id = u.municipio_id then 25 else 0 end
     + case when e.starts_at < now() + interval '3 days' then 10 else 0 end
     + case when e.max_capacity is null or e.confirmed_count < e.max_capacity then 10 else -50 end
      )::int as puntos,
      array_remove(array[
        case when u.cats ? e.category then 'te gusta ' || e.category end,
        case when e.category in (select category from hist) then 'ya fuiste a planes de ' || e.category end,
        case when u.munis ? e.municipio_id::text or e.municipio_id = u.municipio_id then 'es en ' || m.nombre end,
        case when e.starts_at < now() + interval '3 days' then 'es en los próximos días' end,
        case when e.max_capacity is not null and e.max_capacity - e.confirmed_count <= 5
             and e.confirmed_count < e.max_capacity then 'quedan pocas plazas' end
      ], null) as motivos
    from eventos e
    cross join u
    left join municipios m on m.id = e.municipio_id
    where e.status = 'abierto'
      and e.starts_at between now() and now() + interval '21 days'
      and (u.edad is null or e.min_age is null or u.edad >= e.min_age)
      and (u.edad is null or e.max_age is null or u.edad <= e.max_age)
      and not exists (select 1 from inscripciones i where i.event_id = e.id and i.user_id = p_user)
  )
  select id, title, starts_at, municipio, puntos, motivos from c
   where puntos > 0
   order by puntos desc, starts_at
   limit p_limite;
$$;

-- Vincular Telegram: el backend la llama cuando recibe /start VINCULAR-<code>
create or replace function public.vincular_telegram(p_code text, p_chat_id bigint)
returns uuid language plpgsql security definer set search_path = public as $$
declare v_id uuid;
begin
  update profiles set telegram_chat_id = p_chat_id, telegram_linked = true,
                      link_code = upper(substr(md5(random()::text),1,8))
   where link_code = upper(p_code)
   returning id into v_id;
  return v_id;  -- null = código no válido
end $$;
revoke execute on function public.vincular_telegram(text, bigint) from public, anon, authenticated;

-- =====================================================================
-- 16. VISTAS PARA EL PANEL DEL MODERADOR
-- =====================================================================
create or replace view v_eventos_stats with (security_invoker = true) as
select e.id, e.title, e.starts_at, e.status, m.nombre as municipio, e.category,
       e.max_capacity, e.confirmed_count,
       count(i.*) filter (where i.status = 'lista_de_espera') as en_espera,
       count(i.*) filter (where i.asistio)                    as asistieron,
       count(i.*) filter (where i.transporte = 'ofrece')      as coches_ofrecidos,
       count(i.*) filter (where i.transporte = 'busca')       as buscan_coche,
       (select round(avg(r.estrellas),2) from resenas r where r.event_id = e.id) as media_estrellas
from eventos e
left join municipios m on m.id = e.municipio_id
left join inscripciones i on i.event_id = e.id
group by e.id, m.nombre;

-- Eventos que se pisan en hora (en pueblos distintos, misma categoría o mismo día)
create or replace view v_solapamientos with (security_invoker = true) as
select a.id as evento_a, a.title as titulo_a, ma.nombre as pueblo_a,
       b.id as evento_b, b.title as titulo_b, mb.nombre as pueblo_b,
       greatest(a.starts_at, b.starts_at) as desde
from eventos a
join eventos b on a.id < b.id
left join municipios ma on ma.id = a.municipio_id
left join municipios mb on mb.id = b.municipio_id
where a.status in ('abierto','pendiente_moderacion') and b.status in ('abierto','pendiente_moderacion')
  and tstzrange(a.starts_at, coalesce(a.ends_at, a.starts_at + interval '2 hours'))
   && tstzrange(b.starts_at, coalesce(b.ends_at, b.starts_at + interval '2 hours'));

create or replace view v_candidatos_publicador with (security_invoker = true) as
select id, full_name, reputation_score, telegram_linked from profiles
 where promocion_pendiente and rol = 'usuario';

-- =====================================================================
-- 17. SEGURIDAD (RLS): quién puede leer/escribir qué desde la app
--     El backend usa la service_role key y se salta estas reglas.
-- =====================================================================
alter table municipios      enable row level security;
alter table categorias      enable row level security;
alter table profiles        enable row level security;
alter table entidades       enable row level security;
alter table eventos         enable row level security;
alter table inscripciones   enable row level security;
alter table resenas         enable row level security;
alter table incidencias     enable row level security;
alter table mensajes_evento enable row level security;
alter table ingestas        enable row level security;
alter table notificaciones  enable row level security;

-- Catálogos: todo el mundo lee
create policy "catalogo lectura" on municipios for select using (true);
create policy "catalogo lectura" on categorias for select using (true);
create policy "catalogo moderador" on municipios for all using (es_moderador()) with check (es_moderador());
create policy "catalogo moderador" on categorias for all using (es_moderador()) with check (es_moderador());

-- Perfiles: cada uno el suyo; moderador todos
create policy "perfil propio lectura" on profiles for select using (id = auth.uid() or es_moderador());
create policy "perfil propio edicion" on profiles for update using (id = auth.uid() or es_moderador());

-- Entidades: públicas en lectura; el dueño edita (salvo auto_approve, que es del moderador)
create policy "entidades lectura" on entidades for select using (true);
create policy "entidades alta"    on entidades for insert with check (owner_user_id = auth.uid() and auto_approve = false);
create policy "entidades edicion" on entidades for update using (owner_user_id = auth.uid() or es_moderador());

-- Eventos: visitantes ven los publicados; cada cual ve los suyos; moderador todo
create policy "eventos lectura" on eventos for select using (
  status in ('abierto','pospuesto','cancelado','finalizado')
  or created_by = auth.uid() or es_moderador());
create policy "eventos alta"    on eventos for insert with check (auth.uid() is not null);
create policy "eventos edicion" on eventos for update using (created_by = auth.uid() or es_moderador());
create policy "eventos borrado" on eventos for delete using (es_moderador());

-- Inscripciones: cada uno las suyas; el organizador ve las de su evento
create policy "insc lectura" on inscripciones for select using (
  user_id = auth.uid() or es_moderador()
  or exists (select 1 from eventos e where e.id = event_id and e.created_by = auth.uid()));
create policy "insc alta"    on inscripciones for insert with check (user_id = auth.uid());
create policy "insc edicion" on inscripciones for update using (
  user_id = auth.uid() or es_moderador()
  or exists (select 1 from eventos e where e.id = event_id and e.created_by = auth.uid()));

-- Reseñas: públicas en lectura
create policy "resenas lectura" on resenas for select using (true);
create policy "resenas alta"    on resenas for insert with check (auth.uid() is not null);
create policy "resenas moderador" on resenas for delete using (es_moderador());

-- Incidencias: cualquiera registrado reporta; el moderador gestiona
create policy "inc alta"      on incidencias for insert with check (user_id = auth.uid());
create policy "inc lectura"   on incidencias for select using (user_id = auth.uid() or es_moderador());
create policy "inc gestion"   on incidencias for update using (es_moderador());

-- Chat del evento: lectura pública si el evento es visible; escribir registrado
create policy "chat lectura" on mensajes_evento for select using (
  exists (select 1 from eventos e where e.id = event_id
          and (e.status in ('abierto','pospuesto','cancelado','finalizado') or es_moderador())));
create policy "chat alta" on mensajes_evento for insert with check (
  user_id = auth.uid() and tipo in ('pregunta','respuesta')
  or (tipo = 'anuncio_fijado' and exists (select 1 from eventos e where e.id = event_id
                                           and (e.created_by = auth.uid() or es_moderador()))));
create policy "chat borrado" on mensajes_evento for delete using (user_id = auth.uid() or es_moderador());

-- Ingestas y notificaciones: solo moderador (el backend entra con service_role)
create policy "ingestas moderador" on ingestas for all using (es_moderador()) with check (es_moderador());
create policy "notif propias" on notificaciones for select using (user_id = auth.uid() or es_moderador());

grant execute on function public.recomendar_eventos(uuid, int) to authenticated;

-- =====================================================================
-- 18. TIEMPO REAL (chat y cambios de evento en directo en la app)
-- =====================================================================
do $$ begin
  alter publication supabase_realtime add table mensajes_evento, eventos, inscripciones;
exception when others then raise notice 'Realtime: activa las tablas a mano en Database > Replication (%).', sqlerrm;
end $$;

-- =====================================================================
-- 19. STORAGE: carpeta pública para carteles y fotos de reseñas
-- =====================================================================
do $$ begin
  insert into storage.buckets (id, name, public) values ('posters','posters',true) on conflict (id) do nothing;
  insert into storage.buckets (id, name, public) values ('resenas','resenas',true) on conflict (id) do nothing;
exception when others then raise notice 'Storage: crea los buckets posters y resenas a mano (%).', sqlerrm;
end $$;

-- =====================================================================
-- 20. DATOS INICIALES
-- =====================================================================
insert into categorias (slug, nombre, icono) values
  ('senderismo','Senderismo','walk-outline'),
  ('deporte','Deporte','bicycle-outline'),
  ('cultura','Cultura','library-outline'),
  ('gastronomia','Gastronomía','restaurant-outline'),
  ('fiestas','Fiestas','musical-notes-outline'),
  ('taller','Taller','construct-outline'),
  ('voluntariado','Voluntariado','heart-outline'),
  ('otro','Otro','ellipsis-horizontal-outline');

-- Coordenadas aproximadas del centro de cada pueblo (revisadlas en el mapa)
insert into municipios (nombre, lat, lng) values
  ('Lucainena de las Torres', 37.0406, -2.2017),
  ('Velefique',               37.1936, -2.4019),
  ('Senés',                   37.1925, -2.3488),
  ('Uleila del Campo',        37.1847, -2.2042),
  ('Tahal',                   37.2228, -2.2856);

-- Eventos de demo (sin autor) para que el Frontend tenga datos desde el minuto 1
insert into eventos (municipio_id, title, category, starts_at, duration_minutes, location_name, description, price, max_capacity, status, origen, is_accessible)
select m.id, v.title, v.cat, date_trunc('day', now()) + v.dias * interval '1 day' + v.hora * interval '1 hour',
       v.dur, v.lugar, v.descr, v.precio, v.plazas, 'abierto', 'moderador', v.acc
from (values
  ('Lucainena de las Torres','Ruta en bici por la Vía Verde','deporte', 1, 9, 180, 'Antigua estación', 'Ruta suave de 25 km por el trazado del tren minero.', 0, 20, false),
  ('Velefique','Subida al mirador de Velefique','senderismo', 2, 10, 150, 'Plaza de la Iglesia', 'Paseo guiado con vistas a la Sierra de los Filabres.', 0, 25, false),
  ('Tahal','Taller de esparto','taller', 3, 17, 120, 'Centro social', 'Aprende a trenzar esparto con artesanas del pueblo.', 5, 12, true),
  ('Senés','Migas solidarias','gastronomia', 4, 13, 180, 'Plaza Mayor', 'Comida popular a beneficio de la asociación de mayores.', 6, 80, true),
  ('Uleila del Campo','Cine de verano en invierno','cultura', 5, 19, 120, 'Salón municipal', 'Proyección y charla con vecinos.', 0, null, true)
) as v(pueblo, title, cat, dias, hora, dur, lugar, descr, precio, plazas, acc)
join municipios m on m.nombre = v.pueblo;

-- =====================================================================
-- RESET (solo si necesitáis empezar de cero; descomentad y ejecutad)
-- =====================================================================
-- drop table if exists notificaciones, ingestas, mensajes_evento, incidencias, resenas,
--   inscripciones, eventos, entidades, profiles, categorias, municipios cascade;
-- drop view if exists v_eventos_stats, v_solapamientos, v_candidatos_publicador;
-- drop type if exists rol_usuario, estado_evento, origen_evento, estado_inscripcion,
--   tipo_transporte, tipo_mensaje, estado_incidencia, estado_ingesta cascade;
-- drop trigger if exists on_auth_user_created on auth.users;
