-- =====================================================================
--  PERMISOS (GRANT) · ejecutar en Supabase > SQL Editor DESPUÉS de schema.sql
--  Los proyectos nuevos de Supabase no dan permisos automáticos sobre las
--  tablas creadas por SQL. Esto da a cada rol SOLO lo que necesita;
--  las reglas RLS siguen decidiendo qué filas ve y toca cada usuario.
--  Se puede ejecutar varias veces sin problema.
-- =====================================================================

grant usage on schema public to anon, authenticated, service_role;

-- SERVIDOR (secret key): todo
grant select, insert, update, delete on all tables in schema public to service_role;
grant usage, select on all sequences in schema public to service_role;
grant execute on all functions in schema public to service_role;

-- APP: leer tablas y vistas (RLS filtra las filas)
grant select on all tables in schema public to anon, authenticated;
grant usage, select on all sequences in schema public to authenticated;

-- APP con sesión: escribir SOLO en tablas (nunca en vistas; RLS decide)
grant insert, update, delete on
  municipios, categorias, profiles, entidades, eventos, inscripciones,
  resenas, incidencias, mensajes_evento, ingestas, notificaciones
  to authenticated;

-- Las vistas son solo de lectura (perfiles_publicos NO debe permitir editar perfiles)
revoke insert, update, delete, truncate on perfiles_publicos, v_eventos_stats,
  v_solapamientos, v_candidatos_publicador from anon, authenticated;

-- Funciones que usan las reglas RLS y la app
grant execute on function
  public.mi_rol(), public.es_moderador(), public.puede_gestionar_evento(uuid),
  public.estados_automaticos(),
  public.estado_automatico(timestamptz, timestamptz, timestamptz, int, int)
  to anon, authenticated;
grant execute on function public.recomendar_eventos(uuid, int) to authenticated;

-- Funciones SOLO del servidor (por si acaso, se quitan a la app)
revoke execute on function public.vincular_telegram(text, bigint),
  public.actualizar_estados_eventos(), public.recalcular_reputacion(uuid)
  from public, anon, authenticated;

-- Tablas que se creen en el futuro: mismos permisos de base
alter default privileges in schema public grant select, insert, update, delete on tables to service_role;
alter default privileges in schema public grant usage, select on sequences to service_role;
alter default privileges in schema public grant execute on functions to service_role;
alter default privileges in schema public grant select on tables to anon, authenticated;
