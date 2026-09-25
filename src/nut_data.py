# -*- coding: utf-8 -*-
"""
螺母类型定义、参数 schema 与标准尺寸库（预设值为联网核对的国标常用值）。

本模块不依赖任何第三方库（纯 Python），可被 CATIA 控制器与 UI 共用，
也可在离线环境单独 import 做几何/参数校验。
参考：文件夹内 readme.md《螺母全面技术指南》。
分类（按结构型式）：六角类 / 圆类 / 方类 / 特殊类。

预设尺寸来源（均为公开标准常用值，建模用“公称/最大值”，单位 mm）：
  hex_type1  GB/T 6170-2015  1型六角螺母（=ISO 4032 / DIN 934）
  hex_type2  GB/T 6175-2016  2型六角螺母（=ISO 4033）
  hex_thin   GB/T 6172.1     六角薄螺母（=ISO 4035 细牙/ ISO 4035）
  hex_flange GB/T 6177.1-2016 六角法兰面螺母（=DIN 6923）
  lock_nut   GB/T 6183.1     非金属嵌件锁紧螺母（钢体同 6170）
  round_nut  GB/T 812-1988   圆螺母（细牙，带扳手槽）
  square_nut GB/T 39-1988    方螺母 C级
  cap_nut    GB/T 923-2009   六角盖形螺母
注：螺距 P 取粗牙（圆螺母取标准细牙）。实际生产有公差，建模以公称尺寸为准。
"""
import math
import re

# 大类分组（供 UI 树形展示）
NUT_CATEGORIES = [
    ("hex", "六角类螺母"),
    ("round", "圆螺母类"),
    ("square", "方螺母类"),
    ("special", "特殊 / 其他"),
]

# ---------------- 螺纹标准数据库 ----------------
# 覆盖：公制粗牙 / 公制细牙 / 英制 UNC(粗) / 英制 UNF(细) / 英制 BSW(惠氏,55°)。
# 每个尺寸元组: (公称大径 mm, 螺距 mm)。英制由 TPI 换算: 螺距 = 25.4 / TPI。
# 牙型角: 公制/UN 系列 60°；BSW(惠氏) 55°。
THREAD_SYSTEMS = {
    "metric_coarse": {
        "name": "公制 粗牙 (M, Coarse)", "angle": 60, "unit": "mm",
        "sizes": {
            "M3": (3.0, 0.5), "M4": (4.0, 0.7), "M5": (5.0, 0.8), "M6": (6.0, 1.0),
            "M8": (8.0, 1.25), "M10": (10.0, 1.5), "M12": (12.0, 1.75), "M14": (14.0, 2.0),
            "M16": (16.0, 2.0), "M18": (18.0, 2.5), "M20": (20.0, 2.5), "M22": (22.0, 2.5),
            "M24": (24.0, 3.0), "M27": (27.0, 3.0), "M30": (30.0, 3.5), "M33": (33.0, 3.5),
            "M36": (36.0, 4.0), "M39": (39.0, 4.0), "M42": (42.0, 4.5), "M45": (45.0, 4.5),
            "M48": (48.0, 5.0),
        },
    },
    "metric_fine": {
        "name": "公制 细牙 (M, Fine)", "angle": 60, "unit": "mm",
        "sizes": {
            "M8×1": (8.0, 1.0), "M10×1": (10.0, 1.0), "M10×1.25": (10.0, 1.25),
            "M12×1.25": (12.0, 1.25), "M12×1.5": (12.0, 1.5), "M14×1.5": (14.0, 1.5),
            "M16×1.5": (16.0, 1.5), "M18×1.5": (18.0, 1.5), "M20×1.5": (20.0, 1.5),
            "M22×1.5": (22.0, 1.5), "M24×2": (24.0, 2.0), "M27×2": (27.0, 2.0),
            "M30×2": (30.0, 2.0), "M33×2": (33.0, 2.0), "M36×3": (36.0, 3.0),
            "M42×3": (42.0, 3.0), "M48×3": (48.0, 3.0),
        },
    },
    "unc": {
        "name": "英制 UNC (粗牙)", "angle": 60, "unit": "inch",
        "sizes": {
            '1/4"-20': (6.35, 25.4 / 20), '5/16"-18': (7.938, 25.4 / 18),
            '3/8"-16': (9.525, 25.4 / 16), '7/16"-14': (11.112, 25.4 / 14),
            '1/2"-13': (12.7, 25.4 / 13), '9/16"-12': (14.288, 25.4 / 12),
            '5/8"-11': (15.875, 25.4 / 11), '3/4"-10': (19.05, 25.4 / 10),
            '7/8"-9': (22.225, 25.4 / 9), '1"-8': (25.4, 25.4 / 8),
        },
    },
    "unf": {
        "name": "英制 UNF (细牙)", "angle": 60, "unit": "inch",
        "sizes": {
            '1/4"-28': (6.35, 25.4 / 28), '5/16"-24': (7.938, 25.4 / 24),
            '3/8"-24': (9.525, 25.4 / 24), '7/16"-20': (11.112, 25.4 / 20),
            '1/2"-20': (12.7, 25.4 / 20), '9/16"-18': (14.288, 25.4 / 18),
            '5/8"-18': (15.875, 25.4 / 18), '3/4"-16': (19.05, 25.4 / 16),
            '7/8"-14': (22.225, 25.4 / 14), '1"-12': (25.4, 25.4 / 12),
        },
    },
    "bsw": {
        "name": "英制 BSW (惠氏 粗牙, 55°)", "angle": 55, "unit": "inch",
        "sizes": {
            '1/4"': (6.35, 25.4 / 20), '5/16"': (7.938, 25.4 / 18),
            '3/8"': (9.525, 25.4 / 16), '1/2"': (12.7, 25.4 / 12),
            '5/8"': (15.875, 25.4 / 11), '3/4"': (19.05, 25.4 / 10),
            '7/8"': (22.225, 25.4 / 9), '1"': (25.4, 25.4 / 8),
        },
    },
}


