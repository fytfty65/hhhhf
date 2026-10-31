import test from 'node:test';
import assert from 'node:assert/strict';
import {
  assessOverageOptions,
  buildCombinedInstruction,
  buildOverageOptions,
  buildRetryInstruction,
  summarizeBudgetChange,
  summarizeCombinedOverage,
  summarizeOverageResult,
  unappliedOptions,
  verifyCombinedOptions,
} from './overageAlternatives.ts';

// 选项只能来自真实报价：没有报价的节点不参与排序，不猜金额。

test('住宿单独成项：换住宿通常比换单个景点省得多', () => {
  const nodes = [
    { day: 1, name: '钟楼酒店', type: '住宿', tags: ['住宿'], is_hotel: true, cost_estimate: 400 },
    { day: 1, name: '陕西历史博物馆', type: '博物馆', tags: ['文化'], cost_estimate: 120 },
    { day: 1, name: '回民街小吃', type: '餐饮', tags: ['美食'], cost_estimate: 60 },
  ];
  const options = buildOverageOptions({ nodes, budget: 1200, shortfall: 200 });
  assert.equal(options[0].id, 'lodging');
  assert.equal(options[0].kind, 'lodging');
  assert.equal(options[0].maxSaving, 400);
  assert.ok(options[0].instruction.includes('住宿换成更实惠的同类'));
  assert.ok(options[0].instruction.includes('景点、交通与餐饮保持原样'));
});

test('其余节点按报价降序取前 3 条，报价越高越先被建议动', () => {
  const nodes = [
    { day: 1, name: '城墙南门', type: '文化', cost_estimate: 54 },
    { day: 1, name: '陕西历史博物馆', type: '博物馆', cost_estimate: 120 },
    { day: 1, name: '回民街小吃', type: '餐饮', cost_estimate: 60 },
    { day: 1, name: '书院门面馆', type: '餐饮', cost_estimate: 50 },
    { day: 1, name: '大唐不夜城', type: '景点', cost_estimate: 90 },
  ];
  const options = buildOverageOptions({ nodes, budget: 1200, shortfall: 200 });
  const nodeOptions = options.filter((option) => option.kind === 'node');
  assert.equal(nodeOptions.length, 3);
  assert.ok(nodeOptions[0].label.includes('陕西历史博物馆'));
  assert.ok(nodeOptions[1].label.includes('大唐不夜城'));
  assert.ok(nodeOptions[2].label.includes('回民街小吃'));
  assert.ok(nodeOptions[0].instruction.includes('其它安排保持原样'));
  // 最后一条恒为"按预算重排"兜底
  assert.equal(options[options.length - 1].id, 'budget');
});

test('limit 能控制条目数，但兜底那条始终在', () => {
  const nodes = [
    { day: 1, name: 'A', cost_estimate: 100 },
    { day: 1, name: 'B', cost_estimate: 80 },
    { day: 1, name: 'C', cost_estimate: 60 },
  ];
  const options = buildOverageOptions({ nodes, budget: 500, shortfall: 100, limit: 1 });
  assert.equal(options.length, 2);
  assert.equal(options[0].id, 'node-0');
  assert.equal(options[1].id, 'budget');
});

test('一个报价都没有时只剩"按预算重排"，不编造省钱清单', () => {
  const nodes = [
    { day: 1, name: '陕西历史博物馆', type: '博物馆', cost: '暂无供应商数据' },
    { day: 1, name: '钟楼酒店', type: '住宿', is_hotel: true, cost: 0 },
  ];
  const options = buildOverageOptions({ nodes, budget: 1200, shortfall: 200 });
  assert.equal(options.length, 1);
  assert.equal(options[0].id, 'budget');
  assert.ok(options[0].detail.includes('当前超出 ¥200'));
  assert.ok(options[0].instruction.includes('总花费请压到 ¥1,200 以内'));
});

test('预算未知时兜底文案不说具体金额（不编造）', () => {
  const options = buildOverageOptions({
    nodes: [{ day: 1, name: '城墙南门', cost_estimate: '¥54' }],
    budget: null,
    shortfall: 0,
  });
  assert.equal(options[options.length - 1].label, '按预算重新压缩');
  assert.ok(!options[options.length - 1].label.includes('¥'));
});

