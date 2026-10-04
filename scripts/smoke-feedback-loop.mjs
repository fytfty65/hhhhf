/**
 * 反馈学习闭环真实验证（选项 A）：证明「反馈 → 画像 → 下一次规划」真的改变了结果。
 *
 * 为什么必须真跑：`profile_applied` 的加成看似接好了线，但"接好线"和"真的影响了下一次推演"
 * 是两件事。这里用**同一个用户、同一座城市、同一句模糊诉求**跑两轮真实推演：
 *   - 第一轮：该用户零反馈 → 期望 source=cold_start、无加成（均衡基线）；
 *   - 中间：提交 4 条正向自然景观反馈 + 4 条负向人文反馈 → 触发画像重聚合；
 *   - 第二轮：同一句诉求 → 期望 source=learned_profile、scenic 加成 > 0、cultural 加成 < 0。
 * 因为诉求与城市都没变（候选池来自同一批 AMap 查询），差异只能归因于学习到的画像。
 *
 * 已知的真实限制（脚本会打印，不当成通过）：
 *   - A/B 分组是 userID 的稳定哈希，**control 组用户学到的画像按设计不施加权重**，
 *     所以脚本会一直注册到 treatment 用户为止，并报告用了几次；
 *   - 路线节点分类是关键词启发式（与网关 preferences.go 的维度词表同源），只作旁证，
 *     硬判据是 profile_applied 的结构化加成。
 *
 * 用法：
 *   node scripts/smoke-feedback-loop.mjs
 *   $env:SMOKE_CITY='库尔勒'; $env:SMOKE_INTENT='去库尔勒玩2天，帮我安排一下'; node scripts/smoke-feedback-loop.mjs
 */

const BASE = process.env.GATEWAY_BASE || 'http://127.0.0.1:8080';
const WS_BASE = process.env.GATEWAY_WS || BASE.replace(/^http/, 'ws');
const PASSWORD = 'Probe#2026';
const STAMP = String(Date.now());
const CITY = process.env.SMOKE_CITY || '库尔勒';
const INTENT = process.env.SMOKE_INTENT || `去${CITY}玩2天，帮我安排一下就行`;
// 房间 WS（/api/v1/room/:id/ws）的契约与全局 /ws 不同：service/room.go 读完整个
// ai-service 响应后**只广播一条 `ai_negotiation_result`**，不逐字流式、也不发 final_route。
// 之前这里只等 final_route/final_route_ready，于是每次都白等到 240s 超时 ——
// 结论看着"能跑"，其实是在等一个这条通道根本不会出现的类型（实测教训）。
const FINAL_TYPES = ['ai_negotiation_result', 'final_route', 'final_route_ready'];
const PLAN_TIMEOUT_MS = Number(process.env.SMOKE_PLAN_TIMEOUT_MS || 240000);

const POSITIVE_TARGETS = ['博斯腾湖', '天山天池', '巴音布鲁克草原', '塔里木胡杨林'];
const NEGATIVE_TARGETS = ['库尔勒博物馆', '库车大寺', '苏巴什佛寺遗址', '库车老街'];

const results = [];
function record(name, ok, detail = '') {
  results.push({ name, ok, detail });
  console.log(`  [${ok ? 'PASS' : 'FAIL'}] ${name}${detail ? ' — ' + detail : ''}`);
}
function note(text) {
  console.log(`  · ${text}`);
}

