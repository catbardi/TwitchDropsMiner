from __future__ import annotations

import os
import re
import sys
import shlex
import ctypes
import asyncio
import logging
import plistlib
import tkinter as tk
from pathlib import Path
from collections import abc
from contextlib import suppress
from textwrap import dedent
from math import log10, ceil
from dataclasses import dataclass
from tkinter.font import Font, nametofont
from functools import partial, cached_property
from datetime import datetime, timedelta, timezone
from tkinter import Tk, ttk, StringVar, DoubleVar, IntVar
from typing import Any, Union, Tuple, TypedDict, NoReturn, Generic, TYPE_CHECKING

import pystray
from yarl import URL
from PIL.ImageTk import PhotoImage
from PIL import Image as Image_module

if sys.platform == "win32":
    import win32api
    import win32con
    import win32gui

if sys.platform == "darwin":
    import AppKit

import customtkinter as ctk

from translate import _
from cache import ImageCache
from exceptions import MinerException, ExitRequest
from utils import resource_path, set_root_icon, webopen, task_wrapper, Game, _T
from utils import autocomplete_matches
from ui.theme import Theme, apply_appearance, get_theme
from ui.inventory_list import VirtualCampaignList
from ui.widgets import (
    SectionFrame,
    accent_button,
    secondary_button,
    accent_tk_button,
    secondary_tk_button,
    settings_switch,
    warning_banner,
    apply_warning_banner_theme,
    FlatScrollbar,
    ThemedButton,
)
from constants import (
    MAX_INT,
    SELF_PATH,
    IS_PACKAGED,
    SCRIPTS_PATH,
    WINDOW_TITLE,
    FORK_OWNER,
    FORK_REPOSITORY,
    ORIGINAL_REPOSITORY,
    LOGGING_LEVELS,
    MAX_WEBSOCKETS,
    WS_TOPICS_LIMIT,
    OUTPUT_FORMATTER,
    State,
    PriorityMode,
)
if sys.platform == "win32":
    from registry import RegistryKey, ValueType, ValueNotFound

if TYPE_CHECKING:
    from twitch import Twitch
    from channel import Channel
    from settings import Settings
    from inventory import DropsCampaign, TimedDrop

logger = logging.getLogger("TwitchDrops")
TK_PADDING = Union[int, Tuple[int, int], Tuple[int, int, int], Tuple[int, int, int, int]]
DIGITS = ceil(log10(WS_TOPICS_LIMIT))

######################
# GUI ELEMENTS START #
######################

class _TKOutputHandler(logging.Handler):
    def __init__(self, output: GUIManager):
        super().__init__()
        self._output = output

    def emit(self, record):
        self._output.print(self.format(record))

class PlaceholderEntry(ttk.Entry):
    def __init__(
        self,
        master: ttk.Widget,
        *args: Any,
        placeholder: str,
        prefill: str = '',
        placeholdercolor: str = "grey60",
        **kwargs: Any,
    ):
        super().__init__(master, *args, **kwargs)
        self._prefill: str = prefill
        self._show: str = kwargs.get("show", '')
        self._text_color: str = kwargs.get("foreground", '')
        self._ph_color: str = placeholdercolor
        self._ph_text: str = placeholder
        self.bind("<FocusIn>", self._focus_in)
        self.bind("<FocusOut>", self._focus_out)
        if isinstance(self, ttk.Combobox):
            # only bind this for comboboxes
            self.bind("<<ComboboxSelected>>", self._combobox_select)
        self._ph: bool = False
        self._insert_placeholder()

    def _insert_placeholder(self) -> None:
        """
        If we're empty, insert a placeholder, set placeholder text color and make sure it's shown.
        If we're not empty, leave the box as is.
        """
        if not super().get():
            self._ph = True
            super().config(foreground=self._ph_color, show='')
            super().insert("end", self._ph_text)

    def _remove_placeholder(self) -> None:
        """
        If we've had a placeholder, clear the box and set normal text colour and show.
        """
        if self._ph:
            self._ph = False
            super().delete(0, "end")
            super().config(foreground=self._text_color, show=self._show)
            if self._prefill:
                super().insert("end", self._prefill)

    def _focus_in(self, event: tk.Event[PlaceholderEntry]) -> None:
        self._remove_placeholder()

    def _focus_out(self, event: tk.Event[PlaceholderEntry]) -> None:
        self._insert_placeholder()

    def _combobox_select(self, event: tk.Event[PlaceholderEntry]):
        # combobox clears and inserts the selected value internally, bypassing the insert method.
        # disable the placeholder flag and set the color here, so _focus_in doesn't clear the entry
        self._ph = False
        super().config(foreground=self._text_color, show=self._show)

    def _store_option(
        self, options: dict[str, object], name: str, attr: str, *, remove: bool = False
    ) -> None:
        if name in options:
            if remove:
                value = options.pop(name)
            else:
                value = options[name]
            setattr(self, attr, value)

    def configure(self, *args: Any, **kwargs: Any) -> Any:
        options: dict[str, Any] = {}
        if args and args[0] is not None:
            options.update(args[0])
        if kwargs:
            options.update(kwargs)
        self._store_option(options, "show", "_show")
        self._store_option(options, "foreground", "_text_color")
        self._store_option(options, "placeholder", "_ph_text", remove=True)
        self._store_option(options, "prefill", "_prefill", remove=True)
        self._store_option(options, "placeholdercolor", "_ph_color", remove=True)
        return super().configure(**kwargs)

    def config(self, *args: Any, **kwargs: Any) -> Any:
        # because 'config = configure' makes mypy complain
        self.configure(*args, **kwargs)

    def get(self) -> str:
        if self._ph:
            return ''
        return super().get()

    def insert(self, index: str | int, content: str) -> None:
        # when inserting into the entry externally, disable the placeholder flag
        if not content:
            # if an empty string was passed in
            return
        self._remove_placeholder()
        super().insert(index, content)

    def delete(self, first: str | int, last: str | int | None = None) -> None:
        super().delete(first, last)
        self._insert_placeholder()

    def clear(self) -> None:
        self.delete(0, "end")

    def replace(self, content: str) -> None:
        super().delete(0, "end")
        self.insert("end", content)

class AutocompleteCombobox(PlaceholderEntry, ttk.Combobox):
    _IGNORED_KEYS = frozenset({"Up", "Down", "Return", "Escape", "Tab"})
    _EDIT_KEYS = frozenset({"BackSpace", "Delete", "Left", "Right", "Home", "End"})

    def __init__(
        self,
        master: ttk.Widget,
        *args: Any,
        search_callback: abc.Callable[[str], None] | None = None,
        max_results: int = 20,
        **kwargs: Any,
    ) -> None:
        self._completion_values: tuple[str, ...] = ()
        self._max_results = max_results
        self._search_callback = search_callback
        self._popdown_listbox: str | None = None
        super().__init__(master, *args, **kwargs)
        # A posted ttk combobox popup handles the next key as a listbox
        # navigation/selection event on Windows. Close it before normal
        # text input is processed and reopen it after the new query is ready.
        self.bind("<KeyPress>", self._prepare_for_typing, add="+")
        self.bind("<KeyRelease>", self._autocomplete, add="+")

    def set_completion_values(self, values: abc.Iterable[str]) -> None:
        self._completion_values = tuple(values)
        self._apply_completion_filter()

    def _apply_completion_filter(self) -> list[str]:
        query = self.get()
        if query:
            matches = autocomplete_matches(
                self._completion_values,
                query,
                limit=self._max_results,
            )
        else:
            matches = list(self._completion_values)
        super().configure(values=matches)
        return matches

    def _autocomplete(self, event: tk.Event[AutocompleteCombobox]) -> None:
        if self._ph or event.keysym in self._IGNORED_KEYS:
            return
        matches = self._apply_completion_filter()
        if self._search_callback is not None:
            self._search_callback(self.get())
        if self.get() and matches:
            self.after_idle(self._post_suggestions)
        else:
            self._unpost_suggestions()

    def _prepare_for_typing(self, event: tk.Event[AutocompleteCombobox]) -> None:
        """Keep ordinary typing in the entry while suggestions are visible."""
        if event.keysym not in self._IGNORED_KEYS:
            self._unpost_suggestions()

    def _bind_popdown_input(self) -> None:
        try:
            popdown = str(self.tk.call("ttk::combobox::PopdownWindow", self._w))
            listbox = f"{popdown}.f.l"
            if listbox == self._popdown_listbox:
                return
            self._root()._bind(
                ("bind", listbox), "<KeyPress>", self._popdown_keypress, "+"
            )
            self._popdown_listbox = listbox
        except tk.TclError:
            pass

    def _popdown_keypress(self, event: tk.Event[AutocompleteCombobox]) -> str | None:
        if event.keysym in self._IGNORED_KEYS:
            return
        if not event.char.isprintable() and event.keysym not in self._EDIT_KEYS:
            return
        self._unpost_suggestions()
        self.focus_set()
        try:
            selection = self.index("sel.first"), self.index("sel.last")
        except tk.TclError:
            selection = None
        if event.char.isprintable():
            if selection is not None:
                self.delete(*selection)
            self.insert("insert", event.char)
        elif selection is not None:
            self.delete(*selection)
        elif event.keysym == "BackSpace":
            cursor = self.index("insert")
            if cursor > 0:
                self.delete(cursor - 1, cursor)
        elif event.keysym == "Delete":
            cursor = self.index("insert")
            if cursor < self.index("end"):
                self.delete(cursor, cursor + 1)
        elif event.keysym == "Left":
            self.icursor(max(0, self.index("insert") - 1))
        elif event.keysym == "Right":
            self.icursor(min(self.index("end"), self.index("insert") + 1))
        elif event.keysym == "Home":
            self.icursor(0)
        elif event.keysym == "End":
            self.icursor("end")
        self._autocomplete(event)
        return "break"

    def _post_suggestions(self) -> None:
        if self.focus_get() is not self or not self.get():
            return
        try:
            self.tk.call("ttk::combobox::Post", self._w)
            self._bind_popdown_input()
        except tk.TclError:
            pass

    def _unpost_suggestions(self) -> None:
        try:
            self.tk.call("ttk::combobox::Unpost", self._w)
        except tk.TclError:
            pass

class PaddedListbox(tk.Listbox):
    def __init__(self, master: ttk.Widget, *args, padding: TK_PADDING = (0, 0, 0, 0), **kwargs):
        # we place the listbox inside a frame with the same background
        # this means we need to forward the 'grid' method to the frame, not the listbox
        self._frame = tk.Frame(master)
        self._frame.rowconfigure(0, weight=1)
        self._frame.columnconfigure(0, weight=1)
        super().__init__(self._frame)
        # mimic default listbox style with sunken relief and borderwidth of 1
        if "relief" not in kwargs:
            kwargs["relief"] = "sunken"
        if "borderwidth" not in kwargs:
            kwargs["borderwidth"] = 1
        self.configure(*args, padding=padding, **kwargs)

    def grid(self, *args, **kwargs):
        return self._frame.grid(*args, **kwargs)

    def grid_remove(self) -> None:
        return self._frame.grid_remove()

    def grid_info(self) -> tk._GridInfo:
        return self._frame.grid_info()

    def grid_forget(self) -> None:
        return self._frame.grid_forget()

    def configure(self, *args: Any, **kwargs: Any) -> Any:
        options: dict[str, Any] = {}
        if args and args[0] is not None:
            options.update(args[0])
        if kwargs:
            options.update(kwargs)
        # NOTE on processed options:
        # • relief is applied to the frame only
        # • background is copied, so that both listbox and frame change color
        # • borderwidth is applied to the frame only
        # bg is folded into background for easier processing
        if "bg" in options:
            options["background"] = options.pop("bg")
        frame_options = {}
        if "relief" in options:
            frame_options["relief"] = options.pop("relief")
        if "background" in options:
            frame_options["background"] = options["background"]  # copy
        if "borderwidth" in options:
            frame_options["borderwidth"] = options.pop("borderwidth")
        self._frame.configure(frame_options)
        # update padding
        if "padding" in options:
            padding: TK_PADDING = options.pop("padding")
            padx1: tk._ScreenUnits
            padx2: tk._ScreenUnits
            pady1: tk._ScreenUnits
            pady2: tk._ScreenUnits
            if not isinstance(padding, tuple) or len(padding) == 1:
                if isinstance(padding, tuple):
                    padding = padding[0]
                padx1 = padx2 = pady1 = pady2 = padding
            elif len(padding) == 2:
                padx1 = padx2 = padding[0]
                pady1 = pady2 = padding[1]
            elif len(padding) == 3:
                padx1, padx2 = padding[0], padding[1]
                pady1 = pady2 = padding[2]
            else:
                padx1, padx2, pady1, pady2 = padding
            super().grid(column=0, row=0, padx=(padx1, padx2), pady=(pady1, pady2), sticky="nsew")
        else:
            super().grid(column=0, row=0, sticky="nsew")
        # listbox uses flat relief to blend in with the inside of the frame
        options["relief"] = "flat"
        return super().configure(options)

    def config(self, *args: Any, **kwargs: Any) -> Any:
        # because 'config = configure' makes mypy complain
        self.configure(*args, **kwargs)

    def configure_theme(self, *, bg: str, fg: str, sel_bg: str, sel_fg: str):
        # Flat well — outer CTk shell owns the soft border (avoids black sunken edge).
        self.configure(
            background=bg,
            foreground=fg,
            selectbackground=sel_bg,
            selectforeground=sel_fg,
            highlightbackground=bg,
            highlightcolor=bg,
            disabledforeground=fg,
            relief="flat",
            borderwidth=0,
            highlightthickness=0,
        )

class MouseOverLabel(ttk.Label):
    def __init__(self, *args, alt_text: str = '', reverse: bool = False, **kwargs) -> None:
        self._org_text: str = ''
        self._alt_text: str = ''
        self._alt_reverse: bool = reverse
        self._bind_enter: str | None = None
        self._bind_leave: str | None = None
        super().__init__(*args, **kwargs)
        self.configure(text=kwargs.get("text", ''), alt_text=alt_text, reverse=reverse)

    def _set_org(self, event: tk.Event[MouseOverLabel]):
        super().config(text=self._org_text)

    def _set_alt(self, event: tk.Event[MouseOverLabel]):
        super().config(text=self._alt_text)

    def configure(self, *args: Any, **kwargs: Any) -> Any:
        options: dict[str, Any] = {}
        if args and args[0] is not None:
            options.update(args[0])
        if kwargs:
            options.update(kwargs)
        applicable_options: set[str] = set((
            "text",
            "reverse",
            "alt_text",
        ))
        if applicable_options.intersection(options.keys()):
            # we need to pop some options, because they can't be passed down to the label,
            # as that will result in an error later down the line
            events_change: bool = False
            if "text" in options:
                if bool(self._org_text) != bool(options["text"]):
                    events_change = True
                self._org_text = options["text"]
            if "alt_text" in options:
                if bool(self._alt_text) != bool(options["alt_text"]):
                    events_change = True
                self._alt_text = options.pop("alt_text")
            if "reverse" in options:
                if bool(self._alt_reverse) != bool(options["reverse"]):
                    events_change = True
                self._alt_reverse = options.pop("reverse")
            if self._org_text and not self._alt_text:
                options["text"] = self._org_text
            elif (not self._org_text or self._alt_reverse) and self._alt_text:
                options["text"] = self._alt_text
            if events_change:
                if self._bind_enter is not None:
                    self.unbind(self._bind_enter)
                    self._bind_enter = None
                if self._bind_leave is not None:
                    self.unbind(self._bind_leave)
                    self._bind_leave = None
                if self._org_text and self._alt_text:
                    if self._alt_reverse:
                        self._bind_enter = self.bind("<Enter>", self._set_org)
                        self._bind_leave = self.bind("<Leave>", self._set_alt)
                    else:
                        self._bind_enter = self.bind("<Enter>", self._set_alt)
                        self._bind_leave = self.bind("<Leave>", self._set_org)
        return super().configure(options)

    def config(self, *args: Any, **kwargs: Any) -> Any:
        # because 'config = configure' makes mypy complain
        self.configure(*args, **kwargs)

class LinkLabel(ttk.Label):
    def __init__(self, *args, link: str, **kwargs) -> None:
        self._link: str = link
        # style provides font and foreground color
        if "style" not in kwargs:
            kwargs["style"] = "Link.TLabel"
        elif not kwargs["style"]:
            super().__init__(*args, **kwargs)
            return
        if "cursor" not in kwargs:
            kwargs["cursor"] = "hand2"
        if "padding" not in kwargs:
            # W, N, E, S
            kwargs["padding"] = (0, 2, 0, 2)
        super().__init__(*args, **kwargs)
        self.bind("<ButtonRelease-1>", lambda e: webopen(self._link))

class SelectMenu(tk.Menubutton, Generic[_T]):
    def __init__(
        self,
        master: tk.Misc,
        *args: Any,
        tearoff: bool = False,
        options: dict[str, _T],
        command: abc.Callable[[_T], Any] | None = None,
        default: str | None = None,
        relief: tk._Relief = "solid",
        background: str = "white",
        **kwargs: Any,
    ):
        width = max((len(k) for k in options.keys()), default=20)
        super().__init__(
            master, *args, background=background, relief=relief, width=width, **kwargs
        )
        self._menu_options: dict[str, _T] = options
        self._command = command
        self.menu = tk.Menu(self, tearoff=tearoff)
        self.config(menu=self.menu)
        for name in options.keys():
            self.menu.add_command(label=name, command=partial(self._select, name))
        if default is not None and default in self._menu_options:
            self.config(text=default)

    def _select(self, option: str) -> None:
        self.config(text=option)
        if self._command is not None:
            self._command(self._menu_options[option])

    def get(self) -> _T:
        return self._menu_options[self.cget("text")]

