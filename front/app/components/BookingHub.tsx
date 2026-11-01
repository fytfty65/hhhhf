'use client';

import Link from 'next/link';
import { useEffect, useRef, useState } from 'react';
import { Ban, ExternalLink, Hotel, Info, Plane, RefreshCw, ShieldCheck, ShoppingCart, Ticket, Train, X } from 'lucide-react';
import { apiJson } from '../lib/apiClient';
import { useFocusTrap } from '../lib/useFocusTrap';

type ProviderStatus = {
  configured?: boolean;
  authenticated?: boolean;
  provider?: string;
  endpoint_count?: number;
};

type OrderRecord = {
  order_id: string;
  provider_order_id?: string;
  status: string;
  provider?: string;
  order_type?: string;
  trip_id?: string;
  created_at?: string;
};

type BookingHubProps = {
  open: boolean;
  destination?: string;
  origin?: string;
  date?: string;
  tripId?: string;
  /** 普通模式：隐藏供应商/内部术语，只保留用户能理解的预订表达 */
  simpleMode?: boolean;
  onClose: () => void;
};

// 官方渠道是「导流」：链接尽量直达目的地/景区的具体检索页，而不是统一跳地图首页。
// 酒店/门票在高德上按目的地检索，用户再进具体门店或景区页面完成交易。
const officialChannelsFor = (destination: string) => {
  const city = destination.trim();
  const hotelQuery = encodeURIComponent(city ? `${city}酒店` : '酒店');
  const ticketQuery = encodeURIComponent(city ? `${city}景区门票` : '景区门票');
  const cityParam = city ? `&city=${encodeURIComponent(city)}` : '';
  return [
    { key: 'train', label: '铁路票', icon: Train, href: 'https://kyfw.12306.cn/otn/leftTicket/init', note: '12306 官方购票，价格和余票以官方页面为准', short: '12306' },
    { key: 'flight', label: '航班', icon: Plane, href: 'https://www.csair.com/cn/', note: '前往航空公司官网查询与购票', short: '航空公司官网' },
    { key: 'hotel', label: '住宿', icon: Hotel, href: `https://www.amap.com/search?query=${hotelQuery}${cityParam}`, note: city ? `先看${city}具体酒店的位置，再进酒店官方页面预订` : '先按目的地检索酒店，再进酒店官方页面预订', short: '地图检索·酒店' },
    { key: 'ticket', label: '门票', icon: Ticket, href: `https://www.amap.com/search?query=${ticketQuery}${cityParam}`, note: city ? `按${city}景区检索，到景区官方售票页核验后购票` : '按景区检索，到景区官方售票页核验后购票', short: '地图检索·景区' },
  ];
};

const orderTypes = [
  { value: 'TRANSPORT', label: '交通' },
  { value: 'LODGING', label: '住宿' },
  { value: 'SCENIC', label: '门票' },
  { value: 'DINING', label: '餐饮' },
];

type OrderFieldDef = {
  key: string;
  label: string;
  type?: 'text' | 'date' | 'number';
  placeholder?: string;
  required?: boolean;
};

// 每个品类的结构化下单字段：用户填这些，前端自己拼成请求，不再让用户写 JSON。
const orderFieldDefs: Record<string, OrderFieldDef[]> = {
  TRANSPORT: [
    { key: 'depart_station', label: '出发地/车站', required: true, placeholder: '例如 北京南' },
    { key: 'arrive_station', label: '目的地/车站', required: true, placeholder: '例如 上海虹桥' },
    { key: 'depart_date', label: '出发日期', type: 'date', required: true },
    { key: 'train_no', label: '车次/航班号', placeholder: '例如 G1 / CA1234' },
    { key: 'passenger_name', label: '乘车/乘机人', placeholder: '与证件一致' },
  ],
  LODGING: [
    { key: 'hotel_name', label: '酒店名称', required: true, placeholder: '与预订页面一致' },
    { key: 'check_in', label: '入住日期', type: 'date', required: true },
    { key: 'check_out', label: '离店日期', type: 'date', required: true },
    { key: 'rooms', label: '房间数', type: 'number', placeholder: '1' },
  ],
  SCENIC: [
    { key: 'scenic_name', label: '景点名称', required: true, placeholder: '与官方售票页一致' },
    { key: 'visit_date', label: '游玩日期', type: 'date', required: true },
    { key: 'quantity', label: '人数/票数', type: 'number', placeholder: '1' },
  ],
  DINING: [
    { key: 'restaurant_name', label: '餐厅名称', required: true, placeholder: '与餐厅页面一致' },
    { key: 'reserve_date', label: '用餐日期', type: 'date', required: true },
    { key: 'people', label: '人数', type: 'number', placeholder: '2' },
  ],
};

