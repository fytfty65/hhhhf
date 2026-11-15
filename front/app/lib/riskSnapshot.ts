'use client';

// Shared, deduplicated reader for the realtime risk snapshot.
//
// Two independent components polled this endpoint on their own 30s timers —
// WorldSafetyGlobe (while the 3D radar is open) and RiskPushCenter — so with the
// radar open the same city was fetched twice per interval. Worse, the backend
// handler for this endpoint costs ~10s of upstream work and had no caching, so
// the duplication doubled real provider traffic.
//
// This module keeps one in-flight promise per city+coordinate, and reuses a
// recent result for a short window. Callers that genuinely need a fresh read
// (a manual refresh, a tab switch) pass { force: true }.
//
// It intentionally depends on the framework-free lib/apiClient so it can be unit
// tested with `node --experimental-strip-types`, which does not transform .tsx modules.

import { ApiRequestError, apiRequest } from './apiClient.ts';

export interface RiskSnapshot {
  [key: string]: unknown;
}

interface CacheEntry {
  data: RiskSnapshot | null;
  at: number;
  promise: Promise<RiskSnapshot | null> | null;
}

// Slightly shorter than the 30s poll interval so a normal tick still refreshes,
// while two components polling within the same tick share one request.
const FRESH_MS = 25_000;

const cache = new Map<string, CacheEntry>();

function keyFor(city: string, coordinate?: [number, number] | null): string {
  const coord = Array.isArray(coordinate) && coordinate.length >= 2 ? `${coordinate[0]},${coordinate[1]}` : '';
  return `${city}::${coord}`;
}

export interface FetchRiskSnapshotOptions {
  coordinate?: [number, number] | null;
  baseline?: unknown;
  force?: boolean;
}

export interface GlobalRiskCityRequest {
  city: string;
  coordinate?: [number, number] | null;
  baseline?: unknown;
}

export interface GlobalRiskBatchResult {
  city: string;
  snapshot: RiskSnapshot | null;
  changes: unknown[];
  available: boolean;
  error?: string;
}

export interface GlobalRiskBatchResponse {
  results: GlobalRiskBatchResult[];
  errors: Array<{ code?: string; message?: string; city?: string }>;
  limits?: { max_cities?: number; concurrency?: number };
}

export interface GlobalRiskSummary {
  cii: string;
  riskLevel: string;
  source: string;
  availableSignals: number;
  totalSignals: number;
  estimated: boolean;
  freshness: string;
  signals: GlobalRiskSignalSummary[];
}

export interface GlobalRiskSignalSummary {
  key: string;
  label: string;
  provider: string;
  available: boolean;
  estimated: boolean;
  freshness: string;
}

/** Convert a provider snapshot into display-safe evidence without inventing missing values. */
export function summarizeGlobalRiskSnapshot(snapshot: RiskSnapshot | null, now = Date.now()): GlobalRiskSummary {
  const sourceMap = snapshot?.signal_sources && typeof snapshot.signal_sources === 'object'
    ? snapshot.signal_sources as Record<string, { provider?: unknown; available?: unknown; estimated?: unknown }>
    : {};
  const signals = Object.values(sourceMap);
  const safetyProvider = sourceMap.safety?.provider;
  const source = typeof snapshot?.source === 'string' && snapshot.source.trim()
    ? snapshot.source.trim()
    : typeof safetyProvider === 'string' && safetyProvider.trim() ? safetyProvider.trim() : '来源未提供';
  const ciiNumber = Number(snapshot?.cii_score);
  const timestampNumber = typeof snapshot?.risk_ts === 'string'
    ? Date.parse(snapshot.risk_ts)
    : Number(snapshot?.risk_ts);
  const ageSeconds = Number.isFinite(timestampNumber) && timestampNumber > 0
    ? Math.max(0, Math.round((now - timestampNumber) / 1000))
    : null;
  const signalLabels: Record<string, string> = { safety: '安全', weather: '天气', traffic: '路况' };
  const signalsSummary = Object.entries(signalLabels).map(([key, label]) => {
    const signal = sourceMap[key];
    const retrieved = signal && typeof signal === 'object' ? (signal as { retrieved_at?: unknown }).retrieved_at : undefined;
    const signalTimestamp = typeof retrieved === 'string' ? Date.parse(retrieved) : Number(retrieved);
    const fallbackTimestamp = Number(snapshot?.risk_ts);
    const timestamp = Number.isFinite(signalTimestamp) && signalTimestamp > 0 ? signalTimestamp : fallbackTimestamp;
    const ageSeconds = Number.isFinite(timestamp) && timestamp > 0 ? Math.max(0, Math.round((now - timestamp) / 1000)) : null;
    return {
      key,
      label,
      provider: typeof signal?.provider === 'string' && signal.provider.trim() ? signal.provider.trim() : '来源未提供',
      available: signal?.available === true,
      estimated: signal?.estimated === true,
      freshness: ageSeconds === null ? '无时间戳' : ageSeconds < 60 ? `${ageSeconds} 秒前` : `${Math.round(ageSeconds / 60)} 分钟前`,
    };
  });
  return {
    cii: Number.isFinite(ciiNumber) ? ciiNumber.toFixed(1) : 'N/A',
    riskLevel: typeof snapshot?.risk_level === 'string' && snapshot.risk_level ? snapshot.risk_level : '未评估',
    source,
    availableSignals: signals.filter((signal) => signal?.available === true).length,
    totalSignals: signals.length,
    estimated: snapshot?.is_estimated === true || signals.some((signal) => signal?.estimated === true),
    freshness: ageSeconds === null ? '无时间戳' : ageSeconds < 60 ? `${ageSeconds} 秒前` : `${Math.round(ageSeconds / 60)} 分钟前`,
    signals: signalsSummary,
  };
}

