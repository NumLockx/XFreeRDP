#!/usr/bin/env python3
"""
tray.py — System tray support for XFreeRDP GUI via Ayatana AppIndicator.

Native GObject/Gtk implementation (no pystray). The Gtk mainloop runs in a
daemon thread; Tkinter code must never be touched from that thread, so all
menu callbacks are marshalled through the `marshal` callable handed to the
constructor (the app feeds them into a queue serviced by the Tk mainloop).
Conversely, menu updates flow the other way: `update_menu()` may be called
from the Tk thread and is dispatched onto the Gtk loop via GLib.idle_add.

Menu specs are plain data:
    spec = [
        {"label": "...", "cmd": "command-name", "enabled": True},
        {"sep": True},
        {"label": "...", "cmd": "..."},
    ]
Labels are escaped for Gtk markup (leading "_" suppressed, "&" stripped).
"""
from __future__ import annotations

from pathlib import Path
from threading import Thread
from typing import Any, Callable

try:
    import gi
    gi.require_version("AyatanaAppIndicator3", "0.1")
    gi.require_version("Gtk", "3.0")
    from gi.repository import AyatanaAppIndicator3 as AI, Gtk, GLib
    TRAY_AVAILABLE = True
except Exception:  # missing gi, missing typelibs — degrade gracefully
    AI = None  # type: ignore[assignment]
    Gtk = None  # type: ignore[assignment]
    GLib = None  # type: ignore[assignment]
    TRAY_AVAILABLE = False


def _escape_label(label: str) -> str:
    # Avoid accidental mnemonics/mangling; Gtk.MenuItem.get_child labels are
    # plain text unless using markup, but underscores would read as mnemonics.
    return label.replace("_", "")


class TrayIcon:
    """A single status-notifier icon with a rebuildable menu."""

    def __init__(self, app_id: str, icon_path: Path | None,
                 tooltip: str, marshal: Callable[[Callable[[], None]], None],
                 icon_fallback: str = "utilities-terminal"):
        if not TRAY_AVAILABLE:
            raise RuntimeError("tray.py: Ayatana AppIndicator not available")
        self._marshal = marshal
        self._commands: dict[str, Callable[[], None]] = {}
        self._spec: list[dict[str, Any]] = []

        icon_arg = str(icon_path) if icon_path and Path(icon_path).exists() \
            else icon_fallback
        self._ind = AI.Indicator.new(app_id, icon_arg,
                                     AI.IndicatorCategory.APPLICATION_STATUS)
        self._ind.set_status(AI.IndicatorStatus.ACTIVE)
        self._ind.set_title(tooltip)
        self._menu = Gtk.Menu()
        self._ind.set_menu(self._menu)

        self._thread = Thread(target=Gtk.main, daemon=True, name="xfreerdp-gtk")
        self._thread.start()

    # ── Called from the Tk thread ──────────────────────────────────────
    def update_menu(self, spec: list[dict[str, Any]],
                    commands: dict[str, Callable[[], None]]) -> None:
        """Replace the whole menu. Safe to call from any thread."""
        self._spec = spec
        self._commands = commands
        GLib.idle_add(self._rebuild)

    def stop(self) -> None:
        GLib.idle_add(Gtk.main_quit)

    # ── Gtk-loop side ──────────────────────────────────────────────────
    def _rebuild(self) -> bool:
        for child in self._menu.get_children():
            self._menu.remove(child)

        for entry in self._spec:
            if entry.get("sep"):
                self._menu.append(Gtk.SeparatorMenuItem())
                continue
            label = _escape_label(entry.get("label", ""))
            item = Gtk.MenuItem(label=label)
            cmd = entry.get("cmd")
            enabled = entry.get("enabled", True)
            item.set_sensitive(enabled)
            if cmd and enabled:
                handler = self._make_handler(cmd)
                item.connect("activate", handler)
            self._menu.append(item)

        self._menu.show_all()
        return False  # one-shot idle callback

    def _make_handler(self, cmd: str) -> Callable[[Any], None]:
        fn = self._commands.get(cmd)
        if fn is None:
            return lambda *_: None
        return lambda _menu_item: self._marshal(fn)
