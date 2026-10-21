"""燃料 LP 模型：遍历所有可用燃料，支持混烧。

YAFC/FP 的局限：
  * 一次只能算一条既定路线（配方要人工选）
  * 燃料是外部输入项，不会自己去找「烧什么最省」
本模型把「采矿 → 加工 → 燃烧发电 → 对外输出」放进同一个 LP，
目标是最小化采煤量，让求解器自己决定烧哪些燃料、各烧多少。

## 燃料通道（关键约束）

能量载体的**物理形态决定它能不能用来发电**，这一点在 Py 里是硬约束：

| 通道 | 载体 | 设备 | 效率 | 红瓶可用 |
|---|---|---|---|---|
| 电力-固体 | fuel_category ∈ {chemical, biomass} | boiler(1.0) → steam-engine(0.5) | 0.5 | ✅ |
| 电力-流体 | fuel_category = **None** | `oil-boiler-mk01`(effectivity 2) | — | ❌ 需 py1 |
| 工艺-流体 | 同上 | `smelter-mk01` / `glassworks-mk01`(type=fluid, 10MW) | 1.0 | ✅ 有容量上限 |

`coal-gas` / `tar` 的 `fuel_category` 是 `None`，而 `boiler` 只收
`["chemical","biomass"]` —— 所以**流体燃料接不进锅炉**，红瓶阶段无法发电。
发电型流体设备 `py-oil-powerplant-mk01` / `oil-boiler-mk01` / `py-burner`
全部要 `py-science-pack-1`（紫瓶），红瓶阶段一个都拿不到。

红瓶阶段唯一能烧流体的是工艺炉（`glass` 与 `alloys-mk01` 只需红瓶解锁），
但它消耗流体是拿来代替**机器自身的用电**，不是发电，而且受你实际有多少台限制。

## 数据（来自 data-raw-dump）

  raw-coal 3 MJ，coal 4 MJ，coke 5 MJ，coal-gas / tar 各 0.2 MJ
  distilled-raw-coal : 10 raw-coal -> 3 coal + 60 gas + 30 tar   电 1.0 MJ
  coal-gas           : 10 coal     -> 6 coke + 40 gas + 50 tar   电 1.5 MJ
  采矿               : 1 raw-coal                               电 0.25 MJ
  发电               : 1 MJ 电 = 2 MJ 固体燃料热值（steam-engine 0.5）
"""
from __future__ import annotations

from .lp import LP

COAL_MJ = 3.0
# 每种燃料的热值（MJ/单位）
FUEL = {"raw-coal": 3.0, "coal": 4.0, "coke": 5.0, "coal-gas": 0.2, "tar": 0.2}
# 能进锅炉的（有 fuel_category），红瓶阶段只有这三种
SOLID = ("raw-coal", "coal", "coke")
# 流体燃料（fuel_category 为 None，锅炉拒收）
FLUID = ("coal-gas", "tar")

POWER_EFF = 0.5      # boiler 1.0 × steam-engine 0.5
MINE_E = 0.25        # MJ 电 / raw-coal


