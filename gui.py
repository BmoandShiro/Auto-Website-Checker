#!/usr/bin/env python3
"""Simple PyQt6 GUI for Auto Website Checker."""

from __future__ import annotations

import datetime
import json
import os
import subprocess
import sys
import webbrowser
from dataclasses import asdict
from typing import List

from PyQt6.QtCore import QThread, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFontMetrics, QIcon
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
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
    QToolButton,
)

from main import QA_ROW_OPTIONS, CheckResult, build_results, is_chromium_available


APP_DATA_DIR = os.path.join(os.path.expanduser("~"), ".auto_website_checker")
SETTINGS_PATH = os.path.join(APP_DATA_DIR, "settings.json")
DEFAULT_SETTINGS = {
    "timeout_seconds": 30,
    "max_links_per_check": 30,
    "fast_load_ms_threshold": 2500,
    "max_pages_to_audit": 5,
    "psi_cooldown_seconds": 3.0,
    "request_throttle_seconds": 0.5,
    "prefer_crux_first": True,
    "enable_core_web_vitals": False,
    "expected_business_name": "",
    "ui_font_size": 10,
    "auto_save_last_run": True,
    "results_history_dir": os.path.join(APP_DATA_DIR, "run-history"),
    "ui_theme": "Dark Gray",
}


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

        self.psi_cooldown = QDoubleSpinBox()
        self.psi_cooldown.setRange(0.0, 60.0)
        self.psi_cooldown.setSingleStep(0.5)
        self.psi_cooldown.setValue(float(current["psi_cooldown_seconds"]))
        form.addRow("PSI cooldown (seconds)", self.psi_cooldown)

        self.throttle = QDoubleSpinBox()
        self.throttle.setRange(0.0, 5.0)
        self.throttle.setSingleStep(0.1)
        self.throttle.setValue(float(current["request_throttle_seconds"]))
        form.addRow("HTTP throttle (seconds)", self.throttle)

        self.prefer_crux = QCheckBox("Prefer CrUX first for CWV")
        self.prefer_crux.setChecked(bool(current["prefer_crux_first"]))
        form.addRow(self.prefer_crux)

        self.enable_cwv = QCheckBox("Enable Core Web Vitals checks")
        self.enable_cwv.setChecked(bool(current.get("enable_core_web_vitals", False)))
        form.addRow(self.enable_cwv)

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
            "psi_cooldown_seconds": float(self.psi_cooldown.value()),
            "request_throttle_seconds": float(self.throttle.value()),
            "prefer_crux_first": bool(self.prefer_crux.isChecked()),
            "enable_core_web_vitals": bool(self.enable_cwv.isChecked()),
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


