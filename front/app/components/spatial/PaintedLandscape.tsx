'use client';

/**
 * PaintedLandscape — 登录门户的「手绘式四季山野」场景（纯 SVG，零外部资源）
 *
 * 设计取舍：不再用 3D 网格（低多边形在远景会显得"像素化"），改为分层手绘：
 * - 四层山峦：远山 → 中山 → 近山 → 前景山脊，每层用垂直渐变（山脊实、山脚虚）
 *   + 山脊受光线描边 + 层间雾带，形成空气透视与纵深；
 * - 山脊轮廓由程序化噪声生成（锐利峰形，非平滑色带）；
 * - 具体细节：暖色日光、缓慢漂移的云、热气球、一小群飞鸟、前景灌木与亮窗小屋；
 * - 四季换色：所有山体/日光/前景颜色通过 CSS 过渡平滑切换；天空由 4 层交叉淡化；
 * - 鼠标视差：各层按深度位移；减少动态时全部静止。
 */

import { useEffect, useRef, useState } from 'react';
import { SEASONS, type Season } from '../../lib/season';

export const VISTA_SEASONS = SEASONS;
export type { Season } from '../../lib/season';

interface ScenePalette {
  far: string;
  mid: string;
  near: string;
  foreground: string;
  rim: string;
  sun: string;
  sunCore: string;
  bush: string;
  cabinBody: string;
  cabinRoof: string;
  window: string;
  bird: string;
  balloonA: string;
  balloonB: string;
  cloud: string;
}

const PALETTES: Record<Season, ScenePalette> = {
  spring: {
    far: '#eee3d2',
    mid: '#dccdb6',
    near: '#c2a98c',
    foreground: '#564835',
    rim: 'rgba(255, 250, 240, 0.6)',
    sun: 'rgba(255, 226, 176, 0.6)',
    sunCore: '#fff4dc',
    bush: '#4c3f31',
    cabinBody: '#3e3327',
    cabinRoof: '#322a20',
    window: '#ffcf8f',
    bird: 'rgba(96, 82, 64, 0.75)',
    balloonA: '#e2622f',
    balloonB: '#f7b06a',
    cloud: 'rgba(255, 252, 246, 0.55)',
  },
  summer: {
    far: '#dde6d5',
    mid: '#c4d3b6',
    near: '#96ab82',
    foreground: '#3d4a37',
    rim: 'rgba(250, 253, 246, 0.55)',
    sun: 'rgba(255, 236, 190, 0.6)',
    sunCore: '#fff8e4',
    bush: '#37422f',
    cabinBody: '#2f3927',
    cabinRoof: '#26301f',
    window: '#ffd58f',
    bird: 'rgba(84, 96, 76, 0.75)',
    balloonA: '#d95f2f',
    balloonB: '#f2b06a',
    cloud: 'rgba(250, 253, 246, 0.5)',
  },
  autumn: {
    far: '#eedcc4',
    mid: '#dcbd97',
    near: '#c2915f',
    foreground: '#4b3826',
    rim: 'rgba(255, 248, 238, 0.55)',
    sun: 'rgba(255, 215, 154, 0.62)',
    sunCore: '#fff0d0',
    bush: '#42321f',
    cabinBody: '#3a2c1c',
    cabinRoof: '#2f2415',
    window: '#ffd08a',
    bird: 'rgba(100, 78, 56, 0.75)',
    balloonA: '#c9822f',
    balloonB: '#eaa85f',
    cloud: 'rgba(255, 250, 240, 0.5)',
  },
  winter: {
    far: '#e4ebf3',
    mid: '#cfdae5',
    near: '#a7b9c9',
    foreground: '#38424d',
    rim: 'rgba(248, 251, 254, 0.6)',
    sun: 'rgba(238, 244, 251, 0.6)',
    sunCore: '#fbfdff',
    bush: '#2f3a44',
    cabinBody: '#28323b',
    cabinRoof: '#1f2730',
    window: '#ffd08a',
    bird: 'rgba(70, 84, 96, 0.75)',
    balloonA: '#c9642f',
    balloonB: '#e8a55e',
    cloud: 'rgba(248, 251, 254, 0.5)',
  },
};

