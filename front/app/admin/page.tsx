'use client';

import { useCallback, useEffect, useState } from 'react';
import type { ReactNode } from 'react';
import Link from 'next/link';
import { AlertTriangle, ArrowLeft, Camera, Check, RefreshCw, ShieldCheck, X } from 'lucide-react';
import { apiFetch, apiJson, ApiRequestError } from '../lib/apiClient';

type EventRow = { type: string; count: number };
type ProbeStat = { name: string; probes: number; ok: number; empty: number; candidates: number; avg_latency_ms: number };
type ProbeSummary = {
  unit?: string;
  total?: number;
  ok?: number;
  empty?: number;
  success_rate?: number;
  candidates?: number;
  avg_latency_ms?: number;
  per_generated?: number;
  by_kind?: ProbeStat[];
  by_provider?: ProbeStat[];
  failing_providers?: { provider: string; probes: number }[];
};

type Overview = {
  generated_at?: string;
  window_days?: number;
  scope?: string;
  health?: { db_ok?: boolean };
  users?: { total?: number; guests?: number; new_7d?: number; active_24h?: number };
  trips?: { total?: number; last_7d?: number };
  rooms?: { total?: number; members?: number; last_7d?: number };
  feedback?: { total?: number; last_7d?: number; positive_7d?: number; negative_7d?: number };
  planning?: {
    last_7d?: number;
    by_type?: EventRow[];
    generated_7d?: number;
    adopted_7d?: number;
    adoption_rate?: number;
    provider_probes?: ProbeSummary;
  };
  security?: { login_failures?: number; lockouts?: number; rate_limited?: number; identity_mismatch?: number };
  runtime?: { uptime_seconds?: number; goroutines?: number; heap_alloc_mb?: number; gc_cycles?: number; go_version?: string };
  warnings?: string[];
};

type AdminPhoto = {
  id: string;
  node_name: string;
  uploader_id: string;
  uploader_ids?: string[];
  mime: string;
  bytes: number;
  width: number;
  height: number;
  status: string;
  reject_reason?: string;
  reported_count?: number;
  created_at: string;
};

const PHOTO_TABS = [
  { key: 'pending', label: '待审核' },
  { key: 'approved', label: '已通过' },
  { key: 'rejected', label: '已拒绝' },
] as const;

