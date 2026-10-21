"""燃料 LP 模型：遍历所有可用燃料，支持混烧。

YAFC/FP 的局限：
  * 一次只能算一条既定路线（配方要人工选）
  * 燃料是外部输入项，不会自己去找「烧什么最省」
本模型把「采矿 → 加工 → 燃烧发电 → 对外输出」放进同一个 LP，
目标是最小化采煤量，让求解器自己决定烧哪些燃料、各烧多少。

红瓶阶段数据（来自 data-raw-dump）：
  raw-coal 3 MJ，coal 4 MJ，coke 5 MJ，coal-gas / tar 各 0.2 MJ
  distilled-raw-coal : 10 raw-coal -> 3 coal + 60 gas + 30 tar   电 1.0 MJ
  coal-gas           : 10 coal     -> 6 coke + 40 gas + 50 tar   电 1.5 MJ
  采矿               : 1 raw-coal                               电 0.25 MJ
  发电               : 1 MJ 电 = 2 MJ 燃料热值（steam-engine effectivity 0.5）
"""
from __future__ import annotations

from .lp import LP

COAL_MJ = 3.0
FUEL = {"raw-coal": 3.0, "coal": 4.0, "coke": 5.0, "coal-gas": 0.2, "tar": 0.2}
POWER_EFF = 0.5
MINE_E = 0.25


def build(allowed: set[str] | None, target_mj: float, extra_power: float = 0.0,
          min_dist1: float = 0.0):
    """allowed=None 表示所有燃料都能烧；否则只允许这些燃料用于发电。"""
    names = ["mine", "dist1", "dist2", "out_raw", "out_coal", "out_coke",
             "flare_gas", "flare_tar"]
    burn = {f: f"burn_{f}" for f in FUEL}
    names += list(burn.values())
    idx = {n: i for i, n in enumerate(names)}
    nv = len(names)

    lp = LP(nv)

    def burns(f):
        return idx[burn[f]] if (allowed is None or f in allowed) else None

    # 物料平衡
    # raw-coal:  1*mine = 10*dist1 + burn_raw + out_raw
    d = {idx["mine"]: 1.0, idx["dist1"]: -10.0, idx["out_raw"]: -1.0}
    if burns("raw-coal") is not None:
        d[burns("raw-coal")] = d.get(burns("raw-coal"), 0) - 1.0
    lp.eq(d, 0)

    # coal: 3*dist1 = 10*dist2 + burn_coal + out_coal
    d = {idx["dist1"]: 3.0, idx["dist2"]: -10.0, idx["out_coal"]: -1.0}
    if burns("coal") is not None:
        d[burns("coal")] = d.get(burns("coal"), 0) - 1.0
    lp.eq(d, 0)

    # coke: 6*dist2 = burn_coke + out_coke
    d = {idx["dist2"]: 6.0, idx["out_coke"]: -1.0}
    if burns("coke") is not None:
        d[burns("coke")] = d.get(burns("coke"), 0) - 1.0
    lp.eq(d, 0)

    # coal-gas: 60*dist1 + 40*dist2 = burn_gas + flare_gas（允许放空，否则副产物会假性堵死）
    d = {idx["dist1"]: 60.0, idx["dist2"]: 40.0, idx["flare_gas"]: -1.0}
    if burns("coal-gas") is not None:
        d[burns("coal-gas")] = d.get(burns("coal-gas"), 0) - 1.0
    lp.eq(d, 0)

    # tar: 30*dist1 + 50*dist2 = burn_tar + flare_tar
    d = {idx["dist1"]: 30.0, idx["dist2"]: 50.0, idx["flare_tar"]: -1.0}
    if burns("tar") is not None:
        d[burns("tar")] = d.get(burns("tar"), 0) - 1.0
    lp.eq(d, 0)

    # 电力平衡：需求 = 供给 × 效率
    demand = {idx["mine"]: MINE_E, idx["dist1"]: 1.0, idx["dist2"]: 1.5}
    supply = {}
    for f, j in burn.items():
        jj = burns(f)
        if jj is not None:
            supply[jj] = supply.get(jj, 0) - POWER_EFF * FUEL[f]
    for j, v in demand.items():
        supply[j] = supply.get(j, 0) + v

    # supply 的符号是「需求 - 发电量」，<= 0 表示发电 >= 需求（允许超发浪费）
    # 额外用电需求作为常数项放在右边
    lp.le(supply, -extra_power)

    # 对外输出热值
    lp.eq({idx["out_raw"]: FUEL["raw-coal"],
           idx["out_coal"]: FUEL["coal"],
           idx["out_coke"]: FUEL["coke"]}, target_mj)

    if min_dist1:
        lp.ge({idx["dist1"]: 1.0}, min_dist1)   # 化工需求：蒸馏线已经在跑

    lp.obj({idx["mine"]: 1.0})
    return lp, names, burn