/** 生成一条山脊的填充路径与脊线路径（确定性噪声，峰形锐利） */
function buildRidge(seed: number, baseY: number, amp: number, jag: number, bottom = 900) {
  const points: [number, number][] = [];
  const count = 44;
  for (let i = 0; i <= count; i += 1) {
    const x = (i / count) * 1440;
    const n =
      Math.sin(i * 0.55 + seed) * 0.55 +
      Math.sin(i * 1.7 + seed * 2.3) * 0.3 +
      Math.sin(i * 3.1 + seed * 4.7) * 0.15;
    const ridge = 1 - Math.abs(n); // 0..1，尖锐峰
    const y = baseY - ridge * amp + Math.sin(i * 2.2 + seed) * amp * jag * 0.12;
    points.push([x, Number(y.toFixed(1))]);
  }
  const line = points.map(([x, y], i) => `${i === 0 ? 'M' : 'L'}${x.toFixed(0)} ${y}`).join(' ');
  const area = `M0 ${bottom} L0 ${points[0][1]} ${points
    .slice(1)
    .map(([x, y]) => `L${x.toFixed(0)} ${y}`)
    .join(' ')} L1440 ${bottom} Z`;
  return { area, line, points };
}

const RIDGE_FAR = buildRidge(3.1, 470, 150, 0.55);
const RIDGE_MID = buildRidge(7.7, 560, 180, 0.8);
const RIDGE_NEAR = buildRidge(13.4, 660, 170, 0.9);
// 前景层向下多画 60px，视差上移时不会在画面底部露出天空
const RIDGE_FOREGROUND = buildRidge(21.2, 815, 45, 0.45, 960);

function ridgeYAt(points: [number, number][], x: number): number {
  const scale = x / 1440;
  const idx = Math.min(points.length - 1, Math.max(0, Math.round(scale * (points.length - 1))));
  return points[idx][1];
}

interface PaintedLandscapeProps {
  intervalMs?: number;
  onSeasonChange?: (season: Season) => void;
  controlledSeason?: Season;
  motionEnabled?: boolean;
  autoRotate?: boolean;
}

