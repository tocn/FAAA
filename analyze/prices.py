"""用 LP 对偶求物品的影子价格（边际成本）。

## 为什么不能用「递归摊成本」

`capital.py` 用的是价值迭代：c_i = min_r (e_r + Σ a_rj c_j) / y_ri。

它在有环的配方图上有两个毛病：
  1. 从高价往下收敛，只会到达**较大**的那个不动点，不是真正的最优
  2. 不同配方集可能落在不同不动点上，于是同一条成本轨迹会出现
     「配方变多、价格反而涨」这种违反经济直觉的结果

实测：electronic-circuit 在 chemical 阶段 46.35 MJ，到 py3 反而 46.62 MJ。
配方集是累加的，真实成本只可能降。所以那个 +0.4% 是算法误差，不是经济现象。

## 正确的工具：线性规划的对偶

对目标物品 t 建 LP：

    min   Σ_r e_r · x_r            （总能耗，e_r 是配方 r 的燃料当量）
    s.t.  Σ_r (y_ri − a_ri) · x_r ≥ d_i   ∀i   （每种物品的净产出 ≥ 需求）
          x_r ≥ 0

目标物品 d_t = 1，其余为 0。引入高价进口变量保证可行（造不出来时价格会顶到进口价）。

对偶变量 π_i 就是物品 i 的**影子价格**：多要一单位 i，最小总能耗增加多少。
这才是严格意义上的边际成本，而且：

  * 联产自动定价（副产物的 π 由它在约束里的作用决定，不需要人工分摊）
  * 循环配方不会低估（环里每转一圈都要付 e_r）
  * 价格随配方集单调不增（加变量只会让最小值更小）

## 经济学含义

价格不是物品的固有属性，而是**给定需求结构下的边际替代率**。
需求 d 变了，π 就变。所以「这个材料值多少 MJ」这句话本身必须带上下文。
本模块默认考察「造 1 个目标物品」这一需求结构，所得 π 应读作
「为了多造一个目标物品，各中间品的边际价值」。
"""
from __future__ import annotations

from .capital import Capital, IMPORT_MJ, RAW_OK, FLUID_MJ, MINE_MJ
from .lp import LP

# 闭包上限。设小了会静默截断，导致某些原料「无来源」而被按进口价封顶，
# 表现为价格突然变成 CAPPED。electron-circuit 在 chemical 阶段实测需要 3144 条，
# 所以留足余量。
MAX_RECIPES = 9000


def closure(cap: Capital, target: str, limit: int = MAX_RECIPES):
    """从目标物品反向收集可达配方（广度优先，超限就截断）。"""
    recipes: list[str] = []
    seen_r: set[str] = set()
    items: set[str] = set()
    frontier = [target]

    while frontier and len(recipes) < limit:
        it = frontier.pop()
        if it in items:
            continue
        items.add(it)
        for rn in cap.prod.get(it, []):
            if rn in seen_r or rn not in cap.allowed:
                continue
            if cap.R[rn].get("hidden"):
                continue
            seen_r.add(rn)
            recipes.append(rn)
            for ing, _ in cap._ings(rn):
                if ing not in items:
                    frontier.append(ing)
            if len(recipes) >= limit:
                break
    return recipes, items


def shadow_prices(cap: Capital, target: str, limit: int = MAX_RECIPES):
    """→ (目标价格, {物品: 影子价格}, 是否被进口价封顶)"""
    recipes, items = closure(cap, target, limit)

    # 物品必须包含所有原料，否则约束缺失
    for rn in recipes:
        for ing, _ in cap._ings(rn):
            items.add(ing)
        for n, _ in cap._ress(rn):
            items.add(n)

    items_l = sorted(items)
    ix = {it: i for i, it in enumerate(items_l)}
    nr, ni = len(recipes), len(items_l)
    nv = nr + ni

    lp = LP(nv)

    # 每种物品一条净产出约束；目标要 1，其余要 0
    for it, i in ix.items():
        row: dict[int, float] = {}
        for j, rn in enumerate(recipes):
            out = sum(a for n, a in cap._ress(rn) if n == it)
            inp = sum(q for n, q in cap._ings(rn) if n == it)
            net = out - inp
            if net:
                row[j] = net
        row[nr + i] = 1.0                      # 进口变量
        lp.ge(row, 1.0 if it == target else 0.0)

    # 目标：能耗 + 进口代价。
    # 进口价必须按物品类型区分，否则整张表会被一种公用流体顶到天上去：
    #   RAW_OK  → 采矿成本
    #   公用流体 → 热力学价值（water=0，steam=0.235 MJ/单位）
    #   其余    → 高价罚（表示本阶段造不出来）
    def import_price(name: str) -> float:
        if name in FLUID_MJ:
            return FLUID_MJ[name]
        if name in RAW_OK:
            return MINE_MJ
        return IMPORT_MJ

    obj: dict[int, float] = {j: cap.craft_mj(rn) for j, rn in enumerate(recipes)}
    for i in range(ni):
        obj[nr + i] = import_price(items_l[i])
    lp.obj(obj)

    vals, duals = lp.solve_duals()
    if vals is None:
        return None, {}, False

    total = sum(obj[j] * vals[j] for j in obj)
    prices = {it: duals[ix[it]] for it in items_l}
    capped = total >= IMPORT_MJ * 0.5
    return total, prices, capped


def main() -> None:
    import sys
    sys.setrecursionlimit(20000)
    from .economy import STAGES

    cap = Capital("red")
    targets = ["copper-plate", "steel-plate", "electronic-circuit"]

    print("=== LP 对偶价格 vs 价值迭代（MJ/个）===")
    print(f'{"物品":22s}{"阶段":>16s}{"LP":>10s}{"迭代":>10s}{"差":>9s}')
    for t in targets:
        for name, packs in (STAGES[0], STAGES[4], STAGES[5]):
            cap.set_stage(packs)
            cap._relax()
            it_v = cap.price(t)
            lp_v, _, capped = shadow_prices(cap, t)
            show_lp = "-" if (lp_v is None or capped) else f"{lp_v:.1f}"
            show_it = "-" if it_v is None else f"{it_v:.1f}"
            diff = ""
            if it_v is not None and lp_v is not None and not capped:
                diff = f"{(it_v - lp_v) / lp_v * 100:+.1f}%"
            print(f"{t:22s}{name:>16s}{show_lp:>10s}{show_it:>10s}{diff:>9s}")

    # 影子价格一览：一次求解给出所有中间品的边际价值
    print()
    print("=== 影子价格前 12（目标 = steel-plate，阶段 = 红瓶）===")
    cap.set_stage(STAGES[0][1])
    total, prices, capped = shadow_prices(cap, "steel-plate")
    top = sorted(prices.items(), key=lambda kv: -kv[1])[:12]
    for it, p in top:
        print(f"  {it:28s} {p:9.3f}")


if __name__ == "__main__":
    main()
