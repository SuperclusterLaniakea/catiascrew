# -*- coding: utf-8 -*-
"""
螺母参数化建模 · PyQt6 主界面。

功能：
  - 左侧类型树：按大类分组（六角类 / 圆类 / 方类 / 特殊类）选择螺母类型
  - 右侧参数表单：根据类型动态生成，含单位、范围校验、国标尺寸预设自动填充
  - 实时 2D 顶视预览：外轮廓 + 内孔，随参数即时更新
  - 操作：连接 CATIA / 绘制到 CATIA / 批量测试全部类型 / 重置默认值
  - 日志面板 + 连接状态栏 + 帮助

仅在真正运行时需要 PyQt6 与 pywin32；结构检查无需 GUI 依赖。
"""
import math

from PyQt6.QtCore import Qt, QSize, QPointF
from PyQt6.QtGui import QPainter, QPen, QBrush, QColor, QPixmap, QFont
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QTreeWidget, QTreeWidgetItem,
    QFormLayout, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QComboBox,
    QDoubleSpinBox, QCheckBox, QTextEdit, QGroupBox, QProgressBar,
    QMessageBox, QFrame, QSizePolicy,
)

from nut_data import (
    NUT_TYPES, NUT_CATEGORIES, PARAM_META, default_params, apply_preset, derive_e,
    THREAD_SYSTEMS, list_thread_systems, thread_sizes, get_thread_spec, estimate_hex_dims,
)
from nut_geometry import build_model, list_all_types