class SelectCombobox(ttk.Combobox):
    def __init__(
        self,
        master: tk.Misc,
        *args,
        width_offset: int = 0,
        width: int | None = None,
        textvariable: tk.StringVar,
        values: list[str] | tuple[str, ...],
        command: abc.Callable[[tk.Event[SelectCombobox]], None] | None = None,
        **kwargs,
    ) -> None:
        if width is None:
            font = Font(master, ttk.Style().lookup("TCombobox", "font"))
            # font.measure returns width in pixels, using '0' as the average character,
            # which is 6 pixels wide. We can convert it to width in characters by dividing.
            width = max(font.measure(v) // 6 + 1 for v in values)
        width += width_offset
        super().__init__(
            master,
            *args,
            width=width,
            values=values,
            state="readonly",
            exportselection=False,
            textvariable=textvariable,
            **kwargs,
        )
        if command is not None:
            self.bind("<<ComboboxSelected>>", command)

###########################################
# GUI ELEMENTS END / GUI DEFINITION START #
###########################################

class StatusBar:
    def __init__(self, manager: GUIManager, master: tk.Misc):
        self._manager = manager
        self._text = ""
        section = SectionFrame(master, _("gui", "status", "name"))
        section.grid(column=0, row=0, columnspan=3, sticky="nsew", padx=4, pady=4)
        self._label = ctk.CTkLabel(section.body, text="", anchor="w")
        self._label.grid(column=0, row=0, sticky="nsew")

    def update(self, text: str):
        self._text = text
        self._label.configure(text=text)
        # Keep tray tooltip in sync so hover shows working vs idle.
        self._manager.tray.set_status(text)

    def clear(self):
        self.update("")

class _WSEntry(TypedDict):
    status: str
    topics: int

class WebsocketStatus:
    def __init__(self, manager: GUIManager, master: tk.Misc):
        section = SectionFrame(master, _("gui", "websocket", "name"))
        section.grid(column=0, row=1, sticky="nsew", padx=4, pady=4)
        self._status_var = StringVar(master)
        self._topics_var = StringVar(master)
        mono = ctk.CTkFont(family="Consolas", size=12)
        ctk.CTkLabel(
            section.body,
            text='\n'.join(
                _("gui", "websocket", "websocket").format(id=i)
                for i in range(1, MAX_WEBSOCKETS + 1)
            ),
            justify="left",
            font=mono,
            anchor="w",
        ).grid(column=0, row=0, sticky="nw")
        ctk.CTkLabel(
            section.body,
            textvariable=self._status_var,
            justify="left",
            font=mono,
            anchor="w",
            width=140,
        ).grid(column=1, row=0, sticky="nw", padx=8)
        ctk.CTkLabel(
            section.body,
            textvariable=self._topics_var,
            justify="right",
            font=mono,
            anchor="e",
            width=60,
        ).grid(column=2, row=0, sticky="ne")
        self._items: dict[int, _WSEntry | None] = {i: None for i in range(MAX_WEBSOCKETS)}
        self._update()

    def update(self, idx: int, status: str | None = None, topics: int | None = None):
        if status is None and topics is None:
            raise TypeError("You need to provide at least one of: status, topics")
        entry = self._items.get(idx)
        if entry is None:
            entry = self._items[idx] = _WSEntry(
                status=_("gui", "websocket", "disconnected"), topics=0
            )
        if status is not None:
            entry["status"] = status
        if topics is not None:
            entry["topics"] = topics
        self._update()

    def remove(self, idx: int):
        if idx in self._items:
            del self._items[idx]
            self._update()

    def _update(self):
        status_lines: list[str] = []
        topic_lines: list[str] = []
        for idx in range(MAX_WEBSOCKETS):
            if (item := self._items.get(idx)) is None:
                status_lines.append('')
                topic_lines.append('')
            else:
                status_lines.append(item["status"])
                topic_lines.append(f"{item['topics']:>{DIGITS}}/{WS_TOPICS_LIMIT}")
        self._status_var.set('\n'.join(status_lines))
        self._topics_var.set('\n'.join(topic_lines))

@dataclass
class LoginData:
    username: str
    password: str
    token: str

class LoginForm:
    def __init__(self, manager: GUIManager, master: tk.Misc):
        self._manager = manager
        self._var = StringVar(master)
        section = SectionFrame(master, _("gui", "login", "name"))
        section.grid(column=1, row=1, sticky="nsew", padx=4, pady=4)
        frame = section.body
        frame.columnconfigure(0, weight=2)
        frame.columnconfigure(1, weight=1)
        frame.rowconfigure(4, weight=1)
        ctk.CTkLabel(frame, text=_("gui", "login", "labels"), anchor="w").grid(
            column=0, row=0, sticky="w"
        )
        ctk.CTkLabel(frame, textvariable=self._var, justify="center").grid(column=1, row=0)
        self._login_entry = PlaceholderEntry(frame, placeholder=_("gui", "login", "username"))
        self._pass_entry = PlaceholderEntry(
            frame, placeholder=_("gui", "login", "password"), show='•'
        )
        self._token_entry = PlaceholderEntry(frame, placeholder=_("gui", "login", "twofa_code"))

        self._confirm = asyncio.Event()
        buttons_frame = ctk.CTkFrame(frame, fg_color="transparent")
        buttons_frame.grid(column=0, row=4, columnspan=2, pady=(8, 0))
        self._button = accent_button(
            buttons_frame,
            text=_("gui", "login", "button"),
            command=self._confirm.set,
            state="disabled",
            width=110,
        )
        self._button.grid(column=0, row=0, padx=(0, 6))
        self._logout_button = secondary_button(
            buttons_frame,
            text=_("gui", "login", "logout"),
            command=self.logout,
            state="disabled",
            width=110,
        )
        self._logout_button.grid(column=1, row=0)
        self.update(_("gui", "login", "logged_out"), None)

    def clear(self, login: bool = False, password: bool = False, token: bool = False):
        clear_all = not login and not password and not token
        if login or clear_all:
            self._login_entry.clear()
        if password or clear_all:
            self._pass_entry.clear()
        if token or clear_all:
            self._token_entry.clear()

    async def wait_for_login_press(self) -> None:
        self._confirm.clear()
        try:
            self._button.configure(state="normal")
            await self._manager.coro_unless_closed(self._confirm.wait())
        finally:
            self._button.configure(state="disabled")

    async def ask_login(self) -> LoginData:
        self.update(_("gui", "login", "required"), None)
        # ensure the window isn't hidden into tray when this runs
        self._manager.grab_attention(sound=False)
        while True:
            logging.getLogger("TwitchDrops").info(_("gui", "login", "request"))
            await self.wait_for_login_press()
            login_data = LoginData(
                self._login_entry.get().strip(),
                self._pass_entry.get(),
                self._token_entry.get().strip(),
            )
            # basic input data validation: 3-25 characters in length, only ascii and underscores
            if (
                not 3 <= len(login_data.username) <= 25
                and re.match(r'^[a-zA-Z0-9_]+$', login_data.username)
            ):
                self.clear(login=True)
                continue
            if len(login_data.password) < 8:
                self.clear(password=True)
                continue
            if login_data.token and len(login_data.token) < 6:
                self.clear(token=True)
                continue
            return login_data

    async def ask_enter_code(self, page_url: URL, user_code: str) -> None:
        self.update(_("gui", "login", "required"), None)
        # ensure the window isn't hidden into tray when this runs
        self._manager.grab_attention(sound=False)
        logging.getLogger("TwitchDrops").info(_("gui", "login", "request"))
        await self.wait_for_login_press()
        # Device code must stay visible in the journal for the user to type it.
        code_msg = f"Enter this code on the Twitch's device activation page: {user_code}"
        logging.getLogger("TwitchDrops").info(code_msg)
        self._manager.output.print(code_msg)
        await asyncio.sleep(4)
        webopen(page_url)

    def update(self, status: str, user_id: int | None):
        if user_id is not None:
            user_str = str(user_id)
        else:
            user_str = "-"
        self._var.set(f"{status}\n{user_str}")
        authenticated = user_id is not None
        if authenticated:
            self._button.grid_remove()
            self._logout_button.grid()
        else:
            self._logout_button.grid_remove()
            self._button.grid()
        self._button.configure(
            state=("disabled" if authenticated or status not in (
                _("gui", "login", "logged_out"),
                _("gui", "login", "required"),
            ) else "normal")
        )
        self._logout_button.configure(state="normal" if authenticated else "disabled")

    def logout(self) -> None:
        asyncio.create_task(task_wrapper(self._logout)())

    async def _logout(self) -> None:
        auth_state = await self._manager._twitch.get_auth()
        async with self._manager._twitch.request(
            "POST",
            "https://id.twitch.tv/oauth2/revoke",
            data={
                "client_id": self._manager._twitch._client_type.CLIENT_ID,
                "token": auth_state.access_token,
            },
        ) as response:
            if response.status != 200:
                logger.error(f"Failed to revoke the auth token: {response.status}")
        auth_state.invalidate(delete_cookies=True)
        self._manager._twitch.change_state(State.RESTART)

class _BaseVars(TypedDict):
    progress: DoubleVar
    percentage: StringVar
    remaining: StringVar

class _CampaignVars(_BaseVars):
    name: StringVar
    game: StringVar

class _DropVars(_BaseVars):
    rewards: StringVar

class _ProgressVars(TypedDict):
    campaign: _CampaignVars
    drop: _DropVars

class CampaignProgress:
    BAR_LENGTH = 420
    ALMOST_DONE_SECONDS = 10

    def __init__(self, manager: GUIManager, master: tk.Misc):
        self._manager = manager
        theme = get_theme()
        self._vars: _ProgressVars = {
            "campaign": {
                "name": StringVar(master),  # campaign name
                "game": StringVar(master),  # game name
                "progress": DoubleVar(master),  # controls the progress bar
                "percentage": StringVar(master),  # percentage display string
                "remaining": StringVar(master),  # time remaining string, filled via _update_time
            },
            "drop": {
                "rewards": StringVar(master),  # drop rewards
                "progress": DoubleVar(master),  # as above
                "percentage": StringVar(master),  # as above
                "remaining": StringVar(master),  # as above
            },
        }
        section = SectionFrame(master, _("gui", "progress", "name"))
        section.grid(column=0, row=2, columnspan=2, sticky="nsew", padx=4, pady=4)
        frame = section.body
        frame.columnconfigure(0, weight=2)
        frame.columnconfigure(1, weight=1)
        game_campaign = ctk.CTkFrame(frame, fg_color="transparent")
        game_campaign.grid(column=0, row=0, columnspan=2, sticky="nsew")
        game_campaign.columnconfigure(0, weight=1)
        game_campaign.columnconfigure(1, weight=1)
        ctk.CTkLabel(game_campaign, text=_("gui", "progress", "game"), text_color=theme.muted).grid(
            column=0, row=0, sticky="w"
        )
        ctk.CTkLabel(game_campaign, textvariable=self._vars["campaign"]["game"]).grid(
            column=0, row=1, sticky="w"
        )
        ctk.CTkLabel(
            game_campaign, text=_("gui", "progress", "campaign"), text_color=theme.muted
        ).grid(column=1, row=0, sticky="w")
        ctk.CTkLabel(game_campaign, textvariable=self._vars["campaign"]["name"]).grid(
            column=1, row=1, sticky="w"
        )
        ctk.CTkLabel(frame, text=_("gui", "progress", "campaign_progress")).grid(
            column=0, row=2, rowspan=2, sticky="w"
        )
        ctk.CTkLabel(frame, textvariable=self._vars["campaign"]["percentage"]).grid(
            column=1, row=2, sticky="e"
        )
        ctk.CTkLabel(frame, textvariable=self._vars["campaign"]["remaining"]).grid(
            column=1, row=3, sticky="e"
        )
        self._campaign_bar = ctk.CTkProgressBar(
            frame,
            width=self.BAR_LENGTH,
            height=12,
            progress_color=theme.accent,
            fg_color=theme.progress_trough,
            corner_radius=6,
        )
        self._campaign_bar.grid(column=0, row=4, columnspan=2, sticky="ew", pady=(4, 8))
        self._campaign_bar.set(0)
        ctk.CTkFrame(frame, height=1, fg_color=theme.border).grid(
            row=5, columnspan=2, sticky="ew", pady=(4, 8)
        )
        ctk.CTkLabel(frame, text=_("gui", "progress", "drop"), text_color=theme.muted).grid(
            column=0, row=6, columnspan=2, sticky="w"
        )
        ctk.CTkLabel(frame, textvariable=self._vars["drop"]["rewards"]).grid(
            column=0, row=7, columnspan=2, sticky="w"
        )
        ctk.CTkLabel(frame, text=_("gui", "progress", "drop_progress")).grid(
            column=0, row=8, rowspan=2, sticky="w"
        )
        ctk.CTkLabel(frame, textvariable=self._vars["drop"]["percentage"]).grid(
            column=1, row=8, sticky="e"
        )
        ctk.CTkLabel(frame, textvariable=self._vars["drop"]["remaining"]).grid(
            column=1, row=9, sticky="e"
        )
        self._drop_bar = ctk.CTkProgressBar(
            frame,
            width=self.BAR_LENGTH,
            height=12,
            progress_color=theme.accent,
            fg_color=theme.progress_trough,
            corner_radius=6,
        )
        self._drop_bar.grid(column=0, row=10, columnspan=2, sticky="ew", pady=(4, 0))
        self._drop_bar.set(0)
        self._drop: TimedDrop | None = None
        self._seconds: int = 0
        self._timer_task: asyncio.Task[None] | None = None
        self.display(None)

    def _divmod(self, minutes: int) -> tuple[int, int]:
        if self._seconds < 60 and minutes > 0:
            minutes -= 1
        hours, minutes = divmod(minutes, 60)
        return (hours, minutes)

    def _update_time(self, seconds: int | None = None):
        if seconds is not None:
            self._seconds = seconds
        drop = self._drop
        if drop is not None:
            drop_minutes = drop.remaining_minutes
            campaign_minutes = drop.campaign.remaining_minutes
        else:
            drop_minutes = 0
            campaign_minutes = 0
        drop_vars: _DropVars = self._vars["drop"]
        campaign_vars: _CampaignVars = self._vars["campaign"]
        dseconds = self._seconds % 60
        hours, minutes = self._divmod(drop_minutes)
        drop_vars["remaining"].set(
            _("gui", "progress", "remaining").format(time=f"{hours:>2}:{minutes:02}:{dseconds:02}")
        )
        hours, minutes = self._divmod(campaign_minutes)
        campaign_vars["remaining"].set(
            _("gui", "progress", "remaining").format(time=f"{hours:>2}:{minutes:02}:{dseconds:02}")
        )

    async def _timer_loop(self):
        self._update_time(60)
        while self._seconds > 0:
            await asyncio.sleep(1)
            self._seconds -= 1
            self._update_time()
        self._timer_task = None

    def start_timer(self):
        if self._timer_task is None:
            if self._drop is None or self._drop.remaining_minutes <= 0:
                # if we're starting the timer at 0 drop minutes,
                # all we need is a single instant time update setting seconds to 60,
                # to avoid substracting a minute from campaign minutes
                self._update_time(60)
            else:
                self._timer_task = asyncio.create_task(self._timer_loop())

    def stop_timer(self):
        if self._timer_task is not None:
            self._timer_task.cancel()
            self._timer_task = None

    def minute_almost_done(self) -> bool:
        # already or almost done
        return self._timer_task is None or self._seconds <= self.ALMOST_DONE_SECONDS

    def display(self, drop: TimedDrop | None, *, countdown: bool = True, subone: bool = False):
        self._drop = drop
        vars_drop = self._vars["drop"]
        vars_campaign = self._vars["campaign"]
        self.stop_timer()
        if drop is None:
            # clear the drop display
            vars_drop["rewards"].set("...")
            vars_drop["progress"].set(0.0)
            vars_drop["percentage"].set("-%")
            vars_campaign["name"].set("...")
            vars_campaign["game"].set("...")
            vars_campaign["progress"].set(0.0)
            vars_campaign["percentage"].set("-%")
            self._campaign_bar.set(0)
            self._drop_bar.set(0)
            self._update_time(0)
            return
        vars_drop["rewards"].set(drop.rewards_text())
        vars_drop["progress"].set(drop.progress)
        vars_drop["percentage"].set(f"{drop.progress:6.1%}")
        self._drop_bar.set(max(0.0, min(1.0, float(drop.progress))))
        campaign = drop.campaign
        vars_campaign["name"].set(campaign.name)
        vars_campaign["game"].set(campaign.game.name)
        vars_campaign["progress"].set(campaign.progress)
        vars_campaign["percentage"].set(
            f"{campaign.progress:6.1%} ({campaign.claimed_drops}/{campaign.total_drops})"
        )
        self._campaign_bar.set(max(0.0, min(1.0, float(campaign.progress))))
        if countdown:
            # restart our seconds update timer
            self.start_timer()
        elif subone:
            # display the current remaining time at 0 seconds (after substracting the minute)
            # this is because the watch loop will substract this minute
            # right after the first watch payload returns with a time update
            self._update_time(0)
        else:
            # display full time with no substracting
            self._update_time(60)

class ConsoleOutput:
    def __init__(self, manager: GUIManager, master: tk.Misc):
        theme = get_theme()
        section = SectionFrame(master, _("gui", "output"))
        section.grid(column=0, row=3, columnspan=3, sticky="nsew", padx=4, pady=4)
        master.rowconfigure(3, weight=1)
        frame = section.body
        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)
        self._xscroll = FlatScrollbar(frame, orient="horizontal")
        self._yscroll = FlatScrollbar(frame, orient="vertical")
        self._corner = tk.Frame(frame, bg=theme.bg, highlightthickness=0, width=12, height=12)
        self._text = tk.Text(
            frame,
            width=52,
            height=10,
            wrap="none",
            state="disabled",
            exportselection=False,
            relief="flat",
            borderwidth=0,
            font=("Consolas", 10),
            xscrollcommand=self._xscroll.set,
            yscrollcommand=self._yscroll.set,
            bg=theme.field_bg,
            fg=theme.fg,
            insertbackground=theme.fg,
            selectbackground=theme.sel_bg,
            selectforeground=theme.sel_fg,
        )
        self._xscroll.config(command=self._text.xview)
        self._yscroll.config(command=self._text.yview)
        self._text.grid(column=0, row=0, sticky="nsew")
        self._xscroll.grid(column=0, row=1, sticky="ew")
        self._yscroll.grid(column=1, row=0, sticky="ns")
        self._corner.grid(column=1, row=1, sticky="nsew")

    def print(self, message: str):
        stamp = datetime.now().strftime("%X")
        if '\n' in message:
            message = message.replace('\n', f"\n{stamp}: ")
        self._text.config(state="normal")
        self._text.insert("end", f"{stamp}: {message}\n")
        self._text.see("end")  # scroll to the newly added line
        self._text.config(state="disabled")

    def configure_theme(self, *, bg: str, fg: str, sel_bg: str, sel_fg: str, trough: str | None = None, surface: str | None = None):
        theme = get_theme()
        trough = trough or theme.scroll_trough
        thumb = surface or theme.scroll_thumb
        self._text.config(
            bg=bg,
            fg=fg,
            insertbackground=fg,
            selectbackground=sel_bg,
            selectforeground=sel_fg,
        )
        self._corner.configure(bg=trough)
        self._xscroll.configure_theme(
            trough=trough, thumb=thumb, active=theme.scroll_active
        )
        self._yscroll.configure_theme(
            trough=trough, thumb=thumb, active=theme.scroll_active
        )

class _Buttons(TypedDict):
    frame: Any
    switch: Any

