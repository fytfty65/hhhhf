'use client';

// Root-level error boundary.
//
// app/error.tsx only catches errors thrown while rendering a route *inside* the
// root layout. A failure in the layout itself (or in a provider it mounts)
// escapes it entirely, and Next falls back to its own unstyled error page with
// no way back into the product. This file is what actually guarantees the user
// never sees a bare white screen: React requires global-error to render its own
// <html> and <body>.

import { useEffect } from 'react';

export default function GlobalError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  useEffect(() => {
    console.error('[omniroute] fatal application error:', error);
  }, [error]);

  const buttonStyle = {
    padding: '10px 20px',
    borderRadius: '10px',
    fontSize: '13px',
    fontWeight: 600,
    cursor: 'pointer',
  } as const;

  return (
    <html lang="zh-CN">
      <body
        style={{
          margin: 0,
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
          <h1 style={{ fontSize: '18px', fontWeight: 600, margin: '0 0 8px' }}>应用启动失败</h1>
          <p style={{ fontSize: '13px', lineHeight: 1.7, color: '#9fb0c9', margin: '0 0 20px' }}>
            页面在加载时遇到了无法恢复的问题。已保存的登录状态与本地数据不受影响，可以重试或重新载入应用。
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
              style={{ ...buttonStyle, border: 'none', color: '#0b1220', background: 'linear-gradient(135deg, #7dd3fc, #38bdf8)' }}
            >
              重试
            </button>
            <button
              type="button"
              onClick={() => {
                if (typeof window !== 'undefined') window.location.reload();
              }}
              style={{ ...buttonStyle, color: '#cfe0f5', background: 'transparent', border: '1px solid rgba(255,255,255,0.22)' }}
            >
              重新载入
            </button>
          </div>
        </div>
      </body>
    </html>
  );
}
