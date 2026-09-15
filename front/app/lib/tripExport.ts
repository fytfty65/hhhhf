// 模块 10 · AI 日记 + 日历 .ics 导出（前端纯函数层）
// 从最终行程（RouteNode[]）确定性生成「Markdown 旅行日记」与「iCalendar(.ics) 日历」，
// 全部为无副作用纯函数，便于单元测试与复用（与服务端模板化简报风格保持一致）。

import type { RouteNode } from './routeEditor';

/** 转义 iCalendar 文本中的特殊字符（反斜杠 / 分号 / 逗号 / 换行）。 */
export function escapeIcs(value: string): string {
  return String(value ?? '')
    .replace(/\\/g, '\\\\')
    .replace(/;/g, '\\;')
    .replace(/,/g, '\\,')
    .replace(/\r?\n/g, '\\n');
}

function pad(n: number): string {
  return String(n).padStart(2, '0');
}

/** 将 Date 转为 iCalendar 的 DATE 值（YYYYMMDD）。 */
export function icsDateKey(d: Date): string {
  return `${d.getFullYear()}${pad(d.getMonth() + 1)}${pad(d.getDate())}`;
}

function addDays(d: Date, n: number): Date {
  return new Date(d.getFullYear(), d.getMonth(), d.getDate() + n);
}

/** 解析起始日期（YYYY-MM-DD），非法或缺失时回退到今天。 */
export function parseStartDate(startDate?: string): Date {
  if (startDate) {
    const m = /^(\d{4})-(\d{1,2})-(\d{1,2})$/.exec(startDate);
    if (m) {
      const d = new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3]));
      if (!Number.isNaN(d.getTime())) return d;
    }
  }
  const now = new Date();
  return new Date(now.getFullYear(), now.getMonth(), now.getDate());
}

/** 按 day 分组行程节点，保持节点原始顺序，返回 day → 节点列表。 */
export function groupByDay(route: RouteNode[]): Map<number, RouteNode[]> {
  const map = new Map<number, RouteNode[]>();
  for (const r of route) {
    const day = Number(r.day) || 1;
    if (!map.has(day)) map.set(day, []);
    map.get(day)!.push(r);
  }
  return map;
}

/** 将行程节点序列化为 iCalendar(.ics) 文本：每天每个节点一个全天的 VEVENT。 */
export function buildIcsCalendar(
  route: RouteNode[],
  options: { city?: string; startDate?: string } = {},
): string {
  const city = options.city || 'OmniRoute 行程';
  const start = parseStartDate(options.startDate);
  const groups = groupByDay(route);
  const days = Array.from(groups.keys()).sort((a, b) => a - b);

  const lines: string[] = [
    'BEGIN:VCALENDAR',
    'VERSION:2.0',
    'PRODID:-//OmniRoute//TripCalendar//ZH-CN',
    'CALSCALE:GREGORIAN',
    `X-WR-CALNAME:${escapeIcs(city)}`,
  ];

  for (const day of days) {
    const date = addDays(start, day - 1);
    const dateKey = icsDateKey(date);
    const nodes = groups.get(day) || [];
    nodes.forEach((n, ni) => {
      const name = n.name || '行程节点';
      const uid = `omniroute-${dateKey}-${day}-${ni}`;
      lines.push('BEGIN:VEVENT');
      lines.push(`UID:${uid}`);
      lines.push(`DTSTART;VALUE=DATE:${dateKey}`);
      lines.push(`SUMMARY:${escapeIcs(name)}`);
      if (n.address) lines.push(`LOCATION:${escapeIcs(String(n.address))}`);
      const descParts = [
        n.time ? `时间：${n.time}` : undefined,
        n.cost ? `费用：${n.cost}` : undefined,
        n.time_reason ? `推荐理由：${n.time_reason}` : undefined,
        n.desc ? String(n.desc) : undefined,
      ].filter(Boolean);
      if (descParts.length > 0) lines.push(`DESCRIPTION:${escapeIcs(descParts.join(' | '))}`);
      lines.push('END:VEVENT');
    });
  }

  lines.push('END:VCALENDAR');
  return lines.join('\r\n');
}

/** 将行程节点序列化为 Markdown 旅行日记（按天分节，逐节点叙述）。 */
export function buildDiaryMarkdown(
  route: RouteNode[],
  options: { city?: string } = {},
): string {
  const city = options.city || '';
  const title = city ? `${city} 旅行日记` : '旅行日记';
  const groups = groupByDay(route);
  const days = Array.from(groups.keys()).sort((a, b) => a - b);

  const lines: string[] = [`# ${title}`, ''];
  if (days.length === 0) {
    lines.push('（暂无行程节点）');
    return lines.join('\n');
  }

  for (const day of days) {
    lines.push(`## Day ${day}`);
    for (const n of groups.get(day) || []) {
      const time = n.time ? `**${n.time}** ` : '';
      const name = n.name || '行程节点';
      const desc = n.desc ? `：${n.desc}` : '';
      const cost = n.cost ? `（${n.cost}）` : '';
      lines.push(`- ${time}· ${name}${desc}${cost}`);
    }
    lines.push('');
  }

  return lines.join('\n').trimEnd() + '\n';
}