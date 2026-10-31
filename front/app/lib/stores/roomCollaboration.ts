'use client';

import { useEffect, useRef, useState } from 'react';
import { WS_BASE, apiFetch, fetchWsTicket } from '../utils';
import { applyRoomVote, pruneRoomVotes, type RoomVoteState } from '../collaborationVotes';
import type { RoomMember, UserProfile } from '../../types';

// 房间协同 Store：房间号的创建/加入、WebSocket 连接与在线成员、节点投票同步。
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
  // 房间内其他成员的节点投票（按节点名聚合）。自己的票由节点组件单独维护，
  // 展示时会排除当前用户，避免同一张票被算两次。
  const [roomVotes, setRoomVotes] = useState<RoomVoteState>({});
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
  // 手动重连入口由 effect 内部注册，避免用 nonce 触发整个 effect 重建。
  const roomReconnectRef = useRef<(() => void) | null>(null);
  const [copied, setCopied] = useState(false);

  // 房间是真实的后端资源：首次登录自动创建并保存邀请码，避免只在浏览器里
  // 随机生成一个其他设备无法加入的“假房间”。
  useEffect(() => {
    if (!currentUser) return;
    let cancelled = false;
    const bootstrapRoom = async () => {
      setRoomError('');
      try {
        const saved = localStorage.getItem(`omni_room_${currentUser.id}`);
        if (saved) {
          const parsed = JSON.parse(saved);
          if (parsed?.room_id && parsed?.invite_code) {
            if (!cancelled) {
              setRoomCode(String(parsed.room_id));
              setRoomReady(true);
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
        localStorage.setItem(`omni_room_${currentUser.id}`, JSON.stringify(data));
        if (!cancelled) {
          setRoomCode(String(data.room_id));
          setRoomReady(true);
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
      try {
        ticket = await fetchWsTicket();
      } catch {
        ticket = '';
      }
      if (!isMounted || !ticket) {
        if (isMounted) timer = setTimeout(connectWs, 2000);
        return;
      }
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

  const retryRoomConnection = () => { roomReconnectRef.current?.(); };

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
      localStorage.setItem(`omni_room_${currentUser.id}`, JSON.stringify({ room_id: data.room_id, invite_code: inviteCode, room_name: data.room_name }));
      setRoomCode(String(data.room_id));
      setRoomReady(true);
      setInputRoomCode('');
    } catch (error: any) {
      setRoomError(error?.message || '加入房间失败');
    }
  };

  const copyRoomCode = async () => {
    try {
      await navigator.clipboard.writeText(roomCode);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {}
  };

  return {
    roomCode,
    roomReady,
    roomError,
    setRoomError,
    roomMembers,
    roomVotes,
    roomWsRef,
    roomSocketReady,
    roomSocketReadyRef,
    roomConnection,
    roomRetryAttempt,
    roomRetryDelayMs,
    retryRoomConnection,
    inputRoomCode,
    setInputRoomCode,
    copied,
    joinRoom,
    copyRoomCode,
    sendRoomVote,
    updateMember,
  };
}
