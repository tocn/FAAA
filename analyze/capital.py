"""机器造价的能量核算：把「建造成本」折算成 MJ，用于回本时间计算。

为什么需要这个模块：
EROI 只算运行能耗，回答不了「这台机器值不值得建」。农业线和风力机这类
设施的成本结构是「一次性建造成本换持续输出」，必须单独一条账：

    回本时间 = 建造成本(MJ) / (净输出功率 MW × 86.4)

（1 MW 运行一天 = 24 × 3600 MJ = 86.4 GJ）

成本口径（两级）：
1. **材料清单** —— 递归展平到原矿，给人看
2. **内含能量** —— 采矿电 + 加工电 + 建造那一次的电，统一折成 MJ 燃料值

口径说明：
- 加工电 = 机器 energy_usage × (配方 energy_required / 机器 crafting_speed)
- 采矿电默认 0.25 MJ/单位（electric-mining-drill 1MW / speed 2 / mining_time 0.5）
- 电力折燃料按 boiler→steam-engine 综合 0.5
- 只看红瓶阶段可得的配方（红瓶科技闭包 ∪ 默认启用）
"""
from __future__ import annotations

import json
import sys

DUMP = r"C:/Users/Administrator/AppData/Roaming/Factorio/script-output/data-raw-dump.json"

POWER_EFF = 0.5          # 煤→电
MINE_MJ = 0.25           # 采矿电 MJ/单位（固体原矿）

# 公用流体的固定单价（MJ/单位），不参与价值迭代。
# steam: boiler 出 250°C，steam 的 heat_capacity 是 1kJ/°C，
#        相对 15°C 的默认温度 → (250-15) kJ = 0.235 MJ/单位
FLUID_MJ = {
    "water": 0.0,
    "steam": 0.235,
}

# 「本阶段造不出来」的物品按高价进口计，不能按原矿价计。
# 用低价兜底会破坏一个基本性质：配方集变大，成本只可能降。
# 原因很直接——低价兜底 = 假装它便宜，等真配方解锁反而"涨价"。
IMPORT_MJ = 1e4

# 视为原矿、不再追问来源的东西
RAW_OK = {
    "iron-ore", "copper-ore", "coal", "stone", "raw-coal", "ore-quartz",
    "ore-aluminium", "ore-tin", "ore-lead", "ore-nickel", "ore-titanium",
    "ore-zinc", "ore-chromium", "ore-manganese", "limestone", "sand",
    # wood 是砍树来的，不经过配方；不加进来会形成
    # log → wood-seeds → wood → log 的纯循环，整条链算不出成本
    "wood",
}


def parse_j(v) -> float:
    """'5MW' / '550kW' / 11000000 → MJ（每秒）"""
    if isinstance(v, (int, float)):
        return float(v) / 1e6
    s = str(v or "").strip().lower().replace(" ", "")
    for suf, mult in (("gw", 1e3), ("mw", 1.0), ("kw", 1e-3), ("w", 1e-6)):
        if s.endswith(suf):
            try:
                return float(s[: -len(suf)]) * mult
            except ValueError:
                return 0.0
    return 0.0


