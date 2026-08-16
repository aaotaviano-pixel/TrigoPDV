"""Visual tokens and reusable widgets for the Trigo de Minas desktop UI.

The interface intentionally relies only on Tkinter/ttk.  This keeps the cashier
application small, dependable on Windows and usable on workstations without a
browser runtime or a network connection.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Callable


class Colors:
    """Central colour palette used throughout the application."""

    INK = "#1C2B24"
    INK_MUTED = "#5C665F"
    FOREST = "#244334"
    FOREST_HOVER = "#315B47"
    GOLD = "#D89B28"
    GOLD_HOVER = "#BC821B"
    CREAM = "#F7F4ED"
    SURFACE = "#FFFFFF"
    SURFACE_ALT = "#EEE9DE"
    LINE = "#DED8CC"
    SUCCESS = "#287A4D"
    SUCCESS_SOFT = "#E5F3E9"
    DANGER = "#B83A3A"
    DANGER_HOVER = "#962E2E"
    DANGER_SOFT = "#FBE9E8"
    INFO = "#2B6FAE"
    INFO_SOFT = "#E7F1FB"
    WARNING = "#A86612"
    WARNING_SOFT = "#FFF4D7"


FONT_FAMILY = "Segoe UI"
MONO_FONT = "Cascadia Mono"


def font(size: int, weight: str = "normal", family: str = FONT_FAMILY) -> tuple[str, int, str]:
    return (family, size, weight)


def configure_style(root: tk.Misc) -> None:
    """Configure ttk once for a compact, high-contrast POS experience."""

    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass

    root.option_add("*tearOff", False)
    root.option_add("*Font", font(10))
    style.configure("App.TFrame", background=Colors.CREAM)
    style.configure("Surface.TFrame", background=Colors.SURFACE)
    style.configure("Card.TFrame", background=Colors.SURFACE)
    style.configure("TLabel", background=Colors.CREAM, foreground=Colors.INK, font=font(10))
    style.configure("Surface.TLabel", background=Colors.SURFACE, foreground=Colors.INK)
    style.configure("Muted.TLabel", background=Colors.SURFACE, foreground=Colors.INK_MUTED)
    style.configure("Title.TLabel", background=Colors.SURFACE, foreground=Colors.INK, font=font(22, "bold"))
    style.configure("Heading.TLabel", background=Colors.SURFACE, foreground=Colors.INK, font=font(14, "bold"))
    style.configure("Small.TLabel", background=Colors.SURFACE, foreground=Colors.INK_MUTED, font=font(9))
    style.configure("Toolbar.TLabel", background=Colors.INK, foreground="#FFFFFF")
    style.configure("TEntry", fieldbackground=Colors.SURFACE, foreground=Colors.INK, bordercolor=Colors.LINE,
                    lightcolor=Colors.GOLD, darkcolor=Colors.LINE, padding=(10, 8), font=font(11))
    style.map("TEntry", bordercolor=[("focus", Colors.GOLD)], lightcolor=[("focus", Colors.GOLD)])
    style.configure("Treeview", background=Colors.SURFACE, foreground=Colors.INK, fieldbackground=Colors.SURFACE,
                    bordercolor=Colors.LINE, rowheight=36, font=font(10))
    style.map("Treeview", background=[("selected", Colors.INFO_SOFT)], foreground=[("selected", Colors.INK)])
    style.configure("Treeview.Heading", background=Colors.SURFACE_ALT, foreground=Colors.INK_MUTED,
                    relief="flat", font=font(9, "bold"), padding=(10, 10))
    style.map("Treeview.Heading", background=[("active", Colors.SURFACE_ALT)])
    style.configure("TNotebook", background=Colors.SURFACE, borderwidth=0)
    style.configure("TNotebook.Tab", background=Colors.SURFACE_ALT, foreground=Colors.INK_MUTED,
                    padding=(18, 10), font=font(10, "bold"))
    style.map("TNotebook.Tab", background=[("selected", Colors.SURFACE)], foreground=[("selected", Colors.FOREST)])
    style.configure("TCombobox", fieldbackground=Colors.SURFACE, background=Colors.SURFACE,
                    foreground=Colors.INK, padding=(8, 7))


class Button(tk.Button):
    """A small Tk button wrapper with predictable modern styling."""

    _VARIANTS = {
        "primary": (Colors.FOREST, "#FFFFFF", Colors.FOREST_HOVER),
        "accent": (Colors.GOLD, "#FFFFFF", Colors.GOLD_HOVER),
        "danger": (Colors.DANGER, "#FFFFFF", Colors.DANGER_HOVER),
        "ghost": (Colors.SURFACE, Colors.FOREST, Colors.SURFACE_ALT),
        "soft": (Colors.SUCCESS_SOFT, Colors.SUCCESS, "#D2EAD9"),
    }

    def __init__(
        self,
        master: tk.Misc,
        text: str,
        command: Callable[[], object] | None = None,
        *,
        variant: str = "primary",
        width: int | None = None,
        **kwargs: object,
    ) -> None:
        base, foreground, active = self._VARIANTS.get(variant, self._VARIANTS["primary"])
        options: dict[str, object] = {
            "text": text,
            "command": command,
            "bg": base,
            "fg": foreground,
            "activebackground": active,
            "activeforeground": foreground,
            "disabledforeground": "#A7ADA9",
            "font": font(10, "bold"),
            "bd": 0,
            "relief": "flat",
            "cursor": "hand2",
            "padx": 14,
            "pady": 9,
            "highlightthickness": 0,
            "takefocus": True,
        }
        if width is not None:
            options["width"] = width
        options.update(kwargs)
        super().__init__(master, **options)


class Card(tk.Frame):
    """White panel with a subtle border, suitable for POS sections."""

    def __init__(self, master: tk.Misc, *, padding: int = 18, **kwargs: object) -> None:
        options: dict[str, object] = {
            "bg": Colors.SURFACE,
            "highlightbackground": Colors.LINE,
            "highlightthickness": 1,
            "bd": 0,
            "padx": padding,
            "pady": padding,
        }
        options.update(kwargs)
        super().__init__(master, **options)


class SectionLabel(tk.Label):
    def __init__(self, master: tk.Misc, text: str, **kwargs: object) -> None:
        options: dict[str, object] = {
            "text": text,
            "bg": Colors.SURFACE,
            "fg": Colors.INK_MUTED,
            "font": font(9, "bold"),
            "anchor": "w",
        }
        options.update(kwargs)
        super().__init__(master, **options)


def money(value: float | int | None) -> str:
    """Render an amount in Brazilian notation without relying on OS locale."""

    amount = float(value or 0)
    rendered = f"{amount:,.2f}"
    return "R$ " + rendered.replace(",", "X").replace(".", ",").replace("X", ".")


def parse_money(value: str | float | int | None) -> float:
    """Accept common Brazilian POS input forms and return a non-rounded float."""

    if isinstance(value, (float, int)):
        return float(value)
    raw = str(value or "").strip().replace("R$", "").replace(" ", "")
    if not raw:
        return 0.0
    if "," in raw:
        raw = raw.replace(".", "").replace(",", ".")
    return float(raw)


def center_window(window: tk.Toplevel | tk.Tk, parent: tk.Misc | None = None) -> None:
    """Center a toplevel after Tk has calculated its requested dimensions."""

    window.update_idletasks()
    width = window.winfo_width()
    height = window.winfo_height()
    if parent is not None:
        x = parent.winfo_rootx() + max((parent.winfo_width() - width) // 2, 0)
        y = parent.winfo_rooty() + max((parent.winfo_height() - height) // 2, 0)
    else:
        x = max((window.winfo_screenwidth() - width) // 2, 0)
        y = max((window.winfo_screenheight() - height) // 2, 0)
    window.geometry(f"+{x}+{y}")