class ChannelList:
    def __init__(self, manager: GUIManager, master: tk.Misc):
        self._manager = manager
        theme = get_theme()
        section = SectionFrame(master, _("gui", "channels", "name"))
        section.grid(column=2, row=1, rowspan=2, sticky="nsew", padx=4, pady=4)
        master.columnconfigure(2, weight=1)
        frame = section.body
        frame.rowconfigure(1, weight=1)
        frame.columnconfigure(0, weight=1)
        buttons_frame = ctk.CTkFrame(frame, fg_color="transparent")
        switch_btn = accent_button(
            buttons_frame,
            text=_("gui", "channels", "switch"),
            state="disabled",
            command=manager._twitch.state_change(State.CHANNEL_SWITCH),
            width=140,
        )
        self._buttons: _Buttons = {
            "frame": buttons_frame,  # type: ignore[typeddict-item]
            "switch": switch_btn,  # type: ignore[typeddict-item]
        }
        buttons_frame.grid(column=0, row=0, columnspan=2, pady=(0, 6))
        switch_btn.grid(column=0, row=0)
        self._scroll = FlatScrollbar(frame, orient="vertical")
        self._table = table = ttk.Treeview(
            frame,
            yscrollcommand=self._scroll.set,
            style="Channels.Treeview",
        )
        self._scroll.config(command=table.yview)
        table.grid(column=0, row=1, sticky="nsew")
        self._scroll.grid(column=1, row=1, sticky="ns")
        self._font = Font(frame, manager._style.lookup("Treeview", "font"))
        self._const_width: set[str] = set()
        table.tag_configure("watching", background=theme.accent_hover, foreground="#ffffff")
        table.bind("<Button-1>", self._disable_column_resize)
        table.bind("<<TreeviewSelect>>", self._selected)
        self._add_column("#0", '', width=0)
        self._add_column(
            "channel", _("gui", "channels", "headings", "channel"), width=100, anchor='w'
        )
        self._add_column(
            "status",
            _("gui", "channels", "headings", "status"),
            width_template=[
                _("gui", "channels", "online"),
                _("gui", "channels", "pending"),
                _("gui", "channels", "offline"),
            ],
        )
        self._add_column("game", _("gui", "channels", "headings", "game"), width=50)
        self._add_column("drops", "🎁", width_template="✔")
        self._add_column(
            "viewers", _("gui", "channels", "headings", "viewers"), width_template="1234567"
        )
        self._add_column("acl_base", "📋", width_template="✔")
        self._channel_map: dict[str, Channel] = {}

    def _add_column(
        self,
        cid: str,
        name: str,
        *,
        anchor: tk._Anchor = "center",
        width: int | None = None,
        width_template: str | list[str] | None = None,
    ):
        table = self._table
        # NOTE: we don't do this for the icon column
        if cid != "#0":
            # we need to save the column settings and headings before modifying the columns...
            columns: tuple[str, ...] = table.cget("columns") or ()
            column_settings: dict[str, tuple[str, tk._Anchor, int, int]] = {}
            for s_cid in columns:
                s_column = table.column(s_cid)
                assert s_column is not None
                s_heading = table.heading(s_cid)
                assert s_heading is not None
                column_settings[s_cid] = (
                    s_heading["text"], s_heading["anchor"], s_column["width"], s_column["minwidth"]
                )
            # ..., then add the column
            table.config(columns=columns + (cid,))
            # ..., and then restore column settings and headings afterwards
            for s_cid, (s_name, s_anchor, s_width, s_minwidth) in column_settings.items():
                table.heading(s_cid, text=s_name, anchor=s_anchor)
                table.column(s_cid, minwidth=s_minwidth, width=s_width, stretch=False)
        # set heading and column settings for the new column
        if width_template is not None:
            if isinstance(width_template, str):
                width = self._measure(width_template)
            else:
                width = max((self._measure(template) for template in width_template), default=20)
            self._const_width.add(cid)
        assert width is not None
        table.heading(cid, text=name, anchor=anchor)
        table.column(cid, minwidth=width, width=width, stretch=False)

    def _disable_column_resize(self, event):
        if self._table.identify_region(event.x, event.y) == "separator":
            return "break"

    def _selected(self, event):
        selection = self._table.selection()
        if selection:
            self._buttons["switch"].configure(state="normal")
        else:
            self._buttons["switch"].configure(state="disabled")

    def _measure(self, text: str) -> int:
        # we need this because columns have 9-10 pixels of padding that cuts text off
        return self._font.measure(text) + 10

    def _redraw(self):
        # this forces a redraw that recalculates widget width
        self._table.event_generate("<<ThemeChanged>>")

    def _adjust_width(self, column: str, value: str):
        # causes the column to expand if the value's width is greater than the current width
        if column in self._const_width:
            return
        value_width = self._measure(value)
        curr_width = self._table.column(column, "width")
        if value_width > curr_width:
            self._table.column(column, width=value_width)
            self._redraw()

    def shrink(self):
        # causes the columns to shrink back after long values have been removed from it
        columns = self._table.cget("columns")
        iids = self._table.get_children()
        for column in columns:
            if column in self._const_width:
                continue
            if iids:
                # table has at least one item
                width = max(self._measure(self._table.set(i, column)) for i in iids)
                self._table.column(column, width=width)
            else:
                # no items - use minwidth
                minwidth = self._table.column(column, "minwidth")
                self._table.column(column, width=minwidth)
        self._redraw()

    def _set(self, iid: str, column: str, value: str):
        self._table.set(iid, column, value)
        self._adjust_width(column, value)

    def _insert(self, iid: str, values: dict[str, str]):
        to_insert: list[str] = []
        for cid in self._table.cget("columns"):
            value = values[cid]
            to_insert.append(value)
            self._adjust_width(cid, value)
        self._table.insert(parent='', index="end", iid=iid, values=to_insert)

    def clear_watching(self):
        for iid in self._table.tag_has("watching"):
            self._table.item(iid, tags='')

    def set_watching(self, channel: Channel):
        self.clear_watching()
        iid = channel.iid
        self._table.item(iid, tags="watching")
        self._table.see(iid)

    def get_selection(self) -> Channel | None:
        if not self._channel_map:
            return None
        selection = self._table.selection()
        if not selection:
            return None
        return self._channel_map[selection[0]]

    def clear_selection(self):
        self._table.selection_set('')

    def configure_theme(self, theme: Theme | None = None) -> None:
        theme = theme or get_theme()
        self._scroll.configure_theme(
            trough=theme.scroll_trough,
            thumb=theme.scroll_thumb,
            active=theme.scroll_active,
        )
        self._table.tag_configure(
            "watching", background=theme.accent_hover, foreground="#ffffff"
        )

    def clear(self):
        iids = self._table.get_children()
        self._table.delete(*iids)
        self._channel_map.clear()
        self.shrink()

    def display(self, channel: Channel, *, add: bool = False):
        iid = channel.iid
        if not add and iid not in self._channel_map:
            # the channel isn't on the list and we're not supposed to add it
            return
        # ACL-based
        acl_based = "✔" if channel.acl_based else "❌"
        # status
        if channel.online:
            status = _("gui", "channels", "online")
        elif channel.pending_online:
            status = _("gui", "channels", "pending")
        else:
            status = _("gui", "channels", "offline")
        # game
        game = str(channel.game or '')
        # drops
        drops = "✔" if channel.drops_enabled else "❌"
        # viewers
        viewers = ''
        if channel.viewers is not None:
            viewers = str(channel.viewers)
        if iid in self._channel_map:
            self._set(iid, "game", game)
            self._set(iid, "drops", drops)
            self._set(iid, "status", status)
            self._set(iid, "viewers", viewers)
            self._set(iid, "acl_base", acl_based)
        elif add:
            self._channel_map[iid] = channel
            self._insert(
                iid,
                {
                    "game": game,
                    "drops": drops,
                    "status": status,
                    "viewers": viewers,
                    "acl_base": acl_based,
                    "channel": channel.name,
                },
            )

    def remove(self, channel: Channel):
        iid = channel.iid
        del self._channel_map[iid]
        self._table.delete(iid)

class TrayIcon:
    TITLE = "Twitch Drops Miner"

    def __init__(self, manager: GUIManager, master: tk.Misc):
        self._manager = manager
        self.icon: pystray.Icon | None = None  # type: ignore[unused-ignore]
        self._icon_images: dict[str, Image_module.Image] = {
            "pickaxe": Image_module.open(resource_path("icons/pickaxe.ico")),
            "active": Image_module.open(resource_path("icons/active.ico")),
            "idle": Image_module.open(resource_path("icons/idle.ico")),
            "error": Image_module.open(resource_path("icons/error.ico")),
            "maint": Image_module.open(resource_path("icons/maint.ico")),
        }
        self._icon_state: str = "pickaxe"
        self._status: str = ""
        self._button = secondary_button(
            master, command=self.minimize, text=_("gui", "tray", "minimize"), width=120
        )

        # Hides Tray button for macOS
        if sys.platform != "darwin":
            self._button.grid(column=0, row=0, sticky="ne", padx=4, pady=4)

    def __del__(self) -> None:
        self.stop()
        for icon_image in self._icon_images.values():
            icon_image.close()

    def _shorten(self, text: str, by_len: int, min_len: int) -> str:
        if (text_len := len(text)) <= min_len + 3 or by_len <= 0:
            # cannot shorten
            return text
        return text[:-min(by_len + 3, text_len - min_len)] + "..."

    def set_status(self, text: str) -> None:
        self._status = text
        drop: TimedDrop | None = None
        progress = getattr(self._manager, "progress", None)
        if progress is not None:
            drop = progress._drop
        self.update_title(drop)

    def get_title(self, drop: TimedDrop | None) -> str:
        status = self._status.strip()
        if drop is None:
            return f"{self.TITLE}\n{status}" if status else self.TITLE
        campaign = drop.campaign
        title_parts: list[str] = [f"{self.TITLE}\n"]
        status_idx: int | None = None
        if status:
            status_idx = len(title_parts)
            title_parts.append(f"{status}\n")
        game_idx = len(title_parts)
        title_parts.append(f"{campaign.game.name}\n")
        reward_idx = len(title_parts)
        title_parts.append(drop.rewards_text())
        title_parts.append(
            f" {drop.progress:.1%} ({campaign.claimed_drops}/{campaign.total_drops})"
        )
        min_len: int = 30
        max_len: int = 127
        missing_len = len(''.join(title_parts)) - max_len
        if missing_len > 0:
            # try shortening the reward text
            title_parts[reward_idx] = self._shorten(
                title_parts[reward_idx], missing_len, min_len
            )
            missing_len = len(''.join(title_parts)) - max_len
        if missing_len > 0:
            # try shortening the game name
            title_parts[game_idx] = self._shorten(
                title_parts[game_idx], missing_len, min_len
            )
            missing_len = len(''.join(title_parts)) - max_len
        if missing_len > 0 and status_idx is not None:
            title_parts[status_idx] = (
                self._shorten(title_parts[status_idx].rstrip("\n"), missing_len, 12)
                + "\n"
            )
            missing_len = len(''.join(title_parts)) - max_len
        if missing_len > 0:
            raise MinerException(f"Title couldn't be shortened: {''.join(title_parts)}")
        return ''.join(title_parts)

    def _start(self):
        loop = asyncio.get_running_loop()
        drop = self._manager.progress._drop

        # we need this because tray icon lives in a separate thread
        def bridge(func):
            return lambda: loop.call_soon_threadsafe(func)

        menu = pystray.Menu(
            pystray.MenuItem(_("gui", "tray", "show"), bridge(self.restore), default=True),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(_("gui", "tray", "quit"), bridge(self.quit)),
        )
        self.icon = pystray.Icon(
            "twitch_miner", self._icon_images[self._icon_state], self.get_title(drop), menu
        )
        # self.icon.run_detached()
        loop.run_in_executor(None, self.icon.run)

    def stop(self):
        if self.icon is not None:
            self.icon.stop()
            self.icon = None

    def quit(self):
        self._manager.close()

    def minimize(self):
        if sys.platform == "darwin":
            return
        if self.icon is None:
            self._start()
        else:
            self.icon.visible = True
        self._manager._root.withdraw()

    def restore(self):
        if self.icon is not None:
            # self.stop()
            self.icon.visible = False
        self._manager._root.deiconify()

    def notify(
        self, message: str, title: str | None = None, duration: float = 10
    ) -> asyncio.Task[None] | None:
        # do nothing if the user disabled notifications
        if not self._manager._twitch.settings.tray_notifications:
            return None
        if self.icon is not None:
            icon = self.icon  # nonlocal scope bind

            async def notifier():
                icon.notify(message, title)
                await asyncio.sleep(duration)
                icon.remove_notification()

            return asyncio.create_task(notifier())
        return None

    def update_title(self, drop: TimedDrop | None):
        if self.icon is not None:
            self.icon.title = self.get_title(drop)

    def change_icon(self, state: str):
        if state not in self._icon_images:
            raise ValueError("Invalid icon state")
        self._icon_state = state
        if self.icon is not None:
            self.icon.icon = self._icon_images[state]

class Notebook:
    """CTkTabview wrapper keeping the previous Notebook API."""

    def __init__(self, manager: GUIManager, master: tk.Misc):
        self._manager = manager
        theme = get_theme()
        self._tabs: list[str] = []
        self._view_callbacks: list[abc.Callable[[Any], Any]] = []
        self._nb = ctk.CTkTabview(
            master,
            fg_color=theme.bg,
            segmented_button_fg_color=theme.surface,
            segmented_button_selected_color=theme.accent,
            segmented_button_selected_hover_color=theme.accent_hover,
            segmented_button_unselected_color=theme.surface_alt,
            segmented_button_unselected_hover_color=theme.border,
            text_color=theme.fg,
            corner_radius=10,
        )
        self._nb.grid(column=0, row=0, sticky="nsew", padx=4, pady=(28, 4))
        master.rowconfigure(0, weight=1)
        master.columnconfigure(0, weight=1)
        self._nb.configure(command=self._on_tab_changed)
        # CTk default: grid_forget(old) THEN grid(new) → empty canvas flash (black/white).
        # Show the new tab first, then hide the old one.
        self._patch_smooth_tab_switch(self._nb)

    @staticmethod
    def _patch_smooth_tab_switch(
        nb: ctk.CTkTabview, *, keep_mapped: bool = True
    ) -> None:
        def _smooth_callback(selected_name: str) -> None:
            # Show the new tab first, then optionally forget the old one.
            # keep_mapped=True (main notebook): no empty-canvas gap.
            # keep_mapped=False (Inventory nested views): avoid stacked pools
            # from different themes showing through each other.
            old = nb._current_name
            nb._current_name = selected_name
            nb._set_grid_current_tab()
            with suppress(Exception):
                nb._tab_dict[selected_name].tkraise()
            if (
                not keep_mapped
                and old
                and old != selected_name
                and old in nb._tab_dict
            ):
                nb._tab_dict[old].grid_forget()
            if nb._command is not None:
                nb._command()

        nb._segmented_button_callback = _smooth_callback  # type: ignore[method-assign]
        try:
            nb._segmented_button.configure(command=_smooth_callback)
        except Exception:
            pass

    def add_tab(self, widget: tk.Misc | None = None, *, name: str, **kwargs: Any) -> ctk.CTkFrame:
        """Create a tab and return its content frame. Optional widget is ignored (compat)."""
        kwargs.pop("text", None)
        kwargs.pop("sticky", None)
        tab = self._nb.add(name)
        self._tabs.append(name)
        content = ctk.CTkFrame(tab, fg_color="transparent")
        content.grid(column=0, row=0, sticky="nsew", padx=4, pady=4)
        tab.grid_columnconfigure(0, weight=1)
        tab.grid_rowconfigure(0, weight=1)
        content.grid_columnconfigure(0, weight=1)
        content.grid_rowconfigure(0, weight=1)
        if widget is not None:
            # Legacy call style: re-grid the provided frame into the tab content
            widget.grid(in_=content, column=0, row=0, sticky="nsew")
            return content
        return content

    def current_tab(self) -> int:
        current = self._nb.get()
        try:
            return self._tabs.index(current)
        except ValueError:
            return 0

    def add_view_event(self, callback: abc.Callable[[Any], Any]):
        self._view_callbacks.append(callback)

    def configure_theme(self, theme: Theme) -> None:
        self._nb.configure(
            fg_color=theme.bg,
            segmented_button_fg_color=theme.surface,
            segmented_button_selected_color=theme.accent,
            segmented_button_selected_hover_color=theme.accent_hover,
            segmented_button_unselected_color=theme.surface_alt,
            segmented_button_unselected_hover_color=theme.border,
            text_color=theme.fg,
        )

    def _on_tab_changed(self):
        self._manager._root.focus_set()
        for callback in self._view_callbacks:
            try:
                callback(None)
            except Exception:
                logger.exception("Notebook view callback failed")

class CampaignDisplay(TypedDict):
    frame: Any
    status: Any
    name_label: Any
    ends_label: Any
    link_label: Any
    channels_label: Any
    actions: Any
    divider: Any
    drops_host: Any
    drops_canvas: Any
    drops_scroll: Any
    drops_row: Any
    drop_frames: list[Any]
    benefit_labels: list[Any]
    progress_labels: dict[str, Any]
    priority_btn: Any
    exclude_btn: Any
    campaign_image_label: Any
    benefit_image_labels: list[Any]
    campaign: Any

