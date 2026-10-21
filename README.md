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

## 七、agent 复核

`watch.py` 只做机械计算。真正需要判断的（配方分支、科技阶段、任务合并）
交给 agent，见 `AGENT_PROTOCOL.md`。

在会话里说一句「复核一下 out/watch 里最新的 candidates」，agent 会：
读 candidates.json → 逐条判断 → 写 final.json + audit → 跑 apply 生成命令。

## 八、快照字段语义（实测，别按文档想当然）

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

## 九、已知限制

1. 没有真实库存，库存告急只能用供给率判断
2. 速率是整局平均，反映不了最近十分钟的突变
3. 机器数是粗估，不含插件塔 / 模块 / 产能科技
4. 引擎统计只覆盖少数物品，Pyanodons 上万种里绝大多数没被记录
5. Instrument Mode **禁用多人**
6. `--start-server` 起不来（卡 auth），无头加载只能用 `--benchmark`

## 十、燃料通道（红瓶阶段硬约束）

能量载体能不能用来发电，取决于它的**物理形态**，这一点在 Py 里是硬约束，
不是"效率高低"的问题：

| 通道 | 载体 | 设备 | effectivity | 红瓶可用 |
|---|---|---|---|---|
| 电力-固体 | `fuel_category ∈ {chemical, biomass}` | `boiler` → `steam-engine` | 0.5 | ✅ |
| 电力-流体 | `fuel_category = None` | `oil-boiler-mk01` / `py-oil-powerplant` / `py-burner` | 2 / 1 / 1 | ❌ 全需 py1 |
| 工艺-流体 | 同上 | `glassworks-mk01` / `smelter-mk01` | 1.0 | ✅ |

原因在数据里：`coal-gas` 和 `tar` 有 `fuel_value` 但**没有 `fuel_category`**，
而 `boiler` 的 `energy_source.fuel_categories` 只有 `["chemical","biomass"]`。
两者接不上，所以**流体燃料进不了锅炉，红瓶阶段无法发电**。

红瓶阶段真正能烧流体的只有两台 `energy_source.type = "fluid"` 的机器：

- `glassworks-mk01`（`glass` 科技解锁，红瓶可用，5 条配方含造玻璃必经的 `glass-1`）
- `smelter-mk01`（`alloys-mk01` 解锁，但该类 45 条配方红瓶只开出 1 条 `duralumin-1`，
  且需要铝板，实际用不起来）

两者都是 10 MW、`effectivity 1.0`。所以流体燃料的价值是二元的：

- **没有工艺炉负荷** → 只能放空，热值等于 0
- **有工艺炉负荷** → 1 MJ 流体顶 1 MJ 机器用电，等价 2 MJ 固体燃料（比锅炉发电贵一倍）

受此约束后的 EROI（`python -m analyze.fuel_model`）：

| 路线 | 无工艺负荷 | 有工艺负荷（≥100 MJ/min） | 旧算法（错，假设能进锅炉） |
|---|---|---|---|
| raw-coal 直烧 | 83.3% | 90.5% | 83.3% |
| raw-coal → coal | 31.2% | 40.0% | 78.1% |
| coal → coke | 22.8% | 30.0% | 82.1% |

最后一行是这次修正的核心：**之前 82.1% 的 coke 结论建立在一个不可能的前提上**
——把煤气和焦油送进锅炉。它们进不去。修正后 coke 只有 22.8%～30%。

`raw-coal` 在有工艺负荷时反而升到 90.5%，是因为蒸馏副产物走工艺通道时
effectivity 是 1.0（锅炉路径只有 0.5），即"把煤转成煤气"这条路
只有在**有玻璃/冶炼产能消化**时才划算。收益在工艺容量 100 MJ/min 处饱和，
一台玻璃窑满负荷是 600 MJ/min，所以只要真的在造玻璃，这个条件不难满足。

## 十一、风力发电机（红瓶阶段）

红瓶阶段唯一的非燃料电源是 **`multiblade-turbine-mk01`**，由 `steel-processing`
解锁（只需红瓶）。`hawt` / `vawt` 系列要 `wind-mk01`（py1），`solar-panel`
要 `solar-energy`（py1+logistic），`accumulator` 要
`electric-energy-accumulators`（py1）——**红瓶阶段连储能都没有**。

### 功率：均值等于标称，但摆幅极大

`scripts/wind/wind.lua:251` 的公式：

```lua
power = max_energy_production * (1 + variation * wind_speed)
wind_speed = (sin(2 * tick/10000) + sin(pi * tick/10000)) / 2
```

`wind_speed` 在 [-1, 1] 之间摆动，长期均值为 0，所以**平均功率 = 标称值**。
但 `variation` 决定摆幅：

| 型号 | 标称 | variation | 瞬时区间 | 均值 |
|---|---|---|---|---|
| multiblade-mk01 | 550 kW | 0.9 | **55 ~ 1045 kW** | 550 kW |
| hawt-mk01 | 5 MW | 0.5 | 2.5 ~ 7.5 MW | 5 MW |
| vawt-mk01 | 4 MW | 0.7 | 1.2 ~ 6.8 MW | 4 MW |

mk01 的摆幅是 **19 倍**，而且红瓶阶段没有蓄电器，波动直接进电网。
所以它只能当"叠加在锅炉上的省煤器"用——锅炉会自动降载，风力多发就少烧煤，
风力掉到 55 kW 时锅炉顶上来。**不能当基荷**。

### 成本 / 功率比

`python -m analyze.capital` 算出（内含能量 = 采矿 + 加工 + 建造那一次的电，
统一折成 MJ 燃料当量）：

| 方案 | 功率 | 内含能量 | MJ/kW | 实际占地 | 格/MW |
|---|---|---|---|---|---|
| 风力 multiblade-mk01 | 550 kW | 864 MJ | **1.57** | 718 格 | **1306** |
| 煤电 1锅炉+2蒸汽机 | 3.5 MW | 349 MJ | **0.10** | 27 格 | **7.7** |

风力机的单位功率造价是煤电的 **16 倍**，占地是 **169 倍**。

占地的关键：建筑本体只有 6.4×6.4 格，但每台会生成一个
`±13.4` 的隐形碰撞体（`prototypes/buildings/multiblade-turbine-mk01.lua`
里的 `simple-entity-with-force`），强制与同类保持 **26.8×26.8** 的净空。
光看建筑会低估 17 倍。各型的净空：hawt-mk01 26.8、vawt-mk01 35.8、
multiblade-mk03 24.8（但那是 34 MW，占地效率 18 格/MW，好得多）。

### 回本时间

多花的建造费 1.47 MJ/kW，煤电每发 1 kW 电要烧 2 kW 热（蒸汽机 effectivity 0.5），
即 2 J/s/kW。所以：

```
1.47 MJ/kW ÷ 0.002 MJ/s/kW ≈ 735 s ≈ 12 分钟
```

**只要连续跑 12 分钟就赚回建造差价**。所以纯能量账上风力很划算，
真正的约束是**地**和**波动**，不是造价。

### 建造材料

```
multiblade-turbine-mk01 = small-electric-pole×6 + small-parts-01×4
                        + stone-brick×20 + inductor1×18 + steel-plate×10 + fish×5
```

`steel-plate×10` 占 53%，`inductor1×18` 占 36%。`fish×5` 只需钓一条鱼
（`fish` 实体的 `minable.results` 就是 5 个）。注意 `fish` 是 pyalienlife 的
模块，红瓶阶段无法养殖，但可以手钓。