def list_thread_systems():
    """返回 [(key, name, unit)]。"""
    return [(k, v["name"], v["unit"]) for k, v in THREAD_SYSTEMS.items()]


def thread_sizes(system_key):
    """返回某标准下的所有规格标签列表。"""
    sys_d = THREAD_SYSTEMS.get(system_key)
    if not sys_d:
        return []
    return list(sys_d["sizes"].keys())


def get_thread_spec(system_key, size_label):
    """返回 (d_mm, pitch_mm, angle_deg) 或 None。"""
    sys_d = THREAD_SYSTEMS.get(system_key)
    if not sys_d:
        return None
    t = sys_d["sizes"].get(size_label)
    if not t:
        return None
    return (t[0], t[1], sys_d["angle"])


def estimate_hex_dims(d):
    """由公称直径 d(mm) 估算六角对边 s 与厚度 m（ISO 比例近似），用于英制等非预设规格。"""
    s = max(5.5, round(d * 1.5 / 0.5) * 0.5)
    m = round(d * 0.8 * 2) / 2.0
    return s, m


# 参数元信息：标签、单位、取值范围、类型、默认值、是否自动派生
PARAM_META = {
    "D":       {"label": "公称直径 D", "unit": "mm", "min": 1.0,  "default": 12.0, "type": "float"},
    "P":       {"label": "螺距 P",     "unit": "mm", "min": 0.1,  "default": 1.75, "type": "float"},
    "s":       {"label": "对边宽度 s", "unit": "mm", "min": 1.0,  "default": 18.0, "type": "float"},
    "e":       {"label": "对角宽度 e", "unit": "mm", "min": 1.0,  "default": 20.78, "type": "float", "auto": True,
                "note": "由 s 自动派生：六角 e=2s/√3，方螺母 e=s·√2，可覆盖"},
    "m":       {"label": "螺母厚度 m", "unit": "mm", "min": 0.5,  "default": 10.8, "type": "float"},
    "bore":    {"label": "内孔直径(螺纹大径)", "unit": "mm", "min": 0.5, "default": 12.0, "type": "float"},
    "chamfer": {"label": "倒角尺寸 c", "unit": "mm", "min": 0.0,  "default": 0.8, "type": "float",
                "note": "0 = 不倒角"},
    "thread":  {"label": "生成真实螺纹齿形", "unit": "", "default": True, "type": "bool"},
    "flange_d": {"label": "法兰外径 d", "unit": "mm", "min": 1.0, "default": 22.5, "type": "float"},
    "flange_t": {"label": "法兰厚度 t", "unit": "mm", "min": 0.5, "default": 2.6, "type": "float"},
    "outer_d": {"label": "外圆直径 d", "unit": "mm", "min": 1.0, "default": 35.0, "type": "float"},
    "slot_w":  {"label": "扳手槽宽",   "unit": "mm", "min": 0.5, "default": 5.3, "type": "float"},
    "slot_d":  {"label": "扳手槽深",   "unit": "mm", "min": 0.5, "default": 3.1, "type": "float"},
    "cap_h":   {"label": "盖顶高度",   "unit": "mm", "min": 0.5, "default": 8.0, "type": "float"},
    "inset":   {"label": "锁紧嵌件凸台", "unit": "", "default": False, "type": "bool"},
    "slot":    {"label": "开槽(六角开槽螺母)", "unit": "", "default": True, "type": "bool"},
    "wing_span": {"label": "蝶形翼展", "unit": "mm", "min": 10.0, "default": 40.0, "type": "float"},
    "weld_proj": {"label": "焊接凸点高", "unit": "mm", "min": 0.5, "default": 1.5, "type": "float"},
    "weld_d":  {"label": "焊接凸点径", "unit": "mm", "min": 1.0, "default": 4.0, "type": "float"},
    "block_w": {"label": "T型块宽", "unit": "mm", "min": 4.0, "default": 16.0, "type": "float"},
    "block_h": {"label": "T型块高", "unit": "mm", "min": 4.0, "default": 10.0, "type": "float"},
    "block_l": {"label": "T型块长", "unit": "mm", "min": 8.0, "default": 28.0, "type": "float"},
    "flange_w": {"label": "T型法兰宽", "unit": "mm", "min": 8.0, "default": 24.0, "type": "float"},
    "flange_t": {"label": "T型法兰厚", "unit": "mm", "min": 1.0, "default": 3.0, "type": "float"},
    "ring_od": {"label": "吊环外径", "unit": "mm", "min": 8.0, "default": 30.0, "type": "float"},
    "ring_id": {"label": "吊环内径", "unit": "mm", "min": 4.0, "default": 18.0, "type": "float"},
    "ring_t":  {"label": "吊环厚", "unit": "mm", "min": 2.0, "default": 8.0, "type": "float"},
    "thread_angle": {"label": "螺纹牙型角", "unit": "°", "min": 30, "default": 60, "type": "float",
                    "note": "公制/UN 系列 60°；英制惠氏 BSW 55°"},
    "fillet":  {"label": "倒圆(棱边圆角)", "unit": "", "default": False, "type": "bool"},
    "fillet_r": {"label": "圆角半径", "unit": "mm", "min": 0.1, "default": 0.5, "type": "float"},
}

