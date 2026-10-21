"""红瓶阶段燃料路线：闭环净能量分析。

与「只算物品热值」的区别：
  1. 采矿要电 -> 电要烧煤 -> 又要把煤采出来（循环自耗）
  2. 加工机器要电（FP 的 energy_total 公式）
  3. 燃料在 burner 机器里还要除 effectivity

发电链：boiler(burner, eff=1.0) -> steam -> steam-engine(effectivity=0.5)
所以 煤热值 -> 电 的综合效率取 0.5。
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


def main() -> None:
    rows = []

    # A 直接烧 raw-coal：采 1 个烧 1 个
    rows.append(row("raw-coal 直接烧", 1.0, 0.0, COAL_MJ, "无加工"))

    # B raw-coal -> coal（distilled-raw-coal: 10 raw -> 3 coal + 60 gas + 30 tar, 2s@500kW）
    out_b = 3 * COAL_SOLID_MJ + 60 * GAS_MJ + 30 * GAS_MJ
    rows.append(row("raw-coal → coal", 10.0, 1.0, out_b, "蒸馏 2s，电 1MJ"))

    # C coal -> coke（coal-gas: 10 coal -> 6 coke + 40 gas + 50 tar, 3s@500kW）
    #    10 coal 需要 10/3 批 B
    batches_b = 10.0 / 3.0
    raw_for_c = batches_b * 10.0
    elec_c = batches_b * 1.0 + 1.5
    out_side_b = batches_b * (60 + 30) * GAS_MJ          # B 的副产物（coal 已进 C）
    out_c = 6 * COKE_MJ + (40 + 50) * GAS_MJ
    rows.append(row("coal → coke（含 B 副产）", raw_for_c, elec_c, out_c + out_side_b,
                    "两段蒸馏，副产物全计入"))

    # D 只烧 coke，副产物丢弃
    rows.append(row("coal → coke（弃副产）", raw_for_c, elec_c, 6 * COKE_MJ,
                    "煤气/焦油不烧，只看焦炭"))

    # E wood -> raw-fiber（fiber-01: wood*10 -> raw-fiber*2, 10s@wpu 500kW）
    #    wood 2MJ，视为外部输入（不追到伐木）
    wood_in = 10 * 2.0
    rows.append(row("wood → raw-fiber", 0.0, 5.0, 12.0, "投入 20MJ wood 未计入煤"))
    rows[-1]["input_mj"] = wood_in + 5.0 / POWER_EFF  # 电折算
    rows[-1]["eroi"] = rows[-1]["out_mj"] / rows[-1]["input_mj"]

    rows.sort(key=lambda r: -r["eroi"])

    print(f'{"路线":26s} {"产出MJ":>8s} {"采煤个":>8s} {"发电用煤":>9s} {"投入MJ":>9s} {"EROI":>7s}')
    print("-" * 76)
    for r in rows:
        print(f'{r["route"]:26s} {r["out_mj"]:8.1f} {r["coal_mined"]:8.2f} '
              f'{r["coal_for_power"]:9.2f} {r["input_mj"]:9.1f} {r["eroi"]*100:6.1f}%')
    print()
    for r in rows:
        print(f'  {r["route"]:26s}  {r["note"]}')




# ---------------- 农业线（fwf 林业） ----------------
# fwf-mk01: speed 0.0909 / 450kW；botanical-nursery: speed 1 / 125kW
# 关键：fwf 升级是负优化 —— mk02 speed +7% 但功率 +344%
FWF = {"fwf-mk01": (0.0909, 450.0), "fwf-mk02": (0.0976, 2000.0),
       "fwf-mk03": (0.0989, 3000.0), "fwf-mk04": (0.0994, 4000.0)}
NURSERY = (1.0, 125.0)
LOG_MJ = 4.0
NURSERY_CRAFT_S = 20.0


def nursery_mj_per_batch() -> float:
    speed, kw = NURSERY
    return kw / 1000.0 * (NURSERY_CRAFT_S / speed)


def agri_rows():
    seed_mj = nursery_mj_per_batch()      # 每批 3 个 seedling
    out = []
    recipes = [("log1", 80, 3), ("log2", 60, 4), ("log3", 40, 6),
               ("log5", 20, 7), ("log7", 25, 9), ("log8", 20, 10)]
    for machine, (speed, kw) in FWF.items():
        for name, t, n in recipes:
            elec = kw / 1000.0 * (t / speed) + seed_mj
            out_mj = n * LOG_MJ
            out.append({
                "machine": machine, "recipe": name,
                "out_mj": out_mj, "elec_mj": elec,
                "x": elec / out_mj,
                "coal_mult": elec / POWER_EFF / out_mj,
                "eroi": out_mj / (elec / POWER_EFF),
            })
    return out


def print_agri():
    print()
    print("=== 农业线（含幼苗培育电） ===")
    print(f'{"机器":10s} {"配方":7s} {"产出MJ":>8s} {"电耗MJ":>9s} {"折煤倍数":>9s} {"EROI":>7s}')
    for r in agri_rows():
        print(f'{r["machine"]:10s} {r["recipe"]:7s} {r["out_mj"]:8.1f} '
              f'{r["elec_mj"]:9.1f} {r["coal_mult"]:8.1f}x {r["eroi"]*100:6.2f}%')




# ---------------- 模块方案（满插件） ----------------
# Factorio 模块效果为加法叠加；speed 下限 -80%，consumption 下限 -80%。
# 红瓶阶段造不出 productivity/speed/efficiency-module（都要 py-science-pack-2 + logistic），
# 唯一能用的是生物模块 sap-tree / vrauks：speed +1.0、pollution +1.0，**没有功耗惩罚**。
MODULE_SETS = {
    "空":                {"speed": 0.0, "consumption": 0.0, "productivity": 0.0},
    "10× sap-tree":      {"speed": 1.0, "consumption": 0.0, "productivity": 0.0},
    "10× speed-module":  {"speed": 0.4, "consumption": 1.0, "productivity": 0.0},
    "10× speed-mk3":     {"speed": 1.0, "consumption": 1.5, "productivity": 0.0},
    "10× prod-mk3":      {"speed": -0.75, "consumption": 2.0, "productivity": 0.3},
    "10× eff-mk3":       {"speed": 0.0, "consumption": -0.5, "productivity": 0.0},
}
BASE_SPEED, BASE_KW = 0.0909, 450.0
LOG1_TIME, LOG1_OUT = 80.0, 12.0


def module_rows(slots: int = 10):
    out = []
    seed = nursery_mj_per_batch()
    for name, e in MODULE_SETS.items():
        spd = max(0.2, 1 + e["speed"] * slots)
        cons = max(0.2, 1 + e["consumption"] * slots)
        prod = 1 + e["productivity"] * slots
        real_t = LOG1_TIME / (BASE_SPEED * spd)
        elec = BASE_KW / 1000 * cons * real_t + seed
        produced = LOG1_OUT * prod
        out.append({"set": name, "speed": spd, "cons": cons, "prod": prod,
                    "elec": elec, "out": produced,
                    "coal": elec / POWER_EFF / produced,
                    "eroi": produced / (elec / POWER_EFF)})
    return out


def print_modules():
    print()
    print("=== 林业模块方案（log1，10 槽） ===")
    print(f'{"方案":20s} {"速度×":>6s} {"功耗×":>6s} {"产能×":>6s} {"电耗MJ":>9s} {"折煤":>8s} {"EROI":>7s}')
    for r in module_rows():
        print(f'{r["set"]:20s} {r["speed"]:6.1f} {r["cons"]:6.1f} {r["prod"]:6.1f} '
              f'{r["elec"]:9.1f} {r["coal"]:7.1f}x {r["eroi"]*100:6.2f}%')


if __name__ == "__main__":
    main()
    print_agri()
    print_modules()
