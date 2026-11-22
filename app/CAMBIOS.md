# Cambios de la app (rama a1-app / a2-panel) · 21/11/2026

## Nuevo
- `src/app/core/ui.ts`: colores por pueblo, etiquetas de estado, fechas en hora de España, textos de precio/lugar/plazas.
- `src/app/core/avisos.service.ts`: avisos en lenguaje sencillo (traduce errores de Supabase/servidor/red).
- `src/app/core/eventos.service.ts`: carga de eventos visibles + organizador (entidad o autor) + media de estrellas; filtros comunes.
- `src/app/core/geocoding.service.ts`: latitud/longitud a partir del lugar (OpenStreetMap Nominatim, sin clave).
- `src/app/components/filtros-eventos/`: barra de filtros fija con chips que se quitan (fila de altura fija).
- `src/app/components/evento-card/`: tarjeta con cartel, pueblo, precio en verde, sello rojo "Finalizado", organizador y valoración.
- `src/app/pages/public/inicio/`: página de inicio con acceso a todo. **Ruta nueva `/inicio`** (la raíz redirige aquí).
- `src/theme/reactiva.scss`: estilos globales (añadido a `angular.json` → styles).

## Modificado
- Agenda: cuadrícula paginada (6/12/24/48), solo el listado hace scroll; cabecera, filtros y pie fijos.
  Filtro de fecha: Eventos próximos (por defecto), Hoy, Este finde, Próximos 7 días, Pasados, Cancelados, Todas.
- Mapa: chinchetas con el color del pueblo, mismos filtros y lista accesible equivalente.
- Ficha: lugar con pueblo, organizador y valoración, fotos post evento (bucket `resenas`, columna `resenas.foto_url`),
  nombres en chat y opiniones (`perfiles_publicos`), "Añadir al calendario" como enlace al `.ics`.
- Proponer: busca latitud y longitud al escribir el lugar; mapa con chincheta arrastrable; envía `lat`/`lng`.
- Panel: secciones en barra lateral (en móvil, tira horizontal); textos sin jerga; estadísticas usan `confirmed_count`.
  Al cambiar el lugar de un evento también se recalculan `lat`/`lng`.
- `api.service.ts`: espera hasta 65 s (Render dormido) y lanza error con el código HTTP.
- Barra inferior más grande; tema de color cálido; modo oscuro del sistema desactivado; zoom permitido.

## A revisar con B1
- Subir foto a una opinión ya enviada hace `update resenas set foto_url` → necesita permiso UPDATE del autor sobre su reseña.
- Subida al bucket `resenas` → necesita permiso INSERT para usuarios registrados en Storage.

# Rediseño «Plaza al sol» · 21/11/2026

## Estética
- Paleta nueva en `src/theme/variables.scss`: verde olivo azulado `#2b6f68` (principal), amarillo sol `#f4b740` (acento, `secondary`), fondo crema `#fbf7f0`. Variables `--rp-*` para reutilizar.
- Rojo `#b42318` reservado solo para errores. Colores de pueblo armonizados en `core/ui.ts`.
- Tipografía: Inter (texto) y Bricolage Grotesque (títulos), en `index.html`.
- Fuera píldoras y círculos decorativos: esquinas de 6–12 px, sin degradados radiales.
- Panel de moderación: barra lateral clara (antes negra con naranja).

## Buscador
- Agenda y Mapa: buscador en la barra de filtros compartida (`filtros-eventos`), campo `texto` en `Filtros`; busca sin tildes en título, pueblo, organizador, lugar, categoría y descripción.
- Inicio: buscador en la portada que abre la agenda con `?q=`.
- Panel: un buscador que filtra la sección abierta (pendientes, IA, incidencias, publicadores, estadísticas).

## Errores en Proponer
- Al enviar, recuadro arriba con cada campo que falla, su sección y el motivo; cada enlace lleva al campo.
- Cada campo con error se marca en rojo con su mensaje debajo, y se recalcula al corregir.
- Si el servidor rechaza un dato y el mensaje cita la columna, el error se muestra en ese campo.

