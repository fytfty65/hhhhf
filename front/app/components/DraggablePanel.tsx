'use client';

import React, { useRef, useState } from 'react';
import { ChevronDown, ChevronUp, GripVertical } from 'lucide-react';

interface DraggablePanelProps {
  title: React.ReactNode;
  icon?: React.ReactNode;
  children: React.ReactNode;
  className?: string;
  style?: React.CSSProperties;
  defaultCollapsed?: boolean;
  onPositionChange?: (offset: { x: number; y: number }) => void;
}

/**
 * 可折叠 + 可拖拽 HUD 面板：
 * - 顶栏作为拖拽手柄（指针捕获，双击复位）
 * - 右上角折叠/展开
 * - 通过 transform 位移，不改变原绝对定位布局
 */
export default function DraggablePanel({
  title,
  icon,
  children,
  className = '',
  style,
  defaultCollapsed = false,
  onPositionChange,
}: DraggablePanelProps) {
  const [collapsed, setCollapsed] = useState(defaultCollapsed);
  const offsetRef = useRef({ x: 0, y: 0 });
  const [offset, setOffset] = useState({ x: 0, y: 0 });

  const onPointerDown = (e: React.PointerEvent<HTMLDivElement>) => {
    // 点击折叠按钮不算拖拽
    if ((e.target as HTMLElement).closest('button')) return;
    const handle = e.currentTarget;
    handle.setPointerCapture(e.pointerId);
    const startX = e.clientX - offsetRef.current.x;
    const startY = e.clientY - offsetRef.current.y;

    const onMove = (ev: PointerEvent) => {
      const next = { x: ev.clientX - startX, y: ev.clientY - startY };
      offsetRef.current = next;
      setOffset(next);
      onPositionChange?.(next);
    };
    const onUp = () => {
      handle.removeEventListener('pointermove', onMove);
      handle.removeEventListener('pointerup', onUp);
      handle.removeEventListener('pointercancel', onUp);
    };
    handle.addEventListener('pointermove', onMove);
    handle.addEventListener('pointerup', onUp);
    handle.addEventListener('pointercancel', onUp);
  };

  const resetPosition = () => {
    offsetRef.current = { x: 0, y: 0 };
    setOffset({ x: 0, y: 0 });
    onPositionChange?.({ x: 0, y: 0 });
  };

  return (
    <div
      className={`${className} flex flex-col overflow-hidden`}
      style={{ ...style, transform: `translate(${offset.x}px, ${offset.y}px)` }}
    >
      {/* 拖拽手柄 + 折叠 */}
      <div
        className="radar-drag-handle flex items-center justify-between gap-2 shrink-0 px-4 py-2.5"
        onPointerDown={onPointerDown}
        onDoubleClick={resetPosition}
        title="拖拽移动 · 双击复位"
      >
        <div className="flex items-center gap-2 min-w-0">
          {icon}
          <span className="truncate">{title}</span>
        </div>
        <div className="flex items-center gap-1.5 shrink-0">
          <GripVertical className="w-3.5 h-3.5 opacity-40" />
          <button
            onClick={() => setCollapsed((c) => !c)}
            aria-label={collapsed ? '展开面板' : '折叠面板'}
            className="text-slate-500 hover:opacity-100 opacity-70 transition-opacity"
          >
            {collapsed ? <ChevronUp className="w-4 h-4" /> : <ChevronDown className="w-4 h-4" />}
          </button>
        </div>
      </div>

      {/* 内容区 */}
      {!collapsed && <div className="min-h-0 flex-1 overflow-hidden">{children}</div>}
    </div>
  );
}