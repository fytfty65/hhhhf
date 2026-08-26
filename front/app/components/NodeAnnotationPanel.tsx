'use client';

import { useCallback, useEffect, useState } from 'react';

const BASE = 'http://localhost:8080/api/v1';

interface Annotation {
  id: string;
  node_key: string;
  user_id: string;
  author: string;
  content: string;
  parent_id: string;
  status: string;
  votes: number;
  created_at: string;
}

export default function NodeAnnotationPanel({ roomId, nodeKey, userId, nickname }: { roomId: string; nodeKey?: string; userId: string; nickname: string }) {
  const [list, setList] = useState<Annotation[]>([]);
  const [content, setContent] = useState('');
  const [status, setStatus] = useState('');

  const reload = useCallback(async () => {
    try {
      const q = nodeKey ? `node_key=${encodeURIComponent(nodeKey)}` : `room_id=${encodeURIComponent(roomId)}`;
      const r = await fetch(`${BASE}/annotations?${q}`);
      const j = await r.json();
      setList(j.annotations as Annotation[]);
    } catch (e) {
      console.error(e);
    }
  }, [roomId, nodeKey]);

  useEffect(() => {
    reload();

    // 实时同步：监听房间 WS，收到 annotation_sync 即刷新，保证多设备批注内容一致
    const ws = new WebSocket(`ws://localhost:8080/api/v1/room/${roomId}/ws?userId=${userId}&nickname=${encodeURIComponent(nickname)}`);
    ws.onmessage = (ev) => {
      try {
        const msg = JSON.parse(ev.data);
        if (msg.type === 'annotation_sync') reload();
      } catch {
        /* ignore */
      }
    };
    return () => ws.close();
  }, [roomId, userId, nickname, reload]);

  const add = async () => {
    if (!content.trim()) return;
    await fetch(`${BASE}/annotation`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ room_id: roomId, node_key: nodeKey || 'default', user_id: userId, content }),
    });
    setContent('');
    reload();
  };

  const vote = async (id: string, value: number) => {
    await fetch(`${BASE}/annotation/vote`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ annotation_id: id, user_id: userId, value }),
    });
    reload();
  };

  const resolve = async (id: string, s: string) => {
    await fetch(`${BASE}/annotation/resolve`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ annotation_id: id, status: s }),
    });
    reload();
  };

  return (
    <div className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
      <div className="mb-3 flex items-center justify-between">
        <h3 className="text-lg font-semibold text-slate-800">节点级协作批注</h3>
        <span className="rounded-full bg-indigo-100 px-2 py-0.5 text-xs text-indigo-600">实时同步</span>
      </div>

      <div className="mb-3 flex gap-2">
        <input
          value={content}
          onChange={(e) => setContent(e.target.value)}
          placeholder="对当前节点发表评论或批注..."
          className="flex-1 rounded-lg border border-slate-300 px-3 py-2 text-sm"
          onKeyDown={(e) => e.key === 'Enter' && add()}
        />
        <button onClick={add} className="rounded-lg bg-indigo-600 px-4 py-2 text-sm text-white hover:bg-indigo-700">发布</button>
      </div>

      <div className="space-y-3">
        {list.length === 0 && <p className="text-sm text-slate-400">暂无批注，快来发表第一条吧～</p>}
        {list.map((a) => (
          <div key={a.id} className={`rounded-lg border p-3 ${a.status === 'resolved' ? 'border-slate-200 bg-slate-50 opacity-60' : 'border-slate-200'}`}>
            <div className="flex items-center gap-2">
              <span className="text-sm font-medium text-slate-700">{a.author}</span>
              {a.status === 'resolved' && <span className="rounded-full bg-emerald-100 px-2 py-0.5 text-xs text-emerald-600">已解决</span>}
            </div>
            <p className="mt-1 text-sm text-slate-600">{a.content}</p>
            <div className="mt-2 flex items-center gap-3 text-xs">
              <button onClick={() => vote(a.id, 1)} className="text-slate-500 hover:text-emerald-600">👍 {a.votes >= 0 ? '+' : ''}{a.votes}</button>
              <button onClick={() => vote(a.id, -1)} className="text-slate-500 hover:text-rose-600">👎</button>
              {a.status === 'open' ? (
                <button onClick={() => resolve(a.id, 'resolved')} className="text-slate-500 hover:text-emerald-600">标记解决</button>
              ) : (
                <button onClick={() => resolve(a.id, 'open')} className="text-slate-500 hover:text-emerald-600">重新打开</button>
              )}
            </div>
          </div>
        ))}
      </div>
      {status && <p className="mt-2 text-xs text-slate-500">{status}</p>}
    </div>
  );
}