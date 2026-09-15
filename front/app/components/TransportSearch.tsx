'use client';

import React, { useState, useCallback, useMemo } from 'react';
import { AnimatePresence, motion } from 'framer-motion';
import {
  Plane, Train, Search, ArrowRight, ArrowUpDown, X,
  Clock, MapPin, DollarSign, AlertCircle, RefreshCw,
  Filter, ChevronDown, ChevronUp, Loader2, Info,
} from 'lucide-react';
import type {
  TransportSearchParams, TransportSearchResponse,
  FlightResult, TrainResult, TransportResult,
  SortField, SortOrder, TransportFilterOptions,
} from '../types/transport';
import {
  searchTransport, TransportApiError,
  formatPrice, formatSeats, getTrainTypeLabel, getTrainTypeColor,
  getSeatStatusColor, getSeatStatusBg, getDiscountColor,
  filterCities,
} from '../lib/transportApi';
import { getCached, setCache } from '../lib/transportCache';

interface TransportSearchProps {
  onClose?: () => void;
}

export default function TransportSearch({ onClose }: TransportSearchProps) {
  // 表单状态
  const [from, setFrom] = useState('');
  const [to, setTo] = useState('');
  const [date, setDate] = useState('');
  const [searchType, setSearchType] = useState<'flight' | 'train'>('flight');
  const [fromSuggestions, setFromSuggestions] = useState<string[]>([]);
  const [toSuggestions, setToSuggestions] = useState<string[]>([]);
  const [showFromSuggestions, setShowFromSuggestions] = useState(false);
  const [showToSuggestions, setShowToSuggestions] = useState(false);

  // 查询状态
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [results, setResults] = useState<TransportResult[]>([]);
  const [responseMeta, setResponseMeta] = useState<{ from: string; to: string; date: string; total: number; cached: boolean } | null>(null);
  const [hasSearched, setHasSearched] = useState(false);

  // 筛选排序状态
  const [showFilters, setShowFilters] = useState(false);
  const [filterOptions, setFilterOptions] = useState<TransportFilterOptions>({
    sortField: 'departure',
    sortOrder: 'asc',
    directOnly: false,
  });

  const handleFromChange = useCallback((value: string) => {
    setFrom(value);
    setFromSuggestions(filterCities(value));
    setShowFromSuggestions(true);
  }, []);

  const handleToChange = useCallback((value: string) => {
    setTo(value);
    setToSuggestions(filterCities(value));
    setShowToSuggestions(true);
  }, []);

  const selectFromCity = useCallback((city: string) => {
    setFrom(city);
    setShowFromSuggestions(false);
  }, []);

  const selectToCity = useCallback((city: string) => {
    setTo(city);
    setShowToSuggestions(false);
  }, []);

  const swapCities = useCallback(() => {
    // Read both values from the closure and commit them in separate setters.
    // The previous form called setTo() from inside the setFrom() updater, which
    // is unsafe: React may invoke an updater more than once (StrictMode,
    // concurrent rendering), so the side effect could fire repeatedly.
    const nextFrom = to;
    const nextTo = from;
    setFrom(nextFrom);
    setTo(nextTo);
  }, [from, to]);

  const handleSearch = useCallback(async () => {
    const params: TransportSearchParams = {
      from: from.trim(),
      to: to.trim(),
      date: date.trim(),
      type: searchType,
    };

    // 基础验证
    if (!params.from) { setError('请填写出发城市'); return; }
    if (!params.to) { setError('请填写目的城市'); return; }
    if (params.from === params.to) { setError('出发地和目的地不能相同'); return; }
    if (!params.date) { setError('请选择出发日期'); return; }

    setLoading(true);
    setError('');
    setResults([]);
    setHasSearched(true);

    // 检查缓存
    const cached = getCached(params);
    if (cached) {
      setResults(cached.results);
      setResponseMeta({ from: cached.from, to: cached.to, date: cached.date, total: cached.total, cached: true });
      setLoading(false);
      return;
    }

    try {
      const response = await searchTransport(params);
      setResults(response.results);
      setResponseMeta({ from: response.from, to: response.to, date: response.date, total: response.total, cached: false });
      setCache(params, response);
    } catch (err) {
      if (err instanceof TransportApiError) {
        setError(err.message);
      } else {
        setError('查询失败，请稍后重试');
      }
    } finally {
      setLoading(false);
    }
  }, [from, to, date, searchType]);

  // 筛选和排序
  const filteredAndSortedResults = useMemo(() => {
    let filtered = [...results];

    // 价格筛选
    if (filterOptions.minPrice !== undefined) {
      filtered = filtered.filter((r: any) => r.price >= filterOptions.minPrice!);
    }
    if (filterOptions.maxPrice !== undefined) {
      filtered = filtered.filter((r: any) => r.price <= filterOptions.maxPrice!);
    }

    // 直达筛选
    if (filterOptions.directOnly && searchType === 'train') {
      filtered = filtered.filter((r: any) => r.isDirect);
    }

    // 排序
    filtered.sort((a: any, b: any) => {
      let cmp = 0;
      switch (filterOptions.sortField) {
        case 'price':
          cmp = (a.price || 0) - (b.price || 0);
          break;
        case 'departure':
          cmp = (a.departure || '').localeCompare(b.departure || '');
          break;
        case 'duration':
          cmp = (a.duration || '').localeCompare(b.duration || '');
          break;
      }
      return filterOptions.sortOrder === 'desc' ? -cmp : cmp;
    });

    return filtered;
  }, [results, filterOptions, searchType]);

  const toggleSort = (field: SortField) => {
    setFilterOptions((prev: TransportFilterOptions) => ({
      ...prev,
      sortField: field,
      sortOrder: prev.sortField === field && prev.sortOrder === 'asc' ? 'desc' : 'asc',
    }));
  };

  const isFlight = searchType === 'flight';

  return (
    <div className="flex flex-col h-full bg-slate-50 dark:bg-slate-900">
      {/* 头部 */}
      <div className="bg-white dark:bg-slate-800 border-b border-slate-200 dark:border-slate-700 px-6 py-4">
        <div className="flex items-center justify-between mb-4">
          <div className="flex items-center gap-3">
            <div className="p-2 bg-gradient-to-br from-orange-500 to-rose-500 rounded-xl">
              {isFlight ? <Plane className="w-5 h-5 text-white" /> : <Train className="w-5 h-5 text-white" />}
            </div>
            <div>
              <h2 className="text-lg font-black text-slate-800 dark:text-white">
                {isFlight ? '航班订票查询' : '火车订票查询'}
              </h2>
              <p className="text-xs text-slate-500">实时查询班次、价格与余票信息</p>
            </div>
          </div>
          {onClose && (
            <button onClick={onClose} className="p-2 hover:bg-slate-100 dark:hover:bg-slate-700 rounded-lg text-slate-400">
              <X className="w-5 h-5" />
            </button>
          )}
        </div>

        {/* 类型切换 */}
        <div className="flex gap-2 mb-4">
          <button
            onClick={() => { setSearchType('flight'); setResults([]); setError(''); setHasSearched(false); }}
            className={`flex-1 py-2.5 rounded-xl font-bold text-sm flex items-center justify-center gap-2 transition-all cursor-pointer ${
              isFlight
                ? 'bg-gradient-to-r from-orange-500 to-rose-500 text-white shadow-lg shadow-orange-200'
                : 'bg-slate-100 dark:bg-slate-700 text-slate-600 dark:text-slate-300 hover:bg-slate-200'
            }`}
          >
            <Plane className="w-4 h-4" /> 航班
          </button>
          <button
            onClick={() => { setSearchType('train'); setResults([]); setError(''); setHasSearched(false); }}
            className={`flex-1 py-2.5 rounded-xl font-bold text-sm flex items-center justify-center gap-2 transition-all cursor-pointer ${
              !isFlight
                ? 'bg-gradient-to-r from-blue-500 to-cyan-500 text-white shadow-lg shadow-blue-200'
                : 'bg-slate-100 dark:bg-slate-700 text-slate-600 dark:text-slate-300 hover:bg-slate-200'
            }`}
          >
            <Train className="w-4 h-4" /> 火车
          </button>
        </div>

        {/* 搜索表单 */}
        <div className="flex flex-wrap items-end gap-3">
          <div className="relative flex-1 min-w-[140px]">
            <label className="block text-[11px] font-bold text-slate-500 mb-1">出发城市</label>
            <div className="relative">
              <MapPin className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-400" />
              <input
                type="text"
                value={from}
                onChange={(e) => handleFromChange(e.target.value)}
                onFocus={() => setShowFromSuggestions(true)}
                onBlur={() => setTimeout(() => setShowFromSuggestions(false), 200)}
                placeholder="例如：北京"
                className="w-full pl-9 pr-3 py-2.5 rounded-xl border border-slate-200 dark:border-slate-600 bg-white dark:bg-slate-700 text-sm font-bold text-slate-800 dark:text-white placeholder-slate-400 focus:outline-none focus:border-orange-400 focus:ring-2 focus:ring-orange-100"
              />
              {showFromSuggestions && fromSuggestions.length > 0 && (
                <div className="absolute top-full left-0 right-0 mt-1 bg-white dark:bg-slate-700 border border-slate-200 dark:border-slate-600 rounded-xl shadow-xl z-50 max-h-48 overflow-y-auto">
                  {fromSuggestions.map((city) => (
                    <button
                      key={city}
                      onMouseDown={() => selectFromCity(city)}
                      className="w-full px-4 py-2 text-left text-sm font-bold text-slate-700 dark:text-slate-200 hover:bg-orange-50 dark:hover:bg-slate-600 cursor-pointer"
                    >
                      {city}
                    </button>
                  ))}
                </div>
              )}
            </div>
          </div>

          <button
            onClick={swapCities}
            className="self-end mb-1 p-2 rounded-full hover:bg-slate-100 dark:hover:bg-slate-700 transition-colors cursor-pointer"
            title="交换出发地与目的地"
          >
            <ArrowRight className="w-4 h-4 text-slate-400 rotate-90" />
          </button>

          <div className="relative flex-1 min-w-[140px]">
            <label className="block text-[11px] font-bold text-slate-500 mb-1">目的城市</label>
            <div className="relative">
              <MapPin className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-400" />
              <input
                type="text"
                value={to}
                onChange={(e) => handleToChange(e.target.value)}
                onFocus={() => setShowToSuggestions(true)}
                onBlur={() => setTimeout(() => setShowToSuggestions(false), 200)}
                placeholder="例如：上海"
                className="w-full pl-9 pr-3 py-2.5 rounded-xl border border-slate-200 dark:border-slate-600 bg-white dark:bg-slate-700 text-sm font-bold text-slate-800 dark:text-white placeholder-slate-400 focus:outline-none focus:border-orange-400 focus:ring-2 focus:ring-orange-100"
              />
              {showToSuggestions && toSuggestions.length > 0 && (
                <div className="absolute top-full left-0 right-0 mt-1 bg-white dark:bg-slate-700 border border-slate-200 dark:border-slate-600 rounded-xl shadow-xl z-50 max-h-48 overflow-y-auto">
                  {toSuggestions.map((city) => (
                    <button
                      key={city}
                      onMouseDown={() => selectToCity(city)}
                      className="w-full px-4 py-2 text-left text-sm font-bold text-slate-700 dark:text-slate-200 hover:bg-orange-50 dark:hover:bg-slate-600 cursor-pointer"
                    >
                      {city}
                    </button>
                  ))}
                </div>
              )}
            </div>
          </div>

          <div className="flex-1 min-w-[140px]">
            <label className="block text-[11px] font-bold text-slate-500 mb-1">出发日期</label>
            <div className="relative">
              <input
                type="date"
                value={date}
                onChange={(e) => setDate(e.target.value)}
                min={new Date().toISOString().slice(0, 10)}
                className="w-full px-4 py-2.5 rounded-xl border border-slate-200 dark:border-slate-600 bg-white dark:bg-slate-700 text-sm font-bold text-slate-800 dark:text-white focus:outline-none focus:border-orange-400 focus:ring-2 focus:ring-orange-100"
              />
            </div>
          </div>

          <button
            onClick={handleSearch}
            disabled={loading}
            className="px-6 py-2.5 rounded-xl bg-gradient-to-r from-orange-500 to-rose-500 text-white font-bold text-sm flex items-center gap-2 hover:shadow-lg hover:shadow-orange-200 transition-all disabled:opacity-50 disabled:cursor-not-allowed cursor-pointer"
          >
            {loading ? (
              <Loader2 className="w-4 h-4 animate-spin" />
            ) : (
              <Search className="w-4 h-4" />
            )}
            查询
          </button>
        </div>
      </div>

      {/* 错误提示 */}
      <AnimatePresence>
        {error && (
          <motion.div
            initial={{ opacity: 0, y: -10 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -10 }}
            className="mx-6 mt-4 p-3 bg-red-50 border border-red-200 rounded-xl flex items-center gap-2"
          >
            <AlertCircle className="w-4 h-4 text-red-500 shrink-0" />
            <p className="text-sm font-bold text-red-600">{error}</p>
          </motion.div>
        )}
      </AnimatePresence>

      {/* 结果区域 */}
      <div className="flex-1 overflow-y-auto px-6 py-4">
        {!hasSearched && !loading && !error && (
          <div className="flex flex-col items-center justify-center h-full text-center py-16">
            <div className="w-20 h-20 rounded-2xl bg-gradient-to-br from-slate-100 to-slate-200 dark:from-slate-800 dark:to-slate-700 flex items-center justify-center mb-4">
              {isFlight ? <Plane className="w-8 h-8 text-slate-400" /> : <Train className="w-8 h-8 text-slate-400" />}
            </div>
            <h3 className="text-lg font-black text-slate-400 mb-2">开始查询</h3>
            <p className="text-sm text-slate-400 max-w-xs">
              输入出发城市、目的城市和日期，点击查询按钮获取实时{isFlight ? '航班' : '火车'}信息
            </p>
          </div>
        )}

        {loading && (
          <div className="flex flex-col items-center justify-center py-16">
            <Loader2 className="w-8 h-8 text-orange-500 animate-spin mb-3" />
            <p className="text-sm font-bold text-slate-500">正在查询{isFlight ? '航班' : '火车'}信息...</p>
          </div>
        )}

        {hasSearched && !loading && !error && results.length === 0 && (
          <div className="flex flex-col items-center justify-center py-16 text-center">
            <div className="w-16 h-16 rounded-full bg-slate-100 dark:bg-slate-800 flex items-center justify-center mb-4">
              <Info className="w-6 h-6 text-slate-400" />
            </div>
            <h3 className="text-lg font-black text-slate-500 mb-2">未找到结果</h3>
            <p className="text-sm text-slate-400">
              未找到从 {from} 到 {to} 的{isFlight ? '航班' : '火车'}信息，请调整查询条件后重试
            </p>
          </div>
        )}

        {hasSearched && !loading && results.length > 0 && (
          <>
            {/* 结果头部 */}
            <div className="flex items-center justify-between mb-4">
              <div>
                <h3 className="text-sm font-black text-slate-800 dark:text-white">
                  {responseMeta?.from} <ArrowRight className="w-3 h-3 inline text-slate-400" /> {responseMeta?.to}
                  <span className="text-slate-400 font-normal ml-2">{responseMeta?.date}</span>
                </h3>
                <p className="text-xs text-slate-500 mt-0.5">
                  共找到 {filteredAndSortedResults.length} 个{isFlight ? '航班' : '车次'}
                  {responseMeta?.cached && (
                    <span className="ml-2 text-amber-500 font-bold">（缓存结果）</span>
                  )}
                </p>
              </div>

              <div className="flex items-center gap-2">
                <button
                  onClick={() => setShowFilters(!showFilters)}
                  className={`p-2 rounded-lg text-xs font-bold flex items-center gap-1 transition-colors cursor-pointer ${
                    showFilters ? 'bg-orange-100 text-orange-600' : 'bg-slate-100 dark:bg-slate-700 text-slate-600 dark:text-slate-300 hover:bg-slate-200'
                  }`}
                >
                  <Filter className="w-3.5 h-3.5" />
                  筛选排序
                  {showFilters ? <ChevronUp className="w-3.5 h-3.5" /> : <ChevronDown className="w-3.5 h-3.5" />}
                </button>
                <button
                  onClick={handleSearch}
                  className="p-2 rounded-lg bg-slate-100 dark:bg-slate-700 text-slate-600 dark:text-slate-300 hover:bg-slate-200 text-xs font-bold flex items-center gap-1 cursor-pointer"
                  title="刷新结果"
                >
                  <RefreshCw className="w-3.5 h-3.5" />
                </button>
              </div>
            </div>

            {/* 筛选面板 */}
            <AnimatePresence>
              {showFilters && (
                <motion.div
                  initial={{ height: 0, opacity: 0 }}
                  animate={{ height: 'auto', opacity: 1 }}
                  exit={{ height: 0, opacity: 0 }}
                  className="overflow-hidden mb-4"
                >
                  <div className="bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-xl p-4 space-y-3">
                    {/* 排序按钮 */}
                    <div>
                      <p className="text-[11px] font-bold text-slate-500 mb-2">排序方式</p>
                      <div className="flex gap-2">
                        {[
                          { field: 'departure' as SortField, label: '出发时间' },
                          { field: 'price' as SortField, label: '价格' },
                          { field: 'duration' as SortField, label: '耗时' },
                        ].map(({ field, label }) => (
                          <button
                            key={field}
                            onClick={() => toggleSort(field)}
                            className={`px-3 py-1.5 rounded-lg text-xs font-bold flex items-center gap-1 transition-colors cursor-pointer ${
                              filterOptions.sortField === field
                                ? 'bg-orange-100 text-orange-600 border border-orange-200'
                                : 'bg-slate-50 dark:bg-slate-700 text-slate-600 dark:text-slate-300 border border-slate-200 dark:border-slate-600'
                            }`}
                          >
                            {label}
                            {filterOptions.sortField === field && (
                              <ArrowUpDown className="w-3 h-3" />
                            )}
                          </button>
                        ))}
                      </div>
                    </div>

                    {/* 价格筛选 */}
                    <div>
                      <p className="text-[11px] font-bold text-slate-500 mb-2">价格范围</p>
                      <div className="flex items-center gap-2">
                        <input
                          type="number"
                          placeholder="最低价"
                          value={filterOptions.minPrice ?? ''}
                          onChange={(e) => setFilterOptions((prev: TransportFilterOptions) => ({
                            ...prev, minPrice: e.target.value ? Number(e.target.value) : undefined,
                          }))}
                          className="w-24 px-3 py-1.5 rounded-lg border border-slate-200 dark:border-slate-600 bg-white dark:bg-slate-700 text-xs font-bold"
                        />
                        <span className="text-slate-400">-</span>
                        <input
                          type="number"
                          placeholder="最高价"
                          value={filterOptions.maxPrice ?? ''}
                          onChange={(e) => setFilterOptions((prev: TransportFilterOptions) => ({
                            ...prev, maxPrice: e.target.value ? Number(e.target.value) : undefined,
                          }))}
                          className="w-24 px-3 py-1.5 rounded-lg border border-slate-200 dark:border-slate-600 bg-white dark:bg-slate-700 text-xs font-bold"
                        />
                      </div>
                    </div>

                    {/* 火车专属筛选 */}
                    {searchType === 'train' && (
                      <div>
                        <label className="flex items-center gap-2 cursor-pointer">
                          <input
                            type="checkbox"
                            checked={filterOptions.directOnly || false}
                            onChange={(e) => setFilterOptions((prev: TransportFilterOptions) => ({
                              ...prev, directOnly: e.target.checked,
                            }))}
                            className="rounded accent-orange-500"
                          />
                          <span className="text-xs font-bold text-slate-600 dark:text-slate-300">仅显示直达车次</span>
                        </label>
                      </div>
                    )}
                  </div>
                </motion.div>
              )}
            </AnimatePresence>

            {/* 结果列表 */}
            <div className="space-y-3">
              {filteredAndSortedResults.map((result: any, idx: number) => (
                <motion.div
                  key={result.id}
                  initial={{ opacity: 0, y: 10 }}
                  animate={{ opacity: 1, y: 0 }}
                  transition={{ delay: idx * 0.05 }}
                  className="bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-xl p-4 hover:shadow-md hover:border-orange-200 dark:hover:border-orange-700 transition-all"
                >
                  {isFlight ? (
                    <FlightResultCard result={result as FlightResult} />
                  ) : (
                    <TrainResultCard result={result as TrainResult} />
                  )}
                </motion.div>
              ))}
            </div>
          </>
        )}
      </div>
    </div>
  );
}

