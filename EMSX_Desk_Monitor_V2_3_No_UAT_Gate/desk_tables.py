"""Account·Ticker를 고정하고 상세 열만 가로 이동하는 거래 표를 제공한다."""

from __future__ import annotations

from collections.abc import Sequence
import tkinter as tk
from tkinter import font as tkfont, ttk
from typing import Any

from kb_theme import SURFACE, TEXT, configure_rows

ColumnSpec = tuple[str, str, int]
IDENTITY_COLUMNS = ("account", "ticker")

ROUTE_COLUMNS: tuple[ColumnSpec, ...] = (
    ("account", "Account", 180),
    ("ticker", "Ticker", 220),
    ("basket_name", "Basket Name", 120),
    ("side", "Side", 48),
    ("qty", "Shares", 82),
    ("instruction", "Instruction", 118),
    ("line_status", "Line Status", 90),
    ("adv_pct", "ADV (20D)", 80),
    ("broker", "Broker", 80),
    ("filled", "Fill", 85),
    ("remain", "Remain", 85),
    ("warning", "Alerts", 240),
    ("create_time", "Create Time", 165),
    ("route_id", "Route#", 66),
    ("iso_country", "ISO Country", 90),
    ("market_code", "Market", 70),
    ("market_status", "장 상태", 100),
    ("market_warning", "휴장 확인", 260),
    ("sec_type", "Sec Type", 120),
    ("adv_reason", "ADV Check", 190),
    ("avg_price", "Avg Price", 90),
    ("parent_visible", "상위 Order 확인", 98),
)

ROUTE_PRIMARY_COLUMNS = (
    "account",
    "ticker",
    "basket_name",
    "side",
    "qty",
    "instruction",
    "line_status",
    "adv_pct",
)

NUMERIC_COLUMNS = {
    "qty",
    "filled",
    "remain",
    "fill_pct",
    "routed",
    "remaining",
    "routes",
    "route_id",
    "adv_pct",
    "avg_price",
}