def build(target_mj: float, *, fluid_power: bool = False,
          proc_cap_mj: float = 0.0, extra_power: float = 0.0,
          min_dist1: float = 0.0, force_out: str | None = None):
    """建模。参数含义：

    fluid_power : 是否已有能烧流体的发电设备（红瓶=False，py1 后=True）
    proc_cap_mj : 工艺炉（smelter/glassworks）每分钟能消化多少 MJ 流体燃料
    extra_power : 工厂其他部分的用电需求（MJ/分钟）
    min_dist1   : 强制蒸馏线至少跑几批（化工需求已经存在时）
    force_out   : 只允许对外输出这一种燃料
    """
    names = ["mine", "dist1", "dist2", "out_raw", "out_coal", "out_coke"]
    # 电力通道
    for f in SOLID:
        names.append(f"burn_{f}")
    if fluid_power:
        for f in FLUID:
            names.append(f"burn_{f}")
    # 工艺通道 + 放空
    for f in FLUID:
        names.append(f"proc_{f}")
        names.append(f"flare_{f}")

    idx = {n: i for i, n in enumerate(names)}
    lp = LP(len(names))

    def has(n):
        return n in idx

    def col(n):
        return {idx[n]: 1.0} if has(n) else {}

    def neg(n):
        return {idx[n]: -1.0} if has(n) else {}

    # ---------- 物料平衡 ----------
    # raw-coal: mine = 10*dist1 + burn + out
    d = {idx["mine"]: 1.0, idx["dist1"]: -10.0, idx["out_raw"]: -1.0}
    d.update(neg("burn_raw-coal"))
    lp.eq(d, 0)

    # coal: 3*dist1 = 10*dist2 + burn + out
    d = {idx["dist1"]: 3.0, idx["dist2"]: -10.0, idx["out_coal"]: -1.0}
    d.update(neg("burn_coal"))
    lp.eq(d, 0)

    # coke: 6*dist2 = burn + out
    d = {idx["dist2"]: 6.0, idx["out_coke"]: -1.0}
    d.update(neg("burn_coke"))
    lp.eq(d, 0)

    # coal-gas: 60*dist1 + 40*dist2 = 发电 + 工艺 + 放空
    d = {idx["dist1"]: 60.0, idx["dist2"]: 40.0, idx["flare_coal-gas"]: -1.0,
         idx["proc_coal-gas"]: -1.0}
    if fluid_power:
        d[idx["burn_coal-gas"]] = -1.0
    lp.eq(d, 0)

    # tar: 30*dist1 + 50*dist2 = 发电 + 工艺 + 放空
    d = {idx["dist1"]: 30.0, idx["dist2"]: 50.0, idx["flare_tar"]: -1.0,
         idx["proc_tar"]: -1.0}
    if fluid_power:
        d[idx["burn_tar"]] = -1.0
    lp.eq(d, 0)

    # ---------- 电力平衡 ----------
    # 需求（MJ 电）− 供给（MJ 电）<= extra_power（允许超发浪费）
    d = {idx["mine"]: MINE_E, idx["dist1"]: 1.0, idx["dist2"]: 1.5}
    # 固体燃料发电
    for f in SOLID:
        d[idx[f"burn_{f}"]] = d.get(idx[f"burn_{f}"], 0) - POWER_EFF * FUEL[f]
    # 流体燃料发电（仅 py1 后）
    if fluid_power:
        for f in FLUID:
            d[idx[f"burn_{f}"]] = d.get(idx[f"burn_{f}"], 0) - POWER_EFF * FUEL[f]
    # 工艺炉烧流体：按 effectivity 1.0 直接顶替用电
    for f in FLUID:
        d[idx[f"proc_{f}"]] = d.get(idx[f"proc_{f}"], 0) - FUEL[f]
    # d 的符号是「需求 − 供给」，额外用电是追加需求 → 移到右边取负
    lp.le(d, -extra_power)

    # ---------- 工艺炉容量 ----------
    # 流体顶替用电受实际 smelter/glassworks 台数限制
    cap = {idx[f"proc_{f}"]: FUEL[f] for f in FLUID}
    lp.le(cap, proc_cap_mj)

    # ---------- 对外输出 ----------
    lp.eq({idx["out_raw"]: FUEL["raw-coal"],
           idx["out_coal"]: FUEL["coal"],
           idx["out_coke"]: FUEL["coke"]}, target_mj)

    if min_dist1:
        lp.ge({idx["dist1"]: 1.0}, min_dist1)

    if force_out:
        for f, var in (("raw-coal", "out_raw"), ("coal", "out_coal"), ("coke", "out_coke")):
            if f != force_out:
                lp.eq({idx[var]: 1.0}, 0.0)

    lp.obj({idx["mine"]: 1.0})
    return lp, names


def run(target_mj: float, **kw):
    lp, names = build(target_mj, **kw)
    status, x, obj = lp.solve()
    if status != "optimal":
        return {"status": status, "coal": None, "detail": {}}
    detail = {n: round(x[i], 4) for i, n in enumerate(names) if x[i] > 1e-6}
    return {"status": status, "coal": obj, "detail": detail}


def _fmt_mix(detail: dict) -> str:
    parts = []
    for k, v in sorted(detail.items()):
        if k.startswith("burn_"):
            parts.append(f"{k[5:]}×{v:.1f}")
    return " + ".join(parts) or "(无燃烧)"


