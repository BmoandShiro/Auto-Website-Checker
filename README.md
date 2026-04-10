# Website Auditer

Website Auditer helps you run structured, pass/fail style checks on a site: emulated **desktop**, **mobile**, and **tablet** views, HTTP link checks, spelling, images, videos, social links, optional business-name matching, and WordPress detection. Use the **PyQt6 GUI** for an interactive table of results, or the **CLI** for CSV output.

## What it checks

Each row can be toggled in the GUI **Config** dialog:

- Fast website/page load speed (desktop/mobile/tablet)
- Navigation bar — responsive menu
- Working links and buttons
- Phone number present in header (not only “book online”)
- Footer — working links
- Spelling and grammar
- Images — resolution / blur heuristic
- Videos — load / reachability
- Social media links vs expected pages
- Business name usage (with optional expected name)
- Rise plugin compatible (WordPress) — manual / TBD style

## Requirements

- Python 3.10+
- Dependencies and Chromium (for browser-based checks):

```bash
pip install -r requirements.txt
python -m playwright install chromium
```

## GUI (recommended)

```bash
python gui.py
```

On Windows: `py -3 gui.py` or `run_gui.bat`.

Paste a URL, optionally set **Expected business name**, then **Run Check**. Use **Settings** for timeouts, themes, and parallelism; **Info** explains the table and each area of the tool.

Portable builds embed Chromium next to the app when you use the build scripts or CI (see below).

## CLI

```bash
python main.py https://example.com --out qa_results.csv
```

If `python` is not on your PATH:

```bash
py -3 main.py https://example.com --out qa_results.csv
```

## Building portable apps

| Platform | Script / workflow | Output |
|----------|-------------------|--------|
| Windows (one folder + bundled Chromium) | `build_portable_dir.bat` | `dist/WebsiteAuditer/` |
| Windows (single EXE) | `build_portable_onefile.bat` | `dist/WebsiteAuditer.exe` |
| macOS DMG | `build_macos_dmg.sh` (on a Mac) | `dist/WebsiteAuditer.dmg` |

PyInstaller uses `assets/app-icon.png` for the window and executable/bundle icon (via Pillow at build time). GitHub Actions **Build Portable Artifacts** uploads matching artifacts (`WebsiteAuditer-windows-dir`, `WebsiteAuditer-windows-exe`, `WebsiteAuditer-macos-dmg`).

Optional: `python -m PyInstaller WebsiteAuditer.spec` (adjust the spec if you change data files).

## CLI CSV columns

- QA Component  
- Y/N  
- Desktop Pass/Fail  
- Mobile Pass/Fail  
- Tablet Pass/Fail  
- Notes  

## Acknowledgements

macOS testing and feedback: @nathanringraham.
