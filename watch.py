"""FAAA watcher —— 游戏运行时监视自动存档，出现新档就分析。

    python watch.py                 # 监视 saves 目录的 _autosave*.zip（默认）
    python watch.py --source live   # 改为监视 script-output/py-stats.json（零开销）
    python watch.py --once          # 只处理当前最新 autosave 一次就退出
    python watch.py --only-if-game  # 游戏没在跑就退出

设计要点：
  * autosave 会轮转（_autosave1/2/3），分析前必须先复制一份，否则会读到一半被覆盖
  * 写入中的 zip 不可读，要等「大小连续两次不变 + level.dat 能读出来」才算写完
  * 同一份档用 (name, mtime, size) 去重
  * autosave 模式下每次分析都要另起 factorio 进程，占用明显；live 模式靠游戏内 mod 导出，几乎零成本
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import zipfile
from pathlib import Path

APPDATA = os.environ.get("APPDATA", "")
SAVES = Path(APPDATA) / "Factorio" / "saves"
SCRIPT_OUT = Path(APPDATA) / "Factorio" / "script-output"
HERE = Path(__file__).resolve().parent
OUT = HERE / "out" / "watch"
STATE = OUT / ".watched.json"
PY = sys.executable

POLL = 5.0
STABLE_ROUNDS = 2


# ---------------- 存档就绪判断 ----------------
def zip_ready(path: Path) -> bool:
    """zip 结构完整且能读出 level.dat，才算游戏写完。"""
    try:
        with zipfile.ZipFile(path) as z:
            names = z.namelist()
            target = None
            for n in names:
                base = n.rsplit("/", 1)[-1]
                if base in ("level.dat", "level.dat0"):
                    target = n
                    break
            if target is None:
                return False
            z.read(target)
            return True
    except (zipfile.BadZipFile, OSError, RuntimeError):
        return False


def pick_autosave() -> Path | None:
    if not SAVES.exists():
        return None
    cands = [p for p in SAVES.glob("_autosave*.zip")]
    if not cands:
        return None
    return max(cands, key=lambda p: p.stat().st_mtime)


def sig(path: Path) -> str:
    st = path.stat()
    return f"{path.name}:{int(st.st_mtime)}:{st.st_size}"


def game_running() -> bool:
    try:
        r = subprocess.run(["tasklist", "/FI", "IMAGENAME eq factorio.exe"],
                           capture_output=True, text=True, timeout=10)
        return "factorio.exe" in (r.stdout or "").lower()
    except (OSError, subprocess.SubprocessError):
        return False


# ---------------- 处理 ----------------
def copy_snapshot(src: Path) -> Path:
    """复制一份再分析，避免轮到下一个 autosave 时文件被覆盖。"""
    OUT.mkdir(parents=True, exist_ok=True)
    dst = OUT / f"snap-{time.strftime('%Y%m%d-%H%M%S')}.zip"
    shutil.copy2(src, dst)
    return dst


def run_pipeline(save: Path, outdir: Path) -> bool:
    cmd = [PY, "-m", "analyze.pipeline", "--save", str(save), "--out", str(outdir)]
    proc = subprocess.run(cmd, cwd=str(HERE))
    return proc.returncode == 0


def write_latest(outdir: Path, save_name: str) -> None:
    """把最新一轮的候选摘要写成人看的 markdown。"""
    cand_path = outdir / "candidates.json"
    if not cand_path.exists():
        return
    doc = json.loads(cand_path.read_text(encoding="utf-8"))
    lines = [
        f"# 最新一次诊断（{time.strftime('%Y-%m-%d %H:%M')}）",
        "",
        f"- 存档：{save_name}",
        f"- tick：{doc.get('tick')}",
        f"- 电力：{doc.get('power', {}).get('produced_mw')} MW 发电 / "
        f"{doc.get('power', {}).get('consumed_mw')} MW 用电 "
        f"（满足率 {doc.get('power', {}).get('satisfaction')}）",
        f"- 在研：{doc.get('research', {}).get('current')}",
        "",
        "## 候选",
        "",
    ]
    for c in doc.get("candidates") or []:
        flag = f"  `{','.join(c['flags'])}`" if c.get("flags") else ""
        lines.append(f"{c['id']}. **[{c['kind']}]** {c['title']}（{c['priority']}）{flag}")
        lines.append(f"   - {c['detail']}")
    lines.append("")
    lines.append("> 复核后运行 `python -m analyze.apply --final <dir>/final.json --out <dir>` 生成游戏内命令。")
    (OUT / "latest.md").write_text("\n".join(lines), encoding="utf-8")


def process(save: Path, outdir: Path) -> None:
    print(f"[watch] 发现新存档 {save.name}，复制快照…", flush=True)
    snap = copy_snapshot(save)
    print(f"[watch] 分析 {snap.name}（Py 存档约需 40–60s）", flush=True)
    ok = run_pipeline(snap, outdir)
    if not ok:
        print("[watch] 分析失败，跳过这一份", flush=True)
        (outdir / "FAILED.txt").write_text(f"{save.name}\n{time.ctime()}\n", encoding="utf-8")
        return
    write_latest(outdir, save.name)
    print(f"[watch] 完成 → {outdir}", flush=True)


# ---------------- 主循环 ----------------
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", choices=["live", "autosave"], default="live")
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--only-if-game", action="store_true")
    ap.add_argument("--poll", type=float, default=POLL)
    args = ap.parse_args()

    if args.only_if_game and not game_running():
        print("[watch] 游戏没在运行，退出")
        return 0
    if args.only_if_game:
        print("[watch] 检测到游戏正在运行")

    OUT.mkdir(parents=True, exist_ok=True)
    seen: set[str] = set()
    if STATE.exists():
        try:
            seen = set(json.loads(STATE.read_text(encoding="utf-8")).get("seen") or [])
        except (OSError, ValueError):
            seen = set()

    def persist():
        STATE.write_text(json.dumps({"seen": sorted(seen)[-200:]}, ensure_ascii=False), encoding="utf-8")

    if args.source == "live":
        print("[watch] live 模式：监视 script-output/py-stats.json（需游戏内 mod 启用 py-stats-auto）")
        target = SCRIPT_OUT / "py-stats.json"
        last = None
        while True:
            if target.exists():
                cur = sig(target)
                if cur != last and zip_ready_safe(target):
                    outdir = OUT / f"live-{time.strftime('%Y%m%d-%H%M%S')}"
                    outdir.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(target, outdir / "py-stats.json")
                    subprocess.run([PY, "-m", "analyze.pipeline",
                                    "--stats", str(outdir / "py-stats.json"),
                                    "--out", str(outdir)], cwd=str(HERE))
                    write_latest(outdir, target.name)
                    last = cur
            if args.once:
                return 0
            time.sleep(args.poll)

    print("[watch] autosave 模式：监视", SAVES)
    prev_size = None
    stable_hits = 0
    while True:
        save = pick_autosave()
        if save is None:
            if args.once:
                print("[watch] 没有 autosave 文件")
                return 0
            time.sleep(args.poll)
            continue

        key = sig(save)
        if key in seen:
            if args.once:
                print(f"[watch] 最新存档已处理过：{save.name}")
                return 0
            prev_size, stable_hits = None, 0
            time.sleep(args.poll)
            continue

        size = save.stat().st_size
        if prev_size is not None and size == prev_size:
            stable_hits += 1
        else:
            stable_hits = 0
        prev_size = size

        if stable_hits >= STABLE_ROUNDS - 1 and zip_ready(save):
            outdir = OUT / f"auto-{time.strftime('%Y%m%d-%H%M%S')}"
            outdir.mkdir(parents=True, exist_ok=True)
            process(save, outdir)
            seen.add(key)
            persist()
            if args.once:
                return 0
            prev_size, stable_hits = None, 0
        time.sleep(args.poll)


def zip_ready_safe(path: Path) -> bool:
    """live 模式下目标不是 zip，只检查文件非空且不是正在写入。"""
    try:
        return path.stat().st_size > 1024
    except OSError:
        return False


if __name__ == "__main__":
    raise SystemExit(main())
