"""材料成本的经济学模型：从「静态价格」到「成本轨迹」。

## 为什么要这个模块

`capital.py` 只回答「现在这个科技阶段，造这个东西要多少 MJ」。
但 Pyanodons 的配方树会随科技不断重构：一条新配方解锁，可能让某个中间品
的价格掉一个数量级，而它下游的所有东西跟着重定价。

用静态价格做规划会犯两种错：
  * 用后期价格评估前期决策 —— 低估当下的代价
  * 用前期价格评估后期投资 —— 高估收益，因为你会先解锁更便宜的路线

所以需要两个概念：

| 概念 | 定义 | 用途 |
|---|---|---|
| **静态成本** | 固定科技快照下，做 1 个物品的最小能耗 | 当下的采购/建造决策 |
| **成本轨迹** | 成本随科技阶段变化的曲线 | 研究优先级、投资时机 |
| **成本断崖** | 相邻阶段间跌幅超过阈值的点 | 该优先研究哪个科技 |

## 价格的口径

本模块算的是**毛成本（gross cost）**：最小化总能耗，不给副产物记收入。

这是有意的：净成本（扣副产物）需要知道副产物的市场需求，而需求是外生的。
同一个副产物，有人要就有价，没人要就是负债（还得花能量处理）。
所以严格说**不存在「物品的内在成本」**，只有「给定需求结构下的边际成本」。
毛成本是它的上界，也是不需要假设需求时唯一能定义的口径。

## 模型

对每个科技阶段 T（由「能产哪些科技瓶」定义）：

    c_i(T) = min over r in R(T)   [ e_r + Σ_j a_rj · c_j(T) ] / y_ri

其中 e_r 是配方 r 的能耗（燃料当量 MJ，已按能量源类型折算），
a_rj 是投入量，y_ri 是产出量，R(T) 是该阶段可用配方集。

这个方程用价值迭代求解（从 INF 往下收敛）。它自动挑最省的配方，
所以「新配方解锁 → 价格下降」会自然体现出来。

## 已知偏差：这是**下界**，不是准确值

价值迭代在环上有两个毛病：
  1. 从高价往下收敛只到「较大不动点」，不是最优
  2. 多产出配方按产量均摊，副产物实际被当成免费，于是环里可以"凭空"变便宜

实测与 LP 对偶（`analyze/prices.py`）的差距：
  copper-plate  红瓶 4.0 / 4.0 一致，chemical 阶段 0.3 vs 4.0（差 13 倍）
  electronic-circuit  chemical 阶段 46.4 vs 169.8（差 3.7 倍）

所以本模块的轨迹只适合**快速筛查「哪里发生了结构变化」**，
要拿具体数字做决策请走 `prices.py` 的 LP 对偶。
两个模型的差距本身也是信息：它衡量的是"联产计价"对这条链有多重要。
"""
from __future__ import annotations

import sys

from .capital import Capital, DUMP

# 科技阶段：按典型推进顺序累积瓶种。
# 顺序本身是假设，但每一阶段内部的配方集是严格由「unit.ingredients ⊆ 已有瓶」推出来的。
STAGES: list[tuple[str, set[str]]] = [
    ("红瓶", {"automation-science-pack"}),
    ("+py1(紫)", {"automation-science-pack", "py-science-pack-1"}),
    ("+logistic(绿)", {"automation-science-pack", "py-science-pack-1",
                       "logistic-science-pack"}),
    ("+py2", {"automation-science-pack", "py-science-pack-1",
              "logistic-science-pack", "py-science-pack-2"}),
    ("+chemical(蓝)", {"automation-science-pack", "py-science-pack-1",
                       "logistic-science-pack", "py-science-pack-2",
                       "chemical-science-pack"}),
    ("+py3", {"automation-science-pack", "py-science-pack-1",
              "logistic-science-pack", "py-science-pack-2",
              "chemical-science-pack", "py-science-pack-3"}),
    ("+production(紫红)", {"automation-science-pack", "py-science-pack-1",
                           "logistic-science-pack", "py-science-pack-2",
                           "chemical-science-pack", "py-science-pack-3",
                           "production-science-pack"}),
    ("+utility(黄)", {"automation-science-pack", "py-science-pack-1",
                      "logistic-science-pack", "py-science-pack-2",
                      "chemical-science-pack", "py-science-pack-3",
                      "production-science-pack", "utility-science-pack"}),
]

