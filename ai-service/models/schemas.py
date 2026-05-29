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

class AgentProposal(BaseModel):
    proposal_id: str
    agent_id: str
    agent_name: str
    agent_role: str
    content: str
    reasoning_chain: List[str]
    fitness_score: float
    cost_impact: float
    status: str = "pending"

class RoundtableResponse(BaseModel):
    negotiation_id: str
    round: int
    proposals: List[AgentProposal]
    consensus_summary: str
    weight_hints: Dict[str, float]