class Capital:
    def __init__(self, stage: str = "red"):
        d = json.load(open(DUMP, encoding="utf-8"))
        self.dump = d
        self.R = d["recipe"]
        self.T = d["technology"]
        self.packs = {"automation-science-pack"}
        self.allowed = self._stage_recipes(stage) if stage == "red" else set(self.R)
        self.prod = {}
        for rn, r in self.R.items():
            for x in self._results(r):
                self.prod.setdefault(x, []).append(rn)
        self._item_memo: dict[str, tuple[dict, float]] = {}
        self._craft_memo: dict[str, float] = {}
        self._cost: dict[str, float] = {}
        self._best: dict[str, str] = {}
        self._ing_cache: dict[str, list] = {}
        self._res_cache: dict[str, list] = {}
        self._machine = self._machine_index()

    # ---------- 阶段筛选 ----------
    def set_stage(self, packs: set[str]) -> None:
        """把可用配方切到「目前能产的科技瓶集合」对应的阶段，并清空价格缓存。

        成本是路径依赖的：解锁一条更省的配方，整个下游价格都会变。
        所以同一个物品在不同阶段有不同的价格，不能算一次就锁死。
        """
        self.packs = set(packs)
        self.allowed = self._recipes_for(packs)
        self._item_memo.clear()
        self._craft_memo.clear()
        self._cost = {}
        self._best = {}

    def _recipes_for(self, packs: set[str]) -> set[str]:
        """可以研究到的科技 → 解锁的配方。

        科技可研究 = 它的 unit.ingredients 全部落在 packs 里；
        没有 unit 的科技是开局自带，永远算在内。
        """
        out = set()
        for n, p in self.T.items():
            u = p.get("unit")
            if u:
                ings = {i[0] for i in u.get("ingredients") or []}
                if not ings <= packs:
                    continue
            for e in (p.get("effects") or []):
                if e.get("type") == "unlock-recipe":
                    out.add(e["recipe"])
        out |= {n for n, r in self.R.items() if r.get("enabled")}
        return out

    def _stage_recipes(self, stage: str) -> set[str]:
        if stage != "red":
            return set(self.R)
        return self._recipes_for({"automation-science-pack"})

    def _closure(self, tech: str) -> set[str]:
        seen, stack = set(), [tech]
        while stack:
            t = stack.pop()
            if t in seen or t not in self.T:
                continue
            seen.add(t)
            stack.extend(self.T[t].get("prerequisites") or [])
        for n, p in self.T.items():
            u = p.get("unit")
            if not u:
                # 没有研究单位的科技是开局自带的（如 steam-power），必须算进来，
                # 否则基础冶炼链会断掉，铁板变成「无来源」
                seen.add(n)
                continue
            ing = [i[0] for i in u.get("ingredients") or []]
            if not ing or all(x == "automation-science-pack" for x in ing):
                seen.add(n)
        return seen

    # ---------- 机器 ----------
    def _machine_index(self) -> dict[str, tuple[str, float, float, str]]:
        """类别 → (机器名, speed, 能耗 MW, 能量源类型)，取速度中位数那台。"""
        by_cat: dict[str, list] = {}
        for kind in ("assembling-machine", "furnace", "mining-drill",
                     "oil-refinery", "chemical-plant", "rocket-silo"):
            for n, p in (self.dump.get(kind) or {}).items():
                usage = parse_j(p.get("energy_usage"))
                speed = p.get("crafting_speed") or 1.0
                src = (p.get("energy_source") or {}).get("type") or "electric"
                for c in p.get("crafting_categories") or []:
                    by_cat.setdefault(c, []).append((n, speed, usage, src))
        out = {}
        for c, lst in by_cat.items():
            lst = [x for x in lst if x[2] > 0] or lst
            # 取「每次合成最省能」的那台：能耗/速度最小。
            # 用速度中位数会让慢而高耗的老机器入选，成本虚高数倍。
            lst.sort(key=lambda x: (x[2] / max(x[1], 1e-9)))
            out[c] = lst[0]
        return out

    # ---------- 配方 ----------
    @staticmethod
    def _results(r) -> list[str]:
        res = r.get("results") or []
        if isinstance(res, dict):
            return list(res)
        return [x.get("name") for x in res if isinstance(x, dict)]

    @staticmethod
    def _ingredients(r) -> list[tuple[str, float]]:
        out = []
        for i in (r.get("ingredients") or []):
            if isinstance(i, dict):
                out.append((i["name"], float(i.get("amount", 1))))
            else:
                out.append((i[0], float(i[1])))
        return out

    def _ings(self, rn: str) -> list[tuple[str, float]]:
        v = self._ing_cache.get(rn)
        if v is None:
            v = self._ingredients(self.R[rn])
            self._ing_cache[rn] = v
        return v

    def _ress(self, rn: str) -> list[tuple[str, float]]:
        v = self._res_cache.get(rn)
        if v is None:
            v = self._result_pairs(self.R[rn])
            self._res_cache[rn] = v
        return v

    def craft_mj(self, rn: str) -> float:
        """做一次配方的**燃料当量**（MJ，已含机器速度）。

        能量源类型决定要不要除效率：
          - burner / fluid / heat：energy_usage 本身就是燃料或热量，直接用
          - electric：那是电，要按 boiler→steam-engine 的 0.5 折回燃料
        """
        if rn in self._craft_memo:
            return self._craft_memo[rn]
        r = self.R[rn]
        cat = r.get("category") or "crafting"
        m = self._machine.get(cat)
        t = float(r.get("energy_required") or 0.5)
        mj = 0.0
        if m:
            _, speed, usage, src = m
            raw = usage * (t / max(speed, 1e-6))
            mj = raw if src in ("burner", "fluid", "heat", "void") else raw / POWER_EFF
        self._craft_memo[rn] = mj
        return mj

    def pick_recipe(self, item: str) -> str | None:
        """取全局最省的配方（由 _relax 算出），而不是按原料个数猜。"""
        if not self._cost:
            self._relax()
        return self._best.get(item)

    def _relax(self, rounds: int = 80) -> None:
        """价值迭代求每个物品的单位成本（MJ/个）。

        从 INF 往下收敛，所以循环配方（A 需要 B、B 需要 A）不会拿到虚假低价，
        因为每次加工都要付出加工电。这比「选原料最少的配方」可靠得多——
        后者会挑中 iron-oxide-smelting（每块铁板 666 煤）和 steel-from-barrels
        （steel-plate 需要 steel-plate）这种陷阱。
        """
        INF = float("inf")
        cost: dict[str, float] = {}

        # 先给「没有任何红瓶配方」的物品封底，否则第一轮全是 INF，传播不起来
        producible = set()
        referenced = set()
        for rn in self.allowed:
            producible |= {n for n, _ in self._result_pairs(self.R[rn])}
            referenced |= {n for n, _ in self._ingredients(self.R[rn])}
        fluids = set(self.dump.get("fluid") or {})
        for n in referenced | set(self.prod):
            if n not in producible and n not in RAW_OK and n not in FLUID_MJ:
                cost[n] = 0.0 if n in fluids else IMPORT_MJ
        for n in RAW_OK:
            cost.setdefault(n, MINE_MJ)
        for n, v in FLUID_MJ.items():
            cost[n] = v

        def recipe_cost(rn: str) -> tuple[float, dict[str, float]]:
            total = self.craft_mj(rn)
            used: dict[str, float] = {}
            for ing, q in self._ings(rn):
                c = cost.get(ing, INF)
                if c == INF:
                    # 流体取不到来源时按 0 计：水泵/管道本来就是免费供给，
                    # 而「从蒸汽冷凝回水」这类配方会把水自己锁成 INF
                    if ing in fluids:
                        c = 0.0
                    else:
                        return INF, {}
                total += c * q
                used[ing] = used.get(ing, 0.0) + q
            return total, used

        for _ in range(rounds):
            changed = False
            for rn in sorted(self.allowed):
                r = self.R[rn]
                total, _ = recipe_cost(rn)
                if total == INF:
                    continue
                for name, amount in self._ress(rn):
                    if name in FLUID_MJ:
                        continue          # 公用流体用固定价，别让配方把它算歪
                    per = total / max(amount, 1e-9)
                    if per < cost.get(name, INF) - 1e-9:
                        cost[name] = per
                        self._best[name] = rn
                        changed = True
            if not changed:
                break

        for it in RAW_OK:
            cost.setdefault(it, MINE_MJ)
        self._cost = cost

    @staticmethod
    def _result_pairs(r) -> list[tuple[str, float]]:
        res = r.get("results") or []
        if isinstance(res, dict):
            return [(k, float(v)) for k, v in res.items()]
        out = []
        for x in res:
            if isinstance(x, dict):
                out.append((x["name"], float(x.get("amount") or x.get("amount_min") or 1)))
        return out

    def item(self, item: str, _stack: frozenset = frozenset()) -> tuple[dict, float]:
        """→ (原矿清单, 内含能量 MJ)。

        能量以 _relax 的收敛值为准（那是全局最省的路径），递归只负责把
        配方树摊平成原料清单。两者不能混算：_relax 把水和蒸汽按公用流体
        定价，而递归展开会顺着「制取该流体」的配方走到很贵的地方。
        """
        if item in self._item_memo:
            return self._item_memo[item]
        if not self._cost:
            self._relax()
        energy = self._cost.get(item, MINE_MJ)
        raw = self._breakdown(item, _stack)
        self._item_memo[item] = (raw, energy)
        return raw, energy

    def _breakdown(self, item: str, _stack: frozenset, depth: int = 0) -> dict[str, float]:
        """只做清单展开，不算钱。"""
        if item in RAW_OK or item in FLUID_MJ or item in _stack or depth > 14:
            return {item: 1.0}
        rn = self._best.get(item)
        if rn is None:
            return {item: 1.0}

        r = self.R[rn]
        amount = 1.0
        for name, amt in self._result_pairs(r):
            if name == item:
                amount = max(amt, 1e-9)
                break

        raw: dict[str, float] = {}
        for ing, q in self._ingredients(r):
            sub = self._breakdown(ing, _stack | {item}, depth + 1)
            for k, v in sub.items():
                raw[k] = raw.get(k, 0.0) + v * q / amount
        return raw

    def entity(self, item: str) -> dict:
        raw, mj = self.item(item)
        return {"item": item, "raw": raw, "build_mj": mj}

    def price(self, item: str) -> float | None:
        """能算出来的成本；算不出来返回 None，而不是拿原矿兜底价冒充。

        这个区分很重要：兜底价会让「本阶段还造不出来的东西」看起来只要 0.25 MJ，
        等下一阶段真配方解锁，价格反而"上涨"——违反「配方集变大成本只会降」。
        之前的 iron-gear-wheel 就是这样 0.2 → 2.0 的假信号。
        """
        if not self._cost:
            self._relax()
        if item in RAW_OK or item in FLUID_MJ:
            return self._cost.get(item)
        v = self._cost.get(item)
        # 还在按进口价计价 = 本阶段其实造不出来，如实返回 None
        if v is None or v >= IMPORT_MJ * 0.5:
            return None
        return v

    def top_raw(self, raw: dict, n: int = 8) -> list[tuple[str, float]]:
        return sorted(raw.items(), key=lambda kv: -kv[1])[:n]


