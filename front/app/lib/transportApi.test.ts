// 交通票务查询模块单元测试
import test from 'node:test';
import assert from 'node:assert/strict';
import {
  formatPrice, formatSeats, getTrainTypeLabel, getTrainTypeColor,
  getSeatStatusColor, getSeatStatusBg, getDiscountColor, filterCities,
} from './transportApi.ts';
import { getCached, setCache, clearCache, getCacheSize } from './transportCache.ts';

// ========== 格式化函数测试 ==========

test('formatPrice 正确格式化价格', () => {
  assert.equal(formatPrice(0), '暂无报价');
  assert.equal(formatPrice(-1), '暂无报价');
  assert.equal(formatPrice(580), '¥580');
  assert.equal(formatPrice(1280), '¥1,280');
  assert.equal(formatPrice(9999), '¥9,999');
});

test('formatPrice 处理大额价格', () => {
  assert.equal(formatPrice(10000), '¥10,000');
  assert.equal(formatPrice(100000), '¥100,000');
});

test('formatSeats 正确格式化余票', () => {
  assert.equal(formatSeats(0), '售罄');
  assert.equal(formatSeats(-1), '售罄');
  assert.equal(formatSeats(1), '仅剩 1 张');
  assert.equal(formatSeats(5), '仅剩 5 张');
  assert.equal(formatSeats(6), '余 6 张');
  assert.equal(formatSeats(100), '余 100 张');
});

test('getTrainTypeLabel 正确映射车次类型', () => {
  assert.equal(getTrainTypeLabel('G'), '高铁');
  assert.equal(getTrainTypeLabel('D'), '动车');
  assert.equal(getTrainTypeLabel('C'), '城际');
  assert.equal(getTrainTypeLabel('Z'), '直达');
  assert.equal(getTrainTypeLabel('T'), '特快');
  assert.equal(getTrainTypeLabel('K'), '快速');
  assert.equal(getTrainTypeLabel('P'), '普快');
  assert.equal(getTrainTypeLabel('L'), '临客');
  assert.equal(getTrainTypeLabel('Y'), '旅游');
});

test('getTrainTypeLabel 处理未知类型', () => {
  assert.equal(getTrainTypeLabel(''), '列车');
  assert.equal(getTrainTypeLabel('高铁'), '高铁');
  assert.equal(getTrainTypeLabel('未知'), '未知');
});

test('getTrainTypeColor 返回有效的CSS类名', () => {
  assert.ok(getTrainTypeColor('G').includes('bg-'));
  assert.ok(getTrainTypeColor('D').includes('bg-'));
  assert.ok(getTrainTypeColor('未知').includes('bg-'));
});

test('getSeatStatusColor 根据余票返回正确颜色', () => {
  assert.ok(getSeatStatusColor(0).includes('red'));
  assert.ok(getSeatStatusColor(5).includes('amber'));
  assert.ok(getSeatStatusColor(10).includes('amber'));
  assert.ok(getSeatStatusColor(11).includes('emerald'));
  assert.ok(getSeatStatusColor(100).includes('emerald'));
});

test('getSeatStatusBg 根据余票返回正确背景', () => {
  assert.ok(getSeatStatusBg(0).includes('red'));
  assert.ok(getSeatStatusBg(3).includes('amber'));
  assert.ok(getSeatStatusBg(20).includes('emerald'));
});

test('getDiscountColor 根据折扣返回正确颜色', () => {
  assert.ok(getDiscountColor('8折').includes('orange'));
  assert.ok(getDiscountColor('8.5').includes('orange'));
  assert.ok(getDiscountColor('全价').includes('slate'));
});

// ========== 城市过滤测试 ==========

test('filterCities 空查询返回空数组', () => {
  assert.deepEqual(filterCities(''), []);
  assert.deepEqual(filterCities('  '), []);
});

test('filterCities 精确匹配', () => {
  const result = filterCities('北京');
  assert.ok(result.includes('北京'));
});

test('filterCities 模糊匹配', () => {
  const result = filterCities('杭');
  assert.ok(result.includes('杭州'));
  assert.ok(result.length > 0);
});

