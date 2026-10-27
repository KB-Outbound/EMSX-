"""KB 색상과 거래 화면의 공통 구성 요소 스타일을 정의한다."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

BACKGROUND = "#121212"
SURFACE = "#1E1E1E"
SURFACE_ALT = "#232323"
BORDER = "#353535"
TEXT = "#F2F2F2"
MUTED = "#ADADAD"
GOLD = "#FFCC00"
DANGER = "#FF9292"


def apply_theme(root: tk.Tk) -> None:
    """운영체제 기본 테마 대신 일관된 차콜·금색 스타일을 적용한다."""
    style = ttk.Style(root)
    style.theme_use("clam")
    root.configure(background=BACKGROUND)
    style.configure(
        ".",
        background=BACKGROUND,
        foreground=TEXT,
        font=("맑은 고딕", 10),
        bordercolor=BORDER,
        lightcolor=BORDER,
        darkcolor=BORDER,
    )
    style.configure("TFrame", background=BACKGROUND)
    style.configure("TLabel", background=BACKGROUND)
    style.configure("Header.TLabel", font=("맑은 고딕", 16, "bold"))
    style.configure(
        "Metric.TLabel", foreground=GOLD, font=("맑은 고딕", 12, "bold"), padding=(0, 8)
    )
    style.configure(
        "Badge.TLabel",
        background=SURFACE,
        foreground=GOLD,
        padding=(10, 5),
        font=("맑은 고딕", 10, "bold"),
    )
    style.configure("Danger.Badge.TLabel", foreground=DANGER)
    style.configure("Muted.TLabel", foreground=MUTED)
    style.configure(
        "TButton",
        background=SURFACE_ALT,
        foreground=TEXT,
        padding=(12, 8),
        borderwidth=1,
        focusthickness=1,
        focuscolor=GOLD,
    )
    style.map(
        "TButton",
        background=[
            ("disabled", SURFACE),
            ("pressed", "#3D3D3D"),
            ("active", "#303030"),
        ],
        foreground=[("disabled", "#757575")],
        bordercolor=[("focus", GOLD)],
    )
    style.configure(
        "Primary.TButton",
        background=GOLD,
        foreground=BACKGROUND,
        font=("맑은 고딕", 10, "bold"),
        bordercolor=GOLD,
    )
    style.map(
        "Primary.TButton",
        background=[
            ("disabled", "#343128"),
            ("pressed", "#D9AD00"),
            ("active", "#FFDC4D"),
        ],
        foreground=[("disabled", "#A8A18A"), ("!disabled", BACKGROUND)],
        bordercolor=[("disabled", BORDER), ("!disabled", GOLD)],
    )
    style.configure(
        "TEntry", fieldbackground=SURFACE, foreground=TEXT, insertcolor=GOLD, padding=6
    )
    style.map("TEntry", bordercolor=[("focus", GOLD)])
    style.configure(
        "TCombobox",
        fieldbackground=SURFACE,
        foreground=TEXT,
        arrowcolor=GOLD,
        padding=5,
    )
    style.map(
        "TCombobox",
        fieldbackground=[("readonly", SURFACE)],
        foreground=[("disabled", MUTED), ("readonly", TEXT)],
        bordercolor=[("focus", GOLD)],
    )
    style.configure(
        "TCheckbutton", indicatorbackground=SURFACE, indicatorforeground=GOLD, padding=4
    )
    style.map(
        "TCheckbutton",
        background=[("active", BACKGROUND)],
        indicatorbackground=[("selected", GOLD)],
        indicatorforeground=[("selected", BACKGROUND)],
    )
    style.configure("TNotebook", background=BACKGROUND, borderwidth=0)
    style.configure(
        "TNotebook.Tab", background=SURFACE, foreground=MUTED, padding=(16, 9)
    )
    style.map(
        "TNotebook.Tab",
        background=[("selected", GOLD), ("active", SURFACE_ALT)],
        foreground=[("selected", BACKGROUND), ("active", TEXT)],
    )
    style.configure(
        "Treeview",
        rowheight=32,
        background=SURFACE,
        fieldbackground=SURFACE,
        foreground=TEXT,
        borderwidth=1,
        relief="solid",
        font=("맑은 고딕", 10),
    )
    style.configure(
        "Treeview.Heading",
        background=SURFACE_ALT,
        foreground=GOLD,
        padding=(10, 9),
        font=("맑은 고딕", 10, "bold"),
        relief="flat",
    )
    style.map("Treeview.Heading", background=[("active", "#303030")])
    style.map(
        "Treeview",
        background=[("selected", "#51451E")],
        foreground=[("selected", "#FFFFFF")],
    )
    style.configure("TSeparator", background=BORDER)
    style.configure(
        "TScrollbar",
        background=SURFACE_ALT,
        troughcolor=BACKGROUND,
        arrowcolor=MUTED,
        borderwidth=0,
    )
    root.option_add("*TCombobox*Listbox.background", SURFACE)
    root.option_add("*TCombobox*Listbox.foreground", TEXT)
    root.option_add("*TCombobox*Listbox.selectBackground", GOLD)
    root.option_add("*TCombobox*Listbox.selectForeground", BACKGROUND)


def configure_rows(tree: ttk.Treeview) -> None:
    """교차 행과 경고·실행 상태의 표시 우선순위를 정의한다."""
    tree.tag_configure("even", background=SURFACE)
    tree.tag_configure("odd", background=SURFACE_ALT)
    tree.tag_configure("working", foreground=GOLD)
    tree.tag_configure("terminal", foreground=MUTED)
    tree.tag_configure("alert", background="#3B301A", foreground="#FFE58A")
    tree.tag_configure(
        "adv",
        background="#4A3B00",
        foreground="#FFE46B",
        font=("맑은 고딕", 10, "bold"),
    )
    tree.tag_configure("danger", background="#3B2020", foreground=DANGER)


def row_tags(
    index: int, status: str = "", warning: bool = False, adv: bool = False
) -> tuple[str, ...]:
    """배경 태그를 하나만 부여해 교차 배경이 경고·ADV 강조를 덮지 않도록 한다."""
    if status == "Rejected":
        return ("danger",)
    if warning:
        return ("alert",)
    if adv:
        return ("adv",)
    tags = ["odd" if index % 2 else "even"]
    if status in {"Working", "Part-filled", "Assign", "Sent"}:
        tags.append("working")
    elif status in {"Cancel", "Filled", "Completed", "Expired", "Deleted"}:
        tags.append("terminal")
    return tuple(tags)
