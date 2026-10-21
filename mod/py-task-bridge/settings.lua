data_updates =
{
  {
    type = "bool-setting",
    name = "py-stats-auto",
    setting_type = "startup",
    default_value = true,
    order = "a"
  },
  {
    type = "bool-setting",
    name = "py-bridge-selftest",
    setting_type = "startup",
    default_value = false,
    order = "c"
  },
  {
    type = "int-setting",
    name = "py-stats-interval",
    setting_type = "startup",
    default_value = 600,
    minimum_value = 60,
    maximum_value = 3600,
    order = "d"
  },
  {
    type = "int-setting",
    name = "py-stats-delay",
    setting_type = "startup",
    default_value = 300,
    minimum_value = 60,
    maximum_value = 36000,
    order = "b"
  }
}
