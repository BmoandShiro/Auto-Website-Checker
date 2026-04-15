#!/usr/bin/env python3
"""Simple PyQt6 GUI for Website Auditer."""

from __future__ import annotations

import datetime
import json
import os
import re
import subprocess
import sys
import webbrowser
from dataclasses import asdict
from urllib.parse import urlparse
from typing import Any, List

from PyQt6.QtCore import QThread, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFontMetrics, QIcon
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QHeaderView,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QProgressBar,
    QSpinBox,
    QComboBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
    QSizePolicy,
    QScrollArea,
    QTextEdit,
    QToolButton,
)

from main import QA_ROW_OPTIONS, CheckResult, build_results, install_playwright_chromium, is_chromium_available


APP_DATA_DIR = os.path.join(os.path.expanduser("~"), ".website_auditer")
SETTINGS_PATH = os.path.join(APP_DATA_DIR, "settings.json")
CUSTOM_SPELL_DICT_PATH = os.path.join(APP_DATA_DIR, "custom_spell_words.txt")
DEFAULT_SETTINGS = {
    "timeout_seconds": 30,
    "max_links_per_check": 30,
    "fast_load_ms_threshold": 2500,
    "max_pages_to_audit": 5,
    "request_throttle_seconds": 0.5,
    "parallel_checks": True,
    "parallel_max_workers": 12,
    "custom_spell_dictionary_path": CUSTOM_SPELL_DICT_PATH,
    "expected_business_name": "",
    "ui_font_size": 10,
    "auto_save_last_run": True,
    "results_history_dir": os.path.join(APP_DATA_DIR, "run-history"),
    "ui_theme": "Dark Gray + Blue Accent",
}

APP_VERSION = "v1.0.0"


