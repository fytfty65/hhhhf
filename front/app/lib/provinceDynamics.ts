// 模块 7 · 省数据动态化纯函数层（前端）
// 与服务端 gateway/internal/service/province.go 逻辑镜像，用于「本地即时排序」兜底。

export type Season = 'spring' | 'summer' | 'autumn' | 'winter';

/** 依据公历月份返回所属季节。 */
export function seasonOf(date: Date): Season {
  const m = date.getMonth() + 1;
  if (m >= 3 && m <= 5) return 'spring';
  if (m >= 6 && m <= 8) return 'summer';
  if (m >= 9 && m <= 11) return 'autumn';
  return 'winter';
}

/** 季节中文标签，用于 UI 角标。 */
export function seasonLabel(season: Season): string {
  return { spring: '春季', summer: '夏季', autumn: '秋季', winter: '冬季' }[season];
}

const SEASON_KEYWORDS: Record<Season, string[]> = {
  spring: ['赏花', '樱', '桃', '油菜', '郁金香', '踏青', '花海', '春'],
  summer: ['避暑', '海滨', '沙滩', '漂流', '瀑布', '海岛', '水上', '荷花', '草原', '夏'],
  autumn: ['赏枫', '红叶', '晒秋', '银杏', '胡杨', '秋', '登高', '满山红'],
  winter: ['冰雪', '冰雕', '雾凇', '温泉', '滑雪', '冬捕', '灯会', '雪', '冬'],
};

/** 计算景点与给定季节的相关度（0~1）。无季节特征给中性偏低分。 */
export function seasonRelevance(name: string, desc = '', tags: string[] = [], season: Season): number {
  const kws = SEASON_KEYWORDS[season] || [];
  if (kws.length === 0) return 0.5;
  const text = `${name} ${desc} ${tags.join(' ')}`.toLowerCase();
  let hits = 0;
  for (const kw of kws) {
    if (text.includes(kw.toLowerCase())) hits++;
  }
  if (hits === 0) return 0.3;
  return Math.min(1, 0.5 + 0.1667 * hits);
}

export interface RankableHotspot {
  name: string;
  desc?: string;
  tags?: string[];
}

export interface RankedHotspot extends RankableHotspot {
  seasonMatch: number;
  weight: number; // 0~1
  score: number;
}

export interface Interactions {
  clicks?: number;
  likes?: number;
}

/** 埋点互动热度权重（0~1，指数饱和），与服务端 InteractionWeight 镜像。 */
export function interactionWeight(clicks: number, likes: number): number {
  const raw = clicks * 2 + likes * 3;
  return 1 - Math.exp(-raw / 10);
}

/** 综合「季节相关度 + 埋点热度权重」本地重排（与服务端 RankHotspots 镜像）。 */
export function rankHotspots(
  hotspots: RankableHotspot[],
  interactions: Record<string, Interactions>,
  season: Season,
): RankedHotspot[] {
  return hotspots
    .map((h) => {
      const it = interactions[h.name] ?? {};
      const sm = seasonRelevance(h.name, h.desc, h.tags ?? [], season);
      const w = interactionWeight(it.clicks ?? 0, it.likes ?? 0);
      return {
        name: h.name,
        desc: h.desc,
        tags: h.tags,
        seasonMatch: round2(sm),
        weight: round2(w),
        score: round2(0.6 * sm + 0.4 * w),
      };
    })
    .sort((a, b) => b.score - a.score || b.weight - a.weight);
}

export interface ServerRank {
  name: string;
  season_match: number;
  weight: number;
  score: number;
  clicks: number;
  likes: number;
}

/** 依据服务端返回的排序结果，对本地静态景点重排并附加分数，热门前置。 */
export function applyServerRanking(hotspots: RankableHotspot[], ranked: ServerRank[]): RankedHotspot[] {
  const byName = new Map(ranked.map((r) => [r.name, r]));
  const order = new Map(ranked.map((r, i) => [r.name, i]));
  return hotspots
    .map((h) => {
      const s = byName.get(h.name);
      return {
        name: h.name,
        desc: h.desc,
        tags: h.tags,
        seasonMatch: s ? round2(s.season_match) : 0,
        weight: s ? round2(s.weight) : 0,
        score: s ? round2(s.score) : 0,
      };
    })
    .sort((a, b) => (order.get(a.name) ?? Number.MAX_SAFE_INTEGER) - (order.get(b.name) ?? Number.MAX_SAFE_INTEGER));
}

function round2(v: number): number {
  return Math.round(v * 100) / 100;
}