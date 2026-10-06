"""RollingGo MCP 酒店取价的测试（2026-10-05）。

用**真实响应**做样本（`tools/probe_mcp_provider.py` 打出来的原始结构，只删掉长文本字段），
这样字段名解析不会"自己编一个形状然后自测通过"。
"""

import ast
import io
import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import rollinggo_mcp  # noqa: E402

# 真实响应片段（喀什，2026-10-11 入住）：`searchHotels` 返回的第一家
REAL_ROW = {
    "hotelId": 2172962,
    "bookingUrl": "https://rollinggo.cn/pages/hotel/detail/index?id=2172962&checkInDate=2026-10-11&checkOutDate=2026-10-12",
    "name": "尚客优酒店(喀什古城店)",
    "brand": "桑克U酒店",
    "address": "色满路43号",
    "latitude": 39.475218,
    "longitude": 75.986157,
    "starRating": 2.0,
    "price": {"message": "查价成功。1晚总价：131CNY（约131CNY/晚）", "hasPrice": True, "currency": "CNY", "lowestPrice": 131.0},
    "hotelAmenities": ["叫醒服务", "行李寄存"],
}
REAL_PAYLOAD = {"success": True, "code": 2000, "message": "酒店搜索成功", "hotelInformationList": [REAL_ROW]}


def _env(**overrides):
    """只读测试：把供应商配置显式设好（不读机器上的真实 .env）。"""
    values = {"ROLLINGGO_API_URL": "https://mcp.example.test/mcp", "ROLLINGGO_API_KEY": "mcp_test_key"}
    values.update(overrides)
    return mock.patch.dict(os.environ, values, clear=False)


class ConfigTests(unittest.TestCase):
    def test_not_configured_is_an_honest_state(self):
        with mock.patch.dict(os.environ, {"ROLLINGGO_API_URL": "", "ROLLINGGO_API_KEY": ""}, clear=False):
            config = rollinggo_mcp.mcp_config()
        self.assertFalse(config["enabled"])
        self.assertIn("ROLLINGGO_API_URL", config["reason"])

    def test_token_without_url_is_not_enabled(self):
        with mock.patch.dict(os.environ, {"ROLLINGGO_API_URL": "", "ROLLINGGO_API_KEY": "mcp_x"}, clear=False):
            self.assertFalse(rollinggo_mcp.mcp_config()["enabled"])

    def test_enabled_when_both_present(self):
        with _env():
            config = rollinggo_mcp.mcp_config()
        self.assertTrue(config["enabled"])
        self.assertTrue(config["token_present"])

    def test_explicit_switch_can_turn_it_off(self):
        with _env(ROLLINGGO_MCP_ENABLED="0"):
            config = rollinggo_mcp.mcp_config()
        self.assertFalse(config["enabled"])
        self.assertIn("显式关闭", config["reason"])


class ParsingTests(unittest.TestCase):
    def test_real_payload_parses(self):
        rows = rollinggo_mcp.hotel_rows(REAL_PAYLOAD)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["name"], "尚客优酒店(喀什古城店)")

    def test_payload_as_json_string_also_parses(self):
        rows = rollinggo_mcp.hotel_rows(json.dumps(REAL_PAYLOAD, ensure_ascii=False))
        self.assertEqual(len(rows), 1)

    def test_alternative_keys_are_tolerated(self):
        """供应商改键名时不能"悄悄一个都不返回"（静默失败比报错更糟）。"""
        for key in ("hotels", "list", "items"):
            self.assertEqual(len(rollinggo_mcp.hotel_rows({key: [{"name": "甲酒店"}]})), 1)
        self.assertEqual(rollinggo_mcp.hotel_rows({"data": {"hotels": [{"name": "乙酒店"}]}})[0]["name"], "乙酒店")
        self.assertEqual(rollinggo_mcp.hotel_rows("不是 JSON"), [])
        self.assertEqual(rollinggo_mcp.hotel_rows(None), [])

    def test_normalize_real_row(self):
        row = rollinggo_mcp.normalize_hotel(REAL_ROW)
        self.assertEqual(row["price"], 131.0)
        self.assertEqual(row["currency"], "CNY")
        self.assertTrue(row["has_price"])
        self.assertEqual(row["star"], 2.0)
        self.assertEqual(row["hotel_id"], 2172962)
        self.assertIn("bookingUrl", json.dumps({"bookingUrl": row["booking_url"]}))
        self.assertEqual(row["lnglat"], [75.986157, 39.475218])
        self.assertIn("1晚总价：131CNY", row["message"])

    def test_price_falls_back_to_the_message_when_lowest_price_missing(self):
        row = rollinggo_mcp.normalize_hotel(
            {"name": "甲", "price": {"message": "查价成功。1晚总价：418CNY（约418CNY/晚）", "hasPrice": True}}
        )
        self.assertEqual(row["price"], 418.0)

    def test_no_price_is_none_not_zero(self):
        """供应商说"没价"时必须是 None（unknown ≠ 0）。"""
        row = rollinggo_mcp.normalize_hotel({"name": "甲", "price": {"message": "暂无报价", "hasPrice": False}})
        self.assertIsNone(row["price"])
        self.assertFalse(row["has_price"])
        self.assertEqual(rollinggo_mcp.parse_price_message("暂无报价"), None)

    def test_message_price_parser_takes_the_first_amount(self):
        self.assertEqual(rollinggo_mcp.parse_price_message("查价成功。1晚总价：1,280CNY（约1280CNY/晚）"), 1280.0)


