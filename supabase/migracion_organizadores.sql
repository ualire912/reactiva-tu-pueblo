-- =====================================================================
--  MIGRACIÓN · Devolver el organizador real a los planes rescatados por un moderador
--  Ejecutar UNA vez en Supabase > SQL Editor (repetirla no cambia nada más).
--
--  Antes, al pulsar "Crear plan" en "Detectados por IA", el plan se guardaba con el
--  moderador como autor, y la web enseñaba "Organiza <moderador>". Desde ahora lo crea
--  el servidor con el autor correcto. Esto arregla los planes que ya se crearon así:
--  se les pone la asociación de la publicación y, si se conoce, la persona que la propuso.
--  Solo toca planes cuyo autor actual es un moderador y que vienen de una publicación.
-- =====================================================================

update public.eventos e
   set entidad_organizadora_id = coalesce(e.entidad_organizadora_id, i.entidad_id),
       created_by = coalesce(i.user_id, ent.owner_user_id)
  from public.ingestas i
  left join public.entidades ent on ent.id = i.entidad_id
 where i.event_id = e.id
   and e.created_by in (select id from public.profiles where rol = 'moderador')
   and coalesce(i.user_id, ent.owner_user_id) is distinct from e.created_by;