function FlightResultCard({ result }: { result: FlightResult }) {
  return (
    <div className="flex items-center gap-4">
      {/* 航班信息 */}
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2 mb-1">
          <span className="text-xs font-bold text-orange-600 bg-orange-50 dark:bg-orange-900/30 px-2 py-0.5 rounded">
            {result.airline}
          </span>
          <span className="text-sm font-black text-slate-800 dark:text-white">{result.flightNo}</span>
          {result.aircraftType && (
            <span className="text-[10px] text-slate-400">{result.aircraftType}</span>
          )}
        </div>

        <div className="flex items-center gap-4 mt-2">
          <div className="text-center">
            <p className="text-xl font-black text-slate-800 dark:text-white">{result.departure}</p>
            <p className="text-[10px] text-slate-500 mt-0.5 truncate max-w-[80px]" title={result.fromAirport}>
              {result.fromAirport}
            </p>
          </div>

          <div className="flex-1 flex flex-col items-center px-2">
            <p className="text-[10px] text-slate-400 mb-1">{result.duration}</p>
            <div className="w-full h-px bg-slate-200 dark:bg-slate-600 relative">
              <div className="absolute top-1/2 -translate-y-1/2 left-1/2 -translate-x-1/2">
                <Plane className="w-3 h-3 text-slate-400 rotate-90" />
              </div>
            </div>
            <p className="text-[10px] text-slate-400 mt-1">直飞</p>
          </div>

          <div className="text-center">
            <p className="text-xl font-black text-slate-800 dark:text-white">{result.arrival}</p>
            <p className="text-[10px] text-slate-500 mt-0.5 truncate max-w-[80px]" title={result.toAirport}>
              {result.toAirport}
            </p>
          </div>
        </div>
      </div>

      {/* 价格和余票 */}
      <div className="text-right shrink-0">
        <p className="text-2xl font-black text-orange-500">
          {formatPrice(result.price)}
        </p>
        {result.discount && result.discount !== '全价' && (
          <p className={`text-[10px] font-bold ${getDiscountColor(result.discount)}`}>
            {result.discount}
          </p>
        )}
        <p className={`text-xs font-bold mt-1 ${getSeatStatusColor(result.seats)}`}>
          {formatSeats(result.seats)}
        </p>
        {result.onTimeRate && (
          <p className="text-[10px] text-slate-400 mt-0.5">
            准点率 {result.onTimeRate}
          </p>
        )}
      </div>
    </div>
  );
}

