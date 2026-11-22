-- =====================================================================
--  MIGRACIÓN · Los planes de Publicadores se publican sin moderador
--  Ejecutar en Supabase > SQL Editor. Se puede ejecutar varias veces.
--  NO borra ni cambia ningún plan.
--
--  Vuelve a instalar la versión oficial (la de schema.sql) de la regla que
--  decide el estado de un plan nuevo creado desde la web:
--    · Publicador/a, moderador/a o asociación con aprobación automática
--      -> se publica al momento ('abierto').
--    · Resto de vecinos -> 'pendiente_moderacion'.
--  Desde Telegram ya lo decide el servidor (api/ingesta.py) con la misma regla.
--  Útil si la base de datos se creó con una versión anterior de schema.sql.
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
    -- un usuario normal no puede autoaprobarse editando (pasar de pendiente a publicado)
    if auth.uid() is not null and not es_moderador() and mi_rol() <> 'publicador'
       and new.status = any(estados_automaticos())
       and not (old.status = any(estados_automaticos())) then
      new.status := old.status;
    end if;
  end if;

  -- Estado automático: si el evento está publicado, la BD decide si está
  -- abierto, completo, con inscripción cerrada, en curso o finalizado.
  -- (pendiente, pospuesto, cancelado, rechazado y borrador no se tocan)
  if new.status = any(estados_automaticos()) then
    new.status := estado_automatico(new.starts_at, new.ends_at, new.inscripcion_hasta,
                                    new.max_capacity, new.confirmed_count);
  end if;

  -- si cambia algo que afecta al calendario, sube sequence (.ics)
  -- (los cambios automáticos abierto/completo/en_curso/finalizado NO cuentan)
  if tg_op = 'UPDATE' and (
       new.starts_at is distinct from old.starts_at
       or new.ends_at is distinct from old.ends_at
       or new.location_name is distinct from old.location_name
       or (new.status in ('cancelado','pospuesto')) is distinct from (old.status in ('cancelado','pospuesto'))) then
    new.sequence := old.sequence + 1;
  end if;
  return new;
end $$;
drop trigger if exists eventos_antes on eventos;
create trigger eventos_antes before insert or update on eventos
  for each row execute function tg_evento_antes();
