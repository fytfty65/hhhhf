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

// 雷达调色板与全站「暖纸文档风」同源：accent 取陶土橙（暗色 #ea580c），
// 表面取暖纸族（--paper/--ink 同族色值），彩色仅保留 good/warn/danger 语义。
// purple/cyan/gold/indigo/slate 槽位保留是为了不破坏既有引用，
// 但其值已收敛为中性/橙系——禁止作为装饰性彩虹色重新引入。
const DARK: RadarPalette = {
  bg: '#0d0b09',
  deep: '#151210',
  panel: 'rgba(28, 25, 23, 0.85)',
  panelSolid: 'rgba(23, 20, 18, 0.96)',
  border: 'rgba(234, 88, 12, 0.28)',
  borderSoft: 'rgba(214, 207, 196, 0.12)',
  accent: '#ea580c',
  accentGlow: 'rgba(234, 88, 12, 0.4)',
  origin: '#fb923c',
  originGlow: 'rgba(251, 146, 60, 0.5)',
  safe: '#4ade80',
  warn: '#facc15',
  danger: '#fb7185',
  purple: '#a8a29e',
  cyan: '#fb923c',
  gold: '#facc15',
  indigo: '#d6d3d1',
  slate: '#78716c',
  grid: 'rgba(234, 88, 12, 0.10)',
  labelBg: 'rgba(23, 20, 18, 0.96)',
  muted: '#a8a29e',
  divider: 'rgba(214, 207, 196, 0.12)',
};

const LIGHT: RadarPalette = {
  bg: '#f6f2ea',
  deep: '#ece5d8',
  panel: 'rgba(255, 253, 248, 0.85)',
  panelSolid: 'rgba(255, 253, 248, 0.97)',
  border: 'rgba(194, 65, 12, 0.25)',
  borderSoft: 'rgba(120, 83, 45, 0.16)',
  accent: '#c2410c',
  accentGlow: 'rgba(194, 65, 12, 0.35)',
  origin: '#ea580c',
  originGlow: 'rgba(234, 88, 12, 0.4)',
  safe: '#15803d',
  warn: '#a16207',
  danger: '#be123c',
  purple: '#57534e',
  cyan: '#9a3412',
  gold: '#a16207',
  indigo: '#44403c',
  slate: '#78716c',
  grid: 'rgba(120, 83, 45, 0.10)',
  labelBg: 'rgba(255, 253, 248, 0.97)',
  muted: '#57534e',
  divider: 'rgba(120, 83, 45, 0.14)',
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
        background: 'rgba(28, 25, 23, 0.94)',
        border: `1px solid ${p.borderSoft}`,
        borderRadius: '14px',
        backdropFilter: 'blur(12px)',
        boxShadow: '0 12px 32px -16px rgba(0,0,0,0.55)',
      }
    : {
        background: 'rgba(255, 253, 248, 0.97)',
        border: `1px solid ${p.borderSoft}`,
        borderRadius: '14px',
        backdropFilter: 'blur(12px)',
        boxShadow: '0 12px 32px -16px rgba(67,45,25,0.18)',
      };
}

// 深空/浅空背景径向渐变
export function radarBackground(mode: RadarThemeMode): string {
  return mode === 'dark'
    ? '#171310'
    : '#f5f1e9';
}

// 共享动画与地图层 CSS（随主题）。
// 这里刻意不使用“雷达角标 / 扫描线 / 强发光”作为默认装饰，
// 让风险信息像产品页面，而不是工程监控台。
export function radarSharedCss(mode: RadarThemeMode): string {
  const p = radarPalette(mode);
  const scanline = mode === 'dark' ? 'rgba(255,255,255,0.012)' : 'rgba(120,83,45,0.018)';
  const vignette = mode === 'dark' ? 'rgba(20,16,12,0.34)' : 'rgba(120,83,45,0.08)';
  const thumbBorder = mode === 'dark' ? '#071120' : '#ffffff';

  return `
  @keyframes marquee { 0% { transform: translateX(0); } 100% { transform: translateX(-50%); } }
  .animate-marquee { animation: marquee 36s linear infinite; }

  /* 面板保留极轻的状态提示线，不再使用科幻 HUD 对角角标。 */
  .radar-panel::before {
    content: ''; position: absolute; top: 0; left: 16px; right: 16px; height: 1px;
    background: linear-gradient(90deg, transparent, ${p.accent}55, transparent);
    opacity: .65; pointer-events: none;
  }
  .radar-panel::after {
    content: none;
  }

  /* 轻量暗角；默认不铺扫描线，避免“AI 控制台”视觉噪音。 */
  .radar-vignette { background: radial-gradient(ellipse at center, transparent 52%, ${vignette} 100%); }
  .radar-scanlines {
    background-image: linear-gradient(180deg, ${scanline}, transparent 45%, ${scanline});
    animation: none;
  }
  @keyframes radar-scan-drift { from { opacity: .7; } to { opacity: 1; } }
  .radar-sweep {
    background: conic-gradient(from 0deg, ${p.accent}00, ${p.accent}05 14%, transparent 30%);
    animation: none;
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
    box-shadow: 0 0 0 2px ${p.accent}28;
  }
  .radar-range::-moz-range-thumb {
    width: 16px; height: 16px; border-radius: 50%;
    background: ${p.accent}; border: 2px solid ${thumbBorder};
    box-shadow: 0 0 0 2px ${p.accent}28;
  }

  /* 可拖拽面板手柄 */
  .radar-drag-handle { cursor: grab; touch-action: none; user-select: none; }
  .radar-drag-handle:active { cursor: grabbing; }
  `;
}
