#!/usr/bin/env python3
"""
Claude Code Usage Inspector - macOS Menu Bar App

A lightweight menu bar app that displays Claude Code API usage statistics.
On first run, it creates a self-contained venv using uv and installs dependencies.
"""

import os
import sys
import subprocess
import json
from pathlib import Path

APP_DIR = Path(__file__).parent.resolve()
VENV_DIR = APP_DIR / ".venv"
CONFIG_FILE = APP_DIR / "config.json"
PYTHON = VENV_DIR / "bin" / "python"
UV = None  # resolved lazily by find_uv()

def find_uv():
    """Find the uv binary, checking common install locations beyond PATH.

    When launched as a macOS .app bundle (Finder, Login Items), the process
    inherits a minimal environment without the user's shell PATH, so uv
    installed in ~/.local/bin or ~/.cargo/bin won't be found via bare "uv".
    """
    global UV
    if UV is not None:
        return UV

    home = Path.home()
    candidates = [
        "uv",  # on PATH
        str(home / ".local" / "bin" / "uv"),
        str(home / ".cargo" / "bin" / "uv"),
        "/usr/local/bin/uv",
        "/opt/homebrew/bin/uv",
    ]
    for candidate in candidates:
        try:
            subprocess.run([candidate, "--version"], check=True, capture_output=True)
            UV = candidate
            return UV
        except (subprocess.CalledProcessError, FileNotFoundError):
            continue
    return None

def setup_venv():
    """Set up venv with uv on first run."""
    if VENV_DIR.exists() and PYTHON.exists():
        return True

    print("Setting up environment (first run)...")

    # Check if uv is available
    uv = find_uv()
    if not uv:
        print("Error: 'uv' is not installed. Install it with:")
        print("  curl -LsSf https://astral.sh/uv/install.sh | sh")
        return False

    # Create venv
    print("Creating virtual environment...")
    subprocess.run([uv, "venv", str(VENV_DIR)], check=True, cwd=APP_DIR)

    # Install dependencies
    print("Installing dependencies...")
    subprocess.run(
        [uv, "pip", "install", "rumps", "pyobjc-framework-Cocoa", "Pillow"],
        check=True,
        cwd=APP_DIR,
        env={**os.environ, "VIRTUAL_ENV": str(VENV_DIR)}
    )

    print("Setup complete!")
    return True

def relaunch_in_venv():
    """Relaunch the script using the venv Python."""
    os.execv(str(PYTHON), [str(PYTHON), __file__] + sys.argv[1:])

# Bootstrap: ensure we're running in venv
if not sys.prefix.startswith(str(VENV_DIR)):
    if not setup_venv():
        sys.exit(1)
    relaunch_in_venv()

# Now we're in venv - import dependencies
import rumps
import urllib.request
import math
import tempfile
import uuid
from datetime import datetime, timedelta
from threading import Thread, Lock, current_thread, main_thread
from AppKit import (
    NSAlert, NSTextField, NSSecureTextField, NSView,
    NSMakeRect, NSAlertFirstButtonReturn, NSFont,
    NSImage as _NSImage,
)
from PyObjCTools import AppHelper
try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:
    subprocess.run(
        [find_uv() or "uv", "pip", "install", "Pillow"],
        check=True, cwd=APP_DIR,
        env={**os.environ, "VIRTUAL_ENV": str(VENV_DIR)}
    )
    from PIL import Image, ImageDraw, ImageFont

# Battery icon settings (display pixels at 2x retina, saved at 144 DPI)
BAT_BODY_W = 44   # Battery body width
BAT_BODY_H = 30   # Battery body height
BAT_TIP_W = 4     # Tip width
BAT_TIP_H = 12    # Tip height
BAT_BORDER = 2    # Outline thickness
BAT_RADIUS = 5    # Corner radius
BAT_PAD = 2       # Internal padding for fill area
TEXT_GAP = 4       # Gap between battery and its number
PAIR_GAP = 6       # Gap between first pair and second pair
RENDER_SCALE = 3   # Supersampling for anti-aliasing

BAR_COLORS = {
    'green': (52, 199, 89),
    'yellow': (255, 214, 10),
    'red': (255, 69, 58),
}


