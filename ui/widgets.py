"""Reusable CustomTkinter building blocks + scroll-safe tk controls."""

from __future__ import annotations

import tkinter as tk
from typing import Any

import customtkinter as ctk

from ui.theme import Theme, get_theme


class SectionFrame(ctk.CTkFrame):
    """Card-like section with a title row and content area."""

    def __init__(
        self,
        master: Any,
        title: str,
        *,
        subtitle: str | None = None,
        **kwargs: Any,
    ):
        theme = get_theme()
        kwargs.setdefault("fg_color", theme.surface)
        kwargs.setdefault("border_color", theme.border)
        kwargs.setdefault("border_width", 1)
        kwargs.setdefault("corner_radius", 12)
        super().__init__(master, **kwargs)
        self._title = ctk.CTkLabel(
            self,
            text=title,
            anchor="w",
            font=ctk.CTkFont(size=14, weight="bold"),
            text_color=theme.fg,
        )
        self._title.grid(column=0, row=0, sticky="ew", padx=14, pady=(12, 0))
        self._subtitle: ctk.CTkLabel | None = None
        body_row = 1
        if subtitle:
            self._subtitle = ctk.CTkLabel(
                self,
                text=subtitle,
                anchor="w",
                font=ctk.CTkFont(size=11),
                text_color=theme.muted,
                wraplength=420,
                justify="left",
            )
            self._subtitle.grid(column=0, row=1, sticky="ew", padx=14, pady=(2, 0))
            body_row = 2
        # Solid surface (not transparent): CTk children paint their own bg rects;
        # if body is page_bg/white while children use surface, each control looks
        # like a mismatched "slab" (filter screenshots light+dark).
        self.body = ctk.CTkFrame(
            self, fg_color=theme.surface, bg_color=theme.surface
        )
        self.body.grid(column=0, row=body_row, sticky="nsew", padx=14, pady=(8, 14))
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(body_row, weight=1)
        self.body.grid_columnconfigure(0, weight=1)

    def set_title(self, title: str) -> None:
        self._title.configure(text=title)

    def apply_theme(self, theme: Theme | None = None, *, page_bg: str | None = None) -> None:
        theme = theme or get_theme()
        # When hosted on a plain tk.Frame, CTk defaults bg_color to white — pin it.
        kwargs = {
            "fg_color": theme.surface,
            "border_color": theme.border,
            "bg_color": page_bg if page_bg is not None else theme.surface,
        }
        self.configure(**kwargs)
        self._title.configure(text_color=theme.fg, bg_color=theme.surface)
        if self._subtitle is not None:
            self._subtitle.configure(text_color=theme.muted, bg_color=theme.surface)
        # Keep body fill == child bg_color so checkbox/label rects disappear into the card.
        self.body.configure(fg_color=theme.surface, bg_color=theme.surface)


def settings_switch(
    master: Any,
    *,
    variable: Any,
    command: Any = None,
) -> ctk.CTkSwitch:
    theme = get_theme()
    return ctk.CTkSwitch(
        master,
        text="",
        width=42,
        variable=variable,
        command=command,
        progress_color=theme.accent,
        button_color="#ffffff",
        button_hover_color="#efeff1",
        fg_color=theme.border,
        border_color=theme.border,
    )


def warning_banner(master: Any, title: str, body: str) -> ctk.CTkFrame:
    theme = get_theme()
    banner = ctk.CTkFrame(
        master,
        fg_color=theme.warn_bg,
        border_color=theme.warn_border,
        border_width=1,
        corner_radius=8,
    )
    title_lbl = ctk.CTkLabel(
        banner,
        text=title,
        anchor="w",
        font=ctk.CTkFont(size=12, weight="bold"),
        text_color=theme.warn_title,
    )
    title_lbl.grid(column=0, row=0, sticky="ew", padx=10, pady=(8, 0))
    body_lbl = ctk.CTkLabel(
        banner,
        text=body,
        anchor="w",
        justify="left",
        wraplength=360,
        font=ctk.CTkFont(size=11),
        text_color=theme.warn_body,
    )
    body_lbl.grid(column=0, row=1, sticky="ew", padx=10, pady=(2, 8))
    banner.grid_columnconfigure(0, weight=1)
    banner._warn_title = title_lbl  # type: ignore[attr-defined]
    banner._warn_body = body_lbl  # type: ignore[attr-defined]
    return banner


