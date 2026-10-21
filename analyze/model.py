"""快照模型：把 py-stats.json 包装成便于分析的对象。"""
from __future__ import annotations

import json
import os

WINDOWS = ("m1", "m10", "h1", "h10", "h50")

CATEGORY_LABEL = {
    "item": "物品",
    "fluid": "流体",
}


class Graph:
    """离线配方图（由 analyze.graph 生成）。"""

    def __init__(self, data_dir: str):
        def load(fn):
            p = os.path.join(data_dir, fn)
            with open(p, "r", encoding="utf-8") as f:
                return json.load(f)

        self.recipes: dict[str, dict] = load("recipes.json")
        self.producers: dict[str, list[str]] = load("producers.json")
        self.consumers: dict[str, list[str]] = load("consumers.json")
        self.items: dict[str, dict] = load("items.json")
        self.techs: dict[str, dict] = load("techs.json")

    def is_fluid(self, name: str) -> bool:
        it = self.items.get(name)
        return bool(it and it.get("t") == "fluid")

    def best_recipe(self, item: str) -> str | None:
        """挑一个"主"配方：优先非 hidden、主产物、时间与产量在合理范围。

        排除物流/请求类伪配方（request-*、drone、teleport），
        它们的 energy_required 和产出量都不是常规量级。
        """
        cands = self.producers.get(item) or []
        if not cands:
            return None

        def usable(rn: str) -> bool:
            r = self.recipes[rn]
            low = rn.lower()
            if any(k in low for k in ("request-", "-drone", "teleport", "sandbox")):
                return False
            if r["t"] <= 0 or r["t"] > 600:
                return False
            if (r["res"].get(item) or 0) > 5000:
                return False
            return True

        pool = [rn for rn in cands if usable(rn)]
        if not pool:
            return None

        def score(rn):
            r = self.recipes[rn]
            s = 0.0
            if r["n"] == item and r["n"] in self.recipes:
                s += 2
            if not r["hid"]:
                s += 2
            if len(r["res"]) == 1:
                s += 1.5
            if r.get("main") == item:
                s += 2
            if not r["ing"]:
                s -= 3          # 无原料的多半是调试/特殊配方
            return -s

        return sorted(pool, key=score)[0]


class Snapshot:
    """一帧游戏状态。"""

    def __init__(self, raw: dict, graph: Graph | None = None):
        self.raw = raw
        self.graph = graph
        self.tick = raw.get("tick", 0)
        self.items: dict[str, dict] = raw.get("item_flow") or {}
        self.fluids: dict[str, dict] = raw.get("fluid_flow") or {}
        self.power = raw.get("power") or {}
        self.research = raw.get("research") or {}
        self.techs = raw.get("techs") or {}
        self.entity_build = raw.get("entity_build") or {}
        self.surfaces = raw.get("surfaces") or []

    @classmethod
    def load(cls, path: str, graph: Graph | None = None) -> "Snapshot":
        with open(path, "r", encoding="utf-8") as f:
            return cls(json.load(f), graph)

    # ---------- 基础读取 ----------
    # 快照里 o/i/s 每个键含 rate(每分钟,由累计÷游戏时长算出) 与 m1/m10/h1/h10/h50(相对窗口值)
    def _rec(self, name: str) -> dict | None:
        return self.items.get(name) or self.fluids.get(name)

    def out_rate(self, name: str, win: str = "h10") -> float:
        r = self._rec(name)
        if not r:
            return 0.0
        o = r.get("o") or {}
        return o["rate"] if "rate" in o else o.get(win, 0.0)

    def in_rate(self, name: str, win: str = "h10") -> float:
        r = self._rec(name)
        if not r:
            return 0.0
        i = r.get("i") or {}
        return i["rate"] if "rate" in i else i.get(win, 0.0)

    def stock(self, name: str, win: str = "m1") -> float:
        """引擎只提供「净累积」而非真实库存，这里返回净累积量。"""
        r = self._rec(name)
        if not r:
            return 0.0
        s = r.get("s") or {}
        if "total" in s:
            return s["total"]
        return s.get(win, 0.0)

    def net(self, name: str, win: str = "h10") -> float:
        return self.out_rate(name, win) - self.in_rate(name, win)

    def stock_slope(self, name: str) -> float:
        """趋势：用窗口相对值比较（单位相同，只比大小）。"""
        r = self._rec(name)
        if not r:
            return 0.0
        s = r.get("s") or {}
        a, b = s.get("m1"), s.get("h10")
        if a is None or b is None:
            return 0.0
        return a - b

    def cover_minutes(self, name: str) -> float:
        d = self.in_rate(name)
        if d <= 1e-6:
            return float("inf")
        s = self.stock(name)
        return s / d if s > 0 else 0.0

    def supply_ratio(self, name: str, win: str = "h10") -> float:
        """产出 / 消耗。>1 表示有盈余，<1 表示在吃库存。"""
        d = self.in_rate(name, win)
        if d <= 1e-6:
            return float("inf")
        return self.out_rate(name, win) / d

    def label(self, name: str) -> str:
        it = (self.graph.items.get(name) if self.graph else None) or {}
        kind = it.get("t", "item")
        return f"{name} ({CATEGORY_LABEL.get(kind, kind)})"

    def tracked_names(self) -> list[str]:
        return list(self.items.keys()) + list(self.fluids.keys())

    # ---------- 电力 ----------
    @property
    def power_total(self) -> dict:
        return self.power.get("totals") or {}

    def power_headroom(self) -> float:
        t = self.power_total
        p = t.get("produced", 0.0)
        c = t.get("consumed", 0.0)
        if p <= 1e-6:
            return 0.0
        return (p - c) / p

    # ---------- 科研 ----------
    def research_throughput(self) -> dict:
        """当前研究的 "单位/分钟" 吞吐，按最紧缺的科研包决定。"""
        r = self.research
        ing = r.get("ingredients") or {}
        if not ing:
            return {"pack": None, "units_per_min": 0.0, "bottleneck": None, "packs": {}}
        packs = {}
        worst = None
        for pack, amount in ing.items():
            rate = self.out_rate(pack, "h10")
            per_unit = rate / amount if amount else 0.0
            packs[pack] = {"need": amount, "rate_per_min": rate, "units_per_min": per_unit}
            if worst is None or per_unit < worst[1]:
                worst = (pack, per_unit)
        return {
            "pack": worst[0] if worst else None,
            "units_per_min": worst[1] if worst else 0.0,
            "bottleneck": worst[0] if worst else None,
            "packs": packs,
        }