function TrainResultCard({ result }: { result: TrainResult }) {
  const seatTypes = [
    { key: 'business', label: '商务座', seats: result.seats.business },
    { key: 'firstClass', label: '一等座', seats: result.seats.firstClass },
    { key: 'secondClass', label: '二等座', seats: result.seats.secondClass },
    { key: 'softSleeper', label: '软卧', seats: result.seats.softSleeper },
    { key: 'hardSleeper', label: '硬卧', seats: result.seats.hardSleeper },
  ];

  const availableSeats = seatTypes.filter((s) => s.seats > 0);

  return (
    <div className="flex items-start gap-4">
      {/* 车次信息 */}
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2 mb-1">
          <span className={`text-[10px] font-bold text-white px-1.5 py-0.5 rounded ${getTrainTypeColor(result.trainType)}`}>
            {getTrainTypeLabel(result.trainType)}
          </span>
          <span className="text-sm font-black text-slate-800 dark:text-white">{result.trainNo}</span>
          {!result.isDirect && (
            <span className="text-[10px] text-amber-500 bg-amber-50 px-1.5 py-0.5 rounded">中转</span>
          )}
        </div>

        <div className="flex items-center gap-4 mt-2">
          <div className="text-center">
            <p className="text-xl font-black text-slate-800 dark:text-white">{result.departure}</p>
            <p className="text-[10px] text-slate-500 mt-0.5 truncate max-w-[80px]" title={result.fromStation}>
              {result.fromStation}
            </p>
          </div>

          <div className="flex-1 flex flex-col items-center px-2">
            <p className="text-[10px] text-slate-400 mb-1">{result.duration}</p>
            <div className="w-full h-px bg-slate-200 dark:bg-slate-600 relative">
              <div className="absolute top-1/2 -translate-y-1/2 left-1/2 -translate-x-1/2">
                <Train className="w-3 h-3 text-slate-400" />
              </div>
            </div>
          </div>

          <div className="text-center">
            <p className="text-xl font-black text-slate-800 dark:text-white">{result.arrival}</p>
            <p className="text-[10px] text-slate-500 mt-0.5 truncate max-w-[80px]" title={result.toStation}>
              {result.toStation}
            </p>
          </div>
        </div>

        {/* 余票信息 */}
        {availableSeats.length > 0 && (
          <div className="flex flex-wrap gap-1.5 mt-3">
            {seatTypes.filter((s) => s.seats > 0).map((seat) => (
              <span
                key={seat.key}
                className={`text-[10px] font-bold px-2 py-1 rounded border ${getSeatStatusBg(seat.seats)} ${getSeatStatusColor(seat.seats)}`}
              >
                {seat.label}: {formatSeats(seat.seats)}
              </span>
            ))}
          </div>
        )}
      </div>

      {/* 价格 */}
      <div className="text-right shrink-0">
        <p className="text-2xl font-black text-orange-500">
          {formatPrice(result.cheapestPrice || result.price)}
        </p>
        <p className="text-[10px] text-slate-400">起 · 以官方页面为准</p>
        <a
          href="https://kyfw.12306.cn/otn/leftTicket/init"
          target="_blank"
          rel="noopener noreferrer"
          className="mt-2 inline-flex min-h-9 items-center justify-center rounded-md border border-orange-200 bg-orange-50 px-3 text-[11px] font-bold text-orange-700 transition hover:bg-orange-100"
        >
          前往 12306 购票
        </a>
        <p className="mt-1 text-[9px] text-slate-400">余票与价格来源：查询服务 · 请在 12306 核验</p>
      </div>
    </div>
  );
}