def apply_warning_banner_theme(banner: ctk.CTkFrame, theme: Theme | None = None) -> None:
    theme = theme or get_theme()
    banner.configure(fg_color=theme.warn_bg, border_color=theme.warn_border)
    title_lbl = getattr(banner, "_warn_title", None)
    body_lbl = getattr(banner, "_warn_body", None)
    if title_lbl is not None:
        title_lbl.configure(text_color=theme.warn_title)
    if body_lbl is not None:
        body_lbl.configure(text_color=theme.warn_body)


def accent_button(master: Any, **kwargs: Any) -> ctk.CTkButton:
    theme = get_theme()
    kwargs.setdefault("fg_color", theme.accent)
    kwargs.setdefault("hover_color", theme.accent_hover)
    kwargs.setdefault("text_color", "#ffffff")
    kwargs.setdefault("corner_radius", 8)
    kwargs.setdefault("height", 30)
    return ctk.CTkButton(master, **kwargs)


def secondary_button(master: Any, **kwargs: Any) -> ctk.CTkButton:
    theme = get_theme()
    kwargs.setdefault("fg_color", theme.surface_alt)
    kwargs.setdefault("hover_color", theme.border)
    kwargs.setdefault("text_color", theme.fg)
    kwargs.setdefault("border_width", 1)
    kwargs.setdefault("border_color", theme.border)
    kwargs.setdefault("corner_radius", 8)
    kwargs.setdefault("height", 30)
    return ctk.CTkButton(master, **kwargs)


class ThemedButton(tk.Button):
    """
    Flat tk.Button styled like CTk accent/secondary controls.
    Safe inside scroll canvases (unlike customtkinter widgets).
    """

    def __init__(
        self,
        master: Any,
        *,
        variant: str = "secondary",
        **kwargs: Any,
    ):
        self._variant = variant
        theme = get_theme()
        # solid + visible border: flat tk.Button on Windows often hides borderwidth
        kwargs.setdefault("relief", "solid")
        kwargs.setdefault("borderwidth", 1)
        kwargs.setdefault("cursor", "hand2")
        kwargs.setdefault("padx", 10)
        kwargs.setdefault("pady", 5)
        kwargs.setdefault("highlightthickness", 1)
        kwargs.setdefault("anchor", "w")
        super().__init__(master, **kwargs)
        self.apply_theme(theme)
        self.bind("<Enter>", self._on_enter, add="+")
        self.bind("<Leave>", self._on_leave, add="+")

    def apply_theme(self, theme: Theme | None = None) -> None:
        theme = theme or get_theme()
        if self._variant == "accent":
            bg = theme.accent
            hover = theme.accent_hover
            fg = "#ffffff"
            active_bg = theme.accent_hover
            border = theme.accent
        else:
            # Soft chip on card surface — avoid heavy black outlines in light mode
            bg = theme.surface_alt
            hover = theme.border
            fg = theme.fg
            active_bg = hover
            border = theme.border
        self._bg = bg
        self._hover = hover
        self.configure(
            background=bg,
            foreground=fg,
            activebackground=active_bg,
            activeforeground=fg,
            disabledforeground=theme.muted,
            highlightbackground=border,
            highlightcolor=border,
            relief="solid",
            borderwidth=1,
        )

    def _on_enter(self, _event: tk.Event[tk.Misc] | None = None) -> None:
        if str(self.cget("state")) != "disabled":
            self.configure(background=self._hover)

    def _on_leave(self, _event: tk.Event[tk.Misc] | None = None) -> None:
        if str(self.cget("state")) != "disabled":
            self.configure(background=self._bg)


def accent_tk_button(master: Any, **kwargs: Any) -> ThemedButton:
    return ThemedButton(master, variant="accent", **kwargs)


def secondary_tk_button(master: Any, **kwargs: Any) -> ThemedButton:
    return ThemedButton(master, variant="secondary", **kwargs)