def _draw_battery(draw, x, y, fill_frac, color, s):
    """Draw a single battery bar at (x, y) in render-scale coordinates."""
    cr, cg, cb = color
    bw = BAT_BODY_W * s
    bh = BAT_BODY_H * s
    tw = BAT_TIP_W * s
    th = BAT_TIP_H * s
    border = BAT_BORDER * s
    radius = BAT_RADIUS * s
    pad = BAT_PAD * s

    # Battery body outline (rounded rectangle)
    draw.rounded_rectangle(
        [x, y, x + bw - 1, y + bh - 1],
        radius=radius,
        outline=(cr, cg, cb, 200),
        width=border,
    )

    # Battery tip (right side, centered vertically)
    tip_y = y + (bh - th) // 2
    draw.rounded_rectangle(
        [x + bw, tip_y, x + bw + tw - 1, tip_y + th - 1],
        radius=max(1, s),
        fill=(cr, cg, cb, 200),
    )

    # Fill area bounds (inside border + padding)
    fx0 = x + border + pad
    fy0 = y + border + pad
    fx1 = x + bw - border - pad - 1
    fy1 = y + bh - border - pad - 1
    fill_w = fx1 - fx0

    # Empty background (faint)
    draw.rectangle([fx0, fy0, fx1, fy1], fill=(cr, cg, cb, 40))

    # Filled portion
    if fill_frac > 0.01:
        filled_x1 = fx0 + int(fill_w * min(1.0, fill_frac))
        draw.rectangle([fx0, fy0, filled_x1, fy1], fill=(cr, cg, cb, 255))


def _load_font():
    """Load a system font for rendering numbers on the icon."""
    size = int(BAT_BODY_H * RENDER_SCALE * 0.7)
    for path in [
        "/System/Library/Fonts/SFNS.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
        "/System/Library/Fonts/Supplemental/Arial.ttf",
    ]:
        try:
            return ImageFont.truetype(path, size=size)
        except (OSError, IOError):
            continue
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


_FONT = _load_font()


class Account:
    """A monitored Claude Code account (a title + an OAuth token)."""

    def __init__(self, account_id, title, token):
        self.id = account_id
        self.title = title or "untitled"
        self.token = token or ""

        # Latest fetched stats (None until first successful fetch)
        self.session_util = None
        self.weekly_util = None
        self.session_reset = None
        self.weekly_reset = None
        self.status = "--"
        self.last_update = "--"
        self.next_update = "--"

        # References to this account's live menu items, set by rebuild_menu()
        self.menu_top = None
        self.menu_session = None
        self.menu_session_reset = None
        self.menu_weekly = None
        self.menu_weekly_reset = None
        self.menu_status = None
        self.menu_last_update = None
        self.menu_next_update = None

    def to_dict(self):
        return {"id": self.id, "title": self.title, "token": self.token}

    def reset_stats(self):
        """Clear cached usage stats, e.g. when the token changes."""
        self.session_util = None
        self.weekly_util = None
        self.session_reset = None
        self.weekly_reset = None
        self.status = "--"
        self.last_update = "--"
        self.next_update = "--"


def new_account_id():
    return uuid.uuid4().hex[:8]


