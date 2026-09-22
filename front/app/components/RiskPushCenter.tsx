'use client';

import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { AnimatePresence, motion } from 'framer-motion';
import {
  Bell,
  BellRing,
  ShieldAlert,
  Siren,
  CloudRain,
  Navigation,
  AlertTriangle,
  X,
  CheckCircle2,
  Clock,
  Rss,
  BellOff,
  ExternalLink,
  ChevronDown,
  ChevronUp,
} from 'lucide-react';
import { API_BASE, apiJson } from '../lib/utils';
import { fetchRiskSnapshot } from '../lib/riskSnapshot';
import { normalizeRiskNotification, parseRiskTimestamp } from '../lib/riskNotifications';
import { enqueueRiskNotificationAction, loadRiskInboxCache, removeRiskNotificationActions, saveRiskInboxCache } from '../lib/riskNotificationCache';
import { disableWebPushSubscription, requestWebPushSubscription, webPushCapability, type WebPushState } from '../lib/webPush';

const POLL_INTERVAL_MS = 30000;

export interface RiskChange {
  type: string;
  severity: 'LOW' | 'MEDIUM' | 'HIGH';
  title: string;
  detail: string;
  replan?: { proposal_id?: string; status?: string; trip_id?: string; reason?: string; changed_signals?: unknown[] };
}

interface RiskInboxItem extends RiskChange {
  id: string;
  ts: number;
  createdAt: number;
  city?: string;
  source?: string;
  estimated: boolean;
  status: 'unread' | 'read' | 'acknowledged';
  observedAt: number;
  signalSources?: Record<string, { provider?: string; estimated?: boolean; retrieved_at?: string | number | null }>;
  replan?: RiskChange['replan'];
}

interface RiskNotificationResponse {
  data?: {
    notifications?: Array<Record<string, unknown>>;
    unread_count?: number;
  };
  notifications?: Array<Record<string, unknown>>;
  unread_count?: number;
}

interface RiskPushCenterProps {
  city?: string;
  coordinate?: [number, number] | null;
  safetyInfo?: any;
  weatherInfo?: any;
  trafficInfo?: any;
  enabled?: boolean;
  // 目的地风险订阅（P5）：需用户 ID 与房间号以持久化基线并广播变更通知
  userId?: string;
  roomCode?: string;
  onReplan?: (proposal: RiskChange['replan']) => void;
}

// 严重等级视觉元数据
const SEVERITY_META: Record<string, { badge: string; dot: string }> = {
  HIGH: { badge: 'bg-rose-500/12 text-rose-500 border-rose-400/30', dot: 'bg-rose-500' },
  MEDIUM: { badge: 'bg-amber-500/12 text-amber-500 border-amber-400/30', dot: 'bg-amber-500' },
  LOW: { badge: 'bg-sky-500/12 text-sky-500 border-sky-400/30', dot: 'bg-sky-500' },
};

// 变更类型 → 图标
function typeIcon(type: string) {
  switch (type) {
    case 'WEATHER_CHANGE':
      return CloudRain;
    case 'TRAFFIC_CHANGE':
      return Navigation;
    case 'NEW_ALERT':
      return Siren;
    case 'RISK_LEVEL_CHANGE':
      return ShieldAlert;
    case 'CII_CHANGE':
      return AlertTriangle;
    default:
      return AlertTriangle;
  }
}

function relativeTime(ts: number) {
  const diff = Date.now() - ts;
  const s = Math.round(diff / 1000);
  if (s < 5) return '刚刚';
  if (s < 60) return `${s} 秒前`;
  const m = Math.round(s / 60);
  if (m < 60) return `${m} 分钟前`;
  const h = Math.round(m / 60);
  return `${h} 小时前`;
}

