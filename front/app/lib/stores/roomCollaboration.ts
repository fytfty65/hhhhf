'use client';

import { useEffect, useRef, useState } from 'react';
import { WS_BASE, apiFetch, fetchWsTicket, ApiRequestError } from '../utils';
import { applyRoomVote, pruneRoomVotes, type RoomVoteState } from '../collaborationVotes';
import type { RoomMember, UserProfile } from '../../types';

// 房间协同 Store：房间号的创建/加入、WebSocket 连接与在线成员、节点投票同步。
//
// 「房主视图同步」：房主进入规划页会广播 room_view，跟随的成员据此自动进入同一页面；
// 房主推演完成后广播 room_plan，成员直接复用这份方案，不再各自调用一次 AI。
// 成员对某个行程节点的文字建议。房主在大厅/工作台能看到并按节点聚合，
// 采纳后走现有局部重排，结果由 room_plan 广播回所有人。
export type RoomComment = {
  id: string;
  node_name: string;
  day?: number;
  text: string;
  user_id: string;
  user_name: string;
  ts: number;
  status: 'open' | 'adopted' | 'dismissed';
  handled_by?: string;
  handled_by_name?: string;
};

function normalizeRoomComment(raw: any): RoomComment | null {
  if (!raw || typeof raw !== 'object') return null;
  const id = String(raw.id || '').trim();
  const nodeName = String(raw.node_name || '').trim();
  const text = String(raw.text || '').trim();
  if (!id || !nodeName || !text) return null;
  const day = Number(raw.day);
  return {
    id,
    node_name: nodeName,
    day: Number.isFinite(day) && day > 0 ? Math.round(day) : undefined,
    text,
    user_id: String(raw.user_id || ''),
    user_name: String(raw.user_name || '队友'),
    ts: Number(raw.ts) || Date.now(),
    status: raw.status === 'adopted' || raw.status === 'dismissed' ? raw.status : 'open',
    handled_by: raw.handled_by ? String(raw.handled_by) : undefined,
    handled_by_name: raw.handled_by_name ? String(raw.handled_by_name) : undefined,
  };
}

export type RoomViewState = {
  view: 'lobby' | 'planning';
  destination?: string;
  days?: number;
  travelers?: number;
  user_id?: string;
  user_name?: string;
  ts?: number;
};

