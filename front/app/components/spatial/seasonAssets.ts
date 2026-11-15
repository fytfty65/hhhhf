import type { Season } from '../../lib/season';

export const SEASON_ASSET_VERSION = 'v5';
export const seasonAssets = Object.fromEntries(
  (['spring', 'summer', 'autumn', 'winter'] as Season[]).map(season => [season, {
    desktop: `/hero/v5/${season}.webp`,
    mobile: `/hero/v5/${season}-mobile.webp`,
  }]),
) as Record<Season, { desktop: string; mobile: string }>;
