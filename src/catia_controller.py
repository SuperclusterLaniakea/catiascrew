# -*- coding: utf-8 -*-
"""
CATIA V5 自动化控制器（纯 late binding，"无面引用"配方）。

=========================== 为什么这样写 ===========================
在本机 CATIA V5-6R2022 + pywin32 组合下，实测结论（真机探针验证）：

  * 早期绑定(makepy/gencache) **不可用**：生成的 Factory 类残缺，连
    AddNewPad / AddNewHole / Body.Shape 都没有。=> 必须纯 late binding。
  * late binding 下 **取不到实体面**：`Pad.Faces`、`Body.Shape`、
    `Selection.Search` 全部报错。而 AddNewChamfer 必须传面引用，
    偏置平面(AddNewPlaneOffset)上建草图也失败。
  * 可用的：Sketches.Add(Plane对象)、AddNewPad、**负长度 Pad**、
    AddNewPocket、AddNewHoleFromPoint(以 PlaneXY 为支撑)、
    AddNewGroove(需 HybridShapeFactory 建的直线作轴)、AddNewAutoFillet。

=> 因此采用"**无面引用配方**"：
     主体从 XY 平面向 -Z 拉伸，使**顶面恰好落在 XY 平面**，
     于是顶面挖槽/凸台/中心孔都能直接在原始平面完成；
     倒角改用 Groove 旋转切除生成 45° 锥面；
     底部凸台用"穿透主体的负长度 Pad"（布尔并集自然成形）。

所有 CATIA 调用均 try/except 保底：核心实体与螺纹孔优先保证，
装饰特征（倒角/倒圆）失败仅记录警告，不中断建模。

仅在真正连接 CATIA 时才需要 pywin32；本模块顶部懒加载 win32com，
因此离线 import（仅用于结构检查）不会报错。
"""

import math
import sys


