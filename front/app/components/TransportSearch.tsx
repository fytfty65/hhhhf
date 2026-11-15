'use client';

import React, { useId, useState, useCallback, useMemo } from 'react';
import { AnimatePresence, motion } from 'framer-motion';
import {
  Plane, Train, ArrowRight, ArrowLeftRight, X,
  MapPin, AlertCircle, RefreshCw, Calendar, Clock, Sparkles,
  Filter, ChevronLeft, ChevronRight, Loader2, ExternalLink, ShieldCheck
} from 'lucide-react';
import type {
  TransportSearchParams,
  FlightResult, TrainResult, TransportResult,
  SortField, TransportFilterOptions,
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

const POPULAR_CITIES = ['北京', '上海', '成都', '西安', '杭州', '洛阳'];

export default function TransportSearch({ onClose }: TransportSearchProps) {
  // 表单状态
  const fromId = useId();
  const toId = useId();
  const dateId = useId();
  const [from, setFrom] = useState('');
  const [to, setTo] = useState('');
  const [date, setDate] = useState(() => new Date().toISOString().slice(0, 10));
  const [searchType, setSearchType] = useState<'flight' | 'train'>('flight');
  const [fromSuggestions, setFromSuggestions] = useState<string[]>([]);
  const [toSuggestions, setToSuggestions] = useState<string[]>([]);
  const [showFromSuggestions, setShowFromSuggestions] = useState(false);
  const [showToSuggestions, setShowToSuggestions] = useState(false);
  const [isSwapping, setIsSwapping] = useState(false);

  // 查询状态
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [results, setResults] = useState<TransportResult[]>([]);
  const [responseMeta, setResponseMeta] = useState<{
    from: string;
    to: string;
    date: string;
    total: number;
    cached: boolean;
  } | null>(null);
  const [hasSearched, setHasSearched] = useState(false);

  // 筛选排序状态
  const [showPriceFilter, setShowPriceFilter] = useState(false);
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

  const handleQuickCityPick = useCallback((city: string) => {
    if (!from) {
      setFrom(city);
    } else if (!to && city !== from) {
      setTo(city);
    } else if (city !== from) {
      setTo(city);
    }
  }, [from, to]);

  const swapCities = useCallback(() => {
    setIsSwapping(true);
    const nextFrom = to;
    const nextTo = from;
    setFrom(nextFrom);
    setTo(nextTo);
    setTimeout(() => setIsSwapping(false), 300);
  }, [from, to]);

  const executeSearch = useCallback(async (overrideDate?: string) => {
    const targetDate = overrideDate !== undefined ? overrideDate : date;
    const params: TransportSearchParams = {
      from: from.trim(),
      to: to.trim(),
      date: targetDate.trim(),
      type: searchType,
    };

    if (!params.from) { setError('请先填写出发城市'); return; }
    if (!params.to) { setError('请先填写到达城市'); return; }
    if (params.from === params.to) { setError('出发城市与到达城市不能相同'); return; }
    if (!params.date) { setError('请选择出发日期'); return; }

    setLoading(true);
    setError('');
    setResults([]);
    setHasSearched(true);

    const cached = getCached(params);
    if (cached) {
      setResults(cached.results);
      setResponseMeta({
        from: cached.from,
        to: cached.to,
        date: cached.date,
        total: cached.total,
        cached: true,
      });
      setLoading(false);
      return;
    }

    try {
      const response = await searchTransport(params);
      setResults(response.results);
      setResponseMeta({
        from: response.from,
        to: response.to,
        date: response.date,
        total: response.total,
        cached: false,
      });
      setCache(params, response);
    } catch (err) {
      if (err instanceof TransportApiError) {
        setError(err.message);
      } else {
        setError('大交通数据同步超时，请稍后重试');
      }
    } finally {
      setLoading(false);
    }
  }, [from, to, date, searchType]);

  // 快捷切换前一天/后一天
  const shiftDate = useCallback((days: number) => {
    if (!date) return;
    const current = new Date(date);
    current.setDate(current.getDate() + days);
    const todayStr = new Date().toISOString().slice(0, 10);
    const nextDateStr = current.toISOString().slice(0, 10);
    if (nextDateStr < todayStr) return;
    setDate(nextDateStr);
    if (from && to) {
      executeSearch(nextDateStr);
    }
  }, [date, from, to, executeSearch]);

  // 筛选和排序
  const filteredAndSortedResults = useMemo(() => {
    let filtered = [...results];

    if (filterOptions.minPrice !== undefined) {
      filtered = filtered.filter((r: any) => r.price >= filterOptions.minPrice!);
    }
    if (filterOptions.maxPrice !== undefined) {
      filtered = filtered.filter((r: any) => r.price <= filterOptions.maxPrice!);
    }
    if (filterOptions.directOnly && searchType === 'train') {
      filtered = filtered.filter((r: any) => r.isDirect);
    }

    filtered.sort((a: any, b: any) => {
      let cmp = 0;
      switch (filterOptions.sortField) {
        case 'price':
          cmp = ((a.cheapestPrice || a.price) || 0) - ((b.cheapestPrice || b.price) || 0);
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
    <div className="flex flex-col h-full bg-[#FAF8F5] dark:bg-stone-950 text-stone-800 dark:text-stone-100 antialiased selection:bg-orange-500/20 selection:text-orange-600">
      {/* 顶部标题与交通方式切换 */}
      <div className="bg-white dark:bg-stone-900 border-b border-stone-200/80 dark:border-stone-800 px-6 pt-5 pb-6 shadow-[0_2px_16px_rgba(28,25,23,0.02)]">
        <div className="flex flex-wrap items-center justify-between gap-4 mb-5">
          <div className="flex items-center gap-3">
            <span className="w-6 h-6 rounded-full bg-orange-500 text-white text-xs font-bold flex items-center justify-center shadow-sm shadow-orange-500/25">
              {isFlight ? <Plane className="w-3.5 h-3.5" /> : <Train className="w-3.5 h-3.5" />}
            </span>
            <div>
              <div className="flex items-center gap-2">
                <h2 className="text-base font-bold tracking-tight text-stone-900 dark:text-white">
                  大交通班次与余票查询
                </h2>
                <span className="text-[11px] font-medium px-2 py-0.5 rounded-full bg-orange-50 dark:bg-orange-950/60 text-orange-600 dark:text-orange-400 border border-orange-200/60 dark:border-orange-800/50">
                  实时路网连接
                </span>
              </div>
              <p className="text-xs text-stone-500 dark:text-stone-400 mt-0.5">
                先确定城际往返大交通，再衔接当地每日行程（票价及余票以官方出票为准）
              </p>
            </div>
          </div>

          <div className="flex items-center gap-3">
            {/* 内嵌式胶囊切换器 (Segmented Pill) */}
            <div className="relative flex p-1 rounded-full bg-[#F4F2EE] dark:bg-stone-800 border border-stone-200/60 dark:border-stone-700/60">
              {(['flight', 'train'] as const).map((type) => {
                const active = searchType === type;
                return (
                  <button
                    key={type}
                    type="button"
                    onClick={() => {
                      setSearchType(type);
                      setResults([]);
                      setError('');
                      setHasSearched(false);
                    }}
                    className={`relative z-10 flex items-center gap-1.5 px-4 py-1.5 rounded-full text-xs font-semibold transition-colors cursor-pointer ${
                      active
                        ? 'text-stone-900 dark:text-white'
                        : 'text-stone-500 hover:text-stone-700 dark:text-stone-400'
                    }`}
                  >
                    {active && (
                      <motion.span
                        layoutId="transportTypePill"
                        transition={{ type: 'spring', stiffness: 420, damping: 32 }}
                        className="absolute inset-0 rounded-full bg-white dark:bg-stone-900 shadow-sm border border-stone-200/70 dark:border-stone-700"
                      />
                    )}
                    <span className="relative z-10 flex items-center gap-1.5">
                      {type === 'flight' ? (
                        <Plane className={`w-3.5 h-3.5 ${active ? 'text-orange-500' : ''}`} />
                      ) : (
                        <Train className={`w-3.5 h-3.5 ${active ? 'text-orange-500' : ''}`} />
                      )}
                      {type === 'flight' ? '国内航班' : '高铁火车'}
                    </span>
                  </button>
                );
              })}
            </div>

            {onClose && (
              <button
                onClick={onClose}
                className="p-2 rounded-full text-stone-400 hover:text-stone-600 hover:bg-stone-100 dark:hover:bg-stone-800 transition-colors cursor-pointer"
                title="关闭窗口"
              >
                <X className="w-4 h-4" />
              </button>
            )}
          </div>
        </div>

        {/* 一体化组团式搜索栏 */}
        <div className="bg-[#FAF9F6] dark:bg-stone-800/50 p-2 rounded-2xl border border-stone-200/80 dark:border-stone-800">
          <div className="grid grid-cols-1 md:grid-cols-[1fr_auto_1fr_170px_auto] items-center gap-2">
            {/* 出发地 */}
            <div className="relative bg-white dark:bg-stone-900 rounded-xl px-3.5 py-2 border border-stone-200/70 dark:border-stone-700/80 focus-within:border-orange-500 focus-within:ring-3 focus-within:ring-orange-500/10 transition-all">
              <label htmlFor={fromId} className="flex items-center gap-1 text-[11px] font-medium text-stone-400">
                <MapPin className="w-3 h-3 text-orange-500" />
                出发城市
              </label>
              <input
                id={fromId}
                type="text"
                value={from}
                onChange={(e) => handleFromChange(e.target.value)}
                onFocus={() => setShowFromSuggestions(true)}
                onBlur={() => setTimeout(() => setShowFromSuggestions(false), 180)}
                placeholder="例如：北京"
                className="w-full mt-0.5 bg-transparent text-sm font-bold text-stone-900 dark:text-white placeholder-stone-300 dark:placeholder-stone-600 focus:outline-none"
              />
              {showFromSuggestions && fromSuggestions.length > 0 && (
                <div className="absolute left-0 right-0 top-[calc(100%+6px)] bg-white dark:bg-stone-800 border border-stone-200 dark:border-stone-700 rounded-xl shadow-lg py-1.5 z-50 max-h-48 overflow-y-auto">
                  {fromSuggestions.map((city) => (
                    <button
                      key={city}
                      type="button"
                      onMouseDown={() => selectFromCity(city)}
                      className="w-full px-3.5 py-2 text-left text-xs font-semibold text-stone-700 dark:text-stone-200 hover:bg-orange-50 dark:hover:bg-stone-700/70 hover:text-orange-600 transition-colors cursor-pointer flex items-center justify-between"
                    >
                      <span>{city}</span>
                      <span className="text-[10px] text-stone-400">选择出发</span>
                    </button>
                  ))}
                </div>
              )}
            </div>

            {/* 往返切换按钮 */}
            <div className="flex justify-center">
              <button
                type="button"
                onClick={swapCities}
                title="交换出发地与目的地"
                className="w-8 h-8 rounded-full bg-white dark:bg-stone-900 border border-stone-200 dark:border-stone-700 flex items-center justify-center text-stone-500 hover:text-orange-500 hover:border-orange-300 shadow-xs transition-all cursor-pointer"
              >
                <ArrowLeftRight
                  className={`w-3.5 h-3.5 transition-transform duration-300 ${
                    isSwapping ? 'rotate-180 text-orange-500' : ''
                  }`}
                />
              </button>
            </div>

            {/* 目的地 */}
            <div className="relative bg-white dark:bg-stone-900 rounded-xl px-3.5 py-2 border border-stone-200/70 dark:border-stone-700/80 focus-within:border-orange-500 focus-within:ring-3 focus-within:ring-orange-500/10 transition-all">
              <label htmlFor={toId} className="flex items-center gap-1 text-[11px] font-medium text-stone-400">
                <MapPin className="w-3 h-3 text-stone-400" />
                到达城市
              </label>
              <input
                id={toId}
                type="text"
                value={to}
                onChange={(e) => handleToChange(e.target.value)}
                onFocus={() => setShowToSuggestions(true)}
                onBlur={() => setTimeout(() => setShowToSuggestions(false), 180)}
                placeholder="例如：成都、西安"
                className="w-full mt-0.5 bg-transparent text-sm font-bold text-stone-900 dark:text-white placeholder-stone-300 dark:placeholder-stone-600 focus:outline-none"
              />
              {showToSuggestions && toSuggestions.length > 0 && (
                <div className="absolute left-0 right-0 top-[calc(100%+6px)] bg-white dark:bg-stone-800 border border-stone-200 dark:border-stone-700 rounded-xl shadow-lg py-1.5 z-50 max-h-48 overflow-y-auto">
                  {toSuggestions.map((city) => (
                    <button
                      key={city}
                      type="button"
                      onMouseDown={() => selectToCity(city)}
                      className="w-full px-3.5 py-2 text-left text-xs font-semibold text-stone-700 dark:text-stone-200 hover:bg-orange-50 dark:hover:bg-stone-700/70 hover:text-orange-600 transition-colors cursor-pointer flex items-center justify-between"
                    >
                      <span>{city}</span>
                      <span className="text-[10px] text-stone-400">选择到达</span>
                    </button>
                  ))}
                </div>
              )}
            </div>

            {/* 出发日期 */}
            <div className="bg-white dark:bg-stone-900 rounded-xl px-3.5 py-2 border border-stone-200/70 dark:border-stone-700/80 focus-within:border-orange-500 focus-within:ring-3 focus-within:ring-orange-500/10 transition-all">
              <label htmlFor={dateId} className="flex items-center gap-1 text-[11px] font-medium text-stone-400">
                <Calendar className="w-3 h-3 text-stone-400" />
                出发日期
              </label>
              <input
                id={dateId}
                type="date"
                value={date}
                onChange={(e) => setDate(e.target.value)}
                min={new Date().toISOString().slice(0, 10)}
                className="w-full mt-0.5 bg-transparent text-sm font-bold text-stone-900 dark:text-white focus:outline-none tabular-nums"
              />
            </div>

            {/* 查询主按钮 - 对齐 OmniRoute 纯正活力橙胶囊按钮 */}
            <button
              type="button"
              onClick={() => executeSearch()}
              disabled={loading}
              className="h-full min-h-[52px] px-6 rounded-xl bg-[#F97316] hover:bg-[#EA580C] active:scale-[0.99] text-white font-bold text-sm flex items-center justify-center gap-2 shadow-sm shadow-orange-500/20 transition-all disabled:opacity-50 disabled:cursor-not-allowed cursor-pointer"
            >
              {loading ? (
                <Loader2 className="w-4 h-4 animate-spin" />
              ) : (
                <>
                  <span>查询班次</span>
                  <ArrowRight className="w-4 h-4" />
                </>
              )}
            </button>
          </div>
        </div>

        {/* 热门灵感城市胶囊 - 与主界面底部城市 Tag 保持视觉一致 */}
        <div className="flex items-center flex-wrap gap-1.5 mt-3">
          <span className="text-[11px] text-stone-400 mr-1">快速填入：</span>
          {POPULAR_CITIES.map((city) => {
            const isSelected = from === city || to === city;
            return (
              <button
                key={city}
                type="button"
                onClick={() => handleQuickCityPick(city)}
                className={`px-2.5 py-1 rounded-full text-xs font-medium transition-all cursor-pointer ${
                  isSelected
                    ? 'bg-orange-50 dark:bg-orange-950/60 text-orange-600 dark:text-orange-400 border border-orange-200 dark:border-orange-800/60'
                    : 'bg-[#F4F2EE] dark:bg-stone-800 text-stone-600 dark:text-stone-300 hover:bg-stone-200/70 dark:hover:bg-stone-700'
                }`}
              >
                {city}
              </button>
            );
          })}
        </div>
      </div>

      {/* 错误提示条 */}
      <AnimatePresence>
        {error && (
          <motion.div
            initial={{ opacity: 0, y: -8 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -8 }}
            className="mx-6 mt-4 px-4 py-3 bg-red-50/90 dark:bg-red-950/40 border border-red-200/80 dark:border-red-900/60 rounded-xl flex items-center justify-between gap-2"
          >
            <div className="flex items-center gap-2.5">
              <AlertCircle className="w-4 h-4 text-red-500 shrink-0" />
              <p className="text-xs font-semibold text-red-700 dark:text-red-300">{error}</p>
            </div>
            <button
              onClick={() => setError('')}
              className="text-xs text-red-400 hover:text-red-600 cursor-pointer"
            >
              知道了
            </button>
          </motion.div>
        )}
      </AnimatePresence>

      {/* 主体结果区 */}
      <div className="flex-1 overflow-y-auto px-6 py-5">
        {/* 初始未搜索空状态 */}
        {!hasSearched && !loading && !error && (
          <div className="h-full min-h-[280px] flex flex-col items-center justify-center text-center">
            <div className="w-14 h-14 rounded-2xl bg-white dark:bg-stone-900 border border-stone-200/80 dark:border-stone-800 shadow-xs flex items-center justify-center mb-3.5 text-orange-500">
              {isFlight ? <Plane className="w-6 h-6" /> : <Train className="w-6 h-6" />}
            </div>
            <h3 className="text-sm font-bold text-stone-700 dark:text-stone-200 mb-1">
              准备好规划城际大交通了吗？
            </h3>
            <p className="text-xs text-stone-400 max-w-xs leading-relaxed">
              上方选择出发地、目的地与出行日期，即可比对全天{isFlight ? '直飞航班、准点率与舱位折扣' : '高铁动车时刻与各席别余票'}。
            </p>
          </div>
        )}

        {/* 拟真骨架屏加载态（取代单调转圈） */}
        {loading && (
          <div className="space-y-3">
            <div className="flex items-center justify-between mb-2">
              <div className="h-4 w-44 rounded bg-stone-200/70 dark:bg-stone-800 animate-pulse" />
              <div className="h-4 w-28 rounded bg-stone-200/70 dark:bg-stone-800 animate-pulse" />
            </div>
            {[0, 1, 2].map((item) => (
              <div
                key={item}
                className="bg-white dark:bg-stone-900 border border-stone-200/70 dark:border-stone-800 rounded-2xl p-5 animate-pulse flex items-center justify-between gap-6"
              >
                <div className="flex-1 space-y-3">
                  <div className="flex items-center gap-2">
                    <div className="h-5 w-16 rounded-md bg-stone-200/80 dark:bg-stone-800" />
                    <div className="h-5 w-20 rounded-md bg-stone-100 dark:bg-stone-800/60" />
                  </div>
                  <div className="flex items-center gap-6">
                    <div className="h-7 w-16 rounded bg-stone-200/80 dark:bg-stone-800" />
                    <div className="flex-1 h-2 rounded bg-stone-100 dark:bg-stone-800" />
                    <div className="h-7 w-16 rounded bg-stone-200/80 dark:bg-stone-800" />
                  </div>
                </div>
                <div className="w-28 text-right space-y-2 border-l border-dashed border-stone-200 dark:border-stone-800 pl-6">
                  <div className="h-7 w-20 ml-auto rounded bg-orange-100 dark:bg-orange-950/50" />
                  <div className="h-4 w-14 ml-auto rounded bg-stone-100 dark:bg-stone-800" />
                </div>
              </div>
            ))}
          </div>
        )}

        {/* 查询无结果 */}
        {hasSearched && !loading && !error && results.length === 0 && (
          <div className="bg-white dark:bg-stone-900 border border-stone-200/80 dark:border-stone-800 rounded-2xl p-10 text-center my-4">
            <div className="w-12 h-12 rounded-full bg-orange-50 dark:bg-orange-950/50 text-orange-500 flex items-center justify-center mx-auto mb-3">
              <Sparkles className="w-5 h-5" />
            </div>
            <h3 className="text-sm font-bold text-stone-800 dark:text-stone-100 mb-1">
              暂未查到直达{isFlight ? '航班' : '列车'}班次
            </h3>
            <p className="text-xs text-stone-400 max-w-sm mx-auto">
              未检索到 {date} 从「{from}」前往「{to}」的班次，建议尝试切换前后日期或改用{isFlight ? '高铁火车' : '国内航班'}查询。
            </p>
          </div>
        )}

        {/* 结果列表与行内控制栏 */}
        {hasSearched && !loading && results.length > 0 && (
          <>
            {/* 顶部状态与快捷筛选栏 */}
            <div className="bg-white dark:bg-stone-900 border border-stone-200/80 dark:border-stone-800 rounded-2xl p-3.5 mb-4 shadow-[0_2px_10px_rgba(28,25,23,0.02)]">
              <div className="flex flex-wrap items-center justify-between gap-3">
                {/* 路线摘要与前后一天快捷按钮 */}
                <div className="flex items-center flex-wrap gap-2">
                  <div className="flex items-center gap-1.5 text-sm font-bold text-stone-900 dark:text-white">
                    <span>{responseMeta?.from}</span>
                    <ArrowRight className="w-3.5 h-3.5 text-orange-500" />
                    <span>{responseMeta?.to}</span>
                  </div>

                  <div className="flex items-center bg-[#F4F2EE] dark:bg-stone-800 rounded-lg p-0.5 text-xs">
                    <button
                      type="button"
                      onClick={() => shiftDate(-1)}
                      className="px-2 py-1 rounded-md text-stone-500 hover:text-stone-800 hover:bg-white dark:hover:bg-stone-700 transition-colors cursor-pointer"
                      title="前一天"
                    >
                      <ChevronLeft className="w-3.5 h-3.5" />
                    </button>
                    <span className="px-2 font-semibold text-stone-700 dark:text-stone-200 tabular-nums">
                      {responseMeta?.date}
                    </span>
                    <button
                      type="button"
                      onClick={() => shiftDate(1)}
                      className="px-2 py-1 rounded-md text-stone-500 hover:text-stone-800 hover:bg-white dark:hover:bg-stone-700 transition-colors cursor-pointer"
                      title="后一天"
                    >
                      <ChevronRight className="w-3.5 h-3.5" />
                    </button>
                  </div>

                  <span className="text-xs text-stone-400">
                    共 <strong className="text-stone-700 dark:text-stone-200">{filteredAndSortedResults.length}</strong> 个班次
                  </span>

                  {responseMeta?.cached && (
                    <span className="text-[11px] font-medium px-2 py-0.5 rounded-full bg-amber-50 dark:bg-amber-950/50 text-amber-600 dark:text-amber-400 border border-amber-200/60">
                      本地速查缓存
                    </span>
                  )}
                </div>

                {/* 行内排序胶囊与筛选触发器 */}
                <div className="flex items-center flex-wrap gap-1.5">
                  {([
                    { field: 'departure' as SortField, label: '出发早晚' },
                    { field: 'price' as SortField, label: '票价高低' },
                    { field: 'duration' as SortField, label: '耗时最短' },
                  ]).map(({ field, label }) => {
                    const active = filterOptions.sortField === field;
                    return (
                      <button
                        key={field}
                        type="button"
                        onClick={() => toggleSort(field)}
                        className={`px-2.5 py-1.5 rounded-lg text-xs font-semibold flex items-center gap-1 transition-all cursor-pointer ${
                          active
                            ? 'bg-orange-50 dark:bg-orange-950/60 text-orange-600 dark:text-orange-400 border border-orange-200/80 dark:border-orange-800'
                            : 'bg-[#F4F2EE] dark:bg-stone-800 text-stone-600 dark:text-stone-300 hover:bg-stone-200/70'
                        }`}
                      >
                        <span>{label}</span>
                        {active && (
                          <span className="text-[10px]">
                            {filterOptions.sortOrder === 'asc' ? '↑' : '↓'}
                          </span>
                        )}
                      </button>
                    );
                  })}

                  {!isFlight && (
                    <label className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg bg-[#F4F2EE] dark:bg-stone-800 text-xs font-semibold text-stone-600 dark:text-stone-300 cursor-pointer select-none">
                      <input
                        type="checkbox"
                        checked={filterOptions.directOnly || false}
                        onChange={(e) =>
                          setFilterOptions((prev: TransportFilterOptions) => ({
                            ...prev,
                            directOnly: e.target.checked,
                          }))
                        }
                        className="rounded accent-orange-500 w-3.5 h-3.5"
                      />
                      仅看直达
                    </label>
                  )}

                  <button
                    type="button"
                    onClick={() => setShowPriceFilter(!showPriceFilter)}
                    className={`p-1.5 rounded-lg border text-xs font-semibold flex items-center gap-1 transition-colors cursor-pointer ${
                      showPriceFilter || filterOptions.minPrice || filterOptions.maxPrice
                        ? 'bg-orange-50 border-orange-200 text-orange-600'
                        : 'bg-white dark:bg-stone-800 border-stone-200 dark:border-stone-700 text-stone-500 hover:text-stone-700'
                    }`}
                    title="预算区间筛选"
                  >
                    <Filter className="w-3.5 h-3.5" />
                  </button>

                  <button
                    type="button"
                    onClick={() => executeSearch()}
                    className="p-1.5 rounded-lg bg-white dark:bg-stone-800 border border-stone-200 dark:border-stone-700 text-stone-500 hover:text-orange-500 transition-colors cursor-pointer"
                    title="重新同步实时数据"
                  >
                    <RefreshCw className="w-3.5 h-3.5" />
                  </button>
                </div>
              </div>

              {/* 可展开的预算过滤条 */}
              <AnimatePresence>
                {showPriceFilter && (
                  <motion.div
                    initial={{ height: 0, opacity: 0 }}
                    animate={{ height: 'auto', opacity: 1 }}
                    exit={{ height: 0, opacity: 0 }}
                    className="overflow-hidden"
                  >
                    <div className="pt-3 mt-3 border-t border-stone-100 dark:border-stone-800 flex items-center flex-wrap gap-2 text-xs">
                      <span className="text-stone-400 font-medium">单人预算范围（元）：</span>
                      <input
                        type="number"
                        placeholder="最低价"
                        value={filterOptions.minPrice ?? ''}
                        onChange={(e) =>
                          setFilterOptions((prev: TransportFilterOptions) => ({
                            ...prev,
                            minPrice: e.target.value ? Number(e.target.value) : undefined,
                          }))
                        }
                        className="w-24 px-2.5 py-1 rounded-lg border border-stone-200 dark:border-stone-700 bg-[#FAF9F6] dark:bg-stone-800 font-semibold text-stone-800 dark:text-stone-100 focus:outline-none focus:border-orange-400"
                      />
                      <span className="text-stone-300">—</span>
                      <input
                        type="number"
                        placeholder="最高价"
                        value={filterOptions.maxPrice ?? ''}
                        onChange={(e) =>
                          setFilterOptions((prev: TransportFilterOptions) => ({
                            ...prev,
                            maxPrice: e.target.value ? Number(e.target.value) : undefined,
                          }))
                        }
                        className="w-24 px-2.5 py-1 rounded-lg border border-stone-200 dark:border-stone-700 bg-[#FAF9F6] dark:bg-stone-800 font-semibold text-stone-800 dark:text-stone-100 focus:outline-none focus:border-orange-400"
                      />
                      {(filterOptions.minPrice !== undefined || filterOptions.maxPrice !== undefined) && (
                        <button
                          type="button"
                          onClick={() =>
                            setFilterOptions((prev: TransportFilterOptions) => ({
                              ...prev,
                              minPrice: undefined,
                              maxPrice: undefined,
                            }))
                          }
                          className="text-orange-600 hover:underline ml-1 cursor-pointer"
                        >
                          重置预算
                        </button>
                      )}
                    </div>
                  </motion.div>
                )}
              </AnimatePresence>
            </div>

            {/* 拟真票面卡片列表 */}
            <div className="space-y-2.5">
              {filteredAndSortedResults.map((result: any, idx: number) => (
                <motion.div
                  key={result.id}
                  initial={{ opacity: 0, y: 8 }}
                  animate={{ opacity: 1, y: 0 }}
                  transition={{ duration: 0.2, delay: Math.min(idx * 0.035, 0.25) }}
                  className="group relative bg-white dark:bg-stone-900 border border-stone-200/85 dark:border-stone-800 rounded-2xl px-5 py-4 hover:border-orange-300 dark:hover:border-orange-700/80 hover:shadow-[0_6px_24px_rgba(249,115,22,0.06)] transition-all"
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
    <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
      {/* 左侧：航司标识与时刻轴 */}
      <div className="flex-1 min-w-0">
        <div className="flex items-center flex-wrap gap-2 mb-3">
          <span className="text-xs font-bold text-orange-600 dark:text-orange-400 bg-orange-50 dark:bg-orange-950/60 border border-orange-200/60 dark:border-orange-800/50 px-2.5 py-0.5 rounded-full">
            {result.airline}
          </span>
          <span className="text-xs font-bold text-stone-800 dark:text-stone-200 tabular-nums">
            {result.flightNo}
          </span>
          {result.aircraftType && (
            <span className="text-[11px] text-stone-400 bg-[#F4F2EE] dark:bg-stone-800 px-2 py-0.5 rounded-md">
              机型 {result.aircraftType}
            </span>
          )}
          {result.onTimeRate && (
            <span className="text-[11px] text-emerald-600 dark:text-emerald-400 flex items-center gap-1 ml-auto sm:ml-0">
              <ShieldCheck className="w-3 h-3" />
              准点率 {result.onTimeRate}
            </span>
          )}
        </div>

        {/* 时刻对比时间轴 */}
        <div className="flex items-center gap-4 sm:gap-8">
          <div className="text-left min-w-[76px]">
            <p className="text-2xl font-bold tracking-tight text-stone-900 dark:text-white tabular-nums">
              {result.departure}
            </p>
            <p
              className="text-xs font-medium text-stone-500 dark:text-stone-400 mt-0.5 truncate max-w-[110px]"
              title={result.fromAirport}
            >
              {result.fromAirport}
            </p>
          </div>

          {/* 航程轨迹线 */}
          <div className="flex-1 max-w-[180px] flex flex-col items-center">
            <span className="text-[11px] font-medium text-stone-400 mb-1 flex items-center gap-1">
              <Clock className="w-3 h-3" />
              {result.duration}
            </span>
            <div className="w-full flex items-center gap-1">
              <span className="w-1.5 h-1.5 rounded-full bg-stone-300 dark:bg-stone-600" />
              <div className="flex-1 h-px border-t border-dashed border-stone-300 dark:border-stone-600 relative">
                <Plane className="w-3.5 h-3.5 text-orange-500 absolute -top-2 left-1/2 -translate-x-1/2 rotate-90 bg-white dark:bg-stone-900 px-0.5" />
              </div>
              <span className="w-1.5 h-1.5 rounded-full bg-orange-400" />
            </div>
            <span className="text-[10px] text-stone-400 mt-1">直飞航班</span>
          </div>

          <div className="text-right sm:text-left min-w-[76px]">
            <p className="text-2xl font-bold tracking-tight text-stone-900 dark:text-white tabular-nums">
              {result.arrival}
            </p>
            <p
              className="text-xs font-medium text-stone-500 dark:text-stone-400 mt-0.5 truncate max-w-[110px]"
              title={result.toAirport}
            >
              {result.toAirport}
            </p>
          </div>
        </div>
      </div>

      {/* 登机牌虚线分割位与右侧价格区 */}
      <div className="relative sm:pl-6 sm:border-l sm:border-dashed sm:border-stone-200 dark:sm:border-stone-800 flex sm:flex-col items-center sm:items-end justify-between sm:justify-center shrink-0 pt-3 sm:pt-0 border-t sm:border-t-0 border-stone-100">
        {/* 仿真票根上下圆孔切口 */}
        <span className="hidden sm:block absolute -left-[7px] -top-6 w-3.5 h-3.5 rounded-full bg-[#FAF8F5] dark:bg-stone-950 border-b border-stone-200/80 dark:border-stone-800" />
        <span className="hidden sm:block absolute -left-[7px] -bottom-6 w-3.5 h-3.5 rounded-full bg-[#FAF8F5] dark:bg-stone-950 border-t border-stone-200/80 dark:border-stone-800" />

        <div className="text-left sm:text-right">
          <div className="flex items-baseline sm:justify-end gap-1">
            <span className="text-2xl font-bold text-[#F97316] tabular-nums">
              {formatPrice(result.price)}
            </span>
            <span className="text-[11px] text-stone-400">起</span>
          </div>

          <div className="flex items-center sm:justify-end gap-1.5 mt-1">
            {result.discount && result.discount !== '全价' && (
              <span className={`text-[11px] font-semibold ${getDiscountColor(result.discount)}`}>
                {result.discount}
              </span>
            )}
            <span className={`text-xs font-semibold ${getSeatStatusColor(result.seats)}`}>
              {formatSeats(result.seats)}
            </span>
          </div>
        </div>
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
    <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
      {/* 左侧：车次信息、时刻轴与席别余票胶囊 */}
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2 mb-3">
          <span
            className={`text-[11px] font-bold text-white px-2 py-0.5 rounded-md ${getTrainTypeColor(
              result.trainType
            )}`}
          >
            {getTrainTypeLabel(result.trainType)}
          </span>
          <span className="text-sm font-bold text-stone-900 dark:text-white tabular-nums">
            {result.trainNo}
          </span>
          {!result.isDirect && (
            <span className="text-[11px] font-medium text-amber-600 bg-amber-50 border border-amber-200/60 px-2 py-0.5 rounded-full">
              中转换乘
            </span>
          )}
        </div>

        {/* 时刻对比时间轴 */}
        <div className="flex items-center gap-4 sm:gap-8">
          <div className="text-left min-w-[76px]">
            <p className="text-2xl font-bold tracking-tight text-stone-900 dark:text-white tabular-nums">
              {result.departure}
            </p>
            <p
              className="text-xs font-medium text-stone-500 dark:text-stone-400 mt-0.5 truncate max-w-[110px]"
              title={result.fromStation}
            >
              {result.fromStation}
            </p>
          </div>

          <div className="flex-1 max-w-[180px] flex flex-col items-center">
            <span className="text-[11px] font-medium text-stone-400 mb-1 flex items-center gap-1">
              <Clock className="w-3 h-3" />
              {result.duration}
            </span>
            <div className="w-full flex items-center gap-1">
              <span className="w-1.5 h-1.5 rounded-full bg-stone-300 dark:bg-stone-600" />
              <div className="flex-1 h-px border-t border-dashed border-stone-300 dark:border-stone-600 relative">
                <Train className="w-3.5 h-3.5 text-orange-500 absolute -top-2 left-1/2 -translate-x-1/2 bg-white dark:bg-stone-900 px-0.5" />
              </div>
              <span className="w-1.5 h-1.5 rounded-full bg-orange-400" />
            </div>
          </div>

          <div className="text-right sm:text-left min-w-[76px]">
            <p className="text-2xl font-bold tracking-tight text-stone-900 dark:text-white tabular-nums">
              {result.arrival}
            </p>
            <p
              className="text-xs font-medium text-stone-500 dark:text-stone-400 mt-0.5 truncate max-w-[110px]"
              title={result.toStation}
            >
              {result.toStation}
            </p>
          </div>
        </div>

        {/* 席别余票胶囊矩阵 */}
        {availableSeats.length > 0 && (
          <div className="flex flex-wrap gap-1.5 mt-3.5">
            {availableSeats.map((seat) => (
              <span
                key={seat.key}
                className={`text-[11px] font-semibold px-2.5 py-0.5 rounded-full border ${getSeatStatusBg(
                  seat.seats
                )} ${getSeatStatusColor(seat.seats)}`}
              >
                {seat.label} · {formatSeats(seat.seats)}
              </span>
            ))}
          </div>
        )}
      </div>

      {/* 车票虚线分割与右侧购票区 */}
      <div className="relative sm:pl-6 sm:border-l sm:border-dashed sm:border-stone-200 dark:sm:border-stone-800 flex sm:flex-col items-center sm:items-end justify-between sm:justify-center shrink-0 pt-3 sm:pt-0 border-t sm:border-t-0 border-stone-100">
        <span className="hidden sm:block absolute -left-[7px] -top-6 w-3.5 h-3.5 rounded-full bg-[#FAF8F5] dark:bg-stone-950 border-b border-stone-200/80 dark:border-stone-800" />
        <span className="hidden sm:block absolute -left-[7px] -bottom-6 w-3.5 h-3.5 rounded-full bg-[#FAF8F5] dark:bg-stone-950 border-t border-stone-200/80 dark:border-stone-800" />

        <div className="text-left sm:text-right">
          <div className="flex items-baseline sm:justify-end gap-1">
            <span className="text-2xl font-bold text-[#F97316] tabular-nums">
              {formatPrice(result.cheapestPrice || result.price)}
            </span>
            <span className="text-[11px] text-stone-400">起</span>
          </div>
          <p className="text-[10px] text-stone-400 mt-0.5">12306 实时核验为准</p>
        </div>

        <a
          href="https://kyfw.12306.cn/otn/leftTicket/init"
          target="_blank"
          rel="noopener noreferrer"
          className="mt-0 sm:mt-2.5 inline-flex items-center gap-1 rounded-full border border-orange-200 dark:border-orange-800/70 bg-orange-50/80 dark:bg-orange-950/50 hover:bg-[#F97316] hover:text-white hover:border-[#F97316] px-3.5 py-1.5 text-xs font-bold text-orange-600 dark:text-orange-400 transition-all"
        >
          <span>前往 12306</span>
          <ExternalLink className="w-3 h-3" />
        </a>
      </div>
    </div>
  );
}