Copia de seguridad previa: `../app-backup-2026-11-21/src`.
- Igualado al diseño del lienzo: cabecera blanca, portada verde con colinas en Inicio y Agenda, buscador y filtros en tarjeta flotante, botones de actividad con su color y número de planes, tarjeta de plan con cabecera pastel por actividad (`estiloCategoria` en `core/ui.ts`), accesos y pueblos en tonos suaves, botones sin mayúsculas.
- Ficha del plan: cabecera pastel por actividad con fecha e icono, etiquetas suaves, título en Bricolage.
- Panel: saludo según la hora, cifras en tonos suaves (pendientes, IA, incidencias, publicados), cada sección con su color en la barra lateral, tarjetas sin borde de color, botones Rechazar · Editar · Aprobar.
- Proponer: «Cuéntanos tu plan», tira de 4 pasos que marca con «!» los que tienen errores, secciones numeradas y tipo de actividad con botones de color.
- Agenda sin portada (no repite la de Inicio); buscador, desplegables y botones de actividad fijos en la cabecera, siempre visibles al hacer scroll.
- Ventanas emergentes (`ion-alert`) con el estilo del diseño; el aviso de problema explica qué falta si se envía vacío.
- Agenda en escritorio: contador, actividades y filtros activos en una sola fila; cabecera, paginación y barra inferior más bajas para ver más planes.
- «Accesible» y «Cerca de mí» muestran siempre su texto, también en el móvil.
- Proponer: bloque del cartel con dos columnas iguales (zona para subir la foto a la misma altura que el texto), «¿Cuándo?» en 3 columnas y «Condiciones» en 4, títulos de una línea y explicación debajo de cada campo («Déjalo vacío si puede venir todo el que quiera», «Pon 0 si es gratis»…).
- Perfil · Telegram: estado claro (Conectado / Sin conectar), tres ventajas con icono y palabra clave, papel y reputación explicados en una frase, y pasos numerados para conectarlo.
- Mapa: la lista de la derecha son tarjetas blancas sobre crema, con fecha en recuadro suave del color del pueblo, etiqueta del pueblo suavizada (fondo al 12 % + punto de color) y etiqueta de actividad; ventanita de las chinchetas sin emojis y con el mismo estilo.
- Pueblos en toda la app con la misma etiqueta suave (fondo al 12 % + cuadradito de su color + nombre en su color): tarjetas, ficha, panel, pueblos favoritos del perfil. Avatares del chat y opiniones en tono suave. Actividades favoritas del perfil con su color de actividad.
- Proponer convertido en asistente guiado: primero «Tengo un cartel» o «Lo cuento yo»; después una pregunta por pantalla (Qué, Cuándo, Dónde, Detalles) con barra de progreso, botones grandes Atrás/Siguiente, atajos de día (Hoy, Mañana, Este sábado…), hora y duración, preguntas de sí/no (Es gratis / Hay que pagar, Plazas, Edades, Accesible) y una pantalla final «Revisa tu plan» con «Cambiar» en cada parte. Solo se comprueban las preguntas del paso en curso; al leer un cartel va directo al resumen.
- Inicio: invitación a publicar con los dos caminos (abren Proponer en el modo elegido con `?modo=cartel|pasos`) y «Accesos rápidos» sin el acceso duplicado. «Proponer plan» siempre visible en la Agenda.
- Proponer · último día para apuntarse: botones «Hasta que empiece», «El día antes», «2 días antes», «Una semana antes» (calculados desde el día del plan) + «Otro día» con calendario; confirmación en texto («Apuntarse hasta el …»). Campos de fecha y hora con icono; el desplegable va en un bloque crema.
- Proponer: el aviso de errores tiene más separación respecto a la barra de progreso.
- Perfil rediseñado: saludo con inicial, pueblo y papel; índice con 4 botones (Mis planes, Mis gustos, Avisos, Mis datos); planes con fecha en grande y «Tienes plaza / En lista de espera»; gustos con casillas ✓ y «Elegir todos»; avisos con preguntas de dos botones (sustituyen a los interruptores) y Telegram dentro; datos con preguntas sencillas, pueblo con botones y edad por rangos (se guarda una edad representativa: 16, 27, 50, 70; «Prefiero no decirlo» = vacío); barra fija «Guardar cambios» que avisa si hay cambios sin guardar.
- Login: campos nativos con el título encima (el autorrelleno del navegador ya no tapa los títulos) y botón para mostrar u ocultar la contraseña.
- Panel · pendientes y detectados por IA: tarjetas de la misma estructura, descripción cortada a 2 líneas y botones siempre alineados abajo a la derecha.
- Panel · IA: resumen breve (fecha, lugar, precio, qué le falta y «Parece un plan real / Puede ser publicidad»); el porqué de la IA, el enlace y el texto original van plegados en «Ver detalles y texto original».
- Panel · Publicadores: «Buscar a cualquier vecino» por nombre o correo (consulta `profiles` con ilike, máx. 20), con botón para hacerle Publicador/a o quitárselo. Necesita que la política RLS deje leer `profiles` a los moderadores.
- Estadísticas y directorio de vecinos: los estilos estaban en `src/theme/panel-graficos.scss`, registrado solo en `angular.json`, así que el servidor de desarrollo en marcha no los cargaba y todo salía como listas sin formato. Ahora se importa desde `reactiva.scss` (y se quita de `angular.json` para no duplicarlo).
- Estadísticas: aviso compacto cuando no hay inscripciones en 30 días (sin caja vacía), ranking solo con planes que tienen gente («Y N planes sin nadie apuntado»), barras y etiquetas sin forma de píldora, embudo con texto legible.
- Perfil y login: la app funciona sin zone.js, así que tras esperar al servidor la pantalla no se repintaba (se quedaba en «Guardando…» / «Cargando…»). Ahora avisan a Angular con `ChangeDetectorRef.markForCheck()` al terminar de cargar, guardar o entrar.
- Panel · editar un plan: la ventanita de 4 campos sin nombre pasa a un formulario completo en ventana (Qué, Cuándo, Dónde, Condiciones) con título y explicación en cada campo, errores por campo y botones «Guardar cambios» y «Guardar y aprobar» (este último solo en pendientes). Guarda también tipo, pueblo, duración, plazas, edades, accesibilidad y descripción.
- Ficha del plan: la pestaña «Agenda» de abajo queda marcada; los botones (calendario, Google, escuchar) separados de los datos; el cartel se amplía a pantalla completa con «Ver en grande».
- Estadísticas: «Qué te dicen los datos» en filas iguales; las 4 cifras ocupan todo el ancho.
- Panel · Todos los vecinos: filtro por pueblo (incluye «Sin pueblo indicado»; los contadores Todos/Vecinos/Publicadores respetan el pueblo), etiqueta del pueblo en cada vecino y lista por páginas de 8 con Anterior/Siguiente en lugar del scroll interno. Las consultas de vecinos traen también `municipio_id`.
