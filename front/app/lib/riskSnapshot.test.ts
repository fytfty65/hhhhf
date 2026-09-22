import test from 'node:test';
import assert from 'node:assert/strict';
import { fetchGlobalRiskSnapshots, fetchRiskSnapshot, invalidateRiskSnapshot, riskSnapshotCacheSize, summarizeGlobalRiskSnapshot } from './riskSnapshot.ts';

// The bug these tests lock down: WorldSafetyGlobe and RiskPushCenter each ran
// their own 30s timer against /api/v1/risk/realtime, so with the 3D radar open
// the same city was fetched twice per interval even though the backend handler
// costs ~10s of upstream provider work.

interface Call {
  url: string;
  body: unknown;
}

function stubFetch(respond: (call: Call, index: number) => { ok?: boolean; json?: unknown; reject?: boolean }) {
  const calls: Call[] = [];
  const original = globalThis.fetch;
  globalThis.fetch = (async (input: unknown, init?: { body?: string }) => {
    const call: Call = { url: String(input), body: init?.body ? JSON.parse(init.body) : undefined };
    calls.push(call);
    const result = respond(call, calls.length - 1);
    if (result.reject) throw new Error('network down');
    return {
      ok: result.ok ?? true,
      status: result.ok === false ? 502 : 200,
      json: async () => result.json,
    } as unknown as Response;
  }) as typeof globalThis.fetch;
  return {
    calls,
    restore() {
      globalThis.fetch = original;
    },
  };
}

test('concurrent callers for the same city share a single request', async () => {
  invalidateRiskSnapshot();
  const stub = stubFetch(() => ({ json: { snapshot: { cii_score: 12 } } }));
  try {
    const [a, b, c] = await Promise.all([
      fetchRiskSnapshot('Kyoto'),
      fetchRiskSnapshot('Kyoto'),
      fetchRiskSnapshot('Kyoto'),
    ]);
    assert.equal(stub.calls.length, 1, 'three concurrent reads must produce one fetch');
    assert.equal((a as { cii_score: number }).cii_score, 12);
    assert.deepEqual(b, a);
    assert.deepEqual(c, a);
  } finally {
    stub.restore();
  }
});

test('a recent snapshot is reused without a second request', async () => {
  invalidateRiskSnapshot();
  const stub = stubFetch(() => ({ json: { snapshot: { cii_score: 20 } } }));
  try {
    await fetchRiskSnapshot('Osaka');
    const second = await fetchRiskSnapshot('Osaka');
    assert.equal(stub.calls.length, 1, 'a fresh snapshot must not be refetched');
    assert.equal((second as { cii_score: number }).cii_score, 20);
  } finally {
    stub.restore();
  }
});

test('force bypasses the reuse window for manual refresh paths', async () => {
  invalidateRiskSnapshot();
  const stub = stubFetch((_call, index) => ({ json: { snapshot: { cii_score: 10 + index } } }));
  try {
    await fetchRiskSnapshot('Nara');
    await fetchRiskSnapshot('Nara', { force: true });
    assert.equal(stub.calls.length, 2, 'force must issue a real request');
  } finally {
    stub.restore();
  }
});

test('different cities and coordinates are cached independently', async () => {
  invalidateRiskSnapshot();
  const stub = stubFetch(() => ({ json: { snapshot: { cii_score: 5 } } }));
  try {
    await fetchRiskSnapshot('Kyoto', { coordinate: [135.7, 35.0] });
    await fetchRiskSnapshot('Kyoto', { coordinate: [135.8, 35.1] });
    await fetchRiskSnapshot('Osaka');
    assert.equal(stub.calls.length, 3, 'distinct keys must not share a request');
    assert.equal(riskSnapshotCacheSize(), 3);
  } finally {
    stub.restore();
  }
});

test('an empty city name never hits the network', async () => {
  invalidateRiskSnapshot();
  const stub = stubFetch(() => ({ json: { snapshot: {} } }));
  try {
    assert.equal(await fetchRiskSnapshot('   '), null);
    assert.equal(stub.calls.length, 0);
  } finally {
    stub.restore();
  }
});

test('a failed request clears the in-flight entry so the next tick retries', async () => {
  invalidateRiskSnapshot();
  let attempt = 0;
  const stub = stubFetch(() => {
    attempt += 1;
    return attempt === 1 ? { reject: true } : { json: { snapshot: { cii_score: 33 } } };
  });
  try {
    await assert.rejects(() => fetchRiskSnapshot('Sapporo'));
    const recovered = await fetchRiskSnapshot('Sapporo');
    assert.equal(stub.calls.length, 2, 'the retry must issue a new request');
    assert.equal((recovered as { cii_score: number }).cii_score, 33);
  } finally {
    stub.restore();
  }
});

test('a non-ok response yields null and is not memoised as data', async () => {
  invalidateRiskSnapshot();
  const stub = stubFetch(() => ({ ok: false }));
  try {
    assert.equal(await fetchRiskSnapshot('Kobe'), null);
    // No snapshot was stored, so the next call must retry rather than reuse null.
    await fetchRiskSnapshot('Kobe');
    assert.equal(stub.calls.length, 2);
  } finally {
    stub.restore();
  }
});

test('invalidateRiskSnapshot drops only the requested city', async () => {
  invalidateRiskSnapshot();
  const stub = stubFetch(() => ({ json: { snapshot: { cii_score: 1 } } }));
  try {
    await fetchRiskSnapshot('Kyoto');
    await fetchRiskSnapshot('Osaka');
    invalidateRiskSnapshot('Kyoto');
    await fetchRiskSnapshot('Kyoto');
    await fetchRiskSnapshot('Osaka');
    assert.equal(stub.calls.length, 3, 'only the invalidated city should refetch');
  } finally {
    stub.restore();
    invalidateRiskSnapshot();
  }
});

test('global batch query sends selected cities and preserves source evidence', async () => {
  const stub = stubFetch(() => ({ json: {
    results: [{ city: '东京', available: true, changes: [], snapshot: { source: 'provider-x', risk_ts: 1770000000000 } }],
    errors: [],
    limits: { max_cities: 6, concurrency: 3 },
  } }));
  try {
    const response = await fetchGlobalRiskSnapshots([{ city: '东京', coordinate: [139.7, 35.6] }]);
    assert.equal(stub.calls.length, 1);
    assert.equal(stub.calls[0].url.endsWith('/api/v1/risk/global'), true);
    assert.deepEqual((stub.calls[0].body as { cities: unknown[] }).cities[0], { city: '东京', coordinate: [139.7, 35.6] });
    assert.equal(response.results[0].snapshot?.source, 'provider-x');
  } finally {
    stub.restore();
  }
});

test('global risk summary reports evidence and never invents missing CII', () => {
  const summary = summarizeGlobalRiskSnapshot({
    risk_level: 'MEDIUM',
    source: 'risk-provider',
    risk_ts: 1_770_000_000_000,
    is_estimated: false,
    signal_sources: {
      safety: { provider: 'risk-provider', available: true, estimated: false },
      weather: { provider: 'weather-provider', available: true, estimated: true },
      traffic: { provider: 'none', available: false, estimated: false },
    },
  }, 1_770_000_030_000);
  assert.equal(summary.cii, 'N/A');
  assert.equal(summary.availableSignals, 2);
  assert.equal(summary.totalSignals, 3);
  assert.equal(summary.estimated, true);
  assert.equal(summary.freshness, '30 秒前');
});