const providerHealthKeys: Record<string, string> = {
  TRANSPORT: 'transport',
  LODGING: 'lodging',
  SCENIC: 'scenic',
  DINING: 'dining',
};

const orderErrorLabels: Record<string, string> = {
  PROVIDER_NOT_CONFIGURED: '该品类暂不支持平台内下单，请使用上方官方渠道完成交易',
  PROVIDER_TOOLS_UNAVAILABLE: '预订平台暂时不可用，请稍后重试或改用官方渠道',
  ORDER_TOOL_UNAVAILABLE: '该预订平台未提供下单能力，请改用官方渠道',
  ORDER_FIELDS_REQUIRED: '下单信息不完整，请补充上方必填项',
  ORDER_PROVIDER_FAILED: '预订平台下单失败，请稍后重试或改用官方渠道',
  ORDER_ID_MISSING: '预订平台未返回订单号，本次下单不能视为成功',
  ORDER_PERSIST_FAILED: '订单已提交但本地记录失败，请勿重复下单，先联系客服核对',
  CANCEL_TOOL_UNAVAILABLE: '该预订平台未提供取消能力，请联系平台客服',
  CANCEL_PROVIDER_FAILED: '取消失败，请稍后重试或联系平台客服',
  CANCEL_FIELDS_REQUIRED: '取消信息不完整，请联系平台客服',
  IDEMPOTENCY_KEY_REQUIRED: '缺少幂等键，请重新打开预订面板后再试',
  ORDER_CONFIRMATION_REQUIRED: '下单必须显式确认',
  TRIP_ID_REQUIRED: '下单需要已保存的行程，请先在行程页保存方案',
  ORDER_NOT_FOUND: '订单不存在或不属于当前账号',
  DATABASE_UNAVAILABLE: '数据服务暂不可用，请稍后重试',
};

function orderErrorText(error: unknown) {
  const code = String((error as any)?.code || '');
  if (orderErrorLabels[code]) return { code, message: orderErrorLabels[code] };
  return { code, message: error instanceof Error ? error.message : '订单操作失败' };
}