class MatchTests(unittest.TestCase):
    def test_exact_and_contained_names_match(self):
        rows = [rollinggo_mcp.normalize_hotel(REAL_ROW)]
        self.assertIsNotNone(rollinggo_mcp.match_hotel(rows, "尚客优酒店(喀什古城店)"))
        self.assertIsNotNone(rollinggo_mcp.match_hotel(rows, "尚客优酒店"))
        self.assertIsNotNone(rollinggo_mcp.match_hotel(rows, "尚客优酒店(喀什古城店) 分店"))

    def test_different_hotel_does_not_match(self):
        """**绝不拿同城别家的价冒充这一家**（那是编造价格）。"""
        rows = [rollinggo_mcp.normalize_hotel(REAL_ROW)]
        self.assertIsNone(rollinggo_mcp.match_hotel(rows, "喀什古城昆仑塔逸扉度假酒店"))
        self.assertIsNone(rollinggo_mcp.match_hotel(rows, ""))
        self.assertIsNone(rollinggo_mcp.match_hotel([], "任何酒店"))


class CallToolTests(unittest.TestCase):
    def test_mutation_tools_are_refused_by_the_allowlist(self):
        """安全边界：只读白名单以外（下单/取消类）的工具**代码层面就进不来**。"""
        import asyncio

        result = asyncio.run(rollinggo_mcp._call_tool("createOrder", {"hotelId": 1}))
        self.assertFalse(result["ok"])
        self.assertIn("只读白名单", result["error"])
        self.assertEqual(rollinggo_mcp.READ_ONLY_TOOLS, ("searchHotels", "getHotelDetail"))

    def test_not_configured_never_calls_the_network(self):
        import asyncio

        with mock.patch.dict(os.environ, {"ROLLINGGO_API_URL": "", "ROLLINGGO_API_KEY": ""}, clear=False):
            with mock.patch("httpx.AsyncClient") as client:
                result = asyncio.run(rollinggo_mcp._call_tool("searchHotels", {}))
        self.assertFalse(result["ok"])
        self.assertTrue(result.get("not_configured"))
        client.assert_not_called()


