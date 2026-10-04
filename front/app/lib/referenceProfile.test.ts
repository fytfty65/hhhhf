import assert from 'node:assert/strict';
import test from 'node:test';
import {
  ALLOWED_IMAGE_TYPES,
  MAX_IMAGE_BYTES,
  buildOcrPayload,
  buildSavePayload,
  confidenceLabel,
  describeOcrFailure,
  readOcrResult,
  REFERENCE_ROUTE_OCR_ENDPOINT,
  storageDisclosure,
  summarizeProfile,
  validateImageFile,
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

// ---------------------------------------------------------------------------
// 截图识别：本地早拦 + 失败话术 + 结果解析
// ---------------------------------------------------------------------------

test('截图端点挂在参考路线下面（不另开一套路径）', () => {
  assert.equal(REFERENCE_ROUTE_OCR_ENDPOINT, '/api/v1/user/preference/reference-route/ocr');
});

test('本地就能拦掉不支持的格式与超大图片，并给出可执行的下一步', () => {
  assert.equal(validateImageFile({ name: 'a.png', type: 'image/png', size: 1024 }).ok, true);
  assert.equal(validateImageFile({ name: 'a.jpg', type: 'image/jpeg', size: 1024 }).ok, true);
  assert.equal(validateImageFile({ type: 'image/webp', size: 1024 }).ok, true);
  assert.deepEqual(ALLOWED_IMAGE_TYPES, ['image/png', 'image/jpeg', 'image/webp']);

  const heic = validateImageFile({ name: 'IMG_0001.HEIC', type: 'image/heic', size: 2048 });
  assert.equal(heic.ok, false);
  if (!heic.ok) assert.match(heic.reason, /HEIC/);

  const big = validateImageFile({ type: 'image/png', size: MAX_IMAGE_BYTES + 1 });
  assert.equal(big.ok, false);
  if (!big.ok) assert.match(big.reason, /上限/);

  const empty = validateImageFile({ type: 'image/png', size: 0 });
  assert.equal(empty.ok, false);
  const missing = validateImageFile(null);
  assert.equal(missing.ok, false);
  const other = validateImageFile({ type: 'application/pdf', size: 10 });
  assert.equal(other.ok, false);
  if (!other.ok) assert.match(other.reason, /PNG/);
});

test('识别请求体只带图片本身，不带任何用户身份字段', () => {
  const payload = buildOcrPayload('data:image/png;base64,AAAA', 'image/png');
  assert.deepEqual(Object.keys(payload).sort(), ['image_base64', 'mime_type']);
  assert.equal(payload.mime_type, 'image/png');
});

test('失败原因码翻译成用户能照着做的下一步', () => {
  assert.match(describeOcrFailure('unsupported_mime'), /PNG\/JPEG/);
  assert.match(describeOcrFailure('no_text_in_image'), /没有识别到行程文字/);
  assert.match(describeOcrFailure('vision_not_configured'), /粘贴文字/);
  // 网关会加 REFERENCE_OCR_ 前缀，翻译层要能剥掉
  assert.match(describeOcrFailure('REFERENCE_OCR_TOO_LARGE'), /压缩/);
  // 没见过的原因码也要给出去路，不能吞掉变成"操作成功"
  assert.match(describeOcrFailure('weird_new_code'), /粘贴文字/);
  assert.equal(describeOcrFailure('weird_new_code', '上游原话'), '上游原话');
});

test('识别结果解析：没有文字就是失败，绝不返回空字符串当成功', () => {
  const ok = readOcrResult({ data: { text: '第一天 博斯腾湖', note: '请核对', model: 'qwen-vl-max', warnings: [] } });
  assert.equal(ok.ok, true);
  if (ok.ok) {
    assert.equal(ok.text, '第一天 博斯腾湖');
    assert.equal(ok.model, 'qwen-vl-max');
  }
  const blank = readOcrResult({ data: { text: '   ' } });
  assert.equal(blank.ok, false);
  const failed = readOcrResult({ error: { code: 'REFERENCE_OCR_NO_TEXT_IN_IMAGE', message: '' } });
  assert.equal(failed.ok, false);
  if (!failed.ok) assert.match(failed.reason, /没有识别到行程文字/);
  const missing = readOcrResult({});
  assert.equal(missing.ok, false);
});
