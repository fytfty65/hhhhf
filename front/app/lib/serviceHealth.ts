/**
 * 大厅顶部「服务状态」的真实来源。
 *
 * 以前这里是一句写死的「行程服务在线」，把网关、AI 规划、地图数据、实时数据
 * 和交易供应商混成了一个「在线」。这些能力的实际可用性完全不同：
 *   - 网关是否启动：/healthz
 *   - AI 规划是否可达：/api/v1/planning/agents/capabilities（网关会代理 AI 服务）
 *   - 数据供应商是否「已配置」：/api/v1/planning/providers/health（只看配置，不发外部请求）
 *   - 数据供应商是否「可用」：/api/v1/planning/providers/probe（真实探测，较慢，按需触发）
 *
 * 所以状态分为两级：configured（已配置）与 available（可用）。
 * 没有探测过供应商时，只能说「已配置，尚未检测」，不能写成「可用」。
 */

import { apiRequest } from './apiClient';

export type HealthState = 'available' | 'configured' | 'unavailable' | 'unknown';

export const HEALTH_STATE_LABELS: Record<HealthState, string> = {
  available: '可用',
  configured: '已配置，未检测',
  unavailable: '不可用',
  unknown: '未检测',
};

export type ServiceHealthRow = {
  key: 'gateway' | 'ai' | 'map' | 'realtime' | 'booking';
  label: string;
  state: HealthState;
  detail: string;
};

export type ServiceHealthSnapshot = {
  rows: ServiceHealthRow[];
  /** 网关在线的判断是其他能力的前提：网关不可达时其余都只能算「未检测」。 */
  gatewayOnline: boolean;
  checkedAt: string | null;
  probed: boolean;
};

const GATEWAY_TIMEOUT_MS = 4000;
const PLANNING_TIMEOUT_MS = 6000;

function dataOf(payload: any): any {
  return payload?.data ?? payload ?? {};
}

function providerRow(providers: Record<string, any>, kinds: string[]) {
  const configuredKinds = kinds.filter((kind) => providers?.[kind]?.configured === true);
  const reachableKinds = kinds.filter((kind) => providers?.[kind]?.reachable === true);
  return { configuredKinds, reachableKinds };
}

function availabilityFrom(
  kinds: string[],
  providers: Record<string, any>,
  probed: boolean,
): { state: HealthState; detail: string } {
  const { configuredKinds, reachableKinds } = providerRow(providers, kinds);
  if (configuredKinds.length === 0) {
    return { state: 'unavailable', detail: '未配置对应供应商，相关数据使用内置估算' };
  }
  if (!probed) {
    return { state: 'configured', detail: `已配置 ${configuredKinds.length} 个供应商，尚未检测可用性` };
  }
  if (reachableKinds.length === 0) {
    return { state: 'unavailable', detail: `已配置 ${configuredKinds.length} 个供应商，但本次探测均失败` };
  }
  return {
    state: 'available',
    detail: `${reachableKinds.length}/${configuredKinds.length} 个已配置供应商探测可达`,
  };
}

/**
 * 读取一次服务状态。
 * `probe = false` 只查询配置（快，默认）；`probe = true` 会触发真实外部探测（慢，需要用户主动点）。
 */
