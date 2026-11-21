-- =====================================================================
--  PARCHES B1 · ejecutar en Supabase > SQL Editor DESPUÉS de schema.sql
-- =====================================================================

-- Parche 1: nombres visibles en el chat (profiles solo deja leer el propio)
create or replace view perfiles_publicos as select id, full_name, rol from profiles;
grant select on perfiles_publicos to anon, authenticated;

-- Parche 2: poder borrar mi inscripción (para volver a apuntarme tras cancelar)
-- OJO A1: borrar NO sube a nadie de la lista de espera; cancelar (status='cancelada') sí.
drop policy if exists "insc borrar propia" on inscripciones;
create policy "insc borrar propia" on inscripciones for delete using (user_id = auth.uid());

-- =====================================================================
--  COMPROBACIONES (ejecutar y mirar el resultado)
-- =====================================================================
-- select count(*) from municipios;              -- 5
-- select count(*) from categorias;              -- 8
-- select title, status from eventos;            -- 5 eventos 'abierto'
-- select * from storage.buckets;                -- posters, resenas
-- select tablename from pg_publication_tables where pubname = 'supabase_realtime';
--                                                -- mensajes_evento, eventos, inscripciones

-- =====================================================================
--  HACER MODERADOR a quien se registre en la app (cambia el email)
-- =====================================================================
-- update profiles set rol = 'moderador' where email = 'correo.de.a2@ejemplo.com';
