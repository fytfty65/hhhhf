'use client';

import { useMemo, useState } from 'react';
import { RadarThemeMode, radarPalette, RADAR_FONT } from '../../lib/radarTheme';

export interface ForecastDay {
  day: number;
  cost: number;
  over: boolean;
}

interface BudgetForecastChartProps {
  mode: RadarThemeMode;
  perDay: ForecastDay[];
  dailyBudget: number;
}

const fmt = (n: number) => `¥${Math.round(n).toLocaleString()}`;
const truncate = (s: string, n: number) => (s.length > n ? s.slice(0, n) + '…' : s);

/**
 * 多日预算累计曲线（SVG）：
 * 实线 = 逐日累计实际花费；虚线 = 累计预算红线（dailyBudget × Day）。
 * 超预算的 Day 用红色高亮（点位 + 超出量竖条）；悬停显示明细。
 */
export default function BudgetForecastChart({ mode, perDay, dailyBudget }: BudgetForecastChartProps) {
  const COLORS = radarPalette(mode);
  const [hover, setHover] = useState<number | null>(null);

  const W = 640;
  const H = 240;
  const padL = 56;
  const padR = 18;
  const padT = 18;
  const padB = 28;
  const innerW = W - padL - padR;
  const innerH = H - padT - padB;

  const points = useMemo(() => {
    let cum = 0;
    return perDay.map((d, i) => {
      cum += d.cost;
      return {
        day: d.day,
        cost: d.cost,
        cum,
        budgetCum: dailyBudget > 0 ? dailyBudget * (i + 1) : null,
        over: d.over,
      };
    });
  }, [perDay, dailyBudget]);

  const maxVal = useMemo(() => {
    let m = 1;
    points.forEach((p) => {
      m = Math.max(m, p.cum, p.budgetCum ?? 0);
    });
    return Math.max(100, Math.ceil((m * 1.18) / 100) * 100);
  }, [points]);

  // 仅 dailyBudget > 0 时展示预算红线
  const hasBudget = dailyBudget > 0;

  const xFor = (i: number) =>
    points.length <= 1 ? padL + innerW / 2 : padL + (i / (points.length - 1)) * innerW;
  const yFor = (v: number) => padT + innerH - (v / (maxVal || 1)) * innerH;

  if (points.length === 0) {
    return (
      <div className="h-[220px] flex items-center justify-center text-[11px]" style={{ color: COLORS.muted }}>
        暂无多日预算数据
      </div>
    );
  }

  const linePts = points.map((p, i) => `${xFor(i)},${yFor(p.cum)}`).join(' ');
  const areaPath = `M ${xFor(0)} ${padT + innerH} ${points.map((p, i) => `L ${xFor(i)} ${yFor(p.cum)}`).join(' ')} L ${xFor(points.length - 1)} ${padT + innerH} Z`;
  const budgetPath = hasBudget
    ? points.map((p, i) => `${i === 0 ? 'M' : 'L'} ${xFor(i)} ${yFor(p.budgetCum!)}`).join(' ')
    : '';

  const yTicks = [0.25, 0.5, 0.75, 1];

  return (
    <svg
      viewBox={`0 0 ${W} ${H}`}
      preserveAspectRatio="xMidYMid meet"
      className="w-full h-auto"
      onMouseLeave={() => setHover(null)}
      role="img"
      aria-label="多日预算累计曲线"
    >
      <defs>
        <linearGradient id={`bfc-area-${mode}`} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor={COLORS.accent} stopOpacity="0.32" />
          <stop offset="100%" stopColor={COLORS.accent} stopOpacity="0.02" />
        </linearGradient>
      </defs>

      {/* 水平网格 + Y 轴刻度 */}
      {yTicks.map((t) => {
        const v = maxVal * t;
        const y = yFor(v);
        return (
          <g key={t}>
            <line x1={padL} y1={y} x2={W - padR} y2={y} stroke={COLORS.divider} strokeWidth="1" />
            <text x={padL - 8} y={y + 3} textAnchor="end" fontSize="9" fill={COLORS.muted} style={{ fontFamily: RADAR_FONT.data }}>
              {Math.round(v)}
            </text>
          </g>
        );
      })}

      {/* X 轴 Day 刻度 */}
      {points.map((p, i) => (
        <text key={`x-${i}`} x={xFor(i)} y={H - 8} textAnchor="middle" fontSize="9" fill={p.over ? COLORS.danger : COLORS.muted} style={{ fontFamily: RADAR_FONT.data }}>
          D{p.day}
        </text>
      ))}

      {/* 实际累计面积 + 折线（分段着色，超支段红色） */}
      <path d={areaPath} fill={`url(#bfc-area-${mode})`} stroke="none" />
      {points.length > 1 &&
        points.slice(1).map((p, i) => (
          <line
            key={`seg-${i}`}
            x1={xFor(i)}
            y1={yFor(points[i].cum)}
            x2={xFor(i + 1)}
            y2={yFor(p.cum)}
            stroke={p.over ? COLORS.danger : COLORS.accent}
            strokeWidth="2.2"
            strokeLinecap="round"
          />
        ))}

      {/* 累计预算红线（虚线） */}
      {hasBudget && (
        <path
          d={budgetPath}
          fill="none"
          stroke={COLORS.gold}
          strokeWidth="1.8"
          strokeDasharray="5 5"
          strokeLinecap="round"
          opacity="0.85"
        />
      )}

      {/* 超支量竖条（预算线 → 实际累计线之间的缺口） */}
      {hasBudget &&
        points.filter((p) => p.over && p.budgetCum !== null).map((p, i) => {
          const topY = yFor(p.cum);
          const botY = yFor(p.budgetCum!);
          const x = xFor(i);
          const bw = Math.max(6, Math.min(16, innerW / Math.max(1, points.length) * 0.5));
          return (
            <g key={`over-${i}`}>
              <rect x={x - bw / 2} y={topY} width={bw} height={Math.max(2, botY - topY)} rx="2" fill={COLORS.danger} opacity="0.22" />
              <rect x={x - bw / 2} y={topY} width={bw} height={Math.max(2, botY - topY)} rx="2" fill="none" stroke={COLORS.danger} opacity="0.55" strokeWidth="1" />
            </g>
          );
        })}

      {/* 数据点（透明命中区 + 圆点） */}
      {points.map((p, i) => {
        const cx = xFor(i);
        const cy = yFor(p.cum);
        return (
          <g key={`pt-${i}`}>
            <circle
              cx={cx}
              cy={cy}
              r="10"
              fill="transparent"
              style={{ cursor: 'pointer' }}
              onMouseEnter={() => setHover(i)}
            />
            <circle cx={cx} cy={cy} r="3.5" fill={p.over ? COLORS.danger : COLORS.accent} stroke={mode === 'dark' ? COLORS.bg : '#fff'} strokeWidth="1.5" />
          </g>
        );
      })}

      {/* 悬停 Tooltip */}
      {hover !== null && points[hover] && (() => {
        const p = points[hover];
        const cx = xFor(hover);
        const cy = yFor(p.cum);
        const diff = p.budgetCum !== null ? p.cum - p.budgetCum : 0;
        const boxW = 150;
        const boxH = hasBudget ? 70 : 44;
        let bx = cx - boxW / 2;
        bx = Math.max(padL, Math.min(W - padR - boxW, bx));
        const by = 4;
        const lines = [
          { t: `Day ${p.day}`, c: COLORS.accent },
          { t: `累计实际 ${fmt(p.cum)}`, c: p.over ? COLORS.danger : COLORS.accent },
        ];
        if (hasBudget) {
          lines.push({ t: `累计预算 ${fmt(p.budgetCum!)}`, c: COLORS.gold });
          lines.push({ t: `差额 ${diff >= 0 ? '+' : ''}${fmt(diff)}`, c: diff >= 0 ? COLORS.danger : COLORS.safe });
        }
        return (
          <g style={{ pointerEvents: 'none' }}>
            <rect x={bx} y={by} width={boxW} height={boxH} rx="6" fill={COLORS.labelBg} stroke={COLORS.border} strokeWidth="1" />
            {lines.map((l, li) => (
              <text key={li} x={bx + 8} y={by + 16 + li * 14} fontSize="9" fill={l.c} style={{ fontFamily: RADAR_FONT.data }}>
                {truncate(l.t, 22)}
              </text>
            ))}
            <line x1={cx} y1={by + boxH} x2={cx} y2={cy - 4} stroke={COLORS.muted} strokeWidth="1" strokeDasharray="2 2" />
          </g>
        );
      })()}

      {/* 图例 */}
      <g>
        <rect x={W - padR - 120} y={4} width="120" height="0" />
        <circle cx={W - padR - 108} cy={10} r="3" fill={COLORS.accent} />
        <text x={W - padR - 100} y={13} fontSize="9" fill={COLORS.muted} style={{ fontFamily: RADAR_FONT.data }}>累计实际</text>
        {hasBudget && (
          <g>
            <line x1={W - padR - 52} y1={10} x2={W - padR - 34} y2={10} stroke={COLORS.gold} strokeWidth="1.8" strokeDasharray="3 3" />
            <text x={W - padR - 30} y={13} fontSize="9" fill={COLORS.muted} style={{ fontFamily: RADAR_FONT.data }}>累计预算</text>
          </g>
        )}
      </g>
    </svg>
  );
}