export async function loadServiceHealth(options: { probe?: boolean } = {}): Promise<ServiceHealthSnapshot> {
  const probe = Boolean(options.probe);

  const [gatewayResult, providersResult, aiResult] = await Promise.allSettled([
    apiRequest<any>('/healthz', { auth: false, timeoutMs: GATEWAY_TIMEOUT_MS, retries: 0 }),
    apiRequest<any>(
      probe ? '/api/v1/planning/providers/probe' : '/api/v1/planning/providers/health',
      { timeoutMs: probe ? 10000 : PLANNING_TIMEOUT_MS, retries: 0 },
    ),
    apiRequest<any>('/api/v1/planning/agents/capabilities', { timeoutMs: PLANNING_TIMEOUT_MS, retries: 0 }),
  ]);

  const gatewayOnline = gatewayResult.status === 'fulfilled' && gatewayResult.value?.status === 'ok';
  const providers = providersResult.status === 'fulfilled' ? (dataOf(providersResult.value)?.providers || {}) : {};
  const hasProviderData = providersResult.status === 'fulfilled';

  let aiRow: ServiceHealthRow;
  if (aiResult.status === 'fulfilled') {
    const capabilities = dataOf(aiResult.value)?.capabilities;
    const count = Array.isArray(capabilities) ? capabilities.length : 0;
    aiRow = count > 0
      ? { key: 'ai', label: 'AI 规划', state: 'available', detail: `已接入 ${count} 项规划能力` }
      : { key: 'ai', label: 'AI 规划', state: 'unknown', detail: '网关已响应，但没有返回能力清单' };
  } else {
    const error: any = aiResult.reason;
    const upstreamDown = error?.status === 502 || error?.code === 'AGENT_CAPABILITIES_UNAVAILABLE';
    aiRow = upstreamDown
      ? { key: 'ai', label: 'AI 规划', state: 'unavailable', detail: '网关在线，但 AI 规划服务未连接' }
      : { key: 'ai', label: 'AI 规划', state: 'unknown', detail: '未能完成检测，请稍后重试' };
  }

  const mapAvailability = availabilityFrom(['destination', 'scenic'], providers, probe);
  const realtimeAvailability = availabilityFrom(['weather', 'crowd', 'transport'], providers, probe);
  const bookingAvailability = availabilityFrom(['lodging', 'dining', 'scenic'], providers, probe);

  const rows: ServiceHealthRow[] = [
    {
      key: 'gateway',
      label: '网关',
      state: gatewayOnline ? 'available' : 'unavailable',
      detail: gatewayOnline ? '行程网关已响应健康检查' : '无法连接行程网关',
    },
    aiRow,
    { key: 'map', label: '地图与景点数据', ...mapAvailability },
    { key: 'realtime', label: '实时数据', ...realtimeAvailability },
    { key: 'booking', label: '交易供应商', ...bookingAvailability },
  ];

  if (!gatewayOnline) {
    // 网关不可达时其他探测都没有意义，统一标成「未检测」，避免出现「AI 可用但网关离线」这种自相矛盾的组合。
    rows.forEach((row) => {
      if (row.key !== 'gateway' && row.state !== 'unavailable') {
        row.state = 'unknown';
        row.detail = '网关不可达，未完成检测';
      }
    });
  }

  if (!hasProviderData && gatewayOnline) {
    rows.forEach((row) => {
      if (row.key === 'map' || row.key === 'realtime' || row.key === 'booking') {
        row.state = 'unknown';
        row.detail = '未能读取供应商配置';
      }
    });
  }

  return {
    rows,
    gatewayOnline,
    checkedAt: new Date().toISOString(),
    probed: probe,
  };
}

/** 顶部胶囊的摘要文案：只描述真实状态，不用「在线」一词概括所有能力。 */
export function summarizeHealth(snapshot: ServiceHealthSnapshot | null): {
  text: string;
  tone: 'ok' | 'warn' | 'bad';
} {
  if (!snapshot) return { text: '正在检测服务状态', tone: 'warn' };
  if (!snapshot.gatewayOnline) return { text: '网关不可达', tone: 'bad' };
  const available = snapshot.rows.filter((row) => row.state === 'available').length;
  const unavailable = snapshot.rows.filter((row) => row.state === 'unavailable').length;
  if (unavailable > 0) return { text: `部分能力不可用（${available}/5 可用）`, tone: 'warn' };
  if (available === snapshot.rows.length) return { text: '全部能力可用', tone: 'ok' };
  return { text: `部分能力可用（${available}/5 可用）`, tone: 'warn' };
}
