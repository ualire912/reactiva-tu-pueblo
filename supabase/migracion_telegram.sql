-- =====================================================================
--  MIGRACIÓN · Enlazar Telegram con la web y entrar con Telegram
--  Ejecutar en Supabase > SQL Editor. Se puede ejecutar varias veces.
--  NO borra ninguna cuenta ni ningún plan.
--
--  1) Arregla "Conectar con Telegram" desde la web. Fallaba por dos motivos:
--     · el bot enviaba "VINCULAR-ABCD1234" y la BD buscaba "ABCD1234";
--     · si esa persona ya había hablado con el bot, Telegram tenía su propia
--       cuenta con ese chat, y el chat solo puede estar en una cuenta
--       (telegram_chat_id es único) -> error.
--     Ahora se acepta el código con o sin prefijo y, si el chat ya estaba en
--     una cuenta creada solo desde Telegram (tgNNN@reactiva.local), se pasan
--     a la cuenta de la web sus inscripciones, opiniones, mensajes, planes y
--     propuestas, y se libera el chat.
--  2) Tabla para "Entrar con Telegram" desde la web (solo la usa el servidor).
-- =====================================================================

create or replace function public.vincular_telegram(p_code text, p_chat_id bigint)
returns uuid language plpgsql security definer set search_path = public as $$
declare
  v_id    uuid;
  v_otro  uuid;
  v_email text;
  v_code  text := upper(regexp_replace(trim(coalesce(p_code, '')), '^VINCULAR-', '', 'i'));
begin
  select id into v_id from profiles where link_code = v_code;
  if v_id is null then
    return null;  -- código no válido
  end if;

  -- ¿Ese chat de Telegram ya está en otra cuenta?
  select id, email into v_otro, v_email from profiles
   where telegram_chat_id = p_chat_id and id <> v_id;

  if v_otro is not null then
    if coalesce(v_email, '') like 'tg%@reactiva.local' then
      -- Cuenta creada solo desde Telegram: lo que hizo allí pasa a la cuenta de la web
      update inscripciones i set user_id = v_id
       where i.user_id = v_otro
         and not exists (select 1 from inscripciones j where j.user_id = v_id and j.event_id = i.event_id);
      update resenas r set user_id = v_id
       where r.user_id = v_otro
         and not exists (select 1 from resenas s where s.user_id = v_id and s.event_id = r.event_id);
      update notificaciones n set user_id = v_id
       where n.user_id = v_otro
         and not exists (select 1 from notificaciones m where m.user_id = v_id
                           and m.event_id is not distinct from n.event_id and m.tipo = n.tipo);
      update mensajes_evento set user_id = v_id where user_id = v_otro;
      update incidencias     set user_id = v_id where user_id = v_otro;
      update ingestas        set user_id = v_id where user_id = v_otro;
      update eventos         set created_by = v_id where created_by = v_otro;
      update entidades       set owner_user_id = v_id where owner_user_id = v_otro;
    end if;
    -- El chat se libera de la otra cuenta (la cuenta no se borra)
    update profiles set telegram_chat_id = null, telegram_linked = false where id = v_otro;
  end if;

  update profiles
     set telegram_chat_id = p_chat_id,
         telegram_linked  = true,
         link_code        = upper(substr(md5(random()::text), 1, 8))
   where id = v_id;
  return v_id;
end $$;
revoke execute on function public.vincular_telegram(text, bigint) from public, anon, authenticated;

-- Entrar en la web con Telegram: la web pide un código, el bot lo confirma
-- y el servidor deja aquí un acceso de un solo uso (caduca a los 10 minutos).
create table if not exists public.logins_telegram (
  codigo      text primary key,
  user_id     uuid references public.profiles(id) on delete cascade,
  token_hash  text,
  created_at  timestamptz not null default now()
);
alter table public.logins_telegram enable row level security;  -- sin reglas: solo el servidor
revoke all on public.logins_telegram from anon, authenticated;
grant all on public.logins_telegram to service_role;
