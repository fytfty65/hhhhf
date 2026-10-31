'use client';

import { RefreshCw } from 'lucide-react';

export type RoomConnectionState = 'idle' | 'connecting' | 'connected' | 'reconnecting' | 'offline';

const VIEW: Record<RoomConnectionState, { dot: string; label: string; tone: string; hint: string }> = {
  idle: { dot: 'bg-slate-400', label: '等待协作房间就绪', tone: 'text-slate-500', hint: '房间创建好后会自动连接；你也可以先填写需求' },
  connecting: { dot: 'bg-amber-500 animate-pulse', label: '正在连接协作通道…', tone: 'text-amber-700', hint: '连上后才能看到同行者的改动；你可以先填写需求' },
  connected: { dot: 'bg-emerald-500', label: '协作已连接', tone: 'text-emerald-700', hint: '同行者的改动会实时同步' },
  reconnecting: { dot: 'bg-amber-500 animate-pulse', label: '协作连接已断开，正在重连', tone: 'text-amber-700', hint: '不影响你继续规划；重连前同行者的改动暂时收不到' },
  offline: { dot: 'bg-rose-500', label: '网络已断开', tone: 'text-rose-700', hint: '恢复网络后会自动重连；你也可以先继续填写需求' },
};

// 协作连接状态：如实区分「连接中 / 已连接 / 断开重连 / 没网」，
// 并在断开时给出「立即重试」，而不是一直显示一个不会变绿的同步标记。
export default function RoomConnectionStatus({
  connection,
  retryAttempt = 0,
  retryDelayMs = 0,
  memberCount,
  onRetry,
  compact = false,
  testId = 'room-connection',
}: {
  connection: RoomConnectionState;
  retryAttempt?: number;
  retryDelayMs?: number;
  memberCount?: number;
  onRetry?: () => void;
  compact?: boolean;
  testId?: string;
}) {
  const view = VIEW[connection];
  const retryIn = Math.max(1, Math.ceil(retryDelayMs / 1000));
  return (
    <div data-testid={testId} data-connection={connection} className="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs">
      <span className={`h-2 w-2 rounded-full ${view.dot}`} />
      <span data-testid={`${testId}-text`} className={`font-bold ${view.tone}`}>
        {view.label}
        {connection === 'connected' && memberCount ? ` · ${memberCount} 人在线` : ''}
        {connection === 'reconnecting' && (retryAttempt > 0 || retryDelayMs > 0) ? `（第 ${retryAttempt || 1} 次，约 ${retryIn} 秒后）` : ''}
      </span>
      {!compact && connection !== 'connected' && <span className="text-slate-500">{view.hint}</span>}
      {connection !== 'connected' && connection !== 'idle' && onRetry && (
        <button type="button" data-testid={`${testId}-retry`} onClick={onRetry} className="inline-flex items-center gap-1 rounded-md border border-slate-300 bg-white px-2 py-1 text-[11px] font-semibold text-slate-700 transition-colors hover:bg-slate-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-orange-500">
          <RefreshCw className="h-3 w-3" />立即重试
        </button>
      )}
    </div>
  );
}
