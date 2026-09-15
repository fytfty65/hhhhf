import { NextRequest, NextResponse } from 'next/server';

// Secrets are intentionally read only from the server environment. A local
// fallback would silently expose a shared credential and make deployments
// impossible to audit or revoke.
const FLIGHT_API_KEY = process.env.FLIGHT_API_KEY?.trim() || '';
const TRAIN_API_KEY = process.env.TRAIN_API_KEY?.trim() || '';

const FLIGHT_API_URL = process.env.FLIGHT_API_URL || 'https://apis.juhe.cn/flight/query';
const TRAIN_API_URL = process.env.TRAIN_API_URL || 'https://apis.juhe.cn/fapigw/train/query';

const REQUEST_TIMEOUT_MS = 15000;

// 中文城市名 → IATA 城市码映射（航班 API 需要城市码）
const CITY_TO_IATA: Record<string, string> = {
  '北京': 'BJS', '上海': 'SHA', '广州': 'CAN', '深圳': 'SZX',
  '成都': 'CTU', '杭州': 'HGH', '武汉': 'WUH', '西安': 'XIY',
  '重庆': 'CKG', '南京': 'NKG', '天津': 'TSN', '长沙': 'CSX',
  '青岛': 'TAO', '大连': 'DLC', '厦门': 'XMN', '昆明': 'KMG',
  '哈尔滨': 'HRB', '沈阳': 'SHE', '郑州': 'CGO', '济南': 'TNA',
  '福州': 'FOC', '贵阳': 'KWE', '南宁': 'NNG', '海口': 'HAK',
  '三亚': 'SYX', '乌鲁木齐': 'URC', '拉萨': 'LXA', '呼和浩特': 'HET',
  '太原': 'TYN', '石家庄': 'SJW', '长春': 'CGQ', '合肥': 'HFE',
  '南昌': 'KHN', '兰州': 'LHW', '银川': 'INC', '西宁': 'XNN',
  '宁波': 'NGB', '温州': 'WNZ', '珠海': 'ZUH', '桂林': 'KWL',
  '苏州': 'SZV', '无锡': 'WUX', '常州': 'CZX', '徐州': 'XUZ',
  '香港': 'HKG', '澳门': 'MFM', '台北': 'TPE',
};

function toIataCode(city: string): string {
  return CITY_TO_IATA[city.trim()] || city.trim();
}

function validateSearchParams(params: Record<string, string>): string | null {
  if (!params.from || params.from.trim().length === 0) return '请填写出发城市';
  if (!params.to || params.to.trim().length === 0) return '请填写目的城市';
  if (params.from.trim() === params.to.trim()) return '出发地和目的地不能相同';
  if (!params.date || !/^\d{4}-\d{2}-\d{2}$/.test(params.date)) return '请填写正确的日期格式 (YYYY-MM-DD)';
  if (params.date < new Date().toISOString().slice(0, 10)) return '日期不能早于今天';
  if (!['flight', 'train'].includes(params.type)) return '查询类型无效，请选择"航班"或"火车"';
  return null;
}

function buildFlightUrl(params: Record<string, string>, apiKey: string): string {
  const url = new URL(FLIGHT_API_URL);
  url.searchParams.set('key', apiKey);
  url.searchParams.set('departure', toIataCode(params.from));
  url.searchParams.set('arrival', toIataCode(params.to));
  url.searchParams.set('departureDate', params.date.trim());
  return url.toString();
}

function buildTrainUrl(params: Record<string, string>, apiKey: string): string {
  const url = new URL(TRAIN_API_URL);
  url.searchParams.set('key', apiKey);
  url.searchParams.set('search_type', '1'); // 1=通过站点名称查询
  url.searchParams.set('departure_station', params.from.trim());
  url.searchParams.set('arrival_station', params.to.trim());
  url.searchParams.set('date', params.date.trim());
  return url.toString();
}

function transformFlightResponse(raw: any, params: Record<string, string>) {
  const list = raw?.result?.flightInfo || [];
  return {
    type: 'flight' as const,
    from: params.from.trim(),
    to: params.to.trim(),
    date: params.date.trim(),
    results: (Array.isArray(list) ? list : []).map((item: any, idx: number) => ({
      id: item.flightNo ? `${item.flightNo}-${idx}` : `flight-${idx}`,
      flightNo: item.flightNo || '',
      airline: item.airlineName || item.airline || '',
      departure: item.departureTime || '',
      arrival: item.arrivalTime || '',
      from: params.from.trim(),
      to: params.to.trim(),
      fromAirport: item.departureName || item.departure || '',
      toAirport: item.arrivalName || item.arrival || '',
      duration: item.duration || '',
      price: Number(item.ticketPrice || item.price || 0),
      discount: item.discount || '全价',
      seats: item.transferNum !== undefined ? (item.transferNum === 1 ? 20 : 5) : 0,
      aircraftType: item.equipment || '',
      onTimeRate: item.onTimeRate || '--',
    })),
    total: (raw?.result?.flightInfo || []).length,
    cached: false,
  };
}

