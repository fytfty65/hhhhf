// 交通票务查询类型定义

export type TransportType = 'flight' | 'train';

export interface TransportSearchParams {
  from: string;
  to: string;
  date: string; // YYYY-MM-DD
  type: TransportType;
}

export interface FlightResult {
  id: string;
  flightNo: string;
  airline: string;
  departure: string; // HH:mm
  arrival: string; // HH:mm
  from: string;
  to: string;
  fromAirport: string;
  toAirport: string;
  duration: string;
  price: number;
  discount: string;
  seats: number;
  aircraftType: string;
  onTimeRate: string;
}

export interface TrainSeats {
  secondClass: number;
  firstClass: number;
  business: number;
  softSleeper: number;
  hardSleeper: number;
  hardSeat: number;
  noSeat: number;
}

export interface TrainResult {
  id: string;
  trainNo: string;
  trainType: string; // 高铁/动车/普快/特快/直达 → 车次号首字母 G/D/Z/T/K 等
  trainFlags: string[]; // 列车标签，如：智能动车组、复兴号、静音车厢
  departure: string;
  arrival: string;
  fromStation: string;
  toStation: string;
  duration: string;
  price: number;
  cheapestPrice: number;
  seats: TrainSeats;
  isDirect: boolean;
}

export type TransportResult = FlightResult | TrainResult;

export interface TransportSearchResponse {
  type: TransportType;
  from: string;
  to: string;
  date: string;
  results: TransportResult[];
  total: number;
  cached: boolean;
}

export interface TransportSearchError {
  code: string;
  message: string;
  details?: string;
}

export type SortField = 'price' | 'departure' | 'duration';
export type SortOrder = 'asc' | 'desc';

export interface TransportFilterOptions {
  minPrice?: number;
  maxPrice?: number;
  departureTimeRange?: { start: string; end: string };
  arrivalTimeRange?: { start: string; end: string };
  airlines?: string[];
  trainTypes?: string[];
  directOnly?: boolean;
  sortField: SortField;
  sortOrder: SortOrder;
}