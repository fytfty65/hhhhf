/**
 * 多人协同端到端冒烟（双账号，真实 HTTP + WebSocket），零依赖：Node 24 自带全局 fetch 与 WebSocket。
 *
 * 判据（"协同到底通不通"）：
 *  1. 两个账号注册并拿到会话；
 *  2. A 建房拿邀请码 → B 凭邀请码加入同一房间；
 *  3. 双方各自建立到该房间的 WebSocket（鉴权用 ?access_token=，浏览器 WS 无法设 header）；
 *  4. A 发 cursor_move → **B 能收到**（实时协同的最小证据）；
 *  5. A 发 agent_negotiate → **旁观者 B 也能收到同一次推演结果**（协同规划的关键）。
 *
 * 用法：node work/smoke_collab.mjs
 */

const BASE = process.env.GATEWAY_BASE || 'http://127.0.0.1:8080';
const WS_BASE = BASE.replace(/^http/, 'ws');
const PASSWORD = 'Probe#2026';
const STAMP = String(Date.now());
// 推演"最终结果"的实际类型名：网关把多智能体结果作为 ai_negotiation_result 广播，
// 而不是 HTTP 流里的 final_route（第一次冒烟就是按错类型断言，误判成失败）。
const FINAL_TYPES = ['ai_negotiation_result', 'final_route'];
const results = [];

function record(name, ok, detail = '') {
  results.push({ name, ok, detail });
  console.log(`  [${ok ? 'PASS' : 'FAIL'}] ${name}${detail ? ' — ' + detail : ''}`);
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
  return { token: String(payload.token || ''), userId: String(payload.user?.id || '') };
}

/** 收集消息，直到看到目标类型或超时。 */
function collect(ws, seconds, wantTypes) {
  return new Promise((resolve) => {
    const seen = [];
    const timer = setTimeout(() => {
      ws.removeEventListener('message', onMessage);
      resolve(seen);
    }, seconds * 1000);
    function onMessage(event) {
      let msg;
      try {
        msg = JSON.parse(typeof event.data === 'string' ? event.data : String(event.data));
      } catch {
        return;
      }
      seen.push(msg);
      if (wantTypes.length > 0 && wantTypes.every((type) => seen.some((item) => String(item.type) === type))) {
        clearTimeout(timer);
        ws.removeEventListener('message', onMessage);
        resolve(seen);
      }
    }
    ws.addEventListener('message', onMessage);
  });
}

