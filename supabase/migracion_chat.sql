-- =====================================================================
--  MIGRACIÓN · Chat público de cada plan sin insultos
--  Ejecutar en Supabase > SQL Editor (después de schema.sql).
--  Se puede ejecutar varias veces sin problema. NO borra ningún mensaje.
--
--  La web ya avisa antes de enviar un insulto, pero esta regla impide que
--  se guarde aunque alguien se salte la web. Solo afecta a los mensajes de
--  vecinos (los del asistente y del servidor no pasan por aquí).
--  La misma lista está en la app (core/ui.ts) y en el servidor (api/ia.py).
-- =====================================================================

create or replace function public.tg_mensaje_sin_insultos() returns trigger
language plpgsql set search_path = public as $$
declare
  t text;
begin
  if new.user_id is null then
    return new;  -- asistente o servidor
  end if;
  -- minúsculas, sin tildes, trucos típicos (gilip0llas) y letras repetidas (pollaaaa -> polla)
  t := translate(lower(coalesce(new.contenido, '')),
                 'áéíóúàèìòùäëïöüâêîôû013@4$',
                 'aeiouaeiouaeiouaeiouoieaas');
  t := regexp_replace(t, '(.)\1{2,}', '\1', 'g');
  if t ~ ('\y(gilipollas?|imbeciles?|idiotas?|estupid[oa]s?|subnormal(es)?|retrasad[oa]s?|'
          'mierdas?|put[ao]s?|putada|cabron(es|a|as)?|cabrona?zo|hij[oa]s? de puta|maricon(es)?|'
          'bolleras?|zorras?|pollas?|pollon|follar|follamos|follando|capull[oa]s?|pendej[oa]s?|'
          'mamon(es|a|as)?|me cago en|cago en|joder|jodete|jodeos|coñ[oa]|'
          'panchit[oa]s?|sudac[oa]s?|negrat[ao]s?)\y') then
    raise exception 'Este chat es público: escribe tu mensaje sin insultos ni palabrotas, por favor.'
      using errcode = 'P0001';
  end if;
  return new;
end $$;

drop trigger if exists mensajes_sin_insultos on public.mensajes_evento;
create trigger mensajes_sin_insultos
  before insert or update of contenido on public.mensajes_evento
  for each row execute function public.tg_mensaje_sin_insultos();
