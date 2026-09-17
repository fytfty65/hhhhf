'use client';

/**
 * OmniLogo — 系统图标（双水滴渐变 SVG Logo）
 *
 * Extracted verbatim from ContextualLobby.tsx during the file split (批 7)。
 * 仅搬运：props 签名、类名与文案逐字保留，未做任何行为改动。
 */


// ================= 系统图标 =================
export default function OmniLogo({ className = "w-8 h-8" }: { className?: string }) {
  return (
    <svg viewBox="0 0 48 48" fill="none" xmlns="http://www.w3.org/2000/svg" className={className}>
      <defs>
        <linearGradient id="warmGrad" x1="0%" y1="0%" x2="100%" y2="100%">
          <stop offset="0%" stopColor="#FF8E53" /> 
          <stop offset="100%" stopColor="#FF6B6B" />
        </linearGradient>
        <linearGradient id="coolGrad" x1="0%" y1="0%" x2="100%" y2="100%">
          <stop offset="0%" stopColor="#4FACFE" /> 
          <stop offset="100%" stopColor="#00F2FE" />
        </linearGradient>
      </defs>
      <path d="M22 8C14.268 8 8 14.268 8 22C8 33.5 22 44 22 44C22 44 36 33.5 36 22C36 14.268 29.732 8 22 8Z" fill="url(#warmGrad)" fillOpacity="0.9" style={{ mixBlendMode: 'normal' }}/>
      <circle cx="22" cy="22" r="5" fill="#FFFFFF" />
      <path d="M30 14C24.477 14 20 18.477 20 24C20 32 30 40 30 40C30 40 40 32 40 24C40 18.477 35.523 14 30 14Z" fill="url(#coolGrad)" fillOpacity="0.95" style={{ mixBlendMode: 'normal' }}/>
      <circle cx="30" cy="24" r="3" fill="#FFFFFF" />
    </svg>
  );
}
