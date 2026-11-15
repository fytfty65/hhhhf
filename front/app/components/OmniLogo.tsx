'use client';

/**
 * OmniLogo — 系统图标（航路环 + 航点）
 *
 * 与 app/icon.svg（favicon）同一份造型：
 * 墨蓝圆角底 → 暖色 300° 航路环 → 右上角青色航点。
 * 环的缺口交给航点收尾，读作「一条走到站的路线」，而不是一枚普通圆环。
 */

// ================= 系统图标 =================
export default function OmniLogo({ className = "w-8 h-8" }: { className?: string }) {
  return (
    <svg viewBox="0 0 48 48" xmlns="http://www.w3.org/2000/svg" className={className} aria-hidden="true">
      <defs>
        <linearGradient id="omniTileBase" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stopColor="#16283F" />
          <stop offset="0.5" stopColor="#0D1827" />
          <stop offset="1" stopColor="#080F1A" />
        </linearGradient>
        <radialGradient id="omniWarmGlow" cx="0.2" cy="0.1" r="0.8">
          <stop offset="0" stopColor="#FF8E53" stopOpacity="0.26" />
          <stop offset="0.6" stopColor="#FF8E53" stopOpacity="0.05" />
          <stop offset="1" stopColor="#FF8E53" stopOpacity="0" />
        </radialGradient>
        <radialGradient id="omniCoolGlow" cx="0.93" cy="0.95" r="0.75">
          <stop offset="0" stopColor="#4FACFE" stopOpacity="0.20" />
          <stop offset="1" stopColor="#4FACFE" stopOpacity="0" />
        </radialGradient>
        <linearGradient id="omniRimLight" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stopColor="#FFFFFF" stopOpacity="0.22" />
          <stop offset="0.55" stopColor="#FFFFFF" stopOpacity="0.07" />
          <stop offset="1" stopColor="#FFFFFF" stopOpacity="0.03" />
        </linearGradient>
        <linearGradient id="omniRingWarm" x1="0.1" y1="0" x2="0.95" y2="1">
          <stop offset="0" stopColor="#FFDDB6" />
          <stop offset="0.30" stopColor="#FFB271" />
          <stop offset="0.62" stopColor="#FF8A5C" />
          <stop offset="0.88" stopColor="#F76A63" />
          <stop offset="1" stopColor="#E85B6B" />
        </linearGradient>
        <filter id="omniBloom" x="-30%" y="-30%" width="160%" height="160%">
          <feGaussianBlur stdDeviation="2.2" />
        </filter>
        <filter id="omniDrop" x="-20%" y="-20%" width="140%" height="140%">
          <feGaussianBlur stdDeviation="1.2" />
        </filter>
      </defs>

      <rect x="0" y="0" width="48" height="48" rx="12" fill="url(#omniTileBase)" />
      <rect x="0" y="0" width="48" height="48" rx="12" fill="url(#omniWarmGlow)" />
      <rect x="0" y="0" width="48" height="48" rx="12" fill="url(#omniCoolGlow)" />
      <rect x="0.55" y="0.55" width="46.9" height="46.9" rx="11.45" fill="none" stroke="url(#omniRimLight)" strokeWidth="1.1" />

      <path d="M35.88 20.82 A12.3 12.3 0 1 1 27.18 12.12" fill="none" stroke="#FF8E53" strokeOpacity="0.16" strokeWidth="9.2" strokeLinecap="round" filter="url(#omniBloom)" />
      <path d="M35.88 20.82 A12.3 12.3 0 1 1 27.18 12.12" fill="none" stroke="#000000" strokeOpacity="0.30" strokeWidth="6.4" strokeLinecap="round" filter="url(#omniDrop)" transform="translate(0.3 1)" />
      <path d="M35.88 20.82 A12.3 12.3 0 1 1 27.18 12.12" fill="none" stroke="url(#omniRingWarm)" strokeWidth="6.2" strokeLinecap="round" />

      <circle cx="32.70" cy="15.30" r="4.3" fill="#5FE0EF" fillOpacity="0.28" />
      <circle cx="32.70" cy="15.30" r="3.3" fill="#62E3EF" />
      <circle cx="31.55" cy="14.15" r="1.0" fill="#FFFFFF" fillOpacity="0.88" />
    </svg>
  );
}
