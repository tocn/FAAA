-- py-task-bridge / control.lua
--
-- 1. 导出产量 / 电力 / 科技 / 建造数快照 → script-output/py-stats.json
-- 2. 把外部任务清单写入 Todo-List（需 Instrument Mode，见 instrument-control.lua）
--
-- 实测要点（2.0.77）：
--   * *_counts 是「累计总量」，不是按精度嵌套的表
--   * get_flow_count 只接受一个 table 参数，返回「每秒」流量，用于相对趋势
--   * 绝对速率 = 累计总量 ÷ 游戏时长(分钟)，这是唯一单位可靠的算法
--   * 电力统计按「实体名」分项（steam-engine / pump / ...），没有 "electricity" 键
--   * 建造数量在 build_count_statistics 的 output_counts 里

local FPI = defines.flow_precision_index
local BRIDGE = "py-bridge-todo"

local WINDOWS = {
  { key = "m1", idx = FPI.one_minute },
  { key = "m10", idx = FPI.ten_minutes },
  { key = "h1", idx = FPI.one_hour },
  { key = "h10", idx = FPI.ten_hours },
  { key = "h50", idx = FPI.fifty_hours },
}

local function flow_value(stat, name, category, precision, is_storage)
  local req = { category = category, precision_index = precision }
  if name ~= nil then req.name = name end
  if is_storage then req.count = true end
  local ok, v = pcall(stat.get_flow_count, req)
  if ok and type(v) == "number" then return v end
  return nil
end

local function read_flow(stat, minutes)
  local out = {}
  local function ensure(name)
    local rec = out[name]
    if not rec then
      rec = { o = {}, i = {}, s = {} }
      out[name] = rec
    end
    return rec
  end

  local function fill(counts, category, key, is_storage)
    for name, total in pairs(counts) do
      local rec = ensure(name)
      rec[key].rate = (type(total) == "number" and total or 0) / minutes
      rec[key].total = type(total) == "number" and total or 0
      for _, w in ipairs(WINDOWS) do
        local v = flow_value(stat, name, category, w.idx, is_storage)
        if v ~= nil then rec[key][w.key] = v end
      end
    end
  end

  fill(stat.output_counts, "output", "o", false)
  fill(stat.input_counts, "input", "i", false)
  fill(stat.storage_counts, "storage", "s", true)
  return out
end

local function read_power(surface)
  local nets, seen = {}, {}
  local total = { produced = 0, consumed = 0, stored = 0 }
  for _, pole in pairs(surface.find_entities_filtered { type = "electric-pole" }) do
    local id = pole.electric_network_id
    if id and id ~= 0 and not seen[id] then
      seen[id] = true
      local st = pole.electric_network_statistics
      if st and st.valid then
        -- 发电：所有产出实体的 output 之和；耗电：所有输入实体的 input 之和
        local prod, cons = 0, 0
        for ename in pairs(st.output_counts) do
          prod = prod + (flow_value(st, ename, "output", FPI.one_minute, false) or 0)
        end
        for ename in pairs(st.input_counts) do
          cons = cons + (flow_value(st, ename, "input", FPI.one_minute, false) or 0)
        end
        nets[#nets + 1] = {
          id = id,
          produced_w = prod,
          consumed_w = cons,
          satisfaction = cons > 1e-6 and math.min(1, prod / cons) or 1,
        }
        total.produced = total.produced + prod
        total.consumed = total.consumed + cons
      end
    end
  end
  total.satisfaction = total.consumed > 1e-6 and math.min(1, total.produced / total.consumed) or 1
  return nets, total
end

