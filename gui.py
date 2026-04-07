#!/usr/bin/env python3
"""Simple PyQt6 GUI for Auto Website Checker."""

from __future__ import annotations

import csv
import sys
from dataclasses import asdict
from typing import List

from PyQt6.QtCore import QThread, Qt, pyqtSignal
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QApplication,
    QFileDialog,
    QHeaderView,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from main import CheckResult, build_results


class AuditWorker(QThread):
    finished_ok = pyqtSignal(list)
    failed = pyqtSignal(str)

    def __init__(self, url: str) -> None:
        super().__init__()
        self.url = url

    def run(self) -> None:
        try:
            results = build_results(self.url)
            self.finished_ok.emit(results)
        except Exception as exc:  # pragma: no cover
            self.failed.emit(str(exc))


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Auto Website Checker")
        self.resize(1100, 600)
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
        layout.addLayout(url_row)

        self.status_label = QLabel("Enter a URL and click Run Check.")
        layout.addWidget(self.status_label)

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
        layout.addWidget(self.table)

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
        self.status_label.setText("Running checks... this may take a minute.")
        self.worker = AuditWorker(url)
        self.worker.finished_ok.connect(self.on_success)
        self.worker.failed.connect(self.on_error)
        self.worker.start()

    def on_success(self, results: list) -> None:
        self.results = results
        self.table.setRowCount(len(results))
        for row_idx, result in enumerate(results):
            row = asdict(result)
            values = [
                row["component"],
                row["yes_no"],
                row["desktop"],
                row["mobile"],
                row["tablet"],
                row["notes"],
            ]
            for col_idx, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                self._style_result_cell(item, str(value))
                self.table.setItem(row_idx, col_idx, item)
        self.table.resizeRowsToContents()

        self.run_btn.setEnabled(True)
        self.save_btn.setEnabled(True)
        self.status_label.setText("Complete. Results displayed below.")

    def on_error(self, message: str) -> None:
        self.run_btn.setEnabled(True)
        self.save_btn.setEnabled(False)
        self.status_label.setText("Check failed.")
        QMessageBox.critical(self, "Run failed", message)

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