test('带货币符号的报价字符串能解析；免费或未知价格不参与排序', () => {
  const nodes = [
    { day: 1, name: '带符号', cost_estimate: '¥180' },
    { day: 1, name: '免费', cost_estimate: '¥0' },
    { day: 1, name: '未知', cost: '暂无供应商数据' },
    { day: 1, name: '数字', cost: 120 },
  ];
  const options = buildOverageOptions({ nodes, budget: 1000, shortfall: 100 });
  const nodeOptions = options.filter((option) => option.kind === 'node');
  assert.equal(nodeOptions.length, 2);
  assert.ok(nodeOptions[0].label.includes('带符号'));
  assert.equal(nodeOptions[0].maxSaving, 180);
  assert.ok(nodeOptions[1].label.includes('数字'));
});

// 重排后的核对：只说后端真给了的数字。

test('压到预算内才说不再超支', () => {
  const result = summarizeOverageResult({ verifiedCost: 1000, estimatedCost: 50, budget: 1200, shortfall: 0 });
  assert.equal(result.tone, 'ok');
  assert.ok(result.text.includes('不再超支'));
  assert.ok(result.text.includes('¥1,050'));
});

test('仍然超支要如实写缺口，并给出下一步', () => {
  const result = summarizeOverageResult({ verifiedCost: 1300, estimatedCost: 0, budget: 1200, shortfall: 100 });
  assert.equal(result.tone, 'warn');
  assert.ok(result.text.includes('仍超出 ¥100'));
  assert.ok(result.text.includes('可以再换掉一项，或调高预算'));
});

test('后端没给 shortfall 但合计超过预算，同样按超支报（用合计减预算算缺口）', () => {
  const result = summarizeOverageResult({ verifiedCost: 1300, estimatedCost: 0, budget: 1200, shortfall: null });
  assert.equal(result.tone, 'warn');
  assert.ok(result.text.includes('仍超出 ¥100'));
});

test('没有预算数字时不判断是否压到预算内', () => {
  const result = summarizeOverageResult({ verifiedCost: 900, estimatedCost: 100, budget: null, shortfall: null });
  assert.equal(result.tone, 'warn');
  assert.ok(result.text.includes('后端没有给出预算数字，无法判断'));
});

test('这次一个可用金额都没返回时，说无法确认而不是假装成功', () => {
  const result = summarizeOverageResult({ verifiedCost: null, estimatedCost: null, budget: 1200, shortfall: null });
  assert.equal(result.tone, 'warn');
  assert.ok(result.text.includes('没有返回可用的预算核对'));
  assert.ok(result.text.includes('无法确认'));
});

// 预算改动后的连带核对：哪些替代项还成立、哪些已失效。

test('单项评估：省得够就标 enough，省不够就给出还差多少', () => {
  const options = buildOverageOptions({
    nodes: [
      { day: 1, name: '钟楼酒店', type: '住宿', is_hotel: true, cost_estimate: 400 },
      { day: 1, name: '陕西历史博物馆', type: '博物馆', cost_estimate: 120 },
    ],
    budget: 1200,
    shortfall: 200,
  });
  const assessments = assessOverageOptions(options, 200);
  const lodging = assessments.find((item) => item.option.id === 'lodging');
  assert.equal(lodging?.status, 'enough');
  assert.equal(lodging?.remaining, 0);
  const museum = assessments.find((item) => item.option.id === 'node-0');
  assert.equal(museum?.status, 'partial');
  assert.equal(museum?.remaining, 80);
  // "按预算重排"是整单压缩，不算单项不够
  const budget = assessments.find((item) => item.option.id === 'budget');
  assert.equal(budget?.status, 'enough');
});

test('缺口归零后所有替代项都失效（调高预算就不该再劝人换便宜货）', () => {
  const options = buildOverageOptions({
    nodes: [{ day: 1, name: '钟楼酒店', type: '住宿', is_hotel: true, cost_estimate: 400 }],
    budget: 1500,
    shortfall: 0,
  });
  const assessments = assessOverageOptions(options, 0);
  assert.ok(assessments.every((item) => item.status === 'unnecessary'));
});

test('预算调高到够用：明确说预算已改、替代项不用再执行', () => {
  const summary = summarizeBudgetChange({
    requestedBudget: 1500,
    previousBudget: 1200,
    newBudget: 1500,
    verifiedCost: 1400,
    estimatedCost: 0,
    shortfall: 0,
  });
  assert.equal(summary.tone, 'ok');
  assert.equal(summary.adopted, true);
  assert.equal(summary.stillOver, false);
  assert.ok(summary.text.includes('预算已改为 ¥1,500'));
  assert.ok(summary.text.includes('不再超支'));
  assert.ok(summary.text.includes('替代项不用再执行'));
});

