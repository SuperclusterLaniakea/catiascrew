# -*- coding: utf-8 -*-
"""
纯几何计算模块（与 CATIA 解耦，可离线运行/测试）。

职责：根据螺母类型 + 参数，计算 2D 轮廓（外轮廓、内孔）并生成一组
"建模步骤(steps)"。CATIA 控制器 (catia_controller.py) 只负责把这些步骤
翻译成 CATIA COM 调用；本模块本身不依赖 pywin32 / CATIA。

================= 建模坐标系约定（重要） =================
CATIA late-binding 下**取不到实体的面引用**（.Faces / Selection.Search 均
不可用），因此所有特征必须能在固定的原始平面（XY/ZX）上完成。为此统一约定：

    z = 0     -> 螺母"顶面"（草图基准面 = PlaneXY）
    z = -m    -> 螺母"底面"
    主体一律从 XY 平面向 -Z 拉伸  =>  顶面恰好落在 XY 平面上，
    于是"顶面挖槽 / 顶面凸台 / 中心孔"都能直接在 XY 平面上做，无需取面。

轮廓表示：
  polygon : {"kind":"polygon", "points":[(x,y), ...]}            闭合多边形
  circle  : {"kind":"circle",  "cx":0.0, "cy":0.0, "r":r}        闭合圆

建模步骤 op：
  "pad"    : 从 z=0 向 -Z 拉伸 length（主体及底部特征）。outer 必填，inner 可选。
  "pad_up" : 从 z=0 向 +Z 拉伸 length（顶部凸台：吊环、锁紧嵌件）。
  "pocket" : 从 z=0 向 -Z 挖槽 depth（顶面扳手槽等）。
  "hole"   : 中心孔；从 z=0 向下 depth（贯穿/盲孔）。带螺纹时孔径取小径。
  "thread" : 真实螺纹齿形（牙型 60°/55°），由"多牙型轮廓一次 Groove 旋转
             切除"实现（major_r=大径/2, minor_r=小径/2, pitch, depth, angle）。
  "chamfer": 45° 锥形倒角（size），由 Groove 旋转切除实现，需 outer_r。
  "fillet" : 棱边倒圆（radius）。
"""

import math

from nut_data import NUT_TYPES, derive_e


# ---------- 基础轮廓生成 ----------

def _hex_points(s, rot_deg=0.0):
    """正多边形(6边)顶点，flat-to-flat = s，顶点在角度 rot 起算。"""
    R = s / math.sqrt(3.0)  # 外接圆半径（中心->顶点）
    return [(R * math.cos(math.radians(60.0 * i + rot_deg)),
             R * math.sin(math.radians(60.0 * i + rot_deg))) for i in range(6)]


def _square_points(s, rot_deg=45.0):
    """正方形顶点，flat-to-flat = s。默认旋转 45° 使边与坐标轴平行。"""
    r = s / math.sqrt(2.0)
    return [(r * math.cos(math.radians(rot_deg + 90.0 * i)),
             r * math.sin(math.radians(rot_deg + 90.0 * i))) for i in range(4)]


def _circle(cx, cy, r):
    return {"kind": "circle", "cx": cx, "cy": cy, "r": r}


def _polygon(points):
    return {"kind": "polygon", "points": [tuple(p) for p in points]}


def _rect(w, l):
    """以原点为中心的矩形（宽 w 沿 x，高 l 沿 y）。"""
    hw, hl = w / 2.0, l / 2.0
    return _polygon([(-hw, -hl), (hw, -hl), (hw, hl), (-hw, hl)])


def _rect_at(w, l, cx, cy):
    """以 (cx,cy) 为中心的矩形。"""
    hw, hl = w / 2.0, l / 2.0
    return _polygon([(cx - hw, cy - hl), (cx + hw, cy - hl),
                     (cx + hw, cy + hl), (cx - hw, cy + hl)])


def _thread_height(P, angle=60.0):
    """ISO 基本牙型牙高 h = 5H/8，H = P / (2·tan(a/2))。

    60° 公制/UN : h ≈ 0.54127·P
    55° 英制 BSW: h ≈ 0.64033·P
    牙型的"半底宽"恒为 5P/16 = 0.3125·P（与牙型角无关，推导见控制器注释）。
    """
    a = math.radians(float(angle))
    if a <= 0:
        return 0.54127 * P
    H = P / (2.0 * math.tan(a / 2.0))
    return 5.0 * H / 8.0


