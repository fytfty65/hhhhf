export default function NotFound() {
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
      <div style={{ textAlign: 'center', maxWidth: '420px' }}>
        <div style={{ fontSize: '34px', marginBottom: '12px' }} aria-hidden="true">
          🗺️
        </div>
        <h1 style={{ fontSize: '18px', fontWeight: 600, margin: '0 0 8px' }}>没有找到这个页面</h1>
        <p style={{ fontSize: '13px', lineHeight: 1.7, color: '#9fb0c9', margin: '0 0 20px' }}>
          链接可能已失效，或该功能尚未开放。回到首页即可继续规划行程。
        </p>
        <a
          href="/"
          style={{
            display: 'inline-block',
            padding: '10px 20px',
            borderRadius: '10px',
            fontSize: '13px',
            fontWeight: 600,
            textDecoration: 'none',
            color: '#0b1220',
            background: 'linear-gradient(135deg, #7dd3fc, #38bdf8)',
          }}
        >
          返回首页
        </a>
      </div>
    </div>
  );
}
