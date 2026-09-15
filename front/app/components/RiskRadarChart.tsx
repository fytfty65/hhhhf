'use client';

import { useState } from 'react';

export interface RadarDim {
  label: string;
  score: number;
  color: string;
  level?: string;
}

interface RiskRadarChartProps {
  dimensions: RadarDim[];
  accent?: string;
  grid?: string;
  labelColor?: string;
  tooltipBg?: string;
  tooltipText?: string;
  max?: number;
}

const DEG = Math.PI / 180;

function polar(cx: number, cy: number, r: number, angleDeg: number) {
  return {
    x: cx + r * Math.cos(angleDeg * DEG),
    y: cy + r * Math.sin(angleDeg * DEG),
  };
}

/**
 * 基于 SVG 的五维风险雷达图（替代原横向进度条）
 * - viewBox + preserveAspectRatio 保证跨屏幕尺寸显示一致
 * - 悬停顶点查看具体数值与等级
 */
export default function RiskRadarChart({
  dimensions,
  accent = '#38bdf8',
  grid = 'rgba(148,163,184,0.25)',
  labelColor = '#94a3b8',
  tooltipBg = 'rgba(8,15,30,0.96)',
  tooltipText = '#e2e8f0',
  max = 100,
}: RiskRadarChartProps) {
  const [hovered, setHovered] = useState<number | null>(null);

  const n = dimensions.length;
  const cx = 160;
  const cy = 150;
  const R = 88;
  const levels = [0.25, 0.5, 0.75, 1];

  // 角度从正上方开始，顺时针均分
  const angles = dimensions.map((_, i) => -90 + (360 / n) * i);

  const ringPolygons = levels.map((lv) =>
    angles
      .map((a) => polar(cx, cy, R * lv, a))
      .map((p) => `${p.x.toFixed(2)},${p.y.toFixed(2)}`)
      .join(' '),
  );

  const axisLines = angles.map((a) => polar(cx, cy, R, a));

  const dataPoints = dimensions.map((d, i) => {
    const clamped = Math.max(0, Math.min(max, d.score));
    return polar(cx, cy, (clamped / max) * R, angles[i]);
  });

  const dataPolygon = dataPoints.map((p) => `${p.x.toFixed(2)},${p.y.toFixed(2)}`).join(' ');

  const labelAnchor = (a: number) => {
    const c = Math.cos(a * DEG);
    if (c > 0.35) return 'start';
    if (c < -0.35) return 'end';
    return 'middle';
  };

  return (
    <div className="w-full">
      <div className="relative w-full">
        <svg
          viewBox="0 0 320 300"
          className="w-full h-auto"
          role="img"
          aria-label="五维风险雷达图"
        >
          {/* 网格环 */}
          {ringPolygons.map((pts, i) => (
            <polygon
              key={i}
              points={pts}
              fill="none"
              stroke={grid}
              strokeWidth={i === ringPolygons.length - 1 ? 1.2 : 0.8}
              opacity={i === ringPolygons.length - 1 ? 0.9 : 0.6}
            />
          ))}

          {/* 轴线 */}
          {axisLines.map((p, i) => (
            <line
              key={i}
              x1={cx}
              y1={cy}
              x2={p.x}
              y2={p.y}
              stroke={grid}
              strokeWidth="0.8"
              opacity="0.6"
            />
          ))}

          {/* 数据面 */}
          <polygon
            points={dataPolygon}
            fill={accent}
            fillOpacity="0.16"
            stroke={accent}
            strokeWidth="1.8"
            strokeLinejoin="round"
          />

          {/* 顶点与命中区 */}
          {dimensions.map((d, i) => {
            const valuePoint = dataPoints[i];
            const valueLabel = polar(cx, cy, R + 18, angles[i]);
            const nameLabel = polar(cx, cy, R + 36, angles[i]);
            const isHover = hovered === i;
            return (
              <g key={d.label}>
                {/* 命中区 */}
                <circle
                  cx={valuePoint.x}
                  cy={valuePoint.y}
                  r={isHover ? 18 : 15}
                  fill="transparent"
                  style={{ cursor: 'pointer' }}
                  onMouseEnter={() => setHovered(i)}
                  onMouseLeave={() => setHovered(null)}
                />
                <circle
                  cx={valuePoint.x}
                  cy={valuePoint.y}
                  r={isHover ? 5.5 : 4}
                  fill={d.color}
                  stroke="#fff"
                  strokeWidth="1.2"
                  style={{ transition: 'r 0.15s ease', filter: isHover ? `drop-shadow(0 0 6px ${d.color})` : 'none' }}
                />
                {/* 数值 */}
                <text
                  x={valueLabel.x}
                  y={valueLabel.y}
                  textAnchor={labelAnchor(angles[i])}
                  dominantBaseline="middle"
                  fontSize="11"
                  fontWeight="800"
                  fill={d.color}
                  fontFamily="var(--font-radar-data, monospace)"
                >
                  {Math.round(d.score)}
                </text>
                {/* 维度名 */}
                <text
                  x={nameLabel.x}
                  y={nameLabel.y}
                  textAnchor={labelAnchor(angles[i])}
                  dominantBaseline="middle"
                  fontSize="10"
                  fontWeight="600"
                  fill={isHover ? d.color : labelColor}
                  fontFamily="var(--font-radar-display, sans-serif)"
                >
                  {d.label}
                </text>
              </g>
            );
          })}
        </svg>

        {/* 悬停提示 */}
        {hovered !== null && (
          <div className="pointer-events-none absolute left-1/2 top-0 -translate-x-1/2 z-10 px-3 py-1.5 rounded-lg text-center whitespace-nowrap"
            style={{ background: tooltipBg, border: '1px solid ' + accent + '40' }}>
            <div className="flex items-center gap-2">
              <span className="w-2 h-2 rounded-full" style={{ background: dimensions[hovered].color }} />
              <span className="text-[11px] font-bold" style={{ color: tooltipText }}>{dimensions[hovered].label}</span>
              <span className="text-[11px] font-black" style={{ color: dimensions[hovered].color }}>
                {Math.round(dimensions[hovered].score)} / {max}
              </span>
              {dimensions[hovered].level && (
                <span className="text-[9px] font-bold px-1.5 py-0.5 rounded" style={{ background: dimensions[hovered].color + '22', color: dimensions[hovered].color }}>
                  {dimensions[hovered].level}
                </span>
              )}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}