function openSocket(url) {
  return new Promise((resolve, reject) => {
    const ws = new WebSocket(url);
    const timer = setTimeout(() => reject(new Error('WS 建连超时 15s')), 15000);
    ws.addEventListener('open', () => {
      clearTimeout(timer);
      resolve(ws);
    });
    // 把 close 的 code/reason 也带上：握手被拒（401/403/404）时 error 事件里什么都没有，
    // 只有 close 才说得清到底是"没登录"还是"不是房间成员"。
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

async function main() {
  console.log('='.repeat(70));
  console.log('多人协同端到端冒烟（Node 零依赖）');
  console.log('='.repeat(70));

  const alice = await register(`smoke_a_${STAMP}`);
  const bob = await register(`smoke_b_${STAMP}`);
  record('两个账号注册并拿到会话', Boolean(alice.token && bob.token), `token 长度 ${alice.token.length}/${bob.token.length}`);
  if (!alice.token || !bob.token) return;

  const createResponse = await fetch(`${BASE}/api/v1/room/create`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${alice.token}` },
    body: JSON.stringify({ username: `smoke_a_${STAMP}`, room_name: `冒烟房间${STAMP}` }),
  });
  const created = await createResponse.json().catch(() => ({}));
  const roomId = String(created.room_id || '');
  const invite = String(created.invite_code || '');
  record(
    'A 建房并拿到邀请码',
    createResponse.status === 200 && Boolean(roomId && invite),
    `HTTP ${createResponse.status} room=${roomId.slice(0, 8)}… invite=${invite.slice(0, 8)}…`,
  );
  if (!roomId || !invite) {
    console.log('  建房响应:', JSON.stringify(created).slice(0, 300));
    return;
  }

  const joinResponse = await fetch(`${BASE}/api/v1/room/join`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${bob.token}` },
    // 加房与建房一样必须带 username（handler 里 binding:"required"），只传 invite_code 会 400
    body: JSON.stringify({ username: `smoke_b_${STAMP}`, invite_code: invite, role: '队友' }),
  });
  record('B 凭邀请码加入同一房间', joinResponse.status === 200, `HTTP ${joinResponse.status} ${(await joinResponse.text()).slice(0, 140)}`);

  // ---- WebSocket 双连接 ----
  let wsA;
  let wsB;
  try {
    wsA = await openSocket(`${WS_BASE}/api/v1/room/${roomId}/ws?access_token=${alice.token}&user_id=${alice.userId}`);
    wsB = await openSocket(`${WS_BASE}/api/v1/room/${roomId}/ws?access_token=${bob.token}&user_id=${bob.userId}`);
    record('双方 WebSocket 建连', true, '同房间双连接已建立');
  } catch (error) {
    record('双方 WebSocket 建连', false, error.message);
    return;
  }

  // ---- 4) 协同广播 ----
  const bCursor = collect(wsB, 8, ['cursor_move', 'cursor_update', 'presence']);
  wsA.send(
    JSON.stringify({
      type: 'cursor_move',
      room_id: roomId,
      user_id: alice.userId,
      payload: { lng: 86.15, lat: 41.76, node: '博斯腾湖' },
    }),
  );
  const cursorEvents = await bCursor;
  record(
    '协同广播：B 收到 A 的光标/在场事件',
    cursorEvents.length > 0,
    cursorEvents.length > 0 ? `B 收到 ${cursorEvents.length} 条：${cursorEvents.slice(0, 5).map((m) => m.type).join(', ')}` : 'B 什么都没收到（协同未生效）',
  );

  // ---- 5) 协同推演 ----
  const startedAt = Date.now();
  const aFinal = collect(wsA, 220, FINAL_TYPES);
  const bFinal = collect(wsB, 220, FINAL_TYPES);
  wsA.send(
    JSON.stringify({
      type: 'agent_negotiate',
      room_id: roomId,
      user_id: alice.userId,
      payload: {
        destinations: ['库尔勒'],
        current_request: { destinations: ['库尔勒'], city: '' },
        user_preferences: {
          intent: '去库尔勒玩2天，想看自然风景，节奏不要太赶',
          mode: 'coop',
          role: '寻味探索',
          members: 2,
          budget: 4000,
        },
      },
    }),
  );
  const [aEvents, bEvents] = await Promise.all([aFinal, bFinal]);
  const seconds = ((Date.now() - startedAt) / 1000).toFixed(1);
  const aHasFinal = aEvents.some((m) => FINAL_TYPES.includes(String(m.type)));
  const bHasFinal = bEvents.some((m) => FINAL_TYPES.includes(String(m.type)));
  record('发起者 A 收到推演结果', aHasFinal, `${aEvents.length} 条事件 / ${seconds}s`);
  record('旁观者 B 也收到同一推演结果（协同规划关键判据）', bHasFinal, `${bEvents.length} 条事件`);
  // 没拿到 final_route 时，把收到的类型与最后一条内容打出来 —— 用来区分"只是慢"还是"报错了"
  if (!aHasFinal) {
    const types = aEvents.map((m) => String(m.type || (m.token ? 'token' : '?')));
    console.log(`  A 收到的事件类型：${types.slice(0, 14).join(', ')}`);
    const last = aEvents[aEvents.length - 1];
    console.log(`  A 最后一条：${JSON.stringify(last).slice(0, 400)}`);
  }
  if (!bHasFinal) {
    const types = bEvents.map((m) => String(m.type || (m.token ? 'token' : '?')));
    console.log(`  B 收到的事件类型：${types.slice(0, 14).join(', ')}`);
  }

  try {
    wsA.close();
    wsB.close();
  } catch {
    /* 关闭失败不影响结论 */
  }

  console.log('='.repeat(70));
  const passed = results.filter((item) => item.ok).length;
  console.log(`结果：${passed}/${results.length} 项通过`);
  for (const item of results.filter((entry) => !entry.ok)) {
    console.log(`  未通过：${item.name} — ${item.detail}`);
  }
  console.log('='.repeat(70));
}

main().catch((error) => {
  console.error('冒烟脚本异常:', error);
  process.exitCode = 1;
});
