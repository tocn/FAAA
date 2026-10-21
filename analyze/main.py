"""命令行入口。

    python -m analyze.main report --stats py-stats.json --data data --out out
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os

from . import score as scoring
from . import emit
from .model import Graph, Snapshot


def run_report(args) -> int:
    graph = Graph(args.data)
    snap = Snapshot.load(args.stats, graph)
    machines = None
    mp = os.path.join(args.data, "machines.json")
    if os.path.exists(mp):
        with open(mp, "r", encoding="utf-8") as f:
            machines = json.load(f)

    cfg = scoring.DEFAULT_CFG
    tasks = [t.as_dict() for t in scoring.generate(snap, graph, machines, cfg)]

    os.makedirs(args.out, exist_ok=True)
    meta = {
        "tick": snap.tick,
        "version": snap.raw.get("game_version", "?"),
        "save": ("【演示数据】" if "demo" in os.path.basename(args.stats).lower()
                 else "") + os.path.basename(args.stats),
        "time": dt.datetime.now().strftime("%Y-%m-%d %H:%M"),
    }

    p_html = os.path.join(args.out, "report.html")
    with open(p_html, "w", encoding="utf-8") as f:
        f.write(emit.render_html(snap, tasks, cfg, meta))

    p_csv = os.path.join(args.out, "plan.csv")
    emit.write_plan_csv(tasks, p_csv)

    p_todo = os.path.join(args.out, "todo-string.txt")
    with open(p_todo, "w", encoding="utf-8") as f:
        f.write(emit.todo_string(tasks, args.top))

    p_json = os.path.join(args.out, "tasks.json")
    with open(p_json, "w", encoding="utf-8") as f:
        json.dump(tasks, f, ensure_ascii=False, indent=2)

    if args.yafc:
        emit.write_yafc_stub(tasks, os.path.join(args.out, "yafc-pages.yafc"))

    # 控制台摘要
    pt = snap.power_total
    rt = snap.research_throughput()
    print("=" * 78)
    print(f"tick {snap.tick:,}  |  发电 {pt.get('produced',0)/1e6:,.1f} MW  "
          f"用电 {pt.get('consumed',0)/1e6:,.1f} MW  满足率 {pt.get('satisfaction',1)*100:.1f}%")
    print(f"在研 {snap.research.get('current')}  进度 {snap.research.get('progress',0)*100:.1f}%  "
          f"吞吐 {rt['units_per_min']:.2f} unit/min  瓶颈包 {rt['bottleneck']}")
    print("=" * 78)
    for i, t in enumerate(tasks[: args.top], 1):
        print(f"{i:2d}. [{t['priority']:5.1f}] {t['kind']:<9} {t['title']}")
        print(f"      {t['detail']}")
        if t.get("action"):
            print(f"      → {t['action']}")
    print()
    print(f"报告    {p_html}")
    print(f"计划表  {p_csv}")
    print(f"任务    {p_json}")
    print(f"Todo 串 {p_todo}  （游戏内 Todo-List → 导入，粘贴此内容）")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="py-analyzer")
    sub = ap.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("report", help="生成诊断报告与优先任务")
    r.add_argument("--stats", default="py-stats.json")
    r.add_argument("--data", default="data")
    r.add_argument("--out", default="out")
    r.add_argument("--top", type=int, default=10)
    r.add_argument("--yafc", action="store_true", help="额外生成 YAFC 工程骨架")
    r.set_defaults(func=run_report)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
