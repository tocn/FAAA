"""输出：Todo-List 导入串 / HTML 报表 / CSV 计划表 / YAFC 工程片段。"""
from __future__ import annotations

import csv
import html
import json
import os

from . import fstring


# ---------------- Todo-List ----------------
def todo_tasks(tasks: list[dict], limit: int = 10) -> list[dict]:
    """转成 Todo-List mod 的 import 格式。"""
    out = []
    for i, t in enumerate(tasks[:limit], 1):
        detail = t["detail"]
        if t.get("action"):
            detail += " ｜ 建议：" + t["action"]
        out.append({
            "title": f"[{i:02d}] {t['title']}",
            "task": detail,
            "assignee": None,
            "created_by": "py-analyzer",
        })
    return out


def todo_string(tasks: list[dict], limit: int = 10) -> str:
    return fstring.encode_json(todo_tasks(tasks, limit))


# ---------------- HTML ----------------
CSS = """
:root{--bg:#f7f8fa;--card:#fff;--ink:#1c2024;--mute:#6b7280;--line:#e5e7eb;
--red:#d92b2b;--amber:#c77700;--green:#118a4b;--blue:#1f6feb;}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
font:14px/1.6 "Segoe UI","Microsoft YaHei",system-ui,sans-serif}
.wrap{max-width:1080px;margin:0 auto;padding:28px 20px 60px}
h1{font-size:22px;margin:0 0 4px}
.sub{color:var(--mute);font-size:13px;margin-bottom:22px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:12px;margin-bottom:24px}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px 16px}
.k{color:var(--mute);font-size:12px;letter-spacing:.02em}
.v{font-size:22px;font-weight:600;margin-top:2px}
.v small{font-size:12px;color:var(--mute);font-weight:400}
h2{font-size:16px;margin:28px 0 10px;padding-bottom:6px;border-bottom:1px solid var(--line)}
table{width:100%;border-collapse:collapse;background:var(--card);
border:1px solid var(--line);border-radius:10px;overflow:hidden}
th,td{padding:9px 12px;text-align:left;vertical-align:top;font-size:13px;
border-bottom:1px solid var(--line)}
th{background:#fafbfc;color:var(--mute);font-weight:600;font-size:12px}
tr:last-child td{border-bottom:none}
td.num{font-variant-numeric:tabular-nums;white-space:nowrap}
.tag{display:inline-block;padding:1px 7px;border-radius:99px;font-size:11px;
border:1px solid var(--line);background:#f3f4f6;color:var(--mute)}
.p-high{color:var(--red);font-weight:600}
.p-mid{color:var(--amber);font-weight:600}
.p-low{color:var(--mute)}
.bar{height:6px;background:#eef0f3;border-radius:99px;overflow:hidden;margin-top:5px}
.bar>i{display:block;height:100%;background:var(--blue)}
code{background:#f1f2f4;padding:1px 5px;border-radius:4px;font-size:12px}
.foot{color:var(--mute);font-size:12px;margin-top:26px}
"""


def _prio_cls(p: float) -> str:
    return "p-high" if p >= 80 else ("p-mid" if p >= 55 else "p-low")


def render_html(snap, tasks: list[dict], cfg: dict, meta: dict) -> str:
    pt = snap.power_total
    prod_mw = pt.get("produced", 0) / 1e6
    cons_mw = pt.get("consumed", 0) / 1e6
    sat = pt.get("satisfaction", 1) * 100
    rt = snap.research_throughput()

    def esc(s):
        return html.escape(str(s))

    tiles = [
        ("发电", f"{prod_mw:,.1f}<small> MW</small>"),
        ("用电", f"{cons_mw:,.1f}<small> MW</small>"),
        ("电力满足率", f"{sat:.1f}<small> %</small>"),
        ("科研吞吐", f"{rt['units_per_min']:.2f}<small> unit/min</small>"),
        ("在研科技", f"{esc(snap.research.get('current') or '—')}"),
        ("已完成科技", f"{snap.techs.get('researched', 0)}<small> / "
                      f"{snap.techs.get('researched', 0) + len(snap.techs.get('available') or []) + snap.techs.get('locked', 0)}</small>"),
    ]
    tile_html = "".join(
        f'<div class="card"><div class="k">{k}</div><div class="v">{v}</div></div>' for k, v in tiles
    )

    rows = []
    for i, t in enumerate(tasks[:10], 1):
        rows.append(f"""<tr>
<td class="num">{i}</td>
<td><span class="tag">{esc(t['kind'])}</span><br><b>{esc(t['title'])}</b></td>
<td>{esc(t['detail'])}<br><span class="k">→ {esc(t.get('action',''))}</span></td>
<td class="num"><span class="{_prio_cls(t['priority'])}">{t['priority']:.0f}</span>
<div class="bar"><i style="width:{min(100,t['priority']):.0f}%"></i></div></td>
</tr>""")

    # 瓶颈物品表
    bn = []
    for name in snap.tracked_names():
        d = snap.in_rate(name, "h10")
        if d < cfg.get("min_demand", 0.5):
            continue
        r = snap.supply_ratio(name)
        if r == float("inf"):
            continue
        bn.append((name, d, snap.out_rate(name, "h10"), r, snap.stock(name), snap.cover_minutes(name)))
    bn.sort(key=lambda x: x[3])
    bn_rows = "".join(
        f"""<tr><td><code>{esc(n)}</code></td><td class="num">{d:,.1f}</td>
<td class="num">{o:,.1f}</td>
<td class="num {'p-high' if r<0.9 else ''}">{r*100:.0f}%</td>
<td class="num">{s:,.0f}</td>
<td class="num">{('∞' if c==float('inf') else f'{c:,.0f}')}</td></tr>"""
        for n, d, o, r, s, c in bn[:25]
    )

    return f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<title>Pyanodons 工厂诊断</title><style>{CSS}</style></head>
