// front/hooks/useOmniRouteWs.ts
import { useState, useEffect, useRef } from 'react';
import { WS_BASE, fetchWsTicket } from '../app/lib/utils';

export interface AgentLog {
  role: string;
  content: string;
  suggestion: string;
}

export interface RouteNode {
  time: string;
  location: string;
  action: string;
}

export interface OmniRouteData {
  status: string;
  negotiation_summary: string;
  negotiation_log: AgentLog[];
  expert_reports: any;
  route: RouteNode[];
}

export function useOmniRouteWs(roomId: string) {
  const [data, setData] = useState<OmniRouteData | null>(null);
  const [isConnected, setIsConnected] = useState(false);
  const [isInferring, setIsInferring] = useState(false);
  const wsRef = useRef<WebSocket | null>(null);

  useEffect(() => {
    let disposed = false;
    let ws: WebSocket | null = null;
    (async () => {
      // Ticket instead of a long-lived token in the URL. The ticket is
      // single-use and expires after 60 seconds.
      let ticket = '';
      try {
        ticket = await fetchWsTicket();
      } catch {
        return;
      }
      if (disposed || !ticket) return;
      ws = new WebSocket(`${WS_BASE}/api/v1/room/${roomId}/ws?ticket=${encodeURIComponent(ticket)}`);
      wsRef.current = ws;

      ws.onopen = () => setIsConnected(true);
      ws.onclose = () => setIsConnected(false);

      ws.onmessage = (event) => {
        try {
          const msg = JSON.parse(event.data);
          if (msg.type === 'ai_negotiation_result') {
            setData(msg.payload);
            setIsInferring(false);
          }
        } catch (error) {
          console.error('WebSocket 数据解析失败', error);
        }
      };
    })();

    return () => {
      disposed = true;
      ws?.close();
    };
  }, [roomId]);

  const triggerNegotiation = (destinations: string[], userPrefs: any) => {
    if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) {
      setIsInferring(true);
      setData(null); 
      const payload = {
        type: 'agent_negotiate',
        room_id: roomId,
        user_id: 'frontend_user',
        payload: {
          destinations,
          user_preferences: userPrefs,
          constraints: { time_window: "09:00 - 18:00" }
        }
      };
      wsRef.current.send(JSON.stringify(payload));
    } else {
      alert('网关未连接，请检查后端服务！');
    }
  };

  return { isConnected, isInferring, data, triggerNegotiation };
}