class InventoryOverview:
    # Nested view modes inside the Inventory page
    VIEW_FILTERED = 0
    VIEW_ALL_ACTIVE = 1
    SORT_ENDING = "ending"
    SORT_GAME = "game"
    SORT_NAME = "name"
    CAMPAIGN_IMAGE_SIZE = (108, 144)
    BENEFIT_IMAGE_SIZE = (80, 80)

    def __init__(self, manager: GUIManager, master: tk.Misc):
        self._manager = manager
        self._cache: ImageCache = manager._cache
        self._settings: Settings = manager._twitch.settings
        theme = get_theme()
        self._filters = {
            "not_linked": IntVar(
                master, self._settings.priority_mode is PriorityMode.PRIORITY_ONLY
            ),
            "upcoming": IntVar(master, 1),
            "expired": IntVar(master, 0),
            "excluded": IntVar(master, 0),
            "finished": IntVar(master, 0),
        }
        self._sort_mode = StringVar(master, self.SORT_ENDING)
        self._sort_labels = {
            self.SORT_ENDING: _("gui", "inventory", "sort", "ending"),
            self.SORT_GAME: _("gui", "inventory", "sort", "game"),
            self.SORT_NAME: _("gui", "inventory", "sort", "name"),
        }
        self._sort_label_to_mode = {label: mode for mode, label in self._sort_labels.items()}
        self._view_names = [
            _("gui", "inventory", "tabs", "filtered"),
            _("gui", "inventory", "tabs", "all_active"),
        ]
        manager.tabs.add_view_event(self._on_tab_switched)
        # After a live theme flip, card chrome is deferred until Inventory is shown —
        # theming ~14 image-heavy cards synchronously freezes Main↔Inventory switches.
        self._theme_dirty = False
        # Data changed while Inventory was hidden — refresh on next show without destroying cards.
        self._pending_refresh = False
        self._tab_job: str | None = None

        self._views = ctk.CTkTabview(
            master,
            fg_color=theme.bg,
            segmented_button_fg_color=theme.surface,
            segmented_button_selected_color=theme.accent,
            segmented_button_selected_hover_color=theme.accent_hover,
            segmented_button_unselected_color=theme.surface_alt,
            segmented_button_unselected_hover_color=theme.border,
            text_color=theme.fg,
            corner_radius=10,
            command=self._on_view_switched,
        )
        self._views.grid(column=0, row=0, columnspan=2, sticky="nsew")
        master.rowconfigure(0, weight=1)
        master.columnconfigure(0, weight=1)
        # Nested filtered/active views must forget the hidden tab — keeping both
        # mapped let a Light card pool show through the Dark page.
        Notebook._patch_smooth_tab_switch(self._views, keep_mapped=False)

        self._filtered_tab = self._views.add(self._view_names[0])
        self._active_tab = self._views.add(self._view_names[1])
        # Force dark CTk tab chrome — default tab fill can stay light under CTk.
        self._filtered_tab.configure(fg_color=theme.bg, bg_color=theme.bg)
        self._active_tab.configure(fg_color=theme.bg, bg_color=theme.bg)
        # Isolate scroll content from CTk drawing surface — nested CTk parents
        # redraw on every child move and make scrolling look like a reload.
        self._filtered_page = tk.Frame(self._filtered_tab, bg=theme.bg, highlightthickness=0)
        self._active_page = tk.Frame(self._active_tab, bg=theme.bg, highlightthickness=0)
        self._filtered_page.grid(column=0, row=0, sticky="nsew")
        self._active_page.grid(column=0, row=0, sticky="nsew")
        self._filtered_tab.grid_columnconfigure(0, weight=1)
        self._filtered_tab.grid_rowconfigure(0, weight=1)
        self._active_tab.grid_columnconfigure(0, weight=1)
        self._active_tab.grid_rowconfigure(0, weight=1)

        # CTk widgets on a plain tk.Frame need an explicit bg_color or they paint
        # white "slabs" (CTk can't inherit bg from tk parents).
        self._filter_section = SectionFrame(
            self._filtered_page, _("gui", "inventory", "filter", "name")
        )
        self._filter_section.grid(column=0, row=0, columnspan=2, sticky="ew", pady=(0, 6))
        self._filter_section.configure(bg_color=theme.bg)
        self._filter_frame = self._filter_section.body
        # Plain tk row: CTkCheckBox/CTkLabel always paint their own bg rects
        # ("slabs") that fight the card fill. tk widgets share one surface bg.
        self._filter_row = tk.Frame(
            self._filter_frame, bg=theme.surface, highlightthickness=0
        )
        self._filter_row.grid(column=0, row=0, sticky="ew")
        self._filter_show_label = tk.Label(
            self._filter_row,
            text=_("gui", "inventory", "filter", "show"),
            bg=theme.surface,
            fg=theme.fg,
        )
        self._filter_show_label.grid(column=0, row=0, padx=(0, 8))
        icolumn = 0
        self._filter_checks: list[tk.Checkbutton] = []
        for key, label_key in (
            ("not_linked", "not_linked"),
            ("upcoming", "upcoming"),
            ("expired", "expired"),
            ("excluded", "excluded"),
            ("finished", "finished"),
        ):
            box = tk.Checkbutton(
                self._filter_row,
                text=_("gui", "inventory", "filter", label_key),
                variable=self._filters[key],
                command=self.refresh,
                bg=theme.surface,
                fg=theme.fg,
                activebackground=theme.surface,
                activeforeground=theme.fg,
                selectcolor=theme.surface_alt,
                highlightthickness=0,
                bd=0,
                padx=2,
            )
            box.grid(column=(icolumn := icolumn + 1), row=0, padx=4)
            self._filter_checks.append(box)
        self._filter_refresh_btn = accent_button(
            self._filter_row,
            text=_("gui", "inventory", "filter", "refresh"),
            command=self.refresh,
            width=90,
            bg_color=theme.surface,
        )
        self._filter_refresh_btn.grid(column=(icolumn := icolumn + 1), row=0, padx=(8, 0))

        self._active_summary = ctk.CTkLabel(
            self._active_page,
            text=_("gui", "inventory", "active_summary").format(count=0),
            anchor="w",
            bg_color=theme.bg,
            text_color=theme.fg,
        )
        self._active_summary.grid(column=0, row=0, columnspan=2, sticky="ew", pady=(0, 6))

        self._active_tools = ctk.CTkFrame(
            self._active_page, fg_color="transparent", bg_color=theme.bg
        )
        self._active_tools.grid(column=0, row=1, columnspan=2, sticky="ew", pady=(0, 6))
        self._search_entry = ctk.CTkEntry(
            self._active_tools,
            placeholder_text=_("gui", "inventory", "search_placeholder"),
            width=260,
            fg_color=theme.field_bg,
            border_color=theme.border,
            bg_color=theme.bg,
            text_color=theme.fg,
        )
        self._search_entry.pack(side="left", padx=(0, 12))
        self._search_entry.bind("<KeyRelease>", lambda e: self.refresh(), add="+")
        self._sort_combo = ctk.CTkComboBox(
            self._active_tools,
            values=list(self._sort_labels.values()),
            width=200,
            command=lambda _: self._on_sort_selected(),
            fg_color=theme.surface_alt,
            border_color=theme.border,
            button_color=theme.accent,
            button_hover_color=theme.accent_hover,
            bg_color=theme.bg,
            text_color=theme.fg,
        )
        self._sort_combo.set(self._sort_labels[self.SORT_ENDING])
        self._sort_combo.pack(side="left")

        self._filtered_scroll = tk.Frame(self._filtered_page, bg=theme.bg, highlightthickness=0)
        self._filtered_scroll.grid(column=0, row=1, sticky="nsew")
        self._active_scroll = tk.Frame(self._active_page, bg=theme.bg, highlightthickness=0)
        self._active_scroll.grid(column=0, row=2, sticky="nsew")
        self._filtered_page.rowconfigure(1, weight=1)
        self._filtered_page.columnconfigure(0, weight=1)
        self._active_page.rowconfigure(2, weight=1)
        self._active_page.columnconfigure(0, weight=1)
        self._filtered_scroll.rowconfigure(0, weight=1)
        self._filtered_scroll.columnconfigure(0, weight=1)
        self._active_scroll.rowconfigure(0, weight=1)
        self._active_scroll.columnconfigure(0, weight=1)

        list_kwargs = dict(
            on_mousewheel=self._on_mousewheel,
            create_card=self._create_card,
            bind_card=self._bind_card,
            destroy_card=self._destroy_card,
            theme_card=self._theme_card,
        )
        self._filtered_list = VirtualCampaignList(self._filtered_scroll, **list_kwargs)
        self._active_list = VirtualCampaignList(self._active_scroll, **list_kwargs)
        # Compat aliases used by mousewheel / theme helpers
        self._canvas = self._filtered_list.canvas
        self._active_canvas = self._active_list.canvas
        self._main_frame = self._filtered_list.inner
        self._active_main_frame = self._active_list.inner

        self._campaigns: list[DropsCampaign] = []
        self._images_by_campaign: dict[
            DropsCampaign, tuple[PhotoImage, list[PhotoImage]]
        ] = {}
        self._drop_labels: dict[str, ttk.Label] = {}
        self._active_initialized = False

    def _paint_ctk_page_bg(self, root: tk.Misc, *, page_bg: str, surface: str) -> None:
        """Force CTk bg_color when widgets sit on a plain tk.Frame (avoids white slabs)."""
        stack: list[tk.Misc] = [root]
        while stack:
            widget = stack.pop()
            try:
                children = list(widget.winfo_children())
            except Exception:
                continue
            for child in children:
                stack.append(child)
                try:
                    if isinstance(child, SectionFrame):
                        child.apply_theme(get_theme(), page_bg=page_bg)
                    elif isinstance(child, ctk.CTkCheckBox):
                        child.configure(
                            bg_color=surface,
                            border_color=get_theme().muted,
                            border_width=2,
                            text_color=get_theme().fg,
                            fg_color=get_theme().accent,
                            hover_color=get_theme().accent_hover,
                        )
                    elif isinstance(child, ctk.CTkLabel):
                        # Inside a SectionFrame card → surface; otherwise page bg.
                        parent = child.master
                        on_card = isinstance(parent, SectionFrame) or (
                            isinstance(getattr(parent, "master", None), SectionFrame)
                        )
                        child.configure(bg_color=surface if on_card else page_bg)
                    elif isinstance(child, ctk.CTkEntry):
                        child.configure(bg_color=page_bg)
                    elif isinstance(child, ctk.CTkComboBox):
                        child.configure(bg_color=page_bg)
                    elif isinstance(child, ctk.CTkButton):
                        parent = child.master
                        on_card = isinstance(parent, SectionFrame) or (
                            isinstance(getattr(parent, "master", None), SectionFrame)
                        )
                        child.configure(bg_color=surface if on_card else page_bg)
                    elif isinstance(child, ctk.CTkFrame):
                        # Never paint card interiors with page_bg — that was the
                        # filter "slab" strip (body page_bg vs children surface).
                        parent = child.master
                        if isinstance(parent, SectionFrame):
                            child.configure(fg_color=surface, bg_color=surface)
                        else:
                            child.configure(bg_color=page_bg)
                except Exception:
                    pass

    def hibernate_cards(self, *, clear_heights: bool = False) -> None:
        """Drop pooled campaign cards so tab swaps stay light after theme flips."""
        self._filtered_list.hibernate(clear_heights=clear_heights)
        self._active_list.hibernate(clear_heights=clear_heights)

    def configure_theme(self, theme: Theme | None = None, *, bg: str | None = None) -> None:
        theme = theme or get_theme()
        page_bg = bg or theme.bg
        on_inventory = self._manager.tabs.current_tab() == 1
        # Keep existing cards — destroy/recreate during appearance flip was the flash
        # (logs: force create while mapped=false after every theme toggle).
        self._views.configure(
            fg_color=theme.bg,
            segmented_button_fg_color=theme.surface,
            segmented_button_selected_color=theme.accent,
            segmented_button_selected_hover_color=theme.accent_hover,
            segmented_button_unselected_color=theme.surface_alt,
            segmented_button_unselected_hover_color=theme.border,
            text_color=theme.fg,
        )
        self._filtered_tab.configure(fg_color=page_bg, bg_color=page_bg)
        self._active_tab.configure(fg_color=page_bg, bg_color=page_bg)
        self._filtered_page.configure(bg=page_bg)
        self._active_page.configure(bg=page_bg)
        self._filtered_scroll.configure(bg=page_bg)
        self._active_scroll.configure(bg=page_bg)
        self._filter_section.apply_theme(theme, page_bg=page_bg)
        if getattr(self, "_filter_row", None) is not None:
            self._filter_row.configure(bg=theme.surface)
        if getattr(self, "_filter_show_label", None) is not None:
            self._filter_show_label.configure(bg=theme.surface, fg=theme.fg)
        for box in getattr(self, "_filter_checks", []):
            box.configure(
                bg=theme.surface,
                fg=theme.fg,
                activebackground=theme.surface,
                activeforeground=theme.fg,
                selectcolor=theme.surface_alt,
            )
        if getattr(self, "_filter_refresh_btn", None) is not None:
            self._filter_refresh_btn.configure(
                fg_color=theme.accent,
                hover_color=theme.accent_hover,
                bg_color=theme.surface,
                text_color="#ffffff",
            )
        self._active_summary.configure(
            bg_color=page_bg, text_color=theme.fg
        )
        self._active_tools.configure(bg_color=page_bg)
        self._search_entry.configure(
            fg_color=theme.field_bg,
            border_color=theme.border,
            text_color=theme.fg,
            placeholder_text_color=theme.muted,
            bg_color=page_bg,
        )
        self._sort_combo.configure(
            fg_color=theme.surface_alt,
            border_color=theme.border,
            button_color=theme.accent,
            button_hover_color=theme.accent_hover,
            text_color=theme.fg,
            bg_color=page_bg,
        )
        self._paint_ctk_page_bg(
            self._filtered_page, page_bg=page_bg, surface=theme.surface
        )
        self._paint_ctk_page_bg(
            self._active_page, page_bg=page_bg, surface=theme.surface
        )
        # Rebuild card pools on theme flip so ttk leftovers cannot linger as light slabs.
        self._filtered_list.configure_theme(
            bg=page_bg,
            surface=theme.surface,
            border=theme.border,
            cards=True,
            rebuild=True,
        )
        self._active_list.configure_theme(
            bg=page_bg,
            surface=theme.surface,
            border=theme.border,
            cards=True,
            rebuild=True,
        )
        self._theme_dirty = False

    def _cancel_tab_job(self) -> None:
        if self._tab_job is not None:
            with suppress(Exception):
                self._manager._root.after_cancel(self._tab_job)
            self._tab_job = None

    def _on_inventory_shown(self) -> None:
        self._tab_job = None
        # Campaigns often finish loading on Main via add_campaign without refresh;
        # detect empty pool so first Inventory visit still paints cards.
        filtered_empty = len(getattr(self._filtered_list, "_items", ())) == 0
        needs_data = self._pending_refresh or (
            filtered_empty and bool(self._campaigns)
        )
        # Re-push card colors on show so a theme flip while hidden cannot stick.
        theme = get_theme()
        page_bg = theme.bg
        self._filtered_list.configure_theme(
            bg=page_bg, surface=theme.surface, border=theme.border, cards=True
        )
        if self._active_initialized or self._showing_all_active():
            self._active_list.configure_theme(
                bg=page_bg, surface=theme.surface, border=theme.border, cards=True
            )
        self._theme_dirty = False
        if needs_data:
            self._pending_refresh = False
            self.refresh()

    def _on_main_shown(self) -> None:
        self._tab_job = None
        # Inventory cards stay alive; reassert Main tk/ttk colors only after a theme flip.
        self._manager.reassert_main_theme()

    def _showing_all_active(self) -> bool:
        try:
            return self._views.get() == self._view_names[self.VIEW_ALL_ACTIVE]
        except Exception:
            return False

    def _matches_search(self, campaign: DropsCampaign) -> bool:
        query = self._search_entry.get().strip().casefold()
        if not query:
            return True
        return (
            query in campaign.game.name.casefold()
            or query in campaign.name.casefold()
        )

    def _passes_filters(self, campaign: DropsCampaign) -> bool:
        not_linked = bool(self._filters["not_linked"].get())
        expired = bool(self._filters["expired"].get())
        show_excluded = bool(self._filters["excluded"].get())
        upcoming = bool(self._filters["upcoming"].get())
        finished = bool(self._filters["finished"].get())
        priority_only = self._settings.priority_mode is PriorityMode.PRIORITY_ONLY
        game_name = campaign.game.name
        in_exclude = game_name in self._settings.exclude
        passes_priority = (not priority_only) or (game_name in self._settings.priority)
        # Excluded checkbox is additive: also show exclude-list games.
        # Unlike upstream OR, do NOT unlock every non-priority game.
        if show_excluded:
            exclude_ok = in_exclude or (not in_exclude and passes_priority)
        else:
            exclude_ok = not in_exclude and passes_priority
        return bool(
            campaign.required_minutes > 0
            and (not_linked or campaign.eligible)
            and (campaign.active or upcoming and campaign.upcoming or expired and campaign.expired)
            and exclude_ok
            and (finished or not campaign.finished)
        )

    def _is_active_visible(self, campaign: DropsCampaign) -> bool:
        return bool(
            campaign.required_minutes > 0
            and campaign.active
            and self._matches_search(campaign)
        )

    def _filtered_items(self) -> list[DropsCampaign]:
        return [c for c in self._campaigns if self._passes_filters(c)]

    def _active_items(self) -> list[DropsCampaign]:
        campaigns = [c for c in self._campaigns if self._is_active_visible(c)]
        mode = self._sort_mode.get()
        if mode == self.SORT_GAME:
            campaigns.sort(key=lambda c: c.game.name.casefold())
        elif mode == self.SORT_NAME:
            campaigns.sort(key=lambda c: c.name.casefold())
        else:
            campaigns.sort(key=lambda c: c.ends_at)
        return campaigns

    def _on_sort_selected(self, event: Any = None) -> None:
        label = self._sort_combo.get()
        self._sort_mode.set(self._sort_label_to_mode.get(label, self.SORT_ENDING))
        self.refresh()

    def _action_labels(self, campaign: DropsCampaign) -> tuple[str, str]:
        game_name = campaign.game.name
        in_priority = game_name in self._settings.priority
        in_exclude = game_name in self._settings.exclude
        priority_text = _(
            "gui",
            "inventory",
            "actions",
            "remove_priority" if in_priority else "add_priority",
        )
        exclude_text = _(
            "gui",
            "inventory",
            "actions",
            "remove_exclude" if in_exclude else "add_exclude",
        )
        return priority_text, exclude_text

    def _sync_action_buttons(self, campaign: DropsCampaign) -> None:
        priority_text, exclude_text = self._action_labels(campaign)
        for vlist in (self._filtered_list, self._active_list):
            display = vlist.display_for(campaign)
            if not display:
                continue
            display["priority_btn"].configure(text=priority_text)
            display["exclude_btn"].configure(text=exclude_text)

    def _toggle_priority(self, campaign: DropsCampaign) -> None:
        game_name = campaign.game.name
        if game_name in self._settings.priority:
            self._settings.priority.remove(game_name)
        else:
            self._settings.priority.append(game_name)
            if game_name in self._settings.exclude:
                self._settings.exclude.discard(game_name)
        self._settings.persist()
        self._manager.settings.reload_priority_list()
        self._manager.settings.reload_exclude_list()
        self._sync_action_buttons(campaign)
        self.refresh()
        self._manager._twitch.change_state(State.GAMES_UPDATE)

    def _toggle_exclude(self, campaign: DropsCampaign) -> None:
        game_name = campaign.game.name
        if game_name in self._settings.exclude:
            self._settings.exclude.discard(game_name)
        else:
            self._settings.exclude.add(game_name)
            if game_name in self._settings.priority:
                self._settings.priority.remove(game_name)
        self._settings.persist()
        self._manager.settings.reload_priority_list()
        self._manager.settings.reload_exclude_list()
        self._sync_action_buttons(campaign)
        self.refresh()
        self._manager._twitch.change_state(State.GAMES_UPDATE)

    def _on_tab_switched(self, event: tk.Event[ttk.Notebook]) -> None:
        self._cancel_tab_job()
        tab = self._manager.tabs.current_tab()
        # Keep inventory cards alive across tab switches. Suspend the pool while
        # Inventory is hidden so configure/set_items cannot cull or recreate it.
        if tab == 1:
            self._filtered_list.set_suspended(False)
            self._active_list.set_suspended(False)
            self._tab_job = self._manager._root.after(1, self._on_inventory_shown)
        else:
            self._filtered_list.set_suspended(True)
            self._active_list.set_suspended(True)
            if tab == 0:
                self._tab_job = self._manager._root.after(1, self._on_main_shown)

    def _on_view_switched(self, event: tk.Event[ttk.Notebook] | None = None) -> None:
        if self._showing_all_active() and not self._active_initialized:
            self._active_initialized = True
            theme = get_theme()
            self._active_list.configure_theme(
                bg=theme.bg,
                surface=theme.surface,
                border=theme.border,
                cards=True,
            )
        if self._manager.tabs.current_tab() == 1:
            self._manager._root.after(1, self.refresh)

    def get_status(self, campaign: DropsCampaign) -> tuple[str, str]:
        theme = get_theme()
        if campaign.active:
            status_text: str = _("gui", "inventory", "status", "active")
            status_color: str = theme.success
        elif campaign.upcoming:
            status_text = _("gui", "inventory", "status", "upcoming")
            status_color = theme.warning
        else:
            status_text = _("gui", "inventory", "status", "expired")
            status_color = theme.danger
        return (status_text, status_color)

    def refresh(self, *, force: bool = False) -> None:
        filtered = self._filtered_items()
        active = self._active_items()
        self._active_summary.configure(
            text=_("gui", "inventory", "active_summary").format(count=len(active))
        )
        # Defer routine data refresh while Inventory is hidden — keep cards mounted.
        # force=True is used after theme flips so cards are ready before the next visit.
        if not force and self._manager.tabs.current_tab() != 1:
            self._pending_refresh = True
            return
        self._pending_refresh = False
        self._filtered_list.set_items(filtered)
        if self._active_initialized or self._showing_all_active():
            self._active_initialized = True
            self._active_list.set_items(active)

    def _canvas_for_widget(self, widget: tk.Misc | None) -> tk.Canvas:
        current = widget
        while current is not None:
            if current is self._canvas or current is self._active_canvas:
                return current  # type: ignore[return-value]
            if current is self._main_frame:
                return self._canvas
            if current is self._active_main_frame:
                return self._active_canvas
            current = current.master if hasattr(current, "master") else None
        return self._active_canvas if self._showing_all_active() else self._canvas

    def _bind_card_mousewheel(self, widget: tk.Misc) -> None:
        widget.bind("<MouseWheel>", self._on_mousewheel, add="+")
        for child in widget.winfo_children():
            self._bind_card_mousewheel(child)

    def _drops_need_hscroll(self, canvas: tk.Canvas) -> bool:
        bbox = canvas.bbox("all")
        if bbox is None:
            return False
        content_w = bbox[2] - bbox[0]
        return content_w > max(canvas.winfo_width(), 1) + 1

    def _on_drops_mousewheel(self, event: tk.Event[tk.Misc], canvas: tk.Canvas) -> str | None:
        # Sideways only when rewards overflow; otherwise keep vertical list scroll.
        if self._drops_need_hscroll(canvas):
            if event.delta:
                steps = int(-event.delta / 120)
                if steps == 0:
                    steps = -1 if event.delta > 0 else 1
            else:
                steps = -1
            canvas.xview_scroll(steps, "units")
            return "break"
        return self._on_mousewheel(event)

    def _bind_drops_hscroll(self, canvas: tk.Canvas, widget: tk.Misc) -> None:
        widget.bind(
            "<MouseWheel>",
            lambda e, c=canvas: self._on_drops_mousewheel(e, c),
        )
        for child in widget.winfo_children():
            self._bind_drops_hscroll(canvas, child)

    def _on_mousewheel(self, event: tk.Event[tk.Misc]):
        if event.delta:
            steps = int(-event.delta / 120)
            if steps == 0:
                steps = -1 if event.delta > 0 else 1
        else:
            steps = -1
        state: int = event.state if isinstance(event.state, int) else 0
        canvas = self._canvas_for_widget(event.widget)
        if state & 1:
            canvas.xview_scroll(steps, "units")
        else:
            # Few campaigns: content fits — don't "scroll" into empty space.
            try:
                first, last = canvas.yview()
                if first <= 0.001 and last >= 0.999:
                    return "break"
            except Exception:
                pass
            canvas.yview_scroll(steps, "units")
        return "break"

    def _create_card(self, parent: tk.Misc, campaign: DropsCampaign) -> CampaignDisplay:
        """Build a pool card shell for campaign (images filled in _bind_card)."""
        theme = get_theme()
        campaign_frame = tk.Frame(
            parent,
            bg=theme.surface,
            highlightbackground=theme.border,
            highlightthickness=1,
            padx=8,
            pady=8,
        )
        campaign_frame.columnconfigure(1, weight=1)
        campaign_frame.columnconfigure(3, weight=10000)
        # Pure tk labels — ttk.Label/Link.TLabel kept Light system fill on Dark
        # (logs: frame_bg already #18181b while screenshot showed a white card).
        name_label = tk.Label(
            campaign_frame,
            text=campaign.name,
            bg=theme.surface,
            fg=theme.fg,
            anchor="w",
            justify="left",
        )
        name_label.grid(column=0, row=0, columnspan=2, sticky="w")
        status_text, status_color = self.get_status(campaign)
        status_label = tk.Label(
            campaign_frame,
            text=status_text,
            bg=theme.surface,
            fg=status_color,
            anchor="w",
        )
        status_label.grid(column=1, row=1, sticky="w", padx=4)
        ends_label = tk.Label(
            campaign_frame,
            text=_("gui", "inventory", "ends").format(
                time=campaign.ends_at.astimezone().replace(microsecond=0, tzinfo=None)
            ),
            bg=theme.surface,
            fg=theme.muted,
            anchor="w",
        )
        ends_label.grid(column=1, row=2, sticky="w", padx=4)
        if campaign.eligible:
            link_text = _("gui", "inventory", "status", "linked")
            link_fg = theme.success
        else:
            link_text = _("gui", "inventory", "status", "not_linked")
            link_fg = theme.danger
        link_label = tk.Label(
            campaign_frame,
            text=link_text,
            bg=theme.surface,
            fg=link_fg,
            cursor="hand2",
            anchor="w",
        )
        link_label.grid(column=1, row=3, sticky="w", padx=4)
        link_label.bind(
            "<ButtonRelease-1>",
            lambda _e, url=campaign.link_url: webopen(url),
            add="+",
        )
        # Stack vertically — Russian action labels are long and clip side-by-side
        actions = tk.Frame(campaign_frame, bg=theme.surface, highlightthickness=0)
        actions.grid(column=1, row=4, sticky="ew", padx=4, pady=4)
        priority_text, exclude_text = self._action_labels(campaign)
        priority_btn = accent_tk_button(
            actions,
            text=priority_text,
            command=lambda c=campaign: self._toggle_priority(c),
        )
        priority_btn.pack(fill="x", pady=(0, 4))
        exclude_btn = secondary_tk_button(
            actions,
            text=exclude_text,
            command=lambda c=campaign: self._toggle_exclude(c),
        )
        exclude_btn.pack(fill="x")
        acl = campaign.allowed_channels
        if acl:
            if len(acl) <= 5:
                allowed_text: str = '\n'.join(ch.name for ch in acl)
            else:
                allowed_text = '\n'.join(ch.name for ch in acl[:4])
                allowed_text += (
                    f"\n{_('gui', 'inventory', 'and_more').format(amount=len(acl) - 4)}"
                )
        else:
            allowed_text = _("gui", "inventory", "all_channels")
        channels_label = tk.Label(
            campaign_frame,
            text=f"{_('gui', 'inventory', 'allowed_channels')}\n{allowed_text}",
            bg=theme.surface,
            fg=theme.fg,
            anchor="nw",
            justify="left",
        )
        channels_label.grid(column=1, row=5, sticky="nw", padx=4)
        campaign_image_label = tk.Label(
            campaign_frame,
            image=self._cache.placeholder(self.CAMPAIGN_IMAGE_SIZE),
            bg=theme.surface,
            borderwidth=0,
            highlightthickness=0,
        )
        campaign_image_label.grid(column=0, row=1, rowspan=5)
        # tk.Frame divider — ttk.Separator stays light on Windows dark theme.
        divider = tk.Frame(campaign_frame, bg=theme.border, width=1, highlightthickness=0)
        divider.grid(column=2, row=0, rowspan=6, sticky="ns")
        # Horizontal scroll area for reward chips that don't fit the card width.
        drops_host = tk.Frame(campaign_frame, bg=theme.surface, highlightthickness=0)
        drops_host.grid(column=3, row=0, rowspan=6, sticky="nsew", padx=4)
        drops_host.grid_columnconfigure(0, weight=1)
        drops_host.grid_rowconfigure(0, weight=1)
        drops_canvas = tk.Canvas(
            drops_host,
            bg=theme.surface,
            highlightthickness=0,
            bd=0,
            height=1,
        )
        drops_canvas.grid(column=0, row=0, sticky="nsew")
        drops_scroll = FlatScrollbar(
            drops_host, orient="horizontal", command=drops_canvas.xview
        )
        # Shown only when rewards overflow the card width.
        drops_canvas.configure(xscrollcommand=drops_scroll.set)
        drops_row = tk.Frame(drops_canvas, bg=theme.surface, highlightthickness=0)
        drops_window = drops_canvas.create_window(0, 0, anchor="nw", window=drops_row)

        def _sync_drops_scroll(_event: tk.Event[tk.Misc] | None = None) -> None:
            drops_row.update_idletasks()
            bbox = drops_canvas.bbox("all")
            if bbox is None:
                drops_canvas.configure(scrollregion=(0, 0, 0, 0), height=1)
                drops_scroll.grid_remove()
                return
            drops_canvas.configure(scrollregion=bbox)
            content_h = max(bbox[3] - bbox[1], 1)
            content_w = max(bbox[2] - bbox[0], 1)
            drops_canvas.configure(height=content_h)
            drops_canvas.itemconfigure(drops_window, height=content_h)
            view_w = max(drops_canvas.winfo_width(), 1)
            if content_w > view_w + 1:
                drops_scroll.grid(column=0, row=1, sticky="ew")
            else:
                drops_scroll.grid_remove()
                drops_canvas.xview_moveto(0)

        drops_row.bind("<Configure>", _sync_drops_scroll, add="+")
        drops_canvas.bind("<Configure>", _sync_drops_scroll, add="+")

        benefit_image_labels: list[Any] = []
        benefit_labels: list[Any] = []
        drop_frames: list[Any] = []
        progress_labels: dict[str, Any] = {}
        benefit_placeholder = self._cache.placeholder(self.BENEFIT_IMAGE_SIZE)
        for i, drop in enumerate(campaign.drops):
            drop_frame = tk.Frame(
                drops_row,
                bg=theme.surface_alt,
                highlightbackground=theme.border,
                highlightthickness=1,
                padx=5,
                pady=5,
            )
            drop_frame.grid(column=i, row=0, padx=4)
            drop_frames.append(drop_frame)
            benefits_frame = tk.Frame(drop_frame, bg=theme.surface_alt, highlightthickness=0)
            benefits_frame.grid(column=0, row=0)
            for j, benefit in enumerate(drop.benefits):
                benefit_label = tk.Label(
                    benefits_frame,
                    text=benefit.name,
                    image=benefit_placeholder,
                    compound="bottom",
                    bg=theme.surface_alt,
                    fg=theme.fg,
                    borderwidth=0,
                    highlightthickness=0,
                )
                benefit_label.grid(column=j, row=0, padx=5)
                benefit_image_labels.append(benefit_label)
                benefit_labels.append(benefit_label)
            progress = tk.Label(
                drop_frame,
                justify=tk.CENTER,
                bg=theme.surface_alt,
                fg=theme.fg,
            )
            self.update_progress(drop, progress)
            progress.grid(column=0, row=1)
            progress_labels[drop.id] = progress
            self._drop_labels[drop.id] = progress
        self._bind_card_mousewheel(campaign_frame)
        # Overwrite drops-area wheel: scroll rewards sideways instead of the page.
        self._bind_drops_hscroll(drops_canvas, drops_host)
        _sync_drops_scroll()
        return {
            "frame": campaign_frame,
            "status": status_label,
            "name_label": name_label,
            "ends_label": ends_label,
            "link_label": link_label,
            "channels_label": channels_label,
            "actions": actions,
            "divider": divider,
            "drops_host": drops_host,
            "drops_canvas": drops_canvas,
            "drops_scroll": drops_scroll,
            "drops_row": drops_row,
            "drop_frames": drop_frames,
            "benefit_labels": benefit_labels,
            "progress_labels": progress_labels,
            "priority_btn": priority_btn,
            "exclude_btn": exclude_btn,
            "campaign_image_label": campaign_image_label,
            "benefit_image_labels": benefit_image_labels,
            "campaign": campaign,
        }

    def _bind_card(self, display: CampaignDisplay, campaign: DropsCampaign) -> None:
        """Refresh dynamic bits + images when a pool slot is (re)bound."""
        display["campaign"] = campaign
        status_text, status_color = self.get_status(campaign)
        display["status"].configure(text=status_text, foreground=status_color)
        priority_text, exclude_text = self._action_labels(campaign)
        display["priority_btn"].configure(
            text=priority_text,
            command=lambda c=campaign: self._toggle_priority(c),
        )
        display["exclude_btn"].configure(
            text=exclude_text,
            command=lambda c=campaign: self._toggle_exclude(c),
        )
        for drop_id, label in display["progress_labels"].items():
            self._drop_labels[drop_id] = label
            drop = campaign.timed_drops.get(drop_id)
            if drop is not None:
                self.update_progress(drop, label)
        images = self._images_by_campaign.get(campaign)
        if images is not None:
            campaign_image, benefit_images = images
            display["campaign_image_label"].configure(image=campaign_image)
            for label, image in zip(display["benefit_image_labels"], benefit_images):
                label.configure(image=image)
        # Re-assert palette after bind — pool reuse can leave a Light frame on Dark.
        self._theme_card(display)

    def _destroy_card(self, display: CampaignDisplay) -> None:
        for drop_id, label in display.get("progress_labels", {}).items():
            if self._drop_labels.get(drop_id) is label:
                self._drop_labels.pop(drop_id, None)
        frame = display.get("frame")
        if frame is not None:
            try:
                frame.place_forget()
            except Exception:
                pass
            frame.destroy()

    def _theme_card(self, display: CampaignDisplay) -> None:
        theme = get_theme()
        try:
            frame = display["frame"]
            frame.configure(bg=theme.surface, highlightbackground=theme.border)
            display["campaign_image_label"].configure(bg=theme.surface)
            display["actions"].configure(bg=theme.surface)
            display["divider"].configure(bg=theme.border)
            display["drops_host"].configure(bg=theme.surface)
            display["drops_canvas"].configure(bg=theme.surface)
            display["drops_row"].configure(bg=theme.surface)
            if isinstance(display["drops_scroll"], FlatScrollbar):
                display["drops_scroll"].configure_theme(
                    trough=theme.scroll_trough,
                    thumb=theme.scroll_thumb,
                    active=theme.scroll_active,
                )
            for drop_frame in display["drop_frames"]:
                drop_frame.configure(
                    bg=theme.surface_alt, highlightbackground=theme.border
                )
                for child in drop_frame.winfo_children():
                    if isinstance(child, tk.Frame):
                        child.configure(bg=theme.surface_alt)
            for label in display["benefit_labels"]:
                label.configure(bg=theme.surface_alt, fg=theme.fg)
            for progress in display["progress_labels"].values():
                with suppress(Exception):
                    progress.configure(bg=theme.surface_alt, fg=theme.fg)
            display["name_label"].configure(bg=theme.surface, fg=theme.fg)
            display["ends_label"].configure(bg=theme.surface, fg=theme.muted)
            display["channels_label"].configure(bg=theme.surface, fg=theme.fg)
            if isinstance(display["priority_btn"], ThemedButton):
                display["priority_btn"].apply_theme(theme)
            if isinstance(display["exclude_btn"], ThemedButton):
                display["exclude_btn"].apply_theme(theme)
            campaign = display.get("campaign")
            if campaign is not None:
                status_text, status_color = self.get_status(campaign)
                display["status"].configure(
                    text=status_text, bg=theme.surface, fg=status_color
                )
                if campaign.eligible:
                    display["link_label"].configure(
                        bg=theme.surface, fg=theme.success
                    )
                else:
                    display["link_label"].configure(
                        bg=theme.surface, fg=theme.danger
                    )
        except Exception as exc:
            raise

    async def add_campaign(self, campaign: DropsCampaign) -> None:
        self._campaigns.append(campaign)
        # Inventory often loads while Main is selected. Previously we skipped
        # refresh entirely, so filtered cards stayed empty until the user opened
        # "Все активные" (which calls refresh via _on_view_switched).
        if self._manager.tabs.current_tab() == 1:
            self.refresh()
        else:
            self._pending_refresh = True

        benefit_urls = [
            benefit.image_url
            for drop in campaign.drops
            for benefit in drop.benefits
        ]
        loaded = await asyncio.gather(
            self._cache.get(campaign.image_url, self.CAMPAIGN_IMAGE_SIZE),
            *(self._cache.get(url, self.BENEFIT_IMAGE_SIZE) for url in benefit_urls),
        )
        campaign_image = loaded[0]
        benefit_images = list(loaded[1:])
        self._images_by_campaign[campaign] = (campaign_image, benefit_images)
        # Apply to any currently visible pool slots for this campaign
        for vlist in (self._filtered_list, self._active_list):
            display = vlist.display_for(campaign)
            if display is not None:
                self._bind_card(display, campaign)

    def clear(self) -> None:
        self._filtered_list.clear()
        self._active_list.clear()
        self._campaigns.clear()
        self._images_by_campaign.clear()
        self._drop_labels.clear()
        self._active_initialized = False
        self._active_summary.configure(
            text=_("gui", "inventory", "active_summary").format(count=0)
        )

    def update_progress(self, drop: TimedDrop, label: tk.Misc) -> None:
        progress_text: str
        progress_color: str = ''
        if drop.is_claimed:
            progress_color = "green"
            progress_text = _("gui", "inventory", "status", "claimed")
        elif drop.can_claim:
            progress_color = "goldenrod"
            progress_text = _("gui", "inventory", "status", "ready_to_claim")
        elif drop.current_minutes or drop.can_earn():
            progress_text = _("gui", "inventory", "percent_progress").format(
                percent=f"{drop.progress:3.1%}",
                minutes=drop.required_minutes,
            )
            if drop.ends_at < drop.campaign.ends_at:
                progress_text += '\n' + _("gui", "inventory", "ends").format(
                    time=drop.ends_at.astimezone().replace(microsecond=0, tzinfo=None)
                )
        else:
            if drop.required_minutes > 0:
                progress_text = _("gui", "inventory", "minutes_progress").format(
                    minutes=drop.required_minutes
                )
            else:
                progress_text = ''
            if datetime.now(timezone.utc) < drop.starts_at > drop.campaign.starts_at:
                progress_text += '\n' + _("gui", "inventory", "starts").format(
                    time=drop.starts_at.astimezone().replace(microsecond=0, tzinfo=None)
                )
            elif drop.ends_at < drop.campaign.ends_at:
                progress_text += '\n' + _("gui", "inventory", "ends").format(
                    time=drop.ends_at.astimezone().replace(microsecond=0, tzinfo=None)
                )
        theme = get_theme()
        color = progress_color or theme.fg
        try:
            label.configure(text=progress_text, fg=color)
        except tk.TclError:
            label.configure(text=progress_text, foreground=color)

    def update_drop(self, drop: TimedDrop) -> None:
        # A campaign may be bound in filtered and/or active pools — update every visible label.
        updated = False
        for vlist in (self._filtered_list, self._active_list):
            display = vlist.display_for(drop.campaign)
            if display is None:
                continue
            label = display["progress_labels"].get(drop.id)
            if label is not None:
                self.update_progress(drop, label)
                updated = True
        if not updated:
            label = self._drop_labels.get(drop.id)
            if label is not None:
                self.update_progress(drop, label)