class UsageInspectorApp(rumps.App):
    def __init__(self):
        super().__init__("Usage Inspector", title="⏳", template=False, quit_button=None)

        self.config = self.load_config()
        self.accounts = self._load_accounts(self.config)
        self.primary_id = self.config.get("primary_account_id")
        if self.primary_id not in {a.id for a in self.accounts}:
            self.primary_id = self.accounts[0].id if self.accounts else None
        self.refresh_interval = self.config.get("refresh_interval", 300)
        self._fetch_lock = Lock()

        # Icon paths
        self._icon_path = os.path.join(tempfile.gettempdir(), "usage_inspector_icon.png")
        self._empty_icon = os.path.join(tempfile.gettempdir(), "usage_inspector_empty.png")
        Image.new('RGBA', (2, 2), (0, 0, 0, 0)).save(self._empty_icon)

        self.rebuild_menu()

        # Start timer
        self.timer = rumps.Timer(self.fetch_all_usage, self.refresh_interval)
        self.timer.start()

        # Initial fetch
        if self.accounts:
            Thread(target=self.fetch_all_usage, daemon=True).start()
        else:
            self.title = "⚠️"

    # -- Config / persistence -------------------------------------------------

    def load_config(self):
        if CONFIG_FILE.exists():
            try:
                return json.loads(CONFIG_FILE.read_text())
            except Exception:
                pass
        return {}

    def _load_accounts(self, cfg):
        accounts_data = cfg.get("accounts")
        if accounts_data is None:
            # Migrate legacy single-account config (oauth_token key).
            legacy_token = cfg.get("oauth_token", "")
            accounts_data = (
                [{"id": new_account_id(), "title": "untitled", "token": legacy_token}]
                if legacy_token else []
            )
        accounts = []
        for a in accounts_data:
            accounts.append(Account(
                account_id=a.get("id") or new_account_id(),
                title=a.get("title") or "untitled",
                token=a.get("token", ""),
            ))
        return accounts

    def save_config(self):
        self.config["accounts"] = [a.to_dict() for a in self.accounts]
        self.config["primary_account_id"] = self.primary_id
        self.config["refresh_interval"] = self.refresh_interval
        CONFIG_FILE.write_text(json.dumps(self.config, indent=2))

    def _find_account(self, account_id):
        for acc in self.accounts:
            if acc.id == account_id:
                return acc
        return None

    def _primary_account(self):
        return self._find_account(self.primary_id)

    # -- Menu construction ------------------------------------------------------

    def rebuild_menu(self):
        self.menu.clear()

        for acc in self.accounts:
            self.menu[acc.id] = self._build_account_menuitem(acc)

        if not self.accounts:
            self.menu.add(rumps.MenuItem("No accounts configured"))

        self.menu.add(None)
        self.menu.add(rumps.MenuItem("Refresh Now", callback=self.refresh_now))
        self.menu.add(self._build_manage_accounts_menu())
        self.menu.add(rumps.MenuItem("Settings...", callback=self.show_settings))
        self.menu.add(None)
        self.menu.add(rumps.MenuItem("About", callback=self.show_about))
        self.menu.add(rumps.MenuItem("Quit", callback=rumps.quit_application))

    def _build_account_menuitem(self, acc):
        top = rumps.MenuItem(self._account_summary_title(acc))
        top.state = 1 if acc.id == self.primary_id else 0
        acc.menu_top = top

        top.add(rumps.MenuItem("Set as Primary", callback=self._make_set_primary(acc.id)))
        top.add(None)

        acc.menu_session = rumps.MenuItem(self._session_title(acc))
        acc.menu_session_reset = rumps.MenuItem(f"  Resets: {self.format_reset(acc.session_reset)}")
        acc.menu_weekly = rumps.MenuItem(self._weekly_title(acc))
        acc.menu_weekly_reset = rumps.MenuItem(f"  Resets: {self.format_reset(acc.weekly_reset)}")
        acc.menu_status = rumps.MenuItem(f"Status: {acc.status}")
        acc.menu_last_update = rumps.MenuItem(f"Last update: {acc.last_update}")
        acc.menu_next_update = rumps.MenuItem(f"Next update: {acc.next_update}")

        top.add(acc.menu_session)
        top.add(acc.menu_session_reset)
        top.add(None)
        top.add(acc.menu_weekly)
        top.add(acc.menu_weekly_reset)
        top.add(None)
        top.add(acc.menu_status)
        top.add(acc.menu_last_update)
        top.add(acc.menu_next_update)

        return top

    def _build_manage_accounts_menu(self):
        manage = rumps.MenuItem("Manage Accounts")
        manage.add(rumps.MenuItem("Add Account...", callback=self.add_account))
        if self.accounts:
            manage.add(None)
            title_counts = {}
            for acc in self.accounts:
                title_counts[acc.title] = title_counts.get(acc.title, 0) + 1
            for acc in self.accounts:
                # Disambiguate accounts sharing a title (e.g. the "untitled" default)
                label = acc.title if title_counts[acc.title] == 1 else f"{acc.title} ({acc.id[:4]})"
                entry = rumps.MenuItem(label)
                entry.add(rumps.MenuItem("Edit...", callback=self._make_edit_account(acc.id)))
                entry.add(rumps.MenuItem("Remove", callback=self._make_remove_account(acc.id)))
                manage[acc.id] = entry
        return manage

    # -- Menu text helpers --------------------------------------------------

    def _account_summary_title(self, acc):
        if acc.session_util is None or acc.weekly_util is None:
            return f"{acc.title}: --"
        return f"{acc.title}: {int(acc.session_util * 100)}% / {int(acc.weekly_util * 100)}%"

    def _session_title(self, acc):
        if acc.session_util is None:
            return "Session (5h): --"
        return f"Session (5h): {int(acc.session_util * 100)}%"

    def _weekly_title(self, acc):
        if acc.weekly_util is None:
            return "Weekly (7d): --"
        return f"Weekly (7d): {int(acc.weekly_util * 100)}%"

    # -- Main-thread marshalling ---------------------------------------------

    def _run_on_main(self, fn, *args):
        """Run a UI update on the main thread.

        AppKit (the status item, its button, and menu items) is NOT
        thread-safe. Mutating it from a background fetch thread can corrupt
        the app's WindowServer connection and freeze keyboard/mouse input
        system-wide (only a reboot recovers). Network I/O stays on the
        background thread; every UI touch is funnelled through here.
        """
        if current_thread() is main_thread():
            fn(*args)
        else:
            AppHelper.callAfter(fn, *args)

    def _set_title_glyph(self, glyph):
        """MAIN THREAD ONLY. Show a transient status glyph in the menu bar."""
        self.icon = self._empty_icon
        self.title = glyph

    def _render_account(self, acc, glyph=None):
        """MAIN THREAD ONLY. Refresh an account's submenu and, if it is the
        primary account, the menu-bar icon."""
        self._update_account_menu(acc)
        if acc.id != self.primary_id:
            return
        if glyph:
            self.icon = self._empty_icon
            self.title = glyph
        else:
            self._apply_primary_icon()

    def _update_account_menu(self, acc):
        if acc.menu_top is None:
            return
        acc.menu_top.title = self._account_summary_title(acc)
        acc.menu_session.title = self._session_title(acc)
        acc.menu_session_reset.title = f"  Resets: {self.format_reset(acc.session_reset)}"
        acc.menu_weekly.title = self._weekly_title(acc)
        acc.menu_weekly_reset.title = f"  Resets: {self.format_reset(acc.weekly_reset)}"
        acc.menu_status.title = f"Status: {acc.status}"
        acc.menu_last_update.title = f"Last update: {acc.last_update}"
        acc.menu_next_update.title = f"Next update: {acc.next_update}"

    # -- Primary account selection -------------------------------------------

    def _make_set_primary(self, account_id):
        def _callback(_):
            self.set_primary(account_id)
        return _callback

    def set_primary(self, account_id):
        if account_id == self.primary_id:
            return
        self.primary_id = account_id
        self.save_config()
        for acc in self.accounts:
            if acc.menu_top is not None:
                acc.menu_top.state = 1 if acc.id == account_id else 0
        self._apply_primary_icon()

    def _apply_primary_icon(self):
        acc = self._primary_account()
        if acc is None or not acc.token:
            self.icon = self._empty_icon
            self.title = "⚠️"
            return
        if acc.session_util is not None and acc.weekly_util is not None:
            self._update_battery_icon(acc.session_util, acc.weekly_util, acc.session_reset, acc.weekly_reset)
        else:
            self.icon = self._empty_icon
            self.title = "⏳"

    # -- Account management (add/edit/remove) --------------------------------

    def _make_edit_account(self, account_id):
        def _callback(_):
            acc = self._find_account(account_id)
            if acc:
                self.edit_account(acc)
        return _callback

    def _make_remove_account(self, account_id):
        def _callback(_):
            acc = self._find_account(account_id)
            if acc:
                self.remove_account(acc)
        return _callback

    def _prompt_account(self, message, initial_title="", initial_token=""):
        """Show a dialog to enter/edit an account's title and token.

        Returns (title, token) tuple, or None if cancelled.
        """
        alert = NSAlert.alloc().init()
        alert.setMessageText_(message)
        alert.setInformativeText_("Enter a title and the OAuth token for this account")
        alert.addButtonWithTitle_("Save")
        alert.addButtonWithTitle_("Cancel")

        view = NSView.alloc().initWithFrame_(NSMakeRect(0, 0, 400, 80))

        title_label = NSTextField.alloc().initWithFrame_(NSMakeRect(0, 55, 120, 20))
        title_label.setStringValue_("Title:")
        title_label.setBezeled_(False)
        title_label.setDrawsBackground_(False)
        title_label.setEditable_(False)
        title_label.setSelectable_(False)
        view.addSubview_(title_label)

        title_field = NSTextField.alloc().initWithFrame_(NSMakeRect(125, 52, 270, 24))
        title_field.setStringValue_(initial_title)
        title_field.setPlaceholderString_("untitled")
        title_field.setFont_(NSFont.systemFontOfSize_(12))
        view.addSubview_(title_field)

        token_label = NSTextField.alloc().initWithFrame_(NSMakeRect(0, 20, 120, 20))
        token_label.setStringValue_("OAuth Token:")
        token_label.setBezeled_(False)
        token_label.setDrawsBackground_(False)
        token_label.setEditable_(False)
        token_label.setSelectable_(False)
        view.addSubview_(token_label)

        token_field = NSTextField.alloc().initWithFrame_(NSMakeRect(125, 17, 270, 24))
        token_field.setStringValue_(initial_token)
        token_field.setPlaceholderString_("sk-ant-oat01-...")
        token_field.setFont_(NSFont.systemFontOfSize_(12))
        view.addSubview_(token_field)

        alert.setAccessoryView_(view)

        response = alert.runModal()
        if response != NSAlertFirstButtonReturn:
            return None

        new_title = title_field.stringValue().strip() or "untitled"
        new_token = token_field.stringValue().strip()
        return new_title, new_token

    def add_account(self, _):
        result = self._prompt_account("Add Account")
        if not result:
            return
        title, token = result
        acc = Account(new_account_id(), title, token)
        self.accounts.append(acc)
        if self.primary_id is None:
            self.primary_id = acc.id
        self.save_config()
        self.rebuild_menu()
        if acc.id == self.primary_id:
            self._apply_primary_icon()
        if acc.token:
            Thread(target=self._fetch_account, args=(acc,), daemon=True).start()

    def edit_account(self, acc):
        result = self._prompt_account(f'Edit "{acc.title}"', acc.title, acc.token)
        if not result:
            return
        new_title, new_token = result
        if new_token != acc.token:
            # Old usage stats belong to the old token; don't keep showing them.
            acc.reset_stats()
            if not new_token:
                acc.status = "Token not set"
        acc.title, acc.token = new_title, new_token
        self.save_config()
        self.rebuild_menu()
        if acc.token:
            Thread(target=self._fetch_account, args=(acc,), daemon=True).start()
        elif acc.id == self.primary_id:
            self._apply_primary_icon()

    def remove_account(self, acc):
        response = rumps.alert(
            title="Remove Account",
            message=f'Remove "{acc.title}"? This cannot be undone.',
            ok="Remove",
            cancel="Cancel",
        )
        if response != 1:
            return
        self.accounts = [a for a in self.accounts if a.id != acc.id]
        if self.primary_id == acc.id:
            self.primary_id = self.accounts[0].id if self.accounts else None
        self.save_config()
        self.rebuild_menu()
        self._apply_primary_icon()

    # -- Settings (global refresh interval) ----------------------------------

    def show_settings(self, _):
        """Show settings dialog for global options (refresh interval)."""
        alert = NSAlert.alloc().init()
        alert.setMessageText_("Settings")
        alert.setInformativeText_("Configure the global refresh interval")
        alert.addButtonWithTitle_("Save")
        alert.addButtonWithTitle_("Cancel")

        view = NSView.alloc().initWithFrame_(NSMakeRect(0, 0, 210, 30))

        interval_label = NSTextField.alloc().initWithFrame_(NSMakeRect(0, 4, 120, 20))
        interval_label.setStringValue_("Refresh (sec):")
        interval_label.setBezeled_(False)
        interval_label.setDrawsBackground_(False)
        interval_label.setEditable_(False)
        interval_label.setSelectable_(False)
        view.addSubview_(interval_label)

        interval_field = NSTextField.alloc().initWithFrame_(NSMakeRect(125, 1, 80, 24))
        interval_field.setStringValue_(str(self.refresh_interval))
        interval_field.setPlaceholderString_("300")
        interval_field.setFont_(NSFont.systemFontOfSize_(12))
        view.addSubview_(interval_field)

        alert.setAccessoryView_(view)

        response = alert.runModal()
        if response == NSAlertFirstButtonReturn:
            try:
                new_interval = int(interval_field.stringValue().strip())
                if new_interval < 10:
                    new_interval = 10  # Minimum 10 seconds
            except ValueError:
                new_interval = self.refresh_interval

            self.refresh_interval = new_interval
            self.save_config()

            # Restart timer with new interval
            self.timer.stop()
            self.timer = rumps.Timer(self.fetch_all_usage, self.refresh_interval)
            self.timer.start()

    def refresh_now(self, _):
        """Manual refresh."""
        Thread(target=self.fetch_all_usage, daemon=True).start()

    def show_about(self, _):
        """Show about dialog."""
        rumps.alert(
            title="Claude Code Usage Inspector",
            message=(
                "Version 0.3.1\n\n"
                "Author: Bruce Chou (and Claude Code)\n"
                "Email: brucechou1983@gmail.com\n"
                "GitHub: github.com/brucechou1983\n\n"
                "License: MIT"
            ),
            ok="OK"
        )

    @staticmethod
    def _color_for_util(util):
        if util >= 0.8:
            return 'red'
        if util >= 0.5:
            return 'yellow'
        return 'green'

    def _update_battery_icon(self, session_util, weekly_util, session_reset, weekly_reset):
        """Create and set combined battery icon with text labels.

        Layout: [bat_5h] quota_left [bat_7d] quota_left
        Fill level = time remaining until reset.
        Fill color = utilization severity (green/yellow/red).
        """
        now_ts = datetime.now().timestamp()
        session_frac = min(1.0, max(0.0, (int(session_reset) - now_ts) / (5 * 3600))) if session_reset else 0
        weekly_frac = min(1.0, max(0.0, (int(weekly_reset) - now_ts) / (7 * 86400))) if weekly_reset else 0

        s = RENDER_SCALE
        s_text = str(int(session_util * 100))
        w_text = str(int(weekly_util * 100))
        sc = BAR_COLORS[self._color_for_util(session_util)]
        wc = BAR_COLORS[self._color_for_util(weekly_util)]

        # Measure text advance widths
        s_tw = int(_FONT.getlength(s_text))
        w_tw = int(_FONT.getlength(w_text))

        bat_w = (BAT_BODY_W + BAT_TIP_W) * s
        bat_h = BAT_BODY_H * s
        tg = TEXT_GAP * s
        pg = PAIR_GAP * s

        # Total render dimensions
        rw = bat_w + tg + s_tw + pg + bat_w + tg + w_tw
        rw = ((rw + s - 1) // s) * s  # round up to multiple of scale
        rh = bat_h

        img = Image.new('RGBA', (rw, rh), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)

        # First pair: session battery + number
        x = 0
        _draw_battery(draw, x, 0, session_frac, sc, s)
        x += bat_w + tg
        draw.text((x, rh // 2), s_text, fill=sc + (255,), font=_FONT, anchor="lm")
        x += s_tw + pg

        # Second pair: weekly battery + number
        _draw_battery(draw, x, 0, weekly_frac, wc, s)
        x += bat_w + tg
        draw.text((x, rh // 2), w_text, fill=wc + (255,), font=_FONT, anchor="lm")

        # Downscale for anti-aliasing
        final_w = rw // s
        final_h = rh // s
        img = img.resize((final_w, final_h), Image.LANCZOS)
        img.save(self._icon_path)

        # Set icon with explicit point size for proper retina rendering
        try:
            ns_img = _NSImage.alloc().initWithContentsOfFile_(self._icon_path)
            ns_img.setSize_((final_w / 2, final_h / 2))
            ns_img.setTemplate_(False)
            self._nsapp.nsstatusitem.button().setImage_(ns_img)
        except AttributeError:
            self.icon = self._icon_path
        self.title = ""

    def fetch_all_usage(self, _=None):
        """Fetch usage data for every configured account.

        Guarded against overlapping runs: with several accounts, one sequential
        pass (each request can take up to 30s) can outlast a short refresh
        interval or a manual "Refresh Now" click.
        """
        # The rumps.Timer fires this on the MAIN thread; the network requests
        # below can each block for up to 30s. Never run them on the main
        # thread (it would freeze the run loop) — offload to a worker.
        if current_thread() is main_thread():
            Thread(target=self.fetch_all_usage, daemon=True).start()
            return

        if not self._fetch_lock.acquire(blocking=False):
            return
        try:
            for acc in list(self.accounts):
                self._fetch_account(acc)
        finally:
            self._fetch_lock.release()

    def _fetch_account(self, acc):
        """Fetch usage data from the API for a single account.

        Re-checks `acc.id == self.primary_id` at each site rather than caching
        it once, since the request below can take up to 30s and the user may
        switch the primary account while it's in flight.
        """
        if not acc.token:
            acc.status = "Token not set"
            self._run_on_main(self._render_account, acc, "⚠️")
            return

        if acc.id == self.primary_id:
            self._run_on_main(self._set_title_glyph, "🔄")

        try:
            url = "https://api.anthropic.com/v1/messages"
            body = json.dumps({
                "model": "claude-haiku-4-5-20251001",
                "max_tokens": 1,
                "messages": [{"role": "user", "content": "hi"}]
            }).encode()

            headers = {
                "Authorization": f"Bearer {acc.token}",
                "Content-Type": "application/json",
                "anthropic-version": "2023-06-01",
                "anthropic-beta": "oauth-2025-04-20",
                "Cache-Control": "no-cache, no-store",
                "Pragma": "no-cache",
            }

            req = urllib.request.Request(url, data=body, headers=headers, method="POST")

            with urllib.request.urlopen(req, timeout=30) as response:
                # Parse rate limit headers
                session_util = float(response.headers.get("anthropic-ratelimit-unified-5h-utilization", 0))
                weekly_util = float(response.headers.get("anthropic-ratelimit-unified-7d-utilization", 0))
                session_reset = response.headers.get("anthropic-ratelimit-unified-5h-reset")
                weekly_reset = response.headers.get("anthropic-ratelimit-unified-7d-reset")
                status = response.headers.get("anthropic-ratelimit-unified-status", "unknown")

                acc.session_util = session_util
                acc.weekly_util = weekly_util
                acc.session_reset = session_reset
                acc.weekly_reset = weekly_reset
                acc.status = status
                now = datetime.now()
                acc.last_update = now.strftime('%H:%M:%S')
                next_update = now + timedelta(seconds=self.refresh_interval)
                acc.next_update = next_update.strftime('%H:%M:%S')

                # All UI work (submenu + menu-bar battery icon) on main thread.
                self._run_on_main(self._render_account, acc)

        except urllib.error.HTTPError as e:
            if e.code == 401:
                acc.status = "Token expired"
            else:
                acc.status = f"Error {e.code}"
            self._run_on_main(self._render_account, acc, "🔑" if e.code == 401 else "❌")
        except Exception as e:
            acc.status = str(e)[:30]
            self._run_on_main(self._render_account, acc, "❌")

    def format_reset(self, reset_value):
        """Format reset timestamp."""
        if not reset_value:
            return "unknown"
        try:
            epoch = int(reset_value)
            reset_time = datetime.fromtimestamp(epoch)
            now = datetime.now()
            diff = reset_time - now

            if diff.total_seconds() < 0:
                return "just reset"

            total_seconds = int(diff.total_seconds())
            days, day_remainder = divmod(total_seconds, 86400)
            hours, remainder = divmod(day_remainder, 3600)
            minutes = remainder // 60

            time_str = reset_time.strftime("%I:%M %p")
            if days > 0:
                return f"{time_str} ({days}d {hours}h {minutes}m)"
            elif hours > 0:
                return f"{time_str} ({hours}h {minutes}m)"
            return f"{time_str} ({minutes}m)"
        except:
            return "unknown"

if __name__ == "__main__":
    UsageInspectorApp().run()
