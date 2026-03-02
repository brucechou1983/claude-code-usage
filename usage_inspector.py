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
from datetime import datetime, timedelta
from threading import Thread
from AppKit import (
    NSAlert, NSTextField, NSSecureTextField, NSView,
    NSMakeRect, NSAlertFirstButtonReturn, NSFont,
    NSImage as _NSImage,
)
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

class UsageInspectorApp(rumps.App):
    def __init__(self):
        super().__init__("Usage Inspector", title="⏳", template=False, quit_button=None)

        self.config = self.load_config()
        self.token = self.config.get("oauth_token", "")
        self.refresh_interval = self.config.get("refresh_interval", 300)

        # Icon paths
        self._icon_path = os.path.join(tempfile.gettempdir(), "usage_inspector_icon.png")
        self._empty_icon = os.path.join(tempfile.gettempdir(), "usage_inspector_empty.png")
        Image.new('RGBA', (2, 2), (0, 0, 0, 0)).save(self._empty_icon)

        # Menu items
        self.session_item = rumps.MenuItem("Session (5h): --")
        self.weekly_item = rumps.MenuItem("Weekly (7d): --")
        self.session_reset_item = rumps.MenuItem("  Resets: --")
        self.weekly_reset_item = rumps.MenuItem("  Resets: --")
        self.status_item = rumps.MenuItem("Status: --")
        self.last_update_item = rumps.MenuItem("Last update: --")
        self.next_update_item = rumps.MenuItem("Next update: --")

        self.menu = [
            self.session_item,
            self.session_reset_item,
            None,  # separator
            self.weekly_item,
            self.weekly_reset_item,
            None,
            self.status_item,
            self.last_update_item,
            self.next_update_item,
            None,
            rumps.MenuItem("Refresh Now", callback=self.refresh_now),
            rumps.MenuItem("Settings...", callback=self.show_settings),
            None,
            rumps.MenuItem("About", callback=self.show_about),
            rumps.MenuItem("Quit", callback=rumps.quit_application),
        ]

        # Start timer
        self.timer = rumps.Timer(self.fetch_usage, self.refresh_interval)
        self.timer.start()

        # Initial fetch
        if self.token:
            Thread(target=self.fetch_usage, args=(None,), daemon=True).start()
        else:
            self.title = "⚠️"
            self.status_item.title = "Status: Token not set"

    def load_config(self):
        if CONFIG_FILE.exists():
            try:
                return json.loads(CONFIG_FILE.read_text())
            except:
                pass
        return {}

    def save_config(self):
        CONFIG_FILE.write_text(json.dumps(self.config, indent=2))

    def show_settings(self, _):
        """Show settings dialog with multiple fields."""
        alert = NSAlert.alloc().init()
        alert.setMessageText_("Settings")
        alert.setInformativeText_("Configure your Claude Code Usage Inspector")
        alert.addButtonWithTitle_("Save")
        alert.addButtonWithTitle_("Cancel")

        # Create accessory view with fields
        view = NSView.alloc().initWithFrame_(NSMakeRect(0, 0, 400, 80))

        # OAuth token label and field
        token_label = NSTextField.alloc().initWithFrame_(NSMakeRect(0, 55, 120, 20))
        token_label.setStringValue_("OAuth Token:")
        token_label.setBezeled_(False)
        token_label.setDrawsBackground_(False)
        token_label.setEditable_(False)
        token_label.setSelectable_(False)
        view.addSubview_(token_label)

        token_field = NSTextField.alloc().initWithFrame_(NSMakeRect(125, 52, 270, 24))
        token_field.setStringValue_(self.token)
        token_field.setPlaceholderString_("sk-ant-oat01-...")
        token_field.setFont_(NSFont.systemFontOfSize_(12))
        view.addSubview_(token_field)

        # Refresh interval label and field
        interval_label = NSTextField.alloc().initWithFrame_(NSMakeRect(0, 20, 120, 20))
        interval_label.setStringValue_("Refresh (sec):")
        interval_label.setBezeled_(False)
        interval_label.setDrawsBackground_(False)
        interval_label.setEditable_(False)
        interval_label.setSelectable_(False)
        view.addSubview_(interval_label)

        interval_field = NSTextField.alloc().initWithFrame_(NSMakeRect(125, 17, 80, 24))
        interval_field.setStringValue_(str(self.refresh_interval))
        interval_field.setPlaceholderString_("300")
        interval_field.setFont_(NSFont.systemFontOfSize_(12))
        view.addSubview_(interval_field)

        alert.setAccessoryView_(view)

        # Show dialog
        response = alert.runModal()
        if response == NSAlertFirstButtonReturn:
            new_token = token_field.stringValue().strip()
            try:
                new_interval = int(interval_field.stringValue().strip())
                if new_interval < 10:
                    new_interval = 10  # Minimum 10 seconds
            except ValueError:
                new_interval = 300

            self.token = new_token
            self.refresh_interval = new_interval
            self.config["oauth_token"] = self.token
            self.config["refresh_interval"] = self.refresh_interval
            self.save_config()

            # Restart timer with new interval
            self.timer.stop()
            self.timer = rumps.Timer(self.fetch_usage, self.refresh_interval)
            self.timer.start()

            self.refresh_now(None)

    def refresh_now(self, _):
        """Manual refresh."""
        Thread(target=self.fetch_usage, args=(None,), daemon=True).start()

    def show_about(self, _):
        """Show about dialog."""
        rumps.alert(
            title="Claude Code Usage Inspector",
            message=(
                "Version 0.2.0\n\n"
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

    def fetch_usage(self, _):
        """Fetch usage data from API."""
        if not self.token:
            return

        self.icon = self._empty_icon
        self.title = "🔄"

        try:
            url = "https://api.anthropic.com/v1/messages"
            body = json.dumps({
                "model": "claude-haiku-4-5-20251001",
                "max_tokens": 1,
                "messages": [{"role": "user", "content": "hi"}]
            }).encode()

            headers = {
                "Authorization": f"Bearer {self.token}",
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

                # Update menu
                session_pct = int(session_util * 100)
                weekly_pct = int(weekly_util * 100)

                self.session_item.title = f"Session (5h): {session_pct}%"
                self.weekly_item.title = f"Weekly (7d): {weekly_pct}%"
                self.session_reset_item.title = f"  Resets: {self.format_reset(session_reset)}"
                self.weekly_reset_item.title = f"  Resets: {self.format_reset(weekly_reset)}"
                self.status_item.title = f"Status: {status}"
                now = datetime.now()
                self.last_update_item.title = f"Last update: {now.strftime('%H:%M:%S')}"
                next_update = now + timedelta(seconds=self.refresh_interval)
                self.next_update_item.title = f"Next update: {next_update.strftime('%H:%M:%S')}"

                # Update battery icon (fill = time left, color = utilization level)
                self._update_battery_icon(session_util, weekly_util, session_reset, weekly_reset)

        except urllib.error.HTTPError as e:
            self.icon = self._empty_icon
            if e.code == 401:
                self.title = "🔑"
                self.status_item.title = "Status: Token expired"
            else:
                self.title = "❌"
                self.status_item.title = f"Status: Error {e.code}"
        except Exception as e:
            self.icon = self._empty_icon
            self.title = "❌"
            self.status_item.title = f"Status: {str(e)[:30]}"

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
