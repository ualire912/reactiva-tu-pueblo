import { ChangeDetectorRef, Component, OnInit, inject } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { Router, RouterModule } from '@angular/router';
import {
  IonHeader,
  IonToolbar,
  IonTitle,
  IonButtons,
  IonButton,
  IonIcon,
  IonContent,
  IonCard,
  IonCardHeader,
  IonCardTitle,
  IonCardSubtitle,
  IonCardContent,
  IonList,
  IonItem,
  IonLabel,
  IonInput,
  IonSelect,
  IonSelectOption,
  IonToggle,
  IonChip,
  IonBadge,
  IonNote,
  IonFooter,
} from '@ionic/angular';
import { addIcons } from 'ionicons';
import {
  paperPlaneOutline,
  checkmarkCircleOutline,
  refreshOutline,
  logOutOutline,
  calendarOutline,
  volumeHighOutline,
  saveOutline,
  checkmarkCircle,
  ellipseOutline,
  notificationsOutline,
  handLeftOutline,
  micOutline,
  starOutline,
  homeOutline,
  megaphoneOutline,
  shieldCheckmarkOutline,
  heartOutline,
  personOutline,
  checkmark
} from 'ionicons/icons';
import { SupabaseService } from '../../../core/supabase.service';
import { environment } from '../../../../environments/environment';
import { Inscripcion } from '../../../core/models';
import { AvisosService } from '../../../core/avisos.service';
import { colorPueblo, estiloCategoria, etiquetaEstado, fechaCorta, partesFecha } from '../../../core/ui';

@Component({
  selector: 'app-perfil',
  templateUrl: './perfil.page.html',
  standalone: true,
  imports: [
    CommonModule,
    FormsModule,
    RouterModule,
    IonHeader,
    IonToolbar,
    IonTitle,
    IonButtons,
    IonButton,
    IonIcon,
    IonContent,
    IonCard,
    IonCardHeader,
    IonCardTitle,
    IonCardSubtitle,
    IonCardContent,
    IonList,
    IonItem,
    IonLabel,
    IonInput,
    IonSelect,
    IonSelectOption,
    IonToggle,
    IonChip,
    IonBadge,
    IonNote,
    IonFooter
  ]
})
export class PerfilPage implements OnInit {
  public sb = inject(SupabaseService);
  private router = inject(Router);
  private avisos = inject(AvisosService);
  /** La app funciona sin zone.js: tras esperar al servidor hay que avisar a Angular para que repinte. */
  private cdr = inject(ChangeDetectorRef);
  colorPueblo = colorPueblo;
  etiquetaEstado = etiquetaEstado;

  // Campos editables del perfil
  fullName = '';
  edad: number | null = null;
  municipioId: number | null = null;
  categoriasFav: string[] = [];
  municipiosFav: string[] = []; // Siempre strings: ["1", "2"]
  notificacionesActivas = true;
  prefiereAudio = false;

  misPlanes: Inscripcion[] = [];
  guardando = false;
  comprobandoTelegram = false;

  estiloCategoria = estiloCategoria;

  /** Qué significa el papel de la persona, explicado en una frase. */
  papel(rol: string | null | undefined): { nombre: string; icono: string; explica: string } {
    switch (rol) {
      case 'publicador':
        return { nombre: 'Publicador/a', icono: 'megaphone-outline', explica: 'Tus planes se publican al momento, sin esperar revisión.' };
      case 'moderador':
        return { nombre: 'Moderador/a', icono: 'shield-checkmark-outline', explica: 'Revisas y publicas los planes de la comarca.' };
      default:
        return { nombre: 'Vecino/a', icono: 'home-outline', explica: 'Puedes apuntarte a planes y proponer los tuyos.' };
    }
  }

  constructor() {
    addIcons({
      heartOutline,
      personOutline,
      checkmark,
      checkmarkCircle,
      ellipseOutline,
      notificationsOutline,
      handLeftOutline,
      micOutline,
      starOutline,
      homeOutline,
      megaphoneOutline,
      shieldCheckmarkOutline,
      paperPlaneOutline,
      checkmarkCircleOutline,
      refreshOutline,
      logOutOutline,
      calendarOutline,
      volumeHighOutline,
      saveOutline
    });
  }

  async ngOnInit() {
    await this.cargarDatos();
  }

  async ionViewWillEnter() {
    await this.cargarDatos();
  }

  // ---------- Ayudas de presentación ----------
  partesFecha = partesFecha;

  /** Rangos de edad: se guarda una edad representativa del rango (el campo de la base de datos es un número). */
  readonly rangosEdad = [
    { id: 'menor', texto: 'Menos de 18', valor: 16 },
    { id: 'joven', texto: '18 a 35', valor: 27 },
    { id: 'adulto', texto: '36 a 64', valor: 50 },
    { id: 'mayor', texto: '65 o más', valor: 70 },
    { id: 'nada', texto: 'Prefiero no decirlo', valor: null as number | null },
  ];
  /** Rango marcado ('' si todavía no ha contestado). */
  rangoEdad = '';

  private rangoDe(edad: number | null): string {
    if (edad == null) return '';
    if (edad < 18) return 'menor';
    if (edad <= 35) return 'joven';
    if (edad <= 64) return 'adulto';
    return 'mayor';
  }

  elegirRangoEdad(id: string) {
    this.rangoEdad = id;
    this.edad = this.rangosEdad.find((r) => r.id === id)?.valor ?? null;
  }

