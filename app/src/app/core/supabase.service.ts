import { Injectable } from '@angular/core';
import { createClient, SupabaseClient, Session } from '@supabase/supabase-js';
import { BehaviorSubject } from 'rxjs';
import { environment } from '../../environments/environment';
import { Profile, RolUsuario, Municipio, Categoria } from './models';

@Injectable({
  providedIn: 'root'
})
export class SupabaseService {
  public readonly client: SupabaseClient;

  private _session$ = new BehaviorSubject<Session | null>(null);
  public readonly session$ = this._session$.asObservable();

  private _profile$ = new BehaviorSubject<Profile | null>(null);
  public readonly profile$ = this._profile$.asObservable();

  public municipios: Municipio[] = [];
  public categorias: Categoria[] = [];

  constructor() {
    const url = environment.supabaseUrl.startsWith('http') ? environment.supabaseUrl : 'https://placeholder.supabase.co';
    const key = environment.supabaseAnonKey !== 'PEGA_AQUI_SUPABASE_ANON_KEY' ? environment.supabaseAnonKey : 'placeholder-key';
    this.client = createClient(url, key);

    this.client.auth.getSession().then(({ data }) => {
      this._session$.next(data.session);
      if (data.session?.user) {
        this.loadProfile(data.session.user.id);
      }
    });

    this.client.auth.onAuthStateChange((_event, session) => {
      this._session$.next(session);
      if (session?.user) {
        this.loadProfile(session.user.id);
      } else {
        this._profile$.next(null);
      }
    });

    this.loadCatalogos();
  }

  get currentSession(): Session | null {
    return this._session$.value;
  }

  get currentProfile(): Profile | null {
    return this._profile$.value;
  }

  get rol(): RolUsuario {
    return this._profile$.value?.rol ?? 'visitante';
  }

  async loadProfile(userId?: string): Promise<Profile | null> {
    const uid = userId ?? this._session$.value?.user?.id;
    if (!uid) return null;

    const { data, error } = await this.client
      .from('profiles')
      .select('*')
      .eq('id', uid)
      .single();

    if (!error && data) {
      const profile = data as Profile;
      if (!profile.preferencias) {
        profile.preferencias = { categorias: [], municipios: [], audio: false };
      }
      this._profile$.next(profile);
      return profile;
    }
    return null;
  }

  async loadCatalogos(): Promise<void> {
    const [munRes, catRes] = await Promise.all([
      this.client.from('municipios').select('*').order('nombre'),
      this.client.from('categorias').select('*').order('nombre')
    ]);
    if (munRes.data) this.municipios = munRes.data;
    if (catRes.data) this.categorias = catRes.data;
  }

  async signOut(): Promise<void> {
    await this.client.auth.signOut();
    this._session$.next(null);
    this._profile$.next(null);
  }
}