# Auto Website Checker

Simple Python CLI tool that automates your website UX QA checklist section with real device emulation.

## What it checks

- If Inheriting an Exiting Website: Is it a passable design? (manual/TBD)
- Fast website/page load speed (Does it feel fast/snappy?) (desktop/mobile/tablet measured)
- Navigation bar functionality - responsive menu bar (desktop/mobile/tablet emulated)
- Working links & buttons (desktop/mobile/tablet)
- Phone Number Present in Head (NOT Only Book Online) (desktop/mobile/tablet)
- Footer functionality - working links (desktop/mobile/tablet)
- Rise Plugin Compatible (Wordpress) (manual/TBD)
- Core Web Vitals (desktop/mobile via PSI API; tablet mirrors mobile)

Manual/TBD rows are still included for:
- passable design review
- Rise plugin compatibility

## Requirements

- Python 3.10+
- Install dependencies:

```bash
pip install -r requirements.txt
python -m playwright install chromium
```

## Usage

`python main.py https://example.com --out qa_results.csv`

If `python` is not recognized on your machine, use:

`py -3 main.py https://example.com --out qa_results.csv`

## GUI app (PyQt6)

Run:

`python gui.py`

or

`py -3 gui.py`

GUI features:
- Paste website URL
- Click **Run Check**
- View results in a table (Desktop/Mobile/Tablet columns)
- Save displayed results to CSV

## Output

Creates a CSV with columns:
- QA Component
- Y/N
- Desktop Pass/Fail
- Mobile Pass/Fail
- Tablet Pass/Fail
- Notes
