'use client';

import { useState } from 'react';
import { Check, MessageCircle, Send, X } from 'lucide-react';
import type { RoomComment } from '../lib/stores/roomCollaboration';

/**
 * 节点建议线程：成员对某个行程节点写一句建议，队友都能在卡片下看到。
 * 房主不在这里处理，统一进「房间建议」箱，避免同一屏出现两套操作入口。
 */
export default function NodeSuggestionThread({
  comments,
  open,
  day,
  nodeName,
  onToggle,
  onSubmit,
}: {
  comments: RoomComment[];
  open: boolean;
  day: number;
  nodeName: string;
  onToggle: () => void;
  onSubmit: (nodeName: string, day: number, text: string) => boolean;
}) {
  const [draft, setDraft] = useState('');

  const submit = () => {
    const text = draft.trim();
    if (!text) return;
    if (onSubmit(nodeName, day, text)) setDraft('');
  };

  if (!open && comments.length === 0) return null;

  return (
    <div className="mt-2 rounded-xl border border-orange-100 bg-orange-50/50 p-3" data-testid={`node-suggestions-${nodeName}`}>
      <div className="mb-2 flex items-center justify-between">
        <p className="flex items-center gap-1.5 text-[11px] font-bold text-orange-700">
          <MessageCircle className="h-3 w-3" /> 队友建议 · {comments.length}
        </p>
        <button type="button" onClick={onToggle} className="text-[11px] font-bold text-orange-600 transition-colors hover:text-orange-700">
          {open ? '收起' : '写建议'}
        </button>
      </div>

      {comments.length > 0 && (
        <ul className="space-y-1.5">
          {comments.map((item) => (
            <li key={item.id} className="rounded-lg bg-white px-2.5 py-2 text-xs">
              <div className="flex items-center gap-1.5 text-[10px] font-bold">
                <span className="text-slate-500">{item.user_name}</span>
                {item.status === 'adopted' && (
                  <span className="flex items-center gap-0.5 text-emerald-600"><Check className="h-2.5 w-2.5" />已采纳</span>
                )}
                {item.status === 'dismissed' && (
                  <span className="flex items-center gap-0.5 text-slate-400"><X className="h-2.5 w-2.5" />已忽略</span>
                )}
              </div>
              <p className="mt-0.5 font-medium leading-relaxed text-slate-700">{item.text}</p>
            </li>
          ))}
        </ul>
      )}

      {open && (
        <div className="mt-2 flex items-center gap-2">
          <input
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === 'Enter') {
                event.preventDefault();
                submit();
              }
            }}
            maxLength={300}
            placeholder="例如：这天太赶，想换成博物馆"
            className="min-w-0 flex-1 rounded-lg border border-orange-200 bg-white px-2.5 py-1.5 text-xs font-medium text-slate-700 outline-none transition-colors placeholder:text-slate-400 focus:border-orange-400"
          />
          <button
            type="button"
            onClick={submit}
            disabled={!draft.trim()}
            className="flex items-center gap-1 rounded-lg bg-orange-500 px-2.5 py-1.5 text-[11px] font-bold text-white transition-colors hover:bg-orange-600 disabled:opacity-40"
          >
            <Send className="h-3 w-3" /> 发送
          </button>
        </div>
      )}
    </div>
  );
}
