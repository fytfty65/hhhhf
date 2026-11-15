'use client';

// 视图快照：刷新页面后回到原来的位置，而不是被弹回大厅。
// 只用 sessionStorage —— 同一个标签页刷新能恢复，关掉标签页后重新开始，
// 避免把几天前的旧方案当成"当前行程"继续执行。

const LOBBY_PREFIX = 'omni_lobby_';
const WORKSPACE_PREFIX = 'omni_workspace_';
const SNAPSHOT_VERSION = 1;

export type LobbySnapshot = {
  version: number;
  destination: string;
  days: number;
  travelers: number;
  planDestination: string | null;
  showWorkspace: boolean;
  savedAt: number;
};

export type WorkspaceLayerSnapshot = 'plan' | 'evidence' | 'execution' | 'professional';

export type WorkspaceSnapshot = {
  version: number;
  destination: string;
  dynamicRoutes: any[];
  planVariants: any[];
  activePlanId: string;
  planLedgers: any;
  isFallbackRoute: boolean;
  targetCityInfo: any | null;
  budgetData: any | null;
  planGovernance: any | null;
  workspaceLayer: WorkspaceLayerSnapshot;
  activeDayIndex: number;
  savedAt: number;
};

const storage = () => {
  try {
    return window.sessionStorage;
  } catch {
    return null;
  }
};

const readJson = <T,>(key: string): T | null => {
  if (!key) return null;
  const store = storage();
  if (!store) return null;
  try {
    const raw = store.getItem(key);
    if (!raw) return null;
    const parsed = JSON.parse(raw);
    if (!parsed || parsed.version !== SNAPSHOT_VERSION) return null;
    return parsed as T;
  } catch {
    return null;
  }
};

const writeJson = (key: string, value: any) => {
  if (!key) return false;
  const store = storage();
  if (!store) return false;
  try {
    store.setItem(key, JSON.stringify(value));
    return true;
  } catch {
    return false;
  }
};

export const lobbySnapshotKey = (userId?: string | null) =>
  userId ? `${LOBBY_PREFIX}${userId}` : '';

export const workspaceSnapshotKey = (userId?: string | null, roomCode?: string | null) =>
  userId ? `${WORKSPACE_PREFIX}${userId}_${roomCode || 'local'}` : '';

// 目的地口径可能不完全一致（"杭州" vs "杭州市"），宽松比较，避免把有效快照误判成过期。
export const sameDestination = (a?: string | null, b?: string | null) => {
  const normalize = (value?: string | null) =>
    String(value ?? '').trim().replace(/(市|省|自治区|特别行政区)$/, '');
  const left = normalize(a);
  const right = normalize(b);
  if (!left || !right) return true;
  return left === right || left.includes(right) || right.includes(left);
};

export const readLobbySnapshot = (userId?: string | null) =>
  readJson<LobbySnapshot>(lobbySnapshotKey(userId));

export const writeLobbySnapshot = (userId: string | null | undefined, snapshot: Omit<LobbySnapshot, 'version' | 'savedAt'>) => {
  writeJson(lobbySnapshotKey(userId), { ...snapshot, version: SNAPSHOT_VERSION, savedAt: Date.now() });
};

// 配额兜底：节点里的图片/候选清单最占空间，写不下时去掉它们再试一次。
const stripHeavyFields = (node: any) => {
  if (!node || typeof node !== 'object') return node;
  const { photos, map_image, hotel_candidates, ...rest } = node;
  void photos;
  void map_image;
  void hotel_candidates;
  return rest;
};

const compactWorkspaceSnapshot = (snapshot: WorkspaceSnapshot): WorkspaceSnapshot => ({
  ...snapshot,
  dynamicRoutes: snapshot.dynamicRoutes.map(stripHeavyFields),
  planVariants: snapshot.planVariants.map((variant: any) =>
    variant && Array.isArray(variant.nodes)
      ? { ...variant, nodes: variant.nodes.map(stripHeavyFields) }
      : variant,
  ),
});

export const writeWorkspaceSnapshot = (
  userId: string | null | undefined,
  roomCode: string | null | undefined,
  snapshot: Omit<WorkspaceSnapshot, 'version' | 'savedAt'>,
) => {
  const key = workspaceSnapshotKey(userId, roomCode);
  if (!key) return;
  const payload: WorkspaceSnapshot = { ...snapshot, version: SNAPSHOT_VERSION, savedAt: Date.now() };
  if (writeJson(key, payload)) return;
  writeJson(key, compactWorkspaceSnapshot(payload));
};

export const removeWorkspaceSnapshot = (userId?: string | null, roomCode?: string | null) => {
  const store = storage();
  if (!store) return;
  try {
    store.removeItem(workspaceSnapshotKey(userId, roomCode));
  } catch {}
};

/**
 * 读取当前房间的方案快照。
 * 目的地已经在大厅被改掉时，旧方案不再属于这次行程，直接丢弃。
 */
export const readWorkspaceSnapshot = (
  userId?: string | null,
  roomCode?: string | null,
  expectedDestination?: string | null,
) => {
  const snapshot = readJson<WorkspaceSnapshot>(workspaceSnapshotKey(userId, roomCode));
  if (!snapshot || !Array.isArray(snapshot.dynamicRoutes) || snapshot.dynamicRoutes.length === 0) {
    return null;
  }
  if (!sameDestination(snapshot.destination, expectedDestination)) {
    removeWorkspaceSnapshot(userId, roomCode);
    return null;
  }
  return snapshot;
};

// 退出登录时清掉所有快照：换个账号登录不该自动跳进上一个人的工作台。
export const clearWorkspaceSnapshots = () => {
  const store = storage();
  if (!store) return;
  try {
    const keys: string[] = [];
    for (let i = 0; i < store.length; i += 1) {
      const key = store.key(i);
      if (key && (key.startsWith(LOBBY_PREFIX) || key.startsWith(WORKSPACE_PREFIX))) keys.push(key);
    }
    keys.forEach((key) => store.removeItem(key));
  } catch {}
};
