"""从 `factorio --dump-data` 的 data-raw-dump.json 构建离线配方图索引。

用法:
    python -m analyze.graph build <data-raw-dump.json> <out_dir>
"""
from __future__ import annotations

import json
import os
import sys
from collections import defaultdict


def norm(entries) -> dict[str, float]:
    """把 ingredients / results 统一成 {name: amount}。

    支持三种形态:
      [{type,name,amount}, ...]      -> dump 的常见形态
      {name: amount, ...}            -> 简写
      {name: {type,amount}, ...}     -> 对象简写
    """
    out: dict[str, float] = {}
    if not entries:
        return out
    if isinstance(entries, dict):
        for k, v in entries.items():
            if isinstance(v, (int, float)):
                out[k] = out.get(k, 0.0) + float(v)
            elif isinstance(v, dict):
                out[k] = out.get(k, 0.0) + float(v.get("amount", 1))
        return out
    for e in entries:
        name = e.get("name")
        if not name:
            continue
        out[name] = out.get(name, 0.0) + float(e.get("amount", 1))
    return out


def build(dump_path: str, out_dir: str) -> None:
    os.makedirs(out_dir, exist_ok=True)
    with open(dump_path, "r", encoding="utf-8") as f:
        d = json.load(f)

    recipes: dict[str, dict] = {}
    producers: dict[str, list[str]] = defaultdict(list)
    consumers: dict[str, list[str]] = defaultdict(list)

    for name, r in d.get("recipe", {}).items():
        ing = norm(r.get("ingredients"))
        res = norm(r.get("results"))
        if not res and not ing:
            continue
        rec = {
            "n": name,
            "ing": ing,
            "res": res,
            "t": float(r.get("energy_required") or 0.5),
            "cat": r.get("category", "crafting"),
            "prod": bool(r.get("allow_productivity")),
            "hid": bool(r.get("hidden")),
        }
        if r.get("main_product"):
            rec["main"] = r["main_product"]
        recipes[name] = rec
        for it in res:
            producers[it].append(name)
        for it in ing:
            consumers[it].append(name)

    items: dict[str, dict] = {}
    for kind in ("item", "fluid", "tool", "module", "ammo", "capsule", "armor", "gun"):
        for name, p in d.get(kind, {}).items():
            items[name] = {
                "t": kind,
                "sg": p.get("subgroup"),
                "ord": p.get("order"),
                "hid": bool(p.get("hidden")),
            }
    # 资源（矿脉）
    for name, p in d.get("resource", {}).items():
        for res in (p.get("minable") or {}).get("results", []) or []:
            rn = res.get("name")
            if rn:
                items.setdefault(rn, {"t": "item", "sg": p.get("subgroup"), "ord": None, "hid": False})
                items[rn]["resource"] = name

    techs: dict[str, dict] = {}
    for name, t in d.get("technology", {}).items():
        u = t.get("unit") or {}
        techs[name] = {
            "count": u.get("count", 0),
            "time": u.get("time", 1),
            "ing": dict(u.get("ingredients") or []),
            "prereq": list(t.get("prerequisites") or []),
            "unlocks": [e.get("recipe") for e in (t.get("effects") or []) if e.get("type") == "unlock-recipe"],
            "ess": bool(t.get("essential")),
        }

    def dump(obj, fn):
        with open(os.path.join(out_dir, fn), "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))

    dump(recipes, "recipes.json")
    dump(dict(producers), "producers.json")
    dump(dict(consumers), "consumers.json")
    dump(items, "items.json")
    dump(techs, "techs.json")

    print(f"recipes   {len(recipes)}")
    print(f"items     {len(items)}")
    print(f"techs     {len(techs)}")
    print(f"produced  {len(producers)} 种可生产物品")
    print(f"-> {out_dir}")


if __name__ == "__main__":
    if len(sys.argv) < 4 or sys.argv[1] != "build":
        print(__doc__)
        sys.exit(1)
    build(sys.argv[2], sys.argv[3])
