'use client';

/**
 * DraftingPanel — 推演前的诉求打磨面板（出行方式/偏好定位快捷切换、语音意图、快捷键、开始推演）
 *
 * Extracted verbatim from ContextualLobby.tsx during the file split (批 4)。
 * 仅搬运：props 签名、类名与文案逐字保留，未做任何行为改动。
 */

import { useState, useRef, useEffect, useMemo } from 'react';
import { motion } from 'framer-motion';
import { AlertCircle, ArrowRight, Check, MessageSquare, Mic, RefreshCw, Sparkles } from 'lucide-react';
import { parseVoiceIntent } from '../lib/voiceIntent';
import VoiceIntentChips from './VoiceIntentChips';

// 出行方式与偏好定位的快捷切换选项（值需与后端 user_preferences.mode / role 完全对齐）
const TRAVEL_MODES = [
  { value: 'solo', label: '一人行', hint: '1', desc: '单人专属节奏' },
  { value: 'coop', label: '亲友结伴', hint: '2', desc: '多智能体博弈平衡' },
  { value: 'pvp', label: '高性价比', hint: '3', desc: '极致体验成本比' },
];
const USER_ROLES = [
  { value: '寻味探索', label: '寻味探索', hint: 'Alt+1', desc: '美食驱动' },
  { value: '视觉体验', label: '视觉体验', hint: 'Alt+2', desc: '出片导向' },
  { value: '休闲漫步', label: '休闲漫步', hint: 'Alt+3', desc: '宽裕漫游' },
  { value: '深度探索', label: '深度探索', hint: 'Alt+4', desc: '紧凑打卡' },
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
    <motion.div initial={{ opacity: 0, x: 20 }} animate={{ opacity: 1, x: 0 }} exit={{ opacity: 0, x: -20 }} className="p-8 flex flex-col h-full relative">
      <div className="relative overflow-hidden rounded-2xl bg-gradient-to-br from-orange-50/90 to-amber-50/60 border border-orange-100 p-5 shadow-xs mb-6">
        <h3 className="text-sm font-extrabold text-orange-900 mb-2 flex items-center gap-2">
          <Sparkles className="w-4 h-4 text-orange-500" />
          {historyLength > 0 ? `多轮对齐模式 (已追加 ${historyLength} 次诉求)` : `专属向导已就绪 (当前共识组: ${roomMembers.length}人)`}
        </h3>
        <p className="text-xs text-orange-700/80 leading-relaxed font-medium">
          {historyLength > 0 ? '支持增量精进！您可以直接输入：“把第一天的路线缩短”、“中午想吃抓饭”等。系统会在当前成果上打差量补丁。' : '随性写下旅程目标（城市、天数、特殊愿望），底层的多个智能体会同时协调所有同行人的偏好与预算。'}
        </p>
      </div>

      {/* 快速切换：出行方式 + 偏好定位（与后端 user_preferences 对齐，切换后重新推演即生效） */}
      <div className="mb-6 space-y-3">
        <div className="flex items-center justify-between">
          <span className="text-[10px] font-bold text-slate-400 uppercase tracking-wider">出行方式</span>
          <span className="text-[10px] text-slate-300 font-medium">快捷键 1 / 2 / 3</span>
        </div>
        <div className="grid grid-cols-3 gap-2">
          {TRAVEL_MODES.map((m) => (
            <button
              key={m.value}
              onClick={() => onModeChange(m.value)}
              disabled={isReady}
              title={`${m.desc} · 快捷键 ${m.hint}`}
              className={`px-2 py-2 rounded-xl border text-xs font-bold transition-all cursor-pointer flex items-center justify-center gap-1.5 ${activeMode === m.value ? 'border-orange-500 bg-orange-50 text-orange-600 shadow-sm' : 'border-slate-200 bg-white text-slate-600 hover:border-orange-200 hover:bg-orange-50/40'} ${isReady ? 'opacity-50 cursor-not-allowed' : ''}`}
            >
              <span>{m.label}</span>
              <kbd className="text-[9px] px-1 py-0.5 rounded bg-slate-100 text-slate-400 border border-slate-200 font-mono">{m.hint}</kbd>
            </button>
          ))}
        </div>

        <div className="flex items-center justify-between pt-1">
          <span className="text-[10px] font-bold text-slate-400 uppercase tracking-wider">偏好定位</span>
          <span className="text-[10px] text-slate-300 font-medium">快捷键 Alt+1~4</span>
        </div>
        <div className="grid grid-cols-2 gap-2">
          {USER_ROLES.map((r) => (
            <button
              key={r.value}
              onClick={() => onRoleChange(r.value)}
              disabled={isReady}
              title={`${r.desc} · 快捷键 ${r.hint}`}
              className={`px-3 py-2 rounded-xl border text-xs font-bold transition-all cursor-pointer flex items-center justify-between ${activeRole === r.value ? 'border-orange-500 bg-orange-50 text-orange-600 shadow-sm' : 'border-slate-200 bg-white text-slate-600 hover:border-orange-200 hover:bg-orange-50/40'} ${isReady ? 'opacity-50 cursor-not-allowed' : ''}`}
            >
              <span>{r.label}</span>
              <kbd className="text-[9px] px-1 py-0.5 rounded bg-slate-100 text-slate-400 border border-slate-200 font-mono">{r.hint}</kbd>
            </button>
          ))}
        </div>
      </div>

      {apiError && (
        <motion.div initial={{ opacity: 0, y: -10 }} animate={{ opacity: 1, y: 0 }} className="mb-4 p-4 bg-rose-50 border border-rose-200 rounded-2xl flex items-start gap-3 shadow-sm">
          <AlertCircle className="w-5 h-5 text-rose-500 shrink-0 mt-0.5" />
          <div>
            <p className="text-sm font-bold text-rose-700">引擎连接失败</p>
            <p className="text-xs text-rose-500 mt-1 leading-relaxed">{apiError}</p>
          </div>
        </motion.div>
      )}

      <div className="flex-1 flex flex-col mb-8">
        <div className={`flex-1 min-h-[220px] p-5 bg-white rounded-2xl shadow-[0_4px_20px_rgba(0,0,0,0.03)] border transition-all duration-300 flex flex-col group relative ${isReady ? 'border-emerald-200 bg-emerald-50/30' : 'border-slate-200 focus-within:border-orange-400 focus-within:shadow-xl focus-within:ring-4 focus-within:ring-orange-50'}`}>
          <div className="flex justify-between items-center mb-3">
            <span className="text-xs font-bold text-slate-400 uppercase tracking-wider flex items-center gap-1.5">
              <MessageSquare className="w-3.5 h-3.5" /> {historyLength > 0 ? "追问/精进当前路线需求" : "你的个性化旅行诉求"}
            </span>
            <div className="flex items-center gap-2">
              <button onClick={toggleVoice} disabled={isReady} title="语音输入" className={`p-1.5 rounded-lg border transition-colors cursor-pointer ${listening ? 'bg-rose-50 border-rose-200 text-rose-500' : 'bg-slate-50 border-slate-200 text-slate-500 hover:text-orange-500 hover:border-orange-200'} ${isReady ? 'opacity-50 cursor-not-allowed' : ''}`}>
                <Mic className={`w-3.5 h-3.5 ${listening ? 'animate-pulse' : ''}`} />
              </button>
              {isReady && <span className="text-[10px] bg-emerald-100 text-emerald-600 px-2.5 py-1 rounded-full font-bold flex items-center gap-1"><Check className="w-3 h-3"/> 已提交推演</span>}
            </div>
          </div>
          
          <VoiceIntentChips parsed={parsed} />
          <textarea 
            data-testid="intent-input"
            disabled={isReady}
            value={userIntent}
            onChange={(e) => setUserIntent(e.target.value)}
            className="w-full flex-1 bg-transparent resize-none outline-none text-sm text-slate-700 placeholder:text-slate-300 font-medium leading-relaxed custom-scrollbar"
            placeholder={historyLength > 0 ? "例如：还是去那儿，但是把第二天的午餐平替成便宜一点的老字号小吃..." : "例如：我们打算去乌鲁木齐玩3天，想看大巴扎和博物馆，吃地道手抓肉，下午不能太累..."}
          ></textarea>
        </div>
      </div>

      <div className="mt-auto pt-4">
        <button 
          onClick={onSubmit} 
          disabled={isReady || userIntent.trim().length === 0}
          className={`w-full py-4 rounded-2xl font-bold text-sm flex items-center justify-center gap-2 transition-all duration-300 shadow-lg cursor-pointer ${isReady ? 'bg-emerald-500 text-white shadow-emerald-200' : userIntent.trim() ? 'bg-orange-500 text-white hover:bg-orange-600 shadow-orange-200/60 hover:-translate-y-0.5' : 'bg-slate-200 text-slate-400 cursor-not-allowed'}`}
        >
          {isReady ? (
            <><RefreshCw className="w-4 h-4 animate-spin" /> 多智能体博弈寻优中...</>
          ) : (
            <>{historyLength > 0 ? "追加诉求并迭代路书" : "锁定意图并开始推演"} <ArrowRight className="w-4 h-4" /></>
          )}
        </button>
      </div>
    </motion.div>
  );
}