def main() -> None:
    TARGET = 100.0
    print(f"目标：对外输出 {TARGET:.0f} MJ/分钟固体燃料\n")

    print("=== 红瓶阶段（流体不可发电，工艺炉容量 0）===")
    print(f'{"方案":22s} {"采煤":>9s} {"投入MJ":>9s} {"EROI":>7s}  放空')
    rows = []
    for f in SOLID:
        r = run(TARGET, force_out=f)
        rows.append((f"只输出 {f}", r))
    rows.append(("自由输出", run(TARGET)))
    for name, r in rows:
        if r["status"] != "optimal":
            print(f"{name:22s}  {r['status']}")
            continue
        c = r["coal"]
        flare = sum(v for k, v in r["detail"].items() if k.startswith("flare_"))
        print(f"{name:22s} {c:9.2f} {c*COAL_MJ:9.1f} "
              f"{TARGET/(c*COAL_MJ)*100:6.1f}%  {flare:.0f}")

    print("\n=== 差分：如果流体能发电（py1 之后）===")
    print(f'{"方案":22s} {"采煤":>9s} {"EROI":>7s}  燃烧组合')
    for f in SOLID:
        r = run(TARGET, force_out=f, fluid_power=True)
        if r["status"] != "optimal":
            print(f"{f:22s}  {r['status']}")
            continue
        c = r["coal"]
        print(f"{'只输出 '+f:22s} {c:9.2f} {TARGET/(c*COAL_MJ)*100:6.1f}%  {_fmt_mix(r['detail'])}")

    print("\n=== 红瓶阶段有工艺负荷（glassworks 烧流体，effectivity 1.0）===")
    print(f'{"工艺容量MJ/min":>12s} ' + " ".join(f"{f:>18s}" for f in SOLID))
    for cap in (0.0, 100.0, 600.0):
        cells = []
        for f in SOLID:
            r = run(TARGET, force_out=f, proc_cap_mj=cap)
            cells.append(f'{r["coal"]:7.2f}/{TARGET/(r["coal"]*COAL_MJ)*100:5.1f}%'
                         if r["status"] == "optimal" else f'{r["status"]:>14s}')
        print(f'{cap:12.0f} ' + " ".join(f"{c:>18s}" for c in cells))


def power_scan() -> None:
    """纯供电场景：需求越大，红瓶阶段能烧什么。"""
    print("\n=== 纯供电（红瓶）：只有固体燃料能进锅炉 ===")
    print(f'{"需求MJ/min":>11s} {"采煤":>8s} {"EROI":>7s}  组合')
    for p in (30, 60, 120, 240):
        r = run(0.0, extra_power=p)
        if r["status"] != "optimal":
            print(f"{p:11.0f}  {r['status']}")
            continue
        print(f"{p:11.0f} {r['coal']:8.2f} {p/(r['coal']*COAL_MJ)*100:6.1f}%  {_fmt_mix(r['detail'])}")


def mixed_case() -> None:
    """化工线在跑、副产物必须消化 —— 红瓶阶段它们只能进工艺炉或放空。"""
    print("\n=== 蒸馏线已在跑：副产物去向（红瓶，工艺炉容量 0 / 30 MJ）===")
    print(f'{"蒸馏批次":>8s} {"工艺容量":>9s} {"采煤":>7s}  组合')
    for d1 in (2, 5, 10):
        for cap in (0.0, 30.0):
            r = run(0.0, extra_power=60, min_dist1=d1, proc_cap_mj=cap)
            if r["status"] != "optimal":
                print(f"{d1:8.0f} {cap:9.0f}  {r['status']}")
                continue
            proc = sum(v for k, v in r["detail"].items() if k.startswith("proc_"))
            flare = sum(v for k, v in r["detail"].items() if k.startswith("flare_"))
            print(f"{d1:8.0f} {cap:9.0f} {r['coal']:7.1f}  {_fmt_mix(r['detail'])}"
                  f"  (工艺 {proc:.0f} / 放空 {flare:.0f})")


if __name__ == "__main__":
    main()
    power_scan()
    mixed_case()
