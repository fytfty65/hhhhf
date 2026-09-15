// 模块 9 · 语音输入「结构化参数解析」纯函数层（前端）
// 将 Web Speech 识别出的自然语言文本解析为「目的地 / 游玩天数 / 偏好标签」，
// 与 AI 服务的 parse_chinese_days 及城市识别逻辑保持一致口径。

export interface VoiceIntent {
  city: string; // 识别到的目的地（空字符串表示未识别）
  days: number; // 游玩天数（0 表示未识别）
  tags: string[]; // 去重后的偏好标签（按规则顺序）
  normalized: string; // 去除首尾空白的原文
}

const CN_NUM: Record<string, number> = {
  一: 1, 二: 2, 两: 2, 俩: 2, 三: 3, 四: 4, 五: 5,
  六: 6, 七: 7, 八: 8, 九: 9, 十: 10,
};

function clampDays(n: number): number {
  return Math.max(1, Math.min(15, Math.floor(n)));
}

/** 解析游玩天数，支持 "3天" / "五日游" / "两天一夜" / "7天6晚"；未识别返回 0。 */
export function parseDays(text: string): number {
  if (!text) return 0;
  // 阿拉伯数字："3天"、"5日"、"7天6晚"
  const digit = text.match(/(\d+)\s*[天日]/);
  if (digit) return clampDays(Number(digit[1]));
  // 中文复合："两天一夜" -> 2 天
  const compound = text.match(/([一二两俩三四五六七八九十\d]+)\s*天\s*[一二两俩三四五六七八九十\d]+\s*[晚夜]/);
  if (compound) {
    const n = CN_NUM[compound[1]];
    if (n) return clampDays(n);
  }
  // 中文单数字："三天"、"两日游"
  const cn = text.match(/([一二两俩三四五六七八九十]+)\s*[天日]/);
  if (cn) {
    const n = CN_NUM[cn[1]];
    if (n) return clampDays(n);
  }
  return 0;
}

/** 去除目的地候选串尾部的行政区划冗余词（市/省/地区/州…），多次剥离直至稳定。 */
function cleanCity(raw: string): string {
  let s = raw.trim();
  const suffix = /(市|省|地区|州)$/;
  let prev = '';
  while (s !== prev) {
    prev = s;
    s = s.replace(suffix, '');
  }
  return s.trim();
}

/** 从自然语言中提取目的地城市（2~6 个汉字），未识别返回空字符串。 */
export function extractCity(text: string): string {
  if (!text) return '';
  const patterns: RegExp[] = [
    // 动词引导：城市名后须紧跟「玩/天/吃…」等边界词或标点/结尾，避免把后续诉求误并入城市名
    /(?:去|到|前往|抵达|在|目的地是?|想[去在到]|帮我规划|帮我们规划|规划|安排)([\u4e00-\u9fa5]{2,6}?)(?=(?:旅游|旅行|游玩|自由行|深度游|攻略|路书|行程|周边|不变|转转|[玩逛耍待呆住宿吃喝看拍买想天日夜晚的，,。、\d\s]|$))/,
    // 句首城市后紧跟天数："上海5天行程"
    /^([\u4e00-\u9fa5]{2,6})(?=\d+\s*[天日])/,
    // 句首城市紧接行程关键词："上海旅游/成都攻略"
    /^([\u4e00-\u9fa5]{2,6}?)(?:旅游|攻略|路书|行程|游玩|自由行)/,
  ];
  for (const re of patterns) {
    const m = text.match(re);
    if (m?.[1]) return cleanCity(m[1]);
  }
  return '';
}

/** 偏好标签词典：命中即归类，标签按规则定义顺序返回（去重）。 */
const TAG_RULES: Array<{ tag: string; words: string[] }> = [
  { tag: '美食', words: ['吃', '美食', '小吃', '餐厅', '火锅', '烧烤', '手抓', '抓饭', '地道', '老字号', '夜市', '奶茶', '咖啡'] },
  { tag: '摄影', words: ['拍照', '出片', '摄影', '打卡', '机位', '写真', '风景', '日出', '日落'] },
  { tag: '购物', words: ['购物', '逛街', '商场', '免税', '买点', '伴手礼'] },
  { tag: '亲子', words: ['亲子', '孩子', '小朋友', '乐园', '动物园', '带娃', '儿童'] },
  { tag: '自然', words: ['海边', '滨海', '海岛', '看海', '湖泊', '草原', '森林', '爬山', '徒步', '登山', '骑行', '温泉', '公园'] },
  { tag: '历史人文', words: ['历史', '博物馆', '古迹', '文化', '寺庙', '古镇', '遗址', '展览', '非遗', '红色'] },
  { tag: '休闲慢游', words: ['慢慢', '悠闲', '慢游', '不赶', '轻松', '度假', '下午茶', '放空'] },
];

export function extractTags(text: string): string[] {
  if (!text) return [];
  const tags: string[] = [];
  for (const rule of TAG_RULES) {
    if (rule.words.some((w) => text.includes(w))) tags.push(rule.tag);
  }
  return tags;
}

/** 一次完成目的地 / 天数 / 标签的结构化解析。 */
export function parseVoiceIntent(text: string): VoiceIntent {
  const normalized = (text || '').trim();
  return {
    city: extractCity(normalized),
    days: parseDays(normalized),
    tags: extractTags(normalized),
    normalized,
  };
}
