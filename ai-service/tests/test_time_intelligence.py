# -*- coding: utf-8 -*-
"""智能时间管理纯函数单元测试（开放时间感知 + 节奏判断）。"""
import sys
import os
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.time_intelligence import (
    detect_monday_closure_risk,
    analyze_pace,
    monday_closure_notes,
)


class TestMondayClosureAwareness(unittest.TestCase):
    def test_detects_museum(self):
        self.assertTrue(detect_monday_closure_risk("故宫博物院", "博物馆", ["文化"]))

    def test_detects_gallery(self):
        self.assertTrue(detect_monday_closure_risk("中国美术馆", "美术馆", []))

    def test_scenic_spot_not_closed(self):
        self.assertFalse(detect_monday_closure_risk("西湖", "风景", ["自然风光"]))

    def test_notes_mark_monday_closure_nodes(self):
        nodes = [
            {"name": "故宫博物院", "type": "博物馆", "day": 1, "tags": []},
            {"name": "八达岭长城", "type": "风景", "day": 1, "tags": []},
        ]
        notes = monday_closure_notes(nodes)
        self.assertEqual(len(notes), 1)
        self.assertIn("故宫博物院", notes[0])


class TestPaceAnalysis(unittest.TestCase):
    def test_rush_by_intent(self):
        r = analyze_pace(3, 12, "想特种兵暴走多打卡")
        self.assertEqual(r["pace"], "rush")

    def test_rush_by_density(self):
        r = analyze_pace(2, 14, "")
        self.assertEqual(r["pace"], "rush")

    def test_deep_by_intent(self):
        r = analyze_pace(3, 9, "悠闲度假深度游")
        self.assertEqual(r["pace"], "deep")

    def test_deep_by_low_density(self):
        r = analyze_pace(3, 6, "")
        self.assertEqual(r["pace"], "deep")

    def test_balanced_default(self):
        r = analyze_pace(3, 12, "")
        self.assertEqual(r["pace"], "balanced")

    def test_output_structure(self):
        r = analyze_pace(3, 12, "")
        for k in ("pace", "label", "suggestion", "avg_nodes_per_day"):
            self.assertIn(k, r)
        self.assertEqual(r["avg_nodes_per_day"], 4.0)


if __name__ == "__main__":
    unittest.main()