  get inicial(): string {
    return (this.fullName.trim()[0] || '?').toUpperCase();
  }

  get puebloActual(): { nombre: string; color: string } | null {
    const m = this.sb.municipios.find((x) => x.id === Number(this.municipioId));
    return m ? { nombre: m.nombre, color: colorPueblo(m.nombre) } : null;
  }

  suave(color: string): string {
    return 'color-mix(in srgb, ' + color + ' 12%, #ffffff)';
  }

  /** Solo avisa si el plan ya no sigue como estaba previsto. */
  avisoEstado(status: string | null | undefined): string {
    if (status === 'cancelado') return 'Se ha cancelado';
    if (status === 'pospuesto') return 'Se ha aplazado';
    if (status === 'finalizado') return 'Ya se celebró';
    return '';
  }

  irA(id: string) {
    document.getElementById(id)?.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }

  alternarTodosPueblos() {
    this.municipiosFav = this.municipiosFav.length === this.sb.municipios.length ? [] : this.sb.municipios.map((m) => String(m.id));
  }

  // ---------- Cambios sin guardar ----------
  private guardadoComo = '';
  guardadoReciente = false;

  private foto(): string {
    return JSON.stringify([
      this.fullName.trim(),
      this.edad,
      this.municipioId,
      [...this.categoriasFav].sort(),
      [...this.municipiosFav].sort(),
      this.notificacionesActivas,
      this.prefiereAudio,
    ]);
  }

  get hayCambios(): boolean {
    return !!this.guardadoComo && this.foto() !== this.guardadoComo;
  }

  async cargarDatos() {
    const profile = await this.sb.loadProfile();
    if (profile) {
      this.fullName = profile.full_name || '';
      this.edad = profile.edad ?? null;
      this.municipioId = profile.municipio_id ?? null;
      this.categoriasFav = [...(profile.preferencias?.categorias || [])];
      this.municipiosFav = [...(profile.preferencias?.municipios || [])].map(String);
      this.notificacionesActivas = profile.notificaciones_activas ?? true;
      this.prefiereAudio = profile.preferencias?.audio ?? false;
      this.rangoEdad = this.rangoDe(this.edad);
      this.guardadoComo = this.foto();
      this.guardadoReciente = false;
    }
    this.cdr.markForCheck();

    await this.cargarMisPlanes();
  }

  async cargarMisPlanes() {
    const uid = this.sb.currentSession?.user?.id;
    if (!uid) return;

    const { data } = await this.sb.client
      .from('inscripciones')
      .select('*, eventos(*, municipios(nombre))')
      .eq('user_id', uid)
      .neq('status', 'cancelada')
      .order('created_at', { ascending: false });

    if (data) {
      this.misPlanes = data as Inscripcion[];
    }
    this.cdr.markForCheck();
  }

  toggleCategoria(slug: string) {
    if (this.categoriasFav.includes(slug)) {
      this.categoriasFav = this.categoriasFav.filter((c) => c !== slug);
    } else {
      this.categoriasFav = [...this.categoriasFav, slug];
    }
  }

  toggleMunicipioFav(id: number) {
    const idStr = String(id);
    if (this.municipiosFav.includes(idStr)) {
      this.municipiosFav = this.municipiosFav.filter((m) => m !== idStr);
    } else {
      this.municipiosFav = [...this.municipiosFav, idStr];
    }
  }

  async guardarPerfil() {
    const uid = this.sb.currentSession?.user?.id;
    if (!uid) return;

    this.guardando = true;
    this.cdr.markForCheck();
    try {
      const { error } = await this.sb.client
        .from('profiles')
        .update({
          full_name: this.fullName.trim() || null,
          edad: this.edad ? Number(this.edad) : null,
          municipio_id: this.municipioId ? Number(this.municipioId) : null,
          notificaciones_activas: this.notificacionesActivas,
          preferencias: {
            categorias: this.categoriasFav,
            municipios: this.municipiosFav.map(String),
            audio: this.prefiereAudio
          }
        })
        .eq('id', uid);

      if (error) throw error;

      await this.sb.loadProfile(uid);
      this.guardadoComo = this.foto();
      this.guardadoReciente = true;
      this.avisos.ok('¡Guardado! Te recomendaremos planes según tus gustos.');
    } catch (err: any) {
      this.avisos.error(err, 'No hemos podido guardar tus preferencias.');
    } finally {
      this.guardando = false;
      this.cdr.markForCheck();
    }
  }

  get enlaceTelegram(): string {
    const code = this.sb.currentProfile?.link_code || '';
    const bot = environment.telegramBotUsername || 'ReactivaPuebloBot';
    return `https://t.me/${bot}?start=VINCULAR-${code}`;
  }

  async comprobarVinculacion() {
    this.comprobandoTelegram = true;
    try {
      const p = await this.sb.loadProfile();
      if (p?.telegram_linked) {
        this.avisos.ok('¡Telegram conectado! Ya recibirás los avisos en tu móvil.');
      } else {
        this.avisos.info('Aún no está conectado. En Telegram, pulsa "Iniciar" y vuelve a probar.');
      }
    } finally {
      this.comprobandoTelegram = false;
      this.cdr.markForCheck();
    }
  }

  async cerrarSesion() {
    await this.sb.signOut();
    this.avisos.info('Has cerrado sesión. ¡Hasta pronto!');
    this.router.navigateByUrl('/inicio');
  }

  formatearFecha(iso?: string): string {
    return fechaCorta(iso);
  }

}