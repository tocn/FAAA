"""生成一份"有内容"的演示快照，用于验证评分逻辑（不依赖真实存档状态）。

    python -m tools.make_demo <真实 py-stats.json> <输出 demo-stats.json>
"""
from __future__ import annotations

import json
import sys


def flow(out_h10, in_h10, stock, out_m1=None, in_m1=None, stock_m1=None):
    o = {"m1": out_m1 if out_m1 is not None else out_h10,
         "m10": out_h10, "h1": out_h10, "h10": out_h10, "h50": out_h10}
    i = {"m1": in_m1 if in_m1 is not None else in_h10,
         "m10": in_h10, "h1": in_h10, "h10": in_h10, "h50": in_h10}
    s = {"m1": stock_m1 if stock_m1 is not None else stock,
         "m10": stock, "h1": stock, "h10": stock, "h50": stock * 2.5}
    return {"o": o, "i": i, "s": s}


def main(src: str, dst: str) -> None:
    d = json.load(open(src, encoding="utf-8"))

    # ---- 电力：200 MW 装机，220 MW 负载 ----
    d["power"] = {
        "networks": [
            {"id": 1, "surface": "nauvis", "produced": 9.6e9, "consumed": 10.9e9,
             "stored": 48_000_000, "satisfaction": 0.8807},
            {"id": 2, "surface": "nauvis", "produced": 2.4e9, "consumed": 2.4e9,
             "stored": 8_000_000, "satisfaction": 1.0},
        ],
        "totals": {"produced": 12.0e9, "consumed": 13.3e9,
                   "stored": 5.6e7, "satisfaction": 0.9023},
    }

    # ---- 在研科技 ----
    d["research"] = {
        "current": "py-storage-tanks",
        "progress": 0.31,
        "unit_count": 70,
        "unit_energy": 30,
        "ingredients": {"automation-science-pack": 1},
    }
    d["techs"]["researched"] = 46

    # ---- 物品流 ----
    d["item_flow"] = {
        "automation-science-pack": flow(42, 78, 180, stock_m1=120),
        "copper-plate": flow(612, 640, 5_400, stock_m1=4_100),
        "iron-plate": flow(980, 910, 12_000, stock_m1=13_500),
        "steel-plate": flow(74, 96, 1_100, stock_m1=820),
        "iron-gear-wheel": flow(210, 205, 2_600),
        "pipe": flow(96, 92, 900),
        "electronic-circuit": flow(280, 322, 3_900, stock_m1=3_100),
        "engine-unit": flow(61, 128, 240, stock_m1=150),
        "coal": flow(1_650, 1_620, 42_000),
        "stone": flow(340, 300, 18_000),
        "wood": flow(120, 260, 900, stock_m1=600),
        "py-science-pack-1": flow(0, 0, 0),
    }
    d["fluid_flow"] = {
        "lubricant": flow(180, 176, 2_400),
        "steam": flow(24_000, 23_800, 0),
        "sulfuric-acid": flow(120, 210, 1_800, stock_m1=900),
        "water": flow(60_000, 59_000, 0),
    }

    json.dump(d, open(dst, "w", encoding="utf-8"), ensure_ascii=False)
    print("wrote", dst)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
