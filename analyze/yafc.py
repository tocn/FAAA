"""把候选任务转成 YAFC 生产表（.yafc 工程）。

格式依据 D:/gametools/YAFC/py01（YAFC CE 2.20.0 实测样例）：
  页面 content = { expanded, links[], recipes[], modules{} }
  links 元素   = { goods: "!Item.<name>!normal", amount: <每秒>, algorithm: 0 }
  recipes 元素 = 见 RECIPE_TEMPLATE，recipe 引用写 "!Recipe.<name>!normal"

注意：amount 的单位是「每秒」（YAFC 内部统一），所以 60/min 要写成 1。

    python -m analyze.yafc --candidates out/x/candidates.json --out out/x/plan.yafc
"""
from __future__ import annotations

import argparse
import json
import os
import uuid

from .model import Graph

MODULES_BLOCK = {
    "fillMiners": False,
    "autoFillPayback": 0,
    "fillerModule": None,
    "beacon": None,
    "beaconModule": None,
    "beaconsPerBuilding": 8,
    "overrideCrafterBeacons": {},
}

SETTINGS_BLOCK = {
    "milestones": [],
    "itemFlags": {},
    "miningProductivity": 0,
    "researchSpeedBonus": 0,
    "researchProductivity": 0,
    "productivityTechnologyLevels": {},
    "reactorSizeX": 2,
    "reactorSizeY": 2,
    "PollutionCostModifier": 0,
    "spoilingRate": 1,
}

PREFERENCES_BLOCK = {
    "time": 1,
    "itemUnit": 0,
    "fluidUnit": 0,
    "defaultBelt": None,
    "defaultInserter": None,
    "inserterCapacity": 1,
    "sourceResources": [],
    "favorites": [],
    "targetTechnology": None,
    "iconScale": 0.9,
    "maxMilestonesPerTooltipLine": 28,
    "showMilestoneOnInaccessible": True,
}


def recipe_ref(name: str) -> str:
    return f"!Recipe.{name}!normal"


def goods_ref(name: str, is_fluid: bool) -> str:
    return f"!Fluid.{name}!normal" if is_fluid else f"!Item.{name}!normal"


def entity_ref(name: str) -> str:
    return f"!Entity.{name}!normal"


def make_row(recipe: str, entity: str | None) -> dict:
    return {
        "recipe": recipe_ref(recipe),
        "icon": None,
        "description": None,
        "entity": entity_ref(entity) if entity else None,
        "fuel": None,
        "fixedBuildings": 0,
        "fixedFuel": False,
        "fixedIngredient": None,
        "fixedProduct": None,
        "builtBuildings": None,
        "showTotalIO": False,
        "enabled": True,
        "tag": 0,
        "modules": None,
        "subgroup": None,
        "variants": [],
    }


def pick_entity(graph: Graph, machines: dict | None, recipe: str) -> str | None:
    if not machines:
        return None
    rec = graph.recipes.get(recipe)
    if not rec:
        return None
    cat = rec["cat"]
    names = machines.get("by_category", {}).get(cat) or []
    if not names:
        return None
    # 取速度中位数：最高速往往是后期机器，最低速是手搓，中间最贴近实际产线
    speeds = machines.get("machines", {})
    names = sorted(names, key=lambda n: (speeds.get(n, {}).get("speed") or 0))
    return names[len(names) // 2]


def build(candidates_path: str, out_path: str, data_dir: str, title: str = "FAAA 建议") -> int:
    graph = Graph(data_dir)
    machines = None
    mp = os.path.join(data_dir, "machines.json")
    if os.path.exists(mp):
        with open(mp, "r", encoding="utf-8") as f:
            machines = json.load(f)

    doc = json.load(open(candidates_path, encoding="utf-8"))
    cands = doc.get("candidates") or []

    rows, links, used = [], [], []
    for c in cands:
        target = c.get("target")
        rn = c.get("recipe")
        if not target or not rn or rn not in graph.recipes:
            continue
        rate_per_min = float(c.get("delta_per_min") or 0)
        if rate_per_min <= 0:
            continue
        is_fluid = graph.is_fluid(target)
        rows.append(make_row(rn, pick_entity(graph, machines, rn)))
        links.append({
            "goods": goods_ref(target, is_fluid),
            "amount": round(rate_per_min / 60.0, 6),   # 每秒
            "algorithm": 0,
        })
        used.append((c.get("id"), target, rn, rate_per_min))

    if not rows:
        print("[yafc] 没有可写进生产表的候选（需要 target + recipe + delta_per_min > 0）")
        return 2

    guid = uuid.uuid4().hex
    project = {
        "settings": SETTINGS_BLOCK,
        "preferences": PREFERENCES_BLOCK,
        "sharedModuleTemplates": [],
        "yafcVersion": "2.20.0.0",
        "pages": [{
            "contentType": "Yafc.Model.ProductionTable",
            "guid": guid,
            "icon": None,
            "name": title,
            "content": {
                "expanded": True,
                "links": links,
                "recipes": rows,
                "modules": MODULES_BLOCK,
            },
            "scroll": 0,
        }],
        "displayPages": [guid],
    }

    os.makedirs(os.path.dirname(os.path.abspath(out_path)) or ".", exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(project, f, ensure_ascii=False, indent=2)

    print(f"[yafc] {len(rows)} 行 → {out_path}")
    for cid, target, rn, rate in used:
        print(f"       {cid}  {target:32s} {rate:8.1f}/min  ← {rn}")
    print("[yafc] amount 已换算成每秒（YAFC 内部单位）")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="py-analyzer yafc")
    ap.add_argument("--candidates", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--data", default="data")
    ap.add_argument("--title", default="FAAA 建议")
    args = ap.parse_args(argv)
    return build(args.candidates, args.out, args.data, args.title)


if __name__ == "__main__":
    raise SystemExit(main())