def main() -> None:
    sys.setrecursionlimit(20000)
    cap = Capital("red")

    targets = ["multiblade-turbine-mk01", "steam-engine", "boiler",
               "accumulator", "small-electric-pole"]
    print("=== 红瓶阶段建造内含能量（MJ 燃料当量）===")
    for t in targets:
        raw, mj = cap.item(t)
        rn = cap._best.get(t)
        print(f"\n{t}  →  {mj:.1f} MJ   (配方 {rn})")
        if not rn:
            print("    （红瓶阶段无可用配方）")
            continue
        for ing, q in cap._ingredients(cap.R[rn]):
            _, c = cap.item(ing)
            print(f"    {ing:24s} ×{q:<5g} 单价 {c:7.2f}  = {c*q:8.1f}")
        print(f"    加工能耗 {cap.craft_mj(rn):.2f} MJ")


def tiles(name: str, dump: dict) -> float:
    """从原型里读占地（按 collision_box 算方块数）。"""
    for kind, v in dump.items():
        if not isinstance(v, dict) or name not in v:
            continue
        box = v[name].get("collision_box")
        if box:
            (x1, y1), (x2, y2) = box
            return round((x2 - x1) * (y2 - y1), 1)
    return 0.0


# 风力机会额外生成一个隐形碰撞体，强制与同类保持净空（源码里
# prototypes/buildings/<name>.lua 的 simple-entity-with-force）。
# 这块地才是真正的占地成本，光看建筑本身会严重低估。
WIND_CLEARANCE = {
    "multiblade-turbine-mk01": 26.8,   # ±13.4
    "multiblade-turbine-mk03": 24.8,   # ±12.4
    "hawt-turbine-mk01": 26.8,
    "vawt-turbine-mk01": 35.8,         # ±17.9
}


