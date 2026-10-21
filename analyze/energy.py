"""红瓶阶段燃料路线：闭环净能量分析。

与「只算物品热值」的区别：
  1. 采矿要电 -> 电要烧煤 -> 又要把煤采出来（循环自耗）
  2. 加工机器要电（FP 的 energy_total 公式）
  3. 燃料在 burner 机器里还要除 effectivity

发电链：boiler(burner, eff=1.0) -> steam -> steam-engine(effectivity=0.5)
所以 煤热值 -> 电 的综合效率取 0.5。

**流体燃料不在发电链上**：coal-gas / tar 的 fuel_category 是 None，
而 boiler 只收 ["chemical","biomass"]。发电型流体设备
（oil-boiler-mk01 / py-oil-powerplant / py-burner）全部要 py-science-pack-1，
红瓶阶段拿不到。所以本阶段 gas/tar 只能放空或送工艺炉
（glassworks-mk01 / smelter-mk01，effectivity 1.0，受台数限制）。
"""
from __future__ import annotations

import json
import re

DUMP = r"C:/Users/Administrator/AppData/Roaming/Factorio/script-output/data-raw-dump.json"

COAL_MJ = 3.0              # raw-coal 热值
COAL_SOLID_MJ = 4.0        # coal
COKE_MJ = 5.0
GAS_MJ = 0.2               # coal-gas / tar 每单位
MINE_MJ_PER_COAL = 0.25    # electric-mining-drill: 1MW, mining_time 0.5 / speed 2
POWER_EFF = 0.5            # 煤 -> 电


def solve(mine_coal: float, proc_mj: float, out_mj: float):
    """求含发电自耗的总采煤量。"""
    P = 0.0
    for _ in range(500):
        N = mine_coal + P
        elec = N * MINE_MJ_PER_COAL + proc_mj
        newP = elec / (POWER_EFF * COAL_MJ)
        if abs(newP - P) < 1e-12:
            break
        P = newP
    total = N
    return total, P, total * COAL_MJ


def row(name, mine_coal, proc_mj, out_mj, note):
    total, P, in_mj = solve(mine_coal, proc_mj, out_mj)
    eroi = out_mj / in_mj
    return {
        "route": name, "note": note,
        "out_mj": out_mj, "coal_mined": total, "coal_for_power": P,
        "input_mj": in_mj, "eroi": eroi,
    }


def build_rows(fluid_counts: bool):
    """fluid_counts=False：红瓶阶段。

    coal-gas / tar 的 fuel_category 是 None，而 boiler 只收
    ["chemical","biomass"]，所以流体燃料接不进锅炉，无法发电。
    发电型流体设备（oil-boiler-mk01 / py-oil-powerplant / py-burner）
    全部需要 py-science-pack-1，红瓶阶段都拿不到。
    因此本阶段 gas/tar 只能放空 —— 不计入产出。

    唯一例外是工艺炉（glassworks-mk01 / smelter-mk01，各 10MW，
    红瓶可解锁）：流体按 effectivity 1.0 顶替机器自身的用电，
    相当于 1 MJ 流体 = 2 MJ 固体燃料。这一步受台数限制，另行讨论。

    fluid_counts=True：py1 之后，流体可发电，按热值计入。
    """
    rows = []

    # A 直接烧 raw-coal
    rows.append(row("raw-coal 直接烧", 1.0, 0.0, COAL_MJ, "无加工，锅炉直烧"))

    # B distilled-raw-coal: 10 raw -> 3 coal + 60 gas + 30 tar (2s, 电 1MJ)
    gas_b = (60 + 30) * GAS_MJ if fluid_counts else 0.0
    rows.append(row("raw-coal → coal", 10.0, 1.0, 3 * COAL_SOLID_MJ + gas_b,
                    "蒸馏 2s，电 1MJ" + ("" if fluid_counts else "，副产放空")))

    # C coal-gas: 10 coal -> 6 coke + 40 gas + 50 tar (3s, 电 1.5MJ)
    batches_b = 10.0 / 3.0            # 10 coal 需要几批 B
    raw_for_c = batches_b * 10.0
    elec_c = batches_b * 1.0 + 1.5
    gas_c = (40 + 50) * GAS_MJ if fluid_counts else 0.0
    gas_b_side = batches_b * (60 + 30) * GAS_MJ if fluid_counts else 0.0
    rows.append(row("coal → coke（含 B 副产）", raw_for_c, elec_c,
                    6 * COKE_MJ + gas_c + gas_b_side,
                    "两段蒸馏，副产物" + ("计入" if fluid_counts else "放空")))
    rows.append(row("coal → coke（弃副产）", raw_for_c, elec_c, 6 * COKE_MJ,
                    "煤气/焦油不烧，只看焦炭"))

    # E wood -> raw-fiber（外部输入 wood 20MJ）
    r = row("wood → raw-fiber", 0.0, 5.0, 12.0, "投入 20MJ wood 未计入煤")
    r["input_mj"] = 20.0 + 5.0 / POWER_EFF
    r["eroi"] = r["out_mj"] / r["input_mj"]
    rows.append(r)
    return rows


def main() -> None:
    for label, fluid in (("红瓶阶段（流体不可发电，gas/tar 放空）",
                          False),
                         ("假设 py1 后流体可发电（对照）", True)):
        rows = sorted(build_rows(fluid), key=lambda r: -r["eroi"])
        print(f"\n=== {label} ===")
        print(f'{"路线":26s} {"产出MJ":>8s} {"采煤个":>8s} {"发电用煤":>9s} {"投入MJ":>9s} {"EROI":>7s}')
        print("-" * 76)
        for r in rows:
            print(f'{r["route"]:26s} {r["out_mj"]:8.1f} {r["coal_mined"]:8.2f} '
                  f'{r["coal_for_power"]:9.2f} {r["input_mj"]:9.1f} {r["eroi"]*100:6.1f}%')
        print()
        for r in rows:
            print(f'  {r["route"]:26s}  {r["note"]}')


if __name__ == "__main__":
    main()
