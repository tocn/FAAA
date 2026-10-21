-- py-task-bridge / instrument-control.lua
--
-- 本文件被 Instrument Mode 注入到**每一个 mod 的 Lua state**，且早于各自的 control.lua。
-- 因此这里必须极轻：只认领 Todo-List 的 state，其余立刻返回。
--
-- 启动方式（必须带参数，否则此文件根本不会加载）：
--   factorio --instrument-mod py-task-bridge ...
--
-- 注入后能在 Todo-List 的沙箱里直接调它的内部函数，无需它提供 remote 接口。

local STATE = "Todo-List"
if script.mod_name ~= STATE then
  return
end

local BRIDGE = "py-bridge-todo"

local function get_todo()
  return rawget(_G, "todo")
end

local function get_open_list()
  local ok, s = pcall(function() return storage end)
  if not ok or type(s) ~= "table" then return nil end
  local td = s.todo
  if type(td) ~= "table" then return nil end
  return td.open
end

local function existing_titles()
  local set = {}
  local open = get_open_list()
  if open then
    for _, task in ipairs(open) do
      if type(task) == "table" and task.title then
        set[task.title] = true
      end
    end
  end
  return set
end

log("[py-bridge] instrument injected into Todo-List state")

remote.add_interface(BRIDGE, {
  -- 是否已就绪（todo 表已由 Todo-List 的 control.lua 建立）
  ready = function()
    return type(get_todo()) == "table"
  end,

  -- tasks: { {title=..., task=..., assignee=..., created_by=...}, ... }
  -- 返回 { ok=bool, added=n, skipped=n, reason=... }
  push = function(tasks)
    local todo = get_todo()
    if type(todo) ~= "table" then
      return { ok = false, added = 0, skipped = 0, reason = "todo table not ready" }
    end
    if type(tasks) ~= "table" then
      return { ok = false, added = 0, skipped = 0, reason = "bad payload" }
    end
    if type(todo.next_task_id) ~= "function" or type(todo.save_task_to_open_list) ~= "function" then
      return { ok = false, added = 0, skipped = 0, reason = "todo api missing" }
    end

    local seen = existing_titles()
    local added, skipped = 0, 0
    for _, t in ipairs(tasks) do
      local title = t.title
      if type(title) ~= "string" or title == "" then
        skipped = skipped + 1
      elseif seen[title] then
        skipped = skipped + 1
      else
        local author = t.created_by or "py-analyzer"
        local task = {
          id = todo.next_task_id(),
          title = title,
          task = t.task or "",
          assignee = t.assignee,
          created_by = author,
          updated_by = author,
        }
        todo.save_task_to_open_list(task)
        seen[title] = true
        added = added + 1
      end
    end

    if added > 0 and type(todo.update_main_task_list_for_everyone) == "function" then
      pcall(todo.update_main_task_list_for_everyone)
    end
    log(string.format("[py-bridge] push added=%d skipped=%d", added, skipped))
    return { ok = true, added = added, skipped = skipped }
  end,

  -- 列出当前未完成任务标题，便于外部核对/去重
  list = function()
    local out = {}
    local open = get_open_list()
    if open then
      for _, task in ipairs(open) do
        if type(task) == "table" then
          out[#out + 1] = { title = task.title, id = task.id }
        end
      end
    end
    return out
  end,

  -- 只清理由 py-analyzer 创建的任务（按 created_by 过滤），不碰玩家手写的
  clear = function()
    local ok, s = pcall(function() return storage end)
    if not ok or type(s) ~= "table" or type(s.todo) ~= "table" then
      return { ok = false, removed = 0, reason = "storage not accessible" }
    end
    local open = s.todo.open or {}
    local keep, removed = {}, 0
    for _, task in ipairs(open) do
      if type(task) == "table" and task.created_by == "py-analyzer" then
        removed = removed + 1
      else
        keep[#keep + 1] = task
      end
    end
    s.todo.open = keep
    local todo = get_todo()
    if type(todo) == "table" and type(todo.update_main_task_list_for_everyone) == "function" then
      pcall(todo.update_main_task_list_for_everyone)
    end
    return { ok = true, removed = removed }
  end,
})
