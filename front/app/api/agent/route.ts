import { NextResponse } from 'next/server';

export async function POST(request: Request) {
  try {
    const body = await request.json();
    const { intent, mode } = body;

    console.log("📥 [Node.js 网关接收]:", intent, "模式:", mode);

    // 智能提取用户意图中的城市（简单正则，实际可更复杂）
    let targetCity = "洛阳"; 
    if (intent.includes("濮阳") || intent.includes("范县")) targetCity = "濮阳";
    if (intent.includes("广州")) targetCity = "广州";

    // 🚨 构造符合 Python 后端 schemas.GatewayMessage 的数据结构
    const gatewayPayload = {
      type: "negotiation_request",
      room_id: "room_" + Math.random().toString(36).substring(2, 8),
      user_id: "user_master",
      payload: {
        current_request: {
          destinations: [targetCity],
          city: targetCity,
          user_preferences: { mode: mode, intent: intent }
        },
        evolution_memory: [] // 这里未来可以接入用户历史评价库
      }
    };

    console.log("🚀 [发起跨域请求] 转发至 FastAPI 引擎...");

    // 调用你的 Python FastAPI 接口
    const pythonResponse = await fetch('http://127.0.0.1:8001/api/v1/agent/negotiate', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(gatewayPayload),
      // 设置略长的超时时间，因为大模型推演需要时间
      signal: AbortSignal.timeout(60000) 
    });

    if (!pythonResponse.ok) {
      throw new Error(`Python 引擎返回错误: ${pythonResponse.status}`);
    }

    const pythonData = await pythonResponse.json();
    
    // 🚨 关键：对齐前后端的数据结构
    // Python 返回的是 route (没有 s)，前端期望的是 routes，且需要映射属性
    const mappedRoutes = pythonData.route.map((node: any) => ({
      name: node.location || node.name,
      lnglat: node.lnglat || [112.4735, 34.5564], // 必须确保有坐标
      color: "#3B82F6", // 可以根据 node.tags 动态分配颜色
      time: node.time,
      transit: node.transport,
      warning: node.cost_estimate ? `💰 预估: ${node.cost_estimate}` : null,
      desc: node.action
    }));

    return NextResponse.json({ 
      success: true, 
      data: {
        routes: mappedRoutes,
        negotiation_log: pythonData.negotiation_log, // 把智能体吵架的日志传给前端！
        summary: pythonData.negotiation_summary
      } 
    });

 } catch (error: any) {
    // 1. 在运行 npx next dev 的终端里打印真实报错
    console.error("❌ [API 网关真实崩溃原因]:", error.message); 
    
    // 2. 把真实的错误信息原封不动地返回给前端，不要再写死了
    return NextResponse.json({ 
      success: false, 
      error: error.message 
    }, { status: 500 });
  }
}