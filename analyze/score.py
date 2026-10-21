"""任务生成与评分。

输入: Snapshot + Graph (+ 机器产能表)
输出: Task 列表，按优先级排序
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field, asdict

W_TO_MW = 1e6


@dataclass
class Task:
    kind: str
    title: str
    detail: str
    action: str
    priority: float                      # 0-100
    target: str | None = None            # 关联物品 / 科技
    delta_per_min: float = 0.0           # 建议新增产能
    evidence: dict = field(default_factory=dict)
    machines: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return asdict(self)


DEFAULT_CFG = {
    "target_science_per_min": 60.0,
    "cover_warn_min": 45.0,
    "min_demand": 0.5,
    "stock_min_demand": 5.0,
    "power_warn": 0.95,
    "power_headroom_warn": 0.15,
    "ratio_warn": 0.88,
    "max_depth": 4,
    "tech_lookahead": 5,
    "same_kind_cap": 4,      # 同类任务占前 N 名的上限（power/science 不受限）
}


def _fmt(n: float, unit: str = "") -> str:
    if n == float("inf"):
        return "∞"
    if abs(n) >= 1e6:
        return f"{n/1e6:.2f}M{unit}"
    if abs(n) >= 1e4:
        return f"{n/1e3:.1f}k{unit}"
    if abs(n) >= 100:
        return f"{n:.0f}{unit}"
    return f"{n:.1f}{unit}"


def _fmt_eta(minutes: float) -> str:
    if minutes == float("inf"):
        return "∞"
    if minutes >= 120:
        return f"{minutes/60:.1f} 小时"
    return f"{minutes:.0f} 分钟"


def _machines_for(graph, machines, item: str, delta_per_min: float) -> tuple[list[str], str]:
    rn = graph.best_recipe(item)
    if not rn or not machines:
        return [], ""
    r = graph.recipes[rn]
    cat = r["cat"]
    speed = machines.get("typical_speed", {}).get(cat)
    if not speed or speed <= 0:
        return [], ""
    out_per_craft = r["res"].get(item, 0) or 1
    crafts_per_min = delta_per_min / out_per_craft
    seconds_needed = crafts_per_min * r["t"]
    count = seconds_needed / 60.0 / speed
    names = list(machines.get("by_category", {}).get(cat, []))[:6]
    if count < 0.1:
        amount = "现有产能基本够，只需微调"
    else:
        amount = f"约 {count:.1f} 台"
    txt = (f"配方 {rn}（{_fmt(out_per_craft, ' 产出/次')}，{r['t']:g}s，"
           f"类别机器速度按 {speed:g} 计）→ {amount}")
    return names, txt


def _upstream_blame(snap, graph, item: str, cfg, depth: int = 0, visited: set | None = None):
    visited = visited or set()
    if depth >= cfg["max_depth"] or item in visited:
        return None
    visited.add(item)
    rn = graph.best_recipe(item)
    if not rn:
        return None
    r = graph.recipes[rn]
    worst = None
    for ing in r["ing"]:
        ratio = snap.supply_ratio(ing)
        if ratio == float("inf") or snap.in_rate(ing, "h10") < cfg["min_demand"]:
            continue
        cand = (ing, ratio, [item, ing])
        if ratio < cfg["ratio_warn"]:
            deeper = _upstream_blame(snap, graph, ing, cfg, depth + 1, visited)
            if deeper and deeper[1] < cand[1]:
                cand = (deeper[0], deeper[1], [item] + deeper[2])
        if worst is None or cand[1] < worst[1]:
            worst = cand
    return worst


def _should_report_stock(snap, graph, name: str, cfg) -> bool:
    """流体没有真正库存概念（管道内），stock=0 不能当成缺货。"""
    if snap.stock(name) <= 1e-6:
        return snap.supply_ratio(name) < 0.97
    return True


def generate(snap, graph, machines, cfg: dict | None = None) -> list[Task]:
    cfg = {**DEFAULT_CFG, **(cfg or {})}
    tasks: list[Task] = []
    best_by_target: dict[str, Task] = {}
    occupied: set[str] = set()      # 已被更高层判断占用的物品，避免重复报

    def add(t: Task):
        key = t.target or t.title
        prev = best_by_target.get(key)
        if prev is None or t.priority > prev.priority:
            if prev in tasks:
                tasks.remove(prev)
            best_by_target[key] = t
            tasks.append(t)

    # ---------- 1. 电力 ----------
    pt = snap.power_total
    prod = pt.get("produced", 0.0)
    cons = pt.get("consumed", 0.0)
    sat = pt.get("satisfaction", 1.0)
    headroom = snap.power_headroom()
    prod_mw, cons_mw = prod / W_TO_MW, cons / W_TO_MW
    if cons > 0 and (sat < cfg["power_warn"] or headroom < cfg["power_headroom_warn"]):
        deficit_mw = max(0.0, cons_mw - prod_mw)
        sev = max(0.0, 1 - sat) * 100
        if sat >= cfg["power_warn"]:
            sev = max(sev, (cfg["power_headroom_warn"] - headroom)
                      / cfg["power_headroom_warn"] * 55)
        add(Task(
            kind="power",
            title="扩建发电：电网余量不足",
            detail=(f"发电 {prod_mw:,.1f} MW / 用电 {cons_mw:,.1f} MW，"
                    f"满足率 {sat*100:.1f}%，余量 {headroom*100:.1f}%，"
                    ""
                    + (f" 缺口 {deficit_mw:,.1f} MW。" if deficit_mw > 0 else "")),
            action=f"新增约 {deficit_mw*1.25:,.1f} MW 装机（含 25% 余量），优先补被限速的机组",
            priority=min(100.0, 65.0 + (1 - sat) * 300),
            target="electricity",
            delta_per_min=deficit_mw,
            evidence={"produced_mw": prod_mw, "consumed_mw": cons_mw, "satisfaction": sat},
        ))

    # ---------- 2. 科研瓶颈 ----------
    rt = snap.research_throughput()
    cur = snap.research.get("current")
    if cur and rt["packs"]:
        target = cfg["target_science_per_min"]
        tp = rt["units_per_min"]
        rem = snap.research.get("unit_count", 0) * (1 - snap.research.get("progress", 0))
        eta = rem / tp if tp > 1e-6 else float("inf")
        if tp < target:
            sev = min(1.0, (target - tp) / target)
            bottleneck = rt["bottleneck"]
            need = rt["packs"][bottleneck]["need"]
            names = [f"{p} {_fmt(v['rate_per_min'], '/min')}（每单位 {v['need']:g}）"
                     for p, v in rt["packs"].items()]
            delta = max(0.0, (target - tp) * need)
            mm, mtext = _machines_for(graph, machines, bottleneck, delta)
            add(Task(
                kind="science",
                title=f"科研卡在 {bottleneck}",
                detail=(f"在研 {cur}（{snap.research.get('progress', 0)*100:.1f}%，"
                        f"剩 {_fmt(rem, ' 单位')}），实际吞吐 {tp:.2f} unit/min，"
                        f"按此速度还需 {_fmt_eta(eta)}。科研包：{'；'.join(names)}。"),
                action=f"把 {bottleneck} 提到 {_fmt(target*need, '/min')}"
                       f"（新增 {_fmt(delta, '/min')}）"
                       + (f"；{mtext}" if mtext else ""),
                priority=min(100.0, 58 + sev * 38),
                target=bottleneck,
                delta_per_min=delta,
                evidence={"units_per_min": tp, "eta_min": eta},
                machines=mm,
            ))
            occupied.add(bottleneck)

    # ---------- 3. 库存告急 ----------
    # 注意：引擎只给「净累积」不给真实库存，所以判据只用供给率，不用"可撑几分钟"
    for name in snap.tracked_names():
        if name in occupied:
            continue
        d = snap.in_rate(name, "h10")
        if d < cfg["stock_min_demand"]:
            continue
        ratio = snap.supply_ratio(name)
        if ratio >= cfg["ratio_warn"]:
            continue
        if not _should_report_stock(snap, graph, name, cfg):
            continue
        slope = snap.stock_slope(name)
        if ratio >= 1.0 and slope >= 0:
            continue
        sev = max(0.0, 1 - min(ratio, 1.0))
        if sev < 0.08:
            continue
        delta = max(0.0, d - snap.out_rate(name, "h10"))
        mm, mtext = _machines_for(graph, machines, name, delta)
        add(Task(
            kind="stock",
            title=f"{name} 供给不足",
            detail=(f"消耗 {_fmt(d, '/min')}，产出 {_fmt(snap.out_rate(name, 'h10'), '/min')}，"
                    f"供给率 {ratio*100:.0f}%，长期净累积 {_fmt(snap.stock(name))}。"),
            action=f"补 {_fmt(delta, '/min')} 产能" + (f"；{mtext}" if mtext else ""),
            priority=min(100.0, 28 + sev * 50),
            target=name,
            delta_per_min=delta,
            evidence={"ratio": ratio, "stock": snap.stock(name), "slope": slope},
            machines=mm,
        ))

    # ---------- 4. 上游归因 ----------
    ranked = sorted(
        ((n, snap.supply_ratio(n)) for n in snap.tracked_names()
         if snap.in_rate(n, "h10") >= cfg["min_demand"] * 4),
        key=lambda x: x[1],
    )[:10]
    for name, ratio in ranked:
        if ratio >= cfg["ratio_warn"]:
            continue
        blame = _upstream_blame(snap, graph, name, cfg)
        if not blame:
            continue
        b_item, b_ratio, path = blame
        if b_item == name or b_ratio >= cfg["ratio_warn"] or b_item in occupied:
            continue
        delta = max(0.0, snap.in_rate(b_item, "h10") - snap.out_rate(b_item, "h10"))
        mm, mtext = _machines_for(graph, machines, b_item, delta)
        add(Task(
            kind="upstream",
            title=f"根因在 {b_item}（{name} 缺料的源头）",
            detail=(f"追溯 {' → '.join(path)}。{b_item} 供给率 {b_ratio*100:.0f}%"
                    f"（产 {_fmt(snap.out_rate(b_item, '/min'), '/min')} / "
                    f"耗 {_fmt(snap.in_rate(b_item, '/min'), '/min')}）。"),
            action=f"先拉 {b_item}，{name} 会自动缓解" + (f"；{mtext}" if mtext else ""),
            priority=min(100.0, 33 + (1 - min(b_ratio, 1)) * 48),
            target=b_item,
            delta_per_min=delta,
            evidence={"path": path, "ratio": b_ratio},
            machines=mm,
        ))

    # ---------- 5. 科技建议 ----------
    avail = snap.techs.get("available") or []
    if avail:
        def tech_cost(t):
            return max(1e-6, t["count"] * max(t["energy"], 1) * max(t.get("time", 1), 1))
        cheapest = sorted(avail, key=tech_cost)[: cfg["tech_lookahead"]]
        for i, t in enumerate(cheapest[:3]):
            unlock = graph.techs.get(t["name"], {}).get("unlocks") or []
            if not unlock:
                continue
            add(Task(
                kind="tech",
                title=f"可研：{t['name']}（解锁 {len(unlock)} 个配方）",
                detail=(f"成本 {t['count']}×{t.get('time', 1)}s，"
                        f"每单位 {t['energy']:g}J。解锁："
                        f"{'、'.join(unlock[:5])}{' 等' if len(unlock) > 5 else ''}。"),
                action="排队研究",
                priority=max(10.0, 40 - i * 7 - min(8, len(unlock))),
                target=t["name"],
                evidence={"cost": tech_cost(t), "unlocks": unlock[:8]},
            ))

    # ---------- 排序 + 类别配额 ----------
    tasks.sort(key=lambda t: -t.priority)
    forced = [t for t in tasks if t.kind in ("power", "science")][:2]
    out: list[Task] = list(forced)
    counts = {t.kind: 1 for t in forced}
    cap = cfg["same_kind_cap"]
    for t in tasks:
        if len(out) >= 10:
            break
        if t in out:
            continue
        if counts.get(t.kind, 0) >= cap:
            continue
        counts[t.kind] = counts.get(t.kind, 0) + 1
        out.append(t)
    out.sort(key=lambda t: -t.priority)
    return out
