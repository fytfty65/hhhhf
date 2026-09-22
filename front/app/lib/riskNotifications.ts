export interface NormalizedRiskNotification {
  id: string;
  city?: string;
  type: string;
  severity: 'LOW' | 'MEDIUM' | 'HIGH';
  title: string;
  detail: string;
  source?: string;
  estimated: boolean;
  status: 'unread' | 'read' | 'acknowledged';
  createdAt: number;
  observedAt: number;
  signalSources?: Record<string, { provider?: string; estimated?: boolean; retrieved_at?: string | number | null }>;
  replan?: { proposal_id?: string; status?: string; trip_id?: string; reason?: string; changed_signals?: unknown[]; affected_nodes?: string[] };
}

export function parseRiskTimestamp(value: unknown, fallback = Date.now()): number {
  if (typeof value === 'number' && Number.isFinite(value) && value > 0) return value < 10_000_000_000 ? value * 1000 : value;
  if (typeof value === 'string') {
    const parsed = Date.parse(value);
    if (Number.isFinite(parsed) && parsed > 0) return parsed;
  }
  return fallback;
}

export function normalizeRiskNotification(raw: Record<string, unknown>): NormalizedRiskNotification | null {
  const id = String(raw.id || '').trim();
  const title = String(raw.title || '').trim();
  if (!id || !title) return null;
  let signalSources: NormalizedRiskNotification['signalSources'];
  if (typeof raw.signal_sources === 'string') {
    try { signalSources = JSON.parse(raw.signal_sources) as NormalizedRiskNotification['signalSources']; } catch { signalSources = undefined; }
  } else if (raw.signal_sources && typeof raw.signal_sources === 'object') {
    signalSources = raw.signal_sources as NormalizedRiskNotification['signalSources'];
  }
  let replan: NormalizedRiskNotification['replan'];
  if (typeof raw.replan_proposal === 'string') {
    try { replan = JSON.parse(raw.replan_proposal) as NormalizedRiskNotification['replan']; } catch { replan = undefined; }
  } else if (raw.replan_proposal && typeof raw.replan_proposal === 'object') {
    replan = raw.replan_proposal as NormalizedRiskNotification['replan'];
  }
  const severity = String(raw.severity || 'MEDIUM').toUpperCase();
  const createdAt = parseRiskTimestamp(raw.created_at);
  return {
    id,
    city: typeof raw.city === 'string' ? raw.city : undefined,
    type: String(raw.type || 'NEW_ALERT'),
    severity: severity === 'HIGH' || severity === 'LOW' ? severity : 'MEDIUM',
    title,
    detail: String(raw.detail || '请查看风险详情并确认是否需要调整剩余行程。'),
    source: typeof raw.source === 'string' ? raw.source : undefined,
    estimated: raw.estimated === true,
    status: raw.status === 'read' || raw.status === 'acknowledged' ? raw.status : 'unread',
    createdAt,
    observedAt: parseRiskTimestamp(raw.observed_at, createdAt),
    signalSources,
    replan,
  };
}
