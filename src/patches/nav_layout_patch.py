"""导航栏布局补丁：左侧一整列只用一个滚动条，底部 5 项固定。

现状（qfluentwidgets + ok-script 2.0.6）：侧栏分三个区

- ``topLayout``：本项目 9 个自定义页（我们用 ``NavigationItemPosition.TOP``）——**不滚动**；
- ``scrollArea``：框架自己的「Capture / Triggers / Tasks / Templates / Schedule」——**带滚动条**；
- ``bottomLayout``：Debug / Run Code / Notification / Settings / About——固定。

于是左侧看着是「9 项不动 + 下面另起一段带滚动条」，滚动条出现在列表中间。

做法（**不搬运控件，只改它被注册到哪个区**，这样不会出现父窗口/几何错乱）：

1. 包一层 ``FluentWindow.addSubInterface``：本项目 ``src.gui.*`` 的页面一律以
   ``NavigationItemPosition.SCROLL`` 注册 → 它们和框架的项进同一个滚动区；
2. 窗口建好后，把滚动区里**本项目的项挪到最前面**（同一布局内 takeAt + insertWidget，
   控件父窗口不变），保持原有视觉顺序；
3. 底部 5 项本来就是 ``BOTTOM``，完全不动。

⚠️ 第一版补丁直接把 ``topLayout`` 的控件搬到 ``scrollLayout``，结果滚动区被撑到标题栏下面、
和标题文字重叠（用户截图确认）。所以这里改成「只改注册位置 + 同布局内排序」。
"""

from __future__ import annotations

import contextlib

_INSTALLED = False

#: 本项目的页面（``src.gui.*`` 里构造出来的 CustomTab 实例），用于事后排序
_OUR_PAGES: set[int] = set()


def is_our_page(widget) -> bool:
    module = getattr(type(widget), "__module__", "") or ""
    return module.startswith("src.gui.")


def navigation_position_for(widget, position):
    """本项目页面一律进滚动区（并记下来，供事后排序）；其它页面原样返回。"""
    try:
        from qfluentwidgets import NavigationItemPosition
    except Exception:
        return position
    if is_our_page(widget):
        _OUR_PAGES.add(id(widget))
        return NavigationItemPosition.SCROLL
    return position


def reorder_ours_first(panel) -> int:
    """把滚动区里本项目的项挪到最前面，返回移动了几项。

    只在 ``scrollLayout`` 内部调整顺序：控件父窗口不变，不会出现重叠/错位。
    """
    try:
        layout = panel.scrollLayout
    except Exception:
        return 0

    try:
        widgets = []
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget() if item is not None else None
            if widget is not None:
                widgets.append(widget)
    except Exception:
        return 0

    ours = [w for w in widgets if id(w) in _OUR_PAGES]
    others = [w for w in widgets if id(w) not in _OUR_PAGES]
    try:
        for index, widget in enumerate(ours + others):
            layout.insertWidget(index, widget)
    except Exception:
        return 0
    return len(ours)


def install_nav_layout_patch() -> bool:
    """包一层 ``FluentWindow.addSubInterface`` + ``MainWindow.__init__``（幂等）。"""
    global _INSTALLED
    if _INSTALLED:
        return True
    try:
        from ok.ui.qt.MainWindow import MainWindow
        from qfluentwidgets import FluentWindow, NavigationItemPosition
    except Exception:
        return False

    original_add = FluentWindow.addSubInterface
    if not getattr(original_add, "_ok_ap_nav_layout", False):

        def add_sub_interface(self, interface, icon=None, text="", position=NavigationItemPosition.TOP,
                              parent=None, isTransparent=False):
            position = navigation_position_for(interface, position)
            return original_add(self, interface, icon, text, position, parent, isTransparent)

        add_sub_interface._ok_ap_nav_layout = True
        FluentWindow.addSubInterface = add_sub_interface

    original_init = MainWindow.__init__
    if not getattr(original_init, "_ok_ap_nav_layout_init", False):

        def __init__(self, *args, **kwargs):
            original_init(self, *args, **kwargs)
            with contextlib.suppress(Exception):
                reorder_ours_first(self.navigationInterface.panel)

        __init__._ok_ap_nav_layout_init = True
        MainWindow.__init__ = __init__

    _INSTALLED = True
    return True
