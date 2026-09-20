/**
 * 用户自己的实拍照片（⑥）：**真归属**的图片来源，长期最靠谱的一条。
 *
 * 为什么先做在本地：用户上传的图要真有用，得有存储与合规（审核/隐私）配套；在配套没做好之前，
 * 先把"你自己拍的图"存在**你自己的浏览器**里 —— 不落服务器、不外传、不需要审核，也不会
 * 冒充成"这个地方的官方照片"。等以后做了图库，这份 key 结构可以直接搬过去。
 *
 * 三条硬规矩（与项目"不捏造"口径一致）：
 * 1. 只接受图片类型，且单张不超过 `MAX_USER_PHOTO_BYTES`（localStorage 容量有限，
 *    超了要**明确报错**，不能悄悄失败）；
 * 2. 读不出来／解析不了就当作没有（不抛异常炸掉整个卡片）；
 * 3. 展示时必须标明"我上传的实拍"，不能与官方实景图混为一谈。
 */

export const MAX_USER_PHOTO_BYTES = 1_500_000; // 约 1.5MB：再大 localStorage 基本存不下
const KEY_PREFIX = 'omni:user-photo:';

export type StorageLike = Pick<Storage, 'getItem' | 'setItem' | 'removeItem'>;

function storageOf(storage?: StorageLike): StorageLike | null {
  if (storage) return storage;
  try {
    if (typeof window !== 'undefined' && window.localStorage) return window.localStorage;
  } catch {
    return null;
  }
  return null;
}

function keyOf(name: string): string {
  return KEY_PREFIX + String(name || '').trim();
}

/** 读用户为某个地点上传的实拍图（data URL）；没有/坏了都返回空串。 */
export function readUserPhoto(name: string, storage?: StorageLike): string {
  const store = storageOf(storage);
  const key = keyOf(name);
  if (!store || !key.slice(KEY_PREFIX.length)) return '';
  try {
    const value = store.getItem(key) || '';
    return value.startsWith('data:image/') ? value : '';
  } catch {
    return '';
  }
}

export type SaveResult = { ok: true } | { ok: false; reason: string };

/** 存一张用户实拍图；超过体积上限或浏览器拒绝（配额满）时**如实报错**。 */
export function saveUserPhoto(name: string, dataUrl: string, storage?: StorageLike): SaveResult {
  const store = storageOf(storage);
  const key = keyOf(name);
  if (!store || !key.slice(KEY_PREFIX.length)) return { ok: false, reason: '没有可用的本地存储' };
  const value = String(dataUrl || '');
  if (!value.startsWith('data:image/')) return { ok: false, reason: '只支持图片文件' };
  if (value.length > MAX_USER_PHOTO_BYTES) {
    return { ok: false, reason: `图片太大（超过 ${Math.round(MAX_USER_PHOTO_BYTES / 1024)}KB），请压缩后再传` };
  }
  try {
    store.setItem(key, value);
    return { ok: true };
  } catch {
    return { ok: false, reason: '浏览器本地空间不足，先删掉一些旧照片再试' };
  }
}

export function clearUserPhoto(name: string, storage?: StorageLike): void {
  const store = storageOf(storage);
  const key = keyOf(name);
  if (!store) return;
  try {
    store.removeItem(key);
  } catch {
    /* 删不掉就算了，不值得打断用户 */
  }
}

/** 读取文件并转成 data URL；非图片或读失败返回空串（调用方据此提示用户）。 */
export function fileToDataUrl(file: File): Promise<string> {
  return new Promise((resolve) => {
    if (!file || !String(file.type || '').startsWith('image/')) {
      resolve('');
      return;
    }
    try {
      const reader = new FileReader();
      reader.onload = () => resolve(typeof reader.result === 'string' ? reader.result : '');
      reader.onerror = () => resolve('');
      reader.readAsDataURL(file);
    } catch {
      resolve('');
    }
  });
}