test('预算调低后仍不够：如实写缺口，并说替代项仍成立', () => {
  const summary = summarizeBudgetChange({
    requestedBudget: 900,
    previousBudget: 1200,
    newBudget: 900,
    verifiedCost: 1400,
    estimatedCost: 0,
    shortfall: null,
  });
  assert.equal(summary.tone, 'warn');
  assert.equal(summary.adopted, true);
  assert.equal(summary.stillOver, true);
  assert.ok(summary.text.includes('预算已改为 ¥900'));
  assert.ok(summary.text.includes('仍超出 ¥500'));
  assert.ok(summary.text.includes('替代项仍成立'));
});

test('后端没采用新预算时不许说"已改"，要指出去哪里改', () => {
  const summary = summarizeBudgetChange({
    requestedBudget: 1500,
    previousBudget: 1200,
    newBudget: 1200,
    verifiedCost: 1400,
    estimatedCost: 0,
    shortfall: 200,
  });
  assert.equal(summary.tone, 'warn');
  assert.equal(summary.adopted, false);
  assert.ok(summary.text.includes('没有按 ¥1,500 重排'));
  assert.ok(summary.text.includes('预算没改成'));
});

test('没返回预算核对时，无法确认预算是否改成', () => {
  const summary = summarizeBudgetChange({
    requestedBudget: 1500,
    previousBudget: 1200,
    newBudget: null,
    verifiedCost: 1400,
    estimatedCost: 0,
    shortfall: null,
  });
  assert.equal(summary.tone, 'warn');
  assert.equal(summary.adopted, false);
  assert.ok(summary.text.includes('无法确认预算是否已改成 ¥1,500'));
});

// 多步替代叠加：勾两项一次重排，逐项核对有没有落实。

const NODES_BEFORE = [
  { day: 1, name: '钟楼酒店', type: '住宿', is_hotel: true, cost_estimate: 400 },
  { day: 1, name: '陕西历史博物馆', type: '博物馆', cost_estimate: 120 },
  { day: 1, name: '回民街小吃', type: '餐饮', cost_estimate: 60 },
  { day: 2, name: '城墙南门', type: '文化', cost_estimate: 54 },
];

const OPTIONS_2 = () => {
  const options = buildOverageOptions({ nodes: NODES_BEFORE, budget: 1200, shortfall: 200 });
  return {
    lodging: options.find((option) => option.kind === 'lodging')!,
    museum: options.find((option) => option.kind === 'node')!,
    budget: options.find((option) => option.kind === 'budget')!,
  };
};

test('两项合并成一句增量，且带上具体节点名', () => {
  const { lodging, museum } = OPTIONS_2();
  assert.equal(lodging.target, '钟楼酒店');
  const instruction = buildCombinedInstruction([lodging, museum]);
  assert.ok(instruction.includes('同时调整 2 处'));
  assert.ok(instruction.includes('住宿换成更实惠的同类'));
  assert.ok(instruction.includes('把「陕西历史博物馆」换成更便宜的同类'));
  assert.ok(instruction.includes('其它安排尽量保持原样'));
});

test('只选一项或选了"按预算重排"时，不走合并（返回空串）', () => {
  const { lodging, budget } = OPTIONS_2();
  assert.equal(buildCombinedInstruction([lodging]), '');
  assert.equal(buildCombinedInstruction([budget]), '');
  assert.equal(buildCombinedInstruction([budget, lodging]), '');
});

