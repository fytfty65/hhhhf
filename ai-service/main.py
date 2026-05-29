import sys

sys.stdout.reconfigure(encoding='utf-8')
sys.stderr.reconfigure(encoding='utf-8')

from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI
from api import conflict, agent  # 🚨 修复点 1：把 agent 模块引入进来

app = FastAPI(
    title="OmniRoute AI Service",
    description="基于大语言模型的多智能体旅游路线规划微服务",
    version="1.0.0"
)

# 健康检查接口
@app.get("/health")
async def health_check():
    return {"status": "healthy", "service": "OmniRoute-AI"}

# 注册子路由 (加上 v1 前缀)
app.include_router(conflict.router, prefix="/api/v1", tags=["Conflict Detection"])
app.include_router(agent.router, prefix="/api/v1", tags=["Agent Negotiation"])  # 🚨 修复点 2：把 agent 路由挂载上去

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)