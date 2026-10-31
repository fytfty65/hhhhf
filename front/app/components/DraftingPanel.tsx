'use client';

/**
 * DraftingPanel — 推演前的诉求打磨面板（出行方式/偏好定位快捷切换、语音意图、快捷键、开始推演）
 *
 * Extracted verbatim from ContextualLobby.tsx during the file split (批 4)。
 * 仅搬运：props 签名、类名与文案逐字保留，未做任何行为改动。
 */

import { useState, useRef, useEffect, useMemo } from 'react';
import { motion } from 'framer-motion';
import { AlertCircle, ArrowRight, Camera, Car, Check, Coffee, MapIcon, MapPin, MessageSquare, Mic, PiggyBank, RefreshCw, Users } from 'lucide-react';
import { parseVoiceIntent } from '../lib/voiceIntent';
import VoiceIntentChips from './VoiceIntentChips';

// 出行方式与偏好定位的快捷切换选项（值需与后端 user_preferences.mode / role 完全对齐）
const TRAVEL_MODES = [
  { value: 'solo', label: '一人行', hint: '1', desc: '单人专属节奏', icon: MapPin },
  { value: 'coop', label: '亲友结伴', hint: '2', desc: '自动平衡多人偏好', icon: Users },
  { value: 'pvp', label: '高性价比', hint: '3', desc: '极致体验成本比', icon: PiggyBank },
];
const USER_ROLES = [
  { value: '寻味探索', label: '寻味探索', hint: 'Alt+1', desc: '美食驱动', icon: Coffee },
  { value: '视觉体验', label: '视觉体验', hint: 'Alt+2', desc: '出片导向', icon: Camera },
  { value: '休闲漫步', label: '休闲漫步', hint: 'Alt+3', desc: '宽裕漫游', icon: Car },
  { value: '深度探索', label: '深度探索', hint: 'Alt+4', desc: '紧凑打卡', icon: MapIcon },
];

