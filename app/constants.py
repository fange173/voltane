"""
constants.py — 枚举取值、费用分类、提醒模板
"""

ENERGY_TYPES = ("ice", "hev", "phev", "erev", "bev")
RECORD_KINDS = ("fuel", "charge", "snapshot")
CHARGE_TYPES = ("home", "public_ac", "dc", "super", "swap")
FUEL_GRADES = ("92", "95", "98", "0", "E92", "E95", "other")
SNAPSHOT_TAGS = ("ledger_start", "delivery", "before_park", "after_park", "month_start", "other")
SEGMENT_TAGS = ("normal", "idle", "winter", "highway", "load", "abnormal")
MEMBER_ROLES = ("owner", "editor", "viewer")
PLACE_KINDS = ("home_charger", "charge_station", "gas_station", "other")

EXPENSE_CATEGORIES = {
    "insurance": "保险",
    "maintenance": "保养",
    "repair": "维修",
    "inspection": "年检",
    "tires": "轮胎",
    "wash": "洗车美容",
    "parking": "停车",
    "toll": "通行费",
    "fine": "违章",
    "accessory": "用品/改装",
    "loan": "车贷",
    "other": "其他",
}

# 记一笔费用后，提示完成哪些同类提醒
CATEGORY_TO_TEMPLATES = {
    "maintenance": ("maintenance", "cabin_filter", "brake_fluid"),
    "inspection": ("inspection",),
    "tires": ("tires",),
    "insurance": ("insurance",),
    "repair": ("battery_12v",),
}

# 提醒模板：默认规则可编辑，不硬编码法规
REMINDER_TEMPLATES = [
    {"key": "insurance", "title": "保险到期", "interval_months": 12, "lead_days": 30,
     "hint": "记录保险费用时会自动更新到期日"},
    {"key": "inspection", "title": "年检", "interval_months": 24, "lead_days": 30,
     "hint": "6 年内新车一般 2 年申领一次标志，以当地规定为准"},
    {"key": "maintenance", "title": "保养", "interval_months": 12, "interval_km": 10000, "lead_days": 30,
     "lead_km": 800, "ice_km": 7500},
    {"key": "tires", "title": "轮胎检查/更换", "interval_months": 60, "interval_km": 50000, "lead_days": 30,
     "lead_km": 2000},
    {"key": "brake_fluid", "title": "更换刹车油", "interval_months": 24, "lead_days": 30},
    {"key": "cabin_filter", "title": "更换空调滤芯", "interval_months": 12, "interval_km": 20000, "lead_days": 15,
     "lead_km": 1000},
    {"key": "battery_12v", "title": "12V 小电瓶检查", "interval_months": 36, "lead_days": 30},
    {"key": "loan", "title": "车贷还款", "interval_months": 1, "lead_days": 3},
    {"key": "license", "title": "驾驶证换证", "lead_days": 90, "personal": True},
]
TEMPLATE_BY_KEY = {t["key"]: t for t in REMINDER_TEMPLATES}
