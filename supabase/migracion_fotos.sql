-- =====================================================================
--  MIGRACIÓN · Subir fotos desde la app (carteles y fotos de opiniones)
--  Ejecutar en Supabase > SQL Editor DESPUÉS de schema.sql y permisos.sql.
--  Se puede ejecutar varias veces sin problema.
--
--  Por qué: schema.sql crea los buckets 'posters' y 'resenas', pero no hay
--  ninguna regla en storage.objects, así que Supabase rechaza TODAS las
--  subidas desde la app ("new row violates row-level security policy").
-- =====================================================================

-- Los buckets existen y son públicos (las fotos se ven sin registrarse)
insert into storage.buckets (id, name, public)
values ('posters', 'posters', true), ('resenas', 'resenas', true)
on conflict (id) do update set public = true;

-- Ver las fotos: cualquiera
drop policy if exists "fotos lectura" on storage.objects;
create policy "fotos lectura" on storage.objects
  for select using (bucket_id in ('posters', 'resenas'));

-- Subir fotos: cualquier persona con sesión
drop policy if exists "fotos subida" on storage.objects;
create policy "fotos subida" on storage.objects
  for insert to authenticated
  with check (bucket_id in ('posters', 'resenas'));

-- Cambiar o borrar: solo quien subió la foto (o el moderador)
drop policy if exists "fotos edicion propia" on storage.objects;
create policy "fotos edicion propia" on storage.objects
  for update to authenticated
  using (bucket_id in ('posters', 'resenas') and (owner = auth.uid() or public.es_moderador()));

drop policy if exists "fotos borrado propio" on storage.objects;
create policy "fotos borrado propio" on storage.objects
  for delete to authenticated
  using (bucket_id in ('posters', 'resenas') and (owner = auth.uid() or public.es_moderador()));

-- ---------------------------------------------------------------------
--  Añadir o cambiar la FOTO de una opinión ya enviada (ficha del evento).
--  Solo se deja tocar la columna foto_url y solo en la reseña propia:
--  las estrellas y el comentario se siguen cambiando por la API
--  (PUT /resenas/{event_id}), como hasta ahora.
-- ---------------------------------------------------------------------
revoke update on public.resenas from authenticated;
grant update (foto_url) on public.resenas to authenticated;

drop policy if exists "resenas foto propia" on public.resenas;
create policy "resenas foto propia" on public.resenas
  for update to authenticated
  using (user_id = auth.uid())
  with check (user_id = auth.uid());
