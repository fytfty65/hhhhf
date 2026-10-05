"""明确排除项的可修复硬约束测试。

真实背景（2026-10-04）：用户说"不要博物馆/人文"，我们做到了"候选池不留人文 + 目标 0 +
事后报警"，但**模型自己硬塞进来的博物馆不会被修** —— 只是如实告诉你。
这一层把它做成可修复的硬约束：同类替换优先，换不了就移出，动作全部留痕。
"""

import unittest

from core.degradation import collect_degradations
from core.route_composition import repair_forbidden_categories


def classify(name, poi_type="", is_hotel=False):
    """简化分类器：用名字/类别里的关键词判断（与生产 classify_poi_category 同口径的近似）。"""
    text = f"{name} {poi_type}"
    if is_hotel or any(word in name for word in ("酒店", "民宿", "宾馆")):
        return "hotel"
    if any(word in text for word in ("博物馆", "博物", "展馆", "古迹", "遗址", "文化")):
        return "cultural"
    if any(word in text for word in ("餐", "美食", "面", "抓饭", "馕")):
        return "food"
    if any(word in text for word in ("公园", "湖", "草原", "沙漠", "景区", "林")):
        return "scenic"
    return "other"


POOL = [
    {"name": "博斯腾湖风景名胜区", "type": "风景名胜;风景名胜;国家级景点", "lnglat": [86.76, 41.98]},
    {"name": "胡杨林", "type": "风景名胜;风景名胜;风景名胜", "lnglat": [86.1, 41.2]},
    {"name": "托克逊老四号拌面", "type": "餐饮服务;中餐厅;中餐厅", "lnglat": [86.2, 41.7]},
]


class RepairForbiddenTests(unittest.TestCase):
    def test_museum_is_replaced_from_the_pool(self):
        route = [
            {"day": 1, "name": "巴州博物馆", "type": "科教文化服务;博物馆;博物馆", "time": "10:00"},
            {"day": 1, "name": "孔雀公园", "type": "风景名胜;公园广场;公园", "time": "14:00"},
        ]
        kept, actions = repair_forbidden_categories(route, forbidden=("cultural",), classify=classify, pool=POOL)
        names = [item["name"] for item in kept]
        self.assertNotIn("巴州博物馆", names, "明确排除的类别不该再出现")
        self.assertIn("博斯腾湖风景名胜区", names, "应换成同类可去的自然景区")
        self.assertEqual(kept[0]["day"], 1, "替换要保持原位置/原天")
        self.assertEqual(kept[0]["time"], "10:00", "时间字段应保留")
        self.assertEqual(kept[0]["replaced_from"], "巴州博物馆", "要能追溯换掉了什么")
        self.assertEqual(actions[0]["code"], "forbidden_category_replaced")
        self.assertIn("不要人文", actions[0]["reason"])

    def test_nodes_of_other_categories_are_untouched(self):
        route = [
            {"day": 1, "name": "孔雀公园", "type": "风景名胜;公园广场;公园"},
            {"day": 1, "name": "老四号拌面", "type": "餐饮服务;中餐厅"},
        ]
        kept, actions = repair_forbidden_categories(route, forbidden=("cultural",), classify=classify, pool=POOL)
        self.assertEqual([item["name"] for item in kept], ["孔雀公园", "老四号拌面"])
        self.assertEqual(actions, [])

    def test_dropped_when_no_replacement_available(self):
        route = [{"day": 1, "name": "民俗文化博物馆", "type": "科教文化服务;博物馆;博物馆"}]
        kept, actions = repair_forbidden_categories(route, forbidden=("cultural",), classify=classify, pool=[])
        self.assertEqual(kept, [], "没有可替换候选时移出，而不是留着")
        self.assertEqual(actions[0]["code"], "forbidden_category_dropped")
        self.assertIn("没有可替换", actions[0]["reason"])

    def test_replacement_never_duplicates_an_existing_name(self):
        route = [
            {"day": 1, "name": "州博物馆", "type": "科教文化服务;博物馆;博物馆"},
            {"day": 2, "name": "博斯腾湖风景名胜区", "type": "风景名胜;风景名胜;国家级景点"},
        ]
        kept, _ = repair_forbidden_categories(route, forbidden=("cultural",), classify=classify, pool=POOL)
        names = [item["name"] for item in kept]
        self.assertEqual(len(names), len(set(names)), f"替换后不该重名：{names}")

    def test_empty_forbidden_list_is_a_noop(self):
        route = [{"day": 1, "name": "州博物馆", "type": "科教文化服务;博物馆;博物馆"}]
        kept, actions = repair_forbidden_categories(route, forbidden=(), classify=classify, pool=POOL)
        self.assertEqual([item["name"] for item in kept], ["州博物馆"])
        self.assertEqual(actions, [])

    def test_classifier_failure_does_not_crash(self):
        def broken(name, poi_type="", is_hotel=False):
            raise RuntimeError("分类器炸了")

        route = [{"day": 1, "name": "州博物馆"}]
        kept, actions = repair_forbidden_categories(route, forbidden=("cultural",), classify=broken, pool=[])
        self.assertEqual([item["name"] for item in kept], ["州博物馆"], "分类失败按'其它'处理，不该乱删")
        self.assertEqual(actions, [])


class ExclusionRepairDisclosureTests(unittest.TestCase):
    """按禁令动过什么，必须在「遗留问题」里说清楚（不许悄悄改）。"""

    def test_replacement_and_drop_are_both_disclosed(self):
        payload = {
            "exclusion_repair": {
                "forbidden": ["cultural"],
                "actions": [
                    {"code": "forbidden_category_replaced", "from": "巴州博物馆", "to": "博斯腾湖风景名胜区"},
                    {"code": "forbidden_category_dropped", "from": "民俗文化博物馆"},
                ],
            }
        }
        items = collect_degradations(payload).get("items", [])
        entry = next((item for item in items if "巴州博物馆" in str(item.get("reason"))), None)
        self.assertIsNotNone(entry, f"按禁令的修改没有披露：{items}")
        self.assertIn("博斯腾湖风景名胜区", str(entry.get("reason")))
        self.assertIn("民俗文化博物馆", str(entry.get("reason")))

    def test_nothing_registered_without_actions(self):
        items = collect_degradations({"exclusion_repair": {"forbidden": ["cultural"], "actions": []}}).get("items", [])
        self.assertFalse([item for item in items if "不要的类别" in str(item.get("reason"))])


if __name__ == "__main__":
    unittest.main()