test('filterCities 同字不同查询方式返回相同结果', () => {
  const result1 = filterCities('北京');
  const result2 = filterCities(' 北京 ');
  assert.ok(result1.length > 0);
  assert.deepEqual(result1, result2);
});

test('filterCities 最多返回8个结果', () => {
  const result = filterCities('州');
  assert.ok(result.length <= 8);
});

test('filterCities 无匹配返回空数组', () => {
  const result = filterCities('xyz123');
  assert.deepEqual(result, []);
});

// ========== 缓存测试 ==========

test('getCached 未命中返回null', () => {
  clearCache();
  const result = getCached({ from: '北京', to: '上海', date: '2026-10-01', type: 'flight' });
  assert.equal(result, null);
});

test('setCache 和 getCached 基本流程', () => {
  clearCache();
  const mockData = {
    type: 'flight' as const,
    from: '北京', to: '上海', date: '2026-10-01',
    results: [], total: 0, cached: false,
  };

  setCache({ from: '北京', to: '上海', date: '2026-10-01', type: 'flight' }, mockData);

  const cached = getCached({ from: '北京', to: '上海', date: '2026-10-01', type: 'flight' });
  assert.notEqual(cached, null);
  assert.equal(cached?.from, '北京');
  assert.equal(cached?.to, '上海');
  assert.equal(cached?.cached, true); // getCached 标记为缓存
});

test('不同查询参数缓存隔离', () => {
  clearCache();
  const flightData = { type: 'flight' as const, from: '北京', to: '上海', date: '2026-10-01', results: [], total: 0, cached: false };
  const trainData = { type: 'train' as const, from: '北京', to: '上海', date: '2026-10-01', results: [], total: 0, cached: false };

  setCache({ from: '北京', to: '上海', date: '2026-10-01', type: 'flight' }, flightData);
  setCache({ from: '北京', to: '上海', date: '2026-10-01', type: 'train' }, trainData);

  const flightCache = getCached({ from: '北京', to: '上海', date: '2026-10-01', type: 'flight' });
  const trainCache = getCached({ from: '北京', to: '上海', date: '2026-10-01', type: 'train' });

  assert.notEqual(flightCache, null);
  assert.notEqual(trainCache, null);
  assert.equal(flightCache?.type, 'flight');
  assert.equal(trainCache?.type, 'train');
});

test('clearCache 清空所有缓存', () => {
  clearCache();
  const mockData = { type: 'flight' as const, from: '北京', to: '上海', date: '2026-10-01', results: [], total: 0, cached: false };
  setCache({ from: '北京', to: '上海', date: '2026-10-01', type: 'flight' }, mockData);
  assert.equal(getCacheSize(), 1);

  clearCache();
  assert.equal(getCacheSize(), 0);
});

test('getCacheSize 返回正确数量', () => {
  clearCache();
  assert.equal(getCacheSize(), 0);

  const mockData = { type: 'flight' as const, from: '北京', to: '上海', date: '2026-10-01', results: [], total: 0, cached: false };
  setCache({ from: '北京', to: '上海', date: '2026-10-01', type: 'flight' }, mockData);
  setCache({ from: '上海', to: '北京', date: '2026-10-02', type: 'flight' }, mockData);

  assert.equal(getCacheSize(), 2);
});

test('缓存的TTL过期后返回null', () => {
  clearCache();
  const mockData = { type: 'flight' as const, from: '北京', to: '上海', date: '2026-10-01', results: [], total: 0, cached: false };

  // 设置1ms TTL
  setCache({ from: '北京', to: '上海', date: '2026-10-01', type: 'flight' }, mockData, 1);

  // 立即获取应该命中
  const immediate = getCached({ from: '北京', to: '上海', date: '2026-10-01', type: 'flight' });
  assert.notEqual(immediate, null);

  // 等待TTL过期后获取
  return new Promise<void>((resolve) => {
    setTimeout(() => {
      const expired = getCached({ from: '北京', to: '上海', date: '2026-10-01', type: 'flight' });
      assert.equal(expired, null);
      resolve();
    }, 10);
  });
});

// 测试完成后清理
test('cleanup', () => {
  clearCache();
  assert.equal(getCacheSize(), 0);
});