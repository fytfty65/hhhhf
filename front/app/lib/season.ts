export const SEASONS = ['spring', 'summer', 'autumn', 'winter'] as const;
export type Season = (typeof SEASONS)[number];
export type SeasonMode = 'date' | 'manual' | 'demo';

export function seasonFromDate(date = new Date()): Season {
  const month = date.getMonth() + 1;
  if (month >= 3 && month <= 5) return 'spring';
  if (month >= 6 && month <= 8) return 'summer';
  if (month >= 9 && month <= 11) return 'autumn';
  return 'winter';
}

export const SEASON_STORAGE_KEY = 'omni_season_preferences';
export const SEASON_DEMO_INTERVAL_MS = 24_000;

export interface SeasonPreferences {
  season: Season;
  mode: SeasonMode;
  motionEnabled: boolean;
}

export function nextSeason(season: Season): Season {
  return SEASONS[(SEASONS.indexOf(season) + 1) % SEASONS.length];
}

/** Storage is optional and untrusted; invalid fields fall back independently. */
export function restoreSeasonPreferences(raw: string | null, date = new Date()): SeasonPreferences {
  const defaults: SeasonPreferences = { season: seasonFromDate(date), mode: 'date', motionEnabled: true };
  if (!raw) return defaults;
  try {
    const value: unknown = JSON.parse(raw);
    if (!value || typeof value !== 'object' || Array.isArray(value)) return defaults;
    const saved = value as Record<string, unknown>;
    const mode: SeasonMode = saved.mode === 'manual' || saved.mode === 'demo' ? saved.mode : 'date';
    const season = typeof saved.season === 'string' && SEASONS.includes(saved.season as Season)
      ? saved.season as Season : defaults.season;
    return {
      mode,
      season: mode === 'date' ? defaults.season : season,
      motionEnabled: typeof saved.motionEnabled === 'boolean' ? saved.motionEnabled : true,
    };
  } catch {
    return defaults;
  }
}

export function canAnimateSeason(motionEnabled: boolean, reducedMotion: boolean, visible: boolean, busy: boolean): boolean {
  return motionEnabled && !reducedMotion && visible && !busy;
}

export const SEASON_LABELS: Record<Season, { name: string; hint: string; accent: string }> = {
  spring: { name: '春', hint: '花开成路', accent: '#d66b5d' },
  summer: { name: '夏', hint: '山野葱茏', accent: '#39856a' },
  autumn: { name: '秋', hint: '层林尽染', accent: '#bd6b35' },
  winter: { name: '冬', hint: '雪落山河', accent: '#6385a4' },
};
