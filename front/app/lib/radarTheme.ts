import { useSyncExternalStore } from 'react';
import type { CSSProperties } from 'react';

// ============================================================
// 3D 雷达统一主题（明暗双主题 + 语义）
// WorldSafetyGlobe（态势感知雷达）与 FullRouteVisualizer MACRO（行程雷达）
// 共享单一事实来源：调色板、风险/成本语义、成本解析、面板/背景/动画。
// 通过 useRadarTheme() 跟随操作系统偏好与手动 .dark 切换，自动适配明暗主题。
// ============================================================

export type RadarThemeMode = 'light' | 'dark';

export interface RadarPalette {
  bg: string;               // 根背景
  deep: string;             // 更深层背景
  panel: string;            // 玻璃面板底色
  panelSolid: string;       // 实色卡片/标签底色
  border: string;           // 面板描边（accent 系）
  borderSoft: string;       // 弱描边
  accent: string;           // 主色
  accentGlow: string;
  origin: string;           // 起点色
  originGlow: string;
  safe: string;
  warn: string;
  danger: string;
  purple: string;
  cyan: string;
  gold: string;
  indigo: string;
  slate: string;
  grid: string;
  labelBg: string;          // 3D 标签卡片底色
  muted: string;            // 次级文本
  divider: string;
}

const DARK: RadarPalette = {
  bg: '#04070f',
  deep: '#071120',
  panel: 'rgba(11, 20, 36, 0.82)',
  panelSolid: 'rgba(8, 15, 28, 0.96)',
  border: 'rgba(56, 189, 248, 0.22)',
  borderSoft: 'rgba(148, 163, 184, 0.12)',
  accent: '#38bdf8',
  accentGlow: 'rgba(56, 189, 248, 0.45)',
  origin: '#fb923c',
  originGlow: 'rgba(251, 146, 60, 0.5)',
  safe: '#10b981',
  warn: '#f59e0b',
  danger: '#f43f5e',
  purple: '#a78bfa',
  cyan: '#22d3ee',
  gold: '#fbbf24',
  indigo: '#818cf8',
  slate: '#7c8aa0',
  grid: 'rgba(56, 189, 248, 0.12)',
  labelBg: 'rgba(8, 15, 30, 0.96)',
  muted: '#94a3b8',
  divider: 'rgba(148, 163, 184, 0.12)',
};

const LIGHT: RadarPalette = {
  bg: '#f5f8fc',
  deep: '#e6eef8',
  panel: 'rgba(255, 255, 255, 0.82)',
  panelSolid: 'rgba(255, 255, 255, 0.97)',
  border: 'rgba(2, 132, 199, 0.22)',
  borderSoft: 'rgba(100, 116, 139, 0.16)',
  accent: '#0284c7',
  accentGlow: 'rgba(2, 132, 199, 0.35)',
  origin: '#ea580c',
  originGlow: 'rgba(234, 88, 12, 0.4)',
  safe: '#059669',
  warn: '#d97706',
  danger: '#e11d48',
  purple: '#7c3aed',
  cyan: '#0e7490',
  gold: '#d97706',
  indigo: '#4f46e5',
  slate: '#64748b',
  grid: 'rgba(2, 132, 199, 0.10)',
  labelBg: 'rgba(255, 255, 255, 0.97)',
  muted: '#475569',
  divider: 'rgba(100, 116, 139, 0.16)',
};

// 兼容旧引用：默认暗色
export const RADAR_COLORS = DARK;

// 雷达界面字体令牌
export const RADAR_FONT = {
  display: 'var(--font-radar-display, ui-sans-serif, system-ui, sans-serif)',
  data: 'var(--font-radar-data, ui-monospace, SFMono-Regular, Menlo, Consolas, monospace)',
} as const;

export function radarPalette(mode: RadarThemeMode): RadarPalette {
  return mode === 'dark' ? DARK : LIGHT;
}

// ---- 主题订阅：跟随 .dark class（手动切换）+ 操作系统偏好变化 ----
const themeListeners = new Set<() => void>();
let themeSubscribed = false;

function ensureThemeSubscriptions() {
  if (themeSubscribed || typeof window === 'undefined') return;
  themeSubscribed = true;
  const emit = () => themeListeners.forEach((l) => l());
  new MutationObserver(emit).observe(document.documentElement, {
    attributes: true,
    attributeFilter: ['class'],
  });
  window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', emit);
  window.addEventListener('storage', emit);
}

export function useRadarTheme(): RadarThemeMode {
  return useSyncExternalStore(
    (listener) => {
      ensureThemeSubscriptions();
      themeListeners.add(listener);
      return () => themeListeners.delete(listener);
    },
    () => (document.documentElement.classList.contains('dark') ? 'dark' : 'light'),
    () => 'dark',
  );
}