def _outer_radius(contour):
    """轮廓的最大外接半径（倒角锥面用）。"""
    if contour["kind"] == "circle":
        return abs(contour["cx"]) + contour["r"]
    return max(math.hypot(x, y) for (x, y) in contour["points"])


def _round_slots(outer_r, slot_w, slot_d, count=4):
    """圆螺母/六角开槽螺母的扳手槽：围绕顶面的 count 个矩形凹槽轮廓。"""
    slots = []
    base_r = outer_r - slot_d  # 槽底半径
    for k in range(count):
        ang = math.radians(360.0 / count * k)
        local = [(-slot_w / 2.0, 0.0), (slot_w / 2.0, 0.0),
                 (slot_w / 2.0, slot_d), (-slot_w / 2.0, slot_d)]
        pts = []
        for (lx, ly) in local:
            rx = base_r + ly
            x = rx * math.cos(ang) - lx * math.sin(ang)
            y = rx * math.sin(ang) + lx * math.cos(ang)
            pts.append((x, y))
        slots.append(_polygon(pts))
    return slots


# ---------- 主建模函数 ----------

def build_model(type_key, params):
    """返回建模模型 dict：{name, type_key, steps, preview}。"""
    t = NUT_TYPES[type_key]
    D = float(params.get("D", 12.0))
    P = float(params.get("P", 1.75))
    s = float(params.get("s", 18.0))
    m = float(params.get("m", 10.8))
    bore = float(params.get("bore", D))
    chamfer = float(params.get("chamfer", 0.8))
    thread = bool(params.get("thread", False))
    thread_angle = float(params.get("thread_angle", 60.0))
    fillet = bool(params.get("fillet", False))
    fillet_r = float(params.get("fillet_r", 0.5))
    bore_r = bore / 2.0
    th_h = _thread_height(P, thread_angle)          # 牙高
    minor_bore = max(bore - 2.0 * th_h, bore * 0.55)  # 内螺纹小径(牙顶径)

    steps = []

    def add_hole_if(cap_h=0.0):
        """中心孔 + 真实螺纹齿形。

        CATIA 的 Hole 特征即使 ThreadingMode=1 也**只带属性不生成齿形几何**
        （实测 3D 中看不到牙）。因此这里改为"真几何"两步走：
          1) 光孔按内螺纹小径(牙顶径) d1 = D - 2h 钻；
          2) "thread" 步骤用牙型旋转切除把牙槽切到大径 D → 真实 V 形牙。
        """
        blind = bool(cap_h and cap_h > 0)
        depth = max(0.5, m - cap_h) if blind else m
        steps.append({"op": "hole",
                      "diam": minor_bore if thread else bore,
                      "depth": depth, "pitch": P, "angle": thread_angle,
                      "thread": thread, "blind": blind})
        if thread:
            steps.append({"op": "thread",
                          "major_r": bore_r,              # 牙根半径(大径/2)
                          "minor_r": minor_bore / 2.0,    # 牙顶半径(小径/2)
                          "pitch": P, "depth": depth,
                          "angle": thread_angle})

    def add_chamfer_if(outer_r, faces=("top", "bottom")):
        if chamfer and chamfer > 0 and outer_r > chamfer * 1.2:
            steps.append({"op": "chamfer", "size": chamfer,
                          "outer_r": outer_r,
                          "top_z": 0.0, "bottom_z": -m,
                          "faces": list(faces)})

    def add_fillet_if():
        if fillet and fillet_r and fillet_r > 0:
            r = fillet_r
            if thread:
                # AddNewAutoFillet 会圆化**所有**棱边，含牙尖；半径接近牙高时
                # 会把螺纹牙磨平。限制到牙高的 1/4，保证齿形保留。
                r = min(r, max(0.05, th_h * 0.25))
            steps.append({"op": "fillet", "radius": r})

    # ---------------- 六角族（含方、六角开槽） ----------------
    if type_key in ("hex_type1", "hex_type2", "hex_thin", "lock_nut",
                    "square_nut", "slotted_nut"):
        outer = _polygon(_square_points(s)) if type_key == "square_nut" \
            else _polygon(_hex_points(s))
        R = _outer_radius(outer)
        steps.append({"op": "pad", "plane": "XY", "outer": outer,
                      "inner": None, "length": m})
        if type_key == "lock_nut" and params.get("inset"):
            inset_r = min(bore_r * 1.15, R * 0.9)
            steps.append({"op": "pad_up", "plane": "XY",
                          "outer": _circle(0, 0, inset_r), "inner": None,
                          "length": 1.5, "note": "inset collar (approximation)"})
        if type_key == "slotted_nut" and params.get("slot", True):
            slot_w = max(1.5, s * 0.12)
            slot_d = min(m * 0.45, 5.0)
            for slot in _round_slots(R, slot_w, slot_d, count=6):
                steps.append({"op": "pocket", "face": "top",
                              "contour": slot, "depth": slot_d})
        add_hole_if()
        add_chamfer_if(R, ("top", "bottom") if type_key != "lock_nut" else ("top",))
        add_fillet_if()

    # ---------------- 六角法兰面 ----------------
    elif type_key == "hex_flange":
        flange_d = float(params.get("flange_d", derive_e(s) * 1.3))
        flange_t = float(params.get("flange_t", 2.5))
        hexo = _polygon(_hex_points(s))
        R = _outer_radius(hexo)
        # 底部法兰：宽轮廓，拉满全长 m
        steps.append({"op": "pad", "plane": "XY",
                      "outer": _circle(0, 0, flange_d / 2.0),
                      "inner": None, "length": m})
        # 六角体：短 flange_t，露出底部一圈法兰
        steps.append({"op": "pad", "plane": "XY", "outer": hexo,
                      "inner": None, "length": max(0.5, m - flange_t)})
        add_hole_if()
        add_chamfer_if(R, ("top",))
        add_fillet_if()

    # ---------------- 圆螺母（带扳手槽） ----------------
    elif type_key == "round_nut":
        outer_d = float(params.get("outer_d", derive_e(s)))
        outer_r = outer_d / 2.0
        slot_w = float(params.get("slot_w", 4.0))
        slot_d = float(params.get("slot_d", 3.0))
        steps.append({"op": "pad", "plane": "XY",
                      "outer": _circle(0, 0, outer_r), "inner": None, "length": m})
        for slot in _round_slots(outer_r, slot_w, slot_d, count=4):
            steps.append({"op": "pocket", "face": "top",
                          "contour": slot, "depth": slot_d})
        add_hole_if()
        add_chamfer_if(outer_r, ("top", "bottom"))
        add_fillet_if()

    # ---------------- 盖形螺母（封闭盖顶） ----------------
    elif type_key == "cap_nut":
        cap_h = float(params.get("cap_h", 6.0))
        cap_h = min(cap_h, m - 1.0)
        hexo = _polygon(_hex_points(s))
        R = _outer_radius(hexo)
        steps.append({"op": "pad", "plane": "XY", "outer": hexo,
                      "inner": None, "length": m})
        add_hole_if(cap_h)
        add_chamfer_if(R, ("top",))
        add_fillet_if()

    # ---------------- 蝶形螺母 ----------------
    elif type_key == "wing_nut":
        wing_span = float(params.get("wing_span", 40.0))
        boss_r = max(bore_r + 3.0, D * 0.8)
        steps.append({"op": "pad", "plane": "XY",
                      "outer": _circle(0, 0, boss_r), "inner": None, "length": m})
        wing_w = wing_span * 0.30
        wing_h = wing_span * 0.32
        cx_w = wing_span / 2.0 - wing_w / 2.0
        for sign in (1.0, -1.0):
            steps.append({"op": "pad", "plane": "XY",
                          "outer": _rect_at(wing_w, wing_h, sign * cx_w, 0.0),
                          "inner": None, "length": m})
        add_hole_if()
        add_chamfer_if(boss_r, ("top",))
        add_fillet_if()

    # ---------------- 焊接螺母（底部 3 焊接凸点） ----------------
    elif type_key == "weld_nut":
        weld_proj = float(params.get("weld_proj", 1.5))
        weld_d = float(params.get("weld_d", 4.0))
        hexo = _polygon(_hex_points(s))
        R = _outer_radius(hexo)
        steps.append({"op": "pad", "plane": "XY", "outer": hexo,
                      "inner": None, "length": m})
        # 凸点比主体窄：拉穿主体并多伸出 weld_proj，布尔并集后即为底部凸点
        rc = s / 2.0 * 0.55
        for k in range(3):
            ang = math.radians(120.0 * k)
            bx, by = rc * math.cos(ang), rc * math.sin(ang)
            steps.append({"op": "pad", "plane": "XY",
                          "outer": _circle(bx, by, weld_d / 2.0),
                          "inner": None, "length": m + weld_proj})
        add_hole_if()
        add_chamfer_if(R, ("top",))
        add_fillet_if()

    # ---------------- T 型螺母 ----------------
    elif type_key == "tslot_nut":
        block_w = float(params.get("block_w", 16.0))
        block_h = float(params.get("block_h", 10.0))
        block_l = float(params.get("block_l", 28.0))
        flange_w = float(params.get("flange_w", 24.0))
        flange_t = float(params.get("flange_t", 3.0))
        block = _rect(block_w, block_l)
        m = block_h          # T 型块没有独立的 m 参数，主体高度即 block_h
        # 底部宽法兰拉满全长；上部窄块短 flange_t
        steps.append({"op": "pad", "plane": "XY",
                      "outer": _rect(flange_w, block_l), "inner": None,
                      "length": block_h})
        steps.append({"op": "pad", "plane": "XY", "outer": block, "inner": None,
                      "length": max(0.5, block_h - flange_t)})
        add_hole_if()
        add_chamfer_if(_outer_radius(block), ("top",))
        add_fillet_if()

    # ---------------- 吊环螺母 ----------------
    elif type_key == "eye_nut":
        ring_od = float(params.get("ring_od", 30.0))
        ring_id = float(params.get("ring_id", 18.0))
        ring_t = float(params.get("ring_t", 8.0))
        boss_r = max(bore_r + 3.0, D * 0.8)
        steps.append({"op": "pad", "plane": "XY",
                      "outer": _circle(0, 0, boss_r), "inner": None, "length": m})
        # 吊环：顶部正向拉伸的环形（外圆 + 内孔）
        steps.append({"op": "pad_up", "plane": "XY",
                      "outer": _circle(0, 0, ring_od / 2.0),
                      "inner": _circle(0, 0, ring_id / 2.0),
                      "length": ring_t})
        add_hole_if()
        add_chamfer_if(boss_r, ("top",))
        add_fillet_if()

    else:
        raise ValueError(f"未知螺母类型: {type_key}")

    # 预览信息：取首个 pad 的外轮廓 + 内孔半径
    first = next((st for st in steps if st["op"] in ("pad", "pad_up")), None)
    preview = {
        "outer": first["outer"] if first else _polygon(_hex_points(s)),
        "bore_r": bore_r,
        "type_key": type_key,
    }

    size_label = f"M{format_num(D)}"
    name = f"{t['name']}_{size_label}"
    return {"name": name, "type_key": type_key, "steps": steps, "preview": preview}


def format_num(v):
    """把浮点格式化为去掉多余小数的字符串，如 12.0 -> '12'。"""
    if abs(v - round(v)) < 1e-6:
        return str(int(round(v)))
    return f"{v:g}"


def preview_outline(type_key, params):
    """便捷函数：仅返回预览轮廓（外轮廓 + 内孔半径）。"""
    return build_model(type_key, params)["preview"]


def list_all_types():
    """返回 (category, [type_keys]) 分组列表，供 UI 树展示。"""
    from nut_data import NUT_CATEGORIES
    out = []
    for cat_key, cat_name in NUT_CATEGORIES:
        keys = [k for k, v in NUT_TYPES.items() if v["category"] == cat_key]
        if keys:
            out.append((cat_key, cat_name, keys))
    return out
