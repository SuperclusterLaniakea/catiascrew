# -*- coding: utf-8 -*-
"""启动入口：运行 PyQt6 螺母建模工具。"""
import sys
from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import Qt

from ui_app import NutDesigner


def main():
    # Qt6 默认开启高 DPI 自适应；以下属性在 PyQt6 中已移除，做存在性保护
    for attr in ("AA_EnableHighDpiScaling", "AA_UseHighDpiPixmaps"):
        if hasattr(Qt.ApplicationAttribute, attr):
            QApplication.setAttribute(getattr(Qt.ApplicationAttribute, attr), True)

    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    win = NutDesigner()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
