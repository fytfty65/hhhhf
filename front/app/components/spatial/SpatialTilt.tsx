'use client';

/**
 * SpatialTilt — 鼠标跟随 3D 视差容器（暖橙空间沙盘 · Phase 1 原语）
 *
 * 设计约束（与项目工程红线对齐）：
 * - 零外部依赖：只写 CSS 变量（--tilt-nx / --tilt-ny / --tilt-lift / --glare-x / --glare-y），
 *   变换与光泽全部在 globals.css 的 .spatial-tilt / .spatial-tilt-glare 中消费；
 * - rAF 节流：pointermove 里只做一次 setProperty 批处理，不在 React 状态里存光标，
 *   因此指针移动**不会触发任何 React 重渲染**；
 * - 启用门槛：`(hover: hover) and (pointer: fine)` 且未开启 prefers-reduced-motion，
 *   触屏 / 减少动态用户拿到的是完全静态的玻璃卡（连监听都不挂）；
 * - 不劫持子元素 transform：本组件是独立包裹层，可以安全套在 framer-motion
 *   动画元素外面（如登录主卡的入场 motion.div），两者变换互不覆盖。
 *
 * 用法：
 *   <SpatialTilt className="rounded-3xl surface-float" maxTilt={2.5}>
 *     <motion.div ...>...</motion.div>
 *   </SpatialTilt>
 *
 * 建议倾角：页面级大卡 2~3°，小卡片可到 6~8°（大卡角度越大越晕）。
 */

import React, { useCallback, useEffect, useRef, useState } from 'react';

interface SpatialTiltProps {
  children: React.ReactNode;
  /** 附加到宿主元素上的类名（圆角、材质、布局都写在这里） */
  className?: string;
  /** 最大倾角（度）。登录主卡这类大面板建议 3~5，小卡片可以到 8 */
  maxTilt?: number;
  /** 是否渲染跟随鼠标的镜面高光层 */
  glare?: boolean;
  /** 强制关闭（例如嵌套在已有点击拖拽交互里时） */
  disabled?: boolean;
}

export default function SpatialTilt({
  children,
  className = '',
  maxTilt = 4.5,
  glare = true,
  disabled = false,
}: SpatialTiltProps) {
  const hostRef = useRef<HTMLDivElement>(null);
  const frameRef = useRef<number>(0);
  const [enabled, setEnabled] = useState(false);

  // 只在「可悬停 + 精确指针 + 未开启减少动态」时启用，并跟随系统设置变化。
  useEffect(() => {
    if (disabled) {
      setEnabled(false);
      return;
    }
    const fineHover = window.matchMedia('(hover: hover) and (pointer: fine)');
    const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)');
    const evaluate = () =>
      setEnabled(fineHover.matches && !reduceMotion.matches && window.innerWidth >= 1024);
    evaluate();
    fineHover.addEventListener('change', evaluate);
    reduceMotion.addEventListener('change', evaluate);
    window.addEventListener('resize', evaluate, { passive: true });
    return () => {
      fineHover.removeEventListener('change', evaluate);
      reduceMotion.removeEventListener('change', evaluate);
      window.removeEventListener('resize', evaluate);
    };
  }, [disabled]);

  // 统一变量写入：一次指针事件只做这一轮 DOM 写入（rAF 合帧）。
  const apply = useCallback((nx: number, ny: number, lift: number, gx: number, gy: number) => {
    const host = hostRef.current;
    if (!host) return;
    host.style.setProperty('--tilt-nx', nx.toFixed(3));
    host.style.setProperty('--tilt-ny', ny.toFixed(3));
    host.style.setProperty('--tilt-lift', lift.toFixed(3));
    host.style.setProperty('--glare-x', gx.toFixed(1));
    host.style.setProperty('--glare-y', gy.toFixed(1));
    host.dataset.tiltActive = lift === 1 && nx === 0 && ny === 0 ? 'false' : 'true';
  }, []);

  const handlePointerMove = useCallback(
    (event: React.PointerEvent<HTMLDivElement>) => {
      if (!enabled) return;
      const host = hostRef.current;
      if (!host) return;
      const rect = host.getBoundingClientRect();
      if (rect.width < 1 || rect.height < 1) return;
      const nx = (event.clientX - rect.left) / rect.width - 0.5; // -0.5 ~ 0.5
      const ny = (event.clientY - rect.top) / rect.height - 0.5;
      if (frameRef.current) cancelAnimationFrame(frameRef.current);
      frameRef.current = requestAnimationFrame(() => {
        apply(nx, ny, 1.006, (nx + 0.5) * 100, (ny + 0.5) * 100);
      });
    },
    [enabled, apply],
  );

  const handlePointerLeave = useCallback(() => {
    if (frameRef.current) {
      cancelAnimationFrame(frameRef.current);
      frameRef.current = 0;
    }
    apply(0, 0, 1, 50, 50);
  }, [apply]);

  useEffect(
    () => () => {
      if (frameRef.current) cancelAnimationFrame(frameRef.current);
    },
    [],
  );

  return (
    <div
      ref={hostRef}
      onPointerMove={handlePointerMove}
      onPointerLeave={handlePointerLeave}
      data-tilt-active="false"
      data-tilt-enabled={enabled ? 'true' : 'false'}
      style={{ '--tilt-max': maxTilt } as React.CSSProperties}
      className={`spatial-tilt relative ${className}`}
    >
      {children}
      {glare && enabled && (
        <div aria-hidden="true" className="spatial-tilt-glare" />
      )}
    </div>
  );
}
