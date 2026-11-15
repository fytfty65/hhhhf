"""多轮智能体消息总线（确定性、无网络、无 LLM）。

为什么要它（2026-11-03）
------------------------
现有协商回环只把「裁决」塞进目标智能体的输入契约（`revision_directive`），
智能体之间**看不到彼此上一轮提了什么** —— 所谓"多轮协商"在审计上仍然只是
"协调器挨个通知了一遍"。这个总线补的是"谁在第几轮说了什么、发给了谁"：

  · `publish`：协调器/智能体发消息，指定轮次与收件人（`"*"` = 全员）；
  · `inbox`：某智能体收件箱（自动排除自己发的、只取指定轮次之后）；
  · `inject`：把收件箱写进 `AgentInput.context["messages"]`，LLM 处理器据此
    看到上一轮其它智能体的提案与裁决；
  · `transcript`：完整记录，供事件流/审计回答"分歧当时长什么样"。

约束：消息条数有上限（默认 200），超出丢最旧一条；不记录时间戳，
保证同一输入产出同一记录（可单测、可复现）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from .agent_contracts import AgentInput

BROADCAST = "*"


@dataclass(frozen=True)
class AgentMessage:
    message_id: str
    round_no: int
    sender: str
    topic: str
    recipients: Tuple[str, ...]
    content: Dict[str, Any] = field(default_factory=dict)
    correlation_id: str = ""

    def as_dict(self) -> Dict[str, Any]:
        return {
            "message_id": self.message_id,
            "round_no": self.round_no,
            "sender": self.sender,
            "topic": self.topic,
            "recipients": list(self.recipients),
            "content": dict(self.content),
            "correlation_id": self.correlation_id,
        }


class AgentMessageBus:
    """有界、可复现的多轮消息总线。"""

    def __init__(self, *, max_messages: int = 200, correlation_id: str = "") -> None:
        self.max_messages = max(0, int(max_messages))
        self.correlation_id = str(correlation_id or "")
        self._messages: List[AgentMessage] = []
        self._seq = 0
        self._round = 0
        self._dropped = 0

    @property
    def round_no(self) -> int:
        return self._round

    @property
    def dropped(self) -> int:
        return self._dropped

    def start_round(self) -> int:
        """进入下一轮（返回新轮次号；轮次从 1 开始）。"""
        self._round += 1
        return self._round

    def publish(
        self,
        *,
        sender: str,
        topic: str,
        content: Optional[Mapping[str, Any]] = None,
        recipients: Sequence[str] = (BROADCAST,),
        round_no: Optional[int] = None,
    ) -> Optional[AgentMessage]:
        """发一条消息。`round_no` 缺省用当前轮次。"""
        if self.max_messages <= 0:
            self._dropped += 1
            return None
        clean_recipients = tuple(
            str(item).strip() for item in recipients if str(item).strip()
        ) or (BROADCAST,)
        self._seq += 1
        message = AgentMessage(
            message_id=f"msg-{self._seq}",
            round_no=int(self._round if round_no is None else round_no),
            sender=str(sender or "coordinator"),
            topic=str(topic or "message"),
            recipients=clean_recipients,
            content=dict(content or {}),
            correlation_id=self.correlation_id,
        )
        self._messages.append(message)
        if len(self._messages) > self.max_messages:
            self._messages.pop(0)
            self._dropped += 1
        return message

    def inbox(self, agent_id: str, *, since_round: Optional[int] = None) -> List[Dict[str, Any]]:
        """某智能体的收件箱：不含自己发的，只看指定轮次之后（None = 全部）。"""
        target = str(agent_id or "")
        rows: List[Dict[str, Any]] = []
        for message in self._messages:
            if message.sender == target:
                continue
            if since_round is not None and message.round_no <= int(since_round):
                continue
            if BROADCAST not in message.recipients and target not in message.recipients:
                continue
            rows.append(message.as_dict())
        return rows

    def inject(self, request: AgentInput, *, since_round: Optional[int] = None) -> int:
        """把收件箱写进请求上下文（覆盖旧的 messages，避免多轮串用旧快照）。"""
        rows = self.inbox(request.agent_id, since_round=since_round)
        request.context["messages"] = rows
        request.context["message_round"] = self._round
        return len(rows)

    def transcript(self) -> List[Dict[str, Any]]:
        return [message.as_dict() for message in self._messages]

    def summary(self) -> Dict[str, Any]:
        """给事件流/诊断用的聚合口径（不展开正文）。"""
        by_topic: Dict[str, int] = {}
        by_round: Dict[int, int] = {}
        for message in self._messages:
            by_topic[message.topic] = by_topic.get(message.topic, 0) + 1
            by_round[message.round_no] = by_round.get(message.round_no, 0) + 1
        return {
            "rounds": self._round,
            "messages": len(self._messages),
            "dropped": self._dropped,
            "by_topic": dict(sorted(by_topic.items())),
            "by_round": {str(key): by_round[key] for key in sorted(by_round)},
        }