class AuditWorker(QThread):
    finished_ok = pyqtSignal(list)
    row_ready = pyqtSignal(object)
    status = pyqtSignal(str)
    social_links_ready = pyqtSignal(list, list)
    pages_checked_ready = pyqtSignal(list)
    spelling_issues_ready = pyqtSignal(list)
    row_details_ready = pyqtSignal(dict)
    progress_non_cwv = pyqtSignal(int, int)
    progress_cwv = pyqtSignal(int, int)
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
                self.progress_non_cwv.emit(done, total)

            def emit_progress_cwv(done: int, total: int) -> None:
                self.progress_cwv.emit(done, total)

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
                on_progress_non_cwv=emit_progress,
                on_progress_cwv=emit_progress_cwv,
                settings=self.settings,
            )
            self.finished_ok.emit(results)
        except Exception as exc:  # pragma: no cover
            self.failed.emit(str(exc))


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Auto Website Checker")
        self.resize(1100, 600)
        self.settings = self._load_settings()
        self.results: List[CheckResult] = []
        self.row_details_map: dict = {}
        self.latest_social_links: list = []
        self.latest_social_conflicts: list = []
        self.latest_pages_checked: list = []
        self.latest_spelling_issues: list = []
        self.worker: AuditWorker | None = None

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

        self.progress_non_cwv_label = QLabel("QA Checks Progress")
        self.progress_non_cwv_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.progress_non_cwv_label)
        self.progress_non_cwv_bar = QProgressBar()
        self.progress_non_cwv_bar.setRange(0, 100)
        self.progress_non_cwv_bar.setValue(0)
        layout.addWidget(self.progress_non_cwv_bar)

        self.progress_cwv_label = QLabel("Core Web Vitals Progress")
        self.progress_cwv_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.progress_cwv_label)
        self.progress_cwv_bar = QProgressBar()
        self.progress_cwv_bar.setRange(0, 100)
        self.progress_cwv_bar.setValue(0)
        layout.addWidget(self.progress_cwv_bar)

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

        self.spell_list = QListWidget()
        self.spell_list.setVisible(False)
        self.spell_list.setMinimumHeight(100)
        layout.addWidget(self.spell_list)

        history_row = QHBoxLayout()
        history_row.addWidget(QLabel("Recent runs:"))
        self.history_combo = QComboBox()
        history_row.addWidget(self.history_combo)
        self.load_history_btn = QPushButton("Load")
        self.load_history_btn.clicked.connect(self.load_selected_history_run)
        history_row.addWidget(self.load_history_btn)
        layout.addLayout(history_row)
        layout.setStretch(0, 0)  # URL row
        layout.setStretch(1, 0)  # business row
        layout.setStretch(2, 0)  # status
        layout.setStretch(3, 0)  # non-CWV label
        layout.setStretch(4, 0)  # non-CWV bar
        layout.setStretch(5, 0)  # CWV label
        layout.setStretch(6, 0)  # CWV bar
        layout.setStretch(7, 1)  # table gets remaining height
        layout.setStretch(8, 0)  # social label
        layout.setStretch(9, 0)  # social toggle
        layout.setStretch(10, 0)  # social list
        layout.setStretch(11, 0)  # pages toggle
        layout.setStretch(12, 0)  # pages list
        layout.setStretch(13, 0)  # spell toggle
        layout.setStretch(14, 0)  # spell list
        layout.setStretch(15, 0)  # history row
        layout.setStretch(16, 0)  # footer

        footer_row = QHBoxLayout()
        self.credit_label = QLabel("Created by: BMOandShiro")
        self.version_label = QLabel("v0.1.0-alpha")
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
        self.spell_list.clear()
        self.spell_toggle.setChecked(False)
        self.spell_toggle.setText("Spelling/grammar unknown words \u25be")
        self.social_label.setText("Social links (double-click to open)")
        self.status_label.setText("Running checks... this may take a minute.")
        self.progress_non_cwv_bar.setValue(0)
        self.progress_cwv_bar.setValue(0)
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
        self.worker.progress_non_cwv.connect(self.on_progress_non_cwv)
        self.worker.progress_cwv.connect(self.on_progress_cwv)
        self.worker.failed.connect(self.on_error)
        self.worker.start()

    def _load_settings(self) -> dict:
        os.makedirs(APP_DATA_DIR, exist_ok=True)
        if not os.path.exists(SETTINGS_PATH):
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
        try:
            with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
                raw = json.load(f)
            merged = dict(DEFAULT_SETTINGS)
            merged.update(raw)
            return merged
        except Exception:
            return dict(DEFAULT_SETTINGS)

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
            self.settings = updated
            self._save_settings()
            self._apply_ui_font_size()
            self._apply_theme()
            mode = "CrUX-first" if self.settings.get("prefer_crux_first") else "PSI-first"
            self.status_label.setText(f"Settings saved ({mode}).")

    def open_row_config(self) -> None:
        dialog = RowConfigDialog(self.settings, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.settings["enabled_rows"] = dialog.selected_rows()
            self._save_settings()
            self.status_label.setText("Row config saved.")

    def on_success(self, results: list) -> None:
        self.results = results
        self._fit_qa_column()
        self.run_btn.setEnabled(True)
        self.progress_non_cwv_bar.setValue(100)
        self.progress_cwv_bar.setValue(100)
        browser_missing = any("browser unavailable" in (r.notes or "").lower() for r in results)
        if bool(self.settings.get("auto_save_last_run", True)):
            self._save_current_run_to_history()
            self.refresh_history_dropdown()
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

    def on_progress_non_cwv(self, done: int, total: int) -> None:
        if total <= 0:
            self.progress_non_cwv_bar.setValue(0)
            return
        pct = int((done / total) * 100)
        self.progress_non_cwv_bar.setValue(max(0, min(100, pct)))

    def on_progress_cwv(self, done: int, total: int) -> None:
        if total <= 0:
            self.progress_cwv_bar.setValue(0)
            return
        pct = int((done / total) * 100)
        self.progress_cwv_bar.setValue(max(0, min(100, pct)))

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
        self.progress_non_cwv_bar.setValue(0)
        self.progress_cwv_bar.setValue(0)
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
        self.install_browser_btn.setEnabled(False)
        self.status_label.setText("Installing Chromium dependency...")
        try:
            subprocess.run([sys.executable, "-m", "playwright", "install", "chromium"], check=True, timeout=600)
            self.status_label.setText("Chromium installed. Re-run checks to enable browser-based rows.")
            QMessageBox.information(self, "Install complete", "Chromium installed successfully.")
        except Exception as exc:
            self.status_label.setText("Browser install failed.")
            QMessageBox.critical(self, "Install failed", str(exc))
        finally:
            pass

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

    def on_spelling_issues_ready(self, words: list) -> None:
        self.latest_spelling_issues = words
        self.spell_list.clear()
        for word in words:
            self.spell_list.addItem(QListWidgetItem(word))

    def toggle_spell_panel(self, checked: bool) -> None:
        self.spell_list.setVisible(checked)
        self.spell_toggle.setText(
            "Spelling/grammar unknown words \u25b4" if checked else "Spelling/grammar unknown words \u25be"
        )

    def on_row_details_ready(self, details: dict) -> None:
        self.row_details_map = details

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
            label = os.path.basename(full)
            self.history_combo.addItem(label, full)

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
            self.latest_spelling_issues = snap.get("spelling_issues", []) or []
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

def main() -> int:
    app = QApplication(sys.argv)
    # Ensure icon works both in source runs and PyInstaller bundles.
    if getattr(sys, "frozen", False):
        base_dir = getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
    else:
        base_dir = os.path.dirname(__file__)
    icon_path = os.path.join(base_dir, "assets", "app-icon.png")
    if os.path.exists(icon_path):
        icon = QIcon(icon_path)
        app.setWindowIcon(icon)
    window = MainWindow()
    if os.path.exists(icon_path):
        window.setWindowIcon(QIcon(icon_path))
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
