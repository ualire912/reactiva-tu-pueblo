import { inject } from '@angular/core';
import { CanActivateFn, Router } from '@angular/router';
import { SupabaseService } from './supabase.service';

export const authGuard: CanActivateFn = async (_route, state) => {
  const sb = inject(SupabaseService);
  const router = inject(Router);

  if (sb.currentSession) {
    return true;
  }

  const { data } = await sb.client.auth.getSession();
  if (data.session) {
    return true;
  }

  // Se explica por qué hace falta entrar y, después, se vuelve a donde iba
  return router.createUrlTree(['/login'], {
    queryParams: { volver: state.url, motivo: state.url.startsWith('/proponer') ? 'proponer' : null },
  });
};

export const moderadorGuard: CanActivateFn = async () => {
  const sb = inject(SupabaseService);
  const router = inject(Router);

  let profile = sb.currentProfile;
  if (!profile) {
    const { data } = await sb.client.auth.getSession();
    if (data.session?.user) {
      profile = await sb.loadProfile(data.session.user.id);
    }
  }

  if (profile?.rol === 'moderador') {
    return true;
  }

  return router.createUrlTree(['/agenda']);
};