class HotelPriceTests(unittest.TestCase):
    def _call_tool_returning(self, rows, status_ok=True):
        async def fake(tool, arguments, *, timeout=None):
            self.assertEqual(tool, "searchHotels")
            if not status_ok:
                return {"ok": False, "payload": None, "error": "HTTP 500"}
            return {"ok": True, "payload": {"hotelInformationList": rows}}

        return fake

    def test_matched_hotel_returns_verified_source_and_booking_url(self):
        import asyncio

        with _env():
            with mock.patch.object(rollinggo_mcp, "_call_tool", self._call_tool_returning([REAL_ROW])):
                result = asyncio.run(rollinggo_mcp.hotel_price("尚客优酒店(喀什古城店)", "喀什", check_in="2026-10-11", nights=1))
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["price"], 131.0)
        self.assertEqual(result["source"], "rollinggo")
        self.assertIn("rollinggo.cn", result["booking_url"])
        self.assertEqual(result["check_in"], "2026-10-11")

    def test_city_search_is_used_as_a_second_attempt(self):
        """这家酒店在"酒店名检索"里没返回时，再按城市检索一次匹配（命中率实测不稳定）。"""
        import asyncio

        calls = []

        async def fake(tool, arguments, *, timeout=None):
            calls.append(arguments["placeType"])
            if arguments["placeType"] == "酒店":
                return {"ok": True, "payload": {"hotelInformationList": [{"name": "另一家酒店", "price": {"lowestPrice": 999}}]}}
            return {"ok": True, "payload": {"hotelInformationList": [REAL_ROW]}}

        with _env():
            with mock.patch.object(rollinggo_mcp, "_call_tool", fake):
                result = asyncio.run(rollinggo_mcp.hotel_price("尚客优酒店(喀什古城店)", "喀什"))
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["price"], 131.0)
        self.assertEqual(calls, ["酒店", "城市"])

    def test_rows_without_this_hotel_is_not_found_not_a_substitute(self):
        import asyncio

        with _env():
            with mock.patch.object(rollinggo_mcp, "_call_tool", self._call_tool_returning([{"name": "别家酒店", "price": {"lowestPrice": 288}}])):
                result = asyncio.run(rollinggo_mcp.hotel_price("我们要住的酒店", "库尔勒"))
        self.assertEqual(result["status"], "not_found")
        self.assertIsNone(result.get("price"))
        self.assertIn("不拿别家的价顶替", result["error"])

    def test_call_failure_is_reported_with_reason(self):
        import asyncio

        with _env():
            with mock.patch.object(rollinggo_mcp, "_call_tool", self._call_tool_returning([], status_ok=False)):
                result = asyncio.run(rollinggo_mcp.hotel_price("某酒店", "库尔勒"))
        self.assertEqual(result["status"], "error")
        self.assertIn("HTTP 500", result["error"])

    def test_not_configured_short_circuits(self):
        import asyncio

        with mock.patch.dict(os.environ, {"ROLLINGGO_API_URL": "", "ROLLINGGO_API_KEY": ""}, clear=False):
            result = asyncio.run(rollinggo_mcp.hotel_price("某酒店", "库尔勒"))
        self.assertEqual(result["status"], "not_configured")

    def test_arguments_follow_the_declared_schema(self):
        """参数名与枚举照抄服务端 schema（placeType=酒店/城市、place 补国家）。"""
        hotel_args = rollinggo_mcp._arguments(name="甲酒店", city="库尔勒", check_in="2026-10-10", nights=2, adults=2, place_is_hotel=True)
        self.assertEqual(hotel_args["placeType"], "酒店")
        self.assertEqual(hotel_args["place"], "甲酒店 库尔勒")
        self.assertEqual(hotel_args["checkInParam"]["stayNights"], 2)
        city_args = rollinggo_mcp._arguments(name="甲酒店", city="库尔勒", place_is_hotel=False)
        self.assertEqual(city_args["placeType"], "城市")
        self.assertEqual(city_args["place"], "库尔勒 中国")
        # 不传日期时不要把空字符串塞给服务端（它有"未传则用明天"的默认行为）
        self.assertNotIn("checkInDate", city_args["checkInParam"])
        # 晚数有上限（服务端声明最多 28 晚）
        self.assertEqual(rollinggo_mcp._arguments(name="甲", city="乙", nights=99, place_is_hotel=True)["checkInParam"]["stayNights"], 28)