class SettingsDialog(QDialog):
    def __init__(self, current: dict, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Settings")
        layout = QVBoxLayout(self)
        form = QFormLayout()

        self.timeout = QSpinBox()
        self.timeout.setRange(5, 120)
        self.timeout.setValue(int(current["timeout_seconds"]))
        form.addRow("Timeout (seconds)", self.timeout)

        self.max_links = QSpinBox()
        self.max_links.setRange(1, 200)
        self.max_links.setValue(int(current["max_links_per_check"]))
        form.addRow("Max links per check", self.max_links)

        self.fast_threshold = QSpinBox()
        self.fast_threshold.setRange(500, 15000)
        self.fast_threshold.setSingleStep(100)
        self.fast_threshold.setValue(int(current["fast_load_ms_threshold"]))
        form.addRow("Fast threshold (ms)", self.fast_threshold)

        self.max_pages = QSpinBox()
        self.max_pages.setRange(1, 25)
        self.max_pages.setValue(int(current["max_pages_to_audit"]))
        form.addRow("Max pages to audit", self.max_pages)

        self.throttle = QDoubleSpinBox()
        self.throttle.setRange(0.0, 5.0)
        self.throttle.setSingleStep(0.1)
        self.throttle.setValue(float(current["request_throttle_seconds"]))
        form.addRow("HTTP throttle (seconds)", self.throttle)

        self.parallel_checks = QCheckBox("Run parallel HTTP / media checks (faster)")
        self.parallel_checks.setChecked(bool(current.get("parallel_checks", True)))
        form.addRow(self.parallel_checks)

        self.parallel_workers = QSpinBox()
        self.parallel_workers.setRange(2, 32)
        self.parallel_workers.setValue(int(current.get("parallel_max_workers", 12)))
        form.addRow("Parallel max workers", self.parallel_workers)
        self.parallel_checks.toggled.connect(self.parallel_workers.setEnabled)
        self.parallel_workers.setEnabled(self.parallel_checks.isChecked())

        self.ui_font_size = QSpinBox()
        self.ui_font_size.setRange(8, 20)
        self.ui_font_size.setValue(int(current.get("ui_font_size", 10)))
        form.addRow("UI font size", self.ui_font_size)

        self.ui_theme = QComboBox()
        self.ui_theme.addItems(
            ["Dark Gray", "Dark Gray + Blue Accent", "Dark Gray + Orange Accent", "Dark Blue", "Dark Purple", "Light"]
        )
        current_theme = str(current.get("ui_theme", "Dark Gray"))
        idx = self.ui_theme.findText(current_theme)
        self.ui_theme.setCurrentIndex(idx if idx >= 0 else 0)
        form.addRow("Theme", self.ui_theme)

        self.auto_save_last_run = QCheckBox("Auto-save run results to history")
        self.auto_save_last_run.setChecked(bool(current.get("auto_save_last_run", True)))
        form.addRow(self.auto_save_last_run)

        layout.addLayout(form)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def to_settings(self) -> dict:
        return {
            "timeout_seconds": int(self.timeout.value()),
            "max_links_per_check": int(self.max_links.value()),
            "fast_load_ms_threshold": int(self.fast_threshold.value()),
            "max_pages_to_audit": int(self.max_pages.value()),
            "request_throttle_seconds": float(self.throttle.value()),
            "parallel_checks": bool(self.parallel_checks.isChecked()),
            "parallel_max_workers": int(self.parallel_workers.value()),
            "ui_font_size": int(self.ui_font_size.value()),
            "ui_theme": self.ui_theme.currentText(),
            "auto_save_last_run": bool(self.auto_save_last_run.isChecked()),
        }


class RowConfigDialog(QDialog):
    def __init__(self, current: dict, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("QA Row Config")
        layout = QVBoxLayout(self)
        self.checkboxes: dict[str, QCheckBox] = {}
        enabled_rows = current.get("enabled_rows") or {}
        for key, label in QA_ROW_OPTIONS:
            cb = QCheckBox(label)
            cb.setChecked(bool(enabled_rows.get(key, True)))
            self.checkboxes[key] = cb
            layout.addWidget(cb)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def selected_rows(self) -> dict:
        return {k: bool(cb.isChecked()) for k, cb in self.checkboxes.items()}


class ProgramInfoDialog(QDialog):
    """About the app plus a short reference for abbreviations and how checks work."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Website Auditer — Help")
        self.resize(580, 520)
        layout = QVBoxLayout(self)
        text = QTextEdit()
        text.setReadOnly(True)
        text.setPlainText(
            "ABOUT WEBSITE AUDITER\n"
            "---------------------\n"
            f"Website Auditer ({APP_VERSION}) runs automated, QA-style checks on a website you choose: "
            "desktop, mobile, and tablet browser emulation, HTTP link checks, spelling, images, videos, "
            "social links, business name hints, and WordPress detection — with clear Pass / Fail / Manual "
            "or TBD where a person should decide.\n\n"
            "Enter a URL (https is added if you omit it), optionally set Expected Business Name for "
            "matching checks, then Run Check. Use Settings for timeouts, themes, parallelism, and "
            "Config to turn individual table rows on or off. Browser-based rows need Chromium; if they "
            "show Manual, try Install Browser Dependency or confirm Chromium is installed.\n\n"
            "READING THE RESULTS TABLE\n"
            "------------------------\n"
            "• Y/N — Overall yes/no for that check (TBD = needs a human decision).\n"
            "• Desktop / Mobile / Tablet — Outcome for that viewport profile (Pass / Fail / Manual).\n"
            "• Notes — Extra detail; device labels are shortened:\n"
            "    D: = Desktop    M: = Mobile    T: = Tablet\n"
            "  Example: \"D: … | M: … | T: …\" means one note per device type.\n"
            "• Manual — Website Auditer could not finish automatically (often missing Chromium or timeouts).\n"
            "• N/A — Not applicable for that check (e.g. no social links found to evaluate).\n\n"
            "SCOPE (MAX PAGES SETTING)\n"
            "-------------------------\n"
            "• \"Max pages to audit\" limits how many internal URLs are discovered and then used for:\n"
            "  browser runs (desktop/mobile/tablet), fetched HTML, spelling, images, videos,\n"
            "  business name, and social-link detection (combined across those pages).\n"
            "• WordPress detection uses the starting URL only.\n\n"
            "SOCIAL MEDIA ROW\n"
            "----------------\n"
            "• Website Auditer only does a quick HTTP check on social URLs and optional name matching.\n"
            "• It cannot prove a profile is the official business account.\n"
            "• \"Conflict\" in notes means two different handles/accounts appeared for the same platform.\n"
            "• Use the Social links list below the table to open URLs and verify by eye.\n\n"
            "SPELLING & GRAMMAR\n"
            "------------------\n"
            "• Uses a dictionary heuristic — industry terms and names are often flagged.\n"
            "• Each word shows example page URLs and short text snippets where it appeared.\n"
            "• \"Add\" saves the word to your personal dictionary file (re-run the check to apply).\n\n"
            "IMAGES & VIDEOS\n"
            "---------------\n"
            "• Images: pass/fail is blur-only (edge-detection variance); small but sharp files are OK.\n"
            "• Videos: URL reachability; CDNs and embeds can confuse checks.\n"
            "• If a row fails, confirm in a normal browser.\n\n"
            "PERFORMANCE\n"
            "-----------\n"
            "• Settings → \"Parallel\" runs many link/media requests at once for speed.\n"
            "• Lower \"Max pages\" or \"Max links\" if runs feel too slow.\n"
        )
        layout.addWidget(text)
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        layout.addWidget(close_btn, alignment=Qt.AlignmentFlag.AlignRight)


class AuditWorker(QThread):
    finished_ok = pyqtSignal(list)
    row_ready = pyqtSignal(object)
    status = pyqtSignal(str)
    social_links_ready = pyqtSignal(list, list)
    pages_checked_ready = pyqtSignal(list)
    spelling_issues_ready = pyqtSignal(list)
    row_details_ready = pyqtSignal(dict)
    progress = pyqtSignal(int, int)
    failed = pyqtSignal(str)

    def __init__(self, url: str, settings: dict) -> None:
        super().__init__()
        self.url = url
        self.settings = settings

    def run(self) -> None:
        try:
            def emit_row(row: CheckResult) -> None:
                self.row_ready.emit(row)

            def emit_status(message: str) -> None:
                self.status.emit(message)

            def emit_progress(done: int, total: int) -> None:
                self.progress.emit(done, total)

            def emit_social_links(links: list, conflicts: list) -> None:
                self.social_links_ready.emit(links, conflicts)

            def emit_pages_checked(pages: list) -> None:
                self.pages_checked_ready.emit(pages)

            def emit_spelling_issues(words: list) -> None:
                self.spelling_issues_ready.emit(words)

            def emit_row_details(details: dict) -> None:
                self.row_details_ready.emit(details)

            results = build_results(
                self.url,
                on_row=emit_row,
                on_status=emit_status,
                on_social_links=emit_social_links,
                on_pages_checked=emit_pages_checked,
                on_spelling_issues=emit_spelling_issues,
                on_row_details=emit_row_details,
                on_progress=emit_progress,
                settings=self.settings,
            )
            self.finished_ok.emit(results)
        except Exception as exc:  # pragma: no cover
            self.failed.emit(str(exc))


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Website Auditer")
        self.resize(1100, 600)
        self.settings = self._load_settings()
        self.results: List[CheckResult] = []
        self.row_details_map: dict = {}
        self.latest_social_links: list = []
        self.latest_social_conflicts: list = []
        self.latest_pages_checked: list = []
        self.latest_spelling_issues: list = []
        self.worker: AuditWorker | None = None
        self._report_meta: dict = {}

        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)

        url_row = QHBoxLayout()
        url_row.addWidget(QLabel("Website URL:"))
        self.url_input = QLineEdit()
        self.url_input.setPlaceholderText("https://example.com")
        url_row.addWidget(self.url_input)

        self.run_btn = QPushButton("Run Check")
        self.run_btn.clicked.connect(self.run_audit)
        url_row.addWidget(self.run_btn)

        self.settings_btn = QPushButton("Settings")
        self.settings_btn.clicked.connect(self.open_settings)
        url_row.addWidget(self.settings_btn)
        self.config_btn = QPushButton("Config")
        self.config_btn.clicked.connect(self.open_row_config)
        url_row.addWidget(self.config_btn)
        self.info_btn = QPushButton("Info")
        self.info_btn.setToolTip("About Website Auditer, how to read results, and what each check means.")
        self.info_btn.clicked.connect(self.show_program_info)
        url_row.addWidget(self.info_btn)
        layout.addLayout(url_row)

        business_row = QHBoxLayout()
        business_row.addWidget(QLabel("Expected Business Name:"))
        self.business_name_input = QLineEdit()
        self.business_name_input.setPlaceholderText("e.g. Renew Dental Loft")
        self.business_name_input.setText(str(self.settings.get("expected_business_name", "")))
        business_row.addWidget(self.business_name_input)
        layout.addLayout(business_row)

        self.status_label = QLabel("Enter a URL and click Run Check.")
        self.status_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.status_label)

        self.progress_label = QLabel("Check progress")
        self.progress_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.progress_label)
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        layout.addWidget(self.progress_bar)

        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(
            [
                "QA Component",
                "Y/N",
                "Desktop Pass/Fail",
                "Mobile Pass/Fail",
                "Tablet Pass/Fail",
                "Notes",
            ]
        )
        self.table.setWordWrap(True)
        self.table.setTextElideMode(Qt.TextElideMode.ElideNone)
        self.table.verticalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setColumnWidth(0, 430)
        self.table.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.table.setMinimumHeight(280)
        self.table.cellClicked.connect(self.on_table_cell_clicked)
        layout.addWidget(self.table)

        self.social_label = QLabel("Social links (double-click to open)")
        self.social_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.social_label)
        self.social_toggle = QToolButton()
        self.social_toggle.setText("Social links \u25be")
        self.social_toggle.setCheckable(True)
        self.social_toggle.setChecked(False)
        self.social_toggle.toggled.connect(self.toggle_social_panel)
        layout.addWidget(self.social_toggle)
        self.social_list = QListWidget()
        self.social_list.itemDoubleClicked.connect(self.open_social_link)
        self.social_list.setMinimumHeight(120)
        self.social_list.setVisible(False)
        layout.addWidget(self.social_list)

        self.pages_toggle = QToolButton()
        self.pages_toggle.setText("Pages checked \u25be")
        self.pages_toggle.setCheckable(True)
        self.pages_toggle.setChecked(False)
        self.pages_toggle.toggled.connect(self.toggle_pages_panel)
        layout.addWidget(self.pages_toggle)

        self.pages_list = QListWidget()
        self.pages_list.setVisible(False)
        self.pages_list.setMinimumHeight(100)
        self.pages_list.itemDoubleClicked.connect(self.open_page_link)
        layout.addWidget(self.pages_list)

        self.spell_toggle = QToolButton()
        self.spell_toggle.setText("Spelling/grammar unknown words \u25be")
        self.spell_toggle.setCheckable(True)
        self.spell_toggle.setChecked(False)
        self.spell_toggle.toggled.connect(self.toggle_spell_panel)
        layout.addWidget(self.spell_toggle)

        self.spell_scroll = QScrollArea()
        self.spell_scroll.setWidgetResizable(True)
        self.spell_scroll.setVisible(False)
        self.spell_scroll.setMinimumHeight(140)
        self.spell_inner = QWidget()
        self.spell_rows_layout = QVBoxLayout(self.spell_inner)
        self.spell_rows_layout.setContentsMargins(4, 4, 4, 4)
        self.spell_rows_layout.setSpacing(6)
        self.spell_scroll.setWidget(self.spell_inner)
        layout.addWidget(self.spell_scroll)

        history_row = QHBoxLayout()
        history_row.addWidget(QLabel("Recent runs:"))
        self.history_combo = QComboBox()
        history_row.addWidget(self.history_combo)
        self.load_history_btn = QPushButton("Load")
        self.load_history_btn.clicked.connect(self.load_selected_history_run)
        history_row.addWidget(self.load_history_btn)
        self.export_report_btn = QPushButton("Export…")
        self.export_report_btn.clicked.connect(self.export_current_report)
        history_row.addWidget(self.export_report_btn)
        layout.addLayout(history_row)
        layout.setStretch(0, 0)  # URL row
        layout.setStretch(1, 0)  # business row
        layout.setStretch(2, 0)  # status
        layout.setStretch(3, 0)  # progress label
        layout.setStretch(4, 0)  # progress bar
        layout.setStretch(5, 1)  # table gets remaining height
        layout.setStretch(6, 0)  # social label
        layout.setStretch(7, 0)  # social toggle
        layout.setStretch(8, 0)  # social list
        layout.setStretch(9, 0)  # pages toggle
        layout.setStretch(10, 0)  # pages list
        layout.setStretch(11, 0)  # spell toggle
        layout.setStretch(12, 0)  # spell list
        layout.setStretch(13, 0)  # history row
        layout.setStretch(14, 0)  # footer

        footer_row = QHBoxLayout()
        self.credit_label = QLabel("Created by: BMOandShiro")
        self.version_label = QLabel(APP_VERSION)
        footer_row.addWidget(self.credit_label)
        footer_row.addStretch()
        footer_row.addWidget(self.version_label)
        layout.addLayout(footer_row)
        self._apply_ui_font_size()
        self._apply_theme()
        self.refresh_history_dropdown()
        self._startup_browser_prompted = False

    @staticmethod
    def _style_result_cell(item: QTableWidgetItem, value: str) -> None:
        normalized = value.strip().lower()
        if normalized in ("yes", "pass"):
            item.setBackground(QColor(198, 239, 206))
            item.setForeground(QColor(0, 97, 0))
        elif normalized in ("no", "fail"):
            item.setBackground(QColor(255, 199, 206))
            item.setForeground(QColor(156, 0, 6))
        elif normalized in ("n/a", "na"):
            item.setBackground(QColor(228, 228, 231))
            item.setForeground(QColor(63, 63, 70))

    def run_audit(self) -> None:
        url = self.url_input.text().strip()
        if not url:
            QMessageBox.warning(self, "Invalid URL", "Please enter a website URL.")
            return
        # Friendly first-run behavior: accept bare domains and normalize.
        if not url.startswith(("http://", "https://")):
            url = f"https://{url}"
            self.url_input.setText(url)

        # Preflight: if browser-dependent rows are enabled and Chromium is missing,
        # prompt to install first (before running checks).
        self._prompt_install_chromium_if_needed("Chromium Required")

        self.run_btn.setEnabled(False)
        self.results = []
        self.row_details_map = {}
        self.table.setRowCount(0)
        self.social_list.clear()
        self.social_toggle.setChecked(False)
        self.social_toggle.setText("Social links \u25be")
        self.pages_list.clear()
        self._clear_spell_rows()
        self.spell_toggle.setChecked(False)
        self.spell_toggle.setText("Spelling/grammar unknown words \u25be")
        self.social_label.setText("Social links (double-click to open)")
        self.status_label.setText("Running checks... this may take a minute.")
        self.progress_bar.setValue(0)
        self.settings["expected_business_name"] = self.business_name_input.text().strip()
        self._save_settings()
        self.worker = AuditWorker(url, self.settings)
        self.worker.finished_ok.connect(self.on_success)
        self.worker.row_ready.connect(self._append_row)
        self.worker.status.connect(self.status_label.setText)
        self.worker.social_links_ready.connect(self.on_social_links_ready)
        self.worker.pages_checked_ready.connect(self.on_pages_checked_ready)
        self.worker.spelling_issues_ready.connect(self.on_spelling_issues_ready)
        self.worker.row_details_ready.connect(self.on_row_details_ready)
        self.worker.progress.connect(self.on_check_progress)
        self.worker.failed.connect(self.on_error)
        self.worker.start()

    def _load_settings(self) -> dict:
        os.makedirs(APP_DATA_DIR, exist_ok=True)
        if not os.path.exists(SETTINGS_PATH):
            return dict(DEFAULT_SETTINGS)
        try:
            with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
                raw = json.load(f)
            if not isinstance(raw, dict):
                return dict(DEFAULT_SETTINGS)
            merged = dict(DEFAULT_SETTINGS)
            merged.update(raw)
            er = merged.get("enabled_rows")
            if isinstance(er, dict):
                er = dict(er)
                er.pop("core_web_vitals", None)
                er.pop("passable_design", None)
                merged["enabled_rows"] = er
            return merged
        except Exception:
            return dict(DEFAULT_SETTINGS)

    def showEvent(self, event) -> None:  # type: ignore[override]
        super().showEvent(event)
        if not self._startup_browser_prompted:
            self._startup_browser_prompted = True
            self._prompt_install_chromium_if_needed("Install Browser Dependency")

    def _prompt_install_chromium_if_needed(self, title: str) -> None:
        if is_chromium_available():
            return
        choice = QMessageBox.question(
            self,
            title,
            "Chromium is missing and browser-based checks are enabled.\n\nInstall Chromium now?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if choice == QMessageBox.StandardButton.Yes:
            self.install_browser_dependency()
            if not is_chromium_available():
                QMessageBox.warning(
                    self,
                    "Chromium Still Missing",
                    "Could not verify Chromium installation. Checks may fall back to Manual.",
                )

    def _save_settings(self) -> None:
        os.makedirs(APP_DATA_DIR, exist_ok=True)
        with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
            json.dump(self.settings, f, indent=2)

    def _apply_ui_font_size(self) -> None:
        size = int(self.settings.get("ui_font_size", 10))
        font = self.font()
        font.setPointSize(size)
        self.setFont(font)

    def _apply_theme(self) -> None:
        theme = str(self.settings.get("ui_theme", "Dark Gray"))
        if theme == "Dark Blue":
            self.setStyleSheet(
                "QWidget { background:#0f172a; color:#e2e8f0; }"
                "QLabel { color:#cbd5e1; }"
                "QLineEdit,QTextEdit,QListWidget,QTableWidget,QComboBox { background:#111827; color:#e5e7eb; border:1px solid #334155; border-radius:8px; padding:4px; }"
                "QLineEdit:focus,QTextEdit:focus,QListWidget:focus,QTableWidget:focus,QComboBox:focus { border:1px solid #60a5fa; }"
                "QPushButton,QToolButton { background:#1d4ed8; color:#f8fafc; border:1px solid #3b82f6; border-radius:10px; padding:6px 10px; }"
                "QPushButton:hover,QToolButton:hover { background:#2563eb; }"
                "QPushButton:pressed,QToolButton:pressed,QPushButton:checked,QToolButton:checked { background:#60a5fa; color:#0f172a; }"
                "QHeaderView::section { background:#111827; color:#e5e7eb; border:0; padding:6px; }"
                "QProgressBar { border:1px solid #334155; border-radius:8px; background:#111827; color:#e2e8f0; text-align:center; }"
                "QProgressBar::chunk { background:#3b82f6; border-radius:8px; }"
            )
        elif theme == "Dark Purple":
            self.setStyleSheet(
                "QWidget { background:#140b24; color:#f5e9ff; }"
                "QLabel { color:#e9d5ff; }"
                "QLineEdit,QTextEdit,QListWidget,QTableWidget,QComboBox { background:#1f1233; color:#f8f0ff; border:1px solid #6b21a8; border-radius:8px; padding:4px; }"
                "QLineEdit:focus,QTextEdit:focus,QListWidget:focus,QTableWidget:focus,QComboBox:focus { border:1px solid #c084fc; }"
                "QPushButton,QToolButton { background:#6d28d9; color:#fdf4ff; border:1px solid #a855f7; border-radius:10px; padding:6px 10px; }"
                "QPushButton:hover,QToolButton:hover { background:#7c3aed; }"
                "QPushButton:pressed,QToolButton:pressed,QPushButton:checked,QToolButton:checked { background:#c084fc; color:#1f1233; }"
                "QHeaderView::section { background:#1f1233; color:#f8f0ff; border:0; padding:6px; }"
                "QProgressBar { border:1px solid #6b21a8; border-radius:8px; background:#1f1233; color:#f5e9ff; text-align:center; }"
                "QProgressBar::chunk { background:#a855f7; border-radius:8px; }"
            )
        elif theme == "Light":
            self.setStyleSheet(
                "QWidget { background:#f8fafc; color:#0f172a; }"
                "QLabel { color:#334155; }"
                "QLineEdit,QTextEdit,QListWidget,QTableWidget,QComboBox { background:#ffffff; color:#0f172a; border:1px solid #d1d5db; border-radius:8px; padding:4px; }"
                "QLineEdit:focus,QTextEdit:focus,QListWidget:focus,QTableWidget:focus,QComboBox:focus { border:1px solid #2563eb; }"
                "QPushButton,QToolButton { background:#ffffff; color:#1e293b; border:1px solid #cbd5e1; border-radius:10px; padding:6px 10px; }"
                "QPushButton:hover,QToolButton:hover { background:#f1f5f9; }"
                "QPushButton:pressed,QToolButton:pressed,QPushButton:checked,QToolButton:checked { background:#2563eb; color:#ffffff; border:1px solid #2563eb; }"
                "QHeaderView::section { background:#f1f5f9; color:#334155; border:0; padding:6px; }"
                "QProgressBar { border:1px solid #d1d5db; border-radius:8px; background:#ffffff; color:#334155; text-align:center; }"
                "QProgressBar::chunk { background:#2563eb; border-radius:8px; }"
            )
        elif theme == "Dark Gray + Blue Accent":
            self.setStyleSheet(
                "QWidget { background:#18181b; color:#e4e4e7; }"
                "QLabel { color:#d4d4d8; }"
                "QLineEdit,QTextEdit,QListWidget,QTableWidget,QComboBox { background:#27272a; color:#f4f4f5; border:1px solid #3f3f46; border-radius:8px; padding:4px; selection-background-color:#2563eb; selection-color:#ffffff; }"
                "QLineEdit:focus,QTextEdit:focus,QListWidget:focus,QTableWidget:focus,QComboBox:focus { border:1px solid #3f3f46; }"
                "QPushButton,QToolButton { background:#27272a; color:#f4f4f5; border:1px solid #52525b; border-radius:10px; padding:6px 10px; }"
                "QPushButton:hover,QToolButton:hover { background:#1d4ed8; }"
                "QPushButton:pressed,QToolButton:pressed,QPushButton:checked,QToolButton:checked { background:#3b82f6; color:#ffffff; border:1px solid #52525b; }"
                "QHeaderView::section { background:#27272a; color:#f4f4f5; border:0; padding:6px; }"
                "QProgressBar { border:1px solid #3f3f46; border-radius:8px; background:#27272a; color:#f4f4f5; text-align:center; }"
                "QProgressBar::chunk { background:#3b82f6; border-radius:8px; }"
            )
        elif theme == "Dark Gray + Orange Accent":
            self.setStyleSheet(
                "QWidget { background:#18181b; color:#e4e4e7; }"
                "QLabel { color:#d4d4d8; }"
                "QLineEdit,QTextEdit,QListWidget,QTableWidget,QComboBox { background:#27272a; color:#f4f4f5; border:1px solid #3f3f46; border-radius:8px; padding:4px; selection-background-color:#ea580c; selection-color:#ffffff; }"
                "QLineEdit:focus,QTextEdit:focus,QListWidget:focus,QTableWidget:focus,QComboBox:focus { border:1px solid #3f3f46; }"
                "QPushButton,QToolButton { background:#27272a; color:#f4f4f5; border:1px solid #52525b; border-radius:10px; padding:6px 10px; }"
                "QPushButton:hover,QToolButton:hover { background:#9a3412; }"
                "QPushButton:pressed,QToolButton:pressed,QPushButton:checked,QToolButton:checked { background:#f97316; color:#111111; border:1px solid #52525b; }"
                "QHeaderView::section { background:#27272a; color:#f4f4f5; border:0; padding:6px; }"
                "QProgressBar { border:1px solid #3f3f46; border-radius:8px; background:#27272a; color:#f4f4f5; text-align:center; }"
                "QProgressBar::chunk { background:#f97316; border-radius:8px; }"
            )
        else:
            self.setStyleSheet(
                "QWidget { background:#18181b; color:#e4e4e7; }"
                "QLabel { color:#d4d4d8; }"
                "QLineEdit,QTextEdit,QListWidget,QTableWidget,QComboBox { background:#27272a; color:#f4f4f5; border:1px solid #3f3f46; border-radius:8px; padding:4px; selection-background-color:#52525b; selection-color:#fafafa; }"
                "QLineEdit:focus,QTextEdit:focus,QListWidget:focus,QTableWidget:focus,QComboBox:focus { border:1px solid #71717a; }"
                "QPushButton,QToolButton { background:#27272a; color:#f4f4f5; border:1px solid #52525b; border-radius:10px; padding:6px 10px; }"
                "QPushButton:hover,QToolButton:hover { background:#3f3f46; }"
                "QPushButton:pressed,QToolButton:pressed,QPushButton:checked,QToolButton:checked { background:#52525b; color:#ffffff; border:1px solid #71717a; }"
                "QHeaderView::section { background:#27272a; color:#f4f4f5; border:0; padding:6px; }"
                "QProgressBar { border:1px solid #3f3f46; border-radius:8px; background:#27272a; color:#f4f4f5; text-align:center; }"
                "QProgressBar::chunk { background:#71717a; border-radius:8px; }"
            )

    def open_settings(self) -> None:
        dialog = SettingsDialog(self.settings, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            updated = dialog.to_settings()
            updated["results_history_dir"] = self.settings.get("results_history_dir", DEFAULT_SETTINGS["results_history_dir"])
            updated["expected_business_name"] = self.settings.get("expected_business_name", "")
            updated["enabled_rows"] = self.settings.get("enabled_rows", {})
            updated["custom_spell_dictionary_path"] = self.settings.get(
                "custom_spell_dictionary_path", DEFAULT_SETTINGS["custom_spell_dictionary_path"]
            )
            self.settings = updated
            self._save_settings()
            self._apply_ui_font_size()
            self._apply_theme()
            self.status_label.setText("Settings saved.")

    def open_row_config(self) -> None:
        dialog = RowConfigDialog(self.settings, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.settings["enabled_rows"] = dialog.selected_rows()
            self._save_settings()
            self.status_label.setText("Row config saved.")

    def show_program_info(self) -> None:
        ProgramInfoDialog(self).exec()

    def _spell_dict_path(self) -> str:
        p = str(self.settings.get("custom_spell_dictionary_path", "") or "").strip()
        return p if p else CUSTOM_SPELL_DICT_PATH

    def _clear_spell_rows(self) -> None:
        while self.spell_rows_layout.count():
            item = self.spell_rows_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()

    def _add_word_to_dictionary(self, word: str, add_btn: QPushButton | None = None) -> None:
        w = word.strip()
        if not w:
            return
        path = self._spell_dict_path()
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        existing: set[str] = set()
        if os.path.isfile(path):
            try:
                with open(path, encoding="utf-8") as f:
                    existing = {ln.strip().lower() for ln in f if ln.strip() and not ln.strip().startswith("#")}
            except OSError:
                pass
        if w.lower() in existing:
            self.status_label.setText(f"'{w}' is already in your custom dictionary.")
            if add_btn is not None:
                add_btn.setVisible(False)
            return
        try:
            with open(path, "a", encoding="utf-8") as f:
                f.write(w + "\n")
        except OSError as exc:
            self.status_label.setText(f"Could not save '{w}' to dictionary: {exc}")
            return
        if add_btn is not None:
            add_btn.setVisible(False)
        self.status_label.setText(
            f"Added '{w}' to custom dictionary. Re-run the check to refresh spelling."
        )

    def on_success(self, results: list) -> None:
        self.results = results
        self._fit_qa_column()
        self.run_btn.setEnabled(True)
        self.progress_bar.setValue(100)
        browser_missing = any("browser unavailable" in (r.notes or "").lower() for r in results)
        if bool(self.settings.get("auto_save_last_run", True)):
            self._save_current_run_to_history()
            self.refresh_history_dropdown()
        else:
            self._report_meta = {
                "url": self.url_input.text().strip(),
                "saved_at": datetime.datetime.now().isoformat(timespec="seconds"),
                "source_file": "",
            }
        if browser_missing:
            self.status_label.setText("Complete with manual fallback: browser dependency missing. Click 'Install Browser Dependency'.")
            choice = QMessageBox.question(
                self,
                "Browser Dependency Missing",
                "Chromium is missing, so browser-based checks were set to Manual.\n\nInstall Chromium now?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if choice == QMessageBox.StandardButton.Yes:
                self.install_browser_dependency()
        else:
            self.status_label.setText("Complete. Results displayed below.")

    def _fit_qa_column(self) -> None:
        # Auto-size QA Component column to avoid wrapping its text.
        if not self.results:
            return
        fm = QFontMetrics(self.table.font())
        max_width = fm.horizontalAdvance("QA Component")
        for result in self.results:
            max_width = max(max_width, fm.horizontalAdvance(result.component))
        # Add padding and clamp so the table remains usable.
        self.table.setColumnWidth(0, max(260, min(max_width + 28, 760)))

    def on_check_progress(self, done: int, total: int) -> None:
        if total <= 0:
            self.progress_bar.setValue(0)
            return
        pct = int((done / total) * 100)
        self.progress_bar.setValue(max(0, min(100, pct)))

    def _append_row(self, result: CheckResult) -> None:
        row = asdict(result)
        component_label = f"\u25b8 {row['component']}"
        values = [
            component_label,
            row["yes_no"],
            row["desktop"],
            row["mobile"],
            row["tablet"],
            row["notes"],
        ]
        row_idx = self.table.rowCount()
        self.table.insertRow(row_idx)
        for col_idx, value in enumerate(values):
            item = QTableWidgetItem(str(value))
            self._style_result_cell(item, str(value))
            self.table.setItem(row_idx, col_idx, item)
        self.table.resizeRowsToContents()

    def on_error(self, message: str) -> None:
        self.run_btn.setEnabled(True)
        self.progress_bar.setValue(0)
        self.status_label.setText("Check failed.")
        QMessageBox.critical(self, "Run failed", message)

    def install_browser_dependency(self) -> None:
        answer = QMessageBox.question(
            self,
            "Install Browser Dependency",
            "Install Chromium now for browser-based checks? This can take a few minutes.",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.status_label.setText("Installing Chromium dependency...")
        try:
            install_playwright_chromium(timeout_s=600)
            self.status_label.setText("Chromium installed. Re-run checks to enable browser-based rows.")
            QMessageBox.information(self, "Install complete", "Chromium installed successfully.")
        except Exception as exc:
            self.status_label.setText("Browser install failed.")
            QMessageBox.critical(self, "Install failed", str(exc))

    def on_social_links_ready(self, links: list, conflicts: list) -> None:
        self.latest_social_links = links
        self.latest_social_conflicts = conflicts
        self.social_list.clear()
        for entry in links:
            platform = entry.get("platform", "social")
            url = entry.get("url", "")
            account = entry.get("account_key", "")
            item = QListWidgetItem(f"[{platform}] {account} -> {url}")
            item.setData(Qt.ItemDataRole.UserRole, url)
            self.social_list.addItem(item)
        if conflicts:
            self.social_label.setText(f"Social links (conflicts found: {', '.join(conflicts)})")
        else:
            self.social_label.setText("Social links (double-click to open)")

    def toggle_social_panel(self, checked: bool) -> None:
        self.social_list.setVisible(checked)
        self.social_toggle.setText("Social links \u25b4" if checked else "Social links \u25be")

    def on_pages_checked_ready(self, pages: list) -> None:
        self.latest_pages_checked = pages
        self.pages_list.clear()
        for page in pages:
            item = QListWidgetItem(page)
            item.setData(Qt.ItemDataRole.UserRole, page)
            self.pages_list.addItem(item)

    def toggle_pages_panel(self, checked: bool) -> None:
        self.pages_list.setVisible(checked)
        self.pages_toggle.setText("Pages checked \u25b4" if checked else "Pages checked \u25be")

    def on_spelling_issues_ready(self, items: list) -> None:
        self.latest_spelling_issues = items
        self._clear_spell_rows()
        for raw in items:
            if isinstance(raw, str):
                entry: dict[str, Any] = {"word": raw, "pages": [], "snippets": []}
            else:
                entry = dict(raw) if isinstance(raw, dict) else {"word": str(raw), "pages": [], "snippets": []}
            word = str(entry.get("word", "")).strip() or "(unknown)"
            pages = entry.get("pages") or []
            snippets = entry.get("snippets") or []

            row_widget = QWidget()
            row_layout = QHBoxLayout(row_widget)
            row_layout.setContentsMargins(2, 2, 2, 2)
            left = QVBoxLayout()
            wlab = QLabel(word)
            wf = wlab.font()
            wf.setBold(True)
            wlab.setFont(wf)
            wlab.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            left.addWidget(wlab)
            if pages:
                plab = QLabel("Pages: " + "; ".join(str(p) for p in pages[:6]))
                plab.setWordWrap(True)
                plab.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
                left.addWidget(plab)
            if snippets:
                slab = QLabel("Context: " + " | ".join(f"«{s}»" for s in snippets[:3]))
                slab.setWordWrap(True)
                slab.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
                left.addWidget(slab)
            row_layout.addLayout(left, stretch=1)
            add_btn = QPushButton("Add to dictionary")
            add_btn.setToolTip(f"Append '{word}' to your personal word list")
            add_btn.clicked.connect(
                lambda _checked=False, w=word, b=add_btn: self._add_word_to_dictionary(w, b)
            )
            row_layout.addWidget(add_btn, alignment=Qt.AlignmentFlag.AlignTop)
            self.spell_rows_layout.addWidget(row_widget)

    def toggle_spell_panel(self, checked: bool) -> None:
        self.spell_scroll.setVisible(checked)
        self.spell_toggle.setText(
            "Spelling/grammar unknown words \u25b4" if checked else "Spelling/grammar unknown words \u25be"
        )

    def on_row_details_ready(self, details: dict) -> None:
        self.row_details_map = details

    @staticmethod
    def _abbrev_pass_cell(val: str) -> str:
        v = (val or "").strip().lower()
        if v in ("pass", "yes"):
            return "Pass"
        if v in ("fail", "no"):
            return "Fail"
        if v in ("manual", "tbd"):
            return "Man"
        if v in ("n/a", "na"):
            return "N/A"
        return (val or "-")[:8]

    @staticmethod
    def _abbrev_overall_yes_no(yes_no: str) -> str:
        v = (yes_no or "").strip().lower()
        if v == "yes":
            return "Pass"
        if v == "no":
            return "Fail"
        if v == "tbd":
            return "TBD"
        if v in ("n/a", "na"):
            return "N/A"
        return (yes_no or "-")[:6]

    def _build_dashboard_lines(self) -> list[str]:
        """Condensed pass/fail overview for export header."""
        lines: list[str] = []
        lines.append("QUICK DASHBOARD (pass / fail by check)")
        lines.append("-" * 56)
        hdr = f"{'Check':<46} {'Ovl':>4} {'D':>4} {'M':>4} {'T':>4}"
        lines.append(hdr)
        lines.append("-" * 56)
        pass_ov = fail_ov = tbd_ov = na_ov = 0
        for r in self.results:
            title = r.component
            if len(title) > 46:
                title = title[:43] + "..."
            ovl = self._abbrev_overall_yes_no(r.yes_no)
            yn = r.yes_no.strip().lower()
            if yn == "yes":
                pass_ov += 1
            elif yn == "no":
                fail_ov += 1
            elif yn in ("n/a", "na"):
                na_ov += 1
            else:
                tbd_ov += 1
            lines.append(
                f"{title:<46} {ovl:>4} {self._abbrev_pass_cell(r.desktop):>4} "
                f"{self._abbrev_pass_cell(r.mobile):>4} {self._abbrev_pass_cell(r.tablet):>4}"
            )
        lines.append("-" * 56)
        lines.append(
            f"Counts — Pass: {pass_ov}   Fail: {fail_ov}   N/A: {na_ov}   TBD/other: {tbd_ov}   (Overall column)"
        )
        lines.append("  Ovl = overall Y/N   D/M/T = Desktop / Mobile / Tablet   N/A = not applicable")
        return lines

    @staticmethod
    def _detail_export_order(keys: list[str]) -> list[str]:
        """Put spelling detail before working-links detail; working links last."""
        wl = "Working links & buttons"
        sp = "Correct spelling & grammar, no typos"
        rest = sorted(k for k in keys if k not in (wl, sp))
        out = list(rest)
        if sp in keys:
            out.append(sp)
        if wl in keys:
            out.append(wl)
        return out

    def _build_export_report_text(self) -> str:
        lines: list[str] = []
        lines.append("AUTO WEBSITE CHECKER — REPORT")
        lines.append("=" * 56)
        meta = self._report_meta or {}
        url = (meta.get("url") or self.url_input.text().strip() or "(unknown)").strip()
        lines.append(f"URL: {url}")
        saved_at = (meta.get("saved_at") or "").strip()
        if saved_at:
            lines.append(f"Run / saved at: {saved_at.replace('T', ' ')}")
        if meta.get("source_file"):
            lines.append(f"History file: {meta['source_file']}")
        biz = self.business_name_input.text().strip() or str(self.settings.get("expected_business_name", "")).strip()
        if biz:
            lines.append(f"Expected business name: {biz}")
        lines.append("")
        lines.extend(self._build_dashboard_lines())
        lines.append("")
        lines.append("SUMMARY (main table)")
        lines.append("-" * 56)
        for r in self.results:
            lines.append("")
            lines.append(f"• {r.component}")
            lines.append(
                f"  Overall: {r.yes_no}   |   Desktop: {r.desktop}   Mobile: {r.mobile}   Tablet: {r.tablet}"
            )
            if (r.notes or "").strip():
                lines.append(f"  Notes: {r.notes}")
        lines.append("")
        lines.append("DETAIL LISTS (same as expandable rows in the app)")
        lines.append("-" * 56)
        lines.append(
            "Note: “Working links & buttons” sample URLs are listed last (below spelling), "
            "since the list can be long."
        )
        if not self.row_details_map:
            lines.append("(No row-level detail lists for this run.)")
        else:
            for comp in self._detail_export_order(list(self.row_details_map.keys())):
                payload = self.row_details_map.get(comp) or {}
                bad = payload.get("problematic") or []
                ok = payload.get("ok") or []
                if not bad and not ok:
                    continue
                lines.append("")
                lines.append(f"--- {comp} ---")
                lines.append(f"Problematic ({len(bad)}):")
                for x in bad[:500]:
                    lines.append(f"  - {x}")
                if len(bad) > 500:
                    lines.append(f"  … ({len(bad) - 500} more)")
                lines.append(f"OK / reference ({len(ok)}):")
                for x in ok[:500]:
                    lines.append(f"  - {x}")
                if len(ok) > 500:
                    lines.append(f"  … ({len(ok) - 500} more)")
        lines.append("")
        lines.append("SOCIAL LINKS")
        lines.append("-" * 56)
        if self.latest_social_conflicts:
            lines.append("Conflicts / multiple accounts detected:")
            for c in self.latest_social_conflicts:
                lines.append(f"  - {c}")
            lines.append("")
        if self.latest_social_links:
            for entry in self.latest_social_links:
                platform = entry.get("platform", "social")
                surl = entry.get("url", "")
                account = entry.get("account_key", "")
                lines.append(f"  [{platform}] {account}  →  {surl}")
        else:
            lines.append("(None listed for this run.)")
        lines.append("")
        lines.append("PAGES CHECKED")
        lines.append("-" * 56)
        if self.latest_pages_checked:
            for p in self.latest_pages_checked:
                lines.append(f"  - {p}")
        else:
            lines.append("(None listed for this run.)")
        lines.append("")
        lines.append("SPELLING / GRAMMAR (unknown words — heuristic)")
        lines.append("-" * 56)
        if self.latest_spelling_issues:
            for item in self.latest_spelling_issues:
                if isinstance(item, dict):
                    w = str(item.get("word", ""))
                    pg = item.get("pages") or []
                    sn = item.get("snippets") or []
                    lines.append(f"  - {w}")
                    if pg:
                        lines.append(f"      Pages: {'; '.join(str(p) for p in pg[:8])}")
                    if sn:
                        lines.append(f"      Context: {' | '.join('«' + str(s) + '»' for s in sn[:4])}")
                else:
                    lines.append(f"  - {item}")
        else:
            lines.append("(None flagged for this run.)")
        lines.append("")
        lines.append("— End of report —")
        return "\n".join(lines)

    def export_current_report(self) -> None:
        if not self.results:
            QMessageBox.information(self, "Nothing to export", "Run a check or load a history entry first.")
            return
        default_name = "website-qa-report.txt"
        url_slug = (self._report_meta.get("url") or self.url_input.text().strip() or "report").strip()
        if url_slug.startswith(("http://", "https://")):
            try:
                host = urlparse(url_slug).netloc or "report"
                default_name = f"qa-{host.replace(':', '-')}.txt"
            except Exception:
                pass
        path, _filt = QFileDialog.getSaveFileName(
            self,
            "Export report",
            default_name,
            "Text report (*.txt);;All files (*.*)",
        )
        if not path:
            return
        try:
            text = self._build_export_report_text()
            with open(path, "w", encoding="utf-8", newline="\n") as f:
                f.write(text)
            self.status_label.setText(f"Exported report to {path}")
            QMessageBox.information(self, "Export complete", f"Saved:\n{path}")
        except OSError as exc:
            QMessageBox.critical(self, "Export failed", str(exc))

    def _history_dir(self) -> str:
        d = str(self.settings.get("results_history_dir", DEFAULT_SETTINGS["results_history_dir"]))
        if not d:
            d = DEFAULT_SETTINGS["results_history_dir"]
        os.makedirs(d, exist_ok=True)
        return d

    def _save_current_run_to_history(self) -> None:
        if not self.results:
            return
        snapshot = {
            "saved_at": datetime.datetime.now().isoformat(timespec="seconds"),
            "url": self.url_input.text().strip(),
            "results": [asdict(r) for r in self.results],
            "row_details": self.row_details_map,
            "social_links": self.latest_social_links,
            "social_conflicts": self.latest_social_conflicts,
            "pages_checked": self.latest_pages_checked,
            "spelling_issues": self.latest_spelling_issues,
        }
        stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        path = os.path.join(self._history_dir(), f"run-{stamp}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(snapshot, f, indent=2)
        self._report_meta = {
            "url": snapshot["url"],
            "saved_at": snapshot["saved_at"],
            "source_file": os.path.basename(path),
        }

    @staticmethod
    def _history_combo_label(full_path: str) -> str:
        basename = os.path.basename(full_path)
        try:
            with open(full_path, "r", encoding="utf-8") as f:
                snap = json.load(f)
            url = (snap.get("url") or "").strip()
            saved = (snap.get("saved_at") or "").strip()
            if len(saved) >= 19:
                saved_disp = saved[:19].replace("T", " ")
            else:
                m = re.match(
                    r"run-(\d{4})(\d{2})(\d{2})-(\d{2})(\d{2})(\d{2})\.json$",
                    basename,
                    re.I,
                )
                if m:
                    saved_disp = f"{m.group(1)}-{m.group(2)}-{m.group(3)} {m.group(4)}:{m.group(5)}:{m.group(6)}"
                else:
                    saved_disp = basename
            url_disp = url or "(no URL saved)"
            if len(url_disp) > 64:
                url_disp = url_disp[:61] + "..."
            return f"{saved_disp}  —  {url_disp}"
        except Exception:
            return basename

    def refresh_history_dropdown(self) -> None:
        self.history_combo.clear()
        files = []
        d = self._history_dir()
        for name in os.listdir(d):
            if name.lower().endswith(".json") and name.startswith("run-"):
                full = os.path.join(d, name)
                try:
                    mtime = os.path.getmtime(full)
                    files.append((mtime, full))
                except OSError:
                    continue
        files.sort(reverse=True)
        for _mtime, full in files[:10]:
            self.history_combo.addItem(self._history_combo_label(full), full)

    def _render_loaded_results(self) -> None:
        self.table.setRowCount(0)
        for r in self.results:
            self._append_row(r)
        self._fit_qa_column()
        self.on_social_links_ready(self.latest_social_links, self.latest_social_conflicts)
        self.on_pages_checked_ready(self.latest_pages_checked)
        self.on_spelling_issues_ready(self.latest_spelling_issues)

    def load_selected_history_run(self) -> None:
        path = self.history_combo.currentData()
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                snap = json.load(f)
            self.results = [CheckResult(**row) for row in snap.get("results", [])]
            self.row_details_map = snap.get("row_details", {}) or {}
            self.latest_social_links = snap.get("social_links", []) or []
            self.latest_social_conflicts = snap.get("social_conflicts", []) or []
            self.latest_pages_checked = snap.get("pages_checked", []) or []
            spell_raw = snap.get("spelling_issues", []) or []
            spell_norm: list = []
            for x in spell_raw:
                if isinstance(x, dict):
                    spell_norm.append(x)
                else:
                    spell_norm.append({"word": str(x), "pages": [], "snippets": []})
            self.latest_spelling_issues = spell_norm
            self.url_input.setText(str(snap.get("url") or "").strip())
            self._report_meta = {
                "url": str(snap.get("url") or "").strip(),
                "saved_at": str(snap.get("saved_at") or "").strip(),
                "source_file": os.path.basename(path),
            }
            self._render_loaded_results()
            self.status_label.setText(f"Loaded history: {os.path.basename(path)}")
        except Exception as exc:
            QMessageBox.critical(self, "Load failed", str(exc))

    def _is_detail_row(self, row: int) -> bool:
        item = self.table.item(row, 0)
        return bool(item and item.data(Qt.ItemDataRole.UserRole) == "detail_row")

    def _component_name_for_row(self, row: int) -> str:
        item = self.table.item(row, 0)
        if not item:
            return ""
        text = item.text().strip()
        if text.startswith("\u25b8 ") or text.startswith("\u25be "):
            return text[2:].strip()
        return text

    def on_table_cell_clicked(self, row: int, column: int) -> None:
        if column != 0 or self._is_detail_row(row):
            return
        component = self._component_name_for_row(row)
        if not component:
            return
        # Toggle inline detail row directly below the clicked QA row.
        if row + 1 < self.table.rowCount() and self._is_detail_row(row + 1):
            self.table.removeRow(row + 1)
            base_item = self.table.item(row, 0)
            if base_item:
                base_item.setText(f"\u25b8 {component}")
            return

        payload = self.row_details_map.get(component, {"problematic": [], "ok": []})
        bad = payload.get("problematic", []) or []
        ok = payload.get("ok", []) or []
        lines = [f"Problematic ({len(bad)}):"]
        lines.extend(f"- {x}" for x in bad[:200])
        lines.append("")
        lines.append(f"OK ({len(ok)}):")
        lines.extend(f"- {x}" for x in ok[:200])
        detail_text = "\n".join(lines)

        self.table.insertRow(row + 1)
        detail_item = QTableWidgetItem("   details")
        detail_item.setData(Qt.ItemDataRole.UserRole, "detail_row")
        self.table.setItem(row + 1, 0, detail_item)
        for col in range(1, 5):
            empty = QTableWidgetItem("")
            empty.setData(Qt.ItemDataRole.UserRole, "detail_row")
            self.table.setItem(row + 1, col, empty)
        notes_item = QTableWidgetItem(detail_text)
        notes_item.setData(Qt.ItemDataRole.UserRole, "detail_row")
        self.table.setItem(row + 1, 5, notes_item)
        base_item = self.table.item(row, 0)
        if base_item:
            base_item.setText(f"\u25be {component}")
        self.table.resizeRowsToContents()

    def open_social_link(self, item: QListWidgetItem) -> None:
        url = item.data(Qt.ItemDataRole.UserRole)
        if url:
            webbrowser.open(url)

    def open_page_link(self, item: QListWidgetItem) -> None:
        url = item.data(Qt.ItemDataRole.UserRole)
        if url:
            webbrowser.open(url)

def _resolve_app_icon_path() -> str:
    """PyInstaller macOS .app may place data under _MEIPASS or Contents/Resources."""
    if getattr(sys, "frozen", False):
        candidates: List[str] = []
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            candidates.append(meipass)
        exe_dir = os.path.dirname(sys.executable)
        candidates.append(exe_dir)
        candidates.append(os.path.abspath(os.path.join(exe_dir, "..", "Resources")))
        for base in candidates:
            p = os.path.join(base, "assets", "app-icon.png")
            if os.path.exists(p):
                return p
        return ""
    p = os.path.join(os.path.dirname(__file__), "assets", "app-icon.png")
    return p if os.path.exists(p) else ""


def main() -> int:
    if sys.platform == "darwin":
        os.environ.setdefault("QT_MAC_WANTS_LAYER", "1")
    app = QApplication(sys.argv)
    app.setApplicationName("Website Auditer")
    icon_path = _resolve_app_icon_path()
    if icon_path:
        app.setWindowIcon(QIcon(icon_path))
    window = MainWindow()
    if icon_path:
        window.setWindowIcon(QIcon(icon_path))
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
