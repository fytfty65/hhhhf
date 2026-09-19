'use client';

/**
 * RouteSchematicMap — 一块底图瓦片都画不出来时的**离线示意图**。
 *
 * 设计取向与核对面板一致（刻意"去 AI 化"）：
 * - 报表排印：细线、等宽数字、小字号标签；只用既有色板（slate/orange/emerald），不用渐变、不用 emoji；
 * - **不冒充真实地图**：写清"相对位置示意图"并注明底图不可用；
 * - 不依赖任何网络请求（纯 SVG），所以瓦片被墙/超时/浏览器取不到外网时仍然可用。
 *
 * 版面：说明做成**左上角一块小卡片**，位置压在地图顶部控制条（卫星图/实时路况/3D 雷达）
 * 之下 —— 实测放在顶部横条会被控制条压住，放在底部横条会被行程节点卡片条压住。
 */

import React from 'react';
import { projectSchematic, projectSequence, type SchematicInput } from '../lib/routeSchematic';

type Props = {
  /** 方案的**全部**节点（含缺坐标的），组件自己决定用哪种画法 */
  routes?: readonly SchematicInput[] | null;
  className?: string;
};

const DAY_COLORS = ['#f97316', '#0ea5e9', '#10b981', '#8b5cf6', '#f43f5e', '#eab308'];

export default function RouteSchematicMap({ routes, className = '' }: Props) {
  const geographic = React.useMemo(() => projectSchematic(routes), [routes]);
  const sequence = React.useMemo(() => projectSequence(routes), [routes]);
  const hasAnyNode = sequence.plotted > 0;
  // 有坐标就画相对位置图；只有坐标缺失时才退到"顺序图"（并且标题写明，不冒充地理信息）
  const view = geographic.hasCoordinates ? geographic : sequence;
  const isSequence = !geographic.hasCoordinates && hasAnyNode;

  const days = React.useMemo(
    () => Array.from(new Set(view.points.map((point) => point.day))).sort((a, b) => a - b),
    [view.points],
  );

  return (
    <div
      data-testid="route-schematic"
      className={`absolute inset-0 z-10 bg-slate-100 ${className}`}
      aria-label={isSequence ? '行程顺序示意图' : '路线相对位置示意图'}
    >
      <svg
        viewBox="0 0 640 420"
        preserveAspectRatio="xMidYMid slice"
        className="absolute inset-0 h-full w-full"
        role="img"
        aria-label={`${isSequence ? '行程顺序示意图' : '路线示意图'}，共 ${view.plotted} 个节点`}
      >
        <defs>
          <pattern id="schematic-grid" width="40" height="40" patternUnits="userSpaceOnUse">
            <path d="M 40 0 L 0 0 0 40" fill="none" stroke="#cbd5e1" strokeWidth="0.5" />
          </pattern>
        </defs>
        <rect width="640" height="420" fill="url(#schematic-grid)" />

        {view.points.length > 1 && (
          <polyline
            points={view.polyline}
            fill="none"
            stroke="#94a3b8"
            strokeWidth="1.5"
            strokeDasharray="4 4"
          />
        )}

        {view.points.map((point) => {
          const color = DAY_COLORS[(point.day - 1) % DAY_COLORS.length];
          return (
            <g key={`${point.name}-${point.index}`}>
              <circle cx={point.x} cy={point.y} r="9" fill={color} opacity="0.18" />
              <circle cx={point.x} cy={point.y} r="5" fill={color} />
              <text x={point.x + 11} y={point.y + 4} fontSize="11" fontWeight="700" fill="#334155">
                {point.index + 1}. {point.name}
              </text>
              <text x={point.x + 11} y={point.y + 17} fontSize="9" fill="#94a3b8">
                D{point.day}
              </text>
            </g>
          );
        })}
      </svg>

      {/* 说明卡片：压在地图顶部控制条之下（top-[13.5rem]），避开左上角那一列按钮 */}
      <div className="absolute left-4 top-[13.5rem] z-20 max-w-[calc(100%-2rem)] rounded-xl border border-slate-200 bg-white/92 px-3 py-2 shadow-sm backdrop-blur">
        <div className="flex items-baseline gap-2">
          <span className="text-[11px] font-bold tracking-wide text-slate-700">
            {isSequence ? '行程顺序示意图' : '路线相对位置示意图'}
          </span>
          {view.plotted > 0 && (
            <span className="font-mono text-[10px] font-bold tabular-nums text-slate-500">
              {view.plotted} 个节点
            </span>
          )}
        </div>
        <p className="mt-0.5 text-[10px] leading-relaxed text-slate-400">
          {isSequence
            ? '底图暂时取不到、这些节点也没有坐标 · 按行程顺序排列，不代表地理位置'
            : '底图暂时取不到 · 按经纬度相对位置绘制，不是真实地图'}
        </p>
        <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1">
          {days.map((day) => (
            <span key={day} className="flex items-center gap-1 text-[10px] font-bold text-slate-500">
              <span
                className="inline-block h-2 w-2 rounded-full"
                style={{ backgroundColor: DAY_COLORS[(day - 1) % DAY_COLORS.length] }}
              />
              D{day}
            </span>
          ))}
          {geographic.skipped.length > 0 && (
            <span className="text-[10px] text-amber-600">
              缺坐标没定位：{geographic.skipped.slice(0, 4).join('、')}
              {geographic.skipped.length > 4 ? ` 等 ${geographic.skipped.length} 个` : ''}
            </span>
          )}
        </div>
      </div>

      {!hasAnyNode && (
        <div className="absolute inset-0 flex items-center justify-center">
          <div className="rounded-xl border border-slate-200 bg-white/92 px-4 py-3 text-center shadow-sm backdrop-blur">
            <p className="text-xs font-bold text-slate-600">这一版方案还没有可画的行程节点</p>
            <p className="mt-1 text-[11px] text-slate-400">左栏仍可逐个查看地点详情；底图恢复后地图会自动回来</p>
          </div>
        </div>
      )}
    </div>
  );
}