class ObservationTests(unittest.TestCase):
    def test_ok_result_becomes_a_verified_observation(self):
        observation = rollinggo_mcp.build_observation(
            "尚客优酒店(喀什古城店)",
            {
                "status": "ok",
                "matched_name": "尚客优酒店(喀什古城店)",
                "price": 131.0,
                "message": "查价成功。1晚总价：131CNY（约131CNY/晚）",
                "booking_url": "https://rollinggo.cn/x",
                "nights": 1,
                "check_in": "2026-10-11",
            },
        )
        self.assertEqual(observation["value"], 131.0)
        self.assertEqual(observation["source"], "rollinggo")
        self.assertEqual(observation["source_url"], "https://rollinggo.cn/x")
        self.assertIn("131CNY", observation["excerpt"])

    def test_failure_observation_has_no_value(self):
        observation = rollinggo_mcp.build_observation("某酒店", {"status": "not_found", "error": "没有这一家"})
        self.assertIsNone(observation["value"])
        self.assertEqual(observation["source"], "rollinggo")
        self.assertIn("没有这一家", observation["error"])

    def test_price_source_is_accepted_by_the_shared_verified_set(self):
        """`rollinggo` 必须在**全局**可核实来源集合里，否则合并回方案时仍是"估算"。"""
        from core.plan_quality import VERIFIED_SOURCES_OK
        from core.price_sources import VERIFIED_SOURCES, merge_observations

        self.assertIn("rollinggo", VERIFIED_SOURCES)
        self.assertIn("rollinggo", VERIFIED_SOURCES_OK)
        plan = {"route": [{"day": 1, "name": "尚客优酒店(喀什古城店)", "type": "住宿", "cost_estimate": "暂无供应商数据", "estimated": True, "data_sources": {}}]}
        merged = merge_observations(plan, [rollinggo_mcp.build_observation("尚客优酒店(喀什古城店)", {"status": "ok", "matched_name": "尚客优酒店(喀什古城店)", "price": 131.0, "message": "查价成功", "booking_url": "https://rollinggo.cn/x"})])
        node = merged["plan"]["route"][0]
        self.assertEqual(node["cost_estimate"], "¥131")
        self.assertEqual(node["data_sources"]["cost_estimate"], "rollinggo")
        self.assertFalse(node["estimated"], "可核实来源不该再标「估算」")


class SearchHotelsTests(unittest.TestCase):
    """城市级候选检索：让住宿候选**自带实价**（不再是"先选酒店再补价"）。"""

    def _call_tool_returning(self, rows):
        async def fake(tool, arguments, *, timeout=None):
            fake.arguments = arguments
            return {"ok": True, "payload": {"hotelInformationList": rows}}

        return fake

    def test_city_search_returns_normalized_hotels_with_prices(self):
        import asyncio

        with _env():
            with mock.patch.object(rollinggo_mcp, "_call_tool", self._call_tool_returning([REAL_ROW])):
                result = asyncio.run(rollinggo_mcp.search_hotels("库尔勒", size=5))
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["count"], 1)
        hotel = result["hotels"][0]
        self.assertEqual(hotel["name"], "尚客优酒店(喀什古城店)")
        self.assertEqual(hotel["price"], 131.0)
        self.assertIn("rollinggo.cn", hotel["booking_url"])

    def test_budget_and_star_filters_go_to_the_server(self):
        import asyncio

        fake = self._call_tool_returning([REAL_ROW])
        with _env():
            with mock.patch.object(rollinggo_mcp, "_call_tool", fake):
                asyncio.run(
                    rollinggo_mcp.search_hotels("库尔勒", max_price_per_night=400, min_star=4.0)
                )
        arguments = fake.arguments
        self.assertEqual(arguments["hotelTags"], {"maxPricePerNight": 400.0})
        self.assertEqual(arguments["filterOptions"], {"starRatings": [4.0, 5.0]})
        self.assertEqual(arguments["placeType"], "城市")
        self.assertEqual(arguments["place"], "库尔勒 中国")

    def test_filters_are_omitted_when_not_requested(self):
        import asyncio

        fake = self._call_tool_returning([REAL_ROW])
        with _env():
            with mock.patch.object(rollinggo_mcp, "_call_tool", fake):
                asyncio.run(rollinggo_mcp.search_hotels("库尔勒"))
        self.assertNotIn("hotelTags", fake.arguments)
        self.assertNotIn("filterOptions", fake.arguments)

    def test_empty_result_is_reported_not_faked(self):
        import asyncio

        with _env():
            with mock.patch.object(rollinggo_mcp, "_call_tool", self._call_tool_returning([])):
                result = asyncio.run(rollinggo_mcp.search_hotels("某城"))
        self.assertEqual(result["status"], "empty")
        self.assertEqual(result["hotels"], [])
        self.assertTrue(result["error"])

    def test_call_failure_is_reported(self):
        import asyncio

        async def fake(tool, arguments, *, timeout=None):
            return {"ok": False, "payload": None, "error": "HTTP 503"}

        with _env():
            with mock.patch.object(rollinggo_mcp, "_call_tool", fake):
                result = asyncio.run(rollinggo_mcp.search_hotels("某城"))
        self.assertEqual(result["status"], "error")
        self.assertIn("HTTP 503", result["error"])

    def test_not_configured_short_circuits(self):
        import asyncio

        with mock.patch.dict(os.environ, {"ROLLINGGO_API_URL": "", "ROLLINGGO_API_KEY": ""}, clear=False):
            result = asyncio.run(rollinggo_mcp.search_hotels("某城"))
        self.assertEqual(result["status"], "not_configured")

    def test_nameless_rows_are_dropped(self):
        """"只有价格没有名字"的行没法用（也不能塞进方案），直接丢掉并如实计数。"""
        import asyncio

        with _env():
            with mock.patch.object(rollinggo_mcp, "_call_tool", self._call_tool_returning([{"price": {"lowestPrice": 1}}])):
                result = asyncio.run(rollinggo_mcp.search_hotels("某城"))
        self.assertEqual(result["status"], "empty")


