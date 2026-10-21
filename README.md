# FAAA — Factorio AI Agent Advice

游戏运行时监视自动存档，读出产量 / 电力 / 科技，算出现下最该做的事，
经 agent 复核后写进 Todo-List。

```
游戏运行 ──autosave 落盘──> watcher 检测 ──> 分析 ──> candidates.json
                                                          ↓
                                                    agent 复核
                                                          ↓
                                             final.json ──> push-command.txt
                                                                  ↓
                                                    游戏控制台粘贴 → Todo-List
```

## 一、快速开始

```bash
# 1. 把 mod/py-task-bridge 装进 Factorio 的 mods/ 并启用
# 2. Steam 启动选项加：--instrument-mod py-task-bridge
# 3. 开游戏，另开一个终端：
python watch.py
```

游戏内 mod 每 10 分钟刷新一次 `script-output/py-stats.json`，watcher 检测到变化就分析。
**不需要再启动任何 factorio 进程。**

```bash
python watch.py --once              # 处理当前快照一次就退出
python watch.py --only-if-game      # 游戏没在跑就直接退出
python watch.py --source autosave   # 改成监视 autosave（会起进程，见下）
python watch.py --poll 10           # 轮询间隔（秒）
```

mod 的导出节奏由 startup 设置控制：`py-stats-interval` 默认 600 秒，
进游戏后 5 秒会先导一次。

## 二、两种监视源

| | `--source live`（默认） | `--source autosave`（兜底） |
|---|---|---|
| 监视对象 | `script-output/py-stats.json` | `saves/_autosave*.zip` |
| 数据来源 | 游戏内 mod 自己导出 | 另起一个 factorio 进程加载存档 |
| 额外开销 | 可忽略 | **每次 40–60 秒全速加载，内存 3–4 GB** |
| 需要 mod | 需要启用 `py-task-bridge` | 不需要，纯外部 |

**默认 live**：游戏本来就开着，解析器已经在内存里跑，让它顺手导一份是最省的做法。
实测 autosave 模式单次分析耗时 55 秒（其中 47 秒是加载），而且会跟游戏抢 CPU。

### 为什么不在外部解析存档

`level.dat` 解压后有 10.4 MB，是二进制 PropertyTree（2.0 格式，头 8 字节是版本号）。
产量统计确实在里面，但是**按 prototype 数字 ID 存的**，全文搜 `production`
只会命中 mod 的 UI 参数。要读出来必须完整反序列化整棵树，再自己重建 ID↔物品名映射。

工作量几百行、格式随小版本变动、出错难排查 —— 而让游戏导一份只要 480 KB 且零成本。
所以外部解析只作为「游戏没装 mod」时的备用方案，没有实现。

## 三、为什么必须复制快照

autosave 会轮转（`_autosave1/2/3`）。如果直接读 `_autosave1.zip`，
分析到一半游戏存了下一档，文件就被覆盖了。所以 watcher 先复制到
`out/watch/snap-<时间>.zip` 再分析。

同理，写入中的 zip 不可读：watcher 要求**大小连续两次采样不变**、
且 `level.dat` 能被 `zipfile` 读出来，才认为写完了。

## 四、目录

```
FAAA/
├─ watch.py                  监视器（主入口）
├─ run_pipeline 由 analyze/pipeline.py 提供
├─ analyze/
│   ├─ pipeline.py           存档 → 快照 → candidates.json
│   ├─ graph.py              从 data-raw-dump.json 建配方图
│   ├─ score.py              评分（电力/科研/供给/上游/科技）
│   ├─ model.py              快照读取
│   ├─ apply.py              final.json → 游戏内 /sc 命令
│   ├─ emit.py               HTML 报表
│   ├─ fstring.py            Factorio 字符串编解码
│   └─ machines.py           机器产能表
├─ mod/py-task-bridge/       唯一的 mod
│   ├─ control.lua           快照导出 + /py-push 命令
│   └─ instrument-control.lua 注入 Todo-List 的 Lua state
├─ probe/py-probe/           探测 API 行为用，**不要装**
├─ AGENT_PROTOCOL.md         agent 复核规则
└─ data/                     配方索引（.gitignore，本地生成）
```

## 五、配方索引（换 mod 才需要重建）

```bash
factorio --dump-data --mod-directory "<mods>"     # → script-output/data-raw-dump.json（152 MB）
python -m analyze.graph    build <data-raw-dump.json> data
python -m analyze.machines       <data-raw-dump.json> data/machines.json
```

## 六、导进 YAFC（量化求解）

```bash
python -m analyze.yafc --candidates out/watch/<run>/candidates.json --out out/watch/plan.yafc
```

用 YAFC 打开 `plan.yafc`，它会自己跑 OR-Tools 算出精确机器数、模块、副产物。

格式是从 `YAFC CE 2.20.0` 的真实工程文件反推出来的：

- 目标速率写在 `content.links`，不是写在行上
- `amount` 的单位是**每秒**，60/min 要写成 `1`
- 引用格式 `!Recipe.<名字>!normal` / `!Item.<名字>!normal` / `!Entity.<名字>!normal`
- `entity` 按该配方类别里机器速度的**中位数**挑（最高速往往是后期机器）

只有满足 `target + recipe + delta_per_min > 0` 的候选才会进表 —— 电力、科技类任务进不了生产表。

## 六、agent 复核

`watch.py` 只做机械计算。真正需要判断的（配方分支、科技阶段、任务合并）
交给 agent，见 `AGENT_PROTOCOL.md`。

在会话里说一句「复核一下 out/watch 里最新的 candidates」，agent 会：
读 candidates.json → 逐条判断 → 写 final.json + audit → 跑 apply 生成命令。

## 七、快照字段语义（实测，别按文档想当然）

| 字段 | 含义 |
|---|---|
| `item_flow[name].o.rate` | 每分钟产出，由「累计 ÷ 游戏分钟数」算出 |
| `item_flow[name].o.total` | 累计总量（引擎原始值） |
| `item_flow[name].o.m1/h1/h10` | 相对窗口值，**只能比大小**，别当绝对量 |
| `power.totals.produced` | 瓦特，按发电实体名分项求和 |
| `entity_build[name]` | 累计建造数 |
| `recipes_enabled` | 当前已解锁配方名 |

### 四个会踩的坑

1. `*_counts` 是**累计值**，不是按精度嵌套的表
2. `get_flow_count` **只接受一个 table 参数**，传位置参数报 `Expected 1 argument`
3. 电力统计**按实体名分项**（`steam-engine`/`pump`…），没有 `"electricity"` 键
4. `storage_counts` 是**净累积不是库存**，算不出「还能撑几分钟」

## 八、已知限制

1. 没有真实库存，库存告急只能用供给率判断
2. 速率是整局平均，反映不了最近十分钟的突变
3. 机器数是粗估，不含插件塔 / 模块 / 产能科技
4. 引擎统计只覆盖少数物品，Pyanodons 上万种里绝大多数没被记录
5. Instrument Mode **禁用多人**
6. `--start-server` 起不来（卡 auth），无头加载只能用 `--benchmark`
