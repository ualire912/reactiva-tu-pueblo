import { Component, computed, inject, input, model, output, signal } from '@angular/core';
import { IonIcon } from '@ionic/angular';
import { addIcons } from 'ionicons';
import { accessibility, close, navigate, optionsOutline, search } from 'ionicons/icons';
import { SupabaseService } from '../../core/supabase.service';
import { AvisosService } from '../../core/avisos.service';
import { FILTROS_INICIALES, FiltroFecha, Filtros, OPCIONES_FECHA } from '../../core/eventos.service';
import { colorPueblo, estiloCategoria } from '../../core/ui';

interface ChipActivo {
  clave: 'texto' | 'municipio' | 'categoria' | 'fecha' | 'accesible' | 'cerca';
  texto: string;
  color: string;
}

/**
 * Barra de filtros fija (pueblo, tipo, fecha, accesible, cerca de mí)
 * con una fila de chips de altura fija para que la cabecera nunca "salte".
 */
@Component({
  selector: 'app-filtros-eventos',
  standalone: true,
  imports: [IonIcon],
  templateUrl: './filtros-eventos.component.html',
})
export class FiltrosEventosComponent {
  sb = inject(SupabaseService);
  private avisos = inject(AvisosService);

  filtros = model.required<Filtros>();
  total = input<number>(0);
  /** Oculta el filtro de "Cerca de mí" (por ejemplo, en el mapa, que tiene su propio botón). */
  sinCercania = input<boolean>(false);
  /** Si se pasa, el tipo de actividad se elige con botones de color que muestran cuántos planes hay. */
  conteoCategorias = input<Record<string, number> | null>(null);

  categoriasConConteo = computed(() => {
    this.sb.catalogos();
    const conteo = this.conteoCategorias() || {};
    const elegida = this.filtros().categoria;
    return this.sb.categorias
      .map((c) => ({ slug: c.slug, nombre: c.nombre, n: conteo[c.slug] || 0, ...estiloCategoria(c.slug) }))
      .filter((c) => c.n > 0 || c.slug === elegida);
  });
  coords = output<{ lat: number; lng: number } | null>();

  readonly opcionesFecha = OPCIONES_FECHA;
  buscandoUbicacion = signal(false);

  chips = computed<ChipActivo[]>(() => {
    this.sb.catalogos();
    const f = this.filtros();
    const lista: ChipActivo[] = [];
    if (f.texto?.trim()) lista.push({ clave: 'texto', texto: '«' + f.texto.trim() + '»', color: '#24312f' });
    if (f.municipio != null) {
      const nombre = this.sb.municipios.find((m) => m.id === f.municipio)?.nombre || 'Pueblo';
      lista.push({ clave: 'municipio', texto: nombre, color: colorPueblo(nombre) });
    }
    if (f.categoria) {
      const nombre = this.sb.categorias.find((c) => c.slug === f.categoria)?.nombre || f.categoria;
      lista.push({ clave: 'categoria', texto: nombre, color: estiloCategoria(f.categoria).tinta });
    }
    if (f.fecha !== 'proximos') {
      const texto = OPCIONES_FECHA.find((o) => o.valor === f.fecha)?.texto || '';
      lista.push({ clave: 'fecha', texto, color: f.fecha === 'pasados' || f.fecha === 'cancelados' ? '#5e6866' : '#2b6f68' });
    }
    if (f.accesible) lista.push({ clave: 'accesible', texto: 'Accesible', color: '#1b4844' });
    if (f.cerca) lista.push({ clave: 'cerca', texto: 'Cerca de mí', color: '#2b6f68' });
    return lista;
  });

  /** Filtros activos salvo la actividad (que ya se ve marcada en su botón de color). */
  chipsSinCategoria = computed(() => this.chips().filter((c) => c.clave !== 'categoria'));

  constructor() {
    addIcons({ accessibility, close, navigate, optionsOutline, search });
  }

  cambiarTexto(valor: string) {
    this.actualizar({ texto: valor });
  }

  private actualizar(cambios: Partial<Filtros>) {
    this.filtros.set({ ...this.filtros(), ...cambios });
  }

  cambiarMunicipio(valor: string) {
    this.actualizar({ municipio: valor ? Number(valor) : null });
  }

  cambiarCategoria(valor: string) {
    this.actualizar({ categoria: valor || null });
  }

  cambiarFecha(valor: string) {
    this.actualizar({ fecha: (valor || 'proximos') as FiltroFecha });
  }

  alternarAccesible() {
    this.actualizar({ accesible: !this.filtros().accesible });
  }

  alternarCerca() {
    if (this.filtros().cerca) {
      this.actualizar({ cerca: false });
      this.coords.emit(null);
      return;
    }
    if (!('geolocation' in navigator)) {
      this.avisos.aviso('Tu dispositivo no permite conocer tu ubicación.');
      return;
    }
    this.buscandoUbicacion.set(true);
    navigator.geolocation.getCurrentPosition(
      (pos) => {
        this.buscandoUbicacion.set(false);
        this.coords.emit({ lat: pos.coords.latitude, lng: pos.coords.longitude });
        this.actualizar({ cerca: true });
      },
      () => {
        this.buscandoUbicacion.set(false);
        this.avisos.aviso('Para ordenar por cercanía, permite el acceso a tu ubicación.');
      },
      { timeout: 10000 }
    );
  }

  quitar(clave: ChipActivo['clave']) {
    if (clave === 'cerca') this.coords.emit(null);
    const cambios: Record<ChipActivo['clave'], Partial<Filtros>> = {
      texto: { texto: '' },
      municipio: { municipio: null },
      categoria: { categoria: null },
      fecha: { fecha: 'proximos' },
      accesible: { accesible: false },
      cerca: { cerca: false },
    };
    this.actualizar(cambios[clave]);
  }

  limpiar() {
    this.coords.emit(null);
    this.filtros.set({ ...FILTROS_INICIALES });
  }

  colorDe(nombre: string): string {
    return colorPueblo(nombre);
  }
}