// ---- 主题相关的 3D 地球贴图 ----
export function radarGlobeImage(mode: RadarThemeMode) {
  return mode === 'dark'
    ? {
        globeImageUrl: 'https://cdn.jsdelivr.net/npm/three-globe@2.31.1/example/img/earth-dark.jpg',
        bumpImageUrl: 'https://cdn.jsdelivr.net/npm/three-globe@2.31.1/example/img/earth-topology.png',
        atmosphereColor: '#7dd3fc',
      }
    : {
        globeImageUrl: 'https://cdn.jsdelivr.net/npm/three-globe@2.31.1/example/img/earth-blue-marble.jpg',
        bumpImageUrl: 'https://cdn.jsdelivr.net/npm/three-globe@2.31.1/example/img/earth-topology.png',
        atmosphereColor: '#93c5fd',
      };
}

// ---- 风险等级语义（CII 综合影响指数阈值） ----
export const CII_HIGH_THRESHOLD = 40;
export const CII_WARN_THRESHOLD = 20;

export interface RiskProfile {
  label: string;
  color: string;
  text: string;
  ring: string;
  bg: string;
}

export function riskProfile(ciiScore: number, palette: RadarPalette = DARK): RiskProfile {
  if (ciiScore >= CII_HIGH_THRESHOLD) {
    return {
      label: '高危严碍',
      color: palette.danger,
      text: 'text-rose-500 dark:text-rose-400',
      ring: 'rgba(244, 63, 94, 0.7)',
      bg: 'rgba(244, 63, 94, 0.12)',
    };
  }
  if (ciiScore >= CII_WARN_THRESHOLD) {
    return {
      label: '中度预警',
      color: palette.warn,
      text: 'text-amber-500 dark:text-amber-400',
      ring: 'rgba(245, 158, 11, 0.7)',
      bg: 'rgba(245, 158, 11, 0.14)',
    };
  }
  return {
    label: '安全区间',
    color: palette.safe,
    text: 'text-emerald-600 dark:text-emerald-400',
    ring: 'rgba(16, 185, 129, 0.6)',
    bg: 'rgba(16, 185, 129, 0.12)',
  };
}

// ---- 成本分层语义（两个雷达共用同一阈值） ----
export const COST_HIGH_THRESHOLD = 300;
export const COST_WARN_THRESHOLD = 150;

export type CostTier = 'high' | 'medium' | 'normal';

export function costTierOf(cost: number): CostTier {
  if (cost > COST_HIGH_THRESHOLD) return 'high';
  if (cost > COST_WARN_THRESHOLD) return 'medium';
  return 'normal';
}

export function costColor(cost: number, palette: RadarPalette = DARK): string {
  switch (costTierOf(cost)) {
    case 'high':
      return palette.danger;
    case 'medium':
      return palette.warn;
    default:
      return palette.accent;
  }
}

// 成本解析：对齐后端 receipt.go 多格式（¥/￥/$/元/千分位）
export function parseCostNumber(costStr?: string): number {
  if (!costStr) return 0;
  const s = String(costStr);
  const y = s.match(/(?:¥|￥|\$)\s*([\d]+(?:[,.]\d+)*)/) || s.match(/([\d]+(?:[,.]\d+)*)\s*(?:元|RMB|CNY)/i);
  if (y) return parseFloat(y[1].replace(/,/g, ''));
  const n = s.match(/[\d]+(?:[,.]\d+)*/);
  return n ? parseFloat(n[0].replace(/,/g, '')) : 0;
}

// 面板统一样式（随主题）
export function radarPanelStyle(mode: RadarThemeMode): CSSProperties {
  const p = radarPalette(mode);
  return mode === 'dark'
    ? {
        background: p.panel,
        border: `1px solid ${p.border}`,
        borderRadius: '16px',
        backdropFilter: 'blur(26px) saturate(140%)',
        boxShadow: '0 20px 50px -12px rgba(0,0,0,0.6), 0 0 0 1px rgba(56,189,248,0.04), inset 0 1px 0 rgba(255,255,255,0.06)',
      }
    : {
        background: p.panel,
        border: `1px solid ${p.border}`,
        borderRadius: '16px',
        backdropFilter: 'blur(26px) saturate(140%)',
        boxShadow: '0 20px 50px -12px rgba(15,23,42,0.18), 0 0 0 1px rgba(2,132,199,0.04), inset 0 1px 0 rgba(255,255,255,0.85)',
      };
}