class LodgingPassTests(unittest.TestCase):
    """`price_lodging_nodes`：给**最终路线**里的住宿查实价（住宿定型之后才调用）。"""

    def _plan(self):
        return {
            "route": [
                {"day": 1, "name": "库尔勒园林宾馆", "type": "住宿", "is_hotel": True, "cost_estimate": "暂无供应商数据", "estimated": True, "data_sources": {}},
                {"day": 1, "name": "博斯腾湖", "type": "风景名胜", "cost_estimate": "暂无供应商数据", "estimated": True, "data_sources": {}},
            ]
        }

    def test_lodging_node_gets_a_verified_supplier_price(self):
        import asyncio

        async def fake_hotel_price(name, city, *, check_in="", nights=1, adults=2, timeout=None):
            return {
                "status": "ok",
                "name": name,
                "matched_name": name,
                "price": 316.0,
                "message": "查价成功。1晚总价：316CNY（约316CNY/晚）",
                "booking_url": "https://rollinggo.cn/pages/hotel/detail/index?id=2745070",
                "check_in": check_in,
                "nights": nights,
            }

        with _env():
            with mock.patch.object(rollinggo_mcp, "hotel_price", fake_hotel_price):
                result = asyncio.run(rollinggo_mcp.price_lodging_nodes(self._plan(), city="库尔勒", nights=1))
        route = {node["name"]: node for node in result["plan"]["route"]}
        lodging = route["库尔勒园林宾馆"]
        self.assertEqual(lodging["cost_estimate"], "¥316")
        self.assertEqual(lodging["data_sources"]["cost_estimate"], "rollinggo")
        self.assertFalse(lodging["estimated"])
        self.assertIn("rollinggo.cn", lodging["price_source_url"])
        self.assertEqual(result["report"]["matched"], 1)
        self.assertEqual(result["report"]["prices"], {"库尔勒园林宾馆": 316.0})
        # 非住宿节点不该被这次查询碰到
        self.assertEqual(route["博斯腾湖"]["cost_estimate"], "暂无供应商数据")

    def test_only_lodging_nodes_are_queried(self):
        import asyncio

        asked = []

        async def fake_hotel_price(name, city, *, check_in="", nights=1, adults=2, timeout=None):
            asked.append(name)
            return {"status": "not_found", "name": name, "error": "没有这一家"}

        with _env():
            with mock.patch.object(rollinggo_mcp, "hotel_price", fake_hotel_price):
                result = asyncio.run(rollinggo_mcp.price_lodging_nodes(self._plan(), city="库尔勒"))
        self.assertEqual(asked, ["库尔勒园林宾馆"])
        self.assertEqual(result["report"]["not_found"], ["库尔勒园林宾馆"])
        self.assertIsNone(result["plan"]["route"][0]["cost_estimate"] and None)  # 没被改成数字
        self.assertEqual(result["plan"]["route"][0]["cost_estimate"], "暂无供应商数据")

    def test_already_verified_lodging_is_skipped(self):
        """已经有可核实价格（供应商/高德给的）就不再花钱查一遍。"""
        import asyncio

        plan = self._plan()
        plan["route"][0]["cost_estimate"] = "¥288"
        plan["route"][0]["data_sources"] = {"cost_estimate": "amap"}
        plan["route"][0]["estimated"] = False
        asked = []

        async def fake_hotel_price(name, city, **kwargs):
            asked.append(name)
            return {"status": "ok", "name": name, "price": 1.0}

        with _env():
            with mock.patch.object(rollinggo_mcp, "hotel_price", fake_hotel_price):
                asyncio.run(rollinggo_mcp.price_lodging_nodes(plan, city="库尔勒"))
        self.assertEqual(asked, [])

    def test_check_in_date_is_read_from_the_booking_url(self):
        """报价口径要按**供应商 URL 里的实际日期**说（实测它返回的是 2026-10-20，不是"明天"）。"""
        import asyncio

        self.assertEqual(rollinggo_mcp.check_in_from_url(REAL_ROW["bookingUrl"]), "2026-10-11")
        self.assertEqual(rollinggo_mcp.check_in_from_url("https://x/y"), "")
        self.assertEqual(rollinggo_mcp.check_in_from_url(None), "")

        async def fake_hotel_price(name, city, *, check_in="", nights=1, adults=2, timeout=None):
            return {
                "status": "ok",
                "name": name,
                "matched_name": name,
                "price": 183.0,
                "message": "查价成功。2晚总价：366CNY（约183CNY/晚）",
                "booking_url": "https://rollinggo.cn/pages/hotel/detail/index?id=1&checkInDate=2026-10-20&checkOutDate=2026-10-22",
                "check_in_actual": "2026-10-20",
                "check_in": check_in,
            }

        with _env():
            with mock.patch.object(rollinggo_mcp, "hotel_price", fake_hotel_price):
                result = asyncio.run(rollinggo_mcp.price_lodging_nodes(self._plan(), city="库尔勒"))
        self.assertEqual(result["report"]["check_in_dates"], ["2026-10-20"])
        self.assertIn("2026-10-20", result["report"]["check_in"])
        self.assertNotIn("按明日", result["report"]["check_in"])

    def test_user_supplied_date_is_not_overwritten(self):
        import asyncio

        async def fake_hotel_price(name, city, *, check_in="", nights=1, adults=2, timeout=None):
            return {"status": "ok", "name": name, "matched_name": name, "price": 1.0, "check_in_actual": "2026-10-20"}

        with _env():
            with mock.patch.object(rollinggo_mcp, "hotel_price", fake_hotel_price):
                result = asyncio.run(
                    rollinggo_mcp.price_lodging_nodes(self._plan(), city="库尔勒", start_date="2026-12-01")
                )
        self.assertEqual(result["report"]["check_in"], "2026-12-01", "用户给了日期就以用户的为准")

    def test_query_limit_is_respected(self):
        import asyncio

        plan = {"route": [
            {"day": day, "name": f"酒店{day}", "type": "住宿", "is_hotel": True, "cost_estimate": "暂无供应商数据", "estimated": True, "data_sources": {}}
            for day in range(1, 6)
        ]}
        asked = []

        async def fake_hotel_price(name, city, **kwargs):
            asked.append(name)
            return {"status": "not_found", "name": name, "error": "没有"}

        with _env():
            with mock.patch.object(rollinggo_mcp, "hotel_price", fake_hotel_price):
                asyncio.run(rollinggo_mcp.price_lodging_nodes(plan, city="库尔勒", limit=2))
        self.assertEqual(len(asked), 2)

    def test_matched_but_no_number_is_reported_not_invented(self):
        """"匹配到这家、但供应商没给数字"要如实分开记（unknown ≠ 0）。"""
        import asyncio

        async def fake_hotel_price(name, city, **kwargs):
            return {"status": "ok", "name": name, "matched_name": name, "price": None, "message": "暂无可售房型", "check_in": ""}

        with _env():
            with mock.patch.object(rollinggo_mcp, "hotel_price", fake_hotel_price):
                result = asyncio.run(rollinggo_mcp.price_lodging_nodes(self._plan(), city="库尔勒"))
        self.assertEqual(result["report"]["without_price"], ["库尔勒园林宾馆"])
        self.assertEqual(result["report"]["prices"], {})
        self.assertEqual(result["plan"]["route"][0]["cost_estimate"], "暂无供应商数据")

    def test_not_configured_leaves_the_plan_untouched(self):
        import asyncio

        with mock.patch.dict(os.environ, {"ROLLINGGO_API_URL": "", "ROLLINGGO_API_KEY": ""}, clear=False):
            result = asyncio.run(rollinggo_mcp.price_lodging_nodes(self._plan(), city="库尔勒"))
        self.assertFalse(result["report"]["enabled"])
        self.assertEqual(result["report"]["queried"], 0)
        self.assertEqual(result["plan"]["route"][0]["cost_estimate"], "暂无供应商数据")

    def test_check_in_and_nights_are_passed_through(self):
        """报价口径必须跟着价格走：查的是哪个入住日期、住几晚，要能原样下发。"""
        import asyncio

        seen = {}

        async def fake_hotel_price(name, city, *, check_in="", nights=1, adults=2, timeout=None):
            seen["check_in"] = check_in
            seen["nights"] = nights
            return {"status": "not_found", "name": name, "error": "没有"}

        with _env():
            with mock.patch.object(rollinggo_mcp, "hotel_price", fake_hotel_price):
                result = asyncio.run(
                    rollinggo_mcp.price_lodging_nodes(self._plan(), city="库尔勒", nights=2, start_date="2026-10-11")
                )
        self.assertEqual(seen, {"check_in": "2026-10-11", "nights": 2})
        self.assertEqual(result["report"]["check_in"], "2026-10-11")
        self.assertEqual(result["report"]["nights"], 2)