export default function DraftingPanel({ activeMode, activeRole, onModeChange, onRoleChange, onSubmit, isReady, userIntent, setUserIntent, apiError, historyLength, roomMembers }: any) {
  const [listening, setListening] = useState(false);
  const recRef = useRef<any>(null);
  const parsed = useMemo(() => parseVoiceIntent(userIntent || ''), [userIntent]);

  // 快捷键快速切换：数字键 1/2/3 切换出行方式，Alt+1~4 切换偏好定位（输入框聚焦时不触发）
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement | null;
      if (t && (t.tagName === 'TEXTAREA' || t.tagName === 'INPUT' || t.isContentEditable)) return;
      if (e.altKey) {
        const idx = ['1', '2', '3', '4'].indexOf(e.key);
        if (idx >= 0 && USER_ROLES[idx]) { e.preventDefault(); onRoleChange(USER_ROLES[idx].value); }
        return;
      }
      if (e.ctrlKey || e.metaKey) return;
      const mi = ['1', '2', '3'].indexOf(e.key);
      if (mi >= 0) { e.preventDefault(); onModeChange(TRAVEL_MODES[mi].value); }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onModeChange, onRoleChange]);

  const toggleVoice = () => {
    if (listening) {
      recRef.current?.stop();
      setListening(false);
      return;
    }
    const w = (window as any);
    const SR = w.SpeechRecognition || w.webkitSpeechRecognition;
    if (!SR) {
      alert('当前浏览器不支持语音输入，请使用 Chrome / Edge');
      return;
    }
    const rec = new SR();
    rec.lang = 'zh-CN';
    rec.continuous = false;
    rec.interimResults = false;
    rec.onresult = (e: any) => {
      const text = e.results?.[0]?.[0]?.transcript;
      if (text) setUserIntent((prev: string) => (prev ? prev + '，' + text : text));
    };
    rec.onerror = () => setListening(false);
    rec.onend = () => setListening(false);
    recRef.current = rec;
    setListening(true);
    rec.start();
  };

  return (
    <motion.div initial={{ opacity: 0, x: 12 }} animate={{ opacity: 1, x: 0 }} exit={{ opacity: 0, x: -12 }} className="flex h-full flex-col p-5 sm:p-8">
      <div className="mx-auto mb-6 w-full max-w-4xl rounded-2xl border border-orange-100 bg-gradient-to-br from-orange-50/70 to-white px-5 py-4 sm:px-6">
        <h3 className="mb-1.5 text-base font-semibold text-slate-800">
          {historyLength > 0 ? `继续调整行程 · 已追加 ${historyLength} 次` : `先说说这趟旅行 · ${roomMembers.length} 位同行人`}
        </h3>
        <p className="text-sm leading-6 text-slate-600">
          {historyLength > 0 ? '直接描述想改的部分，例如“缩短第一天路线”或“午餐换成抓饭”，其余安排会尽量保持不变。' : '写下目的地、出行天数、必去地点和节奏偏好；预算与同行人的要求也可以一起说明。'}
        </p>
      </div>

      {/* 快速切换：出行方式 + 偏好定位（与后端 user_preferences 对齐，切换后重新推演即生效） */}
      <div className="mx-auto mb-6 w-full max-w-4xl space-y-3">
        <div className="flex items-center justify-between">
          <span className="text-sm font-semibold tracking-wide text-slate-700">出行方式</span>
          <span className="text-xs text-slate-400">快捷键 1 / 2 / 3</span>
        </div>
        <div className="grid grid-cols-3 gap-2">
          {TRAVEL_MODES.map((m) => {
            const selected = activeMode === m.value;
            const Icon = m.icon;
            return (
              <button
                key={m.value}
                onClick={() => onModeChange(m.value)}
                disabled={isReady}
                title={`${m.desc} · 快捷键 ${m.hint}`}
                aria-pressed={selected}
                className={`group relative flex cursor-pointer flex-col items-start gap-1 rounded-xl border px-3 py-2.5 text-left transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-orange-500 focus-visible:ring-offset-2 ${selected ? 'border-orange-600 bg-orange-50/70' : 'border-stone-200 bg-white hover:border-orange-200 hover:bg-orange-50/40'} ${isReady ? 'cursor-not-allowed opacity-50' : ''}`}
              >
                <span className="flex w-full items-center justify-between">
                  <Icon className={`h-4 w-4 ${selected ? 'text-orange-600' : 'text-slate-400 group-hover:text-orange-500'}`} />
                  {selected
                    ? <Check className="h-3.5 w-3.5 text-orange-600" />
                    : <kbd className="rounded border border-slate-200 bg-white px-1 py-0.5 font-mono text-[9px] text-slate-400">{m.hint}</kbd>}
                </span>
                <span className={`text-xs font-bold ${selected ? 'text-orange-800' : 'text-slate-700'}`}>{m.label}</span>
                <span className="text-[10px] leading-tight text-slate-400">{m.desc}</span>
              </button>
            );
          })}
        </div>

        <div className="flex items-center justify-between pt-1">
          <span className="text-sm font-semibold tracking-wide text-slate-700">偏好定位</span>
          <span className="text-xs text-slate-400">快捷键 Alt+1~4</span>
        </div>
        <div className="grid grid-cols-2 gap-2">
          {USER_ROLES.map((r) => {
            const selected = activeRole === r.value;
            const Icon = r.icon;
            return (
              <button
                key={r.value}
                onClick={() => onRoleChange(r.value)}
                disabled={isReady}
                title={`${r.desc} · 快捷键 ${r.hint}`}
                aria-pressed={selected}
                className={`group relative flex cursor-pointer items-center gap-2.5 rounded-xl border px-3 py-2.5 text-left transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-orange-500 focus-visible:ring-offset-2 ${selected ? 'border-orange-600 bg-orange-50/70' : 'border-stone-200 bg-white hover:border-orange-200 hover:bg-orange-50/40'} ${isReady ? 'cursor-not-allowed opacity-50' : ''}`}
              >
                <Icon className={`h-4 w-4 shrink-0 ${selected ? 'text-orange-600' : 'text-slate-400 group-hover:text-orange-500'}`} />
                <span className="min-w-0 flex-1">
                  <span className={`block text-xs font-bold ${selected ? 'text-orange-800' : 'text-slate-700'}`}>{r.label}</span>
                  <span className="block text-[10px] leading-tight text-slate-400">{r.desc}</span>
                </span>
                {selected
                  ? <Check className="h-3.5 w-3.5 shrink-0 text-orange-600" />
                  : <kbd className="shrink-0 rounded border border-slate-200 bg-white px-1 py-0.5 font-mono text-[9px] text-slate-400">{r.hint}</kbd>}
              </button>
            );
          })}
        </div>
      </div>

      {apiError && (
        <motion.div initial={{ opacity: 0, y: -6 }} animate={{ opacity: 1, y: 0 }} className="mx-auto mb-5 flex w-full max-w-4xl items-start gap-3 rounded-xl border border-rose-200 bg-rose-50 p-4">
          <AlertCircle className="w-5 h-5 text-rose-500 shrink-0 mt-0.5" />
          <div>
            <p className="text-sm font-bold text-rose-700">引擎连接失败</p>
            <p className="text-xs text-rose-500 mt-1 leading-relaxed">{apiError}</p>
          </div>
        </motion.div>
      )}

      <div className="mx-auto mb-6 w-full max-w-4xl">
        <div className={`flex min-h-[220px] flex-col rounded-2xl border bg-white p-5 transition-colors sm:min-h-[250px] ${isReady ? 'border-emerald-300 bg-emerald-50/30' : 'border-stone-200 focus-within:border-orange-500 focus-within:ring-2 focus-within:ring-orange-100'}`}>
          <div className="mb-3 flex items-center justify-between">
            <span className="flex items-center gap-1.5 text-sm font-semibold text-slate-700">
              <MessageSquare className="w-3.5 h-3.5" /> {historyLength > 0 ? "追问/精进当前路线需求" : "你的个性化旅行诉求"}
            </span>
            <div className="flex items-center gap-2">
              <button type="button" onClick={toggleVoice} disabled={isReady} title="语音输入" aria-label={listening ? '停止语音输入' : '开始语音输入'} className={`flex h-9 w-9 items-center justify-center rounded-lg border transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-orange-500 ${listening ? 'border-rose-200 bg-rose-50 text-rose-600' : 'border-slate-200 bg-white text-slate-600 hover:bg-slate-50'} ${isReady ? 'cursor-not-allowed opacity-50' : ''}`}>
                <Mic className={`w-3.5 h-3.5 ${listening ? 'animate-pulse' : ''}`} />
              </button>
              {isReady && <span className="flex items-center gap-1 rounded-md bg-emerald-50 px-2 py-1 text-[10px] font-semibold text-emerald-700"><Check className="h-3 w-3"/> 已提交规划</span>}
            </div>
          </div>
          
          <VoiceIntentChips parsed={parsed} />
          <textarea 
            data-testid="intent-input"
            disabled={isReady}
            value={userIntent}
            onChange={(e) => setUserIntent(e.target.value)}
            className="min-h-[150px] w-full flex-1 resize-y bg-transparent text-base leading-8 text-slate-800 outline-none placeholder:text-slate-400 focus-visible:outline-none sm:min-h-[170px]"
            placeholder={historyLength > 0 ? "例如：还是去那儿，但是把第二天的午餐平替成便宜一点的老字号小吃..." : "例如：我们打算去乌鲁木齐玩3天，想看大巴扎和博物馆，吃地道手抓肉，下午不能太累..."}
          ></textarea>
        </div>
      </div>

      <div className="mx-auto mt-auto w-full max-w-4xl pt-4">
        <button
          type="button"
          onClick={onSubmit} 
          disabled={isReady || userIntent.trim().length === 0}
          className={`flex min-h-12 w-full cursor-pointer items-center justify-center gap-2 rounded-xl text-sm font-semibold transition-all focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-orange-500 focus-visible:ring-offset-2 ${isReady ? 'bg-emerald-700 text-white' : userIntent.trim() ? 'bg-orange-700 text-white shadow-md shadow-orange-200 hover:bg-orange-800' : 'cursor-not-allowed border border-stone-200 bg-stone-100 text-stone-400'}`}
        >
          {isReady ? (
            <><RefreshCw className="w-4 h-4 animate-spin" /> 正在整理行程方案…</>
          ) : (
            <>{historyLength > 0 ? "应用调整" : "生成行程方案"} <ArrowRight className="w-4 h-4" /></>
          )}
        </button>
      </div>
    </motion.div>
  );
}
