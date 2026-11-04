"""Twitch-inspired CustomTkinter color tokens (dark/light)."""

from __future__ import annotations

from dataclasses import dataclass

import customtkinter as ctk

@dataclass(frozen=True)
class Theme:
    dark: bool
    bg: str
    surface: str
    surface_alt: str
    fg: str
    muted: str
    accent: str
    accent_hover: str
    border: str
    success: str
    warning: str
    danger: str
    link: str
    sel_bg: str
    sel_fg: str
    field_bg: str
    progress_trough: str
    scroll_trough: str
    scroll_thumb: str
    scroll_active: str
    warn_bg: str
    warn_border: str
    warn_title: str
    warn_body: str

DARK = Theme(
    dark=True,
    bg="#0e0e10",
    surface="#18181b",
    surface_alt="#1f1f23",
    fg="#efeff1",
    muted="#adadb8",
    accent="#9146FF",
    accent_hover="#772ce8",
    border="#2c2c35",
    success="#00f593",
    warning="#eab308",
    danger="#eb0400",
    link="#bf94ff",
    sel_bg="#5c16c5",
    sel_fg="#ffffff",
    field_bg="#0e0e10",
    progress_trough="#2c2c35",
    scroll_trough="#0e0e10",
    scroll_thumb="#3f3f46",
    scroll_active="#71717a",
    warn_bg="#2a1518",
    warn_border="#7f1d1d",
    warn_title="#f87171",
    warn_body="#fbbf24",
)

LIGHT = Theme(
    dark=False,
    # Soft page + card tones — pure #ffffff cards/checkboxes read as a white flash
    # when switching tabs after a theme flip (user screenshots of Filter section).
    bg="#f2f2f4",
    surface="#e8e8ec",
    surface_alt="#dedee3",
    fg="#0e0e10",
    muted="#5a5a66",
    accent="#9146FF",
    accent_hover="#772ce8",
    border="#c8c8d0",
    success="#0a7b46",
    warning="#a16207",
    danger="#c41e1e",
    link="#5c16c5",
    sel_bg="#9146FF",
    sel_fg="#ffffff",
    field_bg="#f0f0f3",
    progress_trough="#d8d8de",
    scroll_trough="#e0e0e6",
    scroll_thumb="#a8a8b0",
    scroll_active="#7a7a84",
    warn_bg="#fff7ed",
    warn_border="#fdba74",
    warn_title="#c2410c",
    warn_body="#9a3412",
)

_current: Theme = DARK

def get_theme() -> Theme:
    return _current

_ctk_base_theme_ready = False

def apply_appearance(dark: bool, *, cascade: bool = True) -> Theme:
    """Update theme tokens. Optionally cascade CTk's global appearance redraw.

    Live theme toggles pass cascade=False: set_appearance_mode walks every CTk
    widget with stock Light/Dark colors before our Twitch palette is applied,
    which looks like a full UI rebuild. apply_theme pushes absolute hex colors.
    """
    global _current, _ctk_base_theme_ready
    _current = DARK if dark else LIGHT
    if not _ctk_base_theme_ready:
        # Install once — repeating on every toggle reloads theme JSON for nothing.
        ctk.set_default_color_theme("dark-blue")
        _ctk_base_theme_ready = True
    mode = "Dark" if dark else "Light"
    if cascade:
        try:
            if ctk.get_appearance_mode() != mode:
                ctk.set_appearance_mode(mode)
        except Exception:
            ctk.set_appearance_mode(mode)
    return _current
