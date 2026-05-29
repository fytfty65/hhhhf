// front/hooks/useOmniRouteWs.ts
import { useState, useEffect, useRef } from 'react';

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
    // 连接到 Go 网关
    const ws = new WebSocket(`ws://localhost:8080/api/v1/room/${roomId}/ws`);
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

    return () => ws.close();
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