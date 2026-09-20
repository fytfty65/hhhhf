import test from 'node:test';
import assert from 'node:assert/strict';

import { describeSource, isRealPhoto, orderPhotoSources } from './photoPriority.ts';

test('优先级：我的实拍 → 已审核公共实拍 → 高德官方 → 影像兜底', () => {
  const ordered = orderPhotoSources({
    myPhoto: 'data:image/jpeg;base64,MINE',
    publicPhotos: [{ url: 'http://gw/api/v1/photos/aaa', uploader: '老王' }],
    amapPhotos: ['https://aos-comment.amap.com/x.jpg'],
    fallback: { url: 'https://server.arcgisonline.com/sat.png', kind: 'satellite', caption: '位置示意：卫星影像（真实地表，非实景照片）' },
  });
  assert.deepEqual(
    ordered.map((item) => item.kind),
    ['my_photo', 'public_photo', 'amap_official', 'imagery'],
  );
  // 每种来源都带自己的署名，谁也不能冒充谁
  assert.match(ordered[0].credit, /我的实拍/);
  assert.match(ordered[1].credit, /老王/);
  assert.match(ordered[1].credit, /已审核/);
  assert.match(ordered[2].credit, /高德官方/);
  assert.match(ordered[3].credit, /非实景照片/);
});

test('影像类必须标"位置示意"，照片类不能标', () => {
  assert.equal(isRealPhoto('my_photo'), true);
  assert.equal(isRealPhoto('public_photo'), true);
  assert.equal(isRealPhoto('amap_official'), true);
  assert.equal(isRealPhoto('imagery'), false);
  assert.equal(isRealPhoto('street_map'), false);
  assert.match(describeSource('imagery'), /非实景照片/);
  assert.doesNotMatch(describeSource('amap_official'), /非实景照片/);
});

test('空值/重复项被丢掉，绝不产生空 URL 候选', () => {
  const ordered = orderPhotoSources({
    myPhoto: '',
    publicPhotos: [{ url: '' }, { url: 'https://x/1.jpg' }, { url: 'https://x/1.jpg' }],
    amapPhotos: ['', 'https://x/1.jpg'],
    fallback: null,
  });
  assert.deepEqual(ordered.map((item) => item.url), ['https://x/1.jpg']);
});

test('什么都没有就返回空数组（上层显示占位，绝不编图）', () => {
  assert.deepEqual(orderPhotoSources({}), []);
  assert.deepEqual(orderPhotoSources({ fallback: { url: '' } }), []);
});

test('后端没给 kind 时兜底图按"影像"处理（宁可标得保守）', () => {
  const ordered = orderPhotoSources({ fallback: { url: 'https://x/map.png', caption: '位置示意' } });
  assert.equal(ordered[0].kind, 'imagery');
  assert.equal(isRealPhoto(ordered[0].kind), false);
});
