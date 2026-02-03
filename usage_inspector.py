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

def setup_venv():
    """Set up venv with uv on first run."""
    if VENV_DIR.exists() and PYTHON.exists():
        return True

    print("Setting up environment (first run)...")

    # Check if uv is available
    try:
        subprocess.run(["uv", "--version"], check=True, capture_output=True)
    except (subprocess.CalledProcessError, FileNotFoundError):
        print("Error: 'uv' is not installed. Install it with:")
        print("  curl -LsSf https://astral.sh/uv/install.sh | sh")
        return False

    # Create venv
    print("Creating virtual environment...")
    subprocess.run(["uv", "venv", str(VENV_DIR)], check=True, cwd=APP_DIR)

    # Install dependencies
    print("Installing dependencies...")
    subprocess.run(
        ["uv", "pip", "install", "rumps", "pyobjc-framework-Cocoa"],
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
from datetime import datetime, timedelta
from threading import Thread

class UsageInspectorApp(rumps.App):
    def __init__(self):
        super().__init__("⏳", quit_button=None)

        self.config = self.load_config()
        self.token = self.config.get("oauth_token", "")

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
            rumps.MenuItem("Set OAuth Token...", callback=self.set_token),
            None,
            rumps.MenuItem("Quit", callback=rumps.quit_application),
        ]

        # Start timer (5 minutes)
        self.timer = rumps.Timer(self.fetch_usage, 300)
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

    def set_token(self, _):
        """Prompt user to set OAuth token."""
        window = rumps.Window(
            message="Enter your Claude Code OAuth token:\n(starts with sk-ant-oat01-...)",
            title="Set OAuth Token",
            default_text=self.token,
            ok="Save",
            cancel="Cancel",
            dimensions=(400, 100)
        )
        response = window.run()
        if response.clicked:
            self.token = response.text.strip()
            self.config["oauth_token"] = self.token
            self.save_config()
            self.refresh_now(None)

    def refresh_now(self, _):
        """Manual refresh."""
        Thread(target=self.fetch_usage, args=(None,), daemon=True).start()

    def fetch_usage(self, _):
        """Fetch usage data from API."""
        if not self.token:
            return

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
                next_update = now + timedelta(minutes=5)
                self.next_update_item.title = f"Next update: {next_update.strftime('%H:%M:%S')}"

                # Update title icon based on usage (session | weekly)
                def get_icon(util):
                    if util >= 0.8:
                        return "🔴"
                    elif util >= 0.5:
                        return "🟡"
                    return "🟢"

                session_icon = get_icon(session_util)
                weekly_icon = get_icon(weekly_util)
                self.title = f"{session_icon}{weekly_icon} {session_pct}/{weekly_pct}%"

        except urllib.error.HTTPError as e:
            if e.code == 401:
                self.title = "🔑"
                self.status_item.title = "Status: Token expired"
            else:
                self.title = "❌"
                self.status_item.title = f"Status: Error {e.code}"
        except Exception as e:
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

            hours, remainder = divmod(int(diff.total_seconds()), 3600)
            minutes = remainder // 60

            time_str = reset_time.strftime("%I:%M %p")
            if hours > 0:
                return f"{time_str} ({hours}h {minutes}m)"
            return f"{time_str} ({minutes}m)"
        except:
            return "unknown"

if __name__ == "__main__":
    UsageInspectorApp().run()
