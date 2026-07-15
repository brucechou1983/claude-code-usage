# Changelog

## v0.3.1 (2026-07-16)

### Fixed

- **System-wide freeze on launch** - Usage data was fetched on background threads that then mutated the menu-bar status item, its icon, and menu items directly. AppKit is not thread-safe; doing this could wedge the shared WindowServer and freeze keyboard/mouse input across all apps (only a reboot recovered), and the app never appeared in Force Quit. All UI updates are now marshalled onto the main thread via `PyObjCTools.AppHelper.callAfter`.
- **Main-thread network stall** - The refresh timer ran its (up to 30s) network requests on the main thread, blocking the run loop. Refreshes now always run on a background worker.

## v0.3.0 (2026-07-11)

### Added

- **Multiple accounts** - Monitor several Claude Code accounts at once, each with its own title and OAuth token
- **Primary account selection** - Pick which account's status is shown in the menu bar; switch anytime from the menu
- **Manage Accounts menu** - Add, edit, and remove accounts without leaving the menu bar

### Changed

- **Settings dialog** - Now only configures the global refresh interval; account tokens moved to Manage Accounts

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