# 每个螺母类型：名称、标准、大类、外廓(shape)、说明、可见参数、默认值、标准尺寸预设
NUT_TYPES = {
    "hex_type1": {
        "key": "hex_type1", "name": "1型六角螺母", "en": "Hexagon nut, style 1",
        "standard": "GB/T 6170 / ISO 4032 / DIN 934",
        "category": "hex", "shape": "hex",
        "desc": "最常用标准六角螺母，高度约为 0.8D，A/B 级。",
        "params": ["D", "P", "s", "m", "chamfer", "bore", "thread"],
        "defaults": {"D": 12.0, "P": 1.75, "s": 18.0, "m": 10.8, "chamfer": 0.8,
                     "bore": 12.0, "thread": True},
        "presets": {
            "M3":  {"P": 0.5,  "s": 5.5,  "m": 2.4},
            "M4":  {"P": 0.7,  "s": 7.0,  "m": 3.2},
            "M5":  {"P": 0.8,  "s": 8.0,  "m": 4.7},
            "M6":  {"P": 1.0,  "s": 10.0, "m": 5.2},
            "M8":  {"P": 1.25, "s": 13.0, "m": 6.8},
            "M10": {"P": 1.5,  "s": 16.0, "m": 8.4},
            "M12": {"P": 1.75, "s": 18.0, "m": 10.8},
            "M14": {"P": 2.0,  "s": 21.0, "m": 12.8},
            "M16": {"P": 2.0,  "s": 24.0, "m": 14.8},
            "M20": {"P": 2.5,  "s": 30.0, "m": 18.0},
            "M24": {"P": 3.0,  "s": 36.0, "m": 21.5},
            "M30": {"P": 3.5,  "s": 46.0, "m": 25.6},
            "M36": {"P": 4.0,  "s": 55.0, "m": 31.0},
        },
    },
    "hex_type2": {
        "key": "hex_type2", "name": "2型六角螺母", "en": "Hexagon nut, style 2",
        "standard": "GB/T 6175 / ISO 4033 / DIN 970",
        "category": "hex", "shape": "hex",
        "desc": "厚度较厚（约 1.0D 及以上），用于高强度连接，性能等级 9/12。",
        "params": ["D", "P", "s", "m", "chamfer", "bore", "thread"],
        "defaults": {"D": 12.0, "P": 1.75, "s": 18.0, "m": 12.0, "chamfer": 0.8,
                     "bore": 12.0, "thread": True},
        "presets": {
            "M5":  {"P": 0.8,  "s": 8.0,  "m": 5.1},
            "M6":  {"P": 1.0,  "s": 10.0, "m": 5.7},
            "M8":  {"P": 1.25, "s": 13.0, "m": 7.5},
            "M10": {"P": 1.5,  "s": 16.0, "m": 9.3},
            "M12": {"P": 1.75, "s": 18.0, "m": 12.0},
            "M14": {"P": 2.0,  "s": 21.0, "m": 14.1},
            "M16": {"P": 2.0,  "s": 24.0, "m": 16.4},
            "M20": {"P": 2.5,  "s": 30.0, "m": 20.3},
            "M24": {"P": 3.0,  "s": 36.0, "m": 23.9},
            "M30": {"P": 3.5,  "s": 46.0, "m": 28.6},
            "M36": {"P": 4.0,  "s": 55.0, "m": 34.7},
        },
    },
    "hex_thin": {
        "key": "hex_thin", "name": "六角薄螺母", "en": "Low hex nut",
        "standard": "GB/T 6172 / ISO 4035",
        "category": "hex", "shape": "hex",
        "desc": "高度最薄（约 0.5D），用于双螺母防松或空间受限场合。",
        "params": ["D", "P", "s", "m", "chamfer", "bore", "thread"],
        "defaults": {"D": 12.0, "P": 1.75, "s": 18.0, "m": 6.5, "chamfer": 0.8,
                     "bore": 12.0, "thread": True},
        "presets": {
            "M3":  {"P": 0.5,  "s": 5.5,  "m": 1.8},
            "M4":  {"P": 0.7,  "s": 7.0,  "m": 2.2},
            "M5":  {"P": 0.8,  "s": 8.0,  "m": 2.7},
            "M6":  {"P": 1.0,  "s": 10.0, "m": 3.2},
            "M8":  {"P": 1.25, "s": 13.0, "m": 4.0},
            "M10": {"P": 1.5,  "s": 16.0, "m": 5.0},
            "M12": {"P": 1.75, "s": 18.0, "m": 6.5},
            "M14": {"P": 2.0,  "s": 21.0, "m": 7.0},
            "M16": {"P": 2.0,  "s": 24.0, "m": 8.0},
            "M20": {"P": 2.5,  "s": 30.0, "m": 10.0},
            "M24": {"P": 3.0,  "s": 36.0, "m": 12.0},
            "M30": {"P": 3.5,  "s": 46.0, "m": 14.0},
            "M36": {"P": 4.0,  "s": 55.0, "m": 17.0},
        },
    },
    "hex_flange": {
        "key": "hex_flange", "name": "六角法兰面螺母", "en": "Hex flange nut",
        "standard": "GB/T 6177.1 / DIN 6923",
        "category": "hex", "shape": "hex",
        "desc": "六角体 + 底部法兰盘，增大接触面积并具备防松效果。",
        "params": ["D", "P", "s", "m", "flange_d", "flange_t", "chamfer", "bore", "thread"],
        "defaults": {"D": 10.0, "P": 1.5, "s": 16.0, "m": 9.5, "flange_d": 19.2,
                     "flange_t": 2.3, "chamfer": 0.6, "bore": 10.0, "thread": True},
        "presets": {
            "M5":  {"P": 0.8,  "s": 8.0,  "m": 5.1,  "flange_d": 10.9, "flange_t": 1.6},
            "M6":  {"P": 1.0,  "s": 10.0, "m": 5.7,  "flange_d": 12.2, "flange_t": 1.8},
            "M8":  {"P": 1.25, "s": 13.0, "m": 7.9,  "flange_d": 16.3, "flange_t": 2.0},
            "M10": {"P": 1.5,  "s": 16.0, "m": 9.5,  "flange_d": 19.2, "flange_t": 2.3},
            "M12": {"P": 1.75, "s": 18.0, "m": 12.2, "flange_d": 22.5, "flange_t": 2.6},
            "M16": {"P": 2.0,  "s": 24.0, "m": 15.9, "flange_d": 29.0, "flange_t": 3.0},
            "M20": {"P": 2.5,  "s": 30.0, "m": 19.0, "flange_d": 36.0, "flange_t": 3.5},
        },
    },
    "lock_nut": {
        "key": "lock_nut", "name": "锁紧螺母", "en": "Lock nut (nylon insert)",
        "standard": "GB/T 6183.1(非金属嵌件) / GB/T 6185.1(全金属)",
        "category": "hex", "shape": "hex",
        "desc": "通过尼龙嵌件或金属变形实现防松；本工具可对非金属型生成顶部嵌件凸台示意。钢体尺寸同 1 型。",
        "params": ["D", "P", "s", "m", "chamfer", "bore", "inset", "thread"],
        "defaults": {"D": 10.0, "P": 1.5, "s": 16.0, "m": 8.4, "chamfer": 0.8,
                     "bore": 10.0, "inset": True, "thread": True},
        "presets": {
            "M3":  {"P": 0.5,  "s": 5.5,  "m": 2.4},
            "M4":  {"P": 0.7,  "s": 7.0,  "m": 3.2},
            "M5":  {"P": 0.8,  "s": 8.0,  "m": 4.7},
            "M6":  {"P": 1.0,  "s": 10.0, "m": 5.2},
            "M8":  {"P": 1.25, "s": 13.0, "m": 6.8},
            "M10": {"P": 1.5,  "s": 16.0, "m": 8.4},
            "M12": {"P": 1.75, "s": 18.0, "m": 10.8},
            "M16": {"P": 2.0,  "s": 24.0, "m": 14.8},
            "M20": {"P": 2.5,  "s": 30.0, "m": 18.0},
            "M24": {"P": 3.0,  "s": 36.0, "m": 21.5},
        },
    },
    "round_nut": {
        "key": "round_nut", "name": "圆螺母", "en": "Round nut (with slots)",
        "standard": "GB/T 812 / GB/T 810（细牙）",
        "category": "round", "shape": "round",
        "desc": "圆柱形带扳手槽，用于轴承锁紧；本工具生成 4 道径向扳手槽（标准细牙螺纹）。",
        "params": ["D", "P", "outer_d", "m", "slot_w", "slot_d", "bore", "thread"],
        "defaults": {"D": 20.0, "P": 1.5, "outer_d": 35.0, "m": 8.0,
                     "slot_w": 5.3, "slot_d": 3.1, "bore": 20.0, "thread": True},
        "presets": {
            "M10×1":     {"P": 1.0,  "outer_d": 22.0, "m": 8.0,  "slot_w": 4.3,  "slot_d": 2.6},
            "M12×1.25":  {"P": 1.25, "outer_d": 25.0, "m": 8.0,  "slot_w": 4.3,  "slot_d": 2.6},
            "M14×1.5":   {"P": 1.5,  "outer_d": 28.0, "m": 8.0,  "slot_w": 4.3,  "slot_d": 2.6},
            "M16×1.5":   {"P": 1.5,  "outer_d": 30.0, "m": 8.0,  "slot_w": 5.3,  "slot_d": 3.1},
            "M18×1.5":   {"P": 1.5,  "outer_d": 32.0, "m": 8.0,  "slot_w": 5.3,  "slot_d": 3.1},
            "M20×1.5":   {"P": 1.5,  "outer_d": 35.0, "m": 8.0,  "slot_w": 5.3,  "slot_d": 3.1},
            "M22×1.5":   {"P": 1.5,  "outer_d": 38.0, "m": 10.0, "slot_w": 5.3,  "slot_d": 3.1},
            "M24×1.5":   {"P": 1.5,  "outer_d": 42.0, "m": 10.0, "slot_w": 5.3,  "slot_d": 3.1},
            "M27×1.5":   {"P": 1.5,  "outer_d": 45.0, "m": 10.0, "slot_w": 5.3,  "slot_d": 3.1},
            "M30×1.5":   {"P": 1.5,  "outer_d": 48.0, "m": 10.0, "slot_w": 5.3,  "slot_d": 3.1},
            "M36×1.5":   {"P": 1.5,  "outer_d": 55.0, "m": 10.0, "slot_w": 6.3,  "slot_d": 3.6},
            "M42×1.5":   {"P": 1.5,  "outer_d": 62.0, "m": 10.0, "slot_w": 6.3,  "slot_d": 3.6},
            "M48×1.5":   {"P": 1.5,  "outer_d": 72.0, "m": 12.0, "slot_w": 8.36, "slot_d": 4.25},
        },
    },
    "square_nut": {
        "key": "square_nut", "name": "方螺母", "en": "Square nut",
        "standard": "GB/T 39",
        "category": "square", "shape": "square",
        "desc": "方形外轮廓，用于槽钢等特殊场合；对角 e = s·√2。",
        "params": ["D", "P", "s", "m", "chamfer", "bore", "thread"],
        "defaults": {"D": 12.0, "P": 1.75, "s": 18.0, "m": 10.0, "chamfer": 0.8,
                     "bore": 12.0, "thread": True},
        "presets": {
            "M3":  {"P": 0.5,  "s": 5.5,  "m": 2.4},
            "M4":  {"P": 0.7,  "s": 7.0,  "m": 3.2},
            "M5":  {"P": 0.8,  "s": 8.0,  "m": 4.0},
            "M6":  {"P": 1.0,  "s": 10.0, "m": 5.0},
            "M8":  {"P": 1.25, "s": 13.0, "m": 6.5},
            "M10": {"P": 1.5,  "s": 16.0, "m": 8.0},
            "M12": {"P": 1.75, "s": 18.0, "m": 10.0},
            "M14": {"P": 2.0,  "s": 21.0, "m": 11.0},
            "M16": {"P": 2.0,  "s": 24.0, "m": 13.0},
            "M20": {"P": 2.5,  "s": 30.0, "m": 16.0},
            "M24": {"P": 3.0,  "s": 36.0, "m": 19.0},
        },
    },
    "cap_nut": {
        "key": "cap_nut", "name": "盖形螺母", "en": "Cap nut",
        "standard": "GB/T 923",
        "category": "special", "shape": "hex",
        "desc": "顶部封闭（保护螺纹端部）；本工具以盖顶实体封堵内孔上端。",
        "params": ["D", "P", "s", "m", "cap_h", "chamfer", "bore", "thread"],
        "defaults": {"D": 10.0, "P": 1.5, "s": 16.0, "m": 18.0, "cap_h": 8.0,
                     "chamfer": 0.8, "bore": 10.0, "thread": True},
        "presets": {
            "M4":  {"P": 0.7,  "s": 7.0,  "m": 8.0,  "cap_h": 3.0},
            "M5":  {"P": 0.8,  "s": 8.0,  "m": 10.0, "cap_h": 3.6},
            "M6":  {"P": 1.0,  "s": 10.0, "m": 12.0, "cap_h": 5.0},
            "M8":  {"P": 1.25, "s": 13.0, "m": 15.0, "cap_h": 6.0},
            "M10": {"P": 1.5,  "s": 16.0, "m": 18.0, "cap_h": 8.0},
            "M12": {"P": 1.75, "s": 18.0, "m": 22.0, "cap_h": 10.0},
            "M14": {"P": 2.0,  "s": 21.0, "m": 25.0, "cap_h": 11.0},
            "M16": {"P": 2.0,  "s": 24.0, "m": 28.0, "cap_h": 13.0},
            "M20": {"P": 2.5,  "s": 30.0, "m": 34.0, "cap_h": 16.0},
            "M24": {"P": 3.0,  "s": 36.0, "m": 42.0, "cap_h": 19.0},
        },
    },
    "slotted_nut": {
        "key": "slotted_nut", "name": "六角开槽螺母", "en": "Castellated / slotted hex nut",
        "standard": "GB/T 6178 / DIN 935（开槽/槽形）",
        "category": "hex", "shape": "hex",
        "desc": "六角体 + 顶部开槽（用于配合开口销防松）；本工具在顶面生成多道径向开槽。",
        "params": ["D", "P", "s", "m", "chamfer", "bore", "slot", "thread"],
        "defaults": {"D": 12.0, "P": 1.75, "s": 18.0, "m": 10.8, "chamfer": 0.8,
                     "bore": 12.0, "slot": True, "thread": True},
        "presets": {
            "M6":  {"P": 1.0,  "s": 10.0, "m": 5.0},
            "M8":  {"P": 1.25, "s": 13.0, "m": 6.5},
            "M10": {"P": 1.5,  "s": 16.0, "m": 8.0},
            "M12": {"P": 1.75, "s": 18.0, "m": 10.0},
            "M16": {"P": 2.0,  "s": 24.0, "m": 13.0},
            "M20": {"P": 2.5,  "s": 30.0, "m": 16.0},
        },
    },
    "wing_nut": {
        "key": "wing_nut", "name": "蝶形螺母", "en": "Wing nut / butterfly nut",
        "standard": "GB/T 62.1（蝶形螺母）",
        "category": "special", "shape": "wing",
        "desc": "两侧蝶翼便于手拧；本工具以中央凸台 + 左右蝶翼近似建模，中心带螺纹孔。",
        "params": ["D", "P", "bore", "wing_span", "chamfer", "thread"],
        "defaults": {"D": 10.0, "P": 1.5, "bore": 10.0, "wing_span": 40.0,
                     "chamfer": 0.5, "thread": True},
        "presets": {
            "M4":  {"P": 0.7,  "wing_span": 22.0},
            "M5":  {"P": 0.8,  "wing_span": 26.0},
            "M6":  {"P": 1.0,  "wing_span": 30.0},
            "M8":  {"P": 1.25, "wing_span": 36.0},
            "M10": {"P": 1.5,  "wing_span": 40.0},
            "M12": {"P": 1.75, "wing_span": 48.0},
        },
    },
    "weld_nut": {
        "key": "weld_nut", "name": "焊接螺母", "en": "Weld nut",
        "standard": "GB/T 13681（六角焊接螺母）",
        "category": "special", "shape": "hex",
        "desc": "六角体 + 底部 3 个焊接凸点；本工具在主体下方生成 3 个 120° 分布的焊接凸台。",
        "params": ["D", "P", "s", "m", "bore", "weld_proj", "weld_d", "chamfer", "thread"],
        "defaults": {"D": 6.0, "P": 1.0, "s": 10.0, "m": 6.0, "bore": 6.0,
                     "weld_proj": 1.5, "weld_d": 4.0, "chamfer": 0.5, "thread": True},
        "presets": {
            "M4":  {"P": 0.7,  "s": 8.0,  "m": 5.0, "weld_d": 3.0},
            "M5":  {"P": 0.8,  "s": 9.0,  "m": 5.5, "weld_d": 3.5},
            "M6":  {"P": 1.0,  "s": 10.0, "m": 6.0, "weld_d": 4.0},
            "M8":  {"P": 1.25, "s": 13.0, "m": 7.5, "weld_d": 5.0},
            "M10": {"P": 1.5,  "s": 16.0, "m": 9.0, "weld_d": 6.0},
        },
    },
    "tslot_nut": {
        "key": "tslot_nut", "name": "T型螺母", "en": "T-slot nut",
        "standard": "GB/T 37（T型槽用螺母）近似",
        "category": "special", "shape": "tslot",
        "desc": "上部方形块 + 下部更宽的薄法兰（T 形截面），中心带螺纹孔，用于 T 型槽。",
        "params": ["D", "P", "bore", "block_w", "block_h", "block_l", "flange_w", "flange_t", "chamfer", "thread"],
        "defaults": {"D": 8.0, "P": 1.25, "bore": 8.0, "block_w": 16.0, "block_h": 10.0,
                     "block_l": 28.0, "flange_w": 24.0, "flange_t": 3.0, "chamfer": 0.5, "thread": True},
        "presets": {
            "M4":  {"P": 0.7,  "block_w": 10.0, "block_h": 7.0,  "block_l": 18.0, "flange_w": 15.0, "flange_t": 2.0},
            "M5":  {"P": 0.8,  "block_w": 12.0, "block_h": 8.0,  "block_l": 20.0, "flange_w": 18.0, "flange_t": 2.5},
            "M6":  {"P": 1.0,  "block_w": 14.0, "block_h": 9.0,  "block_l": 24.0, "flange_w": 20.0, "flange_t": 2.5},
            "M8":  {"P": 1.25, "block_w": 16.0, "block_h": 10.0, "block_l": 28.0, "flange_w": 24.0, "flange_t": 3.0},
            "M10": {"P": 1.5,  "block_w": 20.0, "block_h": 12.0, "block_l": 34.0, "flange_w": 30.0, "flange_t": 3.5},
            "M12": {"P": 1.75, "block_w": 22.0, "block_h": 14.0, "block_l": 40.0, "flange_w": 34.0, "flange_t": 4.0},
        },
    },
    "eye_nut": {
        "key": "eye_nut", "name": "吊环螺母", "en": "Eye nut / lifting nut",
        "standard": "GB/T 825（吊环螺钉/螺母类）近似",
        "category": "special", "shape": "eye",
        "desc": "中央螺纹凸台 + 顶部环形吊环；本工具以环形实体（外圆+内孔）近似吊环截面。",
        "params": ["D", "P", "bore", "ring_od", "ring_id", "ring_t", "chamfer", "thread"],
        "defaults": {"D": 10.0, "P": 1.5, "bore": 10.0, "ring_od": 30.0, "ring_id": 18.0,
                     "ring_t": 8.0, "chamfer": 0.5, "thread": True},
        "presets": {
            "M6":  {"P": 1.0,  "ring_od": 22.0, "ring_id": 13.0, "ring_t": 6.0},
            "M8":  {"P": 1.25, "ring_od": 26.0, "ring_id": 16.0, "ring_t": 7.0},
            "M10": {"P": 1.5,  "ring_od": 30.0, "ring_id": 18.0, "ring_t": 8.0},
            "M12": {"P": 1.75, "ring_od": 34.0, "ring_id": 20.0, "ring_t": 9.0},
            "M16": {"P": 2.0,  "ring_od": 42.0, "ring_id": 26.0, "ring_t": 11.0},
            "M20": {"P": 2.5,  "ring_od": 50.0, "ring_id": 32.0, "ring_t": 14.0},
        },
    },
}


