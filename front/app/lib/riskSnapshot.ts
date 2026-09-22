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
// It intentionally depends on nothing but fetch so it can be unit tested with
// `node --experimental-strip-types`, which does not transform .tsx modules.

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

/** Resolve the API base exactly like lib/utils does, without importing it. */
function apiBase(): string {
  const configured = process.env.NEXT_PUBLIC_API_BASE;
  if (configured) return configured.replace(/\/$/, '');
  if (typeof window !== 'undefined' && window.location) {
    const protocol = window.location.protocol === 'https:' ? 'https:' : 'http:';
    return `${protocol}//${window.location.hostname}:8080`;
  }
  return 'http://127.0.0.1:8080';
}

function authHeaders(): Record<string, string> {
  const base: Record<string, string> = { 'Content-Type': 'application/json' };
  try {
    if (typeof localStorage !== 'undefined') {
      const raw = localStorage.getItem('omni_user');
      const token = raw ? (JSON.parse(raw) as { token?: string } | null)?.token : '';
      if (typeof token === 'string' && token) base.Authorization = `Bearer ${token}`;
    }
  } catch {
    // A malformed localStorage entry must not break risk polling.
  }
  return base;
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
    const response = await fetch(`${apiBase()}/api/v1/risk/realtime`, {
      method: 'POST',
      headers: authHeaders(),
      body: JSON.stringify({
        city: cityName,
        coordinate: options.coordinate ?? undefined,
        baseline: options.baseline,
      }),
    });
    if (!response.ok) return null;
    const payload = (await response.json()) as { snapshot?: RiskSnapshot } | null;
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
  const response = await fetch(`${apiBase()}/api/v1/risk/global`, {
    method: 'POST',
    headers: authHeaders(),
    body: JSON.stringify({ cities: selected }),
  });
  const payload = (await response.json().catch(() => null)) as GlobalRiskBatchResponse | null;
  if (!response.ok) {
    const error = payload?.errors?.[0] || { code: 'GLOBAL_RISK_UNAVAILABLE', message: '全球情报服务暂不可用' };
    throw new Error(error.message || error.code || 'GLOBAL_RISK_UNAVAILABLE');
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
