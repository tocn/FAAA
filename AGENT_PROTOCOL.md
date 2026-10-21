# Agent 二次分析协议

脚本产出 `candidates.json` 之后，由 agent 逐条复核，产出 `final.json` + `audit.json`。
本文件是这个环节的契约：agent 按这里的规则改，apply 按这里的格式读。

## 一、输入

`candidates.json`：

```json
{
  "generated_at": "2026-10-21T21:30:00",
  "save": "py01.zip",
  "tick": 24300,
  "power":   { "produced_mw": 200.0, "consumed_mw": 221.7, "satisfaction": 0.902 },
  "research":{ "current": "py-storage-tanks", "progress": 0.31, "throughput": 42.0 },
  "counts":  { "item_flows": 12, "researched": 46, "recipes_enabled": 0 },
  "candidates": [
    {
      "id": "c01",
      "kind": "power|science|stock|upstream|tech",
      "priority": 94.3,
      "title": "扩建发电：电网余量不足",
      "detail": "发电 200.0 MW / 用电 221.7 MW ...",
      "action": "新增约 27.1 MW 装机",
      "target": "electricity",
      "delta_per_min": 21.7,
      "recipe": "steam-engine",
      "recipe_candidates": ["steam-engine", "py-turbine"],
      "machines_est": "配方 ... → 约 3.2 台",
      "machine_names": ["assembling-machine-1", "..."],
      "evidence": { "produced_mw": 200.0, "satisfaction": 0.902 },
      "flags": ["recipe_locked"]
    }
  ]
}
```

## 二、flag 含义与处置

| flag | 含义 | 处置 |
|---|---|---|
| `no_recipe` | 目标物品没有任何生产配方（矿脉、原油、木头这类） | 改成「开采/采集」动作，或删除 |
| `recipe_locked` | 选中的配方当前未解锁 | 从 `recipe_candidates` 里换一个已解锁的；都没有则改成「先解锁 XX 科技」 |
| `hidden_recipe` | 配方是 hidden，玩家界面上看不到 | 换配方 |
| `huge_batch` | 单次产出 > 500，机器数估算会严重失真 | 保留但改写表述为「按批次算」，或换常规配方 |
| `slow_recipe` | 单次耗时 > 120s（多为大型设备） | 机器数要按实际并行度修正，不能直接用估算值 |
| `implausible_mw` | 电力建议 > 500 MW | 大概率数据错位，降级或删除 |
| `implausible_rate` | 补齐速率 > 1e4 /min | 同上 |

## 三、必须做的语义检查

脚本只算数，下面这些它判不了，agent 必须过一遍：

1. **配方分支**。Pyanodons 同一产物常有 3–5 条路线（普通 / 高效 / 副产 / 后期）。`recipe` 字段是脚本按「非隐藏 + 单主产物 + 有原料」挑的，**不代表当前阶段该用**。要看 `recipes_enabled` 和科技进度判断。
2. **阶段错位**。只有 2 个科技完成时建议「核聚变冷却塔」是荒谬的。任务必须落在「已解锁」范围内，最多往前推一步（紧接着能解锁的）。
3. **重复与矛盾**。同一 `target` 只留一条；`upstream` 和 `stock` 指向同一条链时只留根因那条。
4. **机器数失真**。`machines_est` 里的数字是「配方时间 ÷ 机器速度」的粗算，不含插件塔/模块/产能科技。数字 > 50 或 < 0.1 时要复核。
5. **电力建议的量级**。缺口 21 MW 就建议建 27 MW 是合理的；缺口 2 MW 建议建 100 MW 不合理。
6. **优先级是否可信**。`power` 满足率 < 95% 时通常应该排第一；但如果快照里 0 电杆、0 产量，说明是空档，所有 `stock`/`science` 任务都不可信。

## 四、输出

`final.json`：

```json
{
  "tasks": [
    {
      "title": "[01] 扩建发电至 260 MW",
      "task": "当前 200/221.7 MW（满足率 90.2%）。先补 2 台蒸汽机（+27 MW），再按 25% 余量留冗余。",
      "assignee": null,
      "created_by": "py-analyzer"
    }
  ],
  "audit": [
    {
      "candidate_id": "c01",
      "action": "keep|drop|rewrite|merge|add",
      "before": { "title": "...", "recipe": "...", "delta_per_min": 21.7 },
      "after":  { "title": "...", "recipe": "..." },
      "reason": "满足率 90.2% 已达告警线；缺口 21.7 MW，按 25% 余量取 27 MW。配方 steam-engine 已解锁。"
    }
  ]
}
```

约束：

- `tasks` 最多 10 条，按重要性降序
- `title` 前缀 `[01]`…`[10]` 便于游戏内排序查看
- 每条 `task` 必须带**具体数字**，不能只写「扩产硫酸」
- `audit` 必须覆盖**每一条候选**，包括保留下来的（`action: keep`）
- `reason` 必须引用数字或 flag，不能写「感觉不合适」

## 五、agent 不该做的事

- 不编造快照里没有的物品名或数字
- 不修改 `candidates.json` 原件（只写 final/audit）
- 不因为「看起来太简单」就删掉 `power` 任务
- 不在 `tasks` 里塞超过 10 条 —— Todo-List 面板放不下

## 六、跑法

```bash
# 1. 出候选
python -m analyze.pipeline --save "<存档.zip>" --out out/run1

# 2. agent 读 out/run1/candidates.json，写 out/run1/final.json
#    （由 automation 或会话内完成）

# 3. 生成游戏内命令
python -m analyze.apply --final out/run1/final.json --out out/run1

# 4. 游戏里粘贴 out/run1/push-command.txt
#    需以 --instrument-mod py-task-bridge 启动
```
