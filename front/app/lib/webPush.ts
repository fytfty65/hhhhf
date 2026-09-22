const API_BASE = (process.env.NEXT_PUBLIC_API_BASE || (typeof window !== 'undefined' ? `${window.location.protocol}//${window.location.hostname}:8080` : '')).replace(/\/$/, '');

async function pushApiJson(input: RequestInfo | URL, init: RequestInit = {}) {
  const headers = new Headers(init.headers || {});
  if (typeof window !== 'undefined') {
    try {
      const raw = window.localStorage.getItem('omni_user');
      const token = raw ? JSON.parse(raw)?.token : '';
      if (token && !headers.has('Authorization')) headers.set('Authorization', `Bearer ${token}`);
    } catch { /* notification settings must degrade safely */ }
  }
  const response = await fetch(input, { ...init, headers });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(payload?.error?.message || payload?.error || '通知服务暂不可用');
  return payload;
}

export type WebPushState = 'unsupported' | 'permission-default' | 'permission-denied' | 'subscribed' | 'available' | 'disabled';

const vapidPublicKey = () => String(process.env.NEXT_PUBLIC_WEB_PUSH_VAPID_PUBLIC_KEY || '').trim();

function base64UrlToUint8Array(value: string) {
  const padding = '='.repeat((4 - (value.length % 4)) % 4);
  const normalized = (value + padding).replace(/-/g, '+').replace(/_/g, '/');
  const raw = atob(normalized);
  return Uint8Array.from([...raw].map((char) => char.charCodeAt(0)));
}

export function webPushCapability(): WebPushState {
  if (typeof window === 'undefined' || !('Notification' in window) || !('serviceWorker' in navigator) || !('PushManager' in window)) return 'unsupported';
  if (!vapidPublicKey()) return 'disabled';
  if (Notification.permission === 'denied') return 'permission-denied';
  if (Notification.permission === 'default') return 'permission-default';
  return 'available';
}

export async function requestWebPushSubscription(): Promise<{ state: WebPushState; message?: string }> {
  const capability = webPushCapability();
  if (capability === 'unsupported') return { state: capability, message: '当前浏览器不支持推送，将继续使用站内通知' };
  if (capability === 'disabled') return { state: capability, message: '尚未配置 Web Push 公钥，将继续使用站内通知' };
  if (Notification.permission === 'default') {
    const permission = await Notification.requestPermission();
    if (permission !== 'granted') return { state: 'permission-denied', message: '你拒绝了浏览器通知，仍可在站内查看风险提醒' };
  }
  if (Notification.permission !== 'granted') return { state: 'permission-denied', message: '浏览器通知权限未开启，仍可在站内查看风险提醒' };
  const registration = await navigator.serviceWorker.ready;
  const subscription = await registration.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: base64UrlToUint8Array(vapidPublicKey()) });
  const json = subscription.toJSON();
  if (!json.endpoint || !json.keys?.p256dh || !json.keys.auth) return { state: 'available', message: '浏览器未返回完整推送凭据，已保留站内通知' };
  await pushApiJson(`${API_BASE}/api/v1/notifications/push/subscription`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ endpoint: json.endpoint, keys: json.keys, user_agent: navigator.userAgent }),
  });
  return { state: 'subscribed', message: '浏览器通知已开启，重大风险会提醒你' };
}

export async function disableWebPushSubscription() {
  if (typeof navigator !== 'undefined' && 'serviceWorker' in navigator) {
    const registration = await navigator.serviceWorker.ready;
    const subscription = await registration.pushManager.getSubscription();
    const endpoint = subscription?.endpoint || '';
    await subscription?.unsubscribe();
    await pushApiJson(`${API_BASE}/api/v1/notifications/push/subscription${endpoint ? `?endpoint=${encodeURIComponent(endpoint)}` : ''}`, { method: 'DELETE' });
  }
}
