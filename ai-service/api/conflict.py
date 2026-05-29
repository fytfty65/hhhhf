# ai-service/api/conflict.py
import os
import json
from fastapi import APIRouter, HTTPException
from openai import AsyncOpenAI
from dotenv import load_dotenv
from models.schemas import GatewayMessage, ConflictResponse # 🚨 核心修复：引入 GatewayMessage

load_dotenv()

router = APIRouter()

client = AsyncOpenAI(
    base_url=os.getenv("LLM_BASE_URL", "http://localhost:11434/v1"),
    api_key=os.getenv("LLM_API_KEY", "ollama")
)
MODEL_NAME = os.getenv("LLM_MODEL_NAME", "qwen2.5:0.5b")

CONFLICT_PROMPT = """
你是一个多用户旅游行程冲突检测专家。
请分析以下房间内各成员的画像与自然语言需求，判断是否存在时间、预算、体力或偏好上的冲突。

必须返回严格的 JSON 格式：
{
  "has_conflict": true/false,
  "conflict_level": "none/minor/fatal",
  "conflict_summary": "简短的冲突原因说明（无冲突填无）",
  "involved_members": ["成员ID_1", "成员ID_2"]
}
"""

@router.post("/conflict_detect", response_model=ConflictResponse)
async def run_conflict_detection(msg: GatewayMessage):
    try:
        # 🚨 手动剥离 Payload，与 Agent 逻辑保持一致
        payload = msg.payload
        members_data = payload.get("members", {})
        
        input_str = json.dumps(members_data, ensure_ascii=False)
        print(f"收到房间 {msg.room_id} 的冲突检测请求，正在调用模型 [{MODEL_NAME}]...")

        response = await client.chat.completions.create(
            model=MODEL_NAME,
            messages=[
                {"role": "system", "content": CONFLICT_PROMPT},
                {"role": "user", "content": f"请检测以下成员需求是否存在冲突：\n{input_str}"}
            ],
            response_format={ "type": "json_object" },
            temperature=0.1
        )

        raw_result = response.choices[0].message.content
        result_dict = json.loads(raw_result)
        return ConflictResponse(**result_dict)

    except Exception as e:
        print(f"🔥 冲突检测报错: {str(e)}")
        raise HTTPException(status_code=500, detail=f"冲突检测失败: {str(e)}")