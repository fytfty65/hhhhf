/**
 * 大厅通用纯函数工具（自 ContextualLobby.tsx 拆分出来的叶子逻辑）。
 *
 * 仅搬运，未做任何行为改动；后续批次可继续把同类 helper 搬进来。
 */

// 把协议相对 `//` 或 http 图片统一升级为 https，并对高德图片 CDN 显式禁用 Referer，
// 绕过 store.is.autonavi.com 的防盗链/Referer 校验 —— 这是浏览器端图片“完全不可见”的根因。
export function getCleanPhotoUrl(photoUrl?: string, poiName: string = '', photoIndex: number = 0) {
  if (!photoUrl || typeof photoUrl !== 'string') return '';
  let u = photoUrl.trim();
  if (u.startsWith('//')) u = 'https:' + u;
  else if (u.startsWith('http://')) u = 'https://' + u.slice('http://'.length);
  return u.startsWith('https://') ? u : '';
}

// ---- 文本与行政区工具 ----

// 精简行政区名称（如 "西藏自治区" -> "西藏"）
export function shortenRegionName(full: string) {
  return full.replace(/(壮族自治区|回族自治区|维吾尔自治区|特别行政区|自治区|省|市)$/, '');
}

// ---- 流式/JSON 解析工具 ----

// ================= JSON 与 数据提解助手 =================
export function tryExtractJson(text: string) {
  if (!text) return null;
  let jsonCandidate = "";

  if (text.includes("[FINAL_JSON]")) {
    jsonCandidate = text.split("[FINAL_JSON]")[1];
  } else if (text.includes("```json")) {
    jsonCandidate = text.split("```json")[1];
  } else {
    const firstBrace = text.search(/\{\s*"status"|\{\s*"route"/);
    if (firstBrace !== -1) {
      jsonCandidate = text.substring(firstBrace);
    }
  }

  if (!jsonCandidate) return null;
  jsonCandidate = jsonCandidate.replace(/```json/g, "").replace(/```/g, "").trim();

  const lastBraceIdx = jsonCandidate.lastIndexOf("}");
  if (lastBraceIdx === -1) return null;

  const validSubstring = jsonCandidate.substring(0, lastBraceIdx + 1);
  try {
    return JSON.parse(validSubstring);
  } catch (e) {
    return null;
  }
}

// 👑 坐标归一化：兼容 [lng,lat]、"lng,lat"、{lng,lat}/{lon,lat} 等多种后端/LLM 输出，杜绝 mapbox LngLatLike 崩溃
export function normalizeLnglat(v: any): [number, number] | null {
  if (Array.isArray(v) && v.length >= 2) {
    const lng = Number(v[0]);
    const lat = Number(v[1]);
    if (Number.isFinite(lng) && Number.isFinite(lat)) return [lng, lat];
    return null;
  }
  if (typeof v === 'string') {
    const parts = v.split(',');
    if (parts.length >= 2) {
      const lng = Number(parts[0]);
      const lat = Number(parts[1]);
      if (Number.isFinite(lng) && Number.isFinite(lat)) return [lng, lat];
    }
    return null;
  }
  if (v && typeof v === 'object') {
    const lng = Number((v as any).lng ?? (v as any).lon ?? (v as any).longitude);
    const lat = Number((v as any).lat ?? (v as any).latitude);
    if (Number.isFinite(lng) && Number.isFinite(lat)) return [lng, lat];
  }
  return null;
}

export function extractStreamingRoutes(text: string): any[] {
  if (!text) return [];
  let jsonPart = text;
  if (text.includes("[FINAL_JSON]")) {
    jsonPart = text.split("[FINAL_JSON]")[1];
  } else if (text.includes("```json")) {
    jsonPart = text.split("```json")[1];
  }

  const objectMatches = jsonPart.match(/\{\s*"day"\s*:\s*\d+[\s\S]*?\}/g);
  if (!objectMatches) return [];

  const parsedItems: any[] = [];
  for (const matchStr of objectMatches) {
    try {
      const item = JSON.parse(matchStr);
      if (item && (item.location || item.name)) {
        parsedItems.push({
          day: item.day || 1,
          name: item.location || item.name,
          lnglat: normalizeLnglat(item.lnglat),
          coordinate_status: normalizeLnglat(item.lnglat) ? 'verified' : 'missing',
          color: item.tags?.includes("寻味") || item.type === "food" ? "#f97316" : "#3b82f6",
          desc: item.desc || item.action || "",
          time: item.time || "",
          time_reason: item.time_reason || "",
          transport: item.transport || "",
          tags: item.tags || [],
          cost: item.cost_estimate || item.cost || "暂无供应商数据",
          photos: item.photos || [],
          trust_reason: item.trust_reason || "核心地标推荐",
          amap_url: item.amap_url || "",
          hotel_candidates: item.hotel_candidates || [],
          split_info: item.split_info || "",
          merge_point: Boolean(item.merge_point),
          is_hotel: Boolean(item.is_hotel || item.tags?.includes("住宿"))
        });
      }
    } catch (e) {
      // 容错流式单项解析
    }
  }
  return parsedItems;
}