local function read_research(force)
  local cur = force.current_research
  local r = {
    current = cur and cur.name or nil,
    progress = cur and force.research_progress or 0,
    unit_count = cur and cur.research_unit_count or 0,
    unit_energy = cur and cur.research_unit_energy or 0,
    ingredients = {},
  }
  if cur then
    for _, ing in pairs(cur.research_unit_ingredients) do
      r.ingredients[ing.name] = ing.amount
    end
  end
  local st = { researched = 0, available = {}, locked = 0, researched_names = {} }
  for name, t in pairs(force.technologies) do
    if t.researched then
      st.researched = st.researched + 1
      if #st.researched_names < 400 then
        st.researched_names[#st.researched_names + 1] = name
      end
    elseif t.enabled then
      local ing, pre = {}, {}
      for _, i in pairs(t.research_unit_ingredients) do ing[i.name] = i.amount end
      for pname in pairs(t.prerequisites) do pre[#pre + 1] = pname end
      st.available[#st.available + 1] = {
        name = name, level = t.level, count = t.research_unit_count,
        energy = t.research_unit_energy, ing = ing, prereq = pre,
      }
    else
      st.locked = st.locked + 1
    end
  end
  return r, st
end

local function export()
  local force = game.forces.player
  local minutes = math.max(1, game.tick / 3600)
  local surfaces = {}
  local item_flow, fluid_flow, entity_build = {}, {}, {}
  local power_nets = {}
  local power_total = { produced = 0, consumed = 0, stored = 0, satisfaction = 1 }
  local main_surface = game.surfaces["nauvis"] or game.surfaces[1]
  local errors = {}

  for _, surface in pairs(game.surfaces) do
    local ok, err = pcall(function()
      surfaces[#surfaces + 1] = surface.name
      local nets, total = read_power(surface)
      for _, n in ipairs(nets) do
        n.surface = surface.name
        power_nets[#power_nets + 1] = n
      end
      power_total.produced = power_total.produced + total.produced
      power_total.consumed = power_total.consumed + total.consumed

      for name, rec in pairs(read_flow(force.get_item_production_statistics(surface), minutes)) do
        item_flow[name] = rec
      end
      for name, rec in pairs(read_flow(force.get_fluid_production_statistics(surface), minutes)) do
        fluid_flow[name] = rec
      end

      -- 建造数量：build statistics 的 output_counts
      local bc = force.get_entity_build_count_statistics(surface)
      for name, n in pairs(bc.output_counts) do
        entity_build[name] = (entity_build[name] or 0) + (type(n) == "number" and n or 0)
      end
    end)
    if not ok then errors[#errors + 1] = surface.name .. ": " .. tostring(err) end
  end
  power_total.satisfaction = power_total.consumed > 1e-6
    and math.min(1, power_total.produced / power_total.consumed) or 1

  local research, techstat = read_research(force)

  local recipes_enabled = {}
  for name, r in pairs(force.recipes) do
    if r.enabled then recipes_enabled[#recipes_enabled + 1] = name end
  end

  local data = {
    tick = game.tick,
    game_minutes = minutes,
    game_version = script.active_mods["base"] or "2.0",
    surfaces = surfaces,
    main_surface = main_surface and main_surface.name or nil,
    power = { networks = power_nets, totals = power_total },
    item_flow = item_flow,
    fluid_flow = fluid_flow,
    entity_build = entity_build,
    research = research,
    techs = techstat,
    recipes_enabled = recipes_enabled,
    errors = errors,
    instrument = remote.interfaces[BRIDGE] ~= nil,
    mods = script.active_mods,
  }

  local ok, json = pcall(helpers.table_to_json, data)
  if not ok then
    log("[py-stats] JSON encode failed: " .. tostring(json))
    game.print("[py-stats] JSON encode failed: " .. tostring(json))
    return false
  end
  helpers.write_file("py-stats.json", json, false)

  local n, m = 0, 0
  for _ in pairs(item_flow) do n = n + 1 end
  for _ in pairs(fluid_flow) do m = m + 1 end

  log(string.format("[py-stats] exported tick=%d item=%d fluid=%d nets=%d errors=%d",
    game.tick, n, m, #power_nets, #errors))
  return true
end

-- ---------- Todo 推送 ----------
local function bridge_available()
  return remote.interfaces[BRIDGE] ~= nil
end

local function push_tasks(tasks)
  if not bridge_available() then
    return { ok = false, added = 0, skipped = 0,
             reason = "py-bridge-todo 未注册：需要用 --instrument-mod py-task-bridge 启动" }
  end
  return remote.call(BRIDGE, "push", tasks)
end

local function decode_payload(encoded)
  local ok, text = pcall(helpers.decode_string, encoded)
  if not ok then return nil, "decode_string failed: " .. tostring(text) end
  local ok2, obj = pcall(helpers.json_to_table, text)
  if not ok2 then return nil, "json_to_table failed: " .. tostring(obj) end
  return obj
end

commands.add_command("py-stats",
  "导出产量/电力/科技快照到 script-output/py-stats.json",
  function() export() end)

commands.add_command("py-push",
  "推送任务到 Todo-List。用法: /py-push <encoded>",
  function(event)
    local param = event.parameter
    if not param or param == "" then
      game.print("[py-bridge] 缺少参数：/py-push <encoded>")
      return
    end
    local tasks, err = decode_payload(param)
    if not tasks then
      game.print("[py-bridge] " .. tostring(err))
      return
    end
    local r = push_tasks(tasks)
    game.print(string.format("[py-bridge] ok=%s added=%s skipped=%s %s",
      tostring(r.ok), tostring(r.added), tostring(r.skipped), r.reason or ""))
  end)

commands.add_command("py-todo-clear",
  "清掉 py-analyzer 创建的任务",
  function()
    if not bridge_available() then
      game.print("[py-bridge] 需要 Instrument Mode 才能访问 Todo-List")
      return
    end
    local r = remote.call(BRIDGE, "clear")
    game.print(string.format("[py-bridge] clear ok=%s removed=%s", tostring(r.ok), tostring(r.removed)))
  end)

commands.add_command("py-bridge-status",
  "显示桥接状态",
  function()
    local mode = bridge_available() and "instrument" or "export-only"
    game.print(string.format("[py-bridge] mode=%s", mode))
  end)

remote.add_interface("py-task-bridge", {
  dump = function() return export() end,
  push = function(encoded)
    local tasks, err = decode_payload(encoded)
    if not tasks then return { ok = false, reason = err } end
    return push_tasks(tasks)
  end,
  status = function() return bridge_available() end,
})

-- 自动导出：进游戏 5 秒导一次，之后按 py-stats-interval（默认 600 秒）周期刷新
do
  local auto = settings.startup["py-stats-auto"]
  local iv = settings.startup["py-stats-interval"]
  local seconds = (iv and iv.value) or 600
  local ticks = math.max(3600, seconds * 60)
  if auto == nil or auto.value then
    script.on_nth_tick(300, function()
      script.on_nth_tick(300, nil)
      local ok, err = pcall(export)
      if not ok then log("[py-stats] export failed: " .. tostring(err)) end
    end)
    script.on_nth_tick(ticks, function()
      local ok, err = pcall(export)
      if not ok then log("[py-stats] export failed: " .. tostring(err)) end
    end)
  end
end
