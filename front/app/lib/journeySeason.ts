import { SEASONS, restoreSeasonPreferences, type Season } from './season';

// The journey keeps the season the traveler entered with, independently of
// the landing page's date/demo clock. This is a visual preference, not trip data.
export const JOURNEY_SEASON_KEY = 'omni_journey_season';

export const JOURNEY_TRAILS: Record<Season, string> = {
  spring: 'FOLLOW THE BLOSSOMS, FIND YOUR WAY',
  summer: 'A LITTLE DETOUR, A NEW DISCOVERY',
  autumn: 'TAKE THE SLOW ROAD, KEEP THE GOLD',
  winter: 'QUIET MOUNTAINS, WARM MEMORIES',
};

export function restoreJourneySeason(saved: string | null, portal: string | null): Season {
  return SEASONS.includes(saved as Season) ? saved as Season : restoreSeasonPreferences(portal).season;
}

export const JOURNEY_SEASONS = {
  spring: { name: '春', title: '花间漫游', english: 'SPRING', edition: '01', phrase: '循着花开的方向。', note: '把春光，写进下一站。', journal: '花开有时，出发有你', community: '把路上的春天，分享给彼此。', palette: '樱花粉 · 嫩芽绿' },
  summer: { name: '夏', title: '山海来信', english: 'SUMMER', edition: '02', phrase: '去山海，等一阵风。', note: '把日子，交给山海与长夏。', journal: '山海辽阔，夏日悠长', community: '沿着夏日的风，遇见同行的人。', palette: '青绿 · 日光黄' },
  autumn: { name: '秋', title: '拾光远行', english: 'AUTUMN', edition: '03', phrase: '赴一场山野的秋。', note: '慢一点，收集沿途的金色。', journal: '山野染秋，沿途拾光', community: '收藏一段秋色，留给下一位旅人。', palette: '杏橙 · 枫叶红' },
  winter: { name: '冬', title: '寻雪而行', english: 'WINTER', edition: '04', phrase: '向雪落的远方出发。', note: '在安静的季节，遇见新的风景。', journal: '雪落无声，旅途有暖', community: '让冬日的故事，温暖下一程。', palette: '雪白 · 鸢尾紫' },
} as const;
