#!/usr/bin/env python3
"""
dialogs.py — Dialog windows for XFreeRDP GUI: tooltips, drive redirection,
credential prompts, and the vault unlock popup with a configurable
in-memory key lifetime.
"""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Any

# label, seconds
VAULT_DURATIONS: list[tuple[str, int]] = [
    ("1 minute", 60),
    ("5 minutes", 300),
    ("15 minutes", 900),
    ("30 minutes", 1_800),
    ("1 hour", 3_600),
    ("1 day", 86_400),
    ("7 days", 604_800),
]


def modal_grab(win: tk.Toplevel) -> None:
    """Set a modal grab safely: only once the window is actually viewable,
    never raising if that is momentarily impossible (e.g. parent races).
    Setting a grab before mapping raises TclError and, worse, can leave
    Tk's grab bookkeeping stranded — which is exactly how the
    keyboard-input freeze happened."""
    try:
        win.wait_visibility()
        win.grab_set()
    except tk.TclError:
        pass


def modal_release(win: tk.Toplevel) -> None:
    """Release the modal grab before destroying a dialog. Tk normally
    cleans up when the grabbing window dies, but 'normally' fails when
    dialogs chain or destroy from unusual paths; always release first."""
    try:
        win.grab_release()
    except tk.TclError:
        pass


class Tooltip:
    """Hover tooltip with a dwell delay.

    The original version showed immediately on <Enter> and destroyed on
    <Leave> — meaning rapid pointer travel across the form churned tooltip
    creation/destruction directly over adjacent fields, and a click
    landing during that churn stranded keyboard focus (all entries went
    input-dead until restart; mouse-driven widgets kept working).

    Changes that fix it:
    - 1000 ms dwell delay: crossings spawn nothing, only resting does
    - hides on any ButtonPress: never alive at click time
    - larger offset: less overlap with the widget below
    - destroying a tip swallows TclError (double-destroy is harmless)
    """

    DELAY_MS = 1000

    def __init__(self, widget: tk.Widget, text: str):
        self._widget = widget
        self._text = text
        self._tip_window: tk.Toplevel | None = None
        self._after_id: str | None = None
        widget.bind("<Enter>", self._schedule)
        widget.bind("<Leave>", self._hide)
        widget.bind("<ButtonPress>", self._hide)   # die before clicks land

    def _schedule(self, _event: tk.Event[Any] | None = None) -> None:
        self._cancel()
        self._after_id = self._widget.after(self.DELAY_MS, self._show)

    def _cancel(self) -> None:
        if self._after_id is not None:
            try:
                self._widget.after_cancel(self._after_id)
            except tk.TclError:
                pass
            self._after_id = None

    def _show(self) -> None:
        self._after_id = None
        self._destroy_tip()
        if not self._text or not self._widget.winfo_exists():
            return
        x = self._widget.winfo_rootx() + 24
        y = self._widget.winfo_rooty() + self._widget.winfo_height() + 12
        self._tip_window = tw = tk.Toplevel(self._widget)
        tw.wm_overrideredirect(True)
        tw.wm_geometry(f"+{x}+{y}")
        tk.Label(
            tw,
            text=self._text,
            background="#ffffe0",
            foreground="#1a1a1a",
            relief=tk.SOLID,
            borderwidth=1,
            font=("", 8),
            padx=6,
            pady=3,
            wraplength=300,
            justify=tk.LEFT,
        ).pack()

    def _hide(self, _event: tk.Event[Any] | None = None) -> None:
        self._cancel()
        self._destroy_tip()

    def _destroy_tip(self) -> None:
        if self._tip_window is not None:
            try:
                self._tip_window.destroy()
            except tk.TclError:
                pass        # already gone — never propagate here
            self._tip_window = None