// 深空/浅空背景径向渐变
export function radarBackground(mode: RadarThemeMode): string {
  return mode === 'dark'
    ? 'radial-gradient(ellipse 80% 60% at 30% 35%, #0d2040 0%, #081226 48%, #04070f 100%)'
    : 'radial-gradient(ellipse 80% 60% at 30% 35%, #dbe7f5 0%, #edf3fa 48%, #f5f8fc 100%)';
}

// 共享动画与 HUD CSS（随主题）
export function radarSharedCss(mode: RadarThemeMode): string {
  const p = radarPalette(mode);
  const scanline = mode === 'dark' ? 'rgba(125,200,255,0.032)' : 'rgba(2,132,199,0.06)';
  const vignette = mode === 'dark' ? 'rgba(2,6,18,0.75)' : 'rgba(15,23,42,0.16)';
  const thumbBorder = mode === 'dark' ? '#071120' : '#ffffff';

  return `
  @keyframes marquee { 0% { transform: translateX(0); } 100% { transform: translateX(-50%); } }
  .animate-marquee { animation: marquee 36s linear infinite; }

  /* HUD 玻璃面板：对角高亮括角 */
  /* Do not set position here: callers use absolute/fixed placement for HUD panels.
     The pseudo-corner overlays anchor to the caller's existing positioning context. */
  .radar-panel::before {
    content: ''; position: absolute; top: -1px; left: -1px; width: 16px; height: 16px;
    border-top: 2px solid ${p.accent};
    border-left: 2px solid ${p.accent};
    border-top-left-radius: 6px; opacity: .85; pointer-events: none;
  }
  .radar-panel::after {
    content: ''; position: absolute; bottom: -1px; right: -1px; width: 16px; height: 16px;
    border-bottom: 2px solid ${p.accent};
    border-right: 2px solid ${p.accent};
    border-bottom-right-radius: 6px; opacity: .85; pointer-events: none;
  }

  /* 暗角 + 漂移扫描线 + 旋转雷达扫掠 */
  .radar-vignette { background: radial-gradient(ellipse at center, transparent 52%, ${vignette} 100%); }
  .radar-scanlines {
    background-image: repeating-linear-gradient(0deg, ${scanline} 0px, ${scanline} 1px, transparent 1px, transparent 3px);
    animation: radar-scan-drift 10s linear infinite;
  }
  @keyframes radar-scan-drift { 0% { background-position: 0 0; } 100% { background-position: 0 6px; } }
  .radar-sweep {
    background: conic-gradient(from 0deg, ${p.accent}00, ${p.accent}0e 14%, transparent 30%);
    animation: radar-sweep 6s linear infinite;
  }
  @keyframes radar-sweep { to { transform: rotate(360deg); } }

  /* 点标记脉冲与悬停 */
  @keyframes ws-pulse-ring { 0% { transform: scale(0.5); opacity: 0.5; } 100% { transform: scale(2.8); opacity: 0; } }
  @keyframes ws-pillar-glow { 0% { opacity: .3; transform: translateX(-50%) scaleY(.8); } 50% { opacity: .7; transform: translateX(-50%) scaleY(1.2); } 100% { opacity: .3; transform: translateX(-50%) scaleY(.8); } }
  @keyframes pulse-ring-macro { 0% { transform: scale(0.6); opacity: .5; } 100% { transform: scale(2.5); opacity: 0; } }
  .ws-globe-point:hover .ws-point-card { opacity: 1 !important; transform: translateX(-50%) translateY(0) !important; }
  .ws-globe-point:hover .ws-point-dot { transform: scale(1.4); }
  .macro-point:hover .macro-point-card { opacity: 1 !important; transform: translateX(-50%) translateY(0) !important; pointer-events: auto !important; }
  .macro-point:hover .macro-point-dot { transform: scale(1.4); }

  /* 时间轴滑块 */
  .radar-range {
    -webkit-appearance: none; appearance: none; width: 100%; height: 4px; border-radius: 999px;
    background: linear-gradient(90deg, ${p.accent}b3, ${p.indigo}59); outline: none; cursor: pointer;
  }
  .radar-range::-webkit-slider-thumb {
    -webkit-appearance: none; appearance: none; width: 16px; height: 16px; border-radius: 50%;
    background: ${p.accent}; border: 2px solid ${thumbBorder};
    box-shadow: 0 0 0 3px ${p.accent}40, 0 0 14px ${p.accent}e6;
  }
  .radar-range::-moz-range-thumb {
    width: 16px; height: 16px; border-radius: 50%;
    background: ${p.accent}; border: 2px solid ${thumbBorder};
    box-shadow: 0 0 0 3px ${p.accent}40, 0 0 14px ${p.accent}e6;
  }

  /* 可拖拽面板手柄 */
  .radar-drag-handle { cursor: grab; touch-action: none; user-select: none; }
  .radar-drag-handle:active { cursor: grabbing; }
  `;
}