// 缩略图必须带 Authorization 取：待审照片是私有的，直接用 <img src> 拿不到。
// 每次取回都生成 objectURL，组件卸载时释放，避免长时间开着看板泄漏内存。
function PhotoThumb({ id }: { id: string }) {
  const [src, setSrc] = useState('');
  useEffect(() => {
    let alive = true;
    let objectUrl = '';
    apiFetch(`/api/v1/photos/${id}`, { timeoutMs: 12000 })
      .then(async (response) => {
        if (!response.ok) return;
        const blob = await response.blob();
        objectUrl = URL.createObjectURL(blob);
        if (alive) setSrc(objectUrl);
      })
      .catch(() => {});
    return () => {
      alive = false;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [id]);
  return src ? (
    // blob URL（私有图需带 Authorization 取），无法交给 next/image 优化
    <img src={src} alt="" className="h-16 w-16 shrink-0 rounded-xl border border-stone-200 object-cover" />
  ) : (
    <span className="flex h-16 w-16 shrink-0 items-center justify-center rounded-xl border border-dashed border-stone-200 bg-stone-50 text-stone-300">
      <Camera className="h-5 w-5" />
    </span>
  );
}

// 用户实拍审核：用户上传时一律 pending（仅本人可见），通过后才在规划里作为该景点实拍展示。
// 拒绝必须给理由，理由会回传给上传者。
function PhotoReviewCard() {
  const [status, setStatus] = useState<string>('pending');
  const [photos, setPhotos] = useState<AdminPhoto[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [actionError, setActionError] = useState('');
  const [busyId, setBusyId] = useState('');
  const [rejectingId, setRejectingId] = useState('');
  const [reason, setReason] = useState('');

  const load = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const payload = await apiJson<{ photos?: AdminPhoto[] }>(`/api/v1/admin/photos?status=${status}`);
      setPhotos(payload.photos || []);
    } catch (err) {
      setPhotos([]);
      setError(
        err instanceof ApiRequestError && err.status === 403
          ? '当前账号没有审核权限（ADMIN_USER_IDS 未包含你）'
          : '照片列表加载失败，请稍后重试',
      );
    } finally {
      setLoading(false);
    }
  }, [status]);

  useEffect(() => {
    void load();
  }, [load]);

  const moderate = async (photo: AdminPhoto, next: 'approved' | 'rejected', why = '') => {
    setBusyId(photo.id);
    setActionError('');
    try {
      await apiJson(
        `/api/v1/admin/photos/${photo.id}/moderate`,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ status: next, reason: why }),
        },
        12000,
      );
      setPhotos((prev) => prev.filter((row) => row.id !== photo.id));
      setRejectingId('');
      setReason('');
    } catch (err) {
      setActionError(err instanceof ApiRequestError ? err.message : '操作失败，请重试');
    } finally {
      setBusyId('');
    }
  };

  return (
    <Card title="景点实拍审核" hint="用户上传 · 通过后才在规划中展示">
      <div className="flex flex-wrap items-center gap-2">
        {PHOTO_TABS.map((tab) => (
          <button
            key={tab.key}
            type="button"
            onClick={() => {
              setStatus(tab.key);
              setRejectingId('');
              setActionError('');
            }}
            className={`rounded-full px-3 py-1 text-[11px] font-bold transition ${
              status === tab.key ? 'bg-orange-500 text-white' : 'bg-stone-100 text-stone-600 hover:bg-stone-200'
            }`}
          >
            {tab.label}
            {status === tab.key && !loading ? ` ${photos.length}` : ''}
          </button>
        ))}
        <button
          type="button"
          onClick={() => void load()}
          disabled={loading}
          className="ml-auto flex items-center gap-1 rounded-full px-2.5 py-1 text-[11px] font-bold text-stone-500 hover:bg-stone-100 disabled:opacity-50"
        >
          <RefreshCw className={`h-3 w-3 ${loading ? 'animate-spin' : ''}`} /> 刷新
        </button>
      </div>

      {error && (
        <p className="mt-3 rounded-xl border border-rose-200 bg-rose-50/70 px-3 py-2 text-[11px] font-semibold text-rose-700">{error}</p>
      )}
      {actionError && (
        <p className="mt-3 rounded-xl border border-rose-200 bg-rose-50/70 px-3 py-2 text-[11px] font-semibold text-rose-700">{actionError}</p>
      )}

      {loading ? (
        <p className="mt-4 text-xs text-stone-500">正在加载…</p>
      ) : photos.length === 0 ? (
        <p className="mt-4 text-xs text-stone-500">
          {status === 'pending' ? '没有待审核的照片，队列是空的。' : '这个状态下暂时没有照片。'}
        </p>
      ) : (
        <ul className="mt-4 divide-y divide-stone-100">
          {photos.map((photo) => (
            <li key={photo.id} className="flex flex-col gap-3 py-3 first:pt-0 last:pb-0">
              <div className="flex items-start gap-3">
                <PhotoThumb id={photo.id} />
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="truncate text-sm font-bold text-stone-800">{photo.node_name || '未标注景点'}</span>
                    {(photo.reported_count || 0) > 0 && (
                      <span className="rounded-full bg-rose-50 px-2 py-0.5 text-[10px] font-bold text-rose-700">
                        被举报 {photo.reported_count}
                      </span>
                    )}
                  </div>
                  <p className="mt-1 text-[11px] text-stone-500">
                    {photo.width}×{photo.height} · {Math.max(1, Math.round(photo.bytes / 1024))} KB · 上传者{' '}
                    <span className="font-mono">{(photo.uploader_ids?.[0] || photo.uploader_id || '').slice(0, 8)}</span> ·{' '}
                    {new Date(photo.created_at).toLocaleString('zh-CN')}
                  </p>
                  {photo.status === 'rejected' && photo.reject_reason && (
                    <p className="mt-1 text-[11px] text-stone-500">拒绝理由：{photo.reject_reason}</p>
                  )}
                </div>
                {status === 'pending' && rejectingId !== photo.id && (
                  <div className="flex shrink-0 gap-2">
                    <button
                      type="button"
                      disabled={busyId === photo.id}
                      onClick={() => void moderate(photo, 'approved')}
                      className="flex items-center gap-1 rounded-xl bg-emerald-600 px-3 py-1.5 text-[11px] font-bold text-white hover:bg-emerald-700 disabled:opacity-50"
                    >
                      <Check className="h-3.5 w-3.5" /> 通过
                    </button>
                    <button
                      type="button"
                      disabled={busyId === photo.id}
                      onClick={() => {
                        setRejectingId(photo.id);
                        setReason('');
                        setActionError('');
                      }}
                      className="flex items-center gap-1 rounded-xl bg-stone-100 px-3 py-1.5 text-[11px] font-bold text-stone-700 hover:bg-rose-50 hover:text-rose-700 disabled:opacity-50"
                    >
                      <X className="h-3.5 w-3.5" /> 拒绝
                    </button>
                  </div>
                )}
              </div>

              {status === 'pending' && rejectingId === photo.id && (
                <div className="rounded-xl bg-stone-50 p-3">
                  <label className="text-[11px] font-bold text-stone-600" htmlFor={`reason-${photo.id}`}>
                    拒绝理由（会回传给上传者，必填）
                  </label>
                  <textarea
                    id={`reason-${photo.id}`}
                    value={reason}
                    onChange={(event) => setReason(event.target.value)}
                    rows={2}
                    maxLength={200}
                    placeholder="例如：与景点无关 / 清晰度不足，无法用于规划展示"
                    className="mt-1 w-full resize-none rounded-lg border border-stone-200 bg-white px-3 py-2 text-xs text-stone-700 outline-none focus:border-orange-400"
                  />
                  <div className="mt-2 flex justify-end gap-2">
                    <button
                      type="button"
                      onClick={() => {
                        setRejectingId('');
                        setReason('');
                      }}
                      className="rounded-xl px-3 py-1.5 text-[11px] font-bold text-stone-500 hover:bg-white"
                    >
                      取消
                    </button>
                    <button
                      type="button"
                      disabled={busyId === photo.id || reason.trim().length === 0}
                      onClick={() => void moderate(photo, 'rejected', reason.trim())}
                      className="rounded-xl bg-rose-600 px-3 py-1.5 text-[11px] font-bold text-white hover:bg-rose-700 disabled:opacity-50"
                    >
                      确认拒绝
                    </button>
                  </div>
                </div>
              )}
            </li>
          ))}
        </ul>
      )}

      <p className="mt-3 text-[11px] leading-5 text-stone-500">
        上传的照片默认只有本人可见；审核通过后才会作为该景点的实拍展示在规划结果里。举报会自动把照片退回「待审核」。
      </p>
    </Card>
  );
}

