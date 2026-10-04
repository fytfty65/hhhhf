import assert from 'node:assert/strict';
import test from 'node:test';
import {
  buildSavePayload,
  confidenceLabel,
  storageDisclosure,
  summarizeProfile,
} from './referenceProfile.ts';

/**
 * 参考路线画像（前端侧）的回归锁。
 *
 * 界面必须与后端同一套口径：**整篇原文不保存**、默认存"特征 + 出处片段"、可只存特征、
 * 抽不到的特征显示"未识别"而不是拿默认值冒充。
 */

const PROFILE = {
  features: {
    category_mix: { scenic: 8, food: 5, shopping: 2 },
    pace: 'relaxed',
    time_style: 'night_owl',
    budget_tier: 'mid',
    budget_amount: 300,
    transport: ['walk', 'metro'],
    lodging_style: 'homestay',
    walking_intensity: 'high',
  },
  unknown: ['nodes_per_day'],
  confidence: 0.86,
  reference_count: 1,
  source_label: '小红书-乌鲁木齐4天',
};

test('画像摘要用人话列出学到的特点', () => {
  const { lines, unknown } = summarizeProfile(PROFILE);
  assert.ok(lines.some((line) => line.includes('自然 8') && line.includes('美食 5')), `内容偏好缺失：${lines}`);
  assert.ok(lines.includes('节奏：轻松'));
  assert.ok(lines.includes('时段：夜猫子'));
  assert.ok(lines.some((line) => line.includes('消费档：舒适') && line.includes('¥300')));
  assert.ok(lines.includes('住宿：民宿'));
  assert.ok(lines.includes('交通：步行 / 地铁/公交'));
  assert.deepEqual(unknown, ['节奏与节点数'], '未识别的特征要用人话列出来');
});

test('未识别的特征显示为中文名，且不会凭空补默认值', () => {
  const { unknown } = summarizeProfile({ ...PROFILE, unknown: ['pace', 'budget_tier'] });
  assert.deepEqual(unknown, ['节奏', '消费档']);
  const empty = summarizeProfile({ features: {} });
  assert.deepEqual(empty.lines, []);
  assert.deepEqual(empty.unknown, []);
});

test('没有画像时摘要为空，不编内容', () => {
  assert.deepEqual(summarizeProfile(null).lines, []);
  assert.deepEqual(summarizeProfile(undefined).lines, []);
  assert.equal(confidenceLabel(null), null);
  assert.equal(confidenceLabel(PROFILE), '识别覆盖 86%');
});

test('保存请求体：默认保留出处片段，勾选后只存特征', () => {
  const withEvidence = buildSavePayload('  乌鲁木齐4天  ', '小红书', true);
  assert.equal(withEvidence.ok, true);
  if (withEvidence.ok) {
    assert.equal(withEvidence.payload.text, '乌鲁木齐4天', '正文应去掉首尾空白');
    assert.equal(withEvidence.payload.keep_evidence, true);
    assert.equal(withEvidence.payload.source_label, '小红书');
  }
  const withoutEvidence = buildSavePayload('乌鲁木齐4天', '', false);
  assert.equal(withoutEvidence.ok, true);
  if (withoutEvidence.ok) {
    assert.equal(withoutEvidence.payload.keep_evidence, false);
  }
});

test('空正文与超长正文都要明确拒绝，并说明原因', () => {
  const empty = buildSavePayload('   ', '', true);
  assert.equal(empty.ok, false);
  if (!empty.ok) assert.match(empty.reason, /粘贴/);

  const tooLong = buildSavePayload('字'.repeat(20001), '', true);
  assert.equal(tooLong.ok, false);
  if (!tooLong.ok) assert.match(tooLong.reason, /过长/);
});

test('存储口径文案必须说清"存了什么"，且两种模式都提到不保存整篇原文', () => {
  const kept = storageDisclosure(true);
  assert.match(kept, /出处片段/);
  assert.match(kept, /整篇原文不会被保存/);
  const stripped = storageDisclosure(false);
  assert.match(stripped, /不保存出处片段/);
  assert.match(stripped, /整篇原文也不会被保存/);
});
