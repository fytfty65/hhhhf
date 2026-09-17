'use client';

/**
 * EditIntentModal — 修改我的旅行画像弹窗（偏好定位 + 具体出行诉求，保存后广播同步）
 *
 * Extracted verbatim from ContextualLobby.tsx during the file split (批 3)。
 * 仅搬运：props 签名、类名与文案逐字保留，未做任何行为改动。
 */

import { useState } from 'react';
import { motion } from 'framer-motion';
import { X } from 'lucide-react';

// ================= 修改个人诉求画像 Modal =================
export default function EditIntentModal({ currentRole, currentIntent, onClose, onSave }: any) {
  const [role, setRole] = useState(currentRole);
  const [intent, setIntent] = useState(currentIntent);
  const roles = ['寻味探索', '视觉体验', '休闲漫步', '深度探索'];

  return (
    <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} className="fixed inset-0 bg-slate-900/40 backdrop-blur-sm z-[110] flex items-center justify-center p-4">
      <motion.div initial={{ scale: 0.95, y: 15 }} animate={{ scale: 1, y: 0 }} exit={{ scale: 0.95, y: -15 }} className="bg-white rounded-3xl w-full max-w-md p-7 shadow-2xl border border-slate-100 relative">
        <button onClick={onClose} className="absolute top-5 right-5 p-1.5 rounded-full hover:bg-slate-100 text-slate-400 hover:text-slate-600 transition-colors cursor-pointer">
          <X className="w-5 h-5" />
        </button>
        <h3 className="text-lg font-black text-slate-800 mb-1">修改我的旅行画像</h3>
        <p className="text-xs text-slate-400 mb-4">修改后将实时同步至同一房间内所有好友的沙盘中</p>

        <div className="mb-4">
          <label className="text-xs font-bold text-slate-600 mb-1.5 block">偏好定位</label>
          <div className="grid grid-cols-2 gap-2">
            {roles.map(r => (
              <button key={r} onClick={() => setRole(r)} className={`py-2 px-3 text-xs font-bold rounded-xl border transition-all cursor-pointer ${role === r ? 'bg-orange-50 border-orange-500 text-orange-600 shadow-2xs' : 'bg-slate-50 border-slate-200 text-slate-600'}`}>{r}</button>
            ))}
          </div>
        </div>

        <div className="mb-5">
          <label className="text-xs font-bold text-slate-600 mb-1 block">具体出行诉求</label>
          <textarea rows={3} value={intent} onChange={(e) => setIntent(e.target.value)} className="w-full p-3 bg-slate-50 border border-slate-200 rounded-xl text-xs outline-none focus:border-orange-500 focus:bg-white resize-none font-medium leading-relaxed" />
        </div>

        <div className="flex gap-2">
          <button onClick={onClose} className="flex-1 py-2.5 bg-slate-100 text-slate-600 font-bold rounded-xl text-xs cursor-pointer">取消</button>
          <button onClick={() => onSave(role, intent)} className="flex-1 py-2.5 bg-orange-500 text-white font-bold rounded-xl text-xs shadow-md shadow-orange-200 cursor-pointer">保存并广播同步</button>
        </div>
      </motion.div>
    </motion.div>
  );
}