type PhotoEventRow = {
  photo_id: string;
  node_name: string;
  photo_status: string;
  uploader_id?: string;
  reported_count?: number;
  kind: string;
  actor_id?: string;
  actor_name?: string;
  reason?: string;
  created_at: string;
};

const EVENT_BADGES: Record<string, { label: string; className: string }> = {
  report: { label: '举报', className: 'bg-amber-50 text-amber-700' },
  approved: { label: '通过', className: 'bg-emerald-50 text-emerald-700' },
  rejected: { label: '拒绝', className: 'bg-rose-50 text-rose-700' },
  pending: { label: '退回待审', className: 'bg-stone-100 text-stone-600' },
};

// 举报与审核留痕：照片被谁举报、管理员何时因何下架或放行，用于申诉回溯与滥用排查。
function PhotoHistoryCard() {
  const [events, setEvents] = useState<PhotoEventRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  const load = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const payload = await apiJson<{ events?: PhotoEventRow[] }>('/api/v1/admin/photo-reports?limit=50');
      setEvents(payload.events || []);
    } catch (err) {
      setEvents([]);
      setError(
        err instanceof ApiRequestError && err.status === 403
          ? '当前账号没有查看审核流水的权限'
          : '处理记录加载失败，请稍后重试',
      );
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  return (
    <Card title="举报与审核记录" hint="谁、何时、为何 · 最近 50 条">
      <div className="flex justify-end">
        <button
          type="button"
          onClick={() => void load()}
          disabled={loading}
          className="flex items-center gap-1 rounded-full px-2.5 py-1 text-[11px] font-bold text-stone-500 hover:bg-stone-100 disabled:opacity-50"
        >
          <RefreshCw className={`h-3 w-3 ${loading ? 'animate-spin' : ''}`} /> 刷新
        </button>
      </div>

      {error && (
        <p className="mt-2 rounded-xl border border-rose-200 bg-rose-50/70 px-3 py-2 text-[11px] font-semibold text-rose-700">{error}</p>
      )}

      {loading ? (
        <p className="mt-2 text-xs text-stone-500">正在加载…</p>
      ) : events.length === 0 ? (
        <p className="mt-2 text-xs text-stone-500">还没有举报或审核记录。</p>
      ) : (
        <ol className="mt-3 space-y-2">
          {events.map((event, index) => {
            const badge = EVENT_BADGES[event.kind] || { label: event.kind, className: 'bg-stone-100 text-stone-600' };
            return (
              <li key={`${event.photo_id}-${event.created_at}-${index}`} className="flex items-start gap-2 rounded-xl bg-stone-50/70 px-3 py-2">
                <span className={`mt-0.5 shrink-0 rounded-full px-2 py-0.5 text-[10px] font-bold ${badge.className}`}>{badge.label}</span>
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-[11px] font-bold text-stone-700">{event.node_name || '未标注景点'}</span>
                  <span className="mt-0.5 block text-[11px] leading-4 text-stone-500">
                    {event.actor_name
                      ? `操作人 ${event.actor_name}`
                      : event.actor_id
                        ? `操作人 ${event.actor_id.slice(0, 8)}`
                        : '操作人未知'}
                    {event.reason ? ` · ${event.reason}` : ''}
                    {' · '}
                    {new Date(event.created_at).toLocaleString('zh-CN')}
                  </span>
                </span>
              </li>
            );
          })}
        </ol>
      )}
    </Card>
  );
}

