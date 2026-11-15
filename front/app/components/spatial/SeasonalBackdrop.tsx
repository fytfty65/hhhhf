'use client';

/**
 * SeasonalBackdrop — 登录门户的程序化四季山河背景（纯 SVG / CSS，零外部资源）
 *
 * 灵感来自「四季连续变化」的沉浸式首屏：整幅场景由天空渐变 + 三层山峦 + 日光
 * + 飞鸟/热气球 + 季节粒子构成，春樱 → 夏绿 → 秋金 → 冬雪 交叉淡化循环，
 * 用结构化的画面代替大面积模糊光斑（不再有"一团光晕"）。
 *
 * 工程约束：
 * - 不请求任何外部图片/字体（CSP 白名单零改动，沙箱/离线环境同样可渲染）；
 * - 季节色板全部用 CSS 变量喂给 SVG，四个图层共用同一份路径数据；
 * - prefers-reduced-motion：不自动轮播、不播放粒子与漂移（停在当前季节）；
 * - 页面隐藏时暂停轮播，避免后台无谓重绘。
 */

import { useEffect, useRef, useState } from 'react';

export const SEASONS = ['spring', 'summer', 'autumn', 'winter'] as const;
export type Season = (typeof SEASONS)[number];

/** 三层山峦的共享路径（1440×900 视图），四季节只换颜色不换形 */
const MOUNTAIN = {
  far: 'M0 636 C 170 560, 330 596, 500 516 C 660 442, 800 560, 980 504 C 1140 454, 1300 548, 1440 492 L 1440 900 L 0 900 Z',
  mid: 'M0 726 C 210 640, 380 716, 570 636 C 760 556, 920 700, 1100 620 C 1230 562, 1340 664, 1440 606 L 1440 900 L 0 900 Z',
  near: 'M0 806 C 250 736, 440 802, 660 738 C 880 674, 1060 782, 1260 730 C 1350 706, 1400 724, 1440 712 L 1440 900 L 0 900 Z',
};

type Particle = { left: string; size: number; dur: string; delay: string; sway: string };

/** 每个季节的飘落粒子（夏天留白，画面更安静） */
const PARTICLES: Record<Season, Particle[]> = {
  spring: [
    { left: '12%', size: 7, dur: '26s', delay: '0s', sway: '-4vw' },
    { left: '28%', size: 5, dur: '31s', delay: '7s', sway: '-6vw' },
    { left: '44%', size: 6, dur: '29s', delay: '3s', sway: '4vw' },
    { left: '58%', size: 5, dur: '34s', delay: '12s', sway: '-3vw' },
    { left: '72%', size: 7, dur: '27s', delay: '5s', sway: '5vw' },
    { left: '86%', size: 5, dur: '32s', delay: '9s', sway: '-5vw' },
  ],
  summer: [],
  autumn: [
    { left: '10%', size: 8, dur: '24s', delay: '2s', sway: '5vw' },
    { left: '26%', size: 6, dur: '30s', delay: '8s', sway: '-4vw' },
    { left: '46%', size: 7, dur: '27s', delay: '4s', sway: '6vw' },
    { left: '64%', size: 6, dur: '33s', delay: '11s', sway: '-5vw' },
    { left: '80%', size: 8, dur: '25s', delay: '6s', sway: '4vw' },
  ],
  winter: [
    { left: '8%', size: 4, dur: '30s', delay: '0s', sway: '-2vw' },
    { left: '22%', size: 5, dur: '36s', delay: '6s', sway: '3vw' },
    { left: '38%', size: 4, dur: '32s', delay: '2s', sway: '-3vw' },
    { left: '52%', size: 5, dur: '38s', delay: '9s', sway: '2vw' },
    { left: '66%', size: 4, dur: '34s', delay: '4s', sway: '-2vw' },
    { left: '78%', size: 5, dur: '31s', delay: '11s', sway: '3vw' },
    { left: '90%', size: 4, dur: '37s', delay: '7s', sway: '-3vw' },
  ],
};