export function useRoomCollaborationStore({
  currentUser,
  currentUserRef,
  role,
  intent,
}: {
  currentUser: UserProfile | null;
  currentUserRef: { current: UserProfile | null };
  role: string;
  intent: string;
}) {
  const [roomCode, setRoomCode] = useState<string>('');
  const [inputRoomCode, setInputRoomCode] = useState<string>('');
  const [roomReady, setRoomReady] = useState(false);
  const [roomError, setRoomError] = useState('');
  const [roomMembers, setRoomMembers] = useState<RoomMember[]>([]);
  // 是否为房间创建者（房主）。只有房主能把手里的方案分发给其他成员。
  const [isHost, setIsHost] = useState(false);
  const isHostRef = useRef(false);
  // 房主当前所在的视图：成员据此决定是否跟随进入。
  const [roomView, setRoomView] = useState<RoomViewState | null>(null);
  // 跟随房主：默认开启，成员可随时关闭；按用户持久化。
  const [followHost, setFollowHostState] = useState(true);
  // 最近一次房主广播的方案：晚加入的成员进入工作台时直接用它。
  const [latestPlan, setLatestPlan] = useState<{ payload: any; ts: number } | null>(null);
  const latestPlanRef = useRef<{ payload: any; ts: number } | null>(null);
  const followHostRef = useRef(true);
  // 房间内其他成员的节点投票（按节点名聚合）。自己的票由节点组件单独维护，
  // 展示时会排除当前用户，避免同一张票被算两次。
  const [roomVotes, setRoomVotes] = useState<RoomVoteState>({});
  // 节点建议：所有成员都能写，房主负责采纳或忽略。
  const [roomComments, setRoomComments] = useState<RoomComment[]>([]);
  // 专门指向「房间协同 socket」：工作台推理 socket 会覆盖 wsRef，
  // 用它发送投票会在推理连接断开期间静默失败。
  const roomWsRef = useRef<WebSocket | null>(null);
  // 权威的「协同 socket 已打开」信号，由 onopen/onclose 维护；
  // 工作台入口用它判断，而不是猜一个固定时长。
  const [roomSocketReady, setRoomSocketReady] = useState(false);
  const roomSocketReadyRef = useRef(false);
  // 连接状态与重连进度：只给布尔值会让界面在断线时仍显示“已同步”。
  const [roomConnection, setRoomConnection] = useState<'idle' | 'connecting' | 'connected' | 'reconnecting' | 'offline'>('idle');
  const [roomRetryAttempt, setRoomRetryAttempt] = useState(0);
  const [roomRetryDelayMs, setRoomRetryDelayMs] = useState(0);
  // 登录态失效（access token 与 refresh 都过期）：继续 2 秒一次重试只会让
  // 用户静默卡在大厅且不在房间里，这里改为停下并提示重新登录。
  const [roomAuthExpired, setRoomAuthExpired] = useState(false);
  // 手动重连入口由 effect 内部注册，避免用 nonce 触发整个 effect 重建。
  const roomReconnectRef = useRef<(() => void) | null>(null);
  const [copied, setCopied] = useState(false);
  // 刚加入房间时先向房主要一份当前方案（房主可能已经推演完）。
  const wantPlanRef = useRef(false);
  // 房主当前广播出去的视图，供成员进场时索要、也供重连后补发。
  const currentViewRef = useRef<RoomViewState | null>(null);
  // 大厅连接与工作台连接可能几乎同时各发一次索要，方案又大，合并成一次回发。
  const lastPlanReplyRef = useRef(0);

  // 跟随房主按用户持久化：换设备/刷新后仍保持上次的选择。
  useEffect(() => {
    if (!currentUser) return;
    const savedFollow = localStorage.getItem(`omni_follow_host_${currentUser.id}`);
    const nextFollow = savedFollow === null ? true : savedFollow === '1';
    followHostRef.current = nextFollow;
    setFollowHostState(nextFollow);
  }, [currentUser]);

  const setFollowHost = (next: boolean) => {
    followHostRef.current = next;
    setFollowHostState(next);
    if (currentUser) localStorage.setItem(`omni_follow_host_${currentUser.id}`, next ? '1' : '0');
  };

  // 房间是真实的后端资源：首次登录自动创建并保存邀请码，避免只在浏览器里
  // 随机生成一个其他设备无法加入的“假房间”。
  useEffect(() => {
    if (!currentUser) return;
    let cancelled = false;
    const bootstrapRoom = async () => {
      setRoomError('');
      setRoomAuthExpired(false);
      try {
        const saved = localStorage.getItem(`omni_room_${currentUser.id}`);
        if (saved) {
          const parsed = JSON.parse(saved);
          if (parsed?.room_id && parsed?.invite_code) {
            if (!cancelled) {
              setRoomCode(String(parsed.room_id));
              setRoomReady(true);
              // 旧数据没有 owner 字段：创建者本机会先存房间，按房主处理；
              // 通过邀请码加入的设备会在 joinRoom 里显式写成 false。
              const hostFlag = parsed.owner !== false;
              isHostRef.current = hostFlag;
              setIsHost(hostFlag);
            }
            return;
          }
        }
        const res = await apiFetch('/api/v1/room/create', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ username: currentUser.username, room_name: `${currentUser.nickname || currentUser.username} 的旅行计划`, role: '主理人' }),
        });
        const data = await res.json();
        if (!res.ok || !data.room_id) throw new Error(data.error || '协作房间创建失败');
        localStorage.setItem(`omni_room_${currentUser.id}`, JSON.stringify({ ...data, owner: true }));
        if (!cancelled) {
          setRoomCode(String(data.room_id));
          setRoomReady(true);
          isHostRef.current = true;
          setIsHost(true);
        }
      } catch (error: any) {
        if (!cancelled) {
          setRoomReady(false);
          setRoomError(error?.message || '协作房间暂时不可用');
        }
      }
    };
    bootstrapRoom();
    return () => { cancelled = true; };
  }, [currentUser]);

  // WebSocket 维持房间连接与在线成员同步
  useEffect(() => {
    let ws: WebSocket | null = null;
    let timer: ReturnType<typeof setTimeout> | null = null;
    let isMounted = true;
    let retryCount = 0;

    const connectWs = async () => {
      // 新建 socket 尚未就绪，先清标志。否则上一连接留下的 true 会放用户
      // 进入一个仍在握手的协同界面。
      roomSocketReadyRef.current = false;
      if (!isMounted || !currentUser || !roomReady || !roomCode) return;
      // 没网时不必反复握手，先如实显示“网络已断开”，等 online 事件再补连。
      if (typeof navigator !== 'undefined' && navigator.onLine === false) {
        setRoomConnection('offline');
        timer = setTimeout(connectWs, 5000);
        return;
      }
      setRoomConnection('connecting');
      // 浏览器无法在 WebSocket 握手上带 Authorization。把 15 分钟的 access token
      // 放进 URL 会被日志/历史/Referer 泄漏，改为一次性 ticket，网关握手时消费。
      let ticket = '';
      let authExpired = false;
      try {
        ticket = await fetchWsTicket();
      } catch (error) {
        // 401 = access token 失效且 refresh 也续不上。此时任何重试都不会成功，
        // 必须让用户重新登录，而不是每 2 秒静默重试、停在“连接中”。
        authExpired = error instanceof ApiRequestError && error.status === 401;
        ticket = '';
      }
      if (!isMounted) return;
      if (!ticket) {
        if (authExpired) {
          setRoomAuthExpired(true);
          setRoomConnection('offline');
          return;
        }
        timer = setTimeout(connectWs, 2000);
        return;
      }
      setRoomAuthExpired(false);
      const url = `${WS_BASE}/ws?room_id=${encodeURIComponent(roomCode)}&user_id=${encodeURIComponent(currentUser.id)}&nickname=${encodeURIComponent(currentUser.nickname)}&role=${encodeURIComponent(role)}&intent=${encodeURIComponent(intent)}${currentUser.avatarUrl ? '&avatar_url=' + encodeURIComponent(currentUser.avatarUrl) : ''}&ticket=${encodeURIComponent(ticket)}`;
      ws = new WebSocket(url);

      ws.onmessage = (event) => {
        try {
          const msg = JSON.parse(event.data);
          if (msg.type === 'room_members_update') {
            const payload = msg.payload || [];
            // 用 ref 取最新用户，解决闭包陈旧：把真实头像注入成员列表。
            const cu = currentUserRef.current;
            const enriched = payload.map((m: any) => {
              if (m.id === cu?.id && cu?.avatarUrl) {
                return { ...m, avatarUrl: cu.avatarUrl };
              }
              return m;
            });
            setRoomMembers(enriched);
            // 成员离开后清掉他的票，避免界面留着已离线成员的「幽灵票」。
            // 成员列表为空时 pruneRoomVotes 原样返回（通常意味着同步还没到）。
            const activeIds = enriched.map((m: any) => String(m?.id ?? ''));
            setRoomVotes((prev) => pruneRoomVotes(prev, activeIds));
          } else if (msg.type === 'node_vote') {
            // 网关只广播事实（谁对哪个节点投了什么）。自己的票已在本地乐观更新，
            // 这里只合并其他人的票。
            const payload = msg.payload || {};
            const voterId = String(payload.user_id || msg.user_id || '');
            if (!voterId || voterId === currentUserRef.current?.id) return;
            setRoomVotes((prev) => applyRoomVote(prev, payload.node_name, voterId, payload.vote));
          } else if (msg.type === 'node_comment') {
            const comment = normalizeRoomComment(msg.payload);
            if (!comment) return;
            setRoomComments((prev) => (prev.some((item) => item.id === comment.id) ? prev : [...prev, comment]));
          } else if (msg.type === 'node_comment_ack') {
            // 房主采纳/忽略后同步状态，成员端也能看到「已被采纳」。
            const payload = msg.payload || {};
            const targetId = String(payload.id || '');
            if (!targetId) return;
            setRoomComments((prev) => prev.map((item) => item.id === targetId
              ? {
                  ...item,
                  status: payload.status === 'adopted' || payload.status === 'dismissed' ? payload.status : item.status,
                  handled_by: payload.handled_by ? String(payload.handled_by) : item.handled_by,
                  handled_by_name: payload.handled_by_name ? String(payload.handled_by_name) : item.handled_by_name,
                }
              : item));
          } else if (msg.type === 'node_comments_snapshot') {
            const list = Array.isArray(msg.payload) ? msg.payload : [];
            setRoomComments(list
              .map((raw: any) => normalizeRoomComment(raw))
              .filter((item: RoomComment | null): item is RoomComment => Boolean(item))
              .sort((a: RoomComment, b: RoomComment) => a.ts - b.ts));
          } else if (msg.type === 'room_view') {
            // 房主在哪个视图。自己的回显忽略，避免把自己的广播当成别人的动作。
            const payload = msg.payload || {};
            const senderId = String(payload.user_id || msg.user_id || '');
            if (senderId && senderId === currentUserRef.current?.id) return;
            setRoomView({ ...payload, ts: Date.now() });
          } else if (msg.type === 'room_plan') {
            // 房主推演完成的方案。留一份给「随后才进入工作台」的成员。
            const payload = msg.payload || {};
            latestPlanRef.current = { payload, ts: Date.now() };
            setLatestPlan({ payload, ts: Date.now() });
          } else if (msg.type === 'room_view_request') {
            // 新成员进场索要房主当前视图：房主已在规划页时，成员无需等下一次广播。
            if (!isHostRef.current) return;
            const current = currentViewRef.current;
            const socket = roomWsRef.current;
            if (current && socket && socket.readyState === WebSocket.OPEN) {
              socket.send(JSON.stringify({ type: 'room_view', payload: current }));
            }
          } else if (msg.type === 'room_plan_request') {
            // 新成员索要当前方案：只有房主持有可分发的那一份。
            if (!isHostRef.current) return;
            const latest = latestPlanRef.current;
            const socket = roomWsRef.current;
            const now = Date.now();
            if (now - lastPlanReplyRef.current < 1500) return;
            lastPlanReplyRef.current = now;
            if (latest && socket && socket.readyState === WebSocket.OPEN) {
              socket.send(JSON.stringify({ type: 'room_plan', payload: latest.payload }));
            }
          }
        } catch (err) {
          // 静默吞掉解析错误会让“成员列表不动”变成无法定位的玄学问题。
          console.warn('[room-ws] 收到无法解析的消息，已忽略', err);
        }
      };

      ws.onopen = () => {
        if (!isMounted) return;
        retryCount = 0;
        roomSocketReadyRef.current = true;
        setRoomSocketReady(true);
        setRoomConnection('connected');
        setRoomRetryAttempt(0);
        setRoomRetryDelayMs(0);
        // 刚加入房间时主动索要房主已有的方案；房主端收到请求再回一次。
        if (wantPlanRef.current) {
          wantPlanRef.current = false;
          try {
            ws?.send(JSON.stringify({ type: 'room_plan_request', payload: {} }));
          } catch {}
        }
        // 成员（含重连）总是向房主确认一次当前视图：房主可能早已在规划页，
        // 只靠一次性的 room_view 广播会让后加入的人停在大厅。
        if (!isHostRef.current) {
          try {
            ws?.send(JSON.stringify({ type: 'room_view_request', payload: {} }));
          } catch {}
        }
        // 拉取房间里已有的节点建议（晚加入也能看到之前讨论过什么）。
        try {
          ws?.send(JSON.stringify({ type: 'node_comment_request', payload: {} }));
        } catch {}
      };
      ws.onerror = () => {
        console.warn('[room-ws] 连接异常，将按指数退避重连');
      };
      ws.onclose = () => {
        roomSocketReadyRef.current = false;
        setRoomSocketReady(false);
        if (roomWsRef.current === ws) roomWsRef.current = null;
        if (!isMounted) return;
        // 指数退避 + 抖动：1s / 2s / 4s … 上限 30s。固定 3s 会在网关重启时
        // 让所有房间同时打满重连，恢复反而更慢。
        const backoff = Math.min(30000, 1000 * Math.pow(2, retryCount)) + Math.floor(Math.random() * 400);
        retryCount += 1;
        setRoomConnection('reconnecting');
        setRoomRetryAttempt(retryCount);
        setRoomRetryDelayMs(backoff);
        timer = setTimeout(connectWs, backoff);
      };
      roomWsRef.current = ws;
    };

    connectWs();

    // 手动“立即重试”：关掉旧连接、重置退避，立刻重新握手。
    const forceReconnect = () => {
      const previous = ws;
      ws = null;
      if (previous) {
        previous.onclose = null;
        previous.onerror = null;
        try { previous.close(); } catch {}
      }
      roomSocketReadyRef.current = false;
      setRoomSocketReady(false);
      if (timer) clearTimeout(timer);
      timer = null;
      retryCount = 0;
      setRoomRetryAttempt(0);
      setRoomRetryDelayMs(0);
      connectWs();
    };
    roomReconnectRef.current = forceReconnect;

    // 断网/恢复：断网时不再假装“连接中”，恢复后不等退避计时器，立刻补连。
    const handleOffline = () => { if (isMounted) setRoomConnection('offline'); };
    const handleOnline = () => {
      if (!isMounted) return;
      if (ws && ws.readyState === WebSocket.OPEN) { setRoomConnection('connected'); return; }
      if (ws && ws.readyState === WebSocket.CONNECTING) { setRoomConnection('connecting'); return; }
      forceReconnect();
    };
    window.addEventListener('offline', handleOffline);
    window.addEventListener('online', handleOnline);

    // 移动端/标签页切回前台时立刻补连，而不是等退避计时器慢慢走完。
    const handleVisibility = () => {
      if (!isMounted || document.visibilityState !== 'visible') return;
      if (ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING)) return;
      if (timer) clearTimeout(timer);
      timer = null;
      retryCount = 0;
      connectWs();
    };
    document.addEventListener('visibilitychange', handleVisibility);

    return () => {
      isMounted = false;
      roomReconnectRef.current = null;
      document.removeEventListener('visibilitychange', handleVisibility);
      window.removeEventListener('offline', handleOffline);
      window.removeEventListener('online', handleOnline);
      if (timer) clearTimeout(timer);
      if (roomWsRef.current === ws) roomWsRef.current = null;
      if (ws) ws.close();
    };
  }, [roomCode, roomReady, currentUser, role, intent, currentUserRef]);

  // 把节点投票事实交给房间协同 socket 广播。连接未就绪时只保留本地乐观更新，
  // 不排队重试——否则离线期间改过的票会在重连后以「旧事实」重新广播出去。
  const sendRoomVote = (nodeName: string, vote: 'up' | 'down' | null) => {
    const socket = roomWsRef.current;
    if (!socket || socket.readyState !== WebSocket.OPEN) return;
    socket.send(JSON.stringify({ type: 'node_vote', payload: { node_name: nodeName, vote } }));
  };

  // 发送一条节点建议。连接未就绪时明确返回失败，由界面提示稍后再试。
  const sendNodeComment = (nodeName: string, day: number | undefined, text: string) => {
    const node = String(nodeName || '').trim();
    const body = String(text || '').trim();
    if (!node || !body) return false;
    const socket = roomWsRef.current;
    if (!socket || socket.readyState !== WebSocket.OPEN) {
      setRoomError('房间连接正在恢复，建议稍后再发送');
      return false;
    }
    const suffix = Math.random().toString(36).slice(2, 8);
    socket.send(JSON.stringify({
      type: 'node_comment',
      payload: {
        id: `c_${currentUser?.id || 'guest'}_${Date.now()}_${suffix}`,
        node_name: node,
        day,
        text: body.slice(0, 300),
      },
    }));
    return true;
  };

  // 房主处理建议：采纳或忽略。先做本地乐观更新，网络回显会再确认一次。
  const ackNodeComment = (id: string, status: 'adopted' | 'dismissed') => {
    if (!id) return;
    setRoomComments((prev) => prev.map((item) => (item.id === id
      ? { ...item, status, handled_by: currentUser?.id, handled_by_name: currentUser?.nickname }
      : item)));
    const socket = roomWsRef.current;
    if (!socket || socket.readyState !== WebSocket.OPEN) return;
    socket.send(JSON.stringify({ type: 'node_comment_ack', payload: { id, status } }));
  };

  const retryRoomConnection = () => { roomReconnectRef.current?.(); };

  // 房主广播自己所在的视图，跟随的成员据此自动进出规划页。
  const sendRoomView = (view: 'lobby' | 'planning', extra?: { destination?: string; days?: number; travelers?: number }) => {
    const payload: RoomViewState = {
      view,
      destination: extra?.destination,
      days: extra?.days,
      travelers: extra?.travelers,
      user_id: currentUser?.id,
      user_name: currentUser?.nickname,
      ts: Date.now(),
    };
    // 先记住房主视图：即使此刻 socket 未就绪，成员稍后索要时也能答上来。
    currentViewRef.current = payload;
    const socket = roomWsRef.current;
    if (!socket || socket.readyState !== WebSocket.OPEN) return;
    socket.send(JSON.stringify({ type: 'room_view', payload }));
  };

  // 房主把推演结果分发给房间；同时本地留一份，供新成员索要时重发。
  const broadcastRoomPlan = (payload: any) => {
    const snapshot = { payload, ts: Date.now() };
    latestPlanRef.current = snapshot;
    setLatestPlan(snapshot);
    const socket = roomWsRef.current;
    if (!socket || socket.readyState !== WebSocket.OPEN) return;
    socket.send(JSON.stringify({ type: 'room_plan', payload }));
  };

  const updateMember = (nextRole: string, nextIntent: string) => {
    const socket = roomWsRef.current;
    if (!socket || socket.readyState !== WebSocket.OPEN) return;
    socket.send(JSON.stringify({ type: 'update_member', payload: { role: nextRole, intent: nextIntent } }));
  };

  const joinRoom = async () => {
    const inviteCode = inputRoomCode.trim().toUpperCase();
    if (!inviteCode || !currentUser) return;
    setRoomError('');
    try {
      const res = await apiFetch('/api/v1/room/join', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username: currentUser.username, invite_code: inviteCode, role }),
      });
      const data = await res.json();
      if (!res.ok || !data.room_id) throw new Error(data.error || '加入房间失败');
      localStorage.setItem(`omni_room_${currentUser.id}`, JSON.stringify({ room_id: data.room_id, invite_code: inviteCode, room_name: data.room_name, owner: false }));
      // 加入者是成员，不是房主；同时先向房主要一次当前方案。
      isHostRef.current = false;
      setIsHost(false);
      wantPlanRef.current = true;
      setRoomCode(String(data.room_id));
      setRoomReady(true);
      setInputRoomCode('');
      // 换房间后旧房间的建议不属于这里，等新房间的 snapshot 覆盖。
      setRoomComments([]);
      setRoomMembers([]);
      setRoomVotes({});
    } catch (error: any) {
      setRoomError(error?.message || '加入房间失败');
    }
  };

  const copyRoomCode = async () => {
    const text = roomCode.trim();
    if (!text) return;
    const markCopied = () => {
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    };
    // 线上是 http:// 访问，不属于安全上下文，navigator.clipboard 不可用
    // 或调用被浏览器直接拒绝。先试现代 API，失败再退回 execCommand。
    try {
      if (navigator.clipboard?.writeText) {
        await navigator.clipboard.writeText(text);
        markCopied();
        return;
      }
    } catch {}
    try {
      const textarea = document.createElement('textarea');
      textarea.value = text;
      textarea.setAttribute('readonly', '');
      textarea.style.position = 'fixed';
      textarea.style.top = '-1000px';
      textarea.style.opacity = '0';
      document.body.appendChild(textarea);
      textarea.select();
      textarea.setSelectionRange(0, text.length);
      const ok = document.execCommand('copy');
      document.body.removeChild(textarea);
      if (ok) {
        markCopied();
        return;
      }
    } catch {}
    // 两种方式都失败时必须告诉用户，否则会以为已经复制成功。
    setRoomError(`复制失败，请手动记下房间码：${text}`);
  };

  // 重新生成一个房间：房间码本来就是随机生成的，只是本机会记住上次的房间，
  // 所以长期显示同一个码。给房主一个明确的“换一个”入口，避免被误认为写死。
  const regenerateRoom = async () => {
    if (!currentUser) return;
    setRoomError('');
    try {
      const res = await apiFetch('/api/v1/room/create', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username: currentUser.username, room_name: `${currentUser.nickname || currentUser.username} 的旅行计划`, role: '主理人' }),
      });
      const data = await res.json();
      if (!res.ok || !data.room_id) throw new Error(data.error || '房间创建失败');
      localStorage.setItem(`omni_room_${currentUser.id}`, JSON.stringify({ ...data, owner: true }));
      isHostRef.current = true;
      setIsHost(true);
      wantPlanRef.current = false;
      currentViewRef.current = null;
      latestPlanRef.current = null;
      setLatestPlan(null);
      setRoomView(null);
      setRoomMembers([]);
      setRoomVotes({});
      setRoomComments([]);
      setRoomCode(String(data.room_id));
      setRoomReady(true);
    } catch (error: any) {
      setRoomError(error?.message || '房间创建失败');
    }
  };

  return {
    roomCode,
    roomReady,
    roomError,
    setRoomError,
    roomMembers,
    roomVotes,
    roomComments,
    sendNodeComment,
    ackNodeComment,
    roomWsRef,
    roomSocketReady,
    roomSocketReadyRef,
    roomConnection,
    roomAuthExpired,
    roomRetryAttempt,
    roomRetryDelayMs,
    retryRoomConnection,
    inputRoomCode,
    setInputRoomCode,
    copied,
    joinRoom,
    copyRoomCode,
    regenerateRoom,
    sendRoomVote,
    updateMember,
    isHost,
    roomView,
    sendRoomView,
    followHost,
    setFollowHost,
    latestPlan,
    broadcastRoomPlan,
  };
}
