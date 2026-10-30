"""KB 색상과 거래 화면의 공통 구성 요소 스타일을 정의한다."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk, font as tkfont
import sys

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
    families = set(tkfont.families(root))
    candidates = (
        ("맑은 고딕", "Malgun Gothic")
        if sys.platform == "win32"
        else ("Noto Sans CJK KR", "Noto Sans KR", "NanumGothic")
    )
    family = next(
        (name for name in candidates if name in families),
        tkfont.nametofont("TkDefaultFont").actual("family"),
    )
    body_font = tkfont.Font(root=root, family=family, size=9)
    root.configure(background=BACKGROUND)
    style.configure(
        ".",
        background=BACKGROUND,
        foreground=TEXT,
        font=(family, 9),
        bordercolor=BORDER,
        lightcolor=BORDER,
        darkcolor=BORDER,
    )
    style.configure("TFrame", background=BACKGROUND)
    style.configure("TLabel", background=BACKGROUND)
    style.configure("Header.TLabel", font=(family, 11, "bold"))
    style.configure("Connection.TLabel", foreground=MUTED, padding=(0, 0))
    style.configure("Error.Connection.TLabel", foreground=DANGER)
    style.configure("Metric.TLabel", foreground=TEXT, font=(family, 9), padding=0)
    style.configure(
        "Badge.TLabel",
        background=BACKGROUND,
        foreground=MUTED,
        padding=0,
        font=(family, 9),
    )
    style.configure("Danger.Badge.TLabel", foreground=DANGER)
    style.configure("Risk.Badge.TLabel", foreground=GOLD)
    style.configure("Muted.TLabel", foreground=MUTED)
    style.layout("Workspace.Tab.TRadiobutton", style.layout("TButton"))
    style.configure(
        "Workspace.Tab.TRadiobutton",
        background=BACKGROUND,
        foreground=MUTED,
        padding=(8, 4),
        bordercolor=BACKGROUND,
    )
    style.map(
        "Workspace.Tab.TRadiobutton",
        background=[("selected", SURFACE_ALT), ("active", SURFACE)],
        foreground=[("selected", TEXT), ("active", TEXT)],
        bordercolor=[("selected", BORDER)],
    )
    style.configure(
        "TButton",
        background=SURFACE_ALT,
        foreground=TEXT,
        padding=(9, 4),
        borderwidth=1,
        focusthickness=1,
        focuscolor=BORDER,
    )
    style.map(
        "TButton",
        background=[
            ("disabled", SURFACE),
            ("pressed", "#3D3D3D"),
            ("active", "#303030"),
        ],
        foreground=[("disabled", "#757575")],
        bordercolor=[("focus", MUTED)],
    )
    style.configure(
        "Primary.TButton",
        background=GOLD,
        foreground=BACKGROUND,
        font=(family, 9, "bold"),
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
        "Active.TButton", foreground=GOLD, background="#2C281A", bordercolor=GOLD
    )
    style.map(
        "Active.TButton",
        foreground=[("!disabled", GOLD)],
        background=[("active", "#393221")],
    )
    style.configure(
        "TEntry", fieldbackground=SURFACE, foreground=TEXT, insertcolor=TEXT, padding=4
    )
    style.map("TEntry", bordercolor=[("focus", MUTED)])
    style.configure(
        "TCombobox",
        fieldbackground=SURFACE,
        foreground=TEXT,
        arrowcolor=MUTED,
        padding=3,
    )
    style.map(
        "TCombobox",
        fieldbackground=[("readonly", SURFACE)],
        foreground=[("disabled", MUTED), ("readonly", TEXT)],
        bordercolor=[("focus", MUTED)],
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
    style.configure(
        "Treeview",
        rowheight=max(26, body_font.metrics("linespace") + 8),
        background=SURFACE,
        fieldbackground=SURFACE,
        foreground=TEXT,
        borderwidth=1,
        relief="solid",
        font=(family, 9),
    )
    style.configure(
        "Treeview.Heading",
        background=SURFACE_ALT,
        foreground=TEXT,
        padding=(6, 5),
        font=(family, 9, "bold"),
        relief="flat",
    )
    style.map("Treeview.Heading", background=[("active", "#303030")])
    style.map(
        "Treeview",
        background=[("selected", "#353B42")],
        foreground=[("selected", "#FFFFFF")],
    )
    style.configure("TSeparator", background=BORDER)
    style.configure(
        "TScrollbar",
        background=SURFACE_ALT,
        troughcolor=BACKGROUND,
        arrowcolor=MUTED,
        borderwidth=0,
        lightcolor=BORDER,
        darkcolor=BORDER,
        bordercolor=BORDER,
    )
    style.map(
        "TScrollbar",
        background=[
            ("disabled", SURFACE),
            ("pressed", "#494949"),
            ("active", "#353535"),
        ],
        arrowcolor=[("disabled", "#555555")],
    )
    root.option_add("*TCombobox*Listbox.background", SURFACE)
    root.option_add("*TCombobox*Listbox.foreground", TEXT)
    root.option_add("*TCombobox*Listbox.selectBackground", "#353B42")
    root.option_add("*TCombobox*Listbox.selectForeground", TEXT)


def configure_rows(tree: ttk.Treeview) -> None:
    """교차 행과 경고·실행 상태의 표시 우선순위를 정의한다."""
    tree.tag_configure("even", background=SURFACE)
    tree.tag_configure("odd", background=SURFACE_ALT)
    tree.tag_configure("working", foreground=TEXT)
    tree.tag_configure("terminal", foreground=MUTED)
    tree.tag_configure("alert", background="#3B301A", foreground="#FFE58A")
    family = tkfont.Font(
        root=tree, font=ttk.Style(tree).lookup("Treeview", "font")
    ).actual("family")
    tree.tag_configure(
        "adv",
        background="#4A3B00",
        foreground="#FFE46B",
        font=(family, 9, "bold"),
    )
    tree.tag_configure("danger", background="#3B2020", foreground=DANGER)
    tree.tag_configure(
        "dividend", background="#4A2D18", foreground="#FFE1A3", font=(family, 9, "bold")
    )


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
