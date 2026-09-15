// 模块 8 · 协作增强（光标同步 + 头像悬浮）纯函数层（前端）
// 与服务端 gateway/internal/service/presence.go 逻辑镜像，用于本地即时合并/过期清理。

export interface NodePresence {
  userId: string;
  name: string;
  role: string;
  avatarUrl?: string;
  avatarSeed?: string;
  nodeKey: string;
  updatedAt: number;
}

/** 协作光标在场态的默认存活时长（与服务端 PresenceTTL 对齐）。 */
export const PRESENCE_TTL_MS = 30_000;

/** 将服务端单条在场记录规范化为前端 NodePresence，非法数据返回 null。 */
export function normalizePresence(raw: any, now: number = Date.now()): NodePresence | null {
  if (!raw || typeof raw !== 'object') return null;
  const userId = String(raw.user_id ?? raw.userId ?? '');
  const nodeKey = String(raw.node_key ?? raw.nodeKey ?? '');
  if (!userId || !nodeKey) return null;
  return {
    userId,
    name: String(raw.name ?? raw.userId ?? ''),
    role: String(raw.role ?? ''),
    avatarUrl: raw.avatar_url ?? raw.avatarUrl ?? '',
    avatarSeed: raw.avatar_seed ?? raw.avatarSeed ?? '',
    nodeKey,
    updatedAt: now,
  };
}

/** 将服务端在场快照规范化为前端列表（过滤非法项）。 */
export function normalizePresenceList(raw: any, now: number = Date.now()): NodePresence[] {
  if (!Array.isArray(raw)) return [];
  const out: NodePresence[] = [];
  for (const item of raw) {
    const p = normalizePresence(item, now);
    if (p) out.push(p);
  }
  return out;
}

/** 合并在场列表：按 userId 去重，后到（incoming）覆盖先存在的记录。 */
export function mergePresence(prev: NodePresence[], incoming: NodePresence[]): NodePresence[] {
  const byUser = new Map<string, NodePresence>();
  for (const p of prev) byUser.set(p.userId, p);
  for (const p of incoming) byUser.set(p.userId, p);
  return Array.from(byUser.values());
}

/** 清理超过 TTL 的过期在场记录（头像悬浮会随超时消失）。 */
export function pruneExpiredPresence(
  list: NodePresence[],
  now: number = Date.now(),
  ttl: number = PRESENCE_TTL_MS,
): NodePresence[] {
  return list.filter((p) => now - p.updatedAt < ttl);
}

/** 取聚焦于指定节点的所有在场成员。 */
export function presenceForNode(list: NodePresence[], nodeKey: string): NodePresence[] {
  return list.filter((p) => p.nodeKey === nodeKey);
}

/** 生成成员头像 URL：优先 avatarUrl（相对路径补 base），缺失回退 DiceBear 种子头像。 */
export function presenceAvatarUrl(p: NodePresence, base = ''): string {
  const url = String(p.avatarUrl || '').trim();
  if (url.startsWith('http') || url.startsWith('data:')) return url;
  if (url) return base + url;
  const seed = encodeURIComponent(p.avatarSeed || p.name || p.userId);
  return `https://api.dicebear.com/9.x/avataaars/svg?seed=${seed}&backgroundColor=fdeed8`;
}