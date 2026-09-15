import sys
import hmac
import os

sys.stdout.reconfigure(encoding='utf-8')
sys.stderr.reconfigure(encoding='utf-8')

from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from api import conflict, agent, carbon_service, operations
from core.observability import ObservabilityMiddleware, METRICS  # P3 可观测性

app = FastAPI(
    title="OmniRoute AI Service",
    description="基于大语言模型的多智能体旅游路线规划微服务",
    version="1.0.0"
)

# P3 可观测性：请求级结构化日志 + 延迟指标 + X-Request-ID 追踪
app.add_middleware(ObservabilityMiddleware)


def _internal_auth_config() -> tuple[str, bool]:
    """Return the gateway shared secret without ever logging its value.

    Internal authentication is REQUIRED unless it is explicitly disabled for a
    local run. The previous default (required only in production) meant an
    unset APP_ENV left every LLM-burning endpoint open on all interfaces.
    """
    expected = os.getenv("AI_SERVICE_INTERNAL_TOKEN", "").strip()
    relaxed = os.getenv("AI_SERVICE_ALLOW_ANONYMOUS", "").strip().lower() in {"1", "true", "yes"}
    return expected, not relaxed


def _bind_host() -> str:
    """Bind loopback by default; a public bind must be an explicit decision."""
    return os.getenv("AI_SERVICE_HOST", "127.0.0.1").strip() or "127.0.0.1"


@app.middleware("http")
async def internal_service_auth(request: Request, call_next):
    # Health remains public for orchestration probes. Every operational AI
    # endpoint is private; when no token is configured the service fails
    # closed instead of serving unauthenticated traffic.
    if request.url.path == "/health":
        return await call_next(request)
    expected, required = _internal_auth_config()
    supplied = request.headers.get("X-Omni-Internal-Token", "")
    if required and not expected:
        return JSONResponse({"error": "AI service internal auth is not configured", "code": "INTERNAL_AUTH_NOT_CONFIGURED"}, status_code=503)
    if expected and not hmac.compare_digest(supplied, expected):
        return JSONResponse({"error": "internal authentication required", "code": "INTERNAL_AUTH_REQUIRED"}, status_code=401)
    return await call_next(request)

# 健康检查接口
@app.get("/health")
async def health_check():
    return {"status": "healthy", "service": "OmniRoute-AI"}

# 指标端点：请求总数 / 分路径计数 / 平均延迟 / 分状态计数
@app.get("/metrics")
async def metrics():
    return METRICS.snapshot()

# 注册子路由 (加上 v1 前缀)
app.include_router(conflict.router, prefix="/api/v1", tags=["Conflict Detection"])
app.include_router(agent.router, prefix="/api/v1", tags=["Agent Negotiation"])  # 🚨 修复点 2：把 agent 路由挂载上去
app.include_router(carbon_service.router, prefix="/api/v1", tags=["Carbon Footprint"])  # P5 碳足迹评估
app.include_router(operations.router, prefix="/api/v1", tags=["Trip Operations"])

if __name__ == "__main__":
    import uvicorn

    # reload=True is a development file watcher and must never front real
    # traffic; it also cannot be combined with a worker count.
    dev_reload = os.getenv("APP_ENV", "development").strip().lower() not in {"production", "prod"}
    uvicorn.run("main:app", host=_bind_host(), port=int(os.getenv("AI_SERVICE_PORT", "8000")), reload=dev_reload)
