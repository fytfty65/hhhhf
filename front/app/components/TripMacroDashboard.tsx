'use client';

import { Wallet, TrendingUp, BarChart3, Clock } from 'lucide-react';
import { RadarThemeMode, radarPalette, radarPanelStyle, RADAR_FONT } from '../lib/radarTheme';
import { RoutePoint } from '../InteractiveAmapComponent';
import DraggablePanel from './DraggablePanel';
import BudgetForecastChart, { ForecastDay } from './charts/BudgetForecastChart';
import CostBarChart from './charts/CostBarChart';
import TimelineGanttChart from './charts/TimelineGanttChart';

interface DailyForecast {
  perDay: ForecastDay[];
  totalSpent: number;
  totalBudget: number;
  remaining: number;
}

interface TripMacroDashboardProps {
  mode: RadarThemeMode;
  timelineDay: number;
  timelineDays: number[];
  routes: RoutePoint[];
  dayTotal: number;
  dailyBudget: number;
  memberCount: number;
  avgCost: number;
  overrun: number;
  hasOverrun: boolean;
  costSortedNodes: any[];
  maxNodeCost: number;
  dailyForecast: DailyForecast;
  onDayChange: (day: number) => void;
  onPoiSelect: (index: number) => void;
}

/**
 * 行程数据看板（MACRO 视口）：纯 SVG/HTML，无第二 WebGL 地球。
 * 聚焦三大核心价值：时间轴 + 预算 + 成本分布。
 * KPI 概览 / 预算累计曲线 / 节点成本条形图 / 时间轴甘特图 / Day 滑块 / 滚动信息条。
 */