def power_report() -> None:
    """风力机 vs 煤电：成本/功率比、占地、回本时间。"""
    import json as _json
    sys.setrecursionlimit(20000)
    cap = Capital("red")
    dump = cap.dump

    # 红瓶阶段唯一可用的风力机：steel-processing 解锁
    wind = "multiblade-turbine-mk01"
    wind_kw = 550.0                       # energy_production，风速均值下≈标称值

    # 煤电基准：1 锅炉 + 2 蒸汽机 ≈ 3.5 MW（boiler 3.7MW 热，engine 15/s × 235kJ × 0.5）
    blk_mj = cap.item("boiler")[1] + 2 * cap.item("steam-engine")[1]
    blk_kw = 3500.0
    blk_tiles = tiles("boiler", dump) + 2 * tiles("steam-engine", dump)

    w_mj = cap.item(wind)[1]
    w_tiles = tiles(wind, dump)

    clear = WIND_CLEARANCE.get(wind, 0.0)
    clear_tiles = clear * clear

    rows = [
        ("风力 multiblade-mk01（净空）", wind_kw, clear_tiles, w_mj),
        ("煤电 1锅炉+2蒸汽机", blk_kw, blk_tiles, blk_mj),
    ]

    print("=== 红瓶阶段：成本 / 功率比 ===")
    print(f'{"方案":24s} {"功率kW":>8s} {"占地":>6s} {"内含MJ":>9s} '
          f'{"MJ/kW":>8s} {"格/MW":>8s}')
    for name, kw, tl, mj in rows:
        print(f"{name:24s} {kw:8.0f} {tl:6.0f} {mj:9.1f} "
              f"{mj/kw:8.2f} {tl/(kw/1000):8.1f}")

    # 煤电的燃料消耗（每 kW 电 = 2 kW 热）
    fuel_per_mw_s = 2.0          # MJ 燃料 / 秒 / MW 电
    wind_mj_per_kw = w_mj / wind_kw
    coal_mj_per_kw = blk_mj / blk_kw
    delta = wind_mj_per_kw - coal_mj_per_kw      # MJ per kW of extra capital
    payback_s = delta / (fuel_per_mw_s / 1000)   # 每秒每 kW 烧多少 MJ

    print()
    print(f"建筑本体 {w_tiles:.0f} 格，但净空要求 {clear:.1f}×{clear:.1f} = {clear_tiles:.0f} 格")
    print(f"→ 实际占地 {clear_tiles/(wind_kw/1000):.0f} 格/MW，"
          f"是煤电的 {clear_tiles/(wind_kw/1000)/(blk_tiles/(blk_kw/1000)):.0f} 倍")
    print()
    print(f"多花的建造费：{delta:.2f} MJ/kW")
    print(f"煤电燃料消耗：{fuel_per_mw_s:.1f} MJ/秒/MW 电")
    print(f"→ 回本时间：{payback_s/60:.0f} 分钟等效满负荷运行")

    print()
    print("=== 单台风力机 vs 等功率煤电 ===")
    coal_mj_for_same = coal_mj_per_kw * wind_kw
    print(f"  风力机内含 {w_mj:.0f} MJ；同功率煤电只需 {coal_mj_for_same:.0f} MJ")
    print(f"  差额 {w_mj - coal_mj_for_same:.0f} MJ ≈ "
          f"{(w_mj - coal_mj_for_same)/3:.0f} 个原煤")
    print(f"  这些差额够煤电跑 {(w_mj - coal_mj_for_same)/(wind_kw/1000*fuel_per_mw_s)/60:.0f} 分钟")


if __name__ == "__main__":
    main()
    power_report()