test('合并重排后逐项核对：都换了就标已更换，并算出合计省了 160', () => {
  const { lodging, museum } = OPTIONS_2();
  const after = [
    { day: 1, name: '巷往小院民宿', type: '住宿', is_hotel: true, cost_estimate: 300 },
    { day: 1, name: '碑林博物馆', type: '博物馆', cost_estimate: 60 },
    { day: 1, name: '回民街小吃', type: '餐饮', cost_estimate: 60 },
    { day: 2, name: '城墙南门', type: '文化', cost_estimate: 54 },
  ];
  const { checks, saved } = verifyCombinedOptions({ options: [lodging, museum], before: NODES_BEFORE, after });
  assert.equal(checks[0].label, '住宿');
  assert.equal(checks[0].applied, true);
  assert.equal(checks[1].label, '「陕西历史博物馆」');
  assert.equal(checks[1].applied, true);
  assert.equal(saved, 160);

  const summary = summarizeCombinedOverage({
    options: [lodging, museum],
    before: NODES_BEFORE,
    after,
    verifiedCost: 900,
    estimatedCost: 50,
    budget: 1200,
    shortfall: 0,
  });
  assert.equal(summary.tone, 'ok');
  assert.ok(summary.text.includes('住宿：已更换'));
  assert.ok(summary.text.includes('「陕西历史博物馆」：已更换'));
  assert.ok(summary.text.includes('已计价节点合计省了 ¥160'));
  assert.ok(summary.text.includes('不再超支'));
});

test('有一项没落实就如实点名，不许当成整体成功', () => {
  const { lodging, museum } = OPTIONS_2();
  // 住宿还是原来那家，只有博物馆被换掉
  const after = [
    { day: 1, name: '钟楼酒店', type: '住宿', is_hotel: true, cost_estimate: 400 },
    { day: 1, name: '碑林博物馆', type: '博物馆', cost_estimate: 60 },
    { day: 1, name: '回民街小吃', type: '餐饮', cost_estimate: 60 },
    { day: 2, name: '城墙南门', type: '文化', cost_estimate: 54 },
  ];
  const summary = summarizeCombinedOverage({
    options: [lodging, museum],
    before: NODES_BEFORE,
    after,
    verifiedCost: 500,
    estimatedCost: 50,
    budget: 1200,
    shortfall: 0,
  });
  assert.equal(summary.tone, 'warn');
  assert.ok(summary.text.includes('住宿：没有变化，仍住原来那家'));
  assert.ok(summary.text.includes('「陕西历史博物馆」：已更换'));
});

test('合并重排没拿到新路线时，直说无法确认', () => {
  const { lodging, museum } = OPTIONS_2();
  const summary = summarizeCombinedOverage({
    options: [lodging, museum],
    before: NODES_BEFORE,
    after: null,
    verifiedCost: null,
    estimatedCost: null,
    budget: 1200,
    shortfall: null,
  });
  assert.equal(summary.tone, 'warn');
  assert.ok(summary.text.includes('没有拿到新方案'));
  assert.ok(summary.text.includes('无法确认'));
  assert.deepEqual(summary.checks, []);
});

// 一键补做：只重试没落实的那几项，已换好的不再动。

test('只挑出没落实的项重试，已换好的不参与', () => {
  const { lodging, museum } = OPTIONS_2();
  const after = [
    { day: 1, name: '钟楼酒店', type: '住宿', is_hotel: true, cost_estimate: 400 },
    { day: 1, name: '碑林博物馆', type: '博物馆', cost_estimate: 60 },
    { day: 1, name: '回民街小吃', type: '餐饮', cost_estimate: 60 },
    { day: 2, name: '城墙南门', type: '文化', cost_estimate: 54 },
  ];
  const summary = summarizeCombinedOverage({
    options: [lodging, museum],
    before: NODES_BEFORE,
    after,
    verifiedCost: 500,
    estimatedCost: 50,
    budget: 1200,
    shortfall: 0,
  });
  const retry = unappliedOptions(summary, [lodging, museum]);
  assert.deepEqual(retry.map((option) => option.id), ['lodging']);
  // 全部落实后没有可补做的项
  assert.deepEqual(unappliedOptions({ ...summary, checks: summary.checks.map((check) => ({ ...check, applied: true })) }, [lodging, museum]), []);
  // 没有核对结果（例如没拿到新方案）时不猜哪些没落实
  assert.deepEqual(unappliedOptions(null, [lodging, museum]), []);
});

test('重试指令：只剩 1 项用它自己的单步指令，2 项仍走合并，空列表不发增量', () => {
  const { lodging, museum, budget } = OPTIONS_2();
  assert.equal(buildRetryInstruction([lodging]), lodging.instruction);
  assert.equal(buildRetryInstruction([museum]), museum.instruction);
  assert.equal(buildRetryInstruction([lodging, museum]), buildCombinedInstruction([lodging, museum]));
  assert.equal(buildRetryInstruction([]), '');
  assert.equal(buildRetryInstruction([budget]), '');
});