<body><div class="wrap">
<h1>Pyanodons 工厂诊断</h1>
<div class="sub">tick {meta.get('tick', 0):,} ｜ 游戏 {esc(meta.get('version','?'))} ｜
存档 {esc(meta.get('save','?'))} ｜ 生成于 {esc(meta.get('time',''))}</div>

<div class="grid">{tile_html}</div>

<h2>优先十项</h2>
<table><thead><tr><th style="width:36px">#</th><th style="width:30%">任务</th>
<th>依据</th><th style="width:96px">优先级</th></tr></thead>
<tbody>{''.join(rows)}</tbody></table>

<h2>供给最紧的物品（按 产出/消耗 升序）</h2>
<table><thead><tr><th>物品</th><th>消耗 /min</th><th>产出 /min</th>
<th>供给率</th><th>库存</th><th>可撑分钟</th></tr></thead>
<tbody>{bn_rows}</tbody></table>

<div class="foot">由 py-analyzer 生成。数据源：游戏内 mod 导出（py-stats.json）+ 全量配方 dump。
建议把「优先十项」通过 Todo-List 的导入字符串一次性灌进游戏。</div>
</div></body></html>"""


# ---------------- CSV ----------------
def write_plan_csv(tasks: list[dict], path: str) -> None:
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["优先级", "类型", "任务", "目标", "建议新增 /min", "依据", "可用机器"])
        for t in tasks:
            w.writerow([
                f"{t['priority']:.0f}", t["kind"], t["title"], t.get("target") or "",
                f"{t.get('delta_per_min', 0):.1f}", t["detail"], " / ".join(t.get("machines") or []),
            ])


# ---------------- YAFC ----------------
YAFC_TEMPLATE_NOTE = """\
YAFC 工程文件是纯 JSON，理论上可以直接生成 production table 让 YAFC 求解。
但 RecipeRow 的序列化字段需要一次真实样例标定：
  1) 在 YAFC 里新建一页，加 1 个配方，填固定速率，保存工程
  2) 把那个 .yafc 文件发给分析器，反推 schema
在那之前，用 plan.csv 手工录入，或直接看 report.html。
"""


def write_yafc_stub(tasks: list[dict], path: str) -> None:
    pages = []
    for i, t in enumerate(tasks[:10], 1):
        pages.append({
            "contentType": "Yafc.Model.ProductionTable",
            "guid": f"{i:032x}",
            "icon": None,
            "name": f"{i:02d} {t['title']}"[:60],
            "content": {"expanded": True, "links": [], "recipes": [], "modules": {
                "fillMiners": False, "autoFillPayback": 0, "fillerModule": None,
                "beacon": None, "beaconModule": None, "beaconsPerBuilding": 8,
                "overrideCrafterBeacons": {},
            }},
            "scroll": None,
        })
    doc = {
        "settings": {"milestones": [], "itemFlags": {}, "miningProductivity": 0,
                     "researchSpeedBonus": 0, "researchProductivity": 0,
                     "productivityTechnologyLevels": {}, "reactorSizeX": 2, "reactorSizeY": 2,
                     "PollutionCostModifier": 0, "spoilingRate": 1},
        "preferences": {"time": 1, "itemUnit": 0, "fluidUnit": 0, "defaultBelt": None,
                        "defaultInserter": None, "inserterCapacity": 1, "sourceResources": [],
                        "favorites": [], "targetTechnology": None, "iconScale": 0.9,
                        "maxMilestonesPerTooltipLine": 28, "showMilestoneOnInaccessible": True},
        "sharedModuleTemplates": [],
        "yafcVersion": None,
        "pages": pages,
        "displayPages": [p["guid"] for p in pages],
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=2)
    with open(os.path.splitext(path)[0] + ".README.txt", "w", encoding="utf-8") as f:
        f.write(YAFC_TEMPLATE_NOTE)
