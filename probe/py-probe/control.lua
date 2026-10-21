-- 探测 2：电力统计键名、precision 枚举、各窗口可用性
local function dump_counts(label, counts)
  local n = 0
  for k, v in pairs(counts) do
    n = n + 1
    if n <= 6 then log(string.format("[p3] %s %s = %s", label, tostring(k), tostring(v))) end
  end
  log(string.format("[p3] %s entries=%d", label, n))
end

local function probe()
  for k, v in pairs(defines.flow_precision_index) do
    log("[p3] fpi " .. tostring(k) .. " = " .. tostring(v))
  end

  local s = game.surfaces["nauvis"]
  local poles = s and s.find_entities_filtered { type = "electric-pole" } or {}
  log("[p3] poles on nauvis = " .. #poles)
  local pst
  for _, p in ipairs(poles) do
    local cand = p.electric_network_statistics
    if cand and cand.valid then
      pst = cand
      log("[p3] using pole id=" .. tostring(p.electric_network_id))
      break
    end
  end
  if not pst then
    log("[p3] no valid pole stats")
  else
    dump_counts("elec.out", pst.output_counts)
    dump_counts("elec.in", pst.input_counts)
    dump_counts("elec.sto", pst.storage_counts)
    local tries = {
      { tag = "no-name output 1m", req = { category = "output", precision_index = defines.flow_precision_index.one_minute } },
      { tag = "electricity output 1m", req = { name = "electricity", category = "output", precision_index = defines.flow_precision_index.one_minute } },
      { tag = "no-name storage 1m", req = { category = "storage", precision_index = defines.flow_precision_index.one_minute, count = true } },
      { tag = "no-name output 10h", req = { category = "output", precision_index = defines.flow_precision_index.ten_hours } },
    }
    for _, t in ipairs(tries) do
      local ok, r = pcall(pst.get_flow_count, t.req)
      log(string.format("[p3] try %-22s ok=%s r=%s", t.tag, tostring(ok), tostring(r)))
    end
  end

  local f = game.forces.player
  local ist = f.get_item_production_statistics(s)
  local first = next(ist.output_counts)
  if first then
    log("[p3] item sample = " .. tostring(first) .. " total=" .. tostring(ist.output_counts[first]))
    for _, w in ipairs({ { "1m", defines.flow_precision_index.one_minute },
                         { "1h", defines.flow_precision_index.one_hour },
                         { "10h", defines.flow_precision_index.ten_hours } }) do
      local ok, r = pcall(ist.get_flow_count, { name = first, category = "output", precision_index = w[2] })
      log(string.format("[p3] item %s %s ok=%s r=%s", first, w[1], tostring(ok), tostring(r)))
    end
  end

  local bc = f.get_entity_build_count_statistics(s)
  dump_counts("build.sto", bc.storage_counts)
  dump_counts("build.out", bc.output_counts)
end

script.on_nth_tick(300, function()
  script.on_nth_tick(300, nil)
  local ok, err = pcall(probe)
  if not ok then log("[p3] failed: " .. tostring(err)) end
end)
