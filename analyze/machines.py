"""从 data-raw-dump.json 提取机器产能表（crafting_speed / categories）。"""
from __future__ import annotations

import json
import sys
from collections import defaultdict

KINDS = ("assembling-machine", "furnace", "rocket-silo", "mining-drill")


def build(dump_path: str, out_path: str) -> None:
    with open(dump_path, "r", encoding="utf-8") as f:
        d = json.load(f)

    machines = {}
    by_cat: dict[str, list[str]] = defaultdict(list)
    for kind in KINDS:
        for name, p in d.get(kind, {}).items():
            speed = p.get("crafting_speed")
            if speed is None:
                continue
            cats = p.get("crafting_categories") or []
            machines[name] = {"kind": kind, "speed": float(speed), "cats": list(cats)}
            for c in cats:
                by_cat[c].append(name)

    # 每个类别取速度的 25 分位 / 中位数：玩家通常用早期机器，用低分位更贴近现实
    typical = {}
    typical_p50 = {}
    for c, names in by_cat.items():
        speeds = sorted(machines[n]["speed"] for n in names)
        typical[c] = speeds[max(0, len(speeds) // 4)]
        typical_p50[c] = speeds[len(speeds) // 2]

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({"machines": machines, "by_category": by_cat,
                   "typical_speed": typical, "median_speed": typical_p50}, f,
                  ensure_ascii=False, separators=(",", ":"))
    print(f"machines {len(machines)}, categories {len(by_cat)}")
    print("typical:", {k: round(v, 2) for k, v in sorted(typical.items(), key=lambda x: -len(by_cat[x[0]]))[:8]})


if __name__ == "__main__":
    build(sys.argv[1], sys.argv[2])
