// 房间内节点投票的纯函数层。
//
// 服务端只广播「谁对哪个节点投了什么票」这一条事实（node_vote），不保存历史，
// 因此新加入的成员看不到入房前的投票。这里不伪造完整历史，只负责把收到的
// 事实合并成可展示的实时聚合结果。

export type VoteValue = 'up' | 'down';

/** 节点名 → 用户 ID → 票值。 */
export type RoomVoteState = Record<string, Record<string, VoteValue>>;

export const MAX_NODE_NAME_LENGTH = 200;

/** 规范化票值：只接受 up / down，其余（含撤销）一律视为无效。 */
export function normalizeVoteValue(raw: unknown): VoteValue | null {
  const value = String(raw ?? '').trim().toLowerCase();
  if (value === 'up' || value === 'down') return value;
  return null;
}

/** 规范化节点名：去空白并按码点截断，与网关侧的截断规则保持一致。 */
export function normalizeNodeName(raw: unknown): string {
  const name = String(raw ?? '').trim();
  if (!name) return '';
  return Array.from(name).slice(0, MAX_NODE_NAME_LENGTH).join('');
}

/** 判断一条票值是否明确表示「撤销」：空串 / null / "null"。 */
function isExplicitWithdrawal(raw: unknown): boolean {
  if (raw === null) return true;
  const value = String(raw).trim().toLowerCase();
  return value === '' || value === 'null';
}

/**
 * 应用一条投票事实。
 * 明确的空值（''、null、"null"）表示该用户撤销投票；无法识别的票值
 * （如 'maybe'、缺字段）直接忽略——不能把脏数据当成撤销，否则会误删票。
 * 非法节点名 / 用户 ID 同样返回原状态。
 */
export function applyRoomVote(
  state: RoomVoteState,
  nodeName: unknown,
  userId: unknown,
  vote: unknown,
): RoomVoteState {
  const name = normalizeNodeName(nodeName);
  const uid = String(userId ?? '').trim();
  if (!name || !uid) return state;

  const nextValue = normalizeVoteValue(vote);
  if (!nextValue && !isExplicitWithdrawal(vote)) return state;

  const byUser: Record<string, VoteValue> = { ...(state[name] || {}) };
  if (nextValue) byUser[uid] = nextValue;
  else delete byUser[uid];

  const next: RoomVoteState = { ...state };
  if (Object.keys(byUser).length > 0) next[name] = byUser;
  else delete next[name];
  return next;
}

/** 统计单个节点的票数；可排除自己，避免与本地已展示的自己的票重复。 */
export function voteCountsFor(
  state: RoomVoteState,
  nodeName: unknown,
  excludeUserId = '',
): { up: number; down: number } {
  const name = normalizeNodeName(nodeName);
  const counts = { up: 0, down: 0 };
  if (!name) return counts;

  const byUser = state[name];
  if (!byUser) return counts;
  for (const [userId, vote] of Object.entries(byUser)) {
    if (excludeUserId && userId === excludeUserId) continue;
    if (vote === 'up') counts.up += 1;
    else if (vote === 'down') counts.down += 1;
  }
  return counts;
}

/** 批量统计所有节点票数，供行程列表一次性渲染。 */
export function voteCountsByNode(
  state: RoomVoteState,
  excludeUserId = '',
): Record<string, { up: number; down: number }> {
  const out: Record<string, { up: number; down: number }> = {};
  for (const name of Object.keys(state)) {
    const counts = voteCountsFor(state, name, excludeUserId);
    if (counts.up > 0 || counts.down > 0) out[name] = counts;
  }
  return out;
}

/**
 * 成员离开后清理其投票，避免界面保留「幽灵票」。
 * 传空成员列表时不做清理：那通常意味着成员同步尚未到达，而不是房间真的没人。
 */
export function pruneRoomVotes(
  state: RoomVoteState,
  activeUserIds: readonly string[],
): RoomVoteState {
  if (activeUserIds.length === 0) return state;

  const active = new Set(activeUserIds.map((id) => String(id ?? '').trim()).filter(Boolean));
  const next: RoomVoteState = {};
  for (const [nodeName, byUser] of Object.entries(state)) {
    const kept: Record<string, VoteValue> = {};
    for (const [userId, vote] of Object.entries(byUser)) {
      if (active.has(userId)) kept[userId] = vote;
    }
    if (Object.keys(kept).length > 0) next[nodeName] = kept;
  }
  return next;
}
