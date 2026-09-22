import type { NormalizedRiskNotification } from './riskNotifications';

export type CachedRiskInboxItem = NormalizedRiskNotification & { ts: number };
export type PendingRiskNotificationAction = { id: string; status: 'read' | 'acknowledged'; queuedAt: number };

interface CachedRiskInbox {
  version: 1;
  savedAt: number;
  serverUnread: number;
  items: CachedRiskInboxItem[];
  pending: PendingRiskNotificationAction[];
}

const CACHE_PREFIX = 'omni_risk_inbox_v1:';

function storageKey(userId: string) {
  return `${CACHE_PREFIX}${encodeURIComponent(userId.trim())}`;
}

function storageAvailable() {
  return typeof window !== 'undefined' && !!window.localStorage;
}

export function loadRiskInboxCache(userId: string): CachedRiskInbox | null {
  if (!storageAvailable() || !userId.trim()) return null;
  try {
    const raw = window.localStorage.getItem(storageKey(userId));
    if (!raw) return null;
    const parsed = JSON.parse(raw) as Partial<CachedRiskInbox>;
    if (parsed.version !== 1 || !Array.isArray(parsed.items)) return null;
    return {
      version: 1,
      savedAt: typeof parsed.savedAt === 'number' ? parsed.savedAt : Date.now(),
      serverUnread: typeof parsed.serverUnread === 'number' ? parsed.serverUnread : 0,
      items: parsed.items.slice(0, 100) as CachedRiskInboxItem[],
      pending: Array.isArray(parsed.pending) ? parsed.pending.slice(0, 100) as PendingRiskNotificationAction[] : [],
    };
  } catch {
    return null;
  }
}

export function saveRiskInboxCache(userId: string, value: Omit<CachedRiskInbox, 'version' | 'savedAt'>) {
  if (!storageAvailable() || !userId.trim()) return;
  try {
    const payload: CachedRiskInbox = { version: 1, savedAt: Date.now(), ...value, items: value.items.slice(0, 100), pending: value.pending.slice(0, 100) };
    window.localStorage.setItem(storageKey(userId), JSON.stringify(payload));
  } catch {
    // Quota/private-mode failures must never break risk polling.
  }
}

export function enqueueRiskNotificationAction(userId: string, action: PendingRiskNotificationAction) {
  const cached = loadRiskInboxCache(userId);
  saveRiskInboxCache(userId, {
    serverUnread: cached?.serverUnread || 0,
    items: cached?.items || [],
    pending: [...(cached?.pending || []).filter((entry) => entry.id !== action.id), action],
  });
}

export function removeRiskNotificationActions(userId: string, ids: string[]) {
  const cached = loadRiskInboxCache(userId);
  if (!cached) return;
  const remove = new Set(ids);
  saveRiskInboxCache(userId, { serverUnread: cached.serverUnread, items: cached.items, pending: cached.pending.filter((entry) => !remove.has(entry.id)) });
}
