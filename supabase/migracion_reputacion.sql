-- =====================================================================
-- MIGRACIÓN · Nuevo sistema de reputación (ejecútala una vez en Supabase > SQL editor)
--
--   1. De cada evento creado: MEDIA de sus reseñas redondeada a estrellas
--        1-2 estrellas -> -1 punto · 3 -> 0 · 4-5 -> +0,5
--   2. Reputación = SUMA de esos puntos (sin media)
--   3. Con 4 puntos, un usuario pasa a PUBLICADOR (sin revisión de la IA ni del moderador)
--
-- El backend (api/reputacion.py) ya calcula esto mismo cada 5 minutos y tras cada reseña;
-- con esta migración la BD lo hace igual al momento y ya no hay dos fórmulas distintas.
-- =====================================================================
create or replace function public.recalcular_reputacion(p_user uuid) returns void
language plpgsql security definer set search_path = public as $$
declare
  v_rep numeric;
begin
  if p_user is null then return; end if;

  select coalesce(sum(case
            when floor(m.media + 0.5) <= 2 then -1
            when floor(m.media + 0.5) = 3  then 0
            else 0.5 end), 0)
    into v_rep
    from (select avg(r.estrellas)::numeric as media
            from resenas r join eventos e on e.id = r.event_id
           where e.created_by = p_user
           group by r.event_id) m;

  update profiles set
    reputation_score    = v_rep,
    rol                 = case when rol = 'usuario' and v_rep >= 4 then 'publicador'::rol_usuario else rol end,
    promocion_pendiente = false,
    promocion_motivo    = case when rol = 'usuario' and v_rep >= 4
                               then 'Reputación de ' || v_rep || ' puntos por las reseñas de sus eventos'
                               else promocion_motivo end
  where id = p_user;
end $$;
revoke execute on function public.recalcular_reputacion(uuid) from public, anon, authenticated;

-- Recalcular ya a todos los que tienen eventos con reseñas
select public.recalcular_reputacion(created_by)
  from (select distinct e.created_by from eventos e join resenas r on r.event_id = e.id
         where e.created_by is not null) t;