class DriveDialog(tk.Toplevel):
    """Modal dialog for adding a drive redirection entry."""

    def __init__(self, parent: tk.Tk | tk.Toplevel):
        from tkinter import filedialog  # local import keeps module load light

        super().__init__(parent)
        self.resizable(False, False)
        self.result: str | None = None
        self.transient(parent)

        f = ttk.Frame(self, padding=15)
        f.pack(fill=tk.BOTH, expand=True)

        ttk.Label(f, text="Share name:").grid(row=0, column=0, sticky=tk.W, pady=4)
        self.name_var = tk.StringVar(value="share")
        ttk.Entry(f, textvariable=self.name_var, width=22).grid(
            row=0, column=1, sticky=tk.EW, padx=(10, 0), pady=4
        )

        ttk.Label(
            f, text="~/ and $VAR in the path are expanded when connecting.",
            foreground="#888",
        ).grid(row=3, column=1, sticky=tk.W, pady=(0, 4))
        self.path_var = tk.StringVar()
        path_row = ttk.Frame(f)
        path_row.grid(row=1, column=1, sticky=tk.EW, padx=(10, 0), pady=4)
        ttk.Entry(path_row, textvariable=self.path_var, width=22).pack(
            side=tk.LEFT, fill=tk.X, expand=True
        )
        ttk.Button(
            path_row,
            text="…",
            width=3,
            command=lambda: self.path_var.set(
                filedialog.askdirectory(title="Select folder") or self.path_var.get()
            ),
        ).pack(side=tk.LEFT, padx=(3, 0))

        btn = ttk.Frame(f)
        btn.grid(row=2, column=0, columnspan=2, pady=(12, 0))
        ttk.Button(btn, text="OK", command=self._ok).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn, text="Cancel", command=self._cancel).pack(side=tk.LEFT, padx=5)

        f.columnconfigure(1, weight=1)
        self.bind("<Return>", lambda _: self._ok())
        self.bind("<Escape>", lambda _: self._cancel())
        self.update_idletasks()
        modal_grab(self)

    def _cancel(self) -> None:
        modal_release(self)
        self.result = None
        self.destroy()

    def _ok(self) -> None:
        name = self.name_var.get().strip()
        path = self.path_var.get().strip()
        if name and path:
            self.result = f"{name},{path}"
        modal_release(self)
        self.destroy()


