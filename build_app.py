#!/usr/bin/env python3
"""Build a macOS .app bundle for Usage Inspector."""

import os
import stat
from pathlib import Path

APP_DIR = Path(__file__).parent.resolve()
APP_NAME = "Usage Inspector"
BUNDLE_ID = "com.usage-inspector.app"

# App bundle structure
APP_BUNDLE = APP_DIR / f"{APP_NAME}.app"
CONTENTS = APP_BUNDLE / "Contents"
MACOS = CONTENTS / "MacOS"
RESOURCES = CONTENTS / "Resources"

def create_bundle():
    # Clean existing
    if APP_BUNDLE.exists():
        import shutil
        shutil.rmtree(APP_BUNDLE)

    # Create directories
    MACOS.mkdir(parents=True)
    RESOURCES.mkdir(parents=True)

    # Create Info.plist
    info_plist = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleName</key>
    <string>{APP_NAME}</string>
    <key>CFBundleDisplayName</key>
    <string>{APP_NAME}</string>
    <key>CFBundleIdentifier</key>
    <string>{BUNDLE_ID}</string>
    <key>CFBundleVersion</key>
    <string>0.1.0</string>
    <key>CFBundleShortVersionString</key>
    <string>0.1.0</string>
    <key>CFBundleExecutable</key>
    <string>launcher</string>
    <key>CFBundlePackageType</key>
    <string>APPL</string>
    <key>LSMinimumSystemVersion</key>
    <string>10.15</string>
    <key>LSUIElement</key>
    <true/>
    <key>NSHighResolutionCapable</key>
    <true/>
    <key>LSApplicationCategoryType</key>
    <string>public.app-category.utilities</string>
</dict>
</plist>
"""
    (CONTENTS / "Info.plist").write_text(info_plist)

    # Create launcher script
    launcher_script = f"""#!/bin/bash
# Usage Inspector Launcher
# This script sets up the environment and runs the menu bar app

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
APP_DIR="{APP_DIR}"
VENV_DIR="$APP_DIR/.venv"
PYTHON="$VENV_DIR/bin/python"
MAIN_SCRIPT="$APP_DIR/usage_inspector.py"

# First run: setup venv with uv
if [ ! -f "$PYTHON" ]; then
    # Check for uv
    if ! command -v uv &> /dev/null; then
        osascript -e 'display dialog "uv is not installed.\\n\\nInstall it with:\\ncurl -LsSf https://astral.sh/uv/install.sh | sh" buttons {{"OK"}} default button "OK" with icon stop with title "Usage Inspector"'
        exit 1
    fi

    # Create venv
    cd "$APP_DIR"
    uv venv "$VENV_DIR"
    VIRTUAL_ENV="$VENV_DIR" uv pip install rumps pyobjc-framework-Cocoa
fi

# Run the app
exec "$PYTHON" "$MAIN_SCRIPT"
"""
    launcher_path = MACOS / "launcher"
    launcher_path.write_text(launcher_script)
    launcher_path.chmod(launcher_path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    print(f"Created: {APP_BUNDLE}")
    print(f"\nTo run: open '{APP_BUNDLE}'")
    print(f"Or double-click '{APP_NAME}.app' in Finder")

if __name__ == "__main__":
    create_bundle()
