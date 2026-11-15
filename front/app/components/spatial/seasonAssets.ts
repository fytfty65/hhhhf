import type { Season } from '../../lib/season';

export const SEASON_ASSET_VERSION = 'v3';
export const seasonAssets: Record<Season, { desktop: string; mobile: string; desktopCandidates?: string[]; mobileCandidates?: string[] }> = {
  spring: {
    desktop: '/hero/v1/spring-desktop.svg', mobile: '/hero/v1/spring-mobile.svg',
    desktopCandidates: ['/hero/v4/spring-pbr.webp', '/hero/v3/spring-cinematic.png', '/hero/v3/spring-desktop.png', '/hero/v2/spring-desktop.webp', '/hero/v2/spring-desktop.svg', '/hero/v1/spring-desktop.webp', '/hero/v1/spring-desktop.svg'],
    mobileCandidates: ['/hero/v4/spring-pbr-mobile.webp', '/hero/v3/spring-cinematic-mobile.png', '/hero/v3/spring-mobile.png', '/hero/v2/spring-mobile.webp', '/hero/v2/spring-mobile.svg', '/hero/v1/spring-mobile.webp', '/hero/v1/spring-mobile.svg'],
  },
  summer: {
    desktop: '/hero/v1/summer-desktop.webp', mobile: '/hero/v1/summer-mobile.webp',
    desktopCandidates: ['/hero/v3/summer-cinematic.png', '/hero/v3/summer-desktop.png', '/hero/v1/summer-desktop.webp'],
    mobileCandidates: ['/hero/v3/summer-cinematic-mobile.png', '/hero/v3/summer-mobile.png', '/hero/v1/summer-mobile.webp'],
  },
  autumn: {
    desktop: '/hero/v1/autumn-desktop.webp', mobile: '/hero/v1/autumn-mobile.webp',
    desktopCandidates: ['/hero/v3/autumn-cinematic.png', '/hero/v3/autumn-desktop.png', '/hero/v1/autumn-desktop.webp'],
    mobileCandidates: ['/hero/v3/autumn-cinematic-mobile.png', '/hero/v3/autumn-mobile.png', '/hero/v1/autumn-mobile.webp'],
  },
  winter: {
    desktop: '/hero/v1/winter-desktop.webp', mobile: '/hero/v1/winter-mobile.webp',
    desktopCandidates: ['/hero/v3/winter-cinematic.png', '/hero/v3/winter-desktop.png', '/hero/v1/winter-desktop.webp'],
    mobileCandidates: ['/hero/v3/winter-cinematic-mobile.png', '/hero/v3/winter-mobile.png', '/hero/v1/winter-mobile.webp'],
  },
};