# 观察对象：从原料到成品的代表性材料
WATCH = [
    "iron-plate", "copper-plate", "steel-plate",
    "iron-gear-wheel", "copper-cable", "pipe",
    "small-parts-01", "inductor1", "ceramic",
    "small-electric-pole", "electronic-circuit",
    "stone-brick", "glass", "coke", "coal",
]

# 跌幅超过这个倍数就算「断崖」
CLIFF_RATIO = 2.0


def sweep(cap: Capital, items: list[str]) -> dict[str, list[float | None]]:
    """一趟扫完所有阶段：每个阶段只解一次价格体系。

    阶段在外层，物品在内层 —— 反过来的话每个物品都要重解一遍，慢十几倍。
    """
    out: dict[str, list[float | None]] = {it: [] for it in items}
    for _, packs in STAGES:
        cap.set_stage(packs)
        cap._relax()
        for it in items:
            out[it].append(cap.price(it))
    return out


def unlock_delta(cap: Capital, i: int) -> set[str]:
    """第 i 个阶段相对上一阶段新增的配方。"""
    prev = cap._recipes_for(STAGES[i - 1][1]) if i > 0 else set()
    return cap._recipes_for(STAGES[i][1]) - prev


def main() -> None:
    sys.setrecursionlimit(20000)
    cap = Capital("red")

    labels = [s[0] for s in STAGES]
    print("=== 材料成本轨迹（MJ/个，价值迭代下界，非精确值）===")
    head = f'{"物品":22s}' + "".join(f"{l:>14s}" for l in labels)
    print(head)
    print("-" * len(head))

    traj = sweep(cap, WATCH)
    for it in WATCH:
        cells = "".join(f"{('-' if v is None else f'{v:.1f}'):>14s}" for v in traj[it])
        print(f"{it:22s}{cells}")

    # ---------- 成本断崖 ----------
    print()
    print("=== 成本断崖（相邻阶段跌幅 ≥ 2 倍）===")
    cliffs = []
    for it, t in traj.items():
        for i in range(1, len(t)):
            a, b = t[i - 1], t[i]
            if a is None or b is None or b <= 0:
                continue
            if a / b >= CLIFF_RATIO:
                cliffs.append((it, i, a, b, a / b))
    cliffs.sort(key=lambda x: -x[4])

    if not cliffs:
        print("  （无）")
    for it, i, a, b, ratio in cliffs[:12]:
        print(f"  {it:22s} {labels[i-1]} → {labels[i]:14s} "
              f"{a:9.1f} → {b:7.1f}  ({ratio:.1f}×)")

    # ---------- 断崖归因 ----------
    print()
    print("=== 断崖归因：该阶段新解锁的配方 ===")
    for it, i, a, b, ratio in cliffs[:6]:
        new = unlock_delta(cap, i)
        makers = [r for r in new
                  if any(n == it for n, _ in cap._ress(r))]
        print(f"\n  {it}  {labels[i-1]} → {labels[i]}   {a:.1f} → {b:.1f} MJ")
        print(f"    该阶段新增 {len(new)} 条配方，直接产出它的 {len(makers)} 条")
        if makers:
            for rn in makers[:4]:
                print(f"      候选: {rn}  {cap._ings(rn)[:4]}")
        else:
            cap.set_stage(STAGES[i][1])
            cap._relax()
            rn = cap._best.get(it)
            if rn:
                print(f"      间接降价：当前最优仍是 {rn}")
                for ing, q in cap._ings(rn)[:4]:
                    print(f"        ← {ing:22s} ×{q:<6g} {cap._cost.get(ing, 0):.1f} MJ")


if __name__ == "__main__":
    main()


if __name__ == "__main__":
    main()