class CatiaController:
    def __init__(self, log=None):
        self.catia = None
        self.doc = None
        self.part = None
        self._axis_ref = None      # 混合直线轴（Groove 回转轴），懒建
        self._log = log or (lambda msg: None)

    # ---------------- 连接 ----------------

    def connect(self, launch_if_needed=False):
        """连接到 CATIA。优先取已运行实例；launch_if_needed=True 时尝试启动。

        强制 late binding：先 import win32com.client.dynamic，再用
        dynamic.Dispatch / dynamic.GetActiveObject，避免 gencache 里残留的
        残缺 CATIA 早期绑定包装把 ShapeFactory 变成不可用的 Factory 类。
        """
        try:
            import win32com.client
            import win32com.client.dynamic
        except ImportError:
            self._log("[错误] 未找到 pywin32，请先 pip install pywin32 并运行于 Windows。")
            raise

        # 1) 优先挂到已经运行的 CATIA（ROT）
        self.catia = None
        try:
            self.catia = win32com.client.GetActiveObject("CATIA.Application")
            self._log("[连接] 已连接到正在运行的 CATIA 实例。")
        except Exception:
            self.catia = None

        # 2) 若拿到的是 gencache 生成的"早期绑定"包装（type 名不是 CDispatch），
        #    其 Factory/Body 类是残缺的（实测没有 AddNewPad / Shape），必须换回
        #    late binding 的 CDispatch。
        if self.catia is not None and type(self.catia).__name__ != "CDispatch":
            self._log("[连接] 检测到早期绑定包装，切换为 late binding。")
            self.catia = None

        if self.catia is None:
            if launch_if_needed:
                self.catia = win32com.client.dynamic.Dispatch("CATIA.Application")
                try:
                    self.catia.Visible = True
                except Exception:
                    pass
                self._log("[连接] 已启动 CATIA。")
            else:
                self._log("[错误] 未找到运行中的 CATIA，请先打开 CATIA 后再试（或勾选'自动启动'）。")
                raise RuntimeError("未找到运行中的 CATIA 实例")
        try:
            self.catia.DisplayFileAlerts = False
        except Exception:
            pass
        return self.catia

    def is_connected(self):
        return self.catia is not None

    # ---------------- 零件 ----------------

    def new_part(self, name="Nut"):
        self.doc = self.catia.Documents.Add("Part")
        self.part = self.doc.Part
        try:
            self.part.Name = name
        except Exception:
            pass
        try:
            self.part.InWorkObject = self.part.MainBody
        except Exception:
            pass
        self._axis_ref = None
        self._log(f"[零件] 已新建零件：{name}")
        return self.part

    # ---------------- 草图辅助 ----------------

    def _plane(self, which):
        oe = self.part.OriginElements
        if which == "YZ":
            return oe.PlaneYZ
        if which == "ZX":
            return oe.PlaneZX
        return oe.PlaneXY

    def _draw_contour(self, f2d, c, sketch=None):
        if c["kind"] == "polygon":
            pts = c["points"]
            n = len(pts)
            lines = []
            for i in range(n):
                x1, y1 = pts[i]
                x2, y2 = pts[(i + 1) % n]
                lines.append(f2d.CreateLine(x1, y1, x2, y2))
            self._try_coincidence(sketch, lines)
        else:  # circle
            f2d.CreateClosedCircle(c["cx"], c["cy"], c["r"])

    def _try_coincidence(self, sketch, lines):
        """为相邻线段端点添加重合约束（catCstTypeOn = 2），强制闭合轮廓。

        CATIA 约束枚举中 0=Reference, 1=Distance, 2=On(重合)。
        误用 1(Distance) 会把多边形端点强加距离约束 → 过约束/几何损坏。
        """
        if sketch is None or len(lines) < 2:
            return
        try:
            cons = sketch.Constraints
            n = len(lines)
            for i in range(n):
                ref_end = self.part.CreateReferenceFromObject(lines[i].EndPoint)
                ref_start = self.part.CreateReferenceFromObject(lines[(i + 1) % n].StartPoint)
                cons.AddBiEltCst(2, ref_end, ref_start)
        except Exception:
            pass

    def _sketch(self, plane, contours):
        """在原始平面上建草图并绘制若干轮廓。直接传 Plane 对象（CATIA 实测要求）。"""
        body = self.part.Bodies.Item(1)
        sk = body.Sketches.Add(self._plane(plane))
        f2d = sk.OpenEdition()
        for c in contours:
            if c is not None:
                self._draw_contour(f2d, c, sk)
        sk.CloseEdition()
        try:
            self.part.Update()
        except Exception:
            pass
        return sk

    def _hybrid_axis(self):
        """返回一条沿 Z 轴的混合直线引用（Groove 的回转轴）。

        关键：轴必须是 HybridShapeFactory 建的直线；用草图里的线作轴时
        CATIA 的 Groove 在 Update 阶段会失败（实测结论）。
        """
        if self._axis_ref is not None:
            return self._axis_ref
        hsf = self.part.HybridShapeFactory
        p1 = hsf.AddNewPointCoord(0.0, 0.0, -500.0)
        p2 = hsf.AddNewPointCoord(0.0, 0.0, 500.0)
        line = hsf.AddNewLinePtPt(p1, p2)
        try:
            self.part.Update()
        except Exception:
            pass
        self._axis_ref = self.part.CreateReferenceFromObject(line)
        return self._axis_ref

    # ---------------- 特征 ----------------

    def _pad(self, step):
        """从 z=0 向 -Z 拉伸（主体 / 底部特征）。"""
        sk = self._sketch(step.get("plane", "XY"),
                          [step.get("outer"), step.get("inner")])
        length = abs(float(step["length"]))
        pad = self.part.ShapeFactory.AddNewPad(sk, -length)
        self.part.Update()
        self._log(f"[Pad] 向下拉伸 {length:g} mm")
        return pad

    def _pad_up(self, step):
        """从 z=0 向 +Z 拉伸（顶部凸台：吊环、锁紧嵌件）。"""
        sk = self._sketch(step.get("plane", "XY"),
                          [step.get("outer"), step.get("inner")])
        length = abs(float(step["length"]))
        pad = self.part.ShapeFactory.AddNewPad(sk, length)
        self.part.Update()
        self._log(f"[Pad] 顶部凸台 +{length:g} mm")
        return pad

    def _pocket(self, step):
        """从顶面 z=0 向 -Z 挖槽（扳手槽等）。"""
        sk = self._sketch("XY", [step.get("contour")])
        depth = abs(float(step["depth"]))
        pocket = self.part.ShapeFactory.AddNewPocket(sk, depth)
        self.part.Update()
        self._log(f"[Pocket] 顶面挖槽 {depth:g} mm")
        return pocket

    def _hole(self, step):
        """中心孔（可带真实螺纹）。

        用 AddNewHoleFromPoint(0,0,0, PlaneXY引用, 深度) —— 以 XY 平面为支撑，
        显式给定孔心 (0,0,0)，CATIA 自动投影定位，无需定位草图。
        螺纹：ThreadingMode=1 + ThreadSide=1（右旋内螺纹）+ ThreadDepth。
        注意：late binding 下 ThreadPitch / LateralThread 不可写，CATIA 按
        直径自动取标准螺距并生成螺纹（Hole 默认即 ThreadingMode=1）。
        """
        sf = self.part.ShapeFactory
        depth = abs(float(step["depth"]))
        diam = float(step["diam"])
        try:
            support = self.part.CreateReferenceFromObject(self.part.OriginElements.PlaneXY)
            hole = sf.AddNewHoleFromPoint(0.0, 0.0, 0.0, support, depth)
        except Exception as e:
            self._log(f"[孔] AddNewHoleFromPoint 失败：{e}")
            return None

        try:
            hole.Diameter.Value = diam
        except Exception as e:
            self._log(f"[孔] 直径设置失败：{e}")

        if step.get("thread"):
            for attr, val in (("ThreadingMode", 1), ("ThreadSide", 1)):
                try:
                    setattr(hole, attr, val)
                except Exception:
                    pass
            try:
                hole.ThreadDepth.Value = depth
            except Exception:
                pass
            try:
                mode = hole.ThreadingMode
            except Exception:
                mode = 1
            self._log(f"[螺纹] 内螺纹孔 Φ{diam:g} 深 {depth:g} mm "
                      f"（牙型角 {step.get('angle', 60)}°，ThreadingMode={mode}）")
        else:
            # 关掉默认螺纹 → 光孔。实测：部分配置下 ThreadingMode=0 会让
            # part.Update() 失败（进而污染零件状态、连累后续倒角），
            # 因此失败时必须回滚为 1 并重新 Update。
            try:
                hole.ThreadingMode = 0
            except Exception:
                pass
            try:
                self.part.Update()
            except Exception:
                try:
                    hole.ThreadingMode = 1
                    self.part.Update()
                except Exception:
                    pass
            self._log(f"[孔] 光孔 Φ{diam:g} 深 {depth:g} mm")

        try:
            self.part.Update()
        except Exception as e:
            self._log(f"[孔] 更新警告（孔可能已生成）：{e}")
        return hole

    def _thread(self, step):
        """真实螺纹齿形几何：多牙型轮廓 + 一次 Groove(旋转切除)。

        为什么不用 Hole 的 ThreadingMode / AddNewHelix / AddNewSlot：
          实测（真机探针）CATIA V5-6R2022 + pywin32 late binding 下——
            * Hole(ThreadingMode=1) 只写螺纹**属性**，3D 中不生成齿形几何；
            * HybridShapeFactory.AddNewHelix  -> "找不到成员"（不可用）；
            * ShapeFactory.AddNewSlot/Rib     -> 第 2 参数"类型不匹配"（不可用）。
          唯一能真正切出齿形的是 AddNewGroove（旋转切除），已真机验证。

        做法：在 ZX 平面（草图坐标 h=z, v=x）按螺距画 N 个 60°(或 55°)
        牙型三角形，一次 Groove 绕 Z 轴 360° 旋转切除：
            三角形顶点 (z_i, major_r)  -> 牙根，靠外（大径/2）
            三角形底边 (z_i ± 5P/16, minor_r) -> 牙顶，靠内（小径/2）
        切除后在孔壁上留下宽 P-5P/8=3P/8 的牙顶 + 向外渐宽的牙根，
        即标准 ISO 三角形牙型（V 形齿）。
        """
        pitch = float(step["pitch"])
        major_r = float(step["major_r"])
        minor_r = float(step["minor_r"])
        depth = abs(float(step["depth"]))
        angle = float(step.get("angle", 60.0))
        if pitch <= 0 or major_r <= minor_r:
            self._log("[螺纹] 参数异常，跳过齿形。")
            return None
        a = math.radians(angle)
        half_w = 5.0 * pitch / 16.0          # 牙型半底宽（=5P/16，与牙型角无关）
        n = int(depth / pitch) + 2

        body = self.part.Bodies.Item(1)
        sk = body.Sketches.Add(self.part.OriginElements.PlaneZX)
        f2d = sk.OpenEdition()
        for i in range(n):
            z0 = -i * pitch
            tri = [(z0, major_r), (z0 - half_w, minor_r), (z0 + half_w, minor_r)]
            lines = []
            for k in range(3):
                (h1, v1) = tri[k]
                (h2, v2) = tri[(k + 1) % 3]
                lines.append(f2d.CreateLine(h1, v1, h2, v2))
            self._try_coincidence(sk, lines)
        sk.CloseEdition()
        try:
            self.part.Update()
        except Exception:
            pass

        groove = self.part.ShapeFactory.AddNewGroove(sk)
        try:
            groove.RevoluteAxis = self._hybrid_axis()
        except Exception as e:
            self._log(f"[螺纹] 回转轴设置失败：{e}")
        for attr, val in (("FirstAngle", 360.0), ("SecondAngle", 0.0)):
            try:
                getattr(groove, attr).Value = val
            except Exception:
                pass
        self.part.Update()
        self._log(f"[螺纹] 真实齿形：Φ{minor_r * 2:g}(小径)→Φ{major_r * 2:g}(大径) "
                  f"螺距 {pitch:g} 牙型角 {angle:g}°，{n} 牙")
        return groove

    def _chamfer(self, step):
        """45° 锥形倒角：用 Groove（旋转切除）在顶/底面切出锥面。

        无需面引用。在 ZX 平面（草图坐标 H=Z, V=X）画一个四边形轮廓：
            顶面 (z=z0)：P1(x=R-c, z=z0)  P4(x=R+2, z=z0)
            下方 (z=z0-c)：P2(x=R,  z=z0-c) P3(x=R+2, z=z0-c)
        绕 Z 轴旋转切除 → 顶面边缘形成 45° 倒角锥面。
        回转轴必须用 HybridShapeFactory 建的直线（草图线不可用）。
        """
        size = float(step["size"])
        R = float(step["outer_r"])
        if R <= size * 1.2:
            return
        sf = self.part.ShapeFactory
        body = self.part.Bodies.Item(1)
        ok_any = False

        for face in step.get("faces", ("top",)):
            z0 = float(step["top_z"]) if face == "top" else float(step["bottom_z"])
            sign = -1.0 if face == "top" else 1.0     # 顶面往下切，底面往上切
            zc = z0 + sign * size
            try:
                sk = body.Sketches.Add(self.part.OriginElements.PlaneZX)
                f2d = sk.OpenEdition()
                # ZX 草图坐标：(h, v) = (z, x)
                P1 = (z0, R - size)
                P2 = (zc, R)
                P3 = (zc, R + 2.0)
                P4 = (z0, R + 2.0)
                lines = []
                quad = [P1, P2, P3, P4]
                for i in range(4):
                    (h1, v1) = quad[i]
                    (h2, v2) = quad[(i + 1) % 4]
                    lines.append(f2d.CreateLine(h1, v1, h2, v2))
                self._try_coincidence(sk, lines)
                sk.CloseEdition()
                self.part.Update()

                groove = sf.AddNewGroove(sk)
                try:
                    groove.RevoluteAxis = self._hybrid_axis()
                except Exception as e:
                    self._log(f"[倒角] 回转轴设置失败：{e}")
                for attr, val in (("FirstAngle", 360.0), ("SecondAngle", 0.0)):
                    try:
                        getattr(groove, attr).Value = val
                    except Exception:
                        pass
                self.part.Update()
                ok_any = True
                self._log(f"[倒角] {'顶' if face == 'top' else '底'}面 45° 锥面倒角 {size:g} mm")
            except Exception as e:
                self._log(f"[倒角] {'顶' if face == 'top' else '底'}面跳过：{e}")
        return ok_any

    def _fillet(self, step):
        """棱边倒圆（自动圆角，无需边引用）。"""
        try:
            r = float(step["radius"])
            self.part.ShapeFactory.AddNewAutoFillet(r, r)
            self.part.Update()
            self._log(f"[倒圆] 棱边圆角 R{r:g} mm")
        except Exception as e:
            self._log(f"[倒圆] 跳过：{e}")

    # ---------------- 构建 ----------------

    def build(self, model, name=None):
        """执行一个 model（来自 nut_geometry.build_model）。返回零件名。"""
        if not self.is_connected():
            raise RuntimeError("尚未连接 CATIA，请先 connect()。")
        name = name or model.get("name", "Nut")
        self.new_part(name)

        steps = model["steps"]
        # 固定顺序：实体 → 顶面特征 → 孔 → 螺纹齿形 → 倒角 → 倒圆
        order = {"pad": 0, "pad_up": 1, "pocket": 2, "hole": 3,
                 "thread": 4, "chamfer": 5, "fillet": 6}

        for idx, step in enumerate(sorted(steps, key=lambda st: order.get(st["op"], 9))):
            op = step["op"]
            try:
                if op == "pad":
                    self._pad(step)
                elif op == "pad_up":
                    self._pad_up(step)
                elif op == "pocket":
                    self._pocket(step)
                elif op == "hole":
                    self._hole(step)
                elif op == "thread":
                    self._thread(step)
                elif op == "chamfer":
                    self._chamfer(step)
                elif op == "fillet":
                    self._fillet(step)
                else:
                    self._log(f"[警告] 未知步骤 op={op}")
            except Exception as e:
                # 装饰特征失败不致命；实体/孔失败才抛
                if op in ("chamfer", "fillet"):
                    self._log(f"[警告] {op} 未生成：{e}")
                else:
                    raise RuntimeError(
                        f"建模失败 @步骤#{idx} (op={op}, 零件={name}): {e}"
                    ) from e

        try:
            self.catia.ActiveWindow.ActiveViewer.Reframe()
        except Exception:
            pass
        try:
            self.catia.ActiveWindow.ActiveViewer.ZoomIn()
        except Exception:
            pass
        self._log(f"[完成] 已在 CATIA 中生成：{name}")
        return name

    def save_as(self, path):
        """另存为 CATPart（可选）。"""
        if self.doc is None:
            return
        try:
            self.doc.SaveAs(path)
            self._log(f"[保存] {path}")
        except Exception as e:
            self._log(f"[保存] 失败：{e}")


# ---------------- 命令行/单测友好入口 ----------------

def build_all_types(controller, draw_func=None):
    """遍历所有螺母类型，各画一个常规数模（供批量测试）。"""
    from nut_data import NUT_TYPES, default_params
    results = []
    for key, t in NUT_TYPES.items():
        try:
            params = default_params(key)
            model = draw_func(key, params) if draw_func else None
            if model is None:
                from nut_geometry import build_model
                model = build_model(key, params)
            controller.build(model)
            results.append((key, True, ""))
        except Exception as e:
            results.append((key, False, str(e)))
    return results


if __name__ == "__main__":
    # 离线直接运行仅做结构自检
    print("catia_controller 模块加载正常。请在 Windows + CATIA 环境下使用。")
    sys.exit(0)
