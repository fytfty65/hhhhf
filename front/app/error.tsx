'use client';

import { useEffect } from 'react';

// Route-level error boundary for the whole App Router tree.
//
// Before this file existed there was no error boundary anywhere in the app and
// no not-found/loading convention files. The entire product lives in a single
// client component (app/page.tsx re-exports ContextualLobby), so any uncaught
// exception already unmounted the whole tree — React had nothing to fall back
// to and the user got Next's default error page with all session state gone.
//
// This boundary gives that failure a recoverable surface: a retry that
// re-renders the segment, and a hard reset that returns to the lobby.
export default function GlobalError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  useEffect(() => {
    // Keep the raw error visible in the console for diagnosis; the UI below
    // deliberately shows only a digest so internal details are not rendered.
    console.error('[omniroute] unhandled UI error:', error);
  }, [error]);

  return (
    <div
      style={{
        minHeight: '100dvh',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        padding: '24px',
        background: 'linear-gradient(160deg, #0b1220 0%, #131c2e 55%, #1b2740 100%)',
        color: '#e8eef8',
        fontFamily: 'system-ui, -apple-system, "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif',
      }}
    >
      <div
        style={{
          width: '100%',
          maxWidth: '440px',
          borderRadius: '20px',
          padding: '28px 24px',
          background: 'rgba(255,255,255,0.05)',
          border: '1px solid rgba(255,255,255,0.10)',
          boxShadow: '0 20px 60px rgba(0,0,0,0.45)',
          textAlign: 'center',
        }}
      >
        <div style={{ fontSize: '34px', lineHeight: 1, marginBottom: '12px' }} aria-hidden="true">
          🧭
        </div>
        <h1 style={{ fontSize: '18px', fontWeight: 600, margin: '0 0 8px' }}>页面遇到了一点问题</h1>
        <p style={{ fontSize: '13px', lineHeight: 1.7, color: '#9fb0c9', margin: '0 0 20px' }}>
          当前视图渲染失败，已为你保留登录状态。可以重试当前页面，或返回首页重新开始规划。
        </p>

        {error.digest ? (
          <p
            style={{
              fontSize: '11px',
              color: '#6f819a',
              margin: '0 0 18px',
              fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace',
            }}
          >
            错误编号：{error.digest}
          </p>
        ) : null}

        <div style={{ display: 'flex', gap: '10px', justifyContent: 'center', flexWrap: 'wrap' }}>
          <button
            type="button"
            onClick={() => reset()}
            style={{
              padding: '10px 20px',
              borderRadius: '10px',
              border: 'none',
              cursor: 'pointer',
              fontSize: '13px',
              fontWeight: 600,
              color: '#0b1220',
              background: 'linear-gradient(135deg, #7dd3fc, #38bdf8)',
            }}
          >
            重试
          </button>
          <button
            type="button"
            onClick={() => {
              if (typeof window !== 'undefined') {
                window.location.href = '/';
              }
            }}
            style={{
              padding: '10px 20px',
              borderRadius: '10px',
              cursor: 'pointer',
              fontSize: '13px',
              fontWeight: 600,
              color: '#cfe0f5',
              background: 'transparent',
              border: '1px solid rgba(255,255,255,0.22)',
            }}
          >
            返回首页
          </button>
        </div>
      </div>
    </div>
  );
}
