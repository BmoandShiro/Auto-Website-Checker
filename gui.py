#!/usr/bin/env python3
"""Simple PyQt6 GUI for Auto Website Checker."""

from __future__ import annotations

import csv
import json
import os
import sys
import webbrowser
from dataclasses import asdict
from typing import List

from PyQt6.QtCore import QThread, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFontMetrics
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
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
    QSizePolicy,
)

from main import CheckResult, build_results


SETTINGS_PATH = os.path.join(os.path.dirname(__file__), "settings.json")
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
        }


class AuditWorker(QThread):
    finished_ok = pyqtSignal(list)
    row_ready = pyqtSignal(object)
    status = pyqtSignal(str)
    social_links_ready = pyqtSignal(list, list)
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

            results = build_results(
                self.url,
                on_row=emit_row,
                on_status=emit_status,
                on_social_links=emit_social_links,
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

        self.save_btn = QPushButton("Save CSV")
        self.save_btn.setEnabled(False)
        self.save_btn.clicked.connect(self.save_csv)
        url_row.addWidget(self.save_btn)

        self.settings_btn = QPushButton("Settings")
        self.settings_btn.clicked.connect(self.open_settings)
        url_row.addWidget(self.settings_btn)
        layout.addLayout(url_row)

        business_row = QHBoxLayout()
        business_row.addWidget(QLabel("Expected Business Name:"))
        self.business_name_input = QLineEdit()
        self.business_name_input.setPlaceholderText("e.g. Renew Dental Loft")
        self.business_name_input.setText(str(self.settings.get("expected_business_name", "")))
        business_row.addWidget(self.business_name_input)
        layout.addLayout(business_row)

        self.status_label = QLabel("Enter a URL and click Run Check.")
        layout.addWidget(self.status_label)

        self.progress_non_cwv_label = QLabel("QA Checks Progress")
        layout.addWidget(self.progress_non_cwv_label)
        self.progress_non_cwv_bar = QProgressBar()
        self.progress_non_cwv_bar.setRange(0, 100)
        self.progress_non_cwv_bar.setValue(0)
        layout.addWidget(self.progress_non_cwv_bar)

        self.progress_cwv_label = QLabel("Core Web Vitals Progress")
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
        layout.addWidget(self.table)

        self.social_label = QLabel("Social links (double-click to open)")
        layout.addWidget(self.social_label)
        self.social_list = QListWidget()
        self.social_list.itemDoubleClicked.connect(self.open_social_link)
        self.social_list.setMinimumHeight(120)
        layout.addWidget(self.social_list)
        layout.setStretch(0, 0)  # URL row
        layout.setStretch(1, 0)  # business row
        layout.setStretch(2, 0)  # status
        layout.setStretch(3, 0)  # non-CWV label
        layout.setStretch(4, 0)  # non-CWV bar
        layout.setStretch(5, 0)  # CWV label
        layout.setStretch(6, 0)  # CWV bar
        layout.setStretch(7, 1)  # table gets remaining height
        layout.setStretch(8, 0)  # social label
        layout.setStretch(9, 0)  # social list
        layout.setStretch(10, 0)  # footer

        footer_row = QHBoxLayout()
        self.credit_label = QLabel("Created by: BMOandShiro")
        self.version_label = QLabel("v0.1.0-alpha")
        footer_row.addWidget(self.credit_label)
        footer_row.addStretch()
        footer_row.addWidget(self.version_label)
        layout.addLayout(footer_row)

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
        if not url.startswith(("http://", "https://")):
            QMessageBox.warning(self, "Invalid URL", "URL must start with http:// or https://")
            return

        self.run_btn.setEnabled(False)
        self.save_btn.setEnabled(False)
        self.results = []
        self.table.setRowCount(0)
        self.social_list.clear()
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
        self.worker.progress_non_cwv.connect(self.on_progress_non_cwv)
        self.worker.progress_cwv.connect(self.on_progress_cwv)
        self.worker.failed.connect(self.on_error)
        self.worker.start()

    def _load_settings(self) -> dict:
        if not os.path.exists(SETTINGS_PATH):
            return dict(DEFAULT_SETTINGS)
        try:
            with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
                raw = json.load(f)
            merged = dict(DEFAULT_SETTINGS)
            merged.update(raw)
            return merged
        except Exception:
            return dict(DEFAULT_SETTINGS)

    def _save_settings(self) -> None:
        with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
            json.dump(self.settings, f, indent=2)

    def open_settings(self) -> None:
        dialog = SettingsDialog(self.settings, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.settings = dialog.to_settings()
            self._save_settings()
            mode = "CrUX-first" if self.settings.get("prefer_crux_first") else "PSI-first"
            self.status_label.setText(f"Settings saved ({mode}).")

    def on_success(self, results: list) -> None:
        self.results = results
        self._fit_qa_column()
        self.run_btn.setEnabled(True)
        self.save_btn.setEnabled(True)
        self.progress_non_cwv_bar.setValue(100)
        self.progress_cwv_bar.setValue(100)
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
        values = [
            row["component"],
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
        self.save_btn.setEnabled(False)
        self.progress_non_cwv_bar.setValue(0)
        self.progress_cwv_bar.setValue(0)
        self.status_label.setText("Check failed.")
        QMessageBox.critical(self, "Run failed", message)

    def on_social_links_ready(self, links: list, conflicts: list) -> None:
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

    def open_social_link(self, item: QListWidgetItem) -> None:
        url = item.data(Qt.ItemDataRole.UserRole)
        if url:
            webbrowser.open(url)

    def save_csv(self) -> None:
        if not self.results:
            return
        path, _ = QFileDialog.getSaveFileName(self, "Save Results", "qa_results.csv", "CSV Files (*.csv)")
        if not path:
            return
        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["QA Component", "Y/N", "Desktop Pass/Fail", "Mobile Pass/Fail", "Tablet Pass/Fail", "Notes"])
            for r in self.results:
                writer.writerow([r.component, r.yes_no, r.desktop, r.mobile, r.tablet, r.notes])
        self.status_label.setText(f"Saved CSV to: {path}")


def main() -> int:
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
