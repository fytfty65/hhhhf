from pydantic import BaseModel
from typing import List, Dict, Any, Optional

# 1. 基础消息结构：接收来自 Go 网关的原始数据
class GatewayMessage(BaseModel):
    type: str
    room_id: str
    user_id: str
    payload: Dict[str, Any]

# 2. 冲突检测响应结构 (供 api/conflict.py 使用)
class ConflictResponse(BaseModel):
    has_conflict: bool
    conflict_level: Optional[str] = None
    conflict_summary: Optional[str] = None
    involved_members: Optional[List[str]] = []
    description: Optional[str] = None
    suggestion: Optional[str] = None

# 3. 协商日志步骤模型：必须包含角色、内容和建议
class NegotiationStep(BaseModel):
    role: str       # 智能体角色名
    content: str    # 说话内容
    suggestion: str # 该智能体给出的具体建议

# 4. 路线节点模型：规范 route 列表里的每一项
class RouteNode(BaseModel):
    time: str
    location: str
    action: str
    transport: Optional[str] = None
    cost_estimate: Optional[str] = None
    tags: Optional[List[str]] = []

# 5. 完整协商响应结构：返回给 OmniRoute 网关
class NegotiateResponse(BaseModel):
    status: str
    negotiation_summary: str
    negotiation_log: List[Dict[str, Any]]
    expert_reports: Dict[str, Any]
    route: List[dict]
    recommended_pois: Optional[List[Dict[str, Any]]] = []
    plan_variants: Optional[List[Dict[str, Any]]] = []
    preference_validation: Optional[Dict[str, Any]] = None

# 【已删除】旧的自然语言"圆桌"契约：AgentProposal / RoundtableResponse
#
# 删除依据（双重证据，不是凭感觉）：
#   1. 全仓库 grep：两者**零调用**（没有任何 import 或实例化；旧 AgentProposal 的字段是
#      content / reasoning_chain / fitness_score —— 自然语言提案，与目标架构"协调器只接受
#      结构化提案"相冲突）；
#   2. EVALUATION_REPORT.md:192 亦记录："schemas.py 中的 AgentProposal / RoundtableResponse
#      是完整定义但零调用的数据模型 —— 重构的骨架已经预留，但从未接线"。
#
# 结构化提案的唯一权威定义在 core/agent_contracts.py：
#   AgentInput / AgentProposal / EvidenceRef / ToolCall / FailureInfo /
#   AgentCapability / validate_agent_proposal() / capability_registry()
# 需要历史实现可查 git：git log -p -- ai-service/models/schemas.py