function transformTrainResponse(raw: any, params: Record<string, string>) {
  // 火车 API 的 result 直接就是数组
  const list = Array.isArray(raw?.result) ? raw.result : [];
  return {
    type: 'train' as const,
    from: params.from.trim(),
    to: params.to.trim(),
    date: params.date.trim(),
    results: list.map((item: any, idx: number) => {
      // 解析 prices 数组，提取各坐席余票
      const priceMap: Record<string, { price: number; num: number | string }> = {};
      let minPrice = Infinity;
      for (const p of (item.prices || [])) {
        const seatName = p.seat_name || '';
        const num = p.num === '无' || p.num === '0' ? 0 : (Number(p.num) || 0);
        const price = Number(p.price) || 0;
        priceMap[seatName] = { price, num };
        if (price > 0 && price < minPrice) minPrice = price;
      }

      const getNum = (name: string) => {
        const v = priceMap[name];
        return v ? v.num : 0;
      };

      // 从车次号提取类型前缀（G→高铁, D→动车, Z→直达, T→特快, K→快速）
      const trainTypePrefix = (item.train_no && /^[A-Z]/.test(item.train_no))
        ? item.train_no[0] : '';

      return {
        id: item.train_no ? `${item.train_no}-${idx}` : `train-${idx}`,
        trainNo: item.train_no || '',
        trainType: trainTypePrefix,
        trainFlags: Array.isArray(item.train_flags) ? item.train_flags : [],
        departure: item.departure_time || '',
        arrival: item.arrival_time || '',
        fromStation: item.departure_station || params.from.trim(),
        toStation: item.arrival_station || params.to.trim(),
        duration: item.duration || '',
        price: minPrice === Infinity ? 0 : minPrice,
        cheapestPrice: minPrice === Infinity ? 0 : minPrice,
        seats: {
          secondClass: getNum('二等座'),
          firstClass: getNum('一等座'),
          business: getNum('商务座'),
          softSleeper: getNum('软卧'),
          hardSleeper: getNum('硬卧'),
          hardSeat: getNum('硬座'),
          noSeat: getNum('无座'),
        },
        isDirect: true,
      };
    }),
    total: list.length,
    cached: false,
  };
}

export async function GET(request: NextRequest) {
  const { searchParams } = new URL(request.url);
  const params: Record<string, string> = {};
  searchParams.forEach((value, key) => { params[key] = value; });

  const validationError = validateSearchParams(params);
  if (validationError) {
    return NextResponse.json(
      { success: false, code: 'INVALID_PARAMS', error: validationError },
      { status: 400 },
    );
  }

  const isFlight = params.type === 'flight';
  const apiKey = isFlight ? FLIGHT_API_KEY : TRAIN_API_KEY;
  if (!apiKey) {
    return NextResponse.json(
      {
        success: false,
        code: 'PROVIDER_NOT_CONFIGURED',
        error: isFlight ? '航班查询服务尚未配置，请联系管理员' : '火车查询服务尚未配置，请联系管理员',
      },
      { status: 503 },
    );
  }
  const apiUrl = isFlight ? buildFlightUrl(params, apiKey) : buildTrainUrl(params, apiKey);

  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);

  try {
    const response = await fetch(apiUrl, {
      signal: controller.signal,
      headers: { 'Accept': 'application/json' },
    });

    const rawText = await response.text();
    let data: any = {};
    try {
      data = JSON.parse(rawText);
    } catch {
      return NextResponse.json(
        { success: false, code: 'INVALID_RESPONSE', error: '外部API返回了非JSON内容' },
        { status: 502 },
      );
    }

    const errorCode = data?.error_code;
    if (errorCode && errorCode !== 0) {
      const reason = data?.reason || `外部API错误 (code: ${errorCode})`;
      return NextResponse.json(
        { success: false, code: 'EXTERNAL_API_ERROR', error: reason },
        { status: 502 },
      );
    }

    const transformed = isFlight
      ? transformFlightResponse(data, params)
      : transformTrainResponse(data, params);

    return NextResponse.json({ success: true, data: transformed });
  } catch (error) {
    clearTimeout(timeoutId);
    if (error instanceof Error && error.name === 'AbortError') {
      return NextResponse.json(
        { success: false, code: 'REQUEST_TIMEOUT', error: '查询超时，请稍后重试' },
        { status: 504 },
      );
    }
    return NextResponse.json(
      { success: false, code: 'NETWORK_ERROR', error: '暂时无法连接订票服务，请稍后重试' },
      { status: 502 },
    );
  } finally {
    clearTimeout(timeoutId);
  }
}