def proxy_validate(entry: PlaceholderEntry, settings: Settings) -> bool:
    raw_url = entry.get().strip()
    entry.replace(raw_url)
    url = URL(raw_url)
    valid = url.host is not None and url.port is not None
    if not valid:
        entry.clear()
        url = URL()
    settings.proxy = url
    return valid

class _SettingsVars(TypedDict):
    tray: IntVar
    proxy: StringVar
    autostart: IntVar
    dark_mode: IntVar
    language: StringVar
    priority_mode: StringVar
    tray_notifications: IntVar
    enable_badges_emotes: IntVar
    available_drops_check: IntVar

class SettingsPanel:
    AUTOSTART_NAME: str = "TwitchDropsMiner"
    AUTOSTART_KEY: str = "HKCU/Software/Microsoft/Windows/CurrentVersion/Run"

    @cached_property
    def PRIORITY_MODES(self) -> dict[PriorityMode, str]:
        # NOTE: Translation calls have to be deferred here,
        # to allow changing the language before the settings panel is initialized.
        return {
            PriorityMode.PRIORITY_ONLY: _("gui", "settings", "priority_modes", "priority_only"),
            PriorityMode.ENDING_SOONEST: _("gui", "settings", "priority_modes", "ending_soonest"),
            PriorityMode.LOW_AVBL_FIRST: _(
                "gui", "settings", "priority_modes", "low_availability"
            ),
        }

    def __init__(self, manager: GUIManager, master: tk.Misc):
        self._manager = manager
        self._settings: Settings = manager._twitch.settings
        theme = get_theme()
        priority_mode = self._settings.priority_mode
        if priority_mode not in self.PRIORITY_MODES:
            priority_mode = PriorityMode.PRIORITY_ONLY
            self._settings.priority_mode = priority_mode
        self._vars: _SettingsVars = {
            "autostart": IntVar(master, 0),
            "language": StringVar(master, _.current),
            "proxy": StringVar(master, str(self._settings.proxy)),
            "tray": IntVar(master, self._settings.autostart_tray),
            "dark_mode": IntVar(master, int(self._settings.dark_mode)),
            "priority_mode": StringVar(master, self.PRIORITY_MODES[priority_mode]),
            "tray_notifications": IntVar(master, self._settings.tray_notifications),
            "enable_badges_emotes": IntVar(
                master, int(self._settings.enable_badges_emotes)
            ),
            "available_drops_check": IntVar(
                master, int(self._settings.available_drops_check)
            ),
        }
        self._game_names: set[str] = set()
        self._search_after_ids: dict[str, str | None] = {"priority": None, "exclude": None}
        self._search_tasks: dict[str, asyncio.Task[None] | None] = {"priority": None, "exclude": None}
        self._search_generation: dict[str, int] = {"priority": 0, "exclude": 0}
        master.rowconfigure(0, weight=1)
        master.columnconfigure(0, weight=1)
        center_frame = ctk.CTkScrollableFrame(master, fg_color="transparent")
        center_frame.grid(column=0, row=0, sticky="nsew", padx=4, pady=4)
        center_frame.grid_columnconfigure(0, weight=5, minsize=280)
        center_frame.grid_columnconfigure(1, weight=6, minsize=260)
        center_frame.grid_columnconfigure(2, weight=5, minsize=220)
        center_frame.grid_rowconfigure(0, weight=1)

        def add_toggle_row(
            parent: Any,
            row: int,
            label: str,
            variable: IntVar,
            command: Any = None,
        ) -> None:
            ctk.CTkLabel(
                parent,
                text=label.rstrip(": ").rstrip(":"),
                anchor="w",
                font=ctk.CTkFont(size=12),
                text_color=theme.fg,
            ).grid(column=0, row=row, sticky="ew", pady=5, padx=(0, 10))
            settings_switch(parent, variable=variable, command=command).grid(
                column=1, row=row, sticky="e", pady=5
            )

        def add_field_row(parent: Any, row: int, label: str) -> ctk.CTkFrame:
            ctk.CTkLabel(
                parent,
                text=label.rstrip(": ").rstrip(":"),
                anchor="w",
                font=ctk.CTkFont(size=12),
                text_color=theme.fg,
            ).grid(column=0, row=row, columnspan=2, sticky="ew", pady=(8, 2))
            holder = ctk.CTkFrame(parent, fg_color="transparent")
            holder.grid(column=0, row=row + 1, columnspan=2, sticky="ew", pady=(0, 2))
            holder.grid_columnconfigure(0, weight=1)
            return holder

        # ── General ──────────────────────────────────────────────
        general_section = SectionFrame(
            center_frame, _("gui", "settings", "general", "name")
        )
        general_section.grid(column=0, row=0, sticky="nsew", padx=(0, 8), pady=(0, 8))
        general = general_section.body
        general.grid_columnconfigure(0, weight=1)
        grow = 0

        lang_holder = add_field_row(general, grow, "Language 🌐 (requires restart)")
        grow += 2
        SelectCombobox(
            lang_holder,
            values=list(_.languages),
            textvariable=self._vars["language"],
            command=lambda e: setattr(self._settings, "language", self._vars["language"].get()),
        ).grid(column=0, row=0, sticky="ew")

        self._vars["autostart"].set(self._query_autostart())
        add_toggle_row(
            general,
            grow,
            _("gui", "settings", "general", "autostart"),
            self._vars["autostart"],
            self.update_autostart,
        )
        grow += 1
        if sys.platform != "darwin":
            add_toggle_row(
                general,
                grow,
                _("gui", "settings", "general", "tray"),
                self._vars["tray"],
                self.update_autostart,
            )
            grow += 1
            add_toggle_row(
                general,
                grow,
                _("gui", "settings", "general", "tray_notifications"),
                self._vars["tray_notifications"],
                lambda: setattr(
                    self._settings,
                    "tray_notifications",
                    bool(self._vars["tray_notifications"].get()),
                ),
            )
            grow += 1
        add_toggle_row(
            general,
            grow,
            _("gui", "settings", "general", "dark_mode"),
            self._vars["dark_mode"],
            self.update_dark_mode,
        )
        grow += 1

        mode_holder = add_field_row(
            general, grow, _("gui", "settings", "general", "priority_mode")
        )
        grow += 2
        SelectCombobox(
            mode_holder,
            command=self.priority_mode,
            textvariable=self._vars["priority_mode"],
            values=list(self.PRIORITY_MODES.values()),
        ).grid(column=0, row=0, sticky="ew")

        proxy_holder = add_field_row(general, grow, _("gui", "settings", "general", "proxy"))
        self._proxy = PlaceholderEntry(
            proxy_holder,
            width=37,
            validate="focusout",
            prefill="http://",
            textvariable=self._vars["proxy"],
            placeholder="http://username:password@address:port",
        )
        self._proxy.config(validatecommand=partial(proxy_validate, self._proxy, self._settings))
        self._proxy.grid(column=0, row=0, sticky="ew")

        # ── Advanced ─────────────────────────────────────────────
        advanced_section = SectionFrame(
            center_frame, _("gui", "settings", "advanced", "name")
        )
        advanced_section.grid(column=0, row=1, sticky="nsew", padx=(0, 8), pady=(0, 8))
        advanced = advanced_section.body
        advanced.grid_columnconfigure(0, weight=1)
        self._warning = warning_banner(
            advanced,
            _("gui", "settings", "advanced", "warning"),
            _("gui", "settings", "advanced", "warning_text"),
        )
        self._warning.grid(column=0, row=0, columnspan=2, sticky="ew", pady=(0, 8))
        add_toggle_row(
            advanced,
            1,
            _("gui", "settings", "advanced", "enable_badges_emotes"),
            self._vars["enable_badges_emotes"],
            lambda: setattr(
                self._settings,
                "enable_badges_emotes",
                bool(self._vars["enable_badges_emotes"].get()),
            ),
        )
        add_toggle_row(
            advanced,
            2,
            _("gui", "settings", "advanced", "available_drops_check"),
            self._vars["available_drops_check"],
            lambda: setattr(
                self._settings,
                "available_drops_check",
                bool(self._vars["available_drops_check"].get()),
            ),
        )

        # ── Priority ─────────────────────────────────────────────
        priority_section = SectionFrame(
            center_frame,
            _("gui", "settings", "priority"),
        )
        priority_section.grid(
            column=1, row=0, rowspan=2, sticky="nsew", padx=(0, 8), pady=(0, 8)
        )
        priority_frame = priority_section.body
        priority_frame.grid_columnconfigure(0, weight=1)
        priority_frame.grid_rowconfigure(1, weight=1)

        priority_toolbar = ctk.CTkFrame(priority_frame, fg_color="transparent")
        priority_toolbar.grid(column=0, row=0, columnspan=2, sticky="ew", pady=(0, 8))
        priority_toolbar.grid_columnconfigure(0, weight=1)
        self._priority_entry = AutocompleteCombobox(
            priority_toolbar,
            placeholder=_("gui", "settings", "game_name"),
            width=28,
            search_callback=lambda query: self._schedule_game_search("priority", query),
        )
        self._priority_entry.grid(column=0, row=0, sticky="ew", padx=(0, 6))
        accent_button(
            priority_toolbar, text="+", command=self.priority_add, width=36, height=30
        ).grid(column=1, row=0)

        self._priority_shell = ctk.CTkFrame(
            priority_frame,
            fg_color=theme.field_bg,
            border_color=theme.border,
            border_width=1,
            corner_radius=8,
        )
        self._priority_shell.grid(column=0, row=1, sticky="nsew", padx=(0, 6))
        self._priority_shell.grid_columnconfigure(0, weight=1)
        self._priority_shell.grid_rowconfigure(0, weight=1)
        self._priority_list = PaddedListbox(
            self._priority_shell,
            height=14,
            padding=(6, 4),
            activestyle="none",
            selectmode="single",
            highlightthickness=0,
            exportselection=False,
            relief="flat",
            borderwidth=0,
            bg=theme.field_bg,
            fg=theme.fg,
            selectbackground=theme.sel_bg,
            selectforeground=theme.sel_fg,
        )
        self._priority_list.grid(column=0, row=0, sticky="nsew", padx=2, pady=2)
        self._priority_list.insert("end", *self._settings.priority)

        controls = ctk.CTkFrame(priority_frame, fg_color="transparent", width=40)
        controls.grid(column=1, row=1, sticky="ns")
        for i, (text, cmd) in enumerate(
            (
                ("⇈", partial(self.priority_move, MAX_INT)),
                ("↑", partial(self.priority_move, 1)),
                ("↓", partial(self.priority_move, -1)),
                ("⇊", partial(self.priority_move, -MAX_INT)),
                ("✕", self.priority_delete),
            )
        ):
            secondary_button(controls, width=36, height=30, text=text, command=cmd).pack(
                fill="x", pady=(0, 6) if i < 4 else (8, 0)
            )

        # ── Exclude ──────────────────────────────────────────────
        exclude_section = SectionFrame(
            center_frame,
            _("gui", "settings", "exclude"),
        )
        exclude_section.grid(column=2, row=0, rowspan=2, sticky="nsew", pady=(0, 8))
        exclude_frame = exclude_section.body
        exclude_frame.grid_columnconfigure(0, weight=1)
        exclude_frame.grid_rowconfigure(1, weight=1)

        exclude_toolbar = ctk.CTkFrame(exclude_frame, fg_color="transparent")
        exclude_toolbar.grid(column=0, row=0, sticky="ew", pady=(0, 8))
        exclude_toolbar.grid_columnconfigure(0, weight=1)
        self._exclude_entry = AutocompleteCombobox(
            exclude_toolbar,
            placeholder=_("gui", "settings", "game_name"),
            width=24,
            search_callback=lambda query: self._schedule_game_search("exclude", query),
        )
        self._exclude_entry.grid(column=0, row=0, sticky="ew", padx=(0, 6))
        accent_button(
            exclude_toolbar, text="+", command=self.exclude_add, width=36, height=30
        ).grid(column=1, row=0)

        self._exclude_shell = ctk.CTkFrame(
            exclude_frame,
            fg_color=theme.field_bg,
            border_color=theme.border,
            border_width=1,
            corner_radius=8,
        )
        self._exclude_shell.grid(column=0, row=1, sticky="nsew")
        self._exclude_shell.grid_columnconfigure(0, weight=1)
        self._exclude_shell.grid_rowconfigure(0, weight=1)
        self._exclude_list = PaddedListbox(
            self._exclude_shell,
            height=14,
            padding=(6, 4),
            activestyle="none",
            selectmode="single",
            highlightthickness=0,
            exportselection=False,
            relief="flat",
            borderwidth=0,
            bg=theme.field_bg,
            fg=theme.fg,
            selectbackground=theme.sel_bg,
            selectforeground=theme.sel_fg,
        )
        self._exclude_list.grid(column=0, row=0, sticky="nsew", padx=2, pady=2)
        self._exclude_list.insert("end", *sorted(self._settings.exclude))
        secondary_button(
            exclude_frame, text="✕", command=self.exclude_delete, height=32
        ).grid(column=0, row=2, sticky="ew", pady=(8, 0))

        # ── Reload bar ───────────────────────────────────────────
        self._reload_frame = ctk.CTkFrame(
            center_frame,
            fg_color=theme.surface,
            border_color=theme.border,
            border_width=1,
            corner_radius=10,
        )
        self._reload_frame.grid(column=0, row=2, columnspan=3, sticky="ew", pady=(4, 0))
        self._reload_frame.grid_columnconfigure(0, weight=1)
        self._reload_label = ctk.CTkLabel(
            self._reload_frame,
            text=_("gui", "settings", "reload_text"),
            anchor="w",
            text_color=theme.muted,
            font=ctk.CTkFont(size=12),
        )
        self._reload_label.grid(column=0, row=0, sticky="ew", padx=14, pady=10)
        accent_button(
            self._reload_frame,
            text=_("gui", "settings", "reload"),
            command=self._manager._twitch.state_change(State.INVENTORY_FETCH),
            width=120,
            height=32,
        ).grid(column=1, row=0, padx=14, pady=10)

    def configure_theme(self, theme: Theme | None = None) -> None:
        theme = theme or get_theme()
        apply_warning_banner_theme(self._warning, theme)
        self._priority_shell.configure(
            fg_color=theme.field_bg, border_color=theme.border
        )
        self._exclude_shell.configure(
            fg_color=theme.field_bg, border_color=theme.border
        )
        self._reload_frame.configure(
            fg_color=theme.surface, border_color=theme.border
        )
        self._reload_label.configure(text_color=theme.muted)

    def clear_selection(self) -> None:
        self._priority_list.selection_clear(0, "end")
        self._exclude_list.selection_clear(0, "end")

    def update_dark_mode(self) -> None:
        dark = bool(self._vars["dark_mode"].get())
        self._settings.dark_mode = dark
        self._manager.apply_theme(dark)

    def _get_self_path(self) -> str:
        # NOTE: we need double quotes in case the path contains spaces
        return f'"{SELF_PATH.resolve()!s}"'

    def _get_autostart_path(self) -> str:
        flags: list[str] = []
        # if applicable, include the current logging level as well
        for lvl_idx, lvl_value in LOGGING_LEVELS.items():
            if lvl_value == self._settings.logging_level:
                if lvl_idx > 0:
                    flags.append(f"-{'v' * lvl_idx}")
                break
        if self._vars["tray"].get():
            flags.append("--tray")
        if not IS_PACKAGED:
            # non-packaged autostart has to be done through the venv path pythonw
            return f"\"{SCRIPTS_PATH / 'pythonw'!s}\" {self._get_self_path()} {' '.join(flags)}"
        return f"{self._get_self_path()} {' '.join(flags)}"

    def _get_linux_autostart_filepath(self) -> Path:
        autostart_folder: Path = Path("~/.config/autostart").expanduser()
        if (config_home := os.environ.get("XDG_CONFIG_HOME")) is not None:
            config_autostart: Path = Path(config_home, "autostart").expanduser()
            if config_autostart.exists():
                autostart_folder = config_autostart
        return autostart_folder / f"{self.AUTOSTART_NAME}.desktop"

    def _get_mac_autostart_filepath(self) -> Path:
        return Path(
            Path.home(), f"Library/LaunchAgents/com.devilxd.{self.AUTOSTART_NAME.lower()}.plist"
        )

    def _query_autostart(self) -> bool:
        if sys.platform == "win32":
            with RegistryKey(self.AUTOSTART_KEY, read_only=True) as key:
                try:
                    value_type, value = key.get(self.AUTOSTART_NAME)
                except ValueNotFound:
                    return False
                # TODO: Consider deleting the old value to avoid autostart errors
                return (
                    value_type is ValueType.REG_SZ
                    and self._get_self_path() in value
                )
        elif sys.platform == "linux":
            autostart_file: Path = self._get_linux_autostart_filepath()
            if not autostart_file.exists():
                return False
            with autostart_file.open('r', encoding="utf8") as file:
                # TODO: Consider deleting the old file to avoid autostart errors
                return self._get_self_path() in file.read()
        elif sys.platform == "darwin":
            plist_file = self._get_mac_autostart_filepath()
            if not plist_file.exists():
                return False
            with plist_file.open('r', encoding="utf8") as file:
                return str(SELF_PATH.resolve()) in file.read()

    def update_autostart(self) -> None:
        enabled = bool(self._vars["autostart"].get())
        self._settings.autostart_tray = bool(self._vars["tray"].get())
        if sys.platform == "win32":
            if enabled:
                with RegistryKey(self.AUTOSTART_KEY) as key:
                    key.set(
                        self.AUTOSTART_NAME,
                        ValueType.REG_SZ,
                        self._get_autostart_path(),
                    )
            else:
                with RegistryKey(self.AUTOSTART_KEY) as key:
                    key.delete(self.AUTOSTART_NAME, silent=True)
        elif sys.platform == "linux":
            autostart_file: Path = self._get_linux_autostart_filepath()
            if enabled:
                file_contents: str = dedent(
                    f"""
                    [Desktop Entry]
                    Type=Application
                    Name=Twitch Drops Miner
                    Description=Mine timed drops on Twitch
                    Exec=sh -c '{self._get_autostart_path()}'
                    """
                )
                with autostart_file.open('w', encoding="utf8") as file:
                    file.write(file_contents)
            else:
                autostart_file.unlink(missing_ok=True)
        elif sys.platform == "darwin":
            plist_file = self._get_mac_autostart_filepath()

            if enabled:
                command_parts = shlex.split(self._get_autostart_path())
                plist_data = {
                    "Label": f"com.devilxd.{self.AUTOSTART_NAME.lower()}",
                    "ProgramArguments": command_parts,
                    "RunAtLoad": True,
                }
                plist_file.parent.mkdir(parents=True, exist_ok=True)
                with plist_file.open("wb") as file:
                    plistlib.dump(plist_data, file)
            else:
                plist_file.unlink(missing_ok=True)

    def _game_entry(self, field: str) -> AutocompleteCombobox:
        if field == "priority":
            return self._priority_entry
        return self._exclude_entry

    def _local_game_choices(self, field: str) -> list[str]:
        blocked = self._settings.priority if field == "priority" else self._settings.exclude
        return sorted(self._game_names.difference(blocked))

    def _schedule_game_search(self, field: str, query: str) -> None:
        entry = self._game_entry(field)
        after_id = self._search_after_ids[field]
        if after_id is not None:
            try:
                entry.after_cancel(after_id)
            except tk.TclError:
                pass
            self._search_after_ids[field] = None
        task = self._search_tasks[field]
        if task is not None and not task.done():
            task.cancel()
        self._search_generation[field] += 1
        generation = self._search_generation[field]
        query = query.strip()
        if not query:
            entry.set_completion_values(self._local_game_choices(field))
            return
        self._search_after_ids[field] = entry.after(
            250,
            lambda: self._start_game_search(field, query, generation),
        )

    def _start_game_search(self, field: str, query: str, generation: int) -> None:
        self._search_after_ids[field] = None
        self._search_tasks[field] = asyncio.create_task(
            self._fetch_game_choices(field, query, generation)
        )

    async def _fetch_game_choices(self, field: str, query: str, generation: int) -> None:
        try:
            games = await self._manager._twitch.search_games(query)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.debug("Game catalog search failed", exc_info=True)
            return
        if generation != self._search_generation[field]:
            return
        entry = self._game_entry(field)
        blocked = self._settings.priority if field == "priority" else self._settings.exclude
        choices = sorted(game.name for game in games if game.name not in blocked)
        entry.set_completion_values(choices)
        if not choices:
            entry._unpost_suggestions()

    def update_excluded_choices(self) -> None:
        self._exclude_entry.set_completion_values(self._local_game_choices("exclude"))

    def update_priority_choices(self) -> None:
        self._priority_entry.set_completion_values(self._local_game_choices("priority"))

    def set_games(self, games: set[Game]) -> None:
        self._game_names.update(game.name for game in games)
        self.update_excluded_choices()
        self.update_priority_choices()

    def reload_priority_list(self) -> None:
        self._priority_list.delete(0, "end")
        self._priority_list.insert("end", *self._settings.priority)
        self.update_priority_choices()

    def reload_exclude_list(self) -> None:
        self._exclude_list.delete(0, "end")
        self._exclude_list.insert("end", *sorted(self._settings.exclude))
        self.update_excluded_choices()

    def _apply_mining_lists_changed(self) -> None:
        # Same effect as inventory card toggles: refresh UI + recompute wanted games
        self._manager.inv.refresh()
        self._manager._twitch.change_state(State.GAMES_UPDATE)

    def priority_add(self) -> None:
        game_name: str = self._priority_entry.get()
        if not game_name:
            # prevent adding empty strings
            return
        self._priority_entry.clear()
        # add it preventing duplicates
        try:
            existing_idx: int = self._settings.priority.index(game_name)
        except ValueError:
            # not there, add it
            self._priority_list.insert("end", game_name)
            self._priority_list.see("end")
            self._settings.priority.append(game_name)
            # Priority wins over exclude (same as inventory card buttons)
            if game_name in self._settings.exclude:
                self._settings.exclude.discard(game_name)
                self.reload_exclude_list()
            self._settings.persist()
            self.update_priority_choices()
            self._apply_mining_lists_changed()
        else:
            # already there, set the selection on it
            self._priority_list.selection_set(existing_idx)
            self._priority_list.see(existing_idx)

    def _priority_idx(self) -> int | None:
        selection: tuple[int, ...] = self._priority_list.curselection()
        if not selection:
            return None
        return selection[0]

    def priority_move(self, amount: int) -> None:
        # amount > 0 = up, amount < 0 = down
        idx: int | None = self._priority_idx()
        max_idx: int = self._priority_list.size() - 1
        if (
            idx is None
            or amount == 0
            or amount > 0 and idx == 0
            or amount < 0 and idx == max_idx
        ):
            return
        insert_idx: int = idx - amount
        if insert_idx <= 0:
            insert_idx = 0
        elif insert_idx >= max_idx:
            insert_idx = max_idx

        item: str = self._priority_list.get(idx)
        self._priority_list.delete(idx)
        self._priority_list.insert(insert_idx, item)
        # reselect the item and scroll the list if needed
        self._priority_list.selection_set(insert_idx)
        self._priority_list.see(insert_idx)
        # update the underlying settings list too
        self._settings.priority.pop(idx)
        self._settings.priority.insert(insert_idx, item)
        self._settings.persist()
        self._apply_mining_lists_changed()

    def priority_delete(self) -> None:
        idx: int | None = self._priority_idx()
        if idx is None:
            return
        self._priority_list.delete(idx)
        del self._settings.priority[idx]
        self._settings.persist()
        self.update_priority_choices()
        self._apply_mining_lists_changed()

    def priority_mode(self, event: tk.Event[ttk.Combobox]) -> None:
        mode_name: str = self._vars["priority_mode"].get()
        for value, name in self.PRIORITY_MODES.items():
            if mode_name == name:
                if self._settings.priority_mode is value:
                    return
                self._settings.priority_mode = value
                self._apply_mining_lists_changed()
                break

    def exclude_add(self) -> None:
        game_name: str = self._exclude_entry.get()
        if not game_name:
            # prevent adding empty strings
            return
        self._exclude_entry.clear()
        if game_name not in self._settings.exclude:
            self._settings.exclude.add(game_name)
            # Exclude removes from priority (same as inventory card buttons)
            if game_name in self._settings.priority:
                self._settings.priority.remove(game_name)
                self.reload_priority_list()
            self._settings.persist()
            self.update_excluded_choices()
            # insert it alphabetically
            for i, item in enumerate(self._exclude_list.get(0, "end")):
                if game_name < item:
                    self._exclude_list.insert(i, game_name)
                    self._exclude_list.see(i)
                    break
            else:
                self._exclude_list.insert("end", game_name)
                self._exclude_list.see("end")
            self._apply_mining_lists_changed()
        else:
            # it was already there, select it
            for i, item in enumerate(self._exclude_list.get(0, "end")):
                if item == game_name:
                    existing_idx = i
                    break
            else:
                # something went horribly wrong and it's not there after all - just return
                return
            self._exclude_list.selection_set(existing_idx)
            self._exclude_list.see(existing_idx)

    def exclude_delete(self) -> None:
        selection: tuple[int, ...] = self._exclude_list.curselection()
        if not selection:
            return None
        idx: int = selection[0]
        item: str = self._exclude_list.get(idx)
        if item in self._settings.exclude:
            self._exclude_list.delete(idx)
            self._settings.exclude.discard(item)
            self._settings.persist()
            self.update_excluded_choices()
            self._apply_mining_lists_changed()