class NutDesigner(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("CATIA 螺母参数化建模工具")
        self.resize(1080, 720)

        self.current_key = None
        self.param_widgets = {}      # key -> (widget, meta)
        self.controller = None
        self._filling = False        # 预设回填中，抑制"切到自定义"逻辑
        self._init_ui()
        self._select_first_type()

    # ---------------- UI 初始化 ----------------

    def _init_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QHBoxLayout(central)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(10)

        # 左：类型树
        left = QVBoxLayout()
        left.addWidget(QLabel("<b>螺母类型</b>"))
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setMinimumWidth(200)
        self.tree.setMaximumWidth(260)
        self._build_type_tree()
        self.tree.itemClicked.connect(self._on_tree_clicked)
        left.addWidget(self.tree)
        root.addLayout(left)

        # 中：参数 + 预览
        mid = QVBoxLayout()
        mid.setSpacing(8)

        self.type_label = QLabel("<b>未选择</b>")
        self.type_label.setWordWrap(True)
        mid.addWidget(self.type_label)

        # 标准预设
        preset_row = QHBoxLayout()
        preset_row.addWidget(QLabel("标准规格:"))
        self.preset_combo = QComboBox()
        self.preset_combo.currentTextChanged.connect(self._on_preset_changed)
        preset_row.addWidget(self.preset_combo)
        preset_row.addStretch(1)
        mid.addLayout(preset_row)
        hint = QLabel("<span style='color:#888;font-size:10px'>选中标准规格将自动带出螺距/对边/厚度；"
                      "也可直接在下方修改任意参数（如改螺距/对边）后点击“绘制到 CATIA”。</span>")
        hint.setWordWrap(True)
        mid.addWidget(hint)

        # 螺纹标准 / 规格（公制粗牙·细牙 / 英制 UNC·UNF·BSW）
        thread_box = QGroupBox("螺纹标准（公制 / 英制 · 粗牙 / 细牙）")
        thread_layout = QVBoxLayout(thread_box)
        ts_row = QHBoxLayout()
        ts_row.addWidget(QLabel("标准:"))
        self.thread_system_combo = QComboBox()
        self.thread_system_combo.currentTextChanged.connect(self._on_thread_system_changed)
        ts_row.addWidget(self.thread_system_combo)
        ts_row.addWidget(QLabel("规格:"))
        self.thread_size_combo = QComboBox()
        self.thread_size_combo.currentTextChanged.connect(self._on_thread_size_changed)
        ts_row.addWidget(self.thread_size_combo)
        ts_row.addStretch(1)
        thread_layout.addLayout(ts_row)
        self.thread_sys_key = "metric_coarse"
        for key, name, _unit in list_thread_systems():
            self.thread_system_combo.addItem(name, key)
        self.thread_system_combo.setCurrentIndex(0)
        self._populate_thread_sizes("metric_coarse")
        mid.addWidget(thread_box)

        # 参数表单
        self.form_box = QGroupBox("参数")
        self.form_layout = QFormLayout(self.form_box)
        mid.addWidget(self.form_box)

        # 预览
        prev_box = QGroupBox("2D 顶视预览（实时）")
        prev_layout = QVBoxLayout(prev_box)
        self.preview = QLabel()
        self.preview.setMinimumHeight(220)
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview.setFrameStyle(QFrame.Shape.Box)
        self.preview.setStyleSheet("background:#1e1e1e;")
        prev_layout.addWidget(self.preview)
        mid.addWidget(prev_box)

        root.addLayout(mid, stretch=2)

        # 右：操作 + 日志
        right = QVBoxLayout()
        right.setSpacing(8)

        op_box = QGroupBox("操作")
        op_layout = QVBoxLayout(op_box)
        self.btn_connect = QPushButton("连接 CATIA")
        self.btn_connect.clicked.connect(self._on_connect)
        self.btn_draw = QPushButton("绘制到 CATIA")
        self.btn_draw.setStyleSheet("font-weight:bold;")
        self.btn_draw.clicked.connect(self._on_draw)
        self.btn_batch = QPushButton("批量测试全部类型")
        self.btn_batch.clicked.connect(self._on_batch)
        self.btn_reset = QPushButton("重置为默认值")
        self.btn_reset.clicked.connect(self._on_reset)
        self.auto_launch = QCheckBox("未连接时自动启动 CATIA")
        for w in (self.btn_connect, self.btn_draw, self.btn_batch,
                  self.btn_reset, self.auto_launch):
            op_layout.addWidget(w)
        right.addWidget(op_box)

        right.addWidget(QLabel("进度"))
        self.progress = QProgressBar()
        self.progress.setValue(0)
        right.addWidget(self.progress)

        right.addWidget(QLabel("日志"))
        self.log = QTextEdit()
        self.log.setReadOnly(True)
        self.log.setMinimumHeight(180)
        right.addWidget(self.log, stretch=1)

        root.addLayout(right, stretch=1)

        # 状态栏
        self.statusBar().showMessage("就绪（未连接 CATIA）")

    def _build_type_tree(self):
        self.tree.clear()
        cat_items = {}
        for cat_key, cat_name in NUT_CATEGORIES:
            item = QTreeWidgetItem([cat_name])
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsSelectable)
            self.tree.addTopLevelItem(item)
            cat_items[cat_key] = item
        for cat_key, cat_name, keys in list_all_types():
            parent = cat_items.get(cat_key)
            if parent is None:
                continue
            for k in keys:
                t = NUT_TYPES[k]
                child = QTreeWidgetItem([t["name"]])
                child.setData(0, Qt.ItemDataRole.UserRole, k)
                parent.addChild(child)
        self.tree.expandAll()

    def _select_first_type(self):
        for i in range(self.tree.topLevelItemCount()):
            top = self.tree.topLevelItem(i)
            if top.childCount():
                self.tree.setCurrentItem(top.child(0))
                self._on_tree_clicked(top.child(0), 0)
                return

    # ---------------- 交互 ----------------

    def _on_tree_clicked(self, item, col):
        key = item.data(0, Qt.ItemDataRole.UserRole)
        if not key:
            return
        self.current_key = key
        t = NUT_TYPES[key]
        self.type_label.setText(
            f"<b>{t['name']}</b> &nbsp;<span style='color:#888'>{t['en']}</span><br>"
            f"<span style='color:#aaa'>标准：{t['standard']}</span><br>"
            f"<span style='color:#bbb'>{t['desc']}</span>"
        )
        self._build_param_form(key)
        self._refresh_preset_combo(key)
        self._update_preview()

    def _build_param_form(self, key):
        # 清空旧表单
        while self.form_layout.rowCount():
            self.form_layout.removeRow(0)
        self.param_widgets.clear()

        t = NUT_TYPES[key]
        params = default_params(key)
        # 布局顺序：先展示 visible_params，再补 e（派生，置灰）
        order = list(t["params"])
        if "s" in order and "e" in PARAM_META and "e" not in order:
            order.append("e")
        # 全局附加参数：螺纹牙型角 / 倒圆（所有类型通用，始终显示）
        for gk in ("thread_angle", "fillet", "fillet_r"):
            if gk in PARAM_META and gk not in order:
                order.append(gk)

        for pkey in order:
            meta = PARAM_META.get(pkey, {})
            label = f"{meta.get('label', pkey)} ({meta.get('unit','')})".rstrip()
            if pkey == "e":
                label = f"{meta.get('label')} (派生)"
            if meta.get("type") == "bool":
                w = QCheckBox()
                w.setChecked(bool(params.get(pkey, meta.get("default", False))))
                self.form_layout.addRow(label, w)
            else:
                w = QDoubleSpinBox()
                w.setRange(meta.get("min", 0.0), 10000.0)
                w.setDecimals(3)
                w.setValue(float(params.get(pkey, meta.get("default", 0.0))))
                w.setSuffix(f" {meta.get('unit','')}")
                if pkey == "e":
                    w.setEnabled(False)  # 派生值，不可手改
                    w.setToolTip(meta.get("note", ""))
                else:
                    w.valueChanged.connect(lambda *_: self._on_param_changed())
                self.form_layout.addRow(label, w)
            self.param_widgets[pkey] = (w, meta)

    def _refresh_preset_combo(self, key):
        self.preset_combo.blockSignals(True)
        self.preset_combo.clear()
        self.preset_combo.addItem("自定义")
        t = NUT_TYPES[key]
        for size in t.get("presets", {}).keys():
            self.preset_combo.addItem(size)
        self.preset_combo.setCurrentText("自定义")
        self.preset_combo.blockSignals(False)

    def _on_preset_changed(self, text):
        if not self.current_key or text == "自定义":
            return
        params = apply_preset(self.current_key, text)
        self._fill_form(params)

    def _fill_form(self, params):
        self._filling = True
        try:
            for pkey, (w, meta) in self.param_widgets.items():
                if pkey not in params:
                    continue
                if meta.get("type") == "bool":
                    w.setChecked(bool(params[pkey]))
                else:
                    w.setValue(float(params[pkey]))
        finally:
            self._filling = False
        self._refresh_derived()

    def _refresh_derived(self):
        """刷新派生量：e（按形状）、bore（=D）、预览。不改变规格下拉框。"""
        shape = NUT_TYPES[self.current_key].get("shape", "hex") if self.current_key else "hex"
        if "s" in self.param_widgets and "e" in self.param_widgets:
            try:
                s = self.param_widgets["s"][0].value()
                self.param_widgets["e"][0].setValue(derive_e(s, shape))
            except Exception:
                pass
        if "D" in self.param_widgets and "bore" in self.param_widgets:
            try:
                self.param_widgets["bore"][0].blockSignals(True)
                self.param_widgets["bore"][0].setValue(self.param_widgets["D"][0].value())
                self.param_widgets["bore"][0].blockSignals(False)
            except Exception:
                pass
        self._update_preview()

    # ---------------- 螺纹标准 / 规格 ----------------

    def _populate_thread_sizes(self, system_key):
        self.thread_size_combo.blockSignals(True)
        self.thread_size_combo.clear()
        for sz in thread_sizes(system_key):
            self.thread_size_combo.addItem(sz)
        self.thread_size_combo.blockSignals(False)

    def _set_field(self, pkey, value):
        """设置某参数控件（若存在）的数值/勾选，屏蔽信号避免误触'自定义'。"""
        item = self.param_widgets.get(pkey)
        if not item:
            return False
        w, meta = item
        try:
            w.blockSignals(True)
            if meta.get("type") == "bool":
                w.setChecked(bool(value))
            else:
                w.setValue(float(value))
            w.blockSignals(False)
            return True
        except Exception:
            try:
                w.blockSignals(False)
            except Exception:
                pass
            return False

    def _on_thread_system_changed(self, text):
        key = self.thread_system_combo.currentData() or "metric_coarse"
        self.thread_sys_key = key
        self._populate_thread_sizes(key)
        if self.thread_size_combo.count():
            self._on_thread_size_changed(self.thread_size_combo.itemText(0))

    def _on_thread_size_changed(self, size_label):
        if not self.current_key or not size_label:
            return
        spec = get_thread_spec(self.thread_sys_key, size_label)
        if not spec:
            return
        d, p, angle = spec
        self._set_field("thread_angle", angle)
        self._set_field("D", d)
        self._set_field("P", p)
        metric = self.thread_sys_key.startswith("metric")
        if metric and size_label in NUT_TYPES[self.current_key].get("presets", {}):
            params = apply_preset(self.current_key, size_label)
            for pk in ("s", "m"):
                if pk in params:
                    self._set_field(pk, params[pk])
            self._set_field("D", d)
            self._set_field("P", p)
        else:
            s_est, m_est = estimate_hex_dims(d)
            self._set_field("s", s_est)
            self._set_field("m", m_est)
        self.preset_combo.blockSignals(True)
        self.preset_combo.setCurrentText("自定义")
        self.preset_combo.blockSignals(False)
        self._refresh_derived()

    def _on_param_changed(self):
        self._refresh_derived()
        # 用户手动修改任意参数 -> 标记当前为"自定义"（预设回填时 _filling 为 True，跳过）
        if not self._filling and self.current_key:
            self.preset_combo.blockSignals(True)
            self.preset_combo.setCurrentText("自定义")
            self.preset_combo.blockSignals(False)

    def _get_params(self):
        out = {}
        for pkey, (w, meta) in self.param_widgets.items():
            if meta.get("type") == "bool":
                out[pkey] = w.isChecked()
            else:
                out[pkey] = w.value()
        return out

    # ---------------- 预览 ----------------

    def _update_preview(self):
        if not self.current_key:
            return
        try:
            params = self._get_params()
            pv = build_model(self.current_key, params)["preview"]
            pix = self._render_preview(pv)
            self.preview.setPixmap(pix)
        except Exception as e:
            self._log(f"[预览] 更新失败：{e}")

    def _render_preview(self, pv, size=220):
        pix = QPixmap(size, size)
        pix.fill(QColor("#1e1e1e"))
        p = QPainter(pix)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        outer = pv["outer"]
        bore_r = pv.get("bore_r", 0.0)

        # 计算包围盒
        if outer["kind"] == "polygon":
            xs = [pt[0] for pt in outer["points"]]
            ys = [pt[1] for pt in outer["points"]]
        else:
            xs = [outer["cx"] - outer["r"], outer["cx"] + outer["r"]]
            ys = [outer["cy"] - outer["r"], outer["cy"] + outer["r"]]
        max_r = max(max(abs(v) for v in xs), max(abs(v) for v in ys), bore_r, 1e-6)
        margin = 18
        scale = (size / 2 - margin) / max_r
        cx, cy = size / 2, size / 2

        def tx(x):
            return cx + x * scale

        def ty(y):
            return cy - y * scale

        # 外轮廓
        p.setPen(QPen(QColor("#4fc3f7"), 2))
        p.setBrush(QBrush(QColor("#263238")))
        if outer["kind"] == "polygon":
            qpts = [QPointF(tx(x), ty(y)) for (x, y) in outer["points"]]
            p.drawPolygon(qpts)
        else:
            r = outer["r"] * scale
            p.drawEllipse(QPointF(tx(outer["cx"]), ty(outer["cy"])), r, r)

        # 内孔
        p.setPen(QPen(QColor("#ff8a65"), 1.5))
        p.setBrush(QBrush(QColor("#1e1e1e")))
        br = bore_r * scale
        if br > 0.5:
            p.drawEllipse(QPointF(cx, cy), br, br)

        # 中心线
        p.setPen(QPen(QColor("#555"), 1, Qt.PenStyle.DashLine))
        p.drawLine(QPointF(margin, cy), QPointF(size - margin, cy))
        p.drawLine(QPointF(cx, margin), QPointF(cx, size - margin))

        p.end()
        return pix

    # ---------------- CATIA 操作 ----------------

    def _ensure_controller(self):
        if self.controller is None:
            from catia_controller import CatiaController
            self.controller = CatiaController(log=self._log)
        return self.controller

    def _on_connect(self):
        try:
            ctrl = self._ensure_controller()
            ctrl.connect(launch_if_needed=self.auto_launch.isChecked())
            self.statusBar().showMessage("已连接 CATIA ✓")
            self._log("[连接] 成功。")
        except Exception as e:
            self.statusBar().showMessage("未连接 CATIA")
            self._log(f"[连接] 失败：{e}")

    def _on_draw(self):
        if not self.current_key:
            return
        try:
            ctrl = self._ensure_controller()
            if not ctrl.is_connected():
                ctrl.connect(launch_if_needed=self.auto_launch.isChecked())
            params = self._get_params()
            model = build_model(self.current_key, params)
            ctrl.build(model)
            self.statusBar().showMessage(f"已绘制：{model['name']} ✓")
            self._log(f"[绘制] 完成：{model['name']}")
        except Exception as e:
            self._log(f"[绘制] 失败：{e}")
            QMessageBox.warning(self, "绘制失败", str(e))

    def _on_batch(self):
        try:
            ctrl = self._ensure_controller()
            if not ctrl.is_connected():
                ctrl.connect(launch_if_needed=self.auto_launch.isChecked())
        except Exception as e:
            self._log(f"[批量] 无法连接 CATIA：{e}")
            QMessageBox.warning(self, "未连接", "请先连接 CATIA 后再批量测试。")
            return

        keys = list(NUT_TYPES.keys())
        self.progress.setValue(0)
        ok, fail = 0, 0
        for i, key in enumerate(keys):
            try:
                params = default_params(key)
                model = build_model(key, params)
                ctrl.build(model)
                ok += 1
                self._log(f"[批量] ({i+1}/{len(keys)}) 成功：{model['name']}")
            except Exception as e:
                fail += 1
                self._log(f"[批量] ({i+1}/{len(keys)}) 失败：{key} -> {e}")
            self.progress.setValue(int((i + 1) / len(keys) * 100))
        self._log(f"[批量] 结束：成功 {ok}，失败 {fail}。")
        self.statusBar().showMessage(f"批量测试完成：成功 {ok} / 失败 {fail}")

    def _on_reset(self):
        if not self.current_key:
            return
        self._fill_form(default_params(self.current_key))
        self.preset_combo.blockSignals(True)
        self.preset_combo.setCurrentText("自定义")
        self.preset_combo.blockSignals(False)

    # ---------------- 日志 ----------------

    def _log(self, msg):
        self.log.append(msg)