export default function RiskPushCenter({
  city,
  coordinate,
  safetyInfo,
  weatherInfo,
  trafficInfo,
  enabled = true,
  userId,
  roomCode,
  onReplan,
}: RiskPushCenterProps) {
  const [items, setItems] = useState<RiskInboxItem[]>([]);
  const [open, setOpen] = useState(false);
  const [serverUnread, setServerUnread] = useState(0);
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [polling, setPolling] = useState(false);
  const [lastSync, setLastSync] = useState<number | null>(null);
  const [subscribed, setSubscribed] = useState(false);
  const [subscriptionBusy, setSubscriptionBusy] = useState(false);
  const [statusMessage, setStatusMessage] = useState('');
  const [online, setOnline] = useState(true);
  const [cacheLoaded, setCacheLoaded] = useState(false);
  const [pushState, setPushState] = useState<WebPushState>('unsupported');
  const [pushBusy, setPushBusy] = useState(false);
  const unread = useMemo(
    () => Math.max(serverUnread, items.filter((item) => item.status === 'unread').length),
    [items, serverUnread],
  );

  useEffect(() => {
    if (!userId || typeof window === 'undefined') return;
    setPushState(webPushCapability());
    const updateOnline = () => setOnline(window.navigator.onLine);
    updateOnline();
    window.addEventListener('online', updateOnline);
    window.addEventListener('offline', updateOnline);
    return () => {
      window.removeEventListener('online', updateOnline);
      window.removeEventListener('offline', updateOnline);
    };
  }, [userId]);

  useEffect(() => {
    if (!userId) return;
    let cancelled = false;
    apiJson<{ data?: { subscribed?: boolean }; subscribed?: boolean }>(`${API_BASE}/api/v1/notifications/push/status`)
      .then((response) => {
        if (cancelled) return;
        const payload = response.data || response;
        if (payload.subscribed) setPushState('subscribed');
      })
      .catch(() => undefined);
    return () => { cancelled = true; };
  }, [userId]);

  const togglePush = useCallback(async () => {
    if (!userId || pushBusy) return;
    setPushBusy(true);
    try {
      if (pushState === 'subscribed') {
        await disableWebPushSubscription();
        setPushState(webPushCapability() === 'disabled' ? 'disabled' : 'available');
        setStatusMessage('浏览器通知已关闭，风险提醒仍保留在站内');
      } else {
        const result = await requestWebPushSubscription();
        setPushState(result.state);
        setStatusMessage(result.message || '通知设置已更新');
      }
    } catch (error) {
      setStatusMessage(error instanceof Error ? error.message : '浏览器通知设置失败，仍可使用站内通知');
    } finally {
      setPushBusy(false);
    }
  }, [pushBusy, pushState, userId]);

  useEffect(() => {
    if (!userId || cacheLoaded) return;
    const cached = loadRiskInboxCache(userId);
    if (cached) {
      setServerUnread(cached.serverUnread);
      setItems(cached.items.map((item) => ({ ...item, ts: item.ts || item.createdAt, createdAt: item.createdAt || item.ts, estimated: item.estimated === true, status: item.status || 'unread', observedAt: item.observedAt || item.createdAt })));
      if (cached.savedAt && Date.now() - cached.savedAt > 24 * 60 * 60 * 1000) {
        setStatusMessage('当前显示的是超过 24 小时的离线风险缓存，联网后会自动核验');
      }
    }
    setCacheLoaded(true);
  }, [userId, cacheLoaded]);

  // 出发前基线快照：由行程生成时推送的 safety/weather/traffic 合并而来
  const baseline = useMemo(() => {
    if (!safetyInfo && !weatherInfo && !trafficInfo) return undefined;
    return {
      cii_score: safetyInfo?.cii_score,
      risk_level: safetyInfo?.risk_level,
      crime_score: safetyInfo?.crime_score,
      weather_score: safetyInfo?.weather_score,
      political_score: safetyInfo?.political_score,
      health_score: safetyInfo?.health_score,
      traffic_score: safetyInfo?.traffic_score,
      active_alerts: safetyInfo?.active_alerts || [],
      weather: weatherInfo,
      traffic: trafficInfo,
    };
  }, [safetyInfo, weatherInfo, trafficInfo]);

  const baselineRef = useRef(baseline);
  useEffect(() => {
    baselineRef.current = baseline;
  }, [baseline]);

  const appendChanges = useCallback((changes: RiskChange[]) => {
    if (changes.length === 0) return;
    const now = Date.now();
    const stamped: RiskInboxItem[] = changes.map((c, i) => ({ ...c, id: `local:${now}-${i}`, ts: now, createdAt: now, city, status: 'unread', estimated: true, observedAt: now }));
    setItems((prev) => [...stamped, ...prev].slice(0, 100));
    if (typeof Notification !== 'undefined' && Notification.permission === 'granted' && stamped[0]) {
      new Notification(`${city || '目的地'}风险提醒`, { body: `${stamped[0].title}：${stamped[0].detail}` });
    }
  }, [city]);

  const loadInbox = useCallback(async () => {
    if (!userId || (typeof navigator !== 'undefined' && !navigator.onLine)) return;
    const cached = loadRiskInboxCache(userId);
    if (cached?.pending.length) {
      const applied: string[] = [];
      for (const action of cached.pending) {
        try {
          await apiJson(`${API_BASE}/api/v1/risk/notifications/${encodeURIComponent(action.id)}/read?status=${action.status}`, { method: 'POST' });
          applied.push(action.id);
        } catch {
          // Keep failed actions queued for the next online event.
        }
      }
      if (applied.length) removeRiskNotificationActions(userId, applied);
    }
    const response = await apiJson<RiskNotificationResponse>(`${API_BASE}/api/v1/risk/notifications`);
    const payload = response.data || response;
    const persisted = (Array.isArray(payload.notifications) ? payload.notifications : [])
      .map(normalizeRiskNotification)
      .filter((item): item is NonNullable<ReturnType<typeof normalizeRiskNotification>> => item !== null)
      .map((item): RiskInboxItem => ({ ...item, ts: item.createdAt, createdAt: item.createdAt }));
    setServerUnread(Number(payload.unread_count || 0));
    setItems((previous) => {
      const local = previous.filter((item) => item.id.startsWith('local:'));
      const persistedIds = new Set(persisted.map((item) => item.id));
      const persistedContent = new Set(persisted.map((item) => `${item.city || ''}|${item.title}|${item.detail}`));
      const next = [...persisted, ...local.filter((item) => !persistedIds.has(item.id))]
        .filter((item) => !item.id.startsWith('local:') || !persistedContent.has(`${item.city || ''}|${item.title}|${item.detail}`))
        .sort((a, b) => b.ts - a.ts)
        .slice(0, 100);
      return next;
    });
    const latest = loadRiskInboxCache(userId);
    saveRiskInboxCache(userId, { serverUnread: Number(payload.unread_count || 0), items: persisted.map((item) => ({ ...item, ts: item.createdAt })), pending: latest?.pending || [] });
  }, [userId]);

  const markPersistedNotificationsRead = useCallback(async () => {
    const unreadItems = items.filter((item) => !item.id.startsWith('local:') && item.status === 'unread');
    if (unreadItems.length === 0) return;
    setItems((previous) => previous.map((item) => item.status === 'unread' ? { ...item, status: 'read' } : item));
    setServerUnread((count) => Math.max(0, count - unreadItems.length));
    await Promise.all(unreadItems.map(async (item) => {
      try {
        if (!online) throw new Error('offline');
        await apiJson(`${API_BASE}/api/v1/risk/notifications/${encodeURIComponent(item.id)}/read?status=read`, { method: 'POST' });
      } catch {
        enqueueRiskNotificationAction(userId || '', { id: item.id, status: 'read', queuedAt: Date.now() });
      }
    }));
  }, [items, online, userId]);

  const acknowledgeNotification = useCallback(async (item: RiskInboxItem) => {
    if (item.id.startsWith('local:')) {
      setItems((previous) => previous.map((entry) => entry.id === item.id ? { ...entry, status: 'acknowledged' } : entry));
      return;
    }
    try {
      if (!online) throw new Error('offline');
      await apiJson(`${API_BASE}/api/v1/risk/notifications/${encodeURIComponent(item.id)}/read?status=acknowledged`, { method: 'POST' });
      setItems((previous) => previous.map((entry) => entry.id === item.id ? { ...entry, status: 'acknowledged' } : entry));
    } catch (error) {
      enqueueRiskNotificationAction(userId || '', { id: item.id, status: 'acknowledged', queuedAt: Date.now() });
      setItems((previous) => previous.map((entry) => entry.id === item.id ? { ...entry, status: 'acknowledged' } : entry));
      setStatusMessage('当前离线，已记录“已处理”，网络恢复后自动同步');
    }
  }, [online, userId]);

  const poll = useCallback(async () => {
    if (!city) return;
    if (typeof navigator !== 'undefined' && !navigator.onLine) {
      setStatusMessage('当前网络不可用，正在显示离线风险缓存');
      setLastSync(Date.now());
      return;
    }
    setPolling(true);
    try {
      let changes: RiskChange[] = [];
      if (subscribed && userId) {
        // 已订阅：走网关订阅服务，对比服务端持久化基线，检测变更并广播房间通知
        const data = await apiJson<{ alerts?: Array<{ change?: RiskChange }> }>(`${API_BASE}/api/v1/risk/subscriptions/check`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ user_id: userId, room_id: roomCode || '' }),
        });
        const alerts = Array.isArray(data?.alerts) ? data.alerts : [];
        changes = alerts
          .map((a: any) => a?.change ? { ...a.change, replan: a.replan } : null)
          .filter((c: any) => c && typeof c === 'object');
      } else {
        // 未订阅：沿用旅中实时对比（出发前快照 vs 实时快照）。
        // 走共享读取器：雷达同时打开时两个组件共享同一次请求。
        const data = (await fetchRiskSnapshot(city, {
          coordinate,
          baseline: baselineRef.current,
        })) as { changes?: RiskChange[] } | null;
        changes = Array.isArray(data?.changes) ? data.changes : [];
      }
      appendChanges(changes);
      if (subscribed && userId) await loadInbox();
      setStatusMessage((message) => message.includes('失败') || message.includes('不可用') ? '' : message);
    } catch (error) {
      setStatusMessage(error instanceof Error ? error.message : '风险数据同步失败，请稍后重试');
    } finally {
      setPolling(false);
      setLastSync(Date.now());
    }
  }, [city, coordinate, subscribed, userId, roomCode, appendChanges, loadInbox]);

  useEffect(() => {
    if (!enabled || !userId) return;
    let cancelled = false;
    loadInbox().catch((error) => {
      if (!cancelled) setStatusMessage(error instanceof Error ? error.message : '风险通知读取失败');
    });
    return () => { cancelled = true; };
  }, [enabled, userId, loadInbox]);

  useEffect(() => {
    if (!enabled || !userId || !online) return;
    void loadInbox().catch(() => undefined);
  }, [enabled, userId, online, loadInbox]);

  useEffect(() => {
    if (!userId || !cacheLoaded) return;
    const persisted = items.map((item) => ({
      ...item,
      createdAt: item.ts,
      observedAt: item.observedAt || item.ts,
    }));
    const cached = loadRiskInboxCache(userId);
    saveRiskInboxCache(userId, { serverUnread, items: persisted, pending: cached?.pending || [] });
  }, [items, serverUnread, userId, cacheLoaded]);

  useEffect(() => {
    if (!enabled || !city) return;
    poll();
    const timer = setInterval(poll, POLL_INTERVAL_MS);
    return () => clearInterval(timer);
  }, [enabled, city, poll]);

  // 订阅状态回读：进入页面时判断当前目的地是否已订阅
  useEffect(() => {
    if (!userId || !city) return;
    let cancelled = false;
    (async () => {
      try {
        const data = await apiJson<{ subscriptions?: Array<{ city?: string }> }>(
          `${API_BASE}/api/v1/risk/subscriptions?user_id=${encodeURIComponent(userId)}`,
        );
        const list = Array.isArray(data?.subscriptions) ? data.subscriptions : [];
        if (cancelled) return;
        setSubscribed(list.some((s: any) => s?.city === city));
      } catch (error) {
        if (!cancelled) setStatusMessage(error instanceof Error ? error.message : '订阅状态读取失败');
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [userId, city]);

  const onSubscribe = useCallback(async () => {
    if (!userId || !city) return;
    setSubscriptionBusy(true);
    try {
      await apiJson(`${API_BASE}/api/v1/risk/subscribe`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ user_id: userId, city, coordinate }),
      });
      setSubscribed(true);
      setStatusMessage(`已订阅 ${city}：重大变化会在站内、浏览器通知及同行房间中提醒`);
      if (typeof Notification !== 'undefined' && Notification.permission === 'default') {
        await Notification.requestPermission();
      }
    } catch (e) {
      setStatusMessage(e instanceof Error ? e.message : '订阅失败，请稍后重试');
    } finally {
      setSubscriptionBusy(false);
    }
  }, [userId, city, coordinate]);

  const onUnsubscribe = useCallback(async () => {
    if (!userId || !city) return;
    setSubscriptionBusy(true);
    try {
      await apiJson(`${API_BASE}/api/v1/risk/unsubscribe`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ user_id: userId, city }),
      });
      setSubscribed(false);
      setStatusMessage(`已取消 ${city} 风险订阅`);
    } catch (e) {
      setStatusMessage(e instanceof Error ? e.message : '取消订阅失败');
    } finally {
      setSubscriptionBusy(false);
    }
  }, [userId, city]);

  if (!city) return null;

  const activeRisk = items.some((i) => i.severity === 'HIGH' || i.severity === 'MEDIUM');

  return (
    <>
      {/* 悬浮推送按钮 */}
      <div className="absolute bottom-24 right-6 z-40 flex items-center gap-3">
        <AnimatePresence>
          {unread > 0 && !open && (
            <motion.div
              key="latest-bubble"
              initial={{ opacity: 0, x: 12, scale: 0.9 }}
              animate={{ opacity: 1, x: 0, scale: 1 }}
              exit={{ opacity: 0, x: 12, scale: 0.9 }}
              className="bg-white/95 backdrop-blur-md border border-slate-200/80 shadow-xl rounded-xl px-3 py-2 max-w-[240px]"
            >
              {items[0] && (
                <div className="flex items-start gap-2">
                  {(() => {
                    const Icon = typeIcon(items[0].type);
                    const meta = SEVERITY_META[items[0].severity] || SEVERITY_META.LOW;
                    return <Icon className="w-4 h-4 mt-0.5 shrink-0 text-slate-600" />;
                  })()}
                  <div className="min-w-0">
                    <p className="text-xs font-bold text-slate-800 leading-tight truncate">{items[0].title}</p>
                    <p className="text-[10px] text-slate-500 leading-snug mt-0.5 line-clamp-2">{items[0].detail}</p>
                  </div>
                </div>
              )}
            </motion.div>
          )}
        </AnimatePresence>

        <motion.button
          onClick={() => {
            const nextOpen = !open;
            setOpen(nextOpen);
            if (nextOpen) {
              void markPersistedNotificationsRead();
              setItems((previous) => previous.map((item) => item.id.startsWith('local:') && item.status === 'unread' ? { ...item, status: 'read' } : item));
            }
          }}
          whileTap={{ scale: 0.92 }}
          className={`relative p-3 rounded-2xl shadow-xl border transition-all cursor-pointer ${
            activeRisk
              ? 'bg-gradient-to-br from-rose-500 to-orange-500 text-white border-rose-400/40'
              : 'bg-white/95 backdrop-blur-md text-slate-700 border-slate-200/80 hover:text-orange-500'
          }`}
          title="旅中风险推送中心"
        >
          {activeRisk && <span className="absolute inset-0 rounded-2xl bg-rose-500/40 animate-ping opacity-30" />}
          {activeRisk ? <BellRing className="w-5 h-5 relative" /> : <Bell className="w-5 h-5 relative" />}
          {unread > 0 && (
            <span className="absolute -top-1.5 -right-1.5 min-w-[18px] h-[18px] px-1 rounded-full bg-rose-500 text-white text-[10px] font-black flex items-center justify-center border-2 border-white">
              {unread > 99 ? '99+' : unread}
            </span>
          )}
        </motion.button>
      </div>

      {/* 推送中心面板 */}
      <AnimatePresence>
        {open && (
          <motion.div
            key="risk-push-panel"
            initial={{ opacity: 0, y: 16, scale: 0.96 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: 16, scale: 0.96 }}
            transition={{ type: 'spring', damping: 24 }}
            className="absolute bottom-44 right-6 z-40 w-[340px] max-w-[calc(100vw-48px)] bg-white/95 backdrop-blur-2xl rounded-2xl shadow-2xl border border-slate-200/80 overflow-hidden flex flex-col"
          >
            {/* 头部 */}
            <div className="px-4 py-3 border-b border-slate-100 flex items-center justify-between bg-slate-50/80">
              <div className="flex items-center gap-2">
                <ShieldAlert className={`w-4 h-4 ${activeRisk ? 'text-rose-500' : 'text-emerald-500'}`} />
                <div>
                  <h4 className="text-sm font-black text-slate-800 leading-none">旅中风险推送中心</h4>
              <p className="text-[10px] text-slate-400 font-medium mt-0.5 flex items-center gap-1">
                    {!online && <><span className="w-1.5 h-1.5 rounded-full bg-amber-500" />离线缓存模式 · </>}
                    {polling ? (
                      <>
                        <span className="w-1.5 h-1.5 rounded-full bg-emerald-500 animate-pulse" />
                        正在评估 {city} 实时风险…
                      </>
                    ) : (
                      <>
                        <Clock className="w-3 h-3" />
                        {lastSync ? `最近同步 ${relativeTime(lastSync)}` : '等待首次同步'}
                      </>
                    )}
                  </p>
                </div>
              </div>
              <div className="flex items-center gap-1">
                {userId && (
                  <button
                    onClick={subscribed ? onUnsubscribe : onSubscribe}
                    disabled={subscriptionBusy}
                    className={`p-1.5 rounded-lg cursor-pointer transition-colors disabled:opacity-50 flex items-center gap-1 text-[10px] font-bold ${
                      subscribed
                        ? 'text-emerald-600 hover:bg-emerald-50'
                        : 'text-sky-600 hover:bg-sky-50'
                    }`}
                    title={subscribed ? '取消目的地风险订阅' : '订阅目的地风险监控'}
                  >
                    {subscribed ? <BellOff className="w-3.5 h-3.5" /> : <Rss className="w-3.5 h-3.5" />}
                    {subscribed ? '已订阅' : '订阅'}
                  </button>
                )}
                {userId && <button type="button" onClick={() => void togglePush()} disabled={pushBusy || pushState === 'unsupported' || pushState === 'disabled'} className={`rounded-lg px-2 py-1 text-[10px] font-bold ${pushState === 'subscribed' ? 'text-emerald-700 hover:bg-emerald-50' : 'text-slate-500 hover:bg-slate-100'} disabled:cursor-not-allowed disabled:opacity-50`} title={pushState === 'subscribed' ? '关闭浏览器通知' : '开启浏览器通知'}>{pushBusy ? '处理中…' : pushState === 'subscribed' ? '浏览器通知已开' : pushState === 'permission-denied' ? '权限已拒绝' : pushState === 'disabled' ? '站内通知' : '开启浏览器通知'}</button>}
                <button
                  onClick={() => setOpen(false)}
                  className="p-1.5 hover:bg-slate-200 rounded-lg text-slate-400 cursor-pointer"
                >
                  <X className="w-4 h-4" />
                </button>
              </div>
            </div>

            {/* 列表 */}
            <div className="max-h-[320px] overflow-y-auto custom-scrollbar">
              {statusMessage && <div className="border-b border-sky-100 bg-sky-50 px-4 py-2 text-[11px] font-bold leading-relaxed text-sky-700">{statusMessage}</div>}
              {items.length === 0 ? (
                <div className="py-10 flex flex-col items-center gap-2 text-center">
                  <CheckCircle2 className="w-8 h-8 text-emerald-500" />
                  <p className="text-sm font-bold text-slate-700">旅途平安，暂无风险变更</p>
                  <p className="text-xs text-slate-400">系统每 {Math.round(POLL_INTERVAL_MS / 1000)} 秒自动评估一次天气、路况与安全情报。</p>
                </div>
              ) : (
                items.map((it) => {
                  const Icon = typeIcon(it.type);
                  const meta = SEVERITY_META[it.severity] || SEVERITY_META.LOW;
                  const expanded = expandedId === it.id;
                  const sourceLabel = it.source || '风险聚合服务';
                  const cityQuery = encodeURIComponent(it.city || city);
                  const observedLabel = it.observedAt ? new Date(it.observedAt).toLocaleString('zh-CN', { hour12: false }) : '时间未知';
                  return (
                    <div key={it.id} className={`px-4 py-3 border-b border-slate-50 flex items-start gap-3 hover:bg-slate-50/60 ${it.status === 'acknowledged' ? 'opacity-70' : ''}`}>
                      <div className={`mt-0.5 p-1.5 rounded-lg border shrink-0 ${meta.badge}`}>
                        <Icon className="w-4 h-4" />
                      </div>
                      <div className="min-w-0 flex-1">
                        <div className="flex items-center justify-between gap-2">
                          <p className="text-xs font-bold text-slate-800 leading-tight">{it.title}</p>
                          <span className="text-[9px] text-slate-400 shrink-0 font-medium">{relativeTime(it.ts)}</span>
                        </div>
                        <p className="text-[11px] text-slate-500 leading-snug mt-0.5">{it.detail}</p>
                        <div className="mt-1.5 flex flex-wrap items-center gap-x-2 gap-y-1 text-[9px] text-slate-400">
                          <span>来源：{sourceLabel}</span>
                          <span>{it.estimated ? '估算信号' : '供应商信号'}</span>
                          <span>观测：{observedLabel}</span>
                          <button type="button" onClick={() => setExpandedId(expanded ? null : it.id)} className="inline-flex items-center gap-0.5 font-bold text-slate-500 hover:text-sky-600">
                            {expanded ? '收起证据' : '查看证据'} {expanded ? <ChevronUp className="h-3 w-3" /> : <ChevronDown className="h-3 w-3" />}
                          </button>
                        </div>
                        {expanded && (
                          <div className="mt-2 rounded-lg border border-slate-100 bg-slate-50/80 p-2 text-[10px] leading-relaxed text-slate-500">
                            <p>本条提醒来自已保存的风险快照，页面不会把估算数据当成实时事实。</p>
                            {it.signalSources && Object.entries(it.signalSources).map(([key, signal]) => (
                              <p key={key}>{key}：{signal.provider || '未提供'} · {signal.estimated ? '估算' : '实测'}{signal.retrieved_at ? ` · ${new Date(parseRiskTimestamp(signal.retrieved_at)).toLocaleString('zh-CN', { hour12: false })}` : ''}</p>
                            ))}
                            <div className="mt-1.5 flex flex-wrap gap-2">
                              <a href={`https://www.amap.com/search?query=${cityQuery}`} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 font-bold text-sky-700 hover:text-sky-900"><ExternalLink className="h-3 w-3" />高德查看目的地</a>
                              <a href={`https://ditu.amap.com/dir?city=${cityQuery}`} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 font-bold text-sky-700 hover:text-sky-900"><ExternalLink className="h-3 w-3" />高德规划路线</a>
                              {it.status !== 'acknowledged' && <button type="button" onClick={() => void acknowledgeNotification(it)} className="font-bold text-emerald-700 hover:text-emerald-900">标记已处理</button>}
                            </div>
                          </div>
                        )}
                        {it.replan && onReplan && (
                          <button type="button" onClick={() => onReplan(it.replan)} className="mt-2 inline-flex min-h-7 items-center rounded-md bg-orange-50 px-2.5 text-[10px] font-bold text-orange-700 hover:bg-orange-100">查看并确认重排提案</button>
                        )}
                      </div>
                      <span className={`w-1.5 h-1.5 rounded-full shrink-0 mt-1.5 ${meta.dot}`} />
                    </div>
                  );
                })
              )}
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </>
  );
}