def run(allowed: set[str] | None, target_mj: float, force_out: str | None = None,
        extra_power: float = 0.0, min_dist1: float = 0.0):
    """force_out 指定时必须只输出该燃料；None 表示求解器自由选择输出什么。

    注意：生产某燃料的伴生副产物（coal-gas / tar）始终允许烧掉，
    否则蒸馏路线会因为副产物堵死而假性不可行。
    """
    lp, names, burn = build(allowed, target_mj, extra_power, min_dist1)
    idx = {n: i for i, n in enumerate(names)}
    if force_out:
        for f, var in (("raw-coal", "out_raw"), ("coal", "out_coal"), ("coke", "out_coke")):
            if f != force_out:
                lp.eq({idx[var]: 1.0}, 0.0)
    status, x, obj = lp.solve()
    if status != "optimal":
        return {"status": status, "coal": None, "detail": {}}
    detail = {n: round(x[i], 4) for i, n in enumerate(names) if x[i] > 1e-6}
    return {"status": status, "coal": obj, "detail": detail}


def main() -> None:
    TARGET = 100.0
    print(f"目标：对外输出 {TARGET:.0f} MJ 可用热值/分钟（允许烧掉伴生副产物）\n")
    print(f'{"方案":26s} {"采煤 个/min":>12s} {"投入 MJ":>9s} {"EROI":>7s}')
    print("-" * 60)

    rows = []
    for f in ("raw-coal", "coal", "coke"):
        r = run(None, TARGET, force_out=f)
        if r["status"] != "optimal":
            rows.append((f"只输出 {f}", None, None, r["status"]))
        else:
            coal = r["coal"]
            rows.append((f"只输出 {f}", coal, coal * COAL_MJ, TARGET / (coal * COAL_MJ)))

    r = run(None, TARGET)
    coal = r["coal"]
    rows.append(("自由输出（最优）", coal, coal * COAL_MJ, TARGET / (coal * COAL_MJ)))

    rows.sort(key=lambda t: (t[1] is None, t[1] or 0))
    for name, coal, mj, eroi in rows:
        if coal is None:
            print(f"{name:26s} {eroi:>12s}")
        else:
            print(f"{name:26s} {coal:12.2f} {mj:9.1f} {eroi*100:6.1f}%")

    print("\n自由输出的解：")
    for k, v in sorted(r["detail"].items()):
        print(f"  {k:16s} {v:9.4f}")

    print("\n只输出 coke 的解（含副产物处理）：")
    r = run(None, TARGET, force_out="coke")
    for k, v in sorted(r["detail"].items()):
        print(f"  {k:16s} {v:9.4f}")


def power_scan():
    """固定不输出燃料，只供外部用电：看不同需求下最优烧什么。"""
    print("\n=== 纯供电场景：需求越大，混烧是否变化 ===")
    print(f'{"需求 MJ/min":>12s} {"采煤":>9s} {"EROI":>7s}  组合')
    for p in (30, 60, 120, 240, 480, 960):
        r = run(None, 0.0, extra_power=p)
        if r["status"] != "optimal":
            print(f"{p:12.0f} {'-':>9s} {'-':>7s}  {r['status']}")
            continue
        coal = r["coal"]
        mix = " + ".join(f"{k.replace('burn_','')}×{v:.1f}"
                         for k, v in r["detail"].items() if k.startswith("burn_"))
        print(f"{p:12.0f} {coal:9.2f} {p/(coal*COAL_MJ)*100:6.1f}%  {mix or '(无燃烧)'}")


def mixed_case():
    """化工线已经在跑，副产物必须消化 —— 这时混烧才有意义。"""
    print("\n=== 有蒸馏线时：副产物 + 燃料的混烧比例 ===")
    print(f'{"蒸馏批次":>8s} {"外部用电":>9s} {"采煤":>7s}  燃烧组合')
    for d1 in (0, 2, 5, 10, 20):
        for p in (60, 240):
            r = run(None, 0.0, extra_power=p, min_dist1=d1)
            if r["status"] != "optimal":
                print(f"{d1:8.0f} {p:9.0f}  {r['status']}")
                continue
            mix = " + ".join(f"{k.replace('burn_','')}×{v:.1f}"
                             for k, v in sorted(r["detail"].items()) if k.startswith("burn_"))
            flare = sum(v for k, v in r["detail"].items() if k.startswith("flare_"))
            print(f"{d1:8.0f} {p:9.0f} {r['coal']:7.1f}  {mix}"
                  + (f"  (放空 {flare:.0f})" if flare else ""))


if __name__ == "__main__":
    main()
    power_scan()
    mixed_case()
