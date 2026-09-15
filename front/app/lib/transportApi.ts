// 交通票务查询客户端服务层
// 负责参数验证、错误处理、超时控制、响应转换

import type {
  TransportSearchParams,
  TransportSearchResponse,
  TransportSearchError,
  TransportType,
} from '../types/transport';

const REQUEST_TIMEOUT_MS = 15000;

export class TransportApiError extends Error {
  code: string;
  status: number;

  constructor(message: string, code = 'UNKNOWN_ERROR', status = 0) {
    super(message);
    this.name = 'TransportApiError';
    this.code = code;
    this.status = status;
  }
}

function validateParams(params: TransportSearchParams): string | null {
  if (!params.from || params.from.trim().length === 0) return '请填写出发城市';
  if (!params.to || params.to.trim().length === 0) return '请填写目的城市';
  if (params.from.trim() === params.to.trim()) return '出发地和目的地不能相同';
  if (!params.date || !/^\d{4}-\d{2}-\d{2}$/.test(params.date)) return '请填写正确的日期格式';
  const today = new Date().toISOString().slice(0, 10);
  if (params.date < today) return '查询日期不能早于今天';
  if (!['flight', 'train'].includes(params.type)) return '查询类型无效';
  return null;
}

export async function searchTransport(params: TransportSearchParams): Promise<TransportSearchResponse> {
  const validationError = validateParams(params);
  if (validationError) {
    throw new TransportApiError(validationError, 'INVALID_PARAMS', 400);
  }

  const query = new URLSearchParams({
    from: params.from.trim(),
    to: params.to.trim(),
    date: params.date.trim(),
    type: params.type,
  });

  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);

  try {
    const response = await fetch(`/api/transport?${query.toString()}`, {
      signal: controller.signal,
      headers: { 'Accept': 'application/json' },
    });

    const data = await response.json();

    if (!response.ok || !data.success) {
      const errorCode = data?.code || 'REQUEST_FAILED';
      const errorMessage = data?.error || `请求失败 (HTTP ${response.status})`;
      throw new TransportApiError(errorMessage, errorCode, response.status);
    }

    return data.data as TransportSearchResponse;
  } catch (error) {
    clearTimeout(timeoutId);
    if (error instanceof TransportApiError) throw error;
    if (error instanceof Error && error.name === 'AbortError') {
      throw new TransportApiError('查询超时，请稍后重试', 'REQUEST_TIMEOUT', 504);
    }
    throw new TransportApiError('暂时无法连接订票服务，请稍后重试', 'NETWORK_ERROR', 0);
  } finally {
    clearTimeout(timeoutId);
  }
}

export function formatPrice(price: number): string {
  if (price <= 0) return '暂无报价';
  return `¥${price.toLocaleString('zh-CN', { minimumFractionDigits: 0, maximumFractionDigits: 0 })}`;
}

export function formatSeats(seats: number): string {
  if (seats <= 0) return '售罄';
  if (seats <= 5) return `仅剩 ${seats} 张`;
  return `余 ${seats} 张`;
}

export function getTrainTypeLabel(trainType: string): string {
  const map: Record<string, string> = {
    'G': '高铁', 'D': '动车', 'C': '城际',
    'Z': '直达', 'T': '特快', 'K': '快速',
    'P': '普快', 'L': '临客', 'Y': '旅游',
  };
  return map[trainType] || trainType || '列车';
}

export function getTrainTypeColor(trainType: string): string {
  const map: Record<string, string> = {
    'G': 'bg-blue-500', 'D': 'bg-sky-500', 'C': 'bg-cyan-500',
    'Z': 'bg-purple-500', 'T': 'bg-indigo-500', 'K': 'bg-teal-500',
  };
  return map[trainType] || 'bg-slate-500';
}

export function getSeatStatusColor(seats: number): string {
  if (seats <= 0) return 'text-red-500';
  if (seats <= 10) return 'text-amber-500';
  return 'text-emerald-500';
}

export function getSeatStatusBg(seats: number): string {
  if (seats <= 0) return 'bg-red-50 border-red-200';
  if (seats <= 10) return 'bg-amber-50 border-amber-200';
  return 'bg-emerald-50 border-emerald-200';
}

export function getDiscountColor(discount: string): string {
  if (discount.includes('折') || Number(discount) < 10) return 'text-orange-500';
  return 'text-slate-500';
}

export const CHINESE_CITIES = [
  '北京', '上海', '广州', '深圳', '杭州', '南京', '成都', '重庆',
  '武汉', '西安', '长沙', '郑州', '天津', '苏州', '厦门', '青岛',
  '昆明', '大连', '沈阳', '哈尔滨', '济南', '福州', '合肥', '南昌',
  '南宁', '贵阳', '兰州', '太原', '石家庄', '长春', '海口', '三亚',
  '拉萨', '乌鲁木齐', '呼和浩特', '银川', '西宁', '宁波', '温州', '无锡',
  '佛山', '东莞', '珠海', '惠州', '中山', '桂林', '丽江', '大理',
];

export function filterCities(query: string): string[] {
  if (!query || query.trim().length === 0) return [];
  const q = query.trim().toLowerCase();
  return CHINESE_CITIES.filter(city =>
    city.toLowerCase().includes(q) || city.includes(q),
  ).slice(0, 8);
}