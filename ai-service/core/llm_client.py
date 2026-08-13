# ai-service/core/worldmonitor_client.py
import requests
import os

class WorldMonitorClient:
    def __init__(self, base_url: str = "https://api.worldmonitor.app"):
        self.base_url = os.getenv("WORLDMONITOR_API_URL", base_url)
        self.api_key = os.getenv("WORLDMONITOR_API_KEY", "")

    def get_location_safety_context(self, region_name: str) -> dict:
        """
        获取特定区域的 CII (国家/地区不稳定指数) 和实时突发事件
        """
        try:
            # 真实对接时可调用 worldmonitor-sdk 或 MCP 接口
            # 此处演示结构化返回
            headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
            # 示例假数据结构，生产环境替换为真实 HTTP/MCP 调用
            return {
                "region": region_name,
                "cii_score": 15, # 低风险 0-100
                "active_alerts": [
                    {"type": "WEATHER", "level": "WARNING", "detail": "山区局部有强降雨，盘山公路易滑坡"},
                    {"type": "TRAFFIC", "level": "INFO", "detail": "景区高峰期交通管制"}
                ]
            }
        except Exception as e:
            return {"region": region_name, "cii_score": 0, "active_alerts": []}