// Route-level loading UI.
//
// The product's entry route is a single large client component that restores
// the session from localStorage and then mounts the whole app tree. Without
// this file the browser shows nothing at all during that window, which on a
// cold cache looks like a broken page. This keeps the first paint on-brand and
// tells the user something is happening.

export default function Loading() {
  return (
    <div
      style={{
        minHeight: '100dvh',
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        justifyContent: 'center',
        gap: '16px',
        padding: '24px',
        background: 'linear-gradient(160deg, #0b1220 0%, #131c2e 55%, #1b2740 100%)',
        color: '#e8eef8',
        fontFamily: 'system-ui, -apple-system, "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif',
      }}
    >
      <div
        aria-hidden="true"
        style={{
          width: '34px',
          height: '34px',
          borderRadius: '50%',
          border: '3px solid rgba(125,211,252,0.25)',
          borderTopColor: '#38bdf8',
          animation: 'omniroute-spin 0.8s linear infinite',
        }}
      />
      <p style={{ fontSize: '13px', color: '#9fb0c9', margin: 0 }}>正在载入 OmniRoute…</p>
      <span role="status" style={{ position: 'absolute', width: 1, height: 1, overflow: 'hidden', clip: 'rect(0 0 0 0)' }}>
        正在载入
      </span>
      <style>{'@keyframes omniroute-spin { to { transform: rotate(360deg) } }'}</style>
    </div>
  );
}
