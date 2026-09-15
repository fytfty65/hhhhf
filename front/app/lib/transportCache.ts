// 查询结果缓存机制
// 内存缓存 + TTL 过期，优化重复查询响应速度

import type { TransportSearchResponse } from '../types/transport';

interface CacheEntry {
  data: TransportSearchResponse;
  timestamp: number;
  ttl: number;
}

const cache = new Map<string, CacheEntry>();

const DEFAULT_TTL_MS = 5 * 60 * 1000; // 5分钟

function buildCacheKey(params: { from: string; to: string; date: string; type: string }): string {
  return `transport:${params.type}:${params.from}:${params.to}:${params.date}`;
}

export function getCached(params: { from: string; to: string; date: string; type: string }): TransportSearchResponse | null {
  const key = buildCacheKey(params);
  const entry = cache.get(key);
  if (!entry) return null;
  if (Date.now() - entry.timestamp > entry.ttl) {
    cache.delete(key);
    return null;
  }
  return { ...entry.data, cached: true };
}

export function setCache(params: { from: string; to: string; date: string; type: string }, data: TransportSearchResponse, ttlMs = DEFAULT_TTL_MS): void {
  const key = buildCacheKey(params);
  cache.set(key, {
    data: { ...data, cached: false },
    timestamp: Date.now(),
    ttl: ttlMs,
  });
}

export function clearCache(): void {
  cache.clear();
}

export function clearExpiredCache(): void {
  const now = Date.now();
  for (const [key, entry] of cache.entries()) {
    if (now - entry.timestamp > entry.ttl) {
      cache.delete(key);
    }
  }
}

export function getCacheSize(): number {
  return cache.size;
}

// 定期清理过期缓存（每30秒）
if (typeof window !== 'undefined') {
  setInterval(clearExpiredCache, 30000);
}