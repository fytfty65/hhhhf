'use client';

import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from 'react';
import { SEASON_STORAGE_KEY, type Season } from '../lib/season';
import { JOURNEY_SEASON_KEY, JOURNEY_SEASONS, restoreJourneySeason } from '../lib/journeySeason';

const JourneySeasonContext = createContext<{ season: Season; enterSeason: (season: Season) => void } | null>(null);

export function JourneySeasonProvider({ children }: { children: ReactNode }) {
  const [season, setSeason] = useState<Season>('spring');
  const enterSeason = useCallback((next: Season) => {
    setSeason(next);
    document.documentElement.dataset.journeySeason = next;
    try { localStorage.setItem(JOURNEY_SEASON_KEY, next); } catch { /* In-memory theme remains usable. */ }
  }, []);

  useEffect(() => {
    const restore = () => {
      let next: Season;
      try { next = restoreJourneySeason(localStorage.getItem(JOURNEY_SEASON_KEY), localStorage.getItem(SEASON_STORAGE_KEY)); }
      catch { next = restoreJourneySeason(null, null); }
      setSeason(next);
      document.documentElement.dataset.journeySeason = next;
    };
    restore();
    const sync = (event: StorageEvent) => { if (event.key === JOURNEY_SEASON_KEY || event.key === null) restore(); };
    window.addEventListener('storage', sync);
    return () => window.removeEventListener('storage', sync);
  }, []);

  return <JourneySeasonContext.Provider value={{ season, enterSeason }}>{children}</JourneySeasonContext.Provider>;
}

export function useJourneySeason() {
  const context = useContext(JourneySeasonContext);
  if (!context) throw new Error('Journey season requires JourneySeasonProvider');
  return { ...context, story: JOURNEY_SEASONS[context.season] };
}
