# Changelog

## v0.1.2 (2026-02-03)

### Added

- **Settings dialog** - Combined OAuth token and refresh interval on single page
- **Configurable refresh interval** - Set custom update frequency (in seconds)

### Fixed

- **App icon generation** - Properly handle non-square logos

## v0.1.1 (2026-02-03)

### Added

- **About dialog** - Shows version, author info, and license
- **App icon** - Custom logo for the app bundle
- **Cache prevention** - Added no-cache headers to API requests

## v0.1.0 (2026-02-03)

Initial release of Claude Code Usage Inspector.

### Features

- **Dual usage indicators** - Menu bar shows both session (5h) and weekly (7d) usage with separate color-coded icons (`🟢🟡 45/62%`)
- **Color-coded status** - Green (<50%), Yellow (50-80%), Red (80%+)
- **Auto-refresh** - Updates every 5 minutes
- **Manual refresh** - "Refresh Now" button for instant updates
- **Reset timers** - Shows when rate limits will reset
- **Self-contained** - Automatically sets up virtual environment on first run using uv
- **Native macOS app** - Runs as a menu bar utility (LSUIElement)

### Requirements

- macOS 10.15+
- uv package manager
