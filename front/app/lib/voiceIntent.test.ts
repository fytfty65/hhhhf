import test from 'node:test';
import assert from 'node:assert/strict';
import { parseDays, extractCity, extractTags, parseVoiceIntent } from './voiceIntent.ts';

test('parseDays 阿拉伯数字天数', () => {
  assert.equal(parseDays('去乌鲁木齐玩3天'), 3);
  assert.equal(parseDays('7天6晚'), 7);
  assert.equal(parseDays('5日游'), 5);
});

test('parseDays 中文数字与复合表达', () => {
  assert.equal(parseDays('五日游'), 5);
  assert.equal(parseDays('两天一夜'), 2);
  assert.equal(parseDays('三天两晚'), 3);
});

test('parseDays 未识别返回 0', () => {
  assert.equal(parseDays(''), 0);
  assert.equal(parseDays('没有说到天数'), 0);
});

test('extractCity 动词引导提取城市', () => {
  assert.equal(extractCity('去乌鲁木齐玩3天'), '乌鲁木齐');
  assert.equal(extractCity('去成都吃火锅'), '成都');
  assert.equal(extractCity('目的地是成都'), '成都');
  assert.equal(extractCity('帮我规划上海的5天行程'), '上海');
});

test('extractCity 去尾部行政区划词', () => {
  assert.equal(extractCity('去乌鲁木齐市玩3天'), '乌鲁木齐');
});

test('extractCity 句首城市接天数/关键词', () => {
  assert.equal(extractCity('上海5天行程'), '上海');
  assert.equal(extractCity('成都旅游攻略'), '成都');
});

test('extractCity 空文本返回空串', () => {
  assert.equal(extractCity(''), '');
});

test('extractTags 按规则归类并去重', () => {
  assert.deepEqual(extractTags('要吃火锅和烧烤'), ['美食']);
  assert.deepEqual(extractTags('拍照出片'), ['摄影']);
  assert.deepEqual(extractTags('想去海边拍照和逛博物馆'), ['摄影', '自然', '历史人文']);
  assert.deepEqual(extractTags(''), []);
});

test('parseVoiceIntent 一次完成结构化解析', () => {
  const v = parseVoiceIntent('去乌鲁木齐玩3天，想看大巴扎和博物馆');
  assert.equal(v.city, '乌鲁木齐');
  assert.equal(v.days, 3);
  assert.deepEqual(v.tags, ['历史人文']);
  assert.equal(v.normalized, '去乌鲁木齐玩3天，想看大巴扎和博物馆');
});

test('parseVoiceIntent 无结构化信息时字段为空', () => {
  const v = parseVoiceIntent('随便走走');
  assert.equal(v.city, '');
  assert.equal(v.days, 0);
  assert.deepEqual(v.tags, []);
});
