import { NextResponse } from 'next/server';

// Kept only as a migration guard for clients that still call the removed
// unauthenticated proxy. All planning now goes through the authenticated
// gateway WebSocket at /ws.
export async function POST() {
  return NextResponse.json(
    { success: false, code: 'LEGACY_AGENT_REMOVED', error: '旧 AI 代理已移除，请使用认证后的网关 WebSocket' },
    { status: 410 },
  );
}
