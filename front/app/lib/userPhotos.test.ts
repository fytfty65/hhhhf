import test from 'node:test';
import assert from 'node:assert/strict';

import {
  MAX_USER_PHOTO_BYTES,
  clearUserPhoto,
  readUserPhoto,
  saveUserPhoto,
  type StorageLike,
} from './userPhotos.ts';

/** 最小可用的假存储：可注入"配额已满"来验证错误路径。 */
function fakeStorage(options: { failOnSet?: boolean } = {}): StorageLike & { data: Map<string, string> } {
  const data = new Map<string, string>();
  return {
    data,
    getItem: (key: string) => (data.has(key) ? (data.get(key) as string) : null),
    setItem: (key: string, value: string) => {
      if (options.failOnSet) throw new Error('QuotaExceededError');
      data.set(key, value);
    },
    removeItem: (key: string) => {
      data.delete(key);
    },
  };
}

const PHOTO = 'data:image/jpeg;base64,AAAA';

test('存进去再读出来（按地点名各自独立）', () => {
  const store = fakeStorage();
  assert.equal(saveUserPhoto('博斯腾湖', PHOTO, store).ok, true);
  assert.equal(readUserPhoto('博斯腾湖', store), PHOTO);
  assert.equal(readUserPhoto('别的地方', store), '', '不同地点不能串图');
});

test('非图片与超大图片都要明确拒绝，不能悄悄失败', () => {
  const store = fakeStorage();
  const notImage = saveUserPhoto('某地', 'https://evil.example/x.jpg', store);
  assert.equal(notImage.ok, false);
  assert.match(notImage.ok ? '' : notImage.reason, /只支持图片/);

  const tooBig = saveUserPhoto('某地', 'data:image/png;base64,' + 'A'.repeat(MAX_USER_PHOTO_BYTES), store);
  assert.equal(tooBig.ok, false);
  assert.match(tooBig.ok ? '' : tooBig.reason, /太大/);
});

test('浏览器配额满要如实报错（而不是静默丢失）', () => {
  const result = saveUserPhoto('某地', PHOTO, fakeStorage({ failOnSet: true }));
  assert.equal(result.ok, false);
  assert.match(result.ok ? '' : result.reason, /空间不足/);
});

test('坏数据/没有存储时读出来是空串，绝不抛异常', () => {
  const store = fakeStorage();
  store.setItem('omni:user-photo:坏数据', 'not-a-data-url');
  assert.equal(readUserPhoto('坏数据', store), '');
  assert.equal(readUserPhoto('', store), '');
  const withoutStorage: StorageLike = {
    getItem: () => {
      throw new Error('blocked');
    },
    setItem: () => {
      throw new Error('blocked');
    },
    removeItem: () => {
      throw new Error('blocked');
    },
  };
  assert.equal(readUserPhoto('某地', withoutStorage), '');
});

test('删除后就查不到了', () => {
  const store = fakeStorage();
  saveUserPhoto('某地', PHOTO, store);
  clearUserPhoto('某地', store);
  assert.equal(readUserPhoto('某地', store), '');
});
