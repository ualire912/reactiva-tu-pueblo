import { Component, computed, effect, input, signal } from '@angular/core';
import { RouterModule } from '@angular/router';
import { IonIcon } from '@ionic/angular';
import { addIcons } from 'ionicons';
import { chevronBack, chevronForward, todayOutline } from 'ionicons/icons';
import { EventoVista } from '../../core/eventos.service';
import { estiloCategoria, haTerminado, iconoCategoria, partesFecha, textoPrecio, ymdMadrid } from '../../core/ui';
import { TiempoEventoComponent } from '../tiempo-evento/tiempo-evento.component';

interface Dia {
  clave: string; // AAAA-MM-DD
  numero: number;
  delMes: boolean;
  hoy: boolean;
  pasado: boolean;
  eventos: EventoVista[];
}

const NOMBRES_DIA = ['Lun', 'Mar', 'Mié', 'Jue', 'Vie', 'Sáb', 'Dom'];
const fmtMes = new Intl.DateTimeFormat('es-ES', { month: 'long', year: 'numeric' });
const fmtDiaLargo = new Intl.DateTimeFormat('es-ES', { weekday: 'long', day: 'numeric', month: 'long', year: 'numeric' });

/** Calendario mensual con los planes: se cambia de mes y al tocar un día se ven sus planes. */
@Component({
  selector: 'app-calendario-eventos',
  standalone: true,
  imports: [RouterModule, IonIcon, TiempoEventoComponent],
  templateUrl: './calendario-eventos.component.html',
})
export class CalendarioEventosComponent {
  eventos = input<EventoVista[]>([]);

  readonly nombresDia = NOMBRES_DIA;
  readonly maxEnCelda = 3;

  /** Primer día del mes que se está viendo. */
  mes = signal(primeroDeMes(new Date()));
  seleccionado = signal<string>(ymdMadrid(new Date()));

  estiloCategoria = estiloCategoria;
  iconoCategoria = iconoCategoria;
  partesFecha = partesFecha;
  textoPrecio = textoPrecio;

  constructor() {
    addIcons({ chevronBack, chevronForward, todayOutline });
    // Al cambiar de mes, se selecciona el primer día con planes (o hoy si es este mes)
    effect(() => {
      const dias = this.dias().filter((d) => d.delMes);
      const hoy = dias.find((d) => d.hoy);
      const conPlanes = dias.find((d) => d.eventos.length && !d.pasado) || dias.find((d) => d.eventos.length);
      const actual = this.seleccionado();
      if (!dias.some((d) => d.clave === actual)) this.seleccionado.set((hoy || conPlanes || dias[0]).clave);
    });
  }

  tituloMes = computed(() => {
    const t = fmtMes.format(this.mes());
    return t.charAt(0).toUpperCase() + t.slice(1);
  });

  /** Planes agrupados por día (hora de España). */
  private porDia = computed(() => {
    const mapa = new Map<string, EventoVista[]>();
    for (const ev of this.eventos()) {
      if (!ev.starts_at) continue;
      const clave = ymdMadrid(new Date(ev.starts_at));
      const lista = mapa.get(clave) || [];
      lista.push(ev);
      mapa.set(clave, lista);
    }
    for (const lista of mapa.values()) lista.sort((a, b) => a.starts_at.localeCompare(b.starts_at));
    return mapa;
  });

  /** 6 semanas × 7 días, empezando en lunes. */
  dias = computed<Dia[]>(() => {
    const primero = this.mes();
    const desfase = (primero.getDay() + 6) % 7; // lunes = 0
    const hoy = ymdMadrid(new Date());
    const dias: Dia[] = [];
    for (let i = 0; i < 42; i++) {
      const d = new Date(primero.getFullYear(), primero.getMonth(), 1 - desfase + i, 12);
      const clave = ymdMadrid(d);
      dias.push({
        clave,
        numero: d.getDate(),
        delMes: d.getMonth() === primero.getMonth(),
        hoy: clave === hoy,
        pasado: clave < hoy,
        eventos: this.porDia().get(clave) || [],
      });
    }
    // Si la última semana es entera del mes siguiente, se quita
    return dias.slice(35).every((d) => !d.delMes) ? dias.slice(0, 35) : dias;
  });

  totalMes = computed(() => this.dias().filter((d) => d.delMes).reduce((n, d) => n + d.eventos.length, 0));

  diaSeleccionado = computed(() => this.dias().find((d) => d.clave === this.seleccionado()) || null);

  tituloDia = computed(() => {
    const [a, m, d] = this.seleccionado().split('-').map(Number);
    const t = fmtDiaLargo.format(new Date(a, m - 1, d, 12));
    return t.charAt(0).toUpperCase() + t.slice(1);
  });

  cambiarMes(salto: number) {
    const m = this.mes();
    this.mes.set(new Date(m.getFullYear(), m.getMonth() + salto, 1, 12));
  }

  irAHoy() {
    this.mes.set(primeroDeMes(new Date()));
    this.seleccionado.set(ymdMadrid(new Date()));
  }

  elegir(dia: Dia) {
    if (!dia.delMes) {
      const [a, m] = dia.clave.split('-').map(Number);
      this.mes.set(new Date(a, m - 1, 1, 12));
    }
    this.seleccionado.set(dia.clave);
  }

  /** Flechas del teclado para moverse entre días. */
  teclado(evento: KeyboardEvent, dia: Dia) {
    const saltos: Record<string, number> = { ArrowLeft: -1, ArrowRight: 1, ArrowUp: -7, ArrowDown: 7 };
    const salto = saltos[evento.key];
    if (!salto) return;
    evento.preventDefault();
    const [a, m, d] = dia.clave.split('-').map(Number);
    const nueva = new Date(a, m - 1, d + salto, 12);
    if (nueva.getMonth() !== this.mes().getMonth()) this.mes.set(primeroDeMes(nueva));
    this.seleccionado.set(ymdMadrid(nueva));
    setTimeout(() => (document.querySelector(`[data-dia="${ymdMadrid(nueva)}"]`) as HTMLElement | null)?.focus());
  }

  etiquetaDia(dia: Dia): string {
    const n = dia.eventos.length;
    return `${dia.numero}${dia.hoy ? ', hoy' : ''}: ${n ? n + (n === 1 ? ' plan' : ' planes') : 'sin planes'}`;
  }

  terminado(ev: EventoVista): boolean {
    return ev.status !== 'cancelado' && haTerminado(ev);
  }
}

function primeroDeMes(d: Date): Date {
  return new Date(d.getFullYear(), d.getMonth(), 1, 12);
}
