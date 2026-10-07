import test from 'node:test';
import assert from 'node:assert/strict';
import { priceProvenance, priceValueOf, officialProfileOf } from './priceProvenance.ts';

// 用的是**真实方案里的节点形状**（2026-10-07 的三条定向通道跑出来的结果）

test('priceValueOf 去掉后端拼在价格后面的括号说明', () => {
  assert.equal(priceValueOf('¥110（票务平台价，非官方）'), '¥110');
  assert.equal(priceValueOf('¥30'), '¥30');
  assert.equal(priceValueOf('暂无供应商数据'), '暂无供应商数据');
  assert.equal(priceValueOf(''), '');
});

test('官方页价格标为可核实，并带来源链接', () => {
  const node = {
    name: '天鹅泉湿地公园',
    cost_estimate: '¥30',
    data_sources: { cost_estimate: 'web_page' },
    price_source_url: 'http://ylwl.xjyl.gov.cn/show/scenicspot/detail?id=581',
    price_excerpt: '门票价格 门票30元/人',
  };
  const info = priceProvenance(node);
  assert.ok(info);
  assert.equal(info.value, '¥30');
  assert.equal(info.kind, 'official');
  assert.equal(info.label, '官方价（可核实）');
  assert.equal(info.verified, true);
  assert.ok(info.url.includes('ylwl.xjyl.gov.cn'));
  assert.ok(info.detail.includes('门票30元'));
});

test('票务平台价标明非官方，票种名照原样带出', () => {
  const node = {
    name: '库尔勒体育公园',
    cost_estimate: '¥28（票务平台价，非官方）',
    data_sources: { cost_estimate: 'platform' },
    price_source_url: 'https://piao.qunar.com/ticket/detail_1735077393.html',
    price_excerpt: '库尔勒体育公园门票成人票',
  };
  const info = priceProvenance(node);
  assert.ok(info);
  assert.equal(info.value, '¥28');
  assert.equal(info.kind, 'platform');
  assert.equal(info.verified, false);
  assert.equal(info.label, '票务平台价（非官方）');
  assert.equal(info.detail, '库尔勒体育公园门票成人票');
});

test('网摘价标成估算，不会看起来像官方价', () => {
  const info = priceProvenance({ cost_estimate: '¥45', data_sources: { cost_estimate: 'web' } });
  assert.ok(info);
  assert.equal(info.kind, 'estimate');
  assert.equal(info.label, '网摘估算');
  assert.equal(info.verified, false);
});

test('供应商实时价（酒店）单独标出来', () => {
  const info = priceProvenance({
    cost_estimate: '¥546',
    data_sources: { cost_estimate: 'rollinggo' },
    price_excerpt: '查价成功。2晚总价：1,092CNY（约546CNY/晚）',
  });
  assert.ok(info);
  assert.equal(info.kind, 'supplier');
  assert.equal(info.label, '供应商实时价（可核实）');
});

test('有价格但没标来源时如实说"来源未标注"，不默认可信', () => {
  const info = priceProvenance({ cost_estimate: '¥80' });
  assert.ok(info);
  assert.equal(info.kind, 'unknown');
  assert.equal(info.verified, false);
});

test('没有价格（或暂无供应商数据）时返回 null，不渲染空标签', () => {
  assert.equal(priceProvenance({ cost_estimate: '暂无供应商数据' }), null);
  assert.equal(priceProvenance({ cost_estimate: '' }), null);
  assert.equal(priceProvenance(null), null);
});

test('officialProfileOf 取文旅部名录登记的官网/官方预约入口', () => {
  const plan = {
    official_profiles: {
      喀什古城: { official_name: '喀什地区喀什古城景区', official_site: '', official_booking: 'https://x/ticket' },
      博斯腾湖: { official_name: '博斯腾湖景区', official_site: 'https://b', official_booking: '' },
      没有入口的: { official_name: '某景区', official_site: '', official_booking: '' },
    },
  };
  assert.equal(officialProfileOf(plan, '喀什古城')?.booking, 'https://x/ticket');
  assert.equal(officialProfileOf(plan, '博斯腾湖')?.site, 'https://b');
  assert.equal(officialProfileOf(plan, '没有入口的'), null);
  assert.equal(officialProfileOf(plan, '不存在的景点'), null);
  assert.equal(officialProfileOf({}, '喀什古城'), null);
});