async function post(path, token, body) {
  const response = await fetch(`${BASE}${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...(token ? { Authorization: `Bearer ${token}` } : {}) },
    body: JSON.stringify(body),
  });
  const text = await response.text();
  let payload = {};
  try {
    payload = JSON.parse(text);
  } catch {
    payload = {};
  }
  return { status: response.status, payload, text };
}

async function get(path, token) {
  const response = await fetch(`${BASE}${path}`, {
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  });
  const text = await response.text();
  let payload = {};
  try {
    payload = JSON.parse(text);
  } catch {
    payload = {};
  }
  return { status: response.status, payload, text };
}

async function register(username) {
  let response = await fetch(`${BASE}/api/auth/register`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username, password: PASSWORD, nickname: username.slice(-4) }),
  });
  if (response.status !== 200) {
    response = await fetch(`${BASE}/api/auth/login`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username, password: PASSWORD }),
    });
  }
  const payload = await response.json().catch(() => ({}));
  return { token: String(payload.token || ''), userId: String(payload.user?.id || ''), status: response.status };
}

function openSocket(url) {
  return new Promise((resolve, reject) => {
    const ws = new WebSocket(url);
    const timer = setTimeout(() => reject(new Error('WS 建连超时 15s')), 15000);
    ws.addEventListener('open', () => {
      clearTimeout(timer);
      resolve(ws);
    });
    ws.addEventListener('close', (event) => {
      clearTimeout(timer);
      reject(new Error(`WS 被关闭 code=${event.code} reason=${event.reason || '(空)'}`));
    });
    ws.addEventListener('error', () => {
      clearTimeout(timer);
      reject(new Error('WS error 事件（握手失败，详见 close code）'));
    });
  });
}

/** 收满一条 final 事件即返回（不再等所有 final 类型，避免把耗时算成 220s） */
function collectFinal(ws, timeoutMs) {
  return new Promise((resolve) => {
    const seen = [];
    const timer = setTimeout(() => {
      ws.removeEventListener('message', onMessage);
      resolve(seen);
    }, timeoutMs);
    function onMessage(event) {
      let msg;
      try {
        msg = JSON.parse(typeof event.data === 'string' ? event.data : String(event.data));
      } catch {
        return;
      }
      seen.push(msg);
      if (FINAL_TYPES.some((type) => String(msg.type) === type)) {
        clearTimeout(timer);
        ws.removeEventListener('message', onMessage);
        resolve(seen);
      }
    }
    ws.addEventListener('message', onMessage);
  });
}

function candidatePayloads(message) {
  const list = [];
  if (message?.payload) list.push(message.payload);
  if (message?.payload?.final_data) list.push(message.payload.final_data);
  if (message?.final_data) list.push(message.final_data);
  return list;
}

function findField(messages, field) {
  for (let i = messages.length - 1; i >= 0; i -= 1) {
    for (const payload of candidatePayloads(messages[i])) {
      if (payload && payload[field] !== undefined && payload[field] !== null) return payload[field];
    }
  }
  return undefined;
}

/** 从 final 载荷里尽量稳地取出节点名（结构随版本变，这里做兜底遍历） */
function collectNodeNames(messages) {
  const names = new Set();
  const visit = (value, depth) => {
    if (!value || depth > 4) return;
    if (Array.isArray(value)) {
      value.forEach((item) => visit(item, depth + 1));
      return;
    }
    if (typeof value !== 'object') return;
    if (typeof value.name === 'string' && value.name.trim()) names.add(value.name.trim());
    if (typeof value.activity === 'string' && value.activity.trim()) names.add(value.activity.trim());
    for (const key of ['nodes', 'items', 'pois', 'route', 'routes', 'days', 'plan']) {
      if (value[key]) visit(value[key], depth + 1);
    }
  };
  for (const message of messages) {
    for (const payload of candidatePayloads(message)) {
      visit(payload?.route, 0);
      visit(payload?.routes, 0);
    }
  }
  return [...names];
}

// 与网关 internal/service/preferences.go 的维度关键词同源（子串启发式），只作旁证
const NATURE_KEYWORDS = ['山', '公园', '湖', '河', '谷', '峡', '林', '草原', '沙漠', '海', '岛', '瀑布', '温泉', '雪山', '冰川', '植物园', '天池', '花海', '湿地', '滩', '湾', '峰'];
const CULTURE_KEYWORDS = ['博物', '寺', '庙', '宫', '塔', '古城', '老街', '遗址', '陵', '馆', '阁', '楼', '石窟', '文庙', '故居', '书院', '清真寺', '城墙', '祠', '钟楼', '鼓楼'];
const FOOD_KEYWORDS = ['餐', '美食', '火锅', '烤', '面', '饭', '小吃', '抓饭', '拌面', '包子', '馕', '夜市', '小吃街', '咖啡', '甜品', '奶茶', '肉', '串', '汤', '饼', '餐厅', '菜馆'];

function classify(name) {
  const dims = [];
  if (NATURE_KEYWORDS.some((kw) => name.includes(kw))) dims.push('nature');
  if (CULTURE_KEYWORDS.some((kw) => name.includes(kw))) dims.push('culture');
  if (FOOD_KEYWORDS.some((kw) => name.includes(kw))) dims.push('food');
  return dims.length > 0 ? dims : ['other'];
}

function mixOf(names) {
  const mix = { nature: 0, culture: 0, food: 0, other: 0 };
  for (const name of names) {
    for (const dim of classify(name)) mix[dim] += 1;
  }
  return mix;
}

function boostVector(applied) {
  const boost = applied?.category_boost || {};
  return Object.keys(boost).length > 0
    ? Object.entries(boost).map(([k, v]) => `${k}${v >= 0 ? '+' : ''}${v}`).join(' ')
    : '(空=均衡基线)';
}

async function runPlan(ws, userId, roomId, label) {
  const startedAt = Date.now();
  const pending = collectFinal(ws, PLAN_TIMEOUT_MS);
  ws.send(
    JSON.stringify({
      type: 'agent_negotiate',
      room_id: roomId,
      user_id: userId,
      payload: {
        destinations: [CITY],
        current_request: { destinations: [CITY], city: '' },
        user_preferences: { intent: INTENT, mode: 'coop', role: '寻味探索', members: 2, budget: 4000 },
      },
    }),
  );
  const events = await pending;
  const seconds = ((Date.now() - startedAt) / 1000).toFixed(1);
  const applied = findField(events, 'profile_applied');
  const names = collectNodeNames(events);
  const pool = findField(events, 'candidate_pool');
  if (!FINAL_TYPES.some((type) => events.some((message) => String(message.type) === type))) {
    // 收不到终稿时把"到底收到了什么"打出来：区分"只是慢"和"类型对不上"
    const types = events.map((message) => String(message.type || (message.token ? 'token' : '?')));
    note(`${label} 未收到终稿；收到的事件类型：${[...new Set(types)].join(', ') || '(空)'}`);
  }
  console.log(
    `  ${label}：${seconds}s / ${events.length} 条事件 / 节点 ${names.length} 个 / 画像 source=${applied?.source ?? '(缺字段)'} tilted=${applied?.tilted ?? '?'} 加成=${boostVector(applied)}`,
  );
  note(`${label} 节点：${names.slice(0, 12).join('、') || '(未解析到节点名)'}`);
  return { events, applied, names, pool, seconds: Number(seconds), mix: mixOf(names) };
}

async function main() {
  console.log('='.repeat(72));
  console.log('反馈学习闭环真实验证（选项 A）');
  console.log('='.repeat(72));
  console.log(`  城市=${CITY} 诉求=「${INTENT}」`);

  // 1) 找一个 treatment 组用户（control 组按 A/B 设计根本不施加画像权重）
  let account = null;
  let attempts = 0;
  let lastVariant = '';
  for (; attempts < 8; attempts += 1) {
    account = await register(`smoke_fb_${STAMP}_${attempts}`);
    if (!account.token || !account.userId) continue;
    const pref = await get('/api/v1/user/preference', account.token);
    lastVariant = String(pref.payload?.profile?.ab_variant || '');
    if (lastVariant === 'treatment') break;
  }
  record(
    '注册到 treatment 组用户（A/B 哈希分组，control 组画像按设计不生效）',
    lastVariant === 'treatment',
    `尝试 ${attempts + 1} 次，variant=${lastVariant || '?'}`,
  );
  if (lastVariant !== 'treatment') {
    console.log('  说明：连注册 8 个用户都落在 control 组，无法验证画像功效（不是功能故障，是分组概率）。');
    console.log('  如需强制对照组对照，可重复运行本脚本。');
    return;
  }

  const room = await post('/api/v1/room/create', account.token, {
    username: `smoke_fb_${STAMP}`,
    room_name: `反馈闭环冒烟房间${STAMP}`,
  });
  const roomId = String(room.payload.room_id || '');
  record('建房', room.status === 200 && Boolean(roomId), `HTTP ${room.status}`);
  if (!roomId) return;

  let ws;
  try {
    ws = await openSocket(`${WS_BASE}/api/v1/room/${roomId}/ws?access_token=${account.token}&user_id=${account.userId}`);
  } catch (error) {
    record('WebSocket 建连', false, error.message);
    return;
  }
  record('WebSocket 建连', true, '房间推演通道已建立');

  // 2) 第一轮：零反馈基线
  const first = await runPlan(ws, account.userId, roomId, '第一轮（无反馈基线）');
  record(
    '基线轮：冷启动无画像加成（均衡基线）',
    first.applied?.source === 'cold_start' && first.applied?.tilted === false,
    `source=${first.applied?.source} tilted=${first.applied?.tilted}`,
  );
  record('第一轮真的出了方案', first.names.length > 0, `${first.names.length} 个节点 / ${first.seconds}s`);

  // 3) 提交真实反馈（正向自然景观 + 负向人文景点）
  let feedbackOk = 0;
  const feedbackDetail = [];
  for (const target of POSITIVE_TARGETS) {
    const res = await post('/api/v1/feedback', account.token, {
      room_id: roomId,
      user_id: account.userId,
      target,
      score: 1,
      reason: '这次的自然景观很舒服，想多安排这类',
    });
    if (res.status === 200) feedbackOk += 1;
    else feedbackDetail.push(`${target}:HTTP${res.status}`);
  }
  for (const target of NEGATIVE_TARGETS) {
    const res = await post('/api/v1/feedback', account.token, {
      room_id: roomId,
      user_id: account.userId,
      target,
      score: -1,
      reason: '人文景点逛得有点累，下次少安排',
    });
    if (res.status === 200) feedbackOk += 1;
    else feedbackDetail.push(`${target}:HTTP${res.status}`);
  }
  record('提交反馈（4 正 4 负）', feedbackOk === 8, `成功 ${feedbackOk}/8 ${feedbackDetail.join(' ')}`);

  // 4) 画像重聚合 → 读回比例
  const refresh = await post('/api/v1/user/preference/refresh', account.token, { user_id: account.userId });
  const learned = refresh.payload?.profile || {};
  record(
    '画像重聚合成功',
    refresh.status === 200 && Number(learned.feedback_count || 0) >= 8,
    `HTTP ${refresh.status} feedback_count=${learned.feedback_count} nature=${learned.nature_ratio} culture=${learned.culture_ratio} food=${learned.food_ratio}`,
  );
  record(
    '自然占比超过人文占比（学到了反馈的方向）',
    Number(learned.nature_ratio || 0) > Number(learned.culture_ratio || 0),
    `nature=${learned.nature_ratio} vs culture=${learned.culture_ratio}`,
  );
  note(`liked_tags=${JSON.stringify(learned.liked_tags || [])} disliked_tags=${JSON.stringify(learned.disliked_tags || [])}`);

  // 5) 第二轮：同一句诉求，看画像是否真的进入裁决
  const second = await runPlan(ws, account.userId, roomId, '第二轮（有反馈）');
  record(
    '第二轮：学习到的画像进入候选裁决（scenic 加成 > 0）',
    Number(second.applied?.category_boost?.scenic || 0) > 0,
    `加成=${boostVector(second.applied)} source=${second.applied?.source}`,
  );
  record(
    '第二轮：人文品类被负向加权（cultural 加成 < 0）',
    Number(second.applied?.category_boost?.cultural || 0) < 0,
    `cultural=${second.applied?.category_boost?.cultural}`,
  );
  record(
    '第二轮：细标签加成命中喜欢的自然标签',
    Number(second.applied?.fine_tag_boost?.湖海水系 || 0) > 0 || Number(second.applied?.fine_tag_boost?.山岳 || 0) > 0,
    JSON.stringify(second.applied?.fine_tag_boost || {}),
  );

  // 6) 结果侧：只打印不判定。
  // 实测（2026-10-04）：库尔勒这一轮的候选池只有 6 个白天候选（cultural 4 / scenic 2），
  // ⚠️ 口径纠正：这里的 `candidate_pool` 是**治理层的可替换候选池**（只在需要替换/配额时构建，
  //    每个意图 limit_per_intent 条），**不是**主白天候选池。主池请看 candidate_supply
  //    （真实探针：库尔勒 景点 22 / 自然 18 / 美食 3）。之前把它当"候选池太小"是我读错了字段。
  // 画像倾斜作用在主池的具体候选上，路线构成仍由模型选择主导 —— 两轮自然/人文节点数
  // 会来回抖。所以这里如实打印观察值，不把"结果一定变好"写成门禁（那会是假绿）。
  console.log('');
  console.log(`  路线品类构成：第一轮 ${JSON.stringify(first.mix)} → 第二轮 ${JSON.stringify(second.mix)}`);
  console.log(`  可替换候选池（治理层，非主池）：第一轮 ${JSON.stringify(first.pool ?? null)} → 第二轮 ${JSON.stringify(second.pool ?? null)}`);
  console.log(
    `  观察：两轮自然 ${first.mix.nature}→${second.mix.nature}、人文 ${first.mix.culture}→${second.mix.culture}（模型随机性内）；硬判据是上面的画像加成向量。`,
  );

  try {
    ws.close();
  } catch {
    /* 关闭失败不影响结论 */
  }

  console.log('');
  console.log(`USER_ID=${account.userId} ROOM_ID=${roomId} AB=${lastVariant}`);
  const failed = results.filter((item) => !item.ok).length;
  console.log('='.repeat(72));
  console.log(failed === 0 ? `PASS - 反馈学习闭环 ${results.length} 项全部通过` : `FAIL - ${failed}/${results.length} 项失败`);
  results.filter((item) => !item.ok).forEach((item) => console.log(`  未通过：${item.name} — ${item.detail}`));
  console.log('='.repeat(72));
  // 必须显式退出：Node 的 WebSocket 连接会让事件循环一直活着，
  // 只设 process.exitCode 的话脚本"跑完了却不结束"（实测：输出全部有，进程不退）。
  process.exit(failed === 0 ? 0 : 1);
}

main().catch((error) => {
  console.error('冒烟脚本异常:', error?.stack || error);
  process.exit(1);
});
