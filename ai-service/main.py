import hmac
import os

# Windows 控制台/重定向管道默认按 cp936 写 stdout：输出 emoji 会抛
# UnicodeEncodeError，把一次成功的启动伪装成崩溃。入口统一改成 UTF-8，
# 且不可编码字符降级为替换符，日志永远不能因为字符集中断进程。
from core.console import ensure_utf8_console

ensure_utf8_console()

# 下面的导入必须晚于 stdout 重配置与 load_dotenv()：api 模块在导入时会读取环境变量。
from dotenv import load_dotenv  # noqa: E402
load_dotenv()

# Fail before the heavy imports below if the interpreter is not the pinned one or
# a locked dependency is missing/unimportable. Without this, a wrong environment
# surfaced as a ModuleNotFoundError from deep inside api.agent, which hid both the
# cause and the fix.
from core.preflight import assert_runtime_ready  # noqa: E402
assert_runtime_ready()

from fastapi import FastAPI, Request  # noqa: E402
from fastapi.responses import JSONResponse, Response  # noqa: E402
from api import conflict, agent, carbon_service, operations, evaluation  # noqa: E402
from api.agent import planning_bandit  # noqa: E402
from core.observability import ObservabilityMiddleware, METRICS  # P3 可观测性  # noqa: E402
from core.cache_registry import cache_metrics  # 缓存命中率/容量/TTL 快照  # noqa: E402
from core.llm_metrics import llm_metrics  # LLM 次数/失败率/延迟分位/token/成本  # noqa: E402

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
# 另附两块此前不可观测的口径：
#   caches —— 每个缓存的容量上限、TTL、命中率、过期/淘汰条数（"缓存到底有没有用"）
#   llm    —— 调用次数、失败率、延迟 p50/p95、首 token 延迟、token 与成本（未配置单价时为 null）
@app.get("/metrics")
async def metrics():
    snapshot = METRICS.snapshot()
    snapshot["caches"] = cache_metrics()
    snapshot["llm"] = llm_metrics()
    return snapshot


# Prometheus 文本格式（含延迟直方图，可算分位数），供抓取器使用。
# 与 JSON 端点并存：JSON 保持既有消费方兼容，本端点服务监控系统。
@app.get("/metrics/prometheus")
async def metrics_prometheus():
    bandit = planning_bandit.snapshot()
    llm = llm_metrics()
    extra = {
        "bandit_decisions": bandit.get("decisions", 0),
        "bandit_explorations": bandit.get("explorations", 0),
        "bandit_rewards_recorded": bandit.get("rewards_recorded", 0),
        "bandit_arm_count": bandit.get("arm_count", 0),
        "bandit_observed_non_greedy_rate": bandit.get("observed_non_greedy_rate", 0.0),
        "llm_calls_total": llm.get("calls", 0),
        "llm_failures_total": llm.get("failures", 0),
        "llm_failure_rate": float(llm.get("failure_rate") or 0.0),
        "llm_latency_p95_ms": float((llm.get("latency_ms") or {}).get("p95") or 0.0),
        "llm_cost_total": float(llm.get("cost") or 0.0),
    }
    for name, stats in cache_metrics().items():
        safe = "".join(ch if ch.isalnum() else "_" for ch in str(name))
        extra[f"cache_{safe}_entries"] = stats.get("entries", 0)
        extra[f"cache_{safe}_hit_rate"] = float(stats.get("hit_rate") or 0.0)
        extra[f"cache_{safe}_evictions_total"] = stats.get("evictions", 0)
        extra[f"cache_{safe}_expired_total"] = stats.get("expired", 0)
    return Response(
        content=METRICS.prometheus(extra=extra),
        media_type="text/plain; version=0.0.4; charset=utf-8",
    )

# 注册子路由 (加上 v1 前缀)
app.include_router(conflict.router, prefix="/api/v1", tags=["Conflict Detection"])
app.include_router(agent.router, prefix="/api/v1", tags=["Agent Negotiation"])  # 🚨 修复点 2：把 agent 路由挂载上去
app.include_router(carbon_service.router, prefix="/api/v1", tags=["Carbon Footprint"])  # P5 碳足迹评估
app.include_router(operations.router, prefix="/api/v1", tags=["Trip Operations"])
app.include_router(evaluation.router, prefix="/api/v1", tags=["Evaluation Observability"])

if __name__ == "__main__":
    import uvicorn

    # reload=True is a development file watcher and must never front real
    # traffic; it also cannot be combined with a worker count.
    dev_reload = os.getenv("APP_ENV", "development").strip().lower() not in {"production", "prod"}
    # 把运行模式打出来：reload 模式在 Windows 上是"父 supervisor + 子 worker"，
    # 子进程会**继承监听套接字**——只杀父进程就留下占住 8000 端口的孤儿，
    # 下次启动直接 bind 失败。日志里有这一行，排查时一眼就能看出是不是 reload 模式。
    print(
        f"🚀 [启动] APP_ENV={os.getenv('APP_ENV')!r} reload={dev_reload} "
        f"bind={_bind_host()}:{os.getenv('AI_SERVICE_PORT', '8000')}",
        flush=True,
    )
    uvicorn.run("main:app", host=_bind_host(), port=int(os.getenv("AI_SERVICE_PORT", "8000")), reload=dev_reload)