class HelpTab:
    WIDTH = 800

    def __init__(self, manager: GUIManager, master: tk.Misc):
        self._twitch = manager._twitch
        master.rowconfigure(0, weight=1)
        master.columnconfigure(0, weight=1)
        center_frame = ctk.CTkScrollableFrame(master, fg_color="transparent")
        center_frame.grid(column=0, row=0, sticky="nsew")
        irow = 0
        # About
        about_section = SectionFrame(center_frame, "About")
        about_section.grid(column=0, row=(irow := irow + 1), sticky="nsew", padx=2, pady=(0, 8))
        about = about_section.body
        about.columnconfigure(2, weight=1)
        # About - created by
        ttk.Label(
            about, text="Application created by: ", anchor="e"
        ).grid(column=0, row=0, sticky="nsew")
        LinkLabel(
            about, link="https://github.com/DevilXD", text="DevilXD"
        ).grid(column=1, row=0, sticky="nsew")
        # About - fork maintainer
        ttk.Label(about, text="Fork maintained by: ", anchor="e").grid(
            column=0, row=1, sticky="nsew"
        )
        LinkLabel(
            about, link=f"https://github.com/{FORK_OWNER}", text=FORK_OWNER
        ).grid(column=1, row=1, sticky="nsew")
        # About - fork repository
        ttk.Label(about, text="Fork repository: ", anchor="e").grid(
            column=0, row=2, sticky="nsew"
        )
        LinkLabel(about, link=FORK_REPOSITORY, text=FORK_REPOSITORY).grid(
            column=1, row=2, sticky="nsew"
        )
        # About - original repository
        ttk.Label(about, text="Original repository: ", anchor="e").grid(
            column=0, row=3, sticky="nsew"
        )
        LinkLabel(about, link=ORIGINAL_REPOSITORY, text=ORIGINAL_REPOSITORY).grid(
            column=1, row=3, sticky="nsew"
        )
        # About - donate
        ttk.Separator(
            about, orient="horizontal"
        ).grid(column=0, row=4, columnspan=3, sticky="nsew")
        ttk.Label(about, text="Donate to the original author: ", anchor="e").grid(
            column=0, row=5, sticky="nsew"
        )
        LinkLabel(
            about,
            link="https://www.buymeacoffee.com/DevilXD",
            text=(
                "If you like the application and found it useful, "
                "please consider donating a small amount of money to support me. Thank you!"
            ),
            wraplength=self.WIDTH,
        ).grid(column=1, row=5, sticky="nsew")
        # Useful links
        links_section = SectionFrame(center_frame, _("gui", "help", "links", "name"))
        links_section.grid(column=0, row=(irow := irow + 1), sticky="nsew", padx=2, pady=(0, 8))
        links = links_section.body
        LinkLabel(
            links,
            link="https://www.twitch.tv/drops/inventory",
            text=_("gui", "help", "links", "inventory"),
        ).grid(column=0, row=0, sticky="nsew")
        LinkLabel(
            links,
            link="https://www.twitch.tv/drops/campaigns",
            text=_("gui", "help", "links", "campaigns"),
        ).grid(column=0, row=1, sticky="nsew")
        LinkLabel(
            links,
            link=f"{FORK_REPOSITORY}/releases/tag/dev-build",
            text=_("gui", "help", "links", "releases"),
        ).grid(column=0, row=2, sticky="nsew")
        LinkLabel(
            links,
            link=(
                f"https://raw.githubusercontent.com/{FORK_OWNER}/"
                "TwitchDropsMiner/master/gql_overrides.json"
            ),
            text=_("gui", "help", "links", "gql_overrides"),
        ).grid(column=0, row=3, sticky="nsew")
        # How It Works
        how_section = SectionFrame(center_frame, _("gui", "help", "how_it_works"))
        how_section.grid(column=0, row=(irow := irow + 1), sticky="nsew", padx=2, pady=(0, 8))
        ctk.CTkLabel(
            how_section.body,
            text=_("gui", "help", "how_it_works_text"),
            wraplength=self.WIDTH,
            justify="left",
            anchor="w",
        ).grid(sticky="nsew")
        start_section = SectionFrame(center_frame, _("gui", "help", "getting_started"))
        start_section.grid(column=0, row=(irow := irow + 1), sticky="nsew", padx=2, pady=(0, 8))
        ctk.CTkLabel(
            start_section.body,
            text=_("gui", "help", "getting_started_text"),
            wraplength=self.WIDTH,
            justify="left",
            anchor="w",
        ).grid(sticky="nsew")