const PARTICLE_KIND: Record<Season, 'petal' | 'leaf' | 'snow' | 'none'> = {
  spring: 'petal',
  summer: 'none',
  autumn: 'leaf',
  winter: 'snow',
};

interface SeasonalBackdropProps {
  /** 季节轮播间隔（毫秒），默认 11s */
  intervalMs?: number;
  /** 季节切换回调（用于页面上的季节文案） */
  onSeasonChange?: (season: Season) => void;
}

export default function SeasonalBackdrop({ intervalMs = 11000, onSeasonChange }: SeasonalBackdropProps) {
  const [season, setSeason] = useState<Season>('spring');
  const callbackRef = useRef(onSeasonChange);

  useEffect(() => {
    callbackRef.current = onSeasonChange;
  }, [onSeasonChange]);

  // 自动轮播：页面隐藏时暂停；减少动态时完全不轮播。
  useEffect(() => {
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;
    let timer: number | undefined;
    const tick = () => {
      setSeason((prev) => SEASONS[(SEASONS.indexOf(prev) + 1) % SEASONS.length]);
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
  }, [intervalMs]);

  useEffect(() => {
    callbackRef.current?.(season);
  }, [season]);

  return (
    <div className="seasonal-backdrop" aria-hidden="true">
      {SEASONS.map((item) => (
        <div
          key={item}
          className="season-layer"
          data-season={item}
          data-active={item === season ? 'true' : 'false'}
        >
          <svg className="season-svg" viewBox="0 0 1440 900" preserveAspectRatio="xMidYMid slice" focusable="false">
            <defs>
              <radialGradient id={`sun-${item}`} cx="0.5" cy="0.5" r="0.5">
                <stop offset="0%" stopColor="var(--sun-strong)" />
                <stop offset="42%" stopColor="var(--sun)" stopOpacity="0.5" />
                <stop offset="100%" stopColor="var(--sun)" stopOpacity="0" />
              </radialGradient>
            </defs>
            <circle cx="1096" cy="252" r="180" fill={`url(#sun-${item})`} />
            <circle cx="1096" cy="252" r="34" fill="var(--sun-core)" opacity="0.9" />
            <path d={MOUNTAIN.far} fill="var(--far)" />
            <path d={MOUNTAIN.mid} fill="var(--mid)" />
            <path d={MOUNTAIN.near} fill="var(--near)" />
          </svg>
          {PARTICLES[item].length > 0 && (
            <div className="season-particles" data-kind={PARTICLE_KIND[item]}>
              {PARTICLES[item].map((particle) => (
                <span
                  key={`${item}-${particle.left}`}
                  style={{
                    left: particle.left,
                    width: `${particle.size}px`,
                    height: `${particle.size}px`,
                    '--dur': particle.dur,
                    '--delay': particle.delay,
                    '--sway': particle.sway,
                  } as React.CSSProperties}
                />
              ))}
            </div>
          )}
        </div>
      ))}

      {/* 不随季节换色的前景点缀：远山飞鸟 + 一只热气球（旅行的隐喻） */}
      <svg className="seasonal-drift" viewBox="0 0 1440 900" preserveAspectRatio="xMidYMid slice" focusable="false">
        <g className="birds" fill="none" stroke="#6b5a48" strokeWidth="2.4" strokeLinecap="round" opacity="0.5">
          <path d="M520 246 q 9 -7 18 0 q 9 -7 18 0" />
          <path d="M612 196 q 7 -5 14 0 q 7 -5 14 0" />
          <path d="M578 306 q 6 -4 12 0 q 6 -4 12 0" />
        </g>
        <g transform="translate(1168 356)">
          <g className="balloon" opacity="0.72">
            <ellipse cx="0" cy="0" rx="17" ry="21" fill="#5f4d3c" />
            <path d="M-11 12 L -8 24 M 11 12 L 8 24" stroke="#5f4d3c" strokeWidth="1.6" opacity="0.85" />
            <rect x="-8" y="24" width="16" height="9" rx="2.4" fill="#5f4d3c" />
          </g>
        </g>
      </svg>
    </div>
  );
}
