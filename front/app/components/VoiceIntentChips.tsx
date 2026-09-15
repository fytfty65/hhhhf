'use client';
import { MapPin, CalendarDays, Tag } from 'lucide-react';
import type { VoiceIntent } from '../lib/voiceIntent';

// 模块 9：语音结构化参数展示。将解析出的「目的地 / 天数 / 偏好标签」以胶囊形式确认回显。
export default function VoiceIntentChips({ parsed }: { parsed: VoiceIntent | null }) {
  if (!parsed) return null;
  const hasAny = Boolean(parsed.city) || parsed.days > 0 || parsed.tags.length > 0;
  if (!hasAny) return null;

  return (
    <div className="flex flex-wrap items-center gap-1.5 mb-3">
      {parsed.city && (
        <span className="flex items-center gap-1 bg-orange-50 border border-orange-200 text-orange-700 px-2 py-0.5 rounded-full text-[11px] font-bold">
          <MapPin className="w-3 h-3" /> 目的地 · {parsed.city}
        </span>
      )}
      {parsed.days > 0 && (
        <span className="flex items-center gap-1 bg-sky-50 border border-sky-200 text-sky-700 px-2 py-0.5 rounded-full text-[11px] font-bold">
          <CalendarDays className="w-3 h-3" /> {parsed.days} 天
        </span>
      )}
      {parsed.tags.map((t) => (
        <span key={t} className="flex items-center gap-1 bg-emerald-50 border border-emerald-200 text-emerald-700 px-2 py-0.5 rounded-full text-[11px] font-bold">
          <Tag className="w-3 h-3" /> {t}
        </span>
      ))}
    </div>
  );
}