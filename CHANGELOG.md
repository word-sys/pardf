# Changelog

All notable changes to this project will be documented in this file.

## [1.9.0] - 2026-05-06

### Added
- **Text Decoration Support**: Implemented Underline support across the entire application (UI, PDF export, and formatting tools).
- **Word-Level Selection**: Enhanced selection engine to support granular word selection (Middle-Click) in addition to block-level selection.
- **Improved Highlighting**: Highlight and Remove Highlight tools now respect word-level selection for precise annotations.
- **Top Toolbar Integration**: Added Underline toggle button to the main formatting toolbar with full property synchronization.
- **Internationalization**: Full localization for new features in both English and Turkish.

### Fixed
- **Stability Fixes**: Resolved multiple `UnboundLocalError` and `TypeError` crashes occurring during text formatting and object manipulation.
- **Rendering Alignment**: Fixed "jumbled" or overlapping text bug when splitting sentences for partial formatting (e.g., coloring a single word).
- **Font Width Estimation**: Added safe fallbacks for font width calculation to prevent `ValueError` crashes with custom system fonts.
- **Link Styling**: Fixed bug where web links were losing their blue color and underline state in the editor view.
- **UI Synchronization**: Resolved issues where the toolbar buttons (Bold, Italic, Underline) would occasionally become unresponsive or show incorrect states.

---

## [1.8.3] - 2026-04-24
- Renamed project branding to word-sys's PDF Editor.
- Fixed language support for the About page.
- Improved multi-distro compatibility for the .deb package.
- Architecture changed to `all` to support arm64 and x86_64.
