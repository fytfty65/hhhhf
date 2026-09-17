'use client';

/**
 * Lobby selection cards.
 *
 * Extracted verbatim from ContextualLobby.tsx during the file split. Both are
 * presentational: they receive their icon and colours as props, so they carry no
 * dependency on the lobby's state and could be moved without behaviour change.
 * Props stay typed `any` to match the call sites exactly (the parent passes
 * inline lucide elements and Tailwind class strings).
 */

import React from 'react';
import { motion } from 'framer-motion';
import { Check } from 'lucide-react';

export function TravelModeCard({ title, desc, icon, color, selected, onClick }: any) {
  return (
    <motion.div whileHover={{ scale: 1.02 }} whileTap={{ scale: 0.98 }} onClick={onClick} className={`cursor-pointer p-5 sm:p-6 rounded-2xl border transition-all duration-300 flex flex-col group ${selected ? 'border-orange-500 bg-orange-50/60 shadow-md' : 'border-slate-200 bg-white hover:bg-slate-50'}`}>
      <div className={`mb-4 w-11 h-11 rounded-xl flex items-center justify-center transition-transform ${selected ? 'bg-orange-500 text-white shadow-sm' : 'bg-slate-100 text-slate-500 group-hover:scale-110'}`}>
        <div className={selected ? 'text-white' : color}>{icon}</div>
      </div>
      <div>
        <h3 className="text-base sm:text-lg font-extrabold text-slate-800 mb-1">{title}</h3>
        <p className="text-xs text-slate-500 font-medium leading-relaxed">{desc}</p>
      </div>
    </motion.div>
  );
}

export function PreferenceCard({ icon, title, desc, selected, onClick, color }: any) {
  return (
    <motion.div whileHover={{ scale: 1.02 }} whileTap={{ scale: 0.97 }} onClick={onClick} className={`cursor-pointer p-4 rounded-xl border-2 transition-all relative ${selected ? color : 'border-slate-100 bg-white'}`}>
      {selected && <div className="absolute top-3 right-3 text-orange-500"><Check className="w-4 h-4" /></div>}
      <div className={`mb-2 ${selected ? '' : 'text-slate-400'}`}>{React.cloneElement(icon, { className: 'w-5 h-5' })}</div>
      <h4 className={`text-sm font-extrabold mb-1 ${selected ? 'text-slate-900' : 'text-slate-700'}`}>{title}</h4>
      <p className={`text-[11px] font-medium leading-snug ${selected ? 'text-slate-700' : 'text-slate-500'}`}>{desc}</p>
    </motion.div>
  );
}