class WiringTests(unittest.TestCase):
    """取价步骤里的接线（源码级检查：这段只在真实链路执行，单测跑不到）。"""

    def _agent_source(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with io.open(os.path.join(root, "api", "agent.py"), encoding="utf-8") as handle:
            return handle.read()

    def test_hotel_prices_are_queried_after_the_route_is_final(self):
        """住宿价格必须在**住宿定型之后**查（收口会补/换酒店，早查的价会丢）。"""
        source = self._agent_source()
        self.assertIn("price_lodging_nodes(", source)
        self.assertIn('"rollinggo": _lodging_report', source)
        # 关键顺序：路线排序（收口之后）→ 酒店查价
        self.assertLess(source.index("order_route_by_day(final_data.get(\"route\")"), source.index("price_lodging_nodes("))

    def test_web_price_step_skips_hotels(self):
        """联网取价不再浪费预算在酒店上（酒店有真供应商），只跑剩下的节点。"""
        source = self._agent_source()
        self.assertIn('_web_targets = [item for item in _targets if str(item.get("intent") or "") != "hotel"]', source)
        self.assertIn("for _index, _item in enumerate(_web_targets):", source)

    def test_lodging_candidates_come_from_the_supplier_with_prices(self):
        """住宿候选要接上供应商实价（"按实价选酒店"，而不是先选完再补价）。"""
        source = self._agent_source()
        self.assertIn("_supplier_search_hotels(", source)
        self.assertIn('"price_source": "rollinggo"', source)
        self.assertIn('"data_source": "rollinggo"', source)
        # 预算/档次交给服务端筛（少一次猜测）
        self.assertIn("max_price_per_night=_nightly_cap", source)
        self.assertIn("min_star=4.0 if is_luxury else None", source)

    def test_supplier_candidate_block_only_uses_defined_names(self):
        """那段代码里用到的局部名字必须都有定义/导入。

        真实教训：先写了 `_normalize_text(...)` 却没有导入，`py_compile` 不会报错，
        运行时才抛 NameError —— 而它被 except 接住，表现成"供应商没给候选"，
        排查只能靠猜（跟之前 `_blocked` 那次是同一类问题）。
        """
        source = self._agent_source()
        self.assertIn("from core.candidate_index import normalize_text as _normalize_text", source)
        # 用到之前必须先绑定：导入行要在第一次使用之前
        self.assertLess(source.index("import normalize_text as _normalize_text"), source.index("_normalize_text(str(item.get"))

    def test_candidates_without_coordinates_never_enter_the_pool(self):
        """供应商没给坐标的候选**不进候选池**（留在可选清单里）。

        真实事故（2026-10-05 伊宁）：某候选没坐标 → 规划写成 `[0,0]` →
        "第 2 天移动 9440km"（整个方案的空间判断全废，总分从 88 掉到 75）。
        """
        source = self._agent_source()
        self.assertIn('_lodging_supply.setdefault("without_coords", []).append(_hotel_name)', source)
        self.assertIn("if not (", source)
        self.assertIn("== 0.0 and float(_hotel_lnglat[1]) == 0.0", source)

    def test_agent_only_uses_read_only_rollinggo_helpers(self):
        """agent.py 里除了 price_lodging_nodes，不许出现别的 rollinggo 调用（更不许下单）。"""
        tree = ast.parse(self._agent_source())
        used = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == "rollinggo_mcp":
                used.add(node.attr)
        self.assertTrue(used.issubset({"price_lodging_nodes"}), used)
        self.assertNotIn("createOrder", self._agent_source())


if __name__ == "__main__":
    unittest.main()
