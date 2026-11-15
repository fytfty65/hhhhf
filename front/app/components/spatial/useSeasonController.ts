'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import {
  canAnimateSeason, nextSeason, restoreSeasonPreferences, seasonFromDate,
  SEASON_DEMO_INTERVAL_MS, SEASON_STORAGE_KEY,
  type Season, type SeasonMode, type SeasonPreferences,
} from '../../lib/season';

/** One clock and one preference owner for every seasonal layer. */
export function useSeasonController(busy: boolean) {
  // A deterministic first render is shared with SSR. Browser state is read only after mount.
  const [preferences, setPreferences] = useState<SeasonPreferences>({
    season: 'spring', mode: 'date', motionEnabled: true,
  });
  const [ready, setReady] = useState(false);
  const [reducedMotion, setReducedMotion] = useState(true);
  const [visible, setVisible] = useState(false);

  useEffect(() => {
    let raw: string | null = null;
    try { raw = window.localStorage.getItem(SEASON_STORAGE_KEY); } catch { /* Private browsing can disable storage. */ }
    setPreferences(restoreSeasonPreferences(raw));
    setReady(true);
    const media = window.matchMedia('(prefers-reduced-motion: reduce)');
    const syncMotion = () => setReducedMotion(media.matches);
    const syncVisibility = () => setVisible(document.visibilityState !== 'hidden');
    syncMotion();
    syncVisibility();
    media.addEventListener('change', syncMotion);
    document.addEventListener('visibilitychange', syncVisibility);
    return () => {
      media.removeEventListener('change', syncMotion);
      document.removeEventListener('visibilitychange', syncVisibility);
    };
  }, []);

  useEffect(() => {
    if (!ready) return;
    try { window.localStorage.setItem(SEASON_STORAGE_KEY, JSON.stringify(preferences)); } catch { /* Preferences still work in memory. */ }
  }, [preferences, ready]);

  const { season, mode, motionEnabled } = preferences;
  const effectiveMotion = ready && canAnimateSeason(motionEnabled, reducedMotion, visible, busy);
  const motionGuard = useRef(false);
  motionGuard.current = effectiveMotion;

  useEffect(() => {
    if (!ready || mode !== 'date' || !visible || busy) return;
    const syncDate = () => setPreferences((current) => {
      const today = seasonFromDate();
      return current.mode === 'date' && current.season !== today ? { ...current, season: today } : current;
    });
    syncDate();
    // Date mode must notice a season boundary even if the tab stays open overnight.
    const timer = window.setInterval(syncDate, 60_000);
    return () => window.clearInterval(timer);
  }, [ready, mode, visible, busy]);

  useEffect(() => {
    if (mode !== 'demo' || !effectiveMotion) return;
    const timer = window.setInterval(() => {
      if (!motionGuard.current || document.hidden) return;
      setPreferences((current) => current.mode === 'demo'
        ? { ...current, season: nextSeason(current.season) } : current);
    }, SEASON_DEMO_INTERVAL_MS);
    // Resuming starts a fresh interval rather than jumping through missed seasons.
    return () => window.clearInterval(timer);
  }, [mode, effectiveMotion]);

  const setSeason = useCallback((value: Season) => {
    setPreferences((current) => ({ ...current, season: value, mode: 'manual' }));
  }, []);
  const setMode = useCallback((value: SeasonMode) => {
    setPreferences((current) => ({
      ...current, mode: value, season: value === 'date' ? seasonFromDate() : current.season,
    }));
  }, []);
  const setMotionEnabled = useCallback((value: boolean) => {
    setPreferences((current) => ({ ...current, motionEnabled: value }));
  }, []);

  return { season, mode, motionEnabled, effectiveMotion, setSeason, setMode, setMotionEnabled };
}

export default useSeasonController;
