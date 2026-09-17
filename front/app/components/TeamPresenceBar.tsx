'use client';

/**
 * TeamPresenceBar — 同行者在线状态/满意度/协作动态条（真实在线状态 + 复制房号）
 *
 * Extracted verbatim from ContextualLobby.tsx during the file split (批 4)。
 * 仅搬运：props 签名、类名与文案逐字保留，未做任何行为改动。
 */

import { useState, useEffect, useMemo } from 'react';
import { API_BASE } from '../lib/utils';
import { Activity, Check, Copy, Radio, Scale, Users } from 'lucide-react';

type ActivityEvt = { id: string; ts: number; type: 'vote' | 'join' | 'consensus' | 'typing' | 'split'; text: string };

export default function TeamPresenceBar({ members = [], teamSatisfaction = {}, roomCode, arbitrationRecords = [], wsConnected = true, currentUser = null }: any) {
  const [liveSat, setLiveSat] = useState<Record<string, number>>({});
  const [onlineMap, setOnlineMap] = useState<Record<string, boolean>>({});
  const [feed] = useState<ActivityEvt[]>([]);
  const [copied, setCopied] = useState(false);

  // 初始化：同步真实满意度与在线状态（不再随机生成假数据）
  useEffect(() => {
    const init: Record<string, number> = {};
    const onlineInit: Record<string, boolean> = {};
    members.forEach((m: any, idx: number) => {
      const k = m.name;
      const ext = Number(teamSatisfaction[k]);
      init[k] = Number.isFinite(ext) && ext > 0 ? ext : 90;
      onlineInit[k] = true; // 房间成员默认可用
    });
    setLiveSat(init);
    setOnlineMap(onlineInit);
  }, [members.length, roomCode, teamSatisfaction]);

  // 外部满意度有更新时同步（真实数据，不做随机抖动）
  useEffect(() => {
    setLiveSat(prev => {
      const next = { ...prev };
      Object.entries(teamSatisfaction || {}).forEach(([k, v]) => {
        const n = Number(v);
        if (Number.isFinite(n) && n > 0) next[k] = Math.max(60, Math.min(99, n));
      });
      return next;
    });
  }, [teamSatisfaction]);

  const avgSatisfaction = useMemo(() => {
    const vals = Object.values(liveSat).map(v => Number(v)).filter(v => Number.isFinite(v) && v > 0);
    return vals.length > 0 ? Math.round(vals.reduce((a, b) => a + b, 0) / vals.length) : 94;
  }, [liveSat]);

  const copyRoom = async () => {
    try {
      await navigator.clipboard.writeText(roomCode || '');
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {}
  };

  const getSatColor = (s: number) =>
    s >= 90 ? 'text-emerald-600' : s >= 78 ? 'text-teal-600' : s >= 65 ? 'text-amber-600' : 'text-rose-500';
  const getSatBar = (s: number) =>
    s >= 90 ? 'from-emerald-400 to-teal-400' : s >= 78 ? 'from-teal-400 to-cyan-400' : s >= 65 ? 'from-amber-400 to-orange-400' : 'from-rose-400 to-pink-400';

  // 👑 头像渲染：优先使用用户上传的真实头像，没有时再用 DiceBear 生成
  const getAvatarUrl = (m: any) => {
    let url = m.avatarUrl || m.avatar_url || '';
    // 如果成员是当前用户本人且没有头像，用 currentUser 的兜底
    if (!url && m.id === currentUser?.id && currentUser?.avatarUrl) {
      url = currentUser.avatarUrl;
    }
    // 相对路径补全为绝对路径
    if (url && !url.startsWith('http') && !url.startsWith('data:')) {
      url = `${API_BASE}${url}`;
    }
    if (url) return url;
    return `https://api.dicebear.com/9.x/avataaars/svg?seed=${encodeURIComponent(m.avatarSeed || m.name)}&backgroundColor=fdeed8`;
  };

  return (
    <div className="mb-6 rounded-2xl overflow-hidden relative bg-white border border-slate-200 shadow-sm">
      {/* ============ HEADER ============ */}
      <div className="relative px-5 pt-4 pb-3 flex flex-wrap items-center justify-between gap-3 border-b border-slate-100">
        <div className="flex items-center gap-3">
          <div className="w-9 h-9 rounded-xl bg-orange-50 border border-orange-200 flex items-center justify-center">
            <Users className="w-[18px] h-[18px] text-orange-500" />
          </div>
          <div>
            <div className="flex items-center gap-2">
              <h3 className="font-black text-[15px] text-slate-800 tracking-wide">同行伙伴</h3>
              <span className="text-[10px] font-bold px-2 py-0.5 rounded-full bg-orange-50 text-orange-600 border border-orange-200">
                {members.length} 人
              </span>
            </div>
            <div className="flex items-center gap-2 mt-0.5">
              <span className={`inline-flex items-center gap-1 text-[10px] font-bold px-1.5 py-0.5 rounded-full ${
                wsConnected
                  ? 'text-emerald-600 bg-emerald-50'
                  : 'text-rose-500 bg-rose-50'
              }`}>
                <span className={`w-1.5 h-1.5 rounded-full ${wsConnected ? 'bg-emerald-500' : 'bg-rose-400'}`} />
                {wsConnected ? '在线' : '离线'}
              </span>
            </div>
          </div>
        </div>

        <div className="flex items-center gap-2">
          {/* 团队满意度 */}
          <div className="flex items-center gap-2.5 bg-slate-50 border border-slate-100 rounded-xl px-3 py-2">
            <div className="relative w-10 h-10 shrink-0">
              <svg viewBox="0 0 36 36" className="w-full h-full -rotate-90">
                <circle cx="18" cy="18" r="15" fill="none" stroke="#e2e8f0" strokeWidth="3" />
                <circle
                  cx="18" cy="18" r="15" fill="none"
                  stroke={avgSatisfaction >= 90 ? '#10b981' : avgSatisfaction >= 78 ? '#14b8a6' : avgSatisfaction >= 65 ? '#f59e0b' : '#f43f5e'}
                  strokeWidth="3" strokeLinecap="round"
                  strokeDasharray={`${(avgSatisfaction / 100) * 94.25} 94.25`}
                  style={{ transition: 'stroke-dasharray 0.6s ease, stroke 0.6s ease' }}
                />
              </svg>
              <div className="absolute inset-0 flex items-center justify-center">
                <span className={`text-[11px] font-black ${getSatColor(avgSatisfaction)}`}>{avgSatisfaction}</span>
              </div>
            </div>
            <div>
              <p className="text-[11px] font-bold text-slate-700">团队满意度</p>
            </div>
          </div>

          {/* 房间号 */}
          <button
            onClick={copyRoom}
            className="group flex items-center gap-1.5 bg-orange-50 border border-orange-200 hover:bg-orange-100 rounded-xl px-3 py-2 transition-colors cursor-pointer"
            title="点击复制房间号"
          >
            <Radio className="w-3.5 h-3.5 text-orange-500" />
            <span className="text-[11px] font-bold text-orange-600 tracking-wider">{roomCode}</span>
            {copied ? <Check className="w-3 h-3 text-emerald-500" /> : <Copy className="w-3 h-3 text-orange-400 group-hover:text-orange-600" />}
          </button>
        </div>
      </div>

      {/* ============ MEMBERS GRID ============ */}
      <div className="relative px-5 py-4">
        <div className="flex items-center justify-between mb-3">
          <div className="flex items-center gap-1.5">
            <Users className="w-3.5 h-3.5 text-slate-400" />
            <span className="text-[11px] font-bold text-slate-500">成员状态</span>
          </div>
        </div>

        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
          {members.map((m: any, idx: number) => {
            const score = typeof liveSat[m.name] === 'number' ? liveSat[m.name] : 90;
            const isOnline = onlineMap[m.name] !== false;
            const roleColor =
              m.role?.includes('寻味') || m.role?.includes('美食') ? 'text-orange-600 bg-orange-50 border-orange-200'
              : m.role?.includes('摄影') || m.role?.includes('风景') ? 'text-emerald-600 bg-emerald-50 border-emerald-200'
              : m.role?.includes('住宿') || m.role?.includes('品质') ? 'text-indigo-600 bg-indigo-50 border-indigo-200'
              : 'text-slate-600 bg-slate-50 border-slate-200';
            return (
              <div
                key={idx}
                className="relative bg-slate-50/80 border border-slate-100 hover:border-orange-200 rounded-xl p-3 transition-all group"
              >
                <div className="flex items-center gap-3">
                  <div className="relative shrink-0">
                    <img
                      src={getAvatarUrl(m)}
                      alt={m.name}
                      className="w-10 h-10 rounded-full bg-white border border-slate-200 object-cover"
                    />
                    <span className={`absolute -bottom-0.5 -right-0.5 w-3 h-3 rounded-full border-2 border-white ${isOnline ? 'bg-emerald-400' : 'bg-slate-300'}`} />
                  </div>
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-1.5">
                      <span className="font-bold text-sm text-slate-800 truncate max-w-[90px]">{m.name}</span>
                      {m.role && (
                        <span className={`text-[10px] px-1.5 py-0.5 rounded-full border font-bold shrink-0 ${roleColor}`}>
                          {m.role}
                        </span>
                      )}
                    </div>
                    <p className="text-[11px] text-slate-400 truncate mt-0.5">{m.intent || '期待同行'}</p>
                    {/* 满意度进度条 */}
                    <div className="mt-2 flex items-center gap-2">
                      <div className="flex-1 h-1.5 rounded-full bg-slate-200 overflow-hidden">
                        <div
                          className={`h-full rounded-full bg-gradient-to-r ${getSatBar(score)} transition-all duration-700`}
                          style={{ width: `${score}%` }}
                        />
                      </div>
                      <span className={`text-[10px] font-bold w-6 text-right ${getSatColor(score)}`}>{score}</span>
                    </div>
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      </div>

      {/* ============ 动态 + 协调记录 ============ */}
      {(arbitrationRecords?.length > 0 || feed.length > 0) && (
        <div className="relative grid grid-cols-1 md:grid-cols-2 gap-0 border-t border-slate-100">
          {/* 最新动态 */}
          {feed.length > 0 && (
            <div className="px-5 py-3 border-b md:border-b-0 md:border-r border-slate-100">
              <div className="flex items-center gap-1.5 mb-2">
                <Activity className="w-3.5 h-3.5 text-slate-400" />
                <span className="text-[11px] font-bold text-slate-500">最新动态</span>
              </div>
              <div className="space-y-1.5 max-h-28 overflow-y-auto custom-scrollbar pr-1">
                {feed.slice(0, 5).map((ev) => (
                  <div key={ev.id} className="flex items-start gap-2 text-[11px] leading-snug text-slate-600">
                    <span className="shrink-0 text-slate-400 mt-0.5">·</span>
                    <span className="flex-1">{ev.text}</span>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* 行程协调记录 */}
          {arbitrationRecords?.length > 0 && (
            <div className="px-5 py-3">
              <div className="flex items-center gap-1.5 mb-2">
                <Scale className="w-3.5 h-3.5 text-slate-400" />
                <span className="text-[11px] font-bold text-slate-500">行程协调</span>
              </div>
              <div className="space-y-1.5 max-h-28 overflow-y-auto custom-scrollbar pr-1">
                {arbitrationRecords.map((rec: string, rIdx: number) => (
                  <div key={rIdx} className="flex items-start gap-2 text-[11px] leading-snug">
                    <span className="shrink-0 w-1 h-1 rounded-full bg-orange-400 mt-1.5" />
                    <span className="text-slate-600">{rec}</span>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
