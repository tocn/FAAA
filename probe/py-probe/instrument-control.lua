-- 验证 Instrument Mode 的三个未知数：
--   1. instrument-control.lua 是否真的在别的 mod 的 Lua state 里执行
--   2. script.mod_name 能否区分当前是哪个 mod
--   3. 延迟到 tick 后，能否读到对方 control.lua 建立的全局（以 Todo-List 的 `todo` 表为例）

local state_name = "?"
local ok_name, n = pcall(function() return script.mod_name end)
if ok_name then state_name = tostring(n) end

log("[probe] instrument-control loaded in state=" .. state_name)

-- 只在目标 mod 的 state 里埋钩子
if state_name == "Todo-List" then
  log("[probe] >>> 命中 Todo-List state，注册延迟检查")

  local checked = false
  script.on_event(defines.events.on_tick, function(e)
    if checked then return end
    checked = true

    local g = rawget(_G, "todo")
    log("[probe] tick=" .. tostring(e.tick) .. " type(todo)=" .. type(g))

    if type(g) == "table" then
      local keys = {}
      for k in pairs(g) do keys[#keys + 1] = tostring(k) end
      table.sort(keys)
      log("[probe] todo 表字段: " .. table.concat(keys, ", "))

      for _, fname in ipairs({ "import_tasks", "save_task_to_open_list", "assemble_task", "next_task_id" }) do
        log("[probe]   todo." .. fname .. " = " .. type(g[fname]))
      end
    end

    -- 检查其 storage（每个 mod 独立实例）
    local s_ok, s = pcall(function() return storage end)
    if s_ok and type(s) == "table" then
      local ks = {}
      for k in pairs(s) do ks[#ks + 1] = tostring(k) end
      table.sort(ks)
      log("[probe] storage 顶层字段: " .. table.concat(ks, ", "))
      if s.todo then
        log("[probe] storage.todo.open 条数 = " .. tostring(#(s.todo.open or {})))
      end
    else
      log("[probe] storage 不可访问: " .. tostring(s))
    end
  end)
end