class FlatScrollbar(tk.Canvas):
    """
    Canvas scrollbar — native tk/ttk scrollbars ignore colors on Windows.
    Compatible with xscrollcommand/yscrollcommand via .set(first, last).
    """

    def __init__(
        self,
        master: Any,
        *,
        orient: str = "vertical",
        command: Any = None,
        width: int = 12,
        **kwargs: Any,
    ):
        theme = get_theme()
        self._orient = orient
        self._command = command
        self._first = 0.0
        self._last = 1.0
        self._trough = theme.scroll_trough
        self._thumb = theme.scroll_thumb
        self._thumb_active = theme.scroll_active
        self._dragging = False
        self._drag_offset = 0.0
        size = {"width": width, "height": width}
        if orient == "vertical":
            size["width"] = width
        else:
            size["height"] = width
        super().__init__(
            master,
            highlightthickness=0,
            bd=0,
            bg=self._trough,
            **size,
            **kwargs,
        )
        self.bind("<Configure>", self._redraw, add="+")
        self.bind("<Button-1>", self._on_press, add="+")
        self.bind("<B1-Motion>", self._on_drag, add="+")
        self.bind("<ButtonRelease-1>", self._on_release, add="+")
        self._redraw()

    def set(self, first: str | float, last: str | float) -> None:
        self._first = float(first)
        self._last = float(last)
        self._redraw()

    def configure_theme(self, *, trough: str, thumb: str, active: str | None = None) -> None:
        self._trough = trough
        self._thumb = thumb
        self._thumb_active = active or thumb
        self.configure(bg=trough)
        self._redraw()

    # tk.Scrollbar API shim used by some call sites
    def config(self, cnf: Any = None, **kwargs: Any) -> Any:
        if isinstance(cnf, dict):
            kwargs = {**cnf, **kwargs}
            cnf = None
        if "command" in kwargs:
            self._command = kwargs.pop("command")
        if kwargs:
            return super().configure(**kwargs)
        return None

    def _thumb_rect(self) -> tuple[float, float, float, float]:
        self.update_idletasks()
        w = max(self.winfo_width(), 1)
        h = max(self.winfo_height(), 1)
        span = max(self._last - self._first, 0.05)
        if self._orient == "vertical":
            y0 = self._first * h
            y1 = max(y0 + span * h, y0 + 18)
            return (2, y0, w - 2, min(y1, h))
        x0 = self._first * w
        x1 = max(x0 + span * w, x0 + 18)
        return (x0, 2, min(x1, w), h - 2)

    def _redraw(self, _event: Any = None) -> None:
        self.delete("all")
        x0, y0, x1, y1 = self._thumb_rect()
        color = self._thumb_active if self._dragging else self._thumb
        self.create_rectangle(x0, y0, x1, y1, fill=color, outline=color, tags="thumb")

    def _fraction_at(self, event: tk.Event[tk.Misc]) -> float:
        if self._orient == "vertical":
            size = max(self.winfo_height(), 1)
            pos = event.y
        else:
            size = max(self.winfo_width(), 1)
            pos = event.x
        return max(0.0, min(1.0, pos / size))

    def _move_to(self, fraction: float) -> None:
        if self._command is None:
            return
        span = max(self._last - self._first, 0.05)
        first = max(0.0, min(1.0 - span, fraction))
        self._command("moveto", first)

    def _on_press(self, event: tk.Event[tk.Misc]) -> None:
        x0, y0, x1, y1 = self._thumb_rect()
        if self._orient == "vertical":
            inside = y0 <= event.y <= y1
            self._drag_offset = (event.y - y0) / max(self.winfo_height(), 1)
        else:
            inside = x0 <= event.x <= x1
            self._drag_offset = (event.x - x0) / max(self.winfo_width(), 1)
        self._dragging = True
        if inside:
            return
        # Jump trough click
        span = max(self._last - self._first, 0.05)
        self._move_to(self._fraction_at(event) - span / 2)
        self._redraw()

    def _on_drag(self, event: tk.Event[tk.Misc]) -> None:
        if not self._dragging:
            return
        span = max(self._last - self._first, 0.05)
        self._move_to(self._fraction_at(event) - self._drag_offset)
        self._redraw()

    def _on_release(self, _event: tk.Event[tk.Misc]) -> None:
        self._dragging = False
        self._redraw()
