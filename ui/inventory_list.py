"""Viewport-virtualized campaign list for inventory scroll canvases."""

from __future__ import annotations

import tkinter as tk
from contextlib import suppress
from typing import TYPE_CHECKING, Any, Callable

from ui.theme import get_theme
from ui.widgets import FlatScrollbar

if TYPE_CHECKING:
    from inventory import DropsCampaign

class VirtualCampaignList:
    """
    Renders only ~POOL campaign cards for the current viewport.
    Cards are placed inside a tall inner frame; off-screen campaigns have no widgets.
    """

    POOL_SIZE = 14
    OVERSCAN = 1
    CARD_PAD_Y = 4
    DEFAULT_CARD_HEIGHT = 210

    def __init__(
        self,
        parent: tk.Misc,
        *,
        on_mousewheel: Callable[[tk.Event[tk.Misc]], Any],
        create_card: Callable[[tk.Misc, "DropsCampaign"], Any],
        bind_card: Callable[[Any, "DropsCampaign"], None],
        destroy_card: Callable[[Any], None],
        theme_card: Callable[[Any], None] | None = None,
    ):
        theme = get_theme()
        self._on_mousewheel = on_mousewheel
        self._create_card = create_card
        self._bind_card = bind_card
        self._destroy_card = destroy_card
        self._theme_card = theme_card
        # When True, freeze the pool (no create/cull). InventoryOverview thaws on tab enter.
        # Start suspended — page is built while Main is selected.
        self._suspended = True

        self._canvas = tk.Canvas(
            parent,
            scrollregion=(0, 0, 0, 0),
            highlightthickness=0,
            bd=0,
            bg=theme.bg,
            yscrollincrement=40,
        )
        self._canvas.grid(column=0, row=0, sticky="nsew")
        self._xscroll = FlatScrollbar(
            parent, orient="horizontal", command=self._canvas.xview
        )
        self._xscroll.grid(column=0, row=1, sticky="ew")
        self._yscroll = FlatScrollbar(
            parent, orient="vertical", command=self._on_scrollbar
        )
        self._yscroll.grid(column=1, row=0, sticky="ns")
        # Corner cell so Windows doesn't leave a system-colored hole
        self._corner = tk.Frame(parent, bg=theme.bg, highlightthickness=0, width=12, height=12)
        self._corner.grid(column=1, row=1, sticky="nsew")
        self._canvas.configure(
            xscrollcommand=self._xscroll.set, yscrollcommand=self._yscroll.set
        )

        self._inner = tk.Frame(self._canvas, bg=theme.bg, highlightthickness=0)
        self._window_id = self._canvas.create_window(0, 0, anchor="nw", window=self._inner)

        self._items: list[DropsCampaign] = []
        self._heights: dict[DropsCampaign, int] = {}
        self._offsets: list[int] = []
        self._total_height = 0

        # slot_index -> (display, campaign | None)
        self._slots: list[tuple[Any, DropsCampaign | None]] = []
        self._index_to_slot: dict[int, int] = {}
        self._update_job: str | None = None

        self._canvas.bind("<Configure>", self._on_canvas_configure, add="+")
        self._canvas.bind("<MouseWheel>", self._wheel, add="+")
        self._inner.bind("<MouseWheel>", self._wheel, add="+")

    @property
    def canvas(self) -> tk.Canvas:
        return self._canvas

    @property
    def inner(self) -> tk.Frame:
        return self._inner

    def configure_theme(
        self,
        *,
        bg: str,
        surface: str | None = None,
        border: str | None = None,
        cards: bool = True,
        rebuild: bool = False,
    ) -> None:
        theme = get_theme()
        del surface, border  # API kept for callers; scroll colors come from theme tokens
        self._canvas.configure(bg=bg)
        self._inner.configure(bg=bg)
        self._corner.configure(bg=theme.scroll_trough)
        self._xscroll.configure_theme(
            trough=theme.scroll_trough,
            thumb=theme.scroll_thumb,
            active=theme.scroll_active,
        )
        self._yscroll.configure_theme(
            trough=theme.scroll_trough,
            thumb=theme.scroll_thumb,
            active=theme.scroll_active,
        )
        if not cards:
            return
        if rebuild:
            # Theme flip: recreate pool so no leftover Light ttk slabs survive.
            items = list(self._items)
            self.hibernate(clear_heights=False)
            if items:
                self.set_items(items)
            return
        if self._theme_card is not None:
            for display, _campaign in self._slots:
                if display is not None:
                    with suppress(Exception):
                        self._theme_card(display)

    def clear(self) -> None:
        for display, _campaign in self._slots:
            if display is not None:
                self._destroy_card(display)
        self._slots.clear()
        self._index_to_slot.clear()
        self._items.clear()
        self._heights.clear()
        self._offsets = []
        self._total_height = 0
        self._inner.configure(width=1, height=1)
        self._canvas.configure(scrollregion=(0, 0, 0, 0))

    def set_items(self, campaigns: list[DropsCampaign]) -> None:
        self._items = list(campaigns)
        # Drop height cache for campaigns no longer present
        alive = set(self._items)
        for campaign in list(self._heights):
            if campaign not in alive:
                del self._heights[campaign]
        self._recompute_offsets()
        self._apply_scrollregion()
        self._schedule_visible_update()

    def invalidate_height(self, campaign: DropsCampaign) -> None:
        self._heights.pop(campaign, None)

    def visible_displays(self) -> list[tuple[DropsCampaign, Any]]:
        out: list[tuple[DropsCampaign, Any]] = []
        for display, campaign in self._slots:
            if display is not None and campaign is not None:
                out.append((campaign, display))
        return out

    def display_for(self, campaign: DropsCampaign) -> Any | None:
        for display, bound in self._slots:
            if display is not None and bound is campaign:
                return display
        return None

    def refresh_visible(self) -> None:
        """Re-run bind hooks for currently shown cards (status/images/buttons)."""
        for display, campaign in self._slots:
            if display is not None and campaign is not None:
                self._bind_card(display, campaign)

    def set_suspended(self, suspended: bool) -> None:
        """Freeze or thaw the card pool when the Inventory page is hidden/shown."""
        self._suspended = suspended
        if not suspended:
            self._schedule_visible_update()

    def hibernate(self, *, clear_heights: bool = False) -> None:
        """Destroy all pooled cards so a hidden tab stays cheap to show/hide."""
        if self._update_job is not None:
            with suppress(Exception):
                self._canvas.after_cancel(self._update_job)
            self._update_job = None
        for i in range(len(self._slots)):
            self._release_slot(i)
        self._slots.clear()
        self._index_to_slot.clear()
        if clear_heights:
            self._heights.clear()
            self._recompute_offsets()
            self._apply_scrollregion()

    def _on_scrollbar(self, *args: Any) -> None:
        self._canvas.yview(*args)
        self._schedule_visible_update()

    def _wheel(self, event: tk.Event[tk.Misc]) -> Any:
        result = self._on_mousewheel(event)
        self._schedule_visible_update()
        return result

    def _on_canvas_configure(self, event: tk.Event[tk.Misc]) -> None:
        # Keep inner at least as wide as the viewport for full-width cards.
        width = max(event.width, 1)
        self._canvas.itemconfigure(self._window_id, width=width)
        self._schedule_visible_update()

    def _height_for(self, campaign: DropsCampaign) -> int:
        return self._heights.get(campaign, self.DEFAULT_CARD_HEIGHT)

    def _recompute_offsets(self) -> None:
        self._offsets = []
        y = 0
        for campaign in self._items:
            self._offsets.append(y)
            y += self._height_for(campaign) + self.CARD_PAD_Y
        self._total_height = max(y, 1)

    def _apply_scrollregion(self) -> None:
        width = max(self._canvas.winfo_width(), self._inner.winfo_reqwidth(), 1)
        view_h = max(self._canvas.winfo_height(), 1)
        view_w = max(self._canvas.winfo_width(), 1)
        self._inner.configure(width=width, height=self._total_height)
        self._canvas.configure(scrollregion=(0, 0, width, self._total_height))
        # Hide scrollbars when content fits (few campaigns / narrow list).
        if self._total_height > view_h + 1:
            self._yscroll.grid(column=1, row=0, sticky="ns")
        else:
            self._yscroll.grid_remove()
            self._canvas.yview_moveto(0)
        if width > view_w + 1:
            self._xscroll.grid(column=0, row=1, sticky="ew")
            self._corner.grid(column=1, row=1, sticky="nsew")
        else:
            self._xscroll.grid_remove()
            self._corner.grid_remove()
            self._canvas.xview_moveto(0)

    def _schedule_visible_update(self) -> None:
        if self._update_job is not None:
            try:
                self._canvas.after_cancel(self._update_job)
            except Exception:
                pass
        self._update_job = self._canvas.after_idle(self._update_visible)

    def _visible_index_range(self) -> tuple[int, int]:
        if not self._items:
            return (0, -1)
        view_h = max(self._canvas.winfo_height(), 1)
        top = self._canvas.canvasy(0)
        bottom = top + view_h
        first = 0
        last = len(self._items) - 1
        for i, campaign in enumerate(self._items):
            y0 = self._offsets[i]
            y1 = y0 + self._height_for(campaign)
            if y1 >= top:
                first = i
                break
        for i in range(len(self._items) - 1, -1, -1):
            y0 = self._offsets[i]
            if y0 <= bottom:
                last = i
                break
        first = max(0, first - self.OVERSCAN)
        last = min(len(self._items) - 1, last + self.OVERSCAN)
        return first, last

    def _free_slot_index(self) -> int | None:
        for i, (display, campaign) in enumerate(self._slots):
            if display is None or campaign is None:
                return i
        if len(self._slots) < self.POOL_SIZE:
            self._slots.append((None, None))
            return len(self._slots) - 1
        return None

    def _release_slot(self, slot_i: int) -> None:
        display, campaign = self._slots[slot_i]
        if display is not None:
            self._destroy_card(display)
        self._slots[slot_i] = (None, None)
        for index, mapped in list(self._index_to_slot.items()):
            if mapped == slot_i:
                del self._index_to_slot[index]

    def _update_visible(self) -> None:
        self._update_job = None
        view_h = max(self._canvas.winfo_height(), 1)
        mapped = bool(self._canvas.winfo_ismapped())
        slots_before = sum(1 for d, c in self._slots if d is not None)
        # Freeze while Inventory page is hidden (explicit suspend). Do NOT use
        # winfo_ismapped() alone — nested CTk tabs report unmapped even when the
        # page is selected, which left filtered slots at 0 until re-enter (flash).
        if self._suspended:
            return
        if not self._items:
            for i in range(len(self._slots)):
                self._release_slot(i)
            self._apply_scrollregion()
            return

        first, last = self._visible_index_range()
        needed = set(range(first, last + 1)) if last >= first else set()

        released = 0
        created = 0
        # Release slots whose index left the window
        for index, slot_i in list(self._index_to_slot.items()):
            if index not in needed:
                self._release_slot(slot_i)
                released += 1

        for index in sorted(needed):
            if index in self._index_to_slot:
                slot_i = self._index_to_slot[index]
                display, bound = self._slots[slot_i]
                campaign = self._items[index]
                if bound is campaign and display is not None:
                    # Keep placement in sync when heights/offsets change
                    display["frame"].place(
                        x=0,
                        y=self._offsets[index],
                        relwidth=1.0,
                    )
                    # Re-assert colors — a theme flip can land while the slot stays
                    # bound; skipping bind left Light frames on Dark (user screenshots).
                    if self._theme_card is not None:
                        with suppress(Exception):
                            self._theme_card(display)
                    continue
                # Rebound existing slot
                if display is not None:
                    self._destroy_card(display)
                display = self._create_card(self._inner, campaign)
                self._slots[slot_i] = (display, campaign)
                self._bind_card(display, campaign)
                self._place_and_measure(display, campaign, index)
                created += 1
                continue

            slot_i = self._free_slot_index()
            if slot_i is None:
                # Evict farthest slot
                slot_i = self._evict_farthest(first, last)
                if slot_i is None:
                    continue
                self._release_slot(slot_i)
                released += 1

            campaign = self._items[index]
            display = self._create_card(self._inner, campaign)
            self._slots[slot_i] = (display, campaign)
            self._index_to_slot[index] = slot_i
            self._bind_card(display, campaign)
            self._place_and_measure(display, campaign, index)
            created += 1

        self._apply_scrollregion()

    def _evict_farthest(self, first: int, last: int) -> int | None:
        worst_slot = None
        worst_dist = -1
        mid = (first + last) / 2
        for index, slot_i in self._index_to_slot.items():
            dist = abs(index - mid)
            if dist > worst_dist:
                worst_dist = dist
                worst_slot = slot_i
        if worst_slot is not None:
            for index, mapped in list(self._index_to_slot.items()):
                if mapped == worst_slot:
                    del self._index_to_slot[index]
        return worst_slot

    def _place_and_measure(
        self,
        display: Any,
        campaign: DropsCampaign,
        index: int,
    ) -> None:
        frame = display["frame"]
        frame.place(x=0, y=self._offsets[index], relwidth=1.0)
        # Skip synchronous layout when we already measured this card — calling
        # update_idletasks per card is what freezes Main↔Inventory after theme flips.
        if campaign in self._heights:
            return
        frame.update_idletasks()
        measured = max(frame.winfo_reqheight(), 1)
        prev = self._heights.get(campaign)
        self._heights[campaign] = measured
        if prev != measured:
            self._recompute_offsets()
            # Re-place all visible after height change
            for idx, slot_i in self._index_to_slot.items():
                disp, bound = self._slots[slot_i]
                if disp is not None and bound is not None and idx < len(self._offsets):
                    disp["frame"].place(x=0, y=self._offsets[idx], relwidth=1.0)