export default function PaintedLandscape({ intervalMs = 11000, onSeasonChange, controlledSeason, motionEnabled = true, autoRotate = true }: PaintedLandscapeProps) {
  const [localSeason, setLocalSeason] = useState<Season>('spring');
  const season = controlledSeason ?? localSeason;
  const hostRef = useRef<HTMLDivElement>(null);
  const frameRef = useRef(0);
  const callbackRef = useRef(onSeasonChange);
  const palette = PALETTES[season];

  useEffect(() => {
    callbackRef.current = onSeasonChange;
  }, [onSeasonChange]);

  useEffect(() => {
    callbackRef.current?.(season);
  }, [season]);

  // 季节轮播：页面隐藏时暂停；减少动态时停在当前季节。
  useEffect(() => {
    if (!autoRotate || controlledSeason || !motionEnabled || window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;
    let timer: number | undefined;
    const tick = () => {
      setLocalSeason((prev) => VISTA_SEASONS[(VISTA_SEASONS.indexOf(prev) + 1) % VISTA_SEASONS.length]);
    };
    const start = () => {
      timer = window.setInterval(tick, intervalMs);
    };
    const stop = () => {
      if (timer !== undefined) {
        window.clearInterval(timer);
        timer = undefined;
      }
    };
    const onVisibility = () => {
      if (document.hidden) stop();
      else {
        stop();
        start();
      }
    };
    start();
    document.addEventListener('visibilitychange', onVisibility);
    return () => {
      stop();
      document.removeEventListener('visibilitychange', onVisibility);
    };
  }, [intervalMs, autoRotate, controlledSeason, motionEnabled]);

  // 鼠标视差：写入 CSS 变量，各层按深度位移（rAF 合帧，不触发 React 渲染）。
  useEffect(() => {
    if (!motionEnabled || window.matchMedia('(prefers-reduced-motion: reduce)').matches || !window.matchMedia('(hover: hover) and (pointer: fine)').matches) return;
    const host = hostRef.current;
    if (!host) return;
    const onPointerMove = (event: PointerEvent) => {
      if (frameRef.current) cancelAnimationFrame(frameRef.current);
      frameRef.current = requestAnimationFrame(() => {
        const x = (event.clientX / window.innerWidth) * 2 - 1;
        const y = (event.clientY / window.innerHeight) * 2 - 1;
        host.style.setProperty('--px', x.toFixed(3));
        host.style.setProperty('--py', y.toFixed(3));
      });
    };
    window.addEventListener('pointermove', onPointerMove, { passive: true });
    return () => {
      window.removeEventListener('pointermove', onPointerMove);
      if (frameRef.current) cancelAnimationFrame(frameRef.current);
    };
  }, [motionEnabled]);

  const cabinX = 700;
  const cabinY = ridgeYAt(RIDGE_FOREGROUND.points, cabinX) + 2;

  return (
    <div ref={hostRef} className="vista3d painted-scene" aria-hidden="true">
      {VISTA_SEASONS.map((item) => (
        <div key={item} className="vista-sky" data-season={item} data-active={item === season ? 'true' : 'false'} />
      ))}

      <svg className="painted-svg" viewBox="0 0 1440 900" preserveAspectRatio="xMidYMax slice" focusable="false">
        <defs>
          <radialGradient id="painted-sun-halo" cx="0.5" cy="0.5" r="0.5">
            <stop offset="0%" style={{ stopColor: palette.sun, transition: 'stop-color 1.2s ease' }} />
            <stop offset="55%" style={{ stopColor: palette.sun, stopOpacity: 0.35, transition: 'stop-color 1.2s ease' }} />
            <stop offset="100%" style={{ stopColor: palette.sun, stopOpacity: 0, transition: 'stop-color 1.2s ease' }} />
          </radialGradient>
          <linearGradient id="painted-far" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" style={{ stopColor: palette.far, transition: 'stop-color 1.2s ease' }} />
            <stop offset="62%" style={{ stopColor: palette.far, stopOpacity: 0.92, transition: 'stop-color 1.2s ease' }} />
            <stop offset="100%" style={{ stopColor: palette.far, stopOpacity: 0.4, transition: 'stop-color 1.2s ease' }} />
          </linearGradient>
          <linearGradient id="painted-mid" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" style={{ stopColor: palette.mid, transition: 'stop-color 1.2s ease' }} />
            <stop offset="62%" style={{ stopColor: palette.mid, stopOpacity: 0.94, transition: 'stop-color 1.2s ease' }} />
            <stop offset="100%" style={{ stopColor: palette.mid, stopOpacity: 0.4, transition: 'stop-color 1.2s ease' }} />
          </linearGradient>
          <linearGradient id="painted-near" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" style={{ stopColor: palette.near, transition: 'stop-color 1.2s ease' }} />
            <stop offset="60%" style={{ stopColor: palette.near, stopOpacity: 0.94, transition: 'stop-color 1.2s ease' }} />
            <stop offset="100%" style={{ stopColor: palette.near, stopOpacity: 0.45, transition: 'stop-color 1.2s ease' }} />
          </linearGradient>
          <linearGradient id="painted-foreground" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" style={{ stopColor: palette.foreground, transition: 'stop-color 1.2s ease' }} />
            <stop offset="100%" style={{ stopColor: palette.foreground, stopOpacity: 0.92, transition: 'stop-color 1.2s ease' }} />
          </linearGradient>
          <linearGradient id="painted-mist" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="#fffdf8" stopOpacity="0" />
            <stop offset="42%" stopColor="#fffdf8" stopOpacity="0.5" />
            <stop offset="100%" stopColor="#fffdf8" stopOpacity="0" />
          </linearGradient>
          <radialGradient id="painted-window-glow" cx="0.5" cy="0.5" r="0.5">
            <stop offset="0%" stopColor={palette.window} stopOpacity="0.7" />
            <stop offset="100%" stopColor={palette.window} stopOpacity="0" />
          </radialGradient>
        </defs>

        {/* 日光（含蓄的暖色盘 + 柔光晕，不是满屏光斑） */}
        <g className="painted-sky-layer">
          <circle cx="862" cy="236" r="110" fill="url(#painted-sun-halo)" />
          <circle cx="862" cy="236" r="30" style={{ fill: palette.sunCore, transition: 'fill 1.2s ease' }} opacity="0.92" />
        </g>

        {/* 缓慢漂移的云 */}
        <g className="painted-clouds" fill={palette.cloud}>
          <g className="painted-cloud painted-cloud-a">
            <ellipse cx="300" cy="168" rx="90" ry="18" />
            <ellipse cx="380" cy="152" rx="64" ry="14" />
            <ellipse cx="236" cy="178" rx="50" ry="12" />
          </g>
          <g className="painted-cloud painted-cloud-b">
            <ellipse cx="640" cy="120" rx="110" ry="17" />
            <ellipse cx="720" cy="106" rx="54" ry="12" />
          </g>
          <g className="painted-cloud painted-cloud-c">
            <ellipse cx="1130" cy="190" rx="96" ry="17" />
            <ellipse cx="1200" cy="174" rx="60" ry="13" />
          </g>
        </g>

        {/* 远山 */}
        <g className="painted-layer painted-layer-far">
          <path d={RIDGE_FAR.area} fill="url(#painted-far)" />
          <path d={RIDGE_FAR.line} fill="none" strokeWidth="1.6" strokeOpacity="0.55" style={{ stroke: palette.rim, transition: 'stroke 1.2s ease' }} />
        </g>
        <rect x="0" y="470" width="1440" height="120" fill="url(#painted-mist)" opacity="0.7" />

        {/* 中山 */}
        <g className="painted-layer painted-layer-mid">
          <path d={RIDGE_MID.area} fill="url(#painted-mid)" />
          <path d={RIDGE_MID.line} fill="none" strokeWidth="1.6" strokeOpacity="0.5" style={{ stroke: palette.rim, transition: 'stroke 1.2s ease' }} />
        </g>
        <rect x="0" y="580" width="1440" height="120" fill="url(#painted-mist)" opacity="0.65" />

        {/* 近山 */}
        <g className="painted-layer painted-layer-near">
          <path d={RIDGE_NEAR.area} fill="url(#painted-near)" />
          <path d={RIDGE_NEAR.line} fill="none" strokeWidth="1.4" strokeOpacity="0.4" style={{ stroke: palette.rim, transition: 'stroke 1.2s ease' }} />
        </g>
        <rect x="0" y="660" width="1440" height="110" fill="url(#painted-mist)" opacity="0.55" />

        {/* 前景山脊 + 亮窗小屋 */}
        <g className="painted-layer painted-layer-foreground">
          <path d={RIDGE_FOREGROUND.area} fill="url(#painted-foreground)" />
          <g className="painted-cabin" transform={`translate(${cabinX} ${cabinY})`}>
            <rect x="-18" y="-19" width="36" height="19" rx="1.5" fill={palette.cabinBody} />
            <path d="M-25 -19 L0 -35 L25 -19 Z" fill={palette.cabinRoof} />
            <rect x="-4.5" y="-14" width="8" height="8" rx="1" fill={palette.window} />
            <circle cx="0" cy="-10" r="16" fill="url(#painted-window-glow)" />
          </g>
        </g>

        {/* 飞鸟 */}
        <g className="painted-birds" fill="none" stroke={palette.bird} strokeWidth="2.4" strokeLinecap="round">
          <path d="M760 330 q 8 -7 16 0 q 8 -7 16 0" />
          <path d="M812 300 q 6 -5 12 0 q 6 -5 12 0" />
          <path d="M736 372 q 5 -4 10 0 q 5 -4 10 0" />
        </g>

        {/* 热气球（旅行隐喻） */}
        <g className="painted-balloon" transform="translate(700 300)">
          <path
            d="M0 -34 C 22 -34, 30 -12, 20 4 L 8 4 C 12 -4, 12 -14, 0 -14 C -12 -14, -12 -4, -8 4 L -20 4 C -30 -12, -22 -34, 0 -34 Z"
            fill={palette.balloonA}
          />
          <path d="M0 -34 C 7 -34, 10 -12, 6 4 L -6 4 C -10 -12, -7 -34, 0 -34 Z" fill={palette.balloonB} opacity="0.85" />
          <path d="M-14 8 L -8 18 M 14 8 L 8 18" stroke={palette.cabinRoof} strokeWidth="1.6" />
          <rect x="-7" y="18" width="14" height="9" rx="2" fill={palette.cabinRoof} />
        </g>
      </svg>
    </div>
  );
}
