"""把 agent 修正后的 final.json 变成游戏里能执行的东西。

    python -m analyze.apply --final out/run/final.json --out out/run
    python -m analyze.apply --final out/run/final.json --rcon 127.0.0.1:34197 --password xxx

产物：
    push-command.txt   一行 /sc 命令，游戏控制台粘贴即用
    final.md           人看的最终清单
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import struct
import sys
import time

from . import fstring

MAX_TASKS = 10


def load_final(path: str) -> list[dict]:
    with open(path, "r", encoding="utf-8") as f:
        doc = json.load(f)
    tasks = doc["tasks"] if isinstance(doc, dict) else doc
    if not isinstance(tasks, list):
        raise ValueError("final.json 应为数组，或含 tasks 数组的对象")
    clean = []
    for t in tasks[:MAX_TASKS]:
        title = (t.get("title") or "").strip()
        if not title:
            continue
        clean.append({
            "title": title[:200],
            "task": (t.get("task") or t.get("detail") or "").strip()[:1000],
            "assignee": t.get("assignee"),
            "created_by": t.get("created_by") or "py-analyzer",
        })
    return clean


def build_command(tasks: list[dict]) -> str:
    encoded = fstring.encode_json(tasks)
    return ('/sc remote.call("py-bridge-todo","push",'
            'helpers.json_to_table(helpers.decode_string("%s")))' % encoded)


def render_markdown(tasks: list[dict], meta: dict | None = None) -> str:
    lines = ["# 本次推送的待办", ""]
    if meta:
        for k, v in meta.items():
            lines.append(f"- {k}: {v}")
        lines.append("")
    for i, t in enumerate(tasks, 1):
        lines.append(f"{i}. **{t['title']}**")
        if t.get("task"):
            lines.append(f"   {t['task']}")
    return "\n".join(lines) + "\n"


# ---------------- RCON（可选，需要游戏以 --rcon-port 启动） ----------------
class Rcon:
    SERVERDATA_AUTH = 3
    SERVERDATA_EXECCOMMAND = 2

    def __init__(self, host: str, port: int, password: str, timeout: float = 10.0):
        self.sock = socket.create_connection((host, port), timeout=timeout)
        self.sock.settimeout(timeout)
        self._send(self.SERVERDATA_AUTH, password)
        _ = self._recv()          # 空包
        resp = self._recv()
        if not resp or resp[0] == -1:
            raise RuntimeError("RCON 认证失败")

    def _send(self, ptype: int, body: str) -> None:
        data = body.encode("utf-8") + b"\x00\x00"
        pkt = struct.pack("<iii", 8 + len(data), 1, ptype) + data
        self.sock.sendall(pkt)

    def _recv(self):
        head = self.sock.recv(4)
        if len(head) < 4:
            return None
        size = struct.unpack("<i", head)[0]
        body = b""
        while len(body) < size:
            chunk = self.sock.recv(size - len(body))
            if not chunk:
                break
            body += chunk
        rid, ptype = struct.unpack("<ii", body[:8])
        return rid, ptype, body[8:-2].decode("utf-8", errors="replace")

    def command(self, cmd: str) -> str:
        self._send(self.SERVERDATA_EXECCOMMAND, cmd)
        time.sleep(0.2)
        resp = self._recv()
        return resp[2] if resp else ""

    def close(self) -> None:
        try:
            self.sock.close()
        except OSError:
            pass


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="py-analyzer apply")
    ap.add_argument("--final", required=True)
    ap.add_argument("--out", default=None)
    ap.add_argument("--rcon", default=None, help="host:port")
    ap.add_argument("--password", default="")
    args = ap.parse_args(argv)

    tasks = load_final(args.final)
    if not tasks:
        print("[apply] final.json 里没有有效任务", file=sys.stderr)
        return 2

    out_dir = args.out or os.path.dirname(os.path.abspath(args.final))
    os.makedirs(out_dir, exist_ok=True)

    cmd = build_command(tasks)
    p_cmd = os.path.join(out_dir, "push-command.txt")
    with open(p_cmd, "w", encoding="utf-8") as f:
        f.write(cmd + "\n")

    p_md = os.path.join(out_dir, "final.md")
    with open(p_md, "w", encoding="utf-8") as f:
        f.write(render_markdown(tasks, {"来源": args.final, "条数": len(tasks)}))

    print(f"[apply] {len(tasks)} 条任务 → {p_cmd}  ({len(cmd)} 字符)")
    print(f"[apply] {p_md}")

    if args.rcon:
        host, _, port = args.rcon.partition(":")
        r = Rcon(host, int(port or 34197), args.password)
        try:
            print("[apply] rcon:", r.command(cmd))
        finally:
            r.close()
    else:
        print("[apply] 在游戏控制台粘贴 push-command.txt 的内容（需 --instrument-mod py-task-bridge 启动）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