def derive_e(s, shape="hex"):
    """由对边宽度 s 派生对角宽度 e。六角 e = 2s/√3；方螺母 e = s·√2。"""
    if shape == "square":
        return s * math.sqrt(2.0)
    return 2.0 * s / math.sqrt(3.0)


def _parse_D_from_label(label):
    """从规格标签解析公称直径 D，例如 'M20×1.5' -> 20.0，'M12' -> 12.0。"""
    m = re.search(r"M?\s*(\d+(?:\.\d+)?)", str(label))
    return float(m.group(1)) if m else None


def get_type(key):
    return NUT_TYPES.get(key)


def default_params(key):
    """返回某类型带 e、bore 派生值后的完整参数 dict。"""
    t = NUT_TYPES[key]
    p = dict(t["defaults"])
    p["bore"] = p["D"]  # 内孔直径默认等于公称直径
    if "s" in p:
        p["e"] = derive_e(p["s"], t.get("shape", "hex"))
    return p


def apply_preset(key, size_label):
    """套用标准尺寸预设，返回合并后的参数字典（含派生的 e、bore）。"""
    t = NUT_TYPES[key]
    p = dict(t["defaults"])
    if size_label in t.get("presets", {}):
        p.update(t["presets"][size_label])
        d = _parse_D_from_label(size_label)
        if d is not None:
            p["D"] = d
    p["bore"] = p["D"]
    if "s" in p:
        p["e"] = derive_e(p["s"], t.get("shape", "hex"))
    return p


def visible_params(key):
    """返回该类型在 UI 中展示的参数键列表。"""
    return NUT_TYPES[key]["params"]
