"""一条命令跑完整链路：存档 → 快照 → 候选任务清单。

    python -m analyze.pipeline --save "<存档.zip>" --out out/run1
    python -m analyze.pipeline --stats py-stats.json --out out/run1   # 跳过加载

产物：
    <out>/py-stats.json      快照原件（留档）
    <out>/candidates.json    候选任务（供 agent 二次分析）
    <out>/report.html        人看的报表
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import shutil
import subprocess
import sys
import time

from . import score as scoring
from .emit import render_html
from .model import Graph, Snapshot

FACTORIO_DEFAULT = r"S:/SteamLibrary/steamapps/common/Factorio/bin/x64/factorio.exe"
MODS_DEFAULT = r"C:/Users/Administrator/AppData/Roaming/Factorio/mods"
OUTPUT_DIR_DEFAULT = r"C:/Users/Administrator/AppData/Roaming/Factorio/script-output"


def run_factorio(save: str, factorio: str, mods: str, ticks: int,
                 instrument: str | None, timeout: int = 900) -> str:
    """无头加载存档并导出快照，返回 snapshot 路径。"""
    out_dir = os.path.abspath(os.path.join(os.environ.get("APPDATA", ""), "Factorio", "script-output"))
    snap_path = os.path.join(out_dir, "py-stats.json")
    if os.path.exists(snap_path):
        os.remove(snap_path)

    cmd = [factorio, "--benchmark", save,
           "--benchmark-ticks", str(ticks),
           "--mod-directory", mods,
           "--disable-audio"]
    if instrument:
        cmd += ["--instrument-mod", instrument]

    t0 = time.time()
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                          encoding="utf-8", errors="replace")
    elapsed = time.time() - t0
    log_blob = (proc.stdout or "") + (proc.stderr or "")

    if not os.path.exists(snap_path):
        tail = "\n".join(log_blob.strip().splitlines()[-25:])
        raise RuntimeError(f"快照未生成（{elapsed:.0f}s），factorio 末段输出:\n{tail}")
    print(f"[pipeline] 加载 {elapsed:.0f}s → {snap_path}")
    return snap_path


def build_candidates(snap: Snapshot, graph: Graph, machines, cfg: dict) -> list[dict]:
    """把评分结果转成供 agent 复核的候选，附带可疑标记。"""
    tasks = [t.as_dict() for t in scoring.generate(snap, graph, machines, cfg)]
    enabled = set((snap.raw.get("recipes_enabled") or []))

    out = []
    for i, t in enumerate(tasks, 1):
        target = t.get("target")
        is_tech = t["kind"] == "tech"
        is_resource = bool(target and (graph.items.get(target) or {}).get("resource"))
        rn = None
        rec = None
        cands = []
        if target and not is_tech:
            rn = graph.best_recipe(target)
            rec = graph.recipes.get(rn) if rn else None
            cands = (graph.producers.get(target) or [])[:12]

        flags = []
        if is_resource:
            flags.append("is_resource")
        if target and not is_tech and not is_resource and rn is None and t["kind"] in ("stock", "science", "upstream"):
            flags.append("no_recipe")
        if rn and enabled and rn not in enabled:
            flags.append("recipe_locked")
        if rec:
            if rec["t"] > 120:
                flags.append("slow_recipe")
            if (rec["res"].get(target) or 0) > 500:
                flags.append("huge_batch")
            if rec["hid"]:
                flags.append("hidden_recipe")
        if t["kind"] == "power" and t.get("delta_per_min", 0) > 500:
            flags.append("implausible_mw")
        if t["kind"] == "stock" and t.get("delta_per_min", 0) > 1e4:
            flags.append("implausible_rate")

        out.append({
            "id": f"c{i:02d}",
            "kind": t["kind"],
            "priority": round(t["priority"], 1),
            "title": t["title"],
            "detail": t["detail"],
            "action": t["action"],
            "target": target,
            "delta_per_min": round(t.get("delta_per_min", 0), 2),
            "recipe": rn,
            "recipe_candidates": cands,
            "machines_est": t.get("evidence", {}).get("machines", ""),
            "machine_names": t.get("machines") or [],
            "evidence": t.get("evidence") or {},
            "flags": flags,
        })
    return out


def run(args) -> int:
    graph = Graph(args.data)
    machines = None
    mp = os.path.join(args.data, "machines.json")
    if os.path.exists(mp):
        with open(mp, "r", encoding="utf-8") as f:
            machines = json.load(f)

    os.makedirs(args.out, exist_ok=True)

    if args.save:
        snap_path = run_factorio(args.save, args.factorio, args.mods, args.ticks,
                                 None if args.no_instrument else args.instrument)
    else:
        snap_path = args.stats
        if not snap_path or not os.path.exists(snap_path):
            print("[pipeline] 需要 --save 或 --stats", file=sys.stderr)
            return 2

    local_snap = os.path.join(args.out, "py-stats.json")
    if os.path.abspath(snap_path) != os.path.abspath(local_snap):
        shutil.copy(snap_path, local_snap)

    snap = Snapshot.load(local_snap, graph)
    cfg = scoring.DEFAULT_CFG
    cands = build_candidates(snap, graph, machines, cfg)

    pt = snap.power_total
    meta = {
        "tick": snap.tick,
        "version": snap.raw.get("game_version", "?"),
        "save": os.path.basename(args.save or args.stats),
        "time": dt.datetime.now().strftime("%Y-%m-%d %H:%M"),
    }
    doc = {
        "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
        "save": meta["save"],
        "tick": snap.tick,
        "game_version": meta["version"],
        "instrument": bool(snap.raw.get("instrument")),
        "power": {
            "produced_mw": round(pt.get("produced", 0) / 1e6, 2),
            "consumed_mw": round(pt.get("consumed", 0) / 1e6, 2),
            "satisfaction": round(pt.get("satisfaction", 1), 4),
        },
        "research": {
            "current": snap.research.get("current"),
            "progress": round(snap.research.get("progress", 0), 4),
            "throughput": round(snap.research_throughput()["units_per_min"], 3),
        },
        "counts": {
            "item_flows": len(snap.items),
            "fluid_flows": len(snap.fluids),
            "researched": snap.techs.get("researched", 0),
            "available": len(snap.techs.get("available") or []),
            "recipes_enabled": len(snap.raw.get("recipes_enabled") or []),
        },
        "warnings": snap.raw.get("errors") or [],
        "candidates": cands,
    }
    p_cand = os.path.join(args.out, "candidates.json")
    with open(p_cand, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=2)

    p_html = os.path.join(args.out, "report.html")
    with open(p_html, "w", encoding="utf-8") as f:
        f.write(render_html(snap, cands, cfg, meta))

    flagged = sum(1 for c in cands if c["flags"])
    print(f"[pipeline] 候选 {len(cands)} 条，其中 {flagged} 条带可疑标记")
    print(f"[pipeline] {p_cand}")
    print(f"[pipeline] {p_html}")

    if len(snap.items) == 0 and len(snap.fluids) == 0:
        print("[pipeline] 注意：快照里没有任何产量数据，可能是新开局或加载失败")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="py-analyzer pipeline")
    ap.add_argument("--save", help="存档 zip；不给则必须给 --stats")
    ap.add_argument("--stats", help="已有快照，跳过无头加载")
    ap.add_argument("--data", default="data")
    ap.add_argument("--out", default="out/run")
    ap.add_argument("--ticks", type=int, default=400)
    ap.add_argument("--factorio", default=FACTORIO_DEFAULT)
    ap.add_argument("--mods", default=MODS_DEFAULT)
    ap.add_argument("--instrument", default="py-task-bridge")
    ap.add_argument("--no-instrument", action="store_true")
    args = ap.parse_args(argv)
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