function fmt(value: number | undefined, suffix = '') {
  if (typeof value !== 'number' || !Number.isFinite(value)) return '—';
  return `${value.toLocaleString('zh-CN')}${suffix}`;
}

function duration(seconds: number | undefined) {
  if (typeof seconds !== 'number' || seconds <= 0) return '—';
  const days = Math.floor(seconds / 86400);
  const hours = Math.floor((seconds % 86400) / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  if (days > 0) return `${days} 天 ${hours} 小时`;
  if (hours > 0) return `${hours} 小时 ${minutes} 分`;
  return `${minutes} 分`;
}

function Card({ title, hint, children }: { title: string; hint?: string; children: ReactNode }) {
  return (
    <section className="rounded-2xl border border-stone-200/80 bg-white p-5 shadow-2xs">
      <div className="flex items-baseline justify-between gap-3">
        <h2 className="text-sm font-black text-stone-900">{title}</h2>
        {hint && <span className="text-[11px] font-semibold text-stone-400">{hint}</span>}
      </div>
      <div className="mt-4">{children}</div>
    </section>
  );
}

function Metric({ label, value, tone = 'text-stone-900' }: { label: string; value: string; tone?: string }) {
  return (
    <div className="rounded-xl bg-stone-50 px-3 py-3">
      <p className="text-[11px] font-bold text-stone-500">{label}</p>
      <p className={`mt-1 text-xl font-black tabular-nums ${tone}`}>{value}</p>
    </div>
  );
}

export default function AdminOverviewPage() {
  const [data, setData] = useState<Overview | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [forbidden, setForbidden] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const response = await apiJson<{ data?: Overview } & Overview>('/api/v1/admin/overview');
      setData(response?.data || response || null);
      setForbidden(false);
    } catch (err) {
      if (err instanceof ApiRequestError && err.status === 403) {
        setForbidden(true);
        setData(null);
      } else if (err instanceof ApiRequestError && err.status === 401) {
        setError('登录状态已失效，请先回到大厅重新登录，再打开这个页面。');
      } else {
        setError(err instanceof Error ? err.message : '暂时无法载入运维数据');
      }
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const planningTotal = data?.planning?.last_7d || 0;
  const maxEvent = Math.max(1, ...(data?.planning?.by_type || []).map((row) => row.count));
  const probes = data?.planning?.provider_probes;
  const probeRate = typeof probes?.success_rate === 'number' ? probes.success_rate : undefined;
  const maxProbeKind = Math.max(1, ...(probes?.by_kind || []).map((row) => row.probes));

  return (
    <main className="min-h-screen bg-[#FAF8F5] px-4 py-10 text-stone-900">
      <div className="mx-auto max-w-5xl">
        <header className="flex flex-wrap items-start justify-between gap-4">
          <div>
            <p className="text-xs font-bold tracking-[0.2em] text-orange-600">OMNIROUTE · OPS</p>
            <h1 className="mt-1 text-2xl font-black sm:text-3xl">运维看板</h1>
            <p className="mt-2 max-w-2xl text-xs leading-5 text-stone-600 sm:text-sm">
              只读页面，数据来自服务端聚合计数。这里不展示任何用户的路书正文、聊天内容或账号明细；
              请求级指标请看网关 <span className="font-mono text-[11px]">/metrics</span>，安全事件取证请看服务端结构化日志。
            </p>
          </div>
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={() => void load()}
              disabled={loading}
              className="inline-flex items-center gap-1.5 rounded-xl border border-stone-300 bg-white px-3 py-2 text-xs font-bold text-stone-700 transition hover:border-orange-300 hover:text-orange-700 disabled:opacity-50"
            >
              <RefreshCw className={`h-3.5 w-3.5 ${loading ? 'animate-spin' : ''}`} /> 刷新
            </button>
            <Link
              href="/"
              className="inline-flex items-center gap-1.5 rounded-xl border border-stone-300 bg-white px-3 py-2 text-xs font-bold text-stone-700 transition hover:border-orange-300 hover:text-orange-700"
            >
              <ArrowLeft className="h-3.5 w-3.5" /> 返回大厅
            </Link>
          </div>
        </header>

        {forbidden && (
          <div className="mt-8 flex items-start gap-3 rounded-2xl border border-amber-200 bg-amber-50 px-5 py-4 text-amber-800" role="alert">
            <ShieldCheck className="mt-0.5 h-5 w-5 shrink-0" />
            <div>
              <p className="text-sm font-bold">当前账号不是管理员</p>
              <p className="mt-1 text-xs leading-5">
                这个页面需要服务端 <span className="font-mono">ADMIN_USER_IDS</span> 白名单授权。前端隐藏入口不算权限，接口本身会拒绝非白名单账号。
              </p>
            </div>
          </div>
        )}

        {error && !forbidden && (
          <div className="mt-8 flex items-start gap-3 rounded-2xl border border-rose-200 bg-rose-50 px-5 py-4 text-rose-700" role="alert">
            <AlertTriangle className="mt-0.5 h-5 w-5 shrink-0" />
            <div>
              <p className="text-sm font-bold">载入失败</p>
              <p className="mt-1 text-xs leading-5">{error}</p>
            </div>
          </div>
        )}

        {loading && !data && !forbidden && (
          <div className="mt-10 grid gap-4 sm:grid-cols-2">
            {[0, 1, 2, 3].map((item) => (
              <div key={item} className="h-28 animate-pulse rounded-2xl border border-stone-200/70 bg-white" />
            ))}
          </div>
        )}

        {data && (
          <div className="mt-6 space-y-4">
            <div className="flex flex-wrap items-center gap-2 text-[11px] font-semibold">
              <span className={`rounded-full px-2.5 py-1 ${data.health?.db_ok ? 'bg-emerald-50 text-emerald-700' : 'bg-rose-50 text-rose-700'}`}>
                数据库 {data.health?.db_ok ? '正常' : '异常'}
              </span>
              <span className="rounded-full bg-stone-100 px-2.5 py-1 text-stone-600">运行 {duration(data.runtime?.uptime_seconds)}</span>
              <span className="rounded-full bg-stone-100 px-2.5 py-1 text-stone-600">goroutine {fmt(data.runtime?.goroutines)}</span>
              <span className="rounded-full bg-stone-100 px-2.5 py-1 text-stone-600">堆内存 {fmt(data.runtime?.heap_alloc_mb, ' MB')}</span>
              <span className="rounded-full bg-stone-100 px-2.5 py-1 text-stone-600">统计窗口 {data.window_days || 7} 天</span>
              {data.generated_at && <span className="text-stone-400">更新于 {new Date(data.generated_at).toLocaleString('zh-CN')}</span>}
            </div>

            {(data.warnings || []).length > 0 && (
              <div className="flex items-start gap-2 rounded-xl border border-amber-200 bg-amber-50/70 px-4 py-2.5 text-[11px] font-semibold text-amber-800">
                <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
                <span>部分指标未取到：{(data.warnings || []).join('；')}</span>
              </div>
            )}

            <PhotoReviewCard />
            <PhotoHistoryCard />

            <div className="grid gap-4 sm:grid-cols-2">
              <Card title="用户" hint="last_active_at 口径">
                <div className="grid grid-cols-2 gap-2">
                  <Metric label="总数" value={fmt(data.users?.total)} />
                  <Metric label="24h 活跃" value={fmt(data.users?.active_24h)} />
                  <Metric label="7 日新增" value={fmt(data.users?.new_7d)} />
                  <Metric label="其中游客" value={fmt(data.users?.guests)} tone="text-stone-600" />
                </div>
              </Card>

              <Card title="路书" hint="已保存行程">
                <div className="grid grid-cols-2 gap-2">
                  <Metric label="总数" value={fmt(data.trips?.total)} />
                  <Metric label="近 7 日" value={fmt(data.trips?.last_7d)} />
                </div>
                <p className="mt-3 text-[11px] leading-5 text-stone-500">
                  只统计已落库的行程，不统计流式生成中的中间态。
                </p>
              </Card>

              <Card title="协同房间" hint="rooms / room_members">
                <div className="grid grid-cols-3 gap-2">
                  <Metric label="房间数" value={fmt(data.rooms?.total)} />
                  <Metric label="成员关系" value={fmt(data.rooms?.members)} />
                  <Metric label="近 7 日新建" value={fmt(data.rooms?.last_7d)} />
                </div>
              </Card>

              <Card title="反馈样本" hint="FeedbackLog">
                <div className="grid grid-cols-3 gap-2">
                  <Metric label="总数" value={fmt(data.feedback?.total)} />
                  <Metric label="近 7 日" value={fmt(data.feedback?.last_7d)} />
                  <Metric
                    label="正 / 负（7日）"
                    value={`${fmt(data.feedback?.positive_7d)} / ${fmt(data.feedback?.negative_7d)}`}
                    tone="text-stone-700"
                  />
                </div>
              </Card>
            </div>

            <Card title="规划链路" hint="用户行为事件 · 近 7 天">
              <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
                <Metric label="行为事件总数" value={fmt(planningTotal)} />
                <Metric label="生成 plan_generated" value={fmt(data.planning?.generated_7d)} />
                <Metric label="采纳 plan_adopted" value={fmt(data.planning?.adopted_7d)} />
                <Metric
                  label="采纳率"
                  value={
                    typeof data.planning?.adoption_rate === 'number'
                      ? `${Math.round(data.planning.adoption_rate * 100)}%`
                      : '—'
                  }
                  tone="text-orange-600"
                />
              </div>
              {(data.planning?.by_type || []).length === 0 ? (
                <p className="mt-4 text-xs text-stone-500">最近 7 天没有规划事件记录。</p>
              ) : (
                <ul className="mt-4 space-y-2">
                  {(data.planning?.by_type || []).map((row) => (
                    <li key={row.type} className="flex items-center gap-3">
                      <span className="w-40 shrink-0 truncate font-mono text-[11px] text-stone-600" title={row.type}>
                        {row.type}
                      </span>
                      <span className="h-2 flex-1 overflow-hidden rounded-full bg-stone-100">
                        <span
                          className="block h-full rounded-full bg-orange-400/80"
                          style={{ width: `${Math.max(4, Math.round((row.count / maxEvent) * 100))}%` }}
                        />
                      </span>
                      <span className="w-12 shrink-0 text-right text-[11px] font-bold tabular-nums text-stone-700">{row.count}</span>
                    </li>
                  ))}
                </ul>
              )}
              <p className="mt-3 text-[11px] leading-5 text-stone-500">
                这里只算用户行为（一条 = 用户点一次）。采纳率的分母是同期 plan_generated；生成数为 0 时显示 0%，不代表“全部未采纳”。
                供应商探针（provider_quality）的计数单位不同，已单独列在下一张卡片，不与这里并列比较。
              </p>
            </Card>

            <Card title="供应商探针" hint="provider_quality · 一次探针 = 请求 × 品类 × 供应商">
              {!probes || (probes.total || 0) === 0 ? (
                <p className="text-xs text-stone-500">最近 7 天没有供应商探针记录。</p>
              ) : (
                <>
                  <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
                    <Metric label="探针次数" value={fmt(probes.total)} />
                    <Metric
                      label="成功率"
                      value={probeRate === undefined ? '—' : `${Math.round(probeRate * 100)}%`}
                      tone={probeRate !== undefined && probeRate < 0.5 ? 'text-rose-600' : 'text-emerald-600'}
                    />
                    <Metric label="每次生成对应" value={fmt(probes.per_generated, ' 次')} tone="text-stone-700" />
                    <Metric label="平均耗时" value={fmt(probes.avg_latency_ms, ' ms')} tone="text-stone-700" />
                  </div>

                  {(probes.failing_providers || []).length > 0 && (
                    <div className="mt-3 flex flex-wrap gap-2">
                      {(probes.failing_providers || []).map((row) => (
                        <span key={row.provider} className="rounded-full bg-rose-50 px-2.5 py-1 text-[11px] font-bold text-rose-700">
                          {row.provider} · {row.probes} 次探针零成功
                        </span>
                      ))}
                    </div>
                  )}

                  {(probes.by_kind || []).length > 0 && (
                    <ul className="mt-4 space-y-2">
                      {(probes.by_kind || []).map((row) => (
                        <li key={row.name} className="flex items-center gap-3">
                          <span className="w-24 shrink-0 truncate font-mono text-[11px] text-stone-600" title={row.name}>
                            {row.name}
                          </span>
                          <span className="h-2 flex-1 overflow-hidden rounded-full bg-stone-100">
                            <span
                              className="block h-full rounded-full bg-stone-400/70"
                              style={{ width: `${Math.max(4, Math.round((row.probes / maxProbeKind) * 100))}%` }}
                            />
                          </span>
                          <span className="w-32 shrink-0 text-right text-[11px] tabular-nums text-stone-600">
                            {row.probes} 次 · 成功 {row.ok}
                          </span>
                        </li>
                      ))}
                    </ul>
                  )}

                  <p className="mt-3 text-[11px] leading-5 text-stone-500">
                    探针次数天然高于生成数：一次点击会按品类调用多个供应商（交通还会分别探航班与火车），失败调用同样计数。
                    因此“每次生成对应 N 次探针”只反映探测深度，不代表采样频率异常；真正要盯的是成功率与零成功供应商。
                  </p>
                </>
              )}
            </Card>

            <Card title="安全事件" hint="本次进程启动以来 · 明细见日志">
              <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
                <Metric label="登录失败" value={fmt(data.security?.login_failures)} />
                <Metric label="账号锁定" value={fmt(data.security?.lockouts)} />
                <Metric label="限流拦截" value={fmt(data.security?.rate_limited)} />
                <Metric label="越权探测" value={fmt(data.security?.identity_mismatch)} />
              </div>
              <p className="mt-3 text-[11px] leading-5 text-stone-500">
                计数随网关重启归零，用来快速判断"是被扫描还是自己人输错密码"；取证仍需查服务端结构化安全日志（type=security 的 JSON 行）。
              </p>
            </Card>

            <p className="pb-6 text-center text-[11px] text-stone-400">
              OmniRoute 运维看板 · 只读 · 聚合口径，不含用户内容明细
            </p>
          </div>
        )}
      </div>
    </main>
  );
}