##########################################
# GUI DEFINITION END / GUI MANAGER START #
##########################################

class GUIManager:
    def __init__(self, twitch: Twitch):
        self._twitch: Twitch = twitch
        self._poll_task: asyncio.Task[NoReturn] | None = None
        self._close_requested = asyncio.Event()
        # Bumps on every live theme flip; Main re-paints when shown if behind.
        self._theme_epoch = 0
        self._main_theme_epoch = 0
        # Apply appearance before creating CTk widgets
        apply_appearance(bool(twitch.settings.dark_mode))
        self._theme: Theme = get_theme()
        self._root = root = ctk.CTk()
        # withdraw immediately to prevent the window from flashing
        self._root.withdraw()
        set_root_icon(root, resource_path("icons/pickaxe.ico"))
        root.title(WINDOW_TITLE)
        root.configure(fg_color=self._theme.bg)
        root.bind_all("<KeyPress-Escape>", self.unfocus)
        self._cache = ImageCache(self)

        # Keep a ttk.Style for Treeview / remaining ttk widgets
        self._style = style = ttk.Style(root)
        style.map(
            "Treeview",
            foreground=self._fixed_map("foreground"),
            background=self._fixed_map("background"),
        )
        style.configure("green.TLabel", foreground=self._theme.success)
        style.configure("yellow.TLabel", foreground=self._theme.warning)
        style.configure("red.TLabel", foreground=self._theme.danger)
        self._fonts: dict[str, Font] = {}

        root_frame = ctk.CTkFrame(root, fg_color="transparent")
        root_frame.grid(column=0, row=0, sticky="nsew", padx=8, pady=8)
        root.rowconfigure(0, weight=1)
        root.columnconfigure(0, weight=1)
        self.tabs = Notebook(self, root_frame)
        self.tray = TrayIcon(self, root_frame)

        self._main_frame = main_frame = self.tabs.add_tab(name=_("gui", "tabs", "main"))
        main_frame.columnconfigure(2, weight=1)
        main_frame.rowconfigure(3, weight=1)
        self.status = StatusBar(self, main_frame)
        self.websockets = WebsocketStatus(self, main_frame)
        self.login = LoginForm(self, main_frame)
        self.progress = CampaignProgress(self, main_frame)
        self.output = ConsoleOutput(self, main_frame)
        self.channels = ChannelList(self, main_frame)

        inv_frame = self.tabs.add_tab(name=_("gui", "tabs", "inventory"))
        self.inv = InventoryOverview(self, inv_frame)

        settings_frame = self.tabs.add_tab(name=_("gui", "tabs", "settings"))
        self.settings = SettingsPanel(self, settings_frame)

        help_frame = self.tabs.add_tab(name=_("gui", "tabs", "help"))
        self.help = HelpTab(self, help_frame)

        root.update_idletasks()
        root.minsize(width=max(920, root.winfo_reqwidth()), height=max(640, root.winfo_reqheight()))
        self._handler = _TKOutputHandler(self)
        self._handler.setLevel(logging.ERROR)
        self._handler.setFormatter(OUTPUT_FORMATTER)
        logger = logging.getLogger("TwitchDrops")
        logger.addHandler(self._handler)
        logger.info("Logging: full DEBUG trail → log.txt; journal shows ERROR+ only")
        if sys.platform == "win32":
            root.update()
            self._message_map = {
                win32con.WM_CLOSE: self.close,
                win32con.WM_QUERYENDSESSION: self.close,
            }
            self.old_wnd_proc = win32gui.SetWindowLong(
                self._handle, win32con.GWL_WNDPROC, self.wnd_proc
            )
            ctypes.windll.user32.ShutdownBlockReasonCreate(
                self._handle, ctypes.c_wchar_p(_("gui", "status", "exiting"))
            )
        else:
            root.protocol("WM_DELETE_WINDOW", self.close)
            root.protocol("WM_DESTROY_WINDOW", self.close)
        self._orig_theme_name = ''
        self.apply_theme(self._twitch.settings.dark_mode)
        if self._twitch.settings.tray and sys.platform != "darwin":
            self._root.after_idle(self.tray.minimize)
        else:
            self._root.after_idle(self._root.deiconify)

    # https://stackoverflow.com/questions/56329342/tkinter-treeview-background-tag-not-working
    def _fixed_map(self, option):
        # Fix for setting text colour for Tkinter 8.6.9
        # From: https://core.tcl.tk/tk/info/509cafafae
        #
        # Returns the style map for 'option' with any styles starting with
        # ('!disabled', '!selected', ...) filtered out.

        # style.map() returns an empty list for missing options, so this
        # should be future-safe.
        return [
            elm for elm in self._style.map("Treeview", query_opt=option)
            if elm[:2] != ("!disabled", "!selected")
        ]

    def wnd_proc(self, hwnd, msg, w_param, l_param):
        """
        This function serves as a message processor for all messages sent
        to the application by Windows.
        """
        if msg == win32con.WM_DESTROY:
            win32api.SetWindowLong(self._handle, win32con.GWL_WNDPROC, self.old_wnd_proc)
        if msg in self._message_map:
            return self._message_map[msg](w_param, l_param)
        return win32gui.CallWindowProc(self.old_wnd_proc, hwnd, msg, w_param, l_param)

    @cached_property
    def _handle(self) -> int:
        return int(self._root.wm_frame(), 16)

    @property
    def running(self) -> bool:
        return self._poll_task is not None

    @property
    def close_requested(self) -> bool:
        return self._close_requested.is_set()

    async def wait_until_closed(self):
        # wait until the user closes the window
        await self._close_requested.wait()

    async def coro_unless_closed(self, coro: abc.Awaitable[_T]) -> _T:
        # In Python 3.11, we need to explicitly wrap awaitables
        tasks = [asyncio.ensure_future(coro), asyncio.ensure_future(self._close_requested.wait())]
        done: set[asyncio.Task[Any]]
        pending: set[asyncio.Task[Any]]
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            task.cancel()
        if self._close_requested.is_set():
            raise ExitRequest()
        return await next(iter(done))

    def prevent_close(self):
        self._close_requested.clear()

    def start(self):
        if self._poll_task is None:
            self._poll_task = asyncio.create_task(self._poll())
        # self.progress.start_timer()

    def stop(self):
        self.progress.stop_timer()
        if self._poll_task is not None:
            self._poll_task.cancel()
            self._poll_task = None

    async def _poll(self):
        """
        This runs the Tkinter event loop via asyncio instead of calling mainloop.
        0.05s gives similar performance and CPU usage.
        Not ideal, but the simplest way to avoid threads, thread safety,
        loop.call_soon_threadsafe, futures and all of that.

        Uses TKINTER_DONT_WAIT to prevent Tcl/Tk from hanging inside native
        system calls (e.g. X11/Wayland input contexts) during heavy UI redraws.
        """
        do_one_event = self._root.dooneevent
        # TKINTER_DONT_WAIT (1 << 1) tells Tcl to return immediately
        # if no events are ready in the queue.
        DONT_WAIT = 1 << 1

        while True:
            try:
                # Drain pending Tk events non-blockingly
                while do_one_event(DONT_WAIT):
                    pass
            except tk.TclError:
                # Root window was destroyed
                break
            await asyncio.sleep(0.05)

        self._poll_task = None

    def close(self, *args) -> int:
        """
        Requests the GUI application to close.
        The window itself will be closed in the closing sequence later.
        """
        self._close_requested.set()
        # notify client we're supposed to close
        self._twitch.close()
        return 0

    def close_window(self):
        """
        Closes the window. Invalidates the logger.
        """
        self.tray.stop()
        logging.getLogger("TwitchDrops").removeHandler(self._handler)
        self._root.destroy()

    def unfocus(self, event):
        # support pressing ESC to unfocus
        self._root.focus_set()
        self.channels.clear_selection()
        self.settings.clear_selection()

    # these are here to interface with underlaying GUI components
    def save(self, *, force: bool = False) -> None:
        self._cache.save(force=force)

    def grab_attention(self, *, sound: bool = True):
        self.tray.restore()
        self._root.focus_set()
        if sound:
            self._root.bell()

    def set_games(self, games: set[Game]) -> None:
        self.settings.set_games(games)

    def display_drop(
        self, drop: TimedDrop, *, countdown: bool = True, subone: bool = False
    ) -> None:
        self.progress.display(drop, countdown=countdown, subone=subone)  # main tab
        # inventory overview is updated from within drops themselves via change events
        self.tray.update_title(drop)  # tray

    def clear_drop(self):
        self.progress.display(None)
        self.tray.update_title(None)

    def print(self, message: str):
        # Status / info: background log.txt only (GUI journal is ERROR+)
        logging.getLogger("TwitchDrops").info(message)

    def print_error(self, message: str):
        # Critical: file + GUI journal
        logging.getLogger("TwitchDrops").error(message)

    def _set_title_bar_theme(self, *, dark: bool) -> None:
        """Match Windows caption + caption text to the active theme (Win11+)."""
        if sys.platform != "win32":
            return
        # DWMWA_USE_IMMERSIVE_DARK_MODE = 20
        # DWMWA_CAPTION_COLOR = 35 (COLORREF 0x00BBGGRR)
        # DWMWA_TEXT_COLOR = 36
        hwnd = self._root.winfo_id()
        frame_hwnd = ctypes.windll.user32.GetParent(hwnd)
        immersive = ctypes.c_int(1 if dark else 0)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(
            frame_hwnd,
            20,
            ctypes.byref(immersive),
            ctypes.sizeof(ctypes.c_int),
        )
        # Dark: #1e1e1e, Light: #f2f2f4
        caption = ctypes.c_int(0x001E1E1E if dark else 0x00F4F2F2)
        text = ctypes.c_int(0x00FFFFFF if dark else 0x00101010)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(
            frame_hwnd,
            35,
            ctypes.byref(caption),
            ctypes.sizeof(ctypes.c_int),
        )
        ctypes.windll.dwmapi.DwmSetWindowAttribute(
            frame_hwnd,
            36,
            ctypes.byref(text),
            ctypes.sizeof(ctypes.c_int),
        )

    def _refresh_ctk_tree(
        self,
        widget: tk.Misc,
        theme: Theme,
        *,
        _skip: set[int] | None = None,
    ) -> None:
        """Push theme tokens into already-created CTk widgets (they keep create-time colors)."""
        # Skip inventory card pools — themed via _theme_card / rebuild on flip.
        if _skip is None:
            _skip = set()
            with suppress(Exception):
                for vlist in (
                    getattr(self.inv, "_filtered_list", None),
                    getattr(self.inv, "_active_list", None),
                ):
                    if vlist is None:
                        continue
                    _skip.add(id(vlist.canvas))
                    _skip.add(id(vlist.inner))
        if id(widget) in _skip:
            return
        try:
            children = widget.winfo_children()
        except Exception:
            return
        for child in children:
            if id(child) in _skip:
                continue
            try:
                if isinstance(child, SectionFrame):
                    child.apply_theme(theme, page_bg=theme.bg)
                elif isinstance(child, ctk.CTkTabview):
                    child.configure(
                        fg_color=theme.bg,
                        segmented_button_fg_color=theme.surface,
                        segmented_button_selected_color=theme.accent,
                        segmented_button_selected_hover_color=theme.accent_hover,
                        segmented_button_unselected_color=theme.surface_alt,
                        segmented_button_unselected_hover_color=theme.border,
                        text_color=theme.fg,
                    )
                elif isinstance(child, ctk.CTkScrollableFrame):
                    # Avoid white "holes" when appearance flips — match window bg.
                    child.configure(fg_color=theme.bg)
                    scrollbar = getattr(child, "_scrollbar", None)
                    if scrollbar is not None:
                        scrollbar.configure(
                            fg_color=theme.scroll_trough,
                            button_color=theme.scroll_thumb,
                            button_hover_color=theme.scroll_active,
                        )
                elif isinstance(child, ctk.CTkCheckBox):
                    # Unchecked box fill follows bg_color — keep it on surface (not CTk Light white).
                    child.configure(
                        fg_color=theme.accent,
                        hover_color=theme.accent_hover,
                        border_color=theme.muted,
                        border_width=2,
                        text_color=theme.fg,
                        checkmark_color="#ffffff",
                        bg_color=theme.surface,
                    )
                elif isinstance(child, ctk.CTkSwitch):
                    child.configure(
                        progress_color=theme.accent,
                        fg_color=theme.border,
                        border_color=theme.border,
                        button_color="#ffffff",
                        button_hover_color="#efeff1",
                    )
                elif isinstance(child, ctk.CTkEntry):
                    child.configure(
                        fg_color=theme.field_bg,
                        border_color=theme.border,
                        text_color=theme.fg,
                        placeholder_text_color=theme.muted,
                    )
                elif isinstance(child, ctk.CTkComboBox):
                    child.configure(
                        fg_color=theme.surface_alt,
                        border_color=theme.border,
                        button_color=theme.accent,
                        button_hover_color=theme.accent_hover,
                        text_color=theme.fg,
                    )
                elif isinstance(child, ctk.CTkProgressBar):
                    child.configure(
                        fg_color=theme.progress_trough,
                        progress_color=theme.accent,
                    )
                elif isinstance(child, ctk.CTkButton):
                    text_color = str(child.cget("text_color")).casefold()
                    if "ffffff" in text_color or text_color in {"white", "#fff"}:
                        child.configure(
                            fg_color=theme.accent,
                            hover_color=theme.accent_hover,
                            text_color="#ffffff",
                        )
                    else:
                        child.configure(
                            fg_color=theme.surface_alt,
                            hover_color=theme.border,
                            text_color=theme.fg,
                            border_color=theme.border,
                        )
                elif isinstance(child, ctk.CTkLabel):
                    # Keep explicit labels readable; muted/semantic titles stay semantic.
                    current = str(child.cget("text_color")).casefold()
                    muted_set = {
                        "#adadb8",
                        "#53535f",
                        "#5a5a66",
                        theme.muted.casefold(),
                    }
                    semantic = {
                        "#00f593",
                        "#eab308",
                        "#eb0400",
                        "#0a7b46",
                        "#a16207",
                        "#c41e1e",
                        "#cf0e0e",
                        "#f87171",
                        "#fbbf24",
                        "#c2410c",
                        "#9a3412",
                        theme.success.casefold(),
                        theme.warning.casefold(),
                        theme.danger.casefold(),
                        theme.warn_title.casefold(),
                        theme.warn_body.casefold(),
                        "green",
                        "red",
                        "goldenrod",
                    }
                    if current in muted_set or "muted" in current:
                        child.configure(text_color=theme.muted)
                    elif current not in semantic:
                        child.configure(text_color=theme.fg)
                elif isinstance(child, ThemedButton):
                    child.apply_theme(theme)
            except Exception:
                pass
            self._refresh_ctk_tree(child, theme, _skip=_skip)

    def reassert_main_theme(self) -> None:
        """Re-apply Main-tab colors after Inventory unmap + live theme flip."""
        if self._main_theme_epoch == self._theme_epoch:
            return
        theme = self._theme
        # CTk cards on Main can stay on the previous palette until remapped.
        self._refresh_ctk_tree(self._main_frame, theme)
        self.output.configure_theme(
            bg=theme.field_bg,
            fg=theme.fg,
            sel_bg=theme.sel_bg,
            sel_fg=theme.sel_fg,
            trough=theme.scroll_trough,
            surface=theme.scroll_thumb,
        )
        self.channels.configure_theme(theme)
        self._main_theme_epoch = self._theme_epoch
        with suppress(Exception):
            self._root.update_idletasks()

    def apply_theme(self, dark: bool) -> None:
        """Apply CustomTkinter appearance + ttk leftovers with Twitch palette."""
        # Keep inventory cards mounted across appearance flips — hibernate+recreate
        # while unmapped caused the black/white flash on the next Inventory enter.
        # cascade=False: do not run CTk's global Light/Dark redraw (transformer effect).
        # We push absolute Twitch hex colors below instead.
        theme = apply_appearance(dark, cascade=False)
        self._theme = theme
        self._theme_epoch += 1
        # Full tree refresh below already paints Main — skip deferred reassert flash.
        self._main_theme_epoch = self._theme_epoch
        if sys.platform != "darwin" and self._style.theme_use() != "clam":
            self._style.theme_use("clam")
        bg = theme.bg
        fg = theme.fg
        sel_bg = theme.sel_bg
        sel_fg = theme.sel_fg
        link = theme.link
        surface = theme.surface
        header = theme.surface_alt
        fieldbg = theme.field_bg
        border = theme.border
        muted = theme.muted
        accent = theme.accent
        try:
            self._root.configure(fg_color=bg)
        except Exception:
            pass

        # Setting theme for macOS
        if sys.platform == "darwin":
            app = AppKit.NSApplication.sharedApplication()
            if dark:
                appearance = AppKit.NSAppearance.appearanceNamed_(AppKit.NSAppearanceNameDarkAqua)
            else:
                appearance = AppKit.NSAppearance.appearanceNamed_(AppKit.NSAppearanceNameAqua)
            app.setAppearance_(appearance)

        # Windows title bar (caption + text)
        self._set_title_bar_theme(dark=dark)

        # Recolor in place — no CTk appearance cascade, no widget destroy/recreate.
        self.tabs.configure_theme(theme)
        self._refresh_ctk_tree(self._root, theme)
        self.settings.configure_theme(theme)

        s = self._style
        # Fonts
        default_font = nametofont("TkDefaultFont")
        self._fonts["default"] = default_font
        # Font - button style with a larger font
        self._fonts["large"] = default_font.copy()
        self._fonts["large"].config(size=10)
        s.configure("Large.TButton", font=self._fonts["large"])
        # Font - button style for sorting arrows
        self._fonts["arrow"] = default_font.copy()
        self._fonts["arrow"].config(size=16)
        s.configure("Arrow.TButton", font=self._fonts["arrow"])
        s.configure("Arrow.TButton", padding=-4)  # reduce padding on arrow buttons
        # Font - label style that mimics links
        self._fonts["underlined"] = default_font.copy()
        self._fonts["underlined"].config(underline=True)
        s.configure(
            "Link.TLabel",
            font=self._fonts["underlined"],
            foreground=link,
            background=surface,
        )
        # Font - label style with a monospace font
        self._fonts["monospaced"] = default_font.copy()
        self._fonts["monospaced"].config(family="Courier New", size=10)
        s.configure("MS.TLabel", font=self._fonts["monospaced"])

        # ttk labels/frames sit inside CTk SectionFrames (surface) — match surface to
        # avoid opaque "black slabs" when window bg differs from card bg.
        s.configure("TFrame", background=surface, foreground=fg)
        s.configure("TLabel", background=surface, foreground=fg)
        s.configure("TLabelframe", background=surface, foreground=fg)
        s.configure("TLabelframe.Label", background=surface, foreground=fg)
        s.configure("green.TLabel", background=surface, foreground=theme.success)
        s.configure("yellow.TLabel", background=surface, foreground=theme.warning)
        s.configure("red.TLabel", background=surface, foreground=theme.danger)
        # Labels inside inventory drop cards (surface_alt) — avoid light native slabs
        s.configure("Card.TLabel", background=header, foreground=fg)
        s.configure("TSeparator", background=border)
        # Buttons and checks
        s.configure("TButton", background=surface, foreground=fg, bordercolor=border)
        s.map(
            "TButton",
            background=[("active", header), ("pressed", border)],
            foreground=[("disabled", muted)],
        )
        s.configure(
            "TCheckbutton",
            background=surface,
            foreground=fg,
            focuscolor=surface,
            bordercolor=border,
        )
        s.map(
            "TCheckbutton",
            # Remove hover visuals by mapping active/pressed to the base background
            background=[
                ("active", surface),
                ("pressed", surface),
            ],
            foreground=[("disabled", muted)],
            indicatorcolor=[
                ("selected", accent if dark else fg),
                ("!selected", border),
            ],
        )
        # Notebook
        s.configure("TNotebook", background=bg, bordercolor=border)
        s.configure("TNotebook.Tab", background=surface, foreground=fg, bordercolor=border)
        s.map(
            "TNotebook.Tab",
            background=[("selected", header), ("active", header)],
            foreground=[("disabled", muted)],
        )
        # Entries/Combos
        s.configure(
            "TEntry", fieldbackground=fieldbg, background=fieldbg, foreground=fg, insertcolor=fg
        )
        s.configure(
            "TCombobox", fieldbackground=fieldbg, background=fieldbg, foreground=fg, arrowcolor=fg
        )
        # Ensure readability for readonly comboboxes (Language, Priority mode)
        s.map(
            "TCombobox",
            foreground=[("readonly", fg), ("!disabled", fg), ("disabled", muted)],
            fieldbackground=[
                ("readonly", fieldbg),
                ("!disabled", fieldbg),
                ("disabled", surface),
            ],
            background=[("readonly", fieldbg), ("!disabled", fieldbg)],
            arrowcolor=[("readonly", fg), ("!disabled", fg)],
        )
        s.map(
            "TEntry",
            foreground=[("disabled", muted), ("!disabled", fg)],
            fieldbackground=[("!disabled", fieldbg), ("disabled", surface)],
        )
        # Treeview — drop native border element so Windows won't paint a white field
        s.layout(
            "Treeview",
            [("Treeview.treearea", {"sticky": "nswe"})],
        )
        s.configure(
            "Treeview",
            background=surface,
            fieldbackground=surface,
            foreground=fg,
            bordercolor=border,
            borderwidth=0,
            relief="flat",
        )
        s.configure(
            "Channels.Treeview",
            background=surface,
            fieldbackground=surface,
            foreground=fg,
            bordercolor=border,
            borderwidth=0,
            relief="flat",
        )
        s.map(
            "Treeview",
            background=[("selected", sel_bg), ("!selected", surface)],
            foreground=[("selected", sel_fg), ("!selected", fg)],
        )
        s.map(
            "Channels.Treeview",
            background=[("selected", sel_bg), ("!selected", surface)],
            foreground=[("selected", sel_fg), ("!selected", fg)],
        )
        s.configure(
            "Treeview.Heading",
            background=header,
            foreground=fg,
            bordercolor=border,
            relief="flat",
        )
        s.map("Treeview.Heading", background=[("active", border)])
        # Keep tag colors working under Tk 8.6.9+ (re-apply after style.configure)
        s.map(
            "Treeview",
            foreground=self._fixed_map("foreground"),
            background=self._fixed_map("background"),
        )
        s.map(
            "Channels.Treeview",
            foreground=self._fixed_map("foreground"),
            background=self._fixed_map("background"),
        )
        # Progressbar
        s.configure(
            "TProgressbar",
            background=accent,
            troughcolor=theme.progress_trough,
        )
        # Scrollbars (fallback for any remaining ttk bars)
        s.configure(
            "Vertical.TScrollbar",
            background=theme.scroll_thumb,
            troughcolor=theme.scroll_trough,
            arrowcolor=fg,
            bordercolor=theme.scroll_trough,
            darkcolor=theme.scroll_thumb,
            lightcolor=theme.scroll_thumb,
            gripcount=0,
        )
        s.configure(
            "Horizontal.TScrollbar",
            background=theme.scroll_thumb,
            troughcolor=theme.scroll_trough,
            arrowcolor=fg,
            bordercolor=theme.scroll_trough,
            darkcolor=theme.scroll_thumb,
            lightcolor=theme.scroll_thumb,
            gripcount=0,
        )
        s.map(
            "Vertical.TScrollbar",
            background=[
                ("active", theme.scroll_active),
                ("pressed", theme.scroll_active),
            ],
        )
        s.map(
            "Horizontal.TScrollbar",
            background=[
                ("active", theme.scroll_active),
                ("pressed", theme.scroll_active),
            ],
        )

        # Pure Tk widgets
        # Console text + scrollbars
        self.output.configure_theme(
            bg=fieldbg,
            fg=fg,
            sel_bg=sel_bg,
            sel_fg=sel_fg,
            trough=theme.scroll_trough,
            surface=theme.scroll_thumb,
        )
        self.channels.configure_theme(theme)
        # Listboxes (field_bg reads as an input well on the card)
        self.settings._priority_list.configure_theme(
            bg=fieldbg, fg=fg, sel_bg=sel_bg, sel_fg=sel_fg
        )
        self.settings._exclude_list.configure_theme(
            bg=fieldbg, fg=fg, sel_bg=sel_bg, sel_fg=sel_fg
        )
        # Inventory chrome + cards rethemed in place under the new palette.
        self.inv.configure_theme(theme)

        # Tk option database for selection/popup list readability (affects Tk-backed widgets)
        # Global selection colors and listbox defaults (covers Combobox dropdown)
        self._root.option_add("*selectBackground", sel_bg)
        self._root.option_add("*selectForeground", sel_fg)
        # Combobox dropdown list (Tk Listbox)
        for key in (
            "*TCombobox*Listbox.background",
            "*TCombobox*Listbox.Background",
            "*Listbox.background",
        ):
            self._root.option_add(key, fieldbg)
        for key in (
            "*TCombobox*Listbox.foreground",
            "*TCombobox*Listbox.Foreground",
            "*Listbox.foreground",
        ):
            self._root.option_add(key, fg)
        for key in (
            "*TCombobox*Listbox.selectBackground",
            "*Listbox.selectBackground",
        ):
            self._root.option_add(key, sel_bg)
        for key in (
            "*TCombobox*Listbox.selectForeground",
            "*Listbox.selectForeground",
        ):
            self._root.option_add(key, sel_fg)

        # Re-assert caption after ttk/CTk churn (some Windows builds reset it).
        self._set_title_bar_theme(dark=dark)