function newIdempotencyKey() {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') return crypto.randomUUID();
  return `omni-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
}

// 切换品类或重新打开时，把目的地/日期带进对应字段，少让用户重复输入。
function defaultOrderFields(kind: string, destination?: string, date?: string): Record<string, string> {
  const fields: Record<string, string> = {};
  for (const def of orderFieldDefs[kind] || []) fields[def.key] = '';
  if (kind === 'TRANSPORT') {
    if (destination) fields.arrive_station = destination;
    if (date) fields.depart_date = date;
  }
  if (kind === 'LODGING' && date) fields.check_in = date;
  if (kind === 'SCENIC' && date) fields.visit_date = date;
  if (kind === 'DINING' && date) fields.reserve_date = date;
  return fields;
}

export default function BookingHub({ open, destination, origin, date, tripId, simpleMode = true, onClose }: BookingHubProps) {
  const [health, setHealth] = useState<Record<string, ProviderStatus> | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  const [orderType, setOrderType] = useState('TRANSPORT');
  const [providerName, setProviderName] = useState('');
  const [orderFields, setOrderFields] = useState<Record<string, string>>(() => defaultOrderFields('TRANSPORT', destination, date));
  const [amountYuan, setAmountYuan] = useState('');
  const [confirmed, setConfirmed] = useState(false);
  const [orderBusy, setOrderBusy] = useState(false);
  const [orderError, setOrderError] = useState<{ code: string; message: string } | null>(null);
  const [orders, setOrders] = useState<OrderRecord[]>([]);
  const [ordersLoading, setOrdersLoading] = useState(false);
  const [orderListError, setOrderListError] = useState('');
  const [cancellingId, setCancellingId] = useState('');
  // 点击官方渠道后明确告诉你“已经打开了什么”，避免用户不知道有没有跳转成功。
  const [openedChannel, setOpenedChannel] = useState<{ key: string; label: string; href: string; short: string } | null>(null);
  const idempotencyKeyRef = useRef('');
  // 弹窗打开时把焦点锁在预订面板内（关闭时组件直接返回 null，此处必须无条件调用）
  const dialogRef = useFocusTrap<HTMLElement>(open);

  // 只有明确读到“未配置”才阻止提交；健康检查失败（health 为 null 或该品类缺失）保持可尝试，
  // 由网关返回真实的 PROVIDER_NOT_CONFIGURED，不在前端臆断供应商状态。
  const providerStatus = health?.[providerHealthKeys[orderType] || 'transport'];
  const providerConfigured = health === null ? null : providerStatus ? Boolean(providerStatus.configured) : null;
  const orderBlocked = providerConfigured === false;

  const fieldDefs = orderFieldDefs[orderType] || [];
  const providerOptions = providerStatus?.provider ? [providerStatus.provider] : [];
  const filledFields = fieldDefs
    .map((def) => ({ label: def.label, value: String(orderFields[def.key] ?? '').trim() }))
    .filter((item) => item.value);
  const amountText = amountYuan.trim() ? `¥${amountYuan.trim()}` : '以预订平台实时报价为准';

  const trackChannel = async (channel: string, targetURL: string) => {
    try {
      await apiJson(`/api/v1/planning/channels/click`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ channel, destination: destination || '', target_url: targetURL }),
      });
    } catch {
      // Channel navigation must remain usable when analytics is unavailable.
    }
  };

  const loadHealth = async () => {
    setLoading(true);
    setError('');
    try {
      const payload = await apiJson<{ data?: { providers?: Record<string, ProviderStatus> }; providers?: Record<string, ProviderStatus> }>(
        `/api/v1/planning/providers/health`,
      );
      setHealth(payload.data?.providers || payload.providers || null);
    } catch (e) {
      setError(e instanceof Error ? e.message : '预订服务状态暂时不可用');
    } finally {
      setLoading(false);
    }
  };

  const loadOrders = async () => {
    setOrdersLoading(true);
    setOrderListError('');
    try {
      const payload = await apiJson<any>(`/api/v1/planning/orders`);
      const list = payload?.orders || payload?.data?.orders;
      setOrders(Array.isArray(list) ? list : []);
    } catch (e) {
      setOrderListError(orderErrorText(e).message);
    } finally {
      setOrdersLoading(false);
    }
  };

  useEffect(() => {
    if (open) {
      setOrderFields(defaultOrderFields(orderType, destination, date));
      setProviderName('');
      setAmountYuan('');
      setConfirmed(false);
      setOpenedChannel(null);
      void loadHealth();
      void loadOrders();
    }
  }, [open]);

  const changeOrderType = (next: string) => {
    setOrderType(next);
    setOrderFields(defaultOrderFields(next, destination, date));
    setProviderName('');
    setAmountYuan('');
    setConfirmed(false);
    idempotencyKeyRef.current = '';
  };

  const submitOrder = async () => {
    if (!tripId) {
      setOrderError({ code: 'TRIP_ID_REQUIRED', message: orderErrorLabels.TRIP_ID_REQUIRED });
      return;
    }
    if (!confirmed) {
      setOrderError({ code: 'ORDER_CONFIRMATION_REQUIRED', message: '请先勾选确认，再提交下单' });
      return;
    }
    if (orderBlocked) {
      setOrderError({ code: 'PROVIDER_NOT_CONFIGURED', message: orderErrorLabels.PROVIDER_NOT_CONFIGURED });
      return;
    }
    const missing = fieldDefs.filter((def) => def.required && !String(orderFields[def.key] ?? '').trim());
    if (missing.length) {
      setOrderError({
        code: 'ORDER_FIELDS_REQUIRED',
        message: `${orderErrorLabels.ORDER_FIELDS_REQUIRED}：${missing.map((def) => def.label).join('、')}`,
      });
      return;
    }
    if (amountYuan.trim()) {
      const parsedAmount = Number(amountYuan.trim());
      if (!Number.isFinite(parsedAmount) || parsedAmount < 0) {
        setOrderError({ code: 'ORDER_AMOUNT_INVALID', message: '订单金额请填写 0 或更大的数字，留空则以下单结果为准' });
        return;
      }
    }
    const fields: Record<string, unknown> = {};
    for (const def of fieldDefs) {
      const raw = String(orderFields[def.key] ?? '').trim();
      if (!raw) continue;
      fields[def.key] = def.type === 'number' ? Number(raw) : raw;
    }
    const key = idempotencyKeyRef.current || newIdempotencyKey();
    idempotencyKeyRef.current = key;
    setOrderBusy(true);
    setOrderError(null);
    try {
      const payload = await apiJson<any>(`/api/v1/planning/orders`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'Idempotency-Key': key },
        body: JSON.stringify({
          trip_id: tripId,
          destination: destination || '',
          order_type: orderType,
          provider: providerName.trim() || undefined,
          confirm: true,
          fields,
          amount: amountYuan.trim() ? Number(amountYuan.trim()) : undefined,
          currency: 'CNY',
        }),
      });
      const order: OrderRecord | undefined = payload?.order_id ? payload : payload?.data?.order_id ? payload.data : undefined;
      if (!order) {
        setOrderError({ code: 'INVALID_RESPONSE', message: '订单返回结果无法识别，请勿重复下单，先核对订单记录' });
        return;
      }
      setOrders((previous) => [order, ...previous.filter((item) => item.order_id !== order.order_id)]);
      setConfirmed(false);
      setOrderFields(defaultOrderFields(orderType, destination, date));
      setAmountYuan('');
      idempotencyKeyRef.current = '';
    } catch (e) {
      // Keep the same idempotency key so a retry cannot create a duplicate order.
      setOrderError(orderErrorText(e));
    } finally {
      setOrderBusy(false);
    }
  };

  const cancelOrder = async (order: OrderRecord) => {
    if (!window.confirm(`确认取消订单 ${order.order_id}？取消会调用预订平台接口，是否产生退改费用以平台规则为准。`)) return;
    setCancellingId(order.order_id);
    setOrderError(null);
    try {
      const payload = await apiJson<any>(`/api/v1/planning/orders/${encodeURIComponent(order.order_id)}`, { method: 'DELETE' });
      const updated: OrderRecord | undefined = payload?.order_id ? payload : payload?.data?.order_id ? payload.data : undefined;
      setOrders((previous) => previous.map((item) => (item.order_id === order.order_id ? { ...item, ...(updated || {}), status: updated?.status || 'cancelled' } : item)));
    } catch (e) {
      setOrderError(orderErrorText(e));
    } finally {
      setCancellingId('');
    }
  };

  if (!open) return null;
  const query = [origin && `from=${encodeURIComponent(origin)}`, destination && `to=${encodeURIComponent(destination)}`, date && `date=${encodeURIComponent(date)}`].filter(Boolean).join('&');

  return (
    <div className="fixed inset-0 z-[160] flex items-center justify-center bg-slate-950/50 p-4 backdrop-blur-sm">
      <button type="button" aria-label="关闭弹窗" onClick={onClose} className="absolute inset-0 cursor-default" />
      <section ref={dialogRef} role="dialog" aria-modal="true" aria-labelledby="booking-hub-title" className="relative z-10 max-h-[90vh] w-full max-w-2xl overflow-y-auto rounded-2xl border border-slate-200 bg-white shadow-2xl">
        <header className="flex items-center justify-between border-b border-slate-100 bg-slate-50 px-5 py-4">
          <div className="flex items-center gap-3"><div className="flex h-10 w-10 items-center justify-center rounded-xl bg-orange-500 text-white"><Ticket className="h-5 w-5" /></div><div><h2 id="booking-hub-title" className="text-base font-black text-slate-900">预订与官方渠道</h2><p className="text-xs text-slate-500">OmniRoute 负责决策与导流，交易将在第三方完成</p></div></div>
          <button onClick={onClose} className="flex h-8 w-8 items-center justify-center rounded-lg text-slate-400 hover:bg-slate-200" aria-label="关闭"><X className="h-4 w-4" /></button>
        </header>
        <div className="space-y-5 p-5">
          <div className="rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-xs leading-5 text-amber-900"><Info className="mr-1 inline h-3.5 w-3.5" />当前账号无企业预订资质，平台不会收集支付信息或承诺出票。请在官方页面核验价格、余票、退改规则后完成交易。<Link href="/capabilities" className="ml-1 font-bold underline underline-offset-2 hover:text-amber-700">查看能力与数据来源</Link></div>

          {openedChannel && (
            <div data-testid="booking-jump-status" className="rounded-xl border border-emerald-200 bg-emerald-50 px-4 py-3 text-xs leading-5 text-emerald-900">
              已在浏览器打开「{openedChannel.label}」（{openedChannel.short}）。若页面没有自动跳转，请
              <a
                href={openedChannel.href}
                target="_blank"
                rel="noopener noreferrer"
                onClick={() => { void trackChannel(openedChannel.key, openedChannel.href); }}
                className="mx-1 font-bold underline underline-offset-2 hover:text-emerald-700"
              >
                点此重试
              </a>
              ，并在官方页面核对价格与余票。
            </div>
          )}

          <div>
            <h3 className="flex items-center gap-2 text-sm font-black text-slate-700"><ExternalLink className="h-4 w-4 text-orange-600" />官方渠道导流（在第三方页面完成交易）</h3>
            <p className="mt-1 text-xs leading-5 text-slate-500">点开会跳转到官方或第三方页面，本站不代收款项、也不承诺出票；价格与余票以对方页面为准。</p>
          </div>
          <div className="grid gap-3 sm:grid-cols-2">
            {officialChannelsFor(destination).map(({ key, label, icon: Icon, href, note, short }) => {
              const status = health?.[key === 'hotel' ? 'lodging' : key === 'ticket' ? 'scenic' : 'transport'];
              const target = key === 'train' && query ? `${href}?${query}` : href;
              return <a key={key} href={target} target="_blank" rel="noopener noreferrer" onClick={() => { setOpenedChannel({ key, label, href: target, short }); void trackChannel(key, target); }} className="group rounded-xl border border-slate-200 bg-white p-4 transition hover:border-orange-300 hover:bg-orange-50/40"><div className="flex items-center justify-between"><span className="flex items-center gap-2 text-sm font-black text-slate-800"><Icon className="h-4 w-4 text-orange-500" />{label}</span><ExternalLink className="h-4 w-4 text-slate-400 transition group-hover:text-orange-600" /></div><p className="mt-2 text-xs leading-5 text-slate-500">{note}</p><p className="mt-2 text-xs font-bold text-slate-400">{status?.configured ? (simpleMode ? '官方入口可用 · 预订平台已配置' : `已配置 · ${status.provider || '预订平台'} · 需在对方页面确认可用`) : '官方入口可用'}</p></a>;
            })}
          </div>

          <div className="rounded-xl border border-slate-200 bg-white p-4" data-testid="platform-order">
            <div className="flex items-start justify-between gap-2">
              <div><h3 className="flex items-center gap-2 text-sm font-black text-slate-700"><ShoppingCart className="h-4 w-4 text-orange-600" />平台内下单（需要已接入的预订平台）</h3><p className="mt-1 text-xs leading-5 text-slate-500">填写信息 → 核对价格 → 确认下单。只有真正接入并确认可用的预订平台才会出现在下拉里；否则请使用上方官方渠道。价格与库存以预订平台返回为准，不会收集支付信息。</p></div>
            </div>
            {!tripId && <p className="mt-2 rounded-md border border-amber-200 bg-amber-50 px-2.5 py-1.5 text-xs text-amber-800">当前还没有已保存的行程，平台内下单不可用；请先在工作区保存方案，或使用上方官方渠道。</p>}
            {orderBlocked && <p className="mt-2 rounded-md border border-amber-200 bg-amber-50 px-2.5 py-1.5 text-xs text-amber-800">当前「{orderTypes.find((item) => item.value === orderType)?.label || orderType}」暂不支持平台内下单，请使用上方官方渠道。</p>}
            <div className="mt-3 grid gap-2 sm:grid-cols-2">
              <label className="text-xs font-bold text-slate-600">品类
                <select data-testid="order-type" value={orderType} onChange={(event) => changeOrderType(event.target.value)} className="mt-1 block h-9 w-full rounded-lg border border-slate-300 px-2 text-sm font-normal text-slate-800">
                  {orderTypes.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}
                </select>
              </label>
              <label className="text-xs font-bold text-slate-600">预订平台
                <select data-testid="order-provider" value={providerName} onChange={(event) => { setProviderName(event.target.value); idempotencyKeyRef.current = ''; }} disabled={!providerOptions.length} className="mt-1 block h-9 w-full rounded-lg border border-slate-300 px-2 text-sm font-normal text-slate-800 disabled:bg-slate-50 disabled:text-slate-500">
                  <option value="">由平台选择</option>
                  {providerOptions.map((name) => <option key={name} value={name}>{name}</option>)}
                </select>
              </label>
            </div>
            <div className="mt-2 grid gap-2 sm:grid-cols-2" data-testid="order-fields">
              {fieldDefs.map((def) => (
                <label key={def.key} className="text-xs font-bold text-slate-600">{def.label}{def.required ? <span className="text-rose-500"> *</span> : null}
                  <input
                    type={def.type === 'number' ? 'number' : def.type === 'date' ? 'date' : 'text'}
                    inputMode={def.type === 'number' ? 'numeric' : undefined}
                    min={def.type === 'number' ? '0' : undefined}
                    value={orderFields[def.key] ?? ''}
                    placeholder={def.placeholder}
                    onChange={(event) => { setOrderFields((previous) => ({ ...previous, [def.key]: event.target.value })); idempotencyKeyRef.current = ''; }}
                    className="mt-1 block h-9 w-full rounded-lg border border-slate-300 px-2 text-sm font-normal text-slate-800"
                  />
                </label>
              ))}
            </div>
            <label className="mt-2 block text-xs font-bold text-slate-600">订单金额（元，可留空）
              <input
                type="number"
                min="0"
                step="0.01"
                value={amountYuan}
                placeholder="留空则以预订平台报价为准"
                onChange={(event) => { setAmountYuan(event.target.value); idempotencyKeyRef.current = ''; }}
                className="mt-1 block h-9 w-full rounded-lg border border-slate-300 px-2 text-sm font-normal text-slate-800"
              />
            </label>

            <div className="mt-3 rounded-lg border border-slate-200 bg-slate-50 px-3 py-2" data-testid="order-summary">
              <p className="text-xs font-bold text-slate-700">下单信息确认</p>
              {filledFields.length ? (
                <ul className="mt-1 grid gap-0.5 text-xs text-slate-600 sm:grid-cols-2">
                  {filledFields.map((item) => <li key={item.label}>{item.label}：<span className="font-medium text-slate-800">{item.value}</span></li>)}
                </ul>
              ) : (
                <p className="mt-1 text-xs text-slate-500">还没有填写下单信息，请补充上方必填项。</p>
              )}
              <p className="mt-1.5 text-xs font-bold text-slate-700">价格：{amountText}</p>
              <p className="text-[11px] leading-4 text-slate-500">页面展示的路线价可能是估算价；提交后以预订平台返回的实时报价与库存为准。</p>
            </div>

            <label className="mt-2 flex items-start gap-2 text-xs text-slate-700"><input type="checkbox" checked={confirmed} onChange={(event) => setConfirmed(event.target.checked)} className="mt-0.5 h-4 w-4 rounded border-slate-300" /><span>我已核对下单信息与价格口径，确认调用预订平台的真实下单接口。</span></label>
            <div className="mt-3 flex flex-wrap items-center gap-2">
              <button type="button" onClick={() => void submitOrder()} disabled={orderBusy || !tripId || !confirmed || orderBlocked} className="inline-flex min-h-9 items-center gap-1.5 rounded-lg bg-orange-600 px-3 text-xs font-bold text-white transition-colors hover:bg-orange-700 disabled:opacity-50">{orderBusy ? '提交中' : '确认下单'}</button>
              <span className="text-[11px] text-slate-400">{simpleMode ? '重复提交不会重复下单。' : '幂等键仅在本次提交内复用，重试不会重复下单。'}</span>
            </div>
            {orderError && <p role="alert" className="mt-2 rounded-md border border-rose-200 bg-rose-50 px-2.5 py-2 text-xs leading-5 text-rose-700">{orderError.message}{!simpleMode && orderError.code ? <span className="ml-1 text-rose-400">({orderError.code})</span> : null}</p>}
            {orderListError && <p role="alert" className="mt-2 rounded-md border border-amber-200 bg-amber-50 px-2.5 py-1.5 text-xs text-amber-800">订单记录读取失败：{orderListError}</p>}
            {(orders.length > 0 || orderListError) && <div className="mt-3 space-y-2" data-testid="platform-orders">
              <div className="flex items-center justify-between">
                <p className="text-xs font-bold text-slate-600">订单记录（最近 50 条）</p>
                <button type="button" onClick={() => void loadOrders()} disabled={ordersLoading} className="inline-flex items-center gap-1 text-[11px] font-bold text-slate-500 hover:text-slate-800 disabled:opacity-50"><RefreshCw className={`h-3 w-3 ${ordersLoading ? 'animate-spin' : ''}`} />刷新</button>
              </div>
              {orders.map((order) => <div key={order.order_id} className="rounded-lg border border-slate-200 bg-slate-50 px-3 py-2 text-xs text-slate-700">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <span className="font-bold text-slate-800">{order.provider || '平台未标注'} · {orderTypes.find((item) => item.value === order.order_type)?.label || order.order_type}</span>
                  <span className={order.status === 'cancelled' ? 'font-bold text-slate-400' : order.status === 'failed' ? 'font-bold text-rose-600' : 'font-bold text-emerald-700'}>{order.status === 'cancelled' ? '已取消' : order.status === 'failed' ? '失败' : order.status === 'pending' ? '处理中' : order.status === 'created' ? '已创建' : order.status}</span>
                </div>
                <p className="mt-1 text-slate-500">订单号 {order.order_id}{order.provider_order_id ? ` · 平台单号 ${order.provider_order_id}` : ''}{order.created_at ? ` · ${new Date(order.created_at).toLocaleString('zh-CN')}` : ''}</p>
                {order.status !== 'cancelled' && <button type="button" onClick={() => void cancelOrder(order)} disabled={cancellingId === order.order_id} className="mt-1.5 inline-flex items-center gap-1 rounded-md border border-slate-300 bg-white px-2 py-1 text-[11px] font-bold text-slate-600 hover:border-rose-300 hover:text-rose-700 disabled:opacity-50"><Ban className="h-3 w-3" />{cancellingId === order.order_id ? '取消中' : '取消订单'}</button>}
              </div>)}
              <p className="text-[11px] text-slate-400">订单来自平台记录；状态以预订平台通知为准，若长时间停留在处理中请到平台后台核对。</p>
            </div>}
          </div>

          <div className="rounded-xl border border-slate-200 bg-slate-50 p-4"><div className="mb-3 flex items-center justify-between"><h3 className="flex items-center gap-2 text-sm font-black text-slate-700"><ShieldCheck className="h-4 w-4 text-emerald-600" />服务状态</h3><button onClick={() => void loadHealth()} disabled={loading} className="flex h-8 w-8 items-center justify-center rounded-lg text-slate-500 hover:bg-white" title="刷新状态"><RefreshCw className={`h-3.5 w-3.5 ${loading ? 'animate-spin' : ''}`} /></button></div>{error && <p className="text-xs text-amber-700">{error}</p>}{!error && <div className="grid grid-cols-2 gap-2 text-xs">{['transport', 'lodging', 'scenic', 'weather'].map((key) => { const status = health?.[key]; return <div key={key} className="flex items-center justify-between rounded-lg bg-white px-3 py-2"><span className="text-slate-500">{key === 'transport' ? '交通预订' : key === 'lodging' ? '住宿预订' : key === 'scenic' ? '门票预订' : '天气数据'}</span><span className={status?.configured ? 'font-bold text-emerald-700' : 'font-bold text-slate-400'}>{status?.configured ? '可用' : '不可用'}</span></div>; })}</div>}</div>
        </div>
      </section>
    </div>
  );
}