class UnlockDialog(tk.Toplevel):
    """Modal vault unlock dialog.

    Asks for the vault passphrase and how long the derived key should stay
    usable in memory. Sets self.result to (passphrase, ttl_seconds) on
    success, or None if cancelled.
    """

    def __init__(self, parent: tk.Tk | tk.Toplevel, creating: bool = False):
        super().__init__(parent)
        self.title("Vault passphrase")
        self.resizable(False, False)
        self.result: tuple[str, int] | None = None
        # Only bind to the parent when it is actually on screen — the root
        # window is withdrawn in tray mode, and a transient leader that is
        # unmapped makes some WMs misplace or bury the dialog.
        if parent.winfo_viewable():
            self.transient(parent)

        pad = ttk.Frame(self, padding=18)
        pad.pack(fill=tk.BOTH, expand=True)

        if creating:
            head = "Create your profile vault passphrase"
            note = (
                "This passphrase encrypts all saved profiles (including "
                "passwords) on disk. There is no recovery — if you forget it, "
                "the vault cannot be opened."
            )
        else:
            head = "Enter vault passphrase to unlock saved profiles"
            note = "Profiles stay unlocked in memory only for the chosen duration."

        ttk.Label(pad, text=head, wraplength=380, justify=tk.LEFT).pack(
            anchor=tk.W, pady=(0, 4)
        )
        ttk.Label(pad, text=note, wraplength=380, justify=tk.LEFT, foreground="#666").pack(
            anchor=tk.W, pady=(0, 10)
        )

        row = ttk.Frame(pad)
        row.pack(fill=tk.X, pady=(0, 4))
        ttk.Label(row, text="Passphrase:").pack(side=tk.LEFT, padx=(0, 8))
        self.pass_entry = ttk.Entry(row, show="●", width=30)
        self.pass_entry.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.pass_entry.focus_set()

        dur_row = ttk.Frame(pad)
        dur_row.pack(fill=tk.X, pady=(8, 0))
        ttk.Label(dur_row, text="Keep unlocked for:").pack(side=tk.LEFT, padx=(0, 8))
        labels = [label for label, _secs in VAULT_DURATIONS]
        self.duration_var = tk.StringVar(value="15 minutes")
        dur_cb = ttk.Combobox(
            dur_row, textvariable=self.duration_var,
            values=labels, width=14, state="readonly",
        )
        dur_cb.pack(side=tk.LEFT)

        custom_row = ttk.Frame(pad)
        custom_row.pack(fill=tk.X, pady=(6, 0))
        ttk.Label(custom_row, text="Or custom (minutes):").pack(side=tk.LEFT, padx=(0, 8))
        self.custom_var = tk.StringVar()
        ttk.Entry(custom_row, textvariable=self.custom_var, width=8).pack(side=tk.LEFT)

        btn = ttk.Frame(pad)
        btn.pack(fill=tk.X, pady=(14, 0))
        ok_btn = ttk.Button(btn, text="Unlock", command=self._ok)
        ok_btn.pack(side=tk.RIGHT, padx=(6, 0))
        ttk.Button(btn, text="Cancel", command=self._cancel).pack(side=tk.RIGHT)

        self.bind("<Return>", lambda _: self._ok())
        self.bind("<Escape>", lambda _: self._cancel())
        # Center on screen (or on parent if visible).
        self.update_idletasks()
        dw, dh = self.winfo_reqwidth(), self.winfo_reqheight()
        if parent.winfo_viewable():
            px, py = parent.winfo_rootx(), parent.winfo_rooty()
            pw, ph = parent.winfo_width(), parent.winfo_height()
        else:
            px, py = 0, 0
            pw, ph = self.winfo_screenwidth(), self.winfo_screenheight()
        x = px + max(0, (pw - dw) // 2)
        y = py + max(0, (ph - dh) // 2)
        self.geometry(f"+{x}+{y}")
        # Grab only after we are mapped and positioned.
        modal_grab(self)

    def _resolve_ttl(self) -> int | None:
        custom = self.custom_var.get().strip()
        if custom:
            try:
                minutes = int(custom)
            except ValueError:
                return None
            return minutes * 60 if minutes > 0 else None
        selected = self.duration_var.get()
        for label, secs in VAULT_DURATIONS:
            if label == selected:
                return secs
        return 900  # fallback: 15 minutes

    def _cancel(self) -> None:
        modal_release(self)
        self.result = None
        self.destroy()

    def _ok(self) -> None:
        passphrase = self.pass_entry.get()
        ttl = self._resolve_ttl()
        if not passphrase:
            return
        if ttl is None:
            # Invalid custom value: fall back to 15 minutes rather than blocking.
            ttl = 900
        self.result = (passphrase, ttl)
        modal_release(self)
        self.destroy()


class PasswordDialog(tk.Toplevel):
    """Credential prompt. Works whether the parent window is visible
    (windowed mode) or withdrawn (tray mode).

    mode: "both"     -> ask for username and password
          "user"     -> ask for username only
          "password" -> ask for password only

    self.result is a dict {"username": str, "password": str} with the
    fields that were asked for (absent keys were not requested), or
    None if the user cancelled.
    """

    def __init__(self, parent: tk.Tk | tk.Toplevel, who: str,
                 mode: str = "password"):
        super().__init__(parent)
        self.title("Credentials" if mode == "both" else
                   "Username" if mode == "user" else "Password")
        self.resizable(False, False)
        self.result: dict[str, str] | None = None
        self._mode = mode

        if parent.winfo_viewable():
            self.transient(parent)

        pad = ttk.Frame(self, padding=16)
        pad.pack(fill=tk.BOTH, expand=True)

        ttk.Label(pad, text=f"Credentials for {who}:").pack(anchor=tk.W)

        self._entries: dict[str, ttk.Entry] = {}
        if mode in ("both", "user"):
            ttk.Label(pad, text="Username:").pack(anchor=tk.W, pady=(8, 0))
            user_entry = ttk.Entry(pad, width=32)
            user_entry.pack(fill=tk.X, pady=(2, 0))
            self._entries["username"] = user_entry
        if mode in ("both", "password"):
            ttk.Label(pad, text="Password:").pack(anchor=tk.W, pady=(8, 0))
            pw_entry = ttk.Entry(pad, show="•", width=32)
            pw_entry.pack(fill=tk.X, pady=(2, 0))
            self._entries["password"] = pw_entry

        btn = ttk.Frame(pad)
        btn.pack(fill=tk.X, pady=(12, 0))
        ttk.Button(btn, text="Connect", command=self._ok).pack(
            side=tk.RIGHT, padx=(6, 0))
        ttk.Button(btn, text="Cancel", command=self._cancel).pack(side=tk.RIGHT)

        self.bind("<Return>", lambda _: self._ok())
        self.bind("<Escape>", lambda _: self._cancel())

        # Center on the parent if it is on screen, else on the screen.
        self.update_idletasks()
        dw, dh = self.winfo_reqwidth(), self.winfo_reqheight()
        if parent.winfo_viewable():
            px, py = parent.winfo_rootx(), parent.winfo_rooty()
            pw, ph = parent.winfo_width(), parent.winfo_height()
        else:
            px, py = 0, 0
            pw, ph = self.winfo_screenwidth(), self.winfo_screenheight()
        self.geometry(f"+{px + max(0, (pw - dw) // 2)}+{py + max(0, (ph - dh) // 2)}")

        (self._entries.get("username") or
         self._entries.get("password")).focus_set()
        # Grab only after we are mapped and positioned.
        modal_grab(self)

    def _cancel(self) -> None:
        modal_release(self)
        self.result = None
        self.destroy()

    def _ok(self) -> None:
        self.result = {field: entry.get()
                       for field, entry in self._entries.items()}
        modal_release(self)
        self.destroy()
