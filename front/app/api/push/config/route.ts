import { NextResponse } from 'next/server';

/**
 * 把 FCM 的**公开**配置交给 Service Worker（`public/firebase-messaging-sw.js`）。
 *
 * 为什么要有这个路由：Service Worker 读不到 Next 的环境变量，如果让它自己写死配置，
 * 就会出现"env 填了、SW 忘了改"的问题（这类问题极难排查）。这里统一从环境变量读，
 * 于是**填密钥只发生在一个地方**（`front/.env.local`）。
 *
 * 安全边界：这些值本来就是公开的（会进前端包），**服务账号 JSON 绝不在这里出现**
 * （它只在 gateway 侧由 FCM_SERVICE_ACCOUNT_JSON_PATH 读取）。
 */
export async function GET() {
  const config = {
    apiKey: process.env.NEXT_PUBLIC_FIREBASE_API_KEY || '',
    authDomain: process.env.NEXT_PUBLIC_FIREBASE_AUTH_DOMAIN || '',
    projectId: process.env.NEXT_PUBLIC_FIREBASE_PROJECT_ID || '',
    storageBucket: process.env.NEXT_PUBLIC_FIREBASE_STORAGE_BUCKET || '',
    messagingSenderId: process.env.NEXT_PUBLIC_FIREBASE_MESSAGING_SENDER_ID || '',
    appId: process.env.NEXT_PUBLIC_FIREBASE_APP_ID || '',
  };
  // 缺任何一项就如实回 enabled:false，让前端/ SW 走"站内通知"降级，而不是拿半份配置去初始化
  const missing = Object.entries(config)
    .filter(([, value]) => !value)
    .map(([key]) => key);
  const enabled = missing.length === 0;

  return NextResponse.json(
    enabled ? { ...config, enabled: true } : { enabled: false, missing },
    { headers: { 'Cache-Control': 'no-store' } },
  );
}