class PinnedTreeview(ttk.Frame):
    """두 표의 행·선택·세로 이동을 일치시키고 식별 열의 축소를 방지한다."""

    def __init__(
        self,
        parent: tk.Misc,
        columns: Sequence[ColumnSpec],
        primary_columns: Sequence[str] | None = None,
        additional_columns: Sequence[str] = (),
    ) -> None:
        super().__init__(parent)
        self.column_keys = tuple(column[0] for column in columns)
        if not set(IDENTITY_COLUMNS).issubset(self.column_keys):
            raise ValueError("거래 표에는 Account와 Ticker가 필요합니다")
        self.detail_keys = tuple(
            key for key in self.column_keys if key not in IDENTITY_COLUMNS
        )
        self._indices = {key: index for index, key in enumerate(self.column_keys)}
        self._primary_columns = tuple(primary_columns or self.column_keys)
        if not set(self._primary_columns).issubset(self.column_keys):
            raise ValueError("거래 표의 기본 열 구성이 올바르지 않습니다")
        self._compact = primary_columns is not None
        self._primary_detail_keys = tuple(
            key for key in self._primary_columns if key not in IDENTITY_COLUMNS
        )
        self._primary_width = sum(
            width for key, _, width in columns if key in self._primary_detail_keys
        )
        self._base_widths = {key: width for key, _, width in columns}
        self._additional_columns = tuple(additional_columns)
        if not set(self._additional_columns).issubset(self.detail_keys):
            raise ValueError("거래 표의 추가 열 구성이 올바르지 않습니다")
        self._details_enabled = not self._compact
        self._reserved_width = self._primary_width
        self._values: dict[str, tuple[Any, ...]] = {}
        self._width_cache: dict[str, int] = {}
        self._syncing = False
        self._tooltip: tk.Toplevel | None = None
        self._hover: tuple[str, str] | None = None
        self._hover_timer: str | None = None
        style = ttk.Style(self)
        self._font = tkfont.Font(
            root=self, font=style.lookup("Treeview", "font") or "TkDefaultFont"
        )
        self._minimum = {
            "account": max(180, self._font.measure("0000000000000000") + 24),
            "ticker": max(220, self._font.measure("XXXXXXXX US Equity") + 24),
        }
        self._desired = self._minimum.copy()
        self.fixed = ttk.Treeview(
            self, columns=IDENTITY_COLUMNS, show="headings", selectmode="browse"
        )
        self.details = ttk.Treeview(
            self, columns=self.detail_keys, show="headings", selectmode="browse"
        )
        for key, label, width in columns:
            tree = self._tree(key)
            tree.heading(key, text=label)
            tree.column(
                key,
                width=self._minimum.get(key, width),
                minwidth=self._minimum.get(
                    key,
                    width
                    if key in self._primary_detail_keys and self._compact
                    else min(width, 60),
                ),
                stretch=False,
                anchor="e" if key in NUMERIC_COLUMNS else "w",
            )
        for tree in (self.fixed, self.details):
            configure_rows(tree)
            tree.configure(
                yscrollcommand=lambda first, last, source=tree: self._sync_yview(
                    source, first, last
                )
            )
            tree.bind("<<TreeviewSelect>>", self._sync_selection)
        self.scroll_y = ttk.Scrollbar(self, orient="vertical", command=self.yview)
        self.scroll_x = ttk.Scrollbar(
            self, orient="horizontal", command=self.details.xview
        )
        self.details.configure(xscrollcommand=self._set_xview)
        self.fixed.grid(row=0, column=0, sticky="nsew")
        self.details.grid(row=0, column=1, sticky="nsew")
        self.scroll_y.grid(row=0, column=2, sticky="ns")
        self.scroll_x.grid(row=1, column=1, sticky="ew")
        self.rowconfigure(0, weight=1)
        self.columnconfigure(1, weight=1)
        self.bind("<Configure>", self._resize)
        self.bind("<Destroy>", self._destroy_tooltip)
        self.fixed.bind("<Button-1>", self._protect_identity)
        self.fixed.bind("<Double-1>", self._protect_identity)
        self.fixed.bind("<Motion>", self._hover_cell)
        self.fixed.bind("<Leave>", self._destroy_tooltip)
        self.set_detail_columns(not self._compact)

    def set_detail_columns(self, enabled: bool) -> None:
        """기본 거래 열과 전체 상세 열 사이를 전환하며 원본 행은 보존한다."""
        self._details_enabled = enabled
        compact = self._compact and not self._details_enabled
        for key in ("basket_name", "instruction"):
            if key in self.detail_keys:
                self.details.column(key, stretch=compact)
        self.details.xview_moveto(0)
        self._choose_columns(self.winfo_width())
        self._fit_identity(self.winfo_width())

    def _choose_columns(self, width: int) -> None:
        """기본 식별 열을 확보한 뒤 여유 폭에 체결·Broker 열을 더한다."""
        keys = self.detail_keys
        self._reserved_width = self._primary_width
        if self._compact and not self._details_enabled:
            chosen = list(self._primary_detail_keys)
            available = (
                width - sum(self._minimum.values()) - self.scroll_y.winfo_reqwidth() - 8
            )
            for key in self._additional_columns:
                cost = self._base_widths[key]
                if self._reserved_width + cost > available:
                    break
                chosen.append(key)
                self._reserved_width += cost
            keys = tuple(chosen)
        if tuple(self.details["displaycolumns"]) != keys:
            if self._compact and not self._details_enabled:
                # 이전 창 폭에서 늘어난 열 너비가 추가 열의 공간을 침범하지 않게 한다.
                for key in keys:
                    self.details.column(key, width=self._base_widths[key])
            self.details.configure(displaycolumns=keys)

    def _tree(self, key: str) -> ttk.Treeview:
        return self.fixed if key in IDENTITY_COLUMNS else self.details

    def _resize(self, event: tk.Event) -> None:
        self._choose_columns(event.width)
        self._fit_identity(event.width)

    def _fit_identity(self, width: int) -> None:
        # 스크롤바·테두리를 제외하고 상세 열에도 최소 표시 공간을 남긴다.
        reserve = (
            (self._reserved_width if self._compact else 300)
            + self.scroll_y.winfo_reqwidth()
            + 8
        )
        spare = max(0, width - reserve - sum(self._minimum.values()))
        extras = {
            key: self._desired[key] - self._minimum[key] for key in IDENTITY_COLUMNS
        }
        total = sum(extras.values())
        for key in IDENTITY_COLUMNS:
            extra = extras[key] if total <= spare else int(spare * extras[key] / total)
            width = self._minimum[key] + extra
            if self.fixed.column(key, "width") != width:
                self.fixed.column(key, width=width)

    def refresh_identity_widths(self) -> None:
        """현재 행의 글자 폭을 캐시해 식별 열을 넓히고 상세 영역을 확보한다."""
        desired = self._minimum.copy()
        for row in self._values.values():
            for key in IDENTITY_COLUMNS:
                value = str(row[self._indices[key]] or "")
                if value not in self._width_cache:
                    if len(self._width_cache) >= 4096:
                        self._width_cache.clear()
                    self._width_cache[value] = self._font.measure(value) + 24
                desired[key] = max(desired[key], self._width_cache[value])
        self._desired = desired
        self._fit_identity(self.winfo_width())

    def _protect_identity(self, event: tk.Event) -> str | None:
        self._destroy_tooltip()
        if self.fixed.identify_region(event.x, event.y) == "separator":
            return "break"
        return None

    def _sync_yview(self, source: ttk.Treeview, first: str, last: str) -> None:
        if self._syncing:
            return
        self._destroy_tooltip()
        self._syncing = True
        try:
            other = self.details if source is self.fixed else self.fixed
            # 실제 위치가 다른 때만 이동해 지연된 스크롤 콜백의 왕복을 막는다.
            if abs(other.yview()[0] - float(first)) > 0.000001:
                other.yview_moveto(float(first))
            self.scroll_y.set(first, last)
        finally:
            self._syncing = False

    def _set_xview(self, first: str, last: str) -> None:
        self.scroll_x.set(first, last)
        if float(first) <= 0.0 and float(last) >= 1.0:
            self.scroll_x.grid_remove()
        else:
            self.scroll_x.grid()

    def _sync_selection(self, event: tk.Event) -> None:
        source = event.widget
        other = self.details if source is self.fixed else self.fixed
        selected = source.selection()
        if other.selection() != selected:
            other.selection_set(selected)
        if source.focus() != other.focus():
            other.focus(source.focus())

    def yview(self, *args: Any) -> None:
        self._destroy_tooltip()
        for tree in (self.fixed, self.details):
            tree.yview(*args)

    def heading(self, key: str, option: str | None = None, **kwargs: Any) -> Any:
        return self._tree(key).heading(key, option, **kwargs)

    def get_children(self, item: str = "") -> tuple[str, ...]:
        return self.details.get_children(item)

    def selection(self) -> tuple[str, ...]:
        return self.details.selection()

    def _cells(self, values: Sequence[Any], keys: Sequence[str]) -> tuple[Any, ...]:
        return tuple(values[self._indices[key]] for key in keys)

    def insert(
        self,
        parent: str,
        index: str | int,
        iid: str,
        *,
        values: Sequence[Any],
        tags: Sequence[str] = (),
    ) -> str:
        self.details.insert(
            parent,
            index,
            iid=iid,
            values=self._cells(values, self.detail_keys),
            tags=tags,
        )
        self.fixed.insert(
            parent,
            index,
            iid=iid,
            values=self._cells(values, IDENTITY_COLUMNS),
            tags=tags,
        )
        self._values[iid] = tuple(values)
        return iid

    def item(self, iid: str, option: str | None = None, **kwargs: Any) -> Any:
        """논리 열 순서를 유지해 CSV와 화면 갱신이 두 영역을 함께 다루게 한다."""
        if kwargs:
            if self._hover and self._hover[0] == iid:
                self._destroy_tooltip()
            values = kwargs.pop("values", None)
            if values is None:
                self.details.item(iid, **kwargs)
                self.fixed.item(iid, **kwargs)
            else:
                self.details.item(
                    iid, values=self._cells(values, self.detail_keys), **kwargs
                )
                self.fixed.item(
                    iid, values=self._cells(values, IDENTITY_COLUMNS), **kwargs
                )
                self._values[iid] = tuple(values)
            return None
        if option == "values":
            return self._values[iid]
        result = self.details.item(iid, option)
        if option is None:
            result["values"] = self._values[iid]
        return result

    def move(self, iid: str, parent: str, index: str | int) -> None:
        for tree in (self.fixed, self.details):
            tree.move(iid, parent, index)

    def delete(self, *items: str) -> None:
        if self._hover and self._hover[0] in items:
            self._destroy_tooltip()
        for tree in (self.fixed, self.details):
            tree.delete(*items)
        for iid in items:
            self._values.pop(iid, None)

    def _hover_cell(self, event: tk.Event) -> None:
        iid = self.fixed.identify_row(event.y)
        column = self.fixed.identify_column(event.x)
        cell = (iid, column)
        if cell == self._hover:
            return
        self._destroy_tooltip()
        if iid and column in ("#1", "#2"):
            key = IDENTITY_COLUMNS[int(column[1:]) - 1]
            value = str(self._values[iid][self._indices[key]])
            if self._font.measure(value) + 24 > self.fixed.column(key, "width"):
                self._hover = cell
                self._hover_timer = self.after(
                    450, lambda: self._show_full_value(value)
                )

    def _show_full_value(self, value: str) -> None:
        self._hover_timer = None
        self._tooltip = tk.Toplevel(self)
        self._tooltip.overrideredirect(True)
        self._tooltip.geometry(
            f"+{self.winfo_pointerx() + 12}+{self.winfo_pointery() + 16}"
        )
        ttk.Label(
            self._tooltip,
            text=value,
            padding=8,
            background=SURFACE,
            foreground=TEXT,
            wraplength=600,
        ).pack()

    def _destroy_tooltip(self, event: tk.Event | None = None) -> None:
        if self._hover_timer is not None:
            self.after_cancel(self._hover_timer)
            self._hover_timer = None
        if self._tooltip is not None:
            tooltip = self._tooltip
            self._tooltip = None
            tooltip.destroy()
        self._hover = None