export default function TripMacroDashboard({
  mode,
  timelineDay,
  timelineDays,
  routes,
  dayTotal,
  dailyBudget,
  memberCount,
  avgCost,
  overrun,
  hasOverrun,
  costSortedNodes,
  maxNodeCost,
  dailyForecast,
  onDayChange,
  onPoiSelect,
}: TripMacroDashboardProps) {
  const COLORS = radarPalette(mode);
  const panelStyle = radarPanelStyle(mode);

  const handleSelectByName = (name: string) => {
    const idx = routes.findIndex((r: any) => r.name === name);
    if (idx >= 0) onPoiSelect(idx);
  };
  const verifiedCount = routes.filter((route: any) => Array.isArray(route.lnglat) && route.lnglat.length >= 2 && route.lnglat.every((value: any) => Number.isFinite(Number(value)) && Number(value) !== 0)).length;
  const estimatedCount = routes.filter((route: any) => route.estimated || route.data_sources?.cost_estimate === 'unavailable').length;
  const dayCounts = timelineDays.map((day) => routes.filter((route: any) => Number(route.day) === day && !route.is_hotel && !route.tags?.includes('住宿')).length);
  const minDayCount = dayCounts.length ? Math.min(...dayCounts) : 0;
  const maxDayCount = dayCounts.length ? Math.max(...dayCounts) : 0;
  const daySpread = maxDayCount - minDayCount;

  return (
    <div className="w-full h-full flex flex-col">
      {/* ============ 可滚动图表区 ============ */}
      <div className="flex-1 min-h-0 overflow-y-auto custom-scrollbar px-4 sm:px-6 pt-[72px] pb-3 space-y-3">
        {/* KPI 概览 */}
        <DraggablePanel
          className="radar-panel"
          style={panelStyle}
          icon={<Wallet className="w-3.5 h-3.5" style={{ color: COLORS.accent }} />}
          title={<span className="text-[13px] font-bold" style={{ color: COLORS.accent }}>行程预算概览</span>}
        >
          <div className="p-3 sm:p-4">
            <div className="grid grid-cols-2 lg:grid-cols-4 gap-2.5">
              <div className="rounded-xl p-3 border" style={{ background: COLORS.deep, borderColor: COLORS.borderSoft }}>
                <div className="text-[11px] mb-1" style={{ color: COLORS.muted }}>Day {timelineDay} 消费</div>
                <div className="text-lg font-black" style={{ color: COLORS.origin, fontFamily: RADAR_FONT.data }}>¥{dayTotal}</div>
              </div>
              <div className="rounded-xl p-3 border" style={{ background: COLORS.deep, borderColor: COLORS.borderSoft }}>
                <div className="text-[11px] mb-1" style={{ color: COLORS.muted }}>日均预算</div>
                <div className="text-lg font-black" style={{ color: COLORS.accent, fontFamily: RADAR_FONT.data }}>{dailyBudget > 0 ? `¥${dailyBudget}` : '—'}</div>
              </div>
              <div className="rounded-xl p-3 border" style={{ background: hasOverrun ? 'rgba(244,63,94,0.12)' : 'rgba(16,185,129,0.12)', borderColor: hasOverrun ? COLORS.danger : COLORS.safe }}>
                <div className="text-[11px] mb-1" style={{ color: hasOverrun ? COLORS.danger : COLORS.safe }}>预算状态</div>
                <div className="text-lg font-black" style={{ color: hasOverrun ? COLORS.danger : COLORS.safe, fontFamily: RADAR_FONT.data }}>
                  {dailyBudget > 0 ? (hasOverrun ? `超 ¥${Math.round(overrun)}` : '健康') : '未设'}
                </div>
              </div>
              <div className="rounded-xl p-3 border" style={{ background: COLORS.deep, borderColor: COLORS.borderSoft }}>
                <div className="text-[11px] mb-1" style={{ color: COLORS.muted }}>人均估计</div>
                <div className="text-lg font-black" style={{ color: COLORS.cyan, fontFamily: RADAR_FONT.data }}>
                  ¥{avgCost}<span className="text-[11px] font-normal" style={{ color: COLORS.muted }}>/{memberCount}人</span>
                </div>
              </div>
            </div>

            {/* 预算使用进度条 */}
            {dailyBudget > 0 && (
              <div className="mt-3">
                <div className="flex justify-between text-[11px] mb-1.5" style={{ fontFamily: RADAR_FONT.data }}>
                  <span style={{ color: COLORS.muted }}>预算使用率</span>
                  <span style={{ color: hasOverrun ? COLORS.danger : COLORS.safe }}>{Math.round((dayTotal / dailyBudget) * 100)}%</span>
                </div>
                <div className="h-2 w-full rounded-full overflow-hidden" style={{ background: COLORS.divider }}>
                  <div
                    className="h-full rounded-full transition-all duration-700"
                    style={{
                      width: `${Math.min(100, Math.round((dayTotal / dailyBudget) * 100))}%`,
                      background: hasOverrun
                        ? `linear-gradient(90deg, ${COLORS.warn}, ${COLORS.danger})`
                        : `linear-gradient(90deg, ${COLORS.safe}, ${COLORS.cyan})`,
                    }}
                  />
                </div>
              </div>
            )}
          </div>
        </DraggablePanel>

        <DraggablePanel className="radar-panel" style={panelStyle} icon={<BarChart3 className="w-3.5 h-3.5" style={{ color: COLORS.cyan }} />} title={<span className="text-[13px] font-bold" style={{ color: COLORS.cyan }}>计划健康度</span>}>
            <div className="grid grid-cols-2 gap-2 p-3 sm:grid-cols-5">
            <div className="rounded-lg border p-2" style={{ borderColor: COLORS.borderSoft, background: COLORS.deep }}><p className="text-[10px]" style={{ color: COLORS.muted }}>节点总数</p><p className="mt-1 text-lg font-black" style={{ color: COLORS.accent }}>{routes.length}</p></div>
            <div className="rounded-lg border p-2" style={{ borderColor: COLORS.borderSoft, background: COLORS.deep }}><p className="text-[10px]" style={{ color: COLORS.muted }}>坐标覆盖</p><p className="mt-1 text-lg font-black" style={{ color: COLORS.safe }}>{routes.length ? Math.round(verifiedCount / routes.length * 100) : 0}%</p></div>
            <div className="rounded-lg border p-2" style={{ borderColor: COLORS.borderSoft, background: COLORS.deep }}><p className="text-[10px]" style={{ color: COLORS.muted }}>估算字段</p><p className="mt-1 text-lg font-black" style={{ color: estimatedCount ? COLORS.warn : COLORS.safe }}>{estimatedCount}</p></div>
            <div className="rounded-lg border p-2" style={{ borderColor: COLORS.borderSoft, background: COLORS.deep }}><p className="text-[10px]" style={{ color: COLORS.muted }}>每日白天节点</p><p className="mt-1 truncate text-xs font-black" style={{ color: COLORS.origin }}>{dayCounts.map((count, index) => `D${timelineDays[index]}:${count}`).join(' · ') || '—'}</p></div>
            <div className="rounded-lg border p-2" style={{ borderColor: daySpread > 1 ? COLORS.warn : COLORS.borderSoft, background: daySpread > 1 ? 'rgba(245,158,11,0.10)' : COLORS.deep }}>
              <p className="text-[10px]" style={{ color: daySpread > 1 ? COLORS.warn : COLORS.muted }}>日程均衡</p>
              <p className="mt-1 text-xs font-black" style={{ color: daySpread > 1 ? COLORS.warn : COLORS.safe }}>{dayCounts.length ? (daySpread > 1 ? `差 ${daySpread} 个` : '均衡') : '—'}</p>
            </div>
          </div>
          {daySpread > 1 && <div className="border-t px-3 py-2 text-[11px]" style={{ borderColor: COLORS.divider, color: COLORS.warn }}>部分日期的白天节点明显偏少。已在最终规划门禁中尝试从真实候选池补齐；仍不足时会明确标记候选池缺口。</div>}
        </DraggablePanel>

        {/* 预算累计曲线 + 节点成本条形图 */}
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
          <DraggablePanel
            className="radar-panel"
            style={panelStyle}
            icon={<TrendingUp className="w-3.5 h-3.5" style={{ color: COLORS.gold }} />}
            title={<span className="text-[13px] font-bold" style={{ color: COLORS.gold }}>多日预算累计</span>}
          >
            <div className="px-3 py-2 border-b flex items-center justify-between" style={{ borderColor: COLORS.divider }}>
              <span className="text-[11px]" style={{ color: COLORS.muted, fontFamily: RADAR_FONT.data }}>累计实际 vs 预算红线</span>
              <span className="text-xs font-mono" style={{ color: COLORS.muted, fontFamily: RADAR_FONT.data }}>
                ¥{Math.round(dailyForecast.totalSpent)} / ¥{dailyForecast.totalBudget ? Math.round(dailyForecast.totalBudget) : '—'}
              </span>
            </div>
            <div className="p-3">
              <BudgetForecastChart mode={mode} perDay={dailyForecast.perDay} dailyBudget={dailyBudget} />
            </div>
          </DraggablePanel>

          <DraggablePanel
            className="radar-panel"
            style={panelStyle}
            icon={<BarChart3 className="w-3.5 h-3.5" style={{ color: COLORS.indigo }} />}
            title={<span className="text-[13px] font-bold" style={{ color: COLORS.indigo }}>节点成本分布</span>}
          >
            <div className="px-3 py-2 border-b flex items-center justify-between" style={{ borderColor: COLORS.divider }}>
              <span className="text-[11px]" style={{ color: COLORS.muted, fontFamily: RADAR_FONT.data }}>TOP {costSortedNodes.length}</span>
              <span className="text-[11px]" style={{ color: COLORS.muted, fontFamily: RADAR_FONT.data }}>点击定位节点</span>
            </div>
            <div className="p-3">
              <CostBarChart
                mode={mode}
                nodes={costSortedNodes.map((n: any) => ({ name: n.name, cost: n.cost }))}
                maxNodeCost={maxNodeCost}
                onSelectNode={handleSelectByName}
              />
            </div>
          </DraggablePanel>
        </div>

        {/* 行程时间轴甘特图 */}
        <DraggablePanel
          className="radar-panel"
          style={panelStyle}
          icon={<Clock className="w-3.5 h-3.5" style={{ color: COLORS.purple }} />}
          title={<span className="text-[13px] font-bold" style={{ color: COLORS.purple }}>行程时间轴</span>}
        >
          <div className="px-3 py-2 border-b flex items-center justify-between" style={{ borderColor: COLORS.divider }}>
            <span className="text-[11px]" style={{ color: COLORS.muted, fontFamily: RADAR_FONT.data }}>Day × 节点甘特图</span>
            <span className="text-[11px]" style={{ color: COLORS.muted, fontFamily: RADAR_FONT.data }}>
              高=danger / 中=warn / 普=accent · 点击回 2D
            </span>
          </div>
          <div className="p-3">
            <TimelineGanttChart
              mode={mode}
              timelineDays={timelineDays}
              routes={routes}
              onSelectNode={onPoiSelect}
            />
          </div>
        </DraggablePanel>
      </div>

      {/* ============ 底部时间轴滑块 ============ */}
      <div className="px-4 sm:px-6 pb-2 shrink-0 pointer-events-auto">
        <div className="radar-panel rounded-2xl px-5 py-3" style={panelStyle}>
          <div className="flex items-center justify-between mb-2">
            <span className="text-[10px] font-bold" style={{ color: COLORS.accent }}>行程时间轴</span>
            <span className="text-[10px]" style={{ color: COLORS.muted, fontFamily: RADAR_FONT.data }}>
              {timelineDays.length > 0 ? `Day ${timelineDay} / ${timelineDays.length}` : '无数据'}
            </span>
          </div>
          <input
            type="range"
            min={1}
            max={Math.max(1, timelineDays.length)}
            value={timelineDay}
            onChange={(e) => onDayChange(Number(e.target.value))}
            aria-label="行程时间轴 Day 选择"
            className="radar-range"
          />
          <div className="flex justify-between mt-1.5">
            {timelineDays.map((d, i) => (
              <span key={i} className={`text-[10px] font-mono ${i + 1 === timelineDay ? 'font-bold' : ''}`} style={{ color: i + 1 === timelineDay ? COLORS.accent : COLORS.muted }}>
                D{d}
              </span>
            ))}
          </div>
        </div>
      </div>

      {/* ============ 底部滚动信息条 ============ */}
      <div
        className="shrink-0 h-7 flex items-center px-4 overflow-hidden"
        style={{
          background: mode === 'dark' ? 'linear-gradient(180deg, transparent, rgba(2,6,23,0.95))' : 'linear-gradient(180deg, transparent, rgba(15,23,42,0.16))',
          borderTop: `1px solid ${COLORS.divider}`,
        }}
      >
        <div className="flex items-center gap-8 animate-marquee whitespace-nowrap" style={{ animationDuration: '25s' }}>
          {[
            { text: `预算: ¥${dayTotal} / ¥${dailyBudget || '—'}`, color: hasOverrun ? COLORS.danger : COLORS.safe },
            { text: `节点数: ${routes.length}`, color: COLORS.accent },
            { text: `人均: ¥${avgCost}`, color: COLORS.accent },
            { text: `超支: ${hasOverrun ? '¥' + Math.round(overrun) : '无'}`, color: hasOverrun ? COLORS.danger : COLORS.safe },
            { text: `总预算: ¥${dailyForecast.totalBudget ? Math.round(dailyForecast.totalBudget) : '—'}`, color: COLORS.accent },
          ].map((item, i) => (
            <div key={i} className="flex items-center gap-1.5">
              <span className="text-xs font-mono" style={{ color: item.color, fontFamily: RADAR_FONT.data }}>{item.text}</span>
              <span className="text-xs mx-1" style={{ color: COLORS.muted }}>|</span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