/**
 * Resolve the latest risk snapshot for a city.
 *
 * Concurrent callers for the same key await the same request. Returns null when
 * the response carries no usable snapshot so callers can keep their previous
 * value instead of blanking the panel.
 */
export async function fetchRiskSnapshot(
  city: string,
  options: FetchRiskSnapshotOptions = {},
): Promise<RiskSnapshot | null> {
  const cityName = (city || '').trim();
  if (!cityName) return null;

  const key = keyFor(cityName, options.coordinate);
  const now = Date.now();
  const existing = cache.get(key);

  if (!options.force && existing) {
    if (existing.promise) return existing.promise;
    if (existing.data && now - existing.at < FRESH_MS) return existing.data;
  }

  const promise = (async (): Promise<RiskSnapshot | null> => {
    let payload: { snapshot?: RiskSnapshot } | null = null;
    try {
      payload = await apiRequest<{ snapshot?: RiskSnapshot } | null>('/api/v1/risk/realtime', {
        method: 'POST',
        body: {
          city: cityName,
          coordinate: options.coordinate ?? undefined,
          baseline: options.baseline,
        },
        retries: 0,
        // 该接口要串行拉取天气/路况/安全三类上游，正常也要 10 秒左右；
        // 默认 12 秒会把“稍慢”误判成服务不可用，留足余量。
        timeoutMs: 25_000,
      });
    } catch (error) {
      // A non-OK response means "no usable snapshot right now" — callers keep
      // their previous value. Transport failures still propagate so the polling
      // layer can surface them.
      if (error instanceof ApiRequestError && error.status > 0) return null;
      throw error;
    }
    const snapshot = payload?.snapshot;
    if (!snapshot || typeof snapshot !== 'object') return null;
    cache.set(key, { data: snapshot, at: Date.now(), promise: null });
    return snapshot;
  })();

  cache.set(key, { data: existing?.data ?? null, at: existing?.at ?? 0, promise });

  try {
    const snapshot = await promise;
    // A failed or empty response must not be memoised as usable data,
    // otherwise the next poll would "reuse" a null snapshot and never retry.
    if (snapshot === null) {
      const entry = cache.get(key);
      if (entry && entry.promise === promise) {
        if (entry.data) cache.set(key, { data: entry.data, at: entry.at, promise: null });
        else cache.delete(key);
      }
    }
    return snapshot;
  } catch (error) {
    // Drop the failed in-flight entry so the next tick retries instead of
    // awaiting a rejected promise forever.
    const entry = cache.get(key);
    if (entry && entry.promise === promise) {
      if (entry.data) cache.set(key, { data: entry.data, at: entry.at, promise: null });
      else cache.delete(key);
    }
    throw error;
  }
}

/** Query only explicitly selected global cities through the bounded batch API. */
export async function fetchGlobalRiskSnapshots(
  cities: GlobalRiskCityRequest[],
): Promise<GlobalRiskBatchResponse> {
  const selected = cities
    .filter((item) => item && typeof item.city === 'string' && item.city.trim())
    .slice(0, 6)
    .map((item) => ({ ...item, city: item.city.trim() }));
  if (!selected.length) return { results: [], errors: [{ code: 'CITIES_REQUIRED', message: '请选择城市' }] };
  let payload: GlobalRiskBatchResponse | null = null;
  try {
    payload = await apiRequest<GlobalRiskBatchResponse>('/api/v1/risk/global', {
      method: 'POST',
      body: { cities: selected },
      retries: 0,
    });
  } catch (error) {
    if (error instanceof ApiRequestError) throw new Error(error.message);
    throw error;
  }
  return {
    results: Array.isArray(payload?.results) ? payload!.results : [],
    errors: Array.isArray(payload?.errors) ? payload!.errors : [],
    limits: payload?.limits,
  };
}

/** Drop memoised snapshots, e.g. when the user explicitly switches city. */
export function invalidateRiskSnapshot(city?: string): void {
  if (!city) {
    cache.clear();
    return;
  }
  const prefix = `${city.trim()}::`;
  for (const key of Array.from(cache.keys())) {
    if (key.startsWith(prefix)) cache.delete(key);
  }
}

/** Exposed for tests: current number of memoised keys. */
export function riskSnapshotCacheSize(): number {
  return cache.size;
}
