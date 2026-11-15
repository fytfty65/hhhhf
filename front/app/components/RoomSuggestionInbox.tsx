'use client';

import { Lightbulb } from 'lucide-react';
import type { RoomComment } from '../lib/stores/roomCollaboration';

/**
 * 房主建议箱：把成员散落在各节点里的建议聚合起来，按节点分组。
 * 「采纳并重排」走现有的局部调整能力，改完由 room_plan 自动同步回所有成员。
 */
export default function RoomSuggestionInbox({
  comments,
  busy = false,
  onAdopt,
  onDismiss,
}: {
  comments: RoomComment[];
  busy?: boolean;
  onAdopt: (comment: RoomComment) => void;
  onDismiss: (id: string) => void;
}) {
  const open = comments.filter((item) => item.status === 'open');
  if (open.length === 0) return null;

  return (
    <section data-testid="room-suggestion-inbox" className="mb-4 rounded-2xl border border-orange-200 bg-orange-50/60 p-4">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <h3 className="flex items-center gap-1.5 text-sm font-black text-orange-800">
          <Lightbulb className="h-4 w-4" /> 房间建议
          <span className="rounded-full bg-orange-500 px-1.5 py-0.5 text-[10px] font-black text-white">{open.length}</span>
        </h3>
        <p className="text-[11px] font-medium text-orange-600">成员对节点提的修改建议，采纳后会触发局部重排</p>
      </div>
      <ul className="space-y-2">
        {open.map((item) => (
          <li key={item.id} className="rounded-xl border border-orange-100 bg-white p-3" data-testid={`suggestion-${item.id}`}>
            <div className="flex flex-wrap items-center gap-1.5 text-[11px] font-bold text-slate-400">
              {item.day ? <span>第 {item.day} 天</span> : null}
              <span className="text-orange-600">{item.node_name}</span>
              <span>· {item.user_name}</span>
            </div>
            <p className="mt-1 text-sm font-medium leading-relaxed text-slate-700">{item.text}</p>
            <div className="mt-2 flex flex-wrap gap-2">
              <button
                type="button"
                disabled={busy}
                onClick={() => onAdopt(item)}
                className="rounded-lg bg-orange-500 px-3 py-1.5 text-[11px] font-bold text-white transition-colors hover:bg-orange-600 disabled:cursor-not-allowed disabled:opacity-50"
              >
                {busy ? '正在重排…' : '采纳并重排'}
              </button>
              <button
                type="button"
                disabled={busy}
                onClick={() => onDismiss(item.id)}
                className="rounded-lg border border-slate-200 bg-white px-3 py-1.5 text-[11px] font-bold text-slate-500 transition-colors hover:border-slate-300 hover:text-slate-700 disabled:opacity-50"
              >
                忽略
              </button>
            </div>
          </li>
        ))}
      </ul>
    </section>
  );
}