###################
# GUI MANAGER END #
###################

if __name__ == "__main__":
    # Everything below is for debug purposes only
    import aiohttp
    from types import SimpleNamespace

    class StrNamespace(SimpleNamespace):
        __hash__ = object.__hash__  # type: ignore

        def __str__(self):
            if hasattr(self, "_str__"):
                return self._str__(self)
            return super().__str__()

    class HashNamespace(SimpleNamespace):
        __hash__ = object.__hash__  # type: ignore

    def create_game(id: int, name: str):
        return StrNamespace(name=name, id=id, _str__=lambda s: s.name)

    iid = 0

    def create_channel(
        name: str,
        status: int,
        game: str | None,
        drops: bool,
        viewers: int,
        acl_based: bool,
    ):
        # status: 0 -> OFFLINE, 1 -> PENDING_ONLINE, 2 -> ONLINE
        if status == 1:
            status = False
            pending = True
        else:
            pending = False
        if game is not None:
            game_obj: StrNamespace | None = create_game(0, game)
        else:
            game_obj = None
        global iid
        return SimpleNamespace(
            name=name,
            iid=(iid := iid + 1),
            online=bool(status),
            pending_online=pending,
            game=game_obj,
            drops_enabled=drops,
            viewers=viewers,
            acl_based=acl_based,
        )

    def create_drop(
        campaign_name: str,
        game_name: str,
        rewards: list[str],
        claimed_drops: int,
        total_drops: int,
        current_minutes: int,
        total_minutes: int,
    ):
        cd = claimed_drops
        td = total_drops
        cm = current_minutes
        tm = total_minutes
        ref_stamp = datetime.now(timezone.utc)
        drop_image_url = (
            "https://static-cdn.jtvnw.net/twitch-quests-assets/"
            "REWARD/e0ede26e-b071-47f0-af5f-b80b26fa9fb4.png"
        )
        campaign_image_url = "https://static-cdn.jtvnw.net/ttv-boxart/515025-120x160.jpg"
        benefits = [SimpleNamespace(name=name, image_url=drop_image_url) for name in rewards]
        mock = SimpleNamespace(
            id="0",
            campaign=HashNamespace(
                name=campaign_name,
                id="campaign",
                game=create_game(0, game_name),
                expired=False,
                active=False,
                upcoming=True,
                eligible=False,
                finished=False,
                link_url="https://google.com",
                image_url=campaign_image_url,
                allowed_channels=[],
                starts_at=ref_stamp,
                ends_at=ref_stamp + timedelta(days=7),
                timed_drops={},
                claimed_drops=cd,
                total_drops=td,
                required_minutes=tm,
                remaining_drops=td - cd,
                progress=(cd * tm + cm) / (td * tm),
                remaining_minutes=(td - cd) * tm - cm,
            ),
            image_url=drop_image_url,
            can_claim=False,
            can_earn=lambda: False,
            is_claimed=False,
            preconditions=True,
            benefits=benefits,
            rewards_text=lambda: ', '.join(b.name for b in benefits),
            starts_at=ref_stamp + timedelta(seconds=2),
            ends_at=ref_stamp + timedelta(days=7) - timedelta(seconds=2),
            progress=cm/tm,
            current_minutes=cm,
            required_minutes=tm,
            remaining_minutes=tm-cm,
        )
        mock.campaign.timed_drops["0"] = mock
        mock.campaign.drops = mock.campaign.timed_drops.values()
        return mock

    async def main(exit_event: asyncio.Event):
        # Initialize GUI debug
        mock = SimpleNamespace(
            settings=SimpleNamespace(
                tray=False,
                priority=[],
                proxy=URL(),
                dark_mode=False,
                alter=lambda: None,
                language="English",
                autostart_tray=False,
                exclude={"Lit Game"},
                tray_notifications=True,
                enable_badges_emotes=False,
                available_drops_check=False,
                logging_level=LOGGING_LEVELS[0],
                priority_mode=PriorityMode.PRIORITY_ONLY,
            )
        )
        mock.change_state = lambda state: mock.gui.print(f"State change: {state.value}")
        mock.state_change = lambda state: partial(mock.change_state, state)
        mock.request = aiohttp.request
        # _.set_language("Русский")
        gui = GUIManager(mock)  # type: ignore
        mock.gui = gui
        mock.close = gui.stop
        gui.start()
        assert gui._poll_task is not None
        gui._poll_task.add_done_callback(lambda t: exit_event.set())
        # Login form
        gui.login.update("Login required", None)
        # Game selector and settings panel games
        gui.set_games(set([
            create_game(420690, "Lit Game"),
            create_game(123456, "Best Game"),
            create_game(654321, "My Game Very Long Name"),
        ]))
        # Channel list
        gui.channels.display(
            create_channel(
                name="Thomus",
                status=0,
                game=None,
                drops=False,
                viewers=0,
                acl_based=True,
            ),
            add=True,
        )
        channel = create_channel(
            name="Traitus", status=1, game=None, drops=False, viewers=0, acl_based=True
        )
        gui.channels.display(channel, add=True)
        gui.channels.set_watching(channel)
        gui.channels.display(
            create_channel(
                name="Testus",
                status=2,
                game="Best Game",
                drops=True,
                viewers=42,
                acl_based=False,
            ),
            add=True,
        )
        gui.channels.display(
            create_channel(
                name="Livus",
                status=2,
                game="Best Game",
                drops=True,
                viewers=69,
                acl_based=False,
            ),
            add=True,
        )
        gui._root.update()
        gui.channels.get_selection()
        # Inventory overview
        drop = create_drop(
            "Wardrobe Cleaning", "Cleaning Masters", ["Fancy Pants"], 2, 7, 0, 240
        )
        campaign = drop.campaign
        await gui.inv.add_campaign(campaign)

        gui.print("Single-line test message")
        await asyncio.sleep(1)
        gui.print("Multi-line\ntest\nmessage")

        # Tray
        # gui.tray.minimize()
        await asyncio.sleep(2)
        claim_text = (
            f"{campaign.game.name}\n"
            f"{drop.rewards_text()} ({campaign.claimed_drops}/{campaign.total_drops})"
        )
        gui.tray.notify(claim_text, "Mined Drop")

        # Drop progress
        gui.display_drop(drop, countdown=False)
        await asyncio.sleep(3)

        gui.progress.start_timer()
        await asyncio.sleep(5)

        gui.clear_drop()
        await asyncio.sleep(5)

        campaign.can_earn = lambda: True
        gui.inv.update_drop(drop)
        gui.display_drop(drop)
        await asyncio.sleep(10)

        drop.current_minutes = 239
        drop.remaining_minutes = 1
        drop.progress = 239/240
        campaign.remaining_minutes -= 1
        gui.inv.update_drop(drop)
        gui.display_drop(drop)
        await asyncio.sleep(63)

        drop.current_minutes = 240
        drop.remaining_minutes = 0
        drop.progress = 1.0
        campaign.remaining_minutes -= 1
        campaign.progress = 3/7
        campaign.claimed_drops = 3
        campaign.remaining_drops = 4
        gui.inv.update_drop(drop)
        gui.display_drop(drop)

    def main_exit(task: asyncio.Task[None]) -> None:
        if task.exception() is not None:
            exit_event.set()

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    exit_event = asyncio.Event()
    main_task = loop.create_task(main(exit_event))
    main_task.add_done_callback(main_exit)
    loop.run_until_complete(exit_event.wait())
    if main_task.done():
        loop.run_until_complete(main_task)
