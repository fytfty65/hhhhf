import { apiRequest } from './apiClient.ts';

export type WebPushState = 'unsupported' | 'permission-default' | 'permission-denied' | 'subscribed' | 'available' | 'disabled';

const vapidPublicKey = () => String(process.env.NEXT_PUBLIC_WEB_PUSH_VAPID_PUBLIC_KEY || '').trim();

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

  // —— B 方案：运输层改用 FCM（VAPID 公钥 = Firebase 控制台 → Cloud Messaging → Web Push 证书那串 B...）——
  // 状态机、文案与降级行为都保持原样：拿不到凭据就退回"站内通知"，绝不假装订阅成功。
  try {
    const publicConfig = await apiRequest<{ enabled?: boolean }>('/api/push/config', {
      origin: 'self',
      auth: false,
      retries: 0,
    }).catch(() => null);
    if (!publicConfig || publicConfig.enabled !== true) {
      return { state: 'disabled', message: '尚未配置 Web Push 公钥，将继续使用站内通知' };
    }
    const [{ initializeApp, getApps }, messagingModule] = await Promise.all([
      import('firebase/app'),
      import('firebase/messaging'),
    ]);
    const supported = await messagingModule.isSupported().catch(() => false);
    if (!supported) return { state: 'unsupported', message: '当前浏览器不支持推送，将继续使用站内通知' };

    const app = getApps().length > 0 ? getApps()[0] : initializeApp(publicConfig);
    // ⚠️ 必须显式注册并用**这个** SW：`navigator.serviceWorker.ready` 可能解析到别的注册
    //    （旧代码在 layout 里注册了 /sw.js，同作用域会把 FCM 的 SW 顶掉）。
    const registration = await navigator.serviceWorker.register('/firebase-messaging-sw.js');
    await navigator.serviceWorker.ready;
    const deviceToken = await messagingModule.getToken(messagingModule.getMessaging(app), {
      vapidKey: vapidPublicKey(),
      serviceWorkerRegistration: registration,
    });
    if (!deviceToken) return { state: 'available', message: '未能取得推送凭据，已保留站内通知' };

    await apiRequest('/api/v1/notifications/push/fcm-token', {
      method: 'POST',
      body: { token: deviceToken, platform: String(navigator.userAgent || '').slice(0, 32) },
      retries: 0,
    });
    return { state: 'subscribed', message: '浏览器通知已开启，重大风险会提醒你' };
  } catch (error) {
    // 不许把"依赖没装"糊成"网络或浏览器限制" —— 实测就是这么被藏起来的：
    // front 里没装 firebase 时 `import('firebase/app')` 抛 module-not-found，
    // 被笼统的 catch 吞掉后只显示"可能是网络问题"，排查方向被带偏。
    const text = String((error as { message?: string })?.message || error || '');
    if (/cannot find module|failed to resolve|module not found/i.test(text)) {
      return { state: 'disabled', message: '推送依赖未安装（front 目录缺少 firebase），已保留站内通知' };
    }
    return { state: 'available', message: '推送登记未完成（可能是网络或浏览器限制），已保留站内通知' };
  }
}

export async function disableWebPushSubscription() {
  if (typeof navigator === 'undefined' || !('serviceWorker' in navigator)) return;
  let deviceToken = '';
  try {
    const [{ getApps }, messagingModule] = await Promise.all([import('firebase/app'), import('firebase/messaging')]);
    const apps = getApps();
    if (apps.length > 0) {
      const messaging = messagingModule.getMessaging(apps[0]);
      const registration = await navigator.serviceWorker.register('/firebase-messaging-sw.js');
      await navigator.serviceWorker.ready;
      deviceToken = (await messagingModule.getToken(messaging, {
        vapidKey: vapidPublicKey(),
        serviceWorkerRegistration: registration,
      }).catch(() => '')) || '';
      await messagingModule.deleteToken(messaging).catch(() => undefined);
    }
  } catch {
    /* 取消订阅失败不能卡住用户，下面仍然尝试服务端注销 */
  }
  try {
    await apiRequest('/api/v1/notifications/push/fcm-token', {
      method: 'DELETE',
      query: { token: deviceToken || undefined },
      retries: 0,
    });
  } catch {
    /* 服务端注销失败也保持安静：本地已 deleteToken，之后不会再收到 */
  }
}
