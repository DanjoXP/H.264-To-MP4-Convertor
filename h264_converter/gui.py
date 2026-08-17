import os
import sys
import threading
from pathlib import Path
from typing import List, Optional, Tuple

from PySide6.QtCore import QEvent, QObject, QThread, Qt, QUrl, Signal
from PySide6.QtGui import QColor, QCursor, QDragEnterEvent, QDropEvent, QFont, QIcon, QPalette
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListView,
    QMainWindow,
    QMenu,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QToolTip,
    QVBoxLayout,
    QWidget,
)


from converter import (
    calculate_output_path,
    convert_264_to_mp4,
    get_ffmpeg_executable,
    scan_for_264_files,
)


class ConversionWorker(QObject):
    """Worker object running in a separate QThread to handle batch video conversion."""

    # Signals
    file_started = Signal(int, str)  # (row_index, input_file)
    file_finished = Signal(int, bool, str)  # (row_index, success, log_msg)
    overall_progress = Signal(int, int)  # (current, total)
    log_message = Signal(str)
    all_completed = Signal(int, int)  # (success_count, fail_count)

    def __init__(
        self,
        file_items: List[dict],
        output_dir: str,
        preserve_structure: bool,
        fps: Optional[float] = None,
        target_duration_sec: Optional[float] = None,
        max_workers: int = 4
    ):
        super().__init__()
        self.file_items = file_items  # list of dicts: {"row": i, "file": path, "base_dir": dir}
        self.output_dir = output_dir
        self.preserve_structure = preserve_structure
        self.fps = fps
        self.target_duration_sec = target_duration_sec
        self.max_workers = max_workers
        self._is_cancelled = False
        self._active_procs = []
        self._proc_lock = threading.Lock()

    def cancel(self):
        self._is_cancelled = True
        with self._proc_lock:
            for p in self._active_procs:
                try:
                    p.kill()
                except Exception:
                    pass
            self._active_procs.clear()

    def _process_single_file(self, idx: int, item: dict, ffmpeg_exe: str, total: int) -> Tuple[int, bool, str]:
        if self._is_cancelled:
            return item["row"], False, "Cancelled by user"

        row = item["row"]
        input_file = item["file"]
        base_dir = item["base_dir"]

        self.file_started.emit(row, input_file)

        out_mp4 = calculate_output_path(
            input_file=input_file,
            input_base_dir=base_dir,
            output_dir=self.output_dir,
            preserve_structure=self.preserve_structure
        )

        # Skip files that have already been converted
        if item.get("already_completed") or (os.path.exists(out_mp4) and os.path.getsize(out_mp4) > 1024):
            log = f"[Skipped] {os.path.basename(out_mp4)} already converted."
            self.file_finished.emit(row, True, log)
            self.log_message.emit(f"[{idx+1}/{total}] {log}")
            return row, True, log

        def sub_callback(msg):
            self.log_message.emit(f"[{idx+1}/{total}] {msg}")

        def register_proc(proc):
            with self._proc_lock:
                self._active_procs.append(proc)

        success, log = convert_264_to_mp4(
            input_file=input_file,
            output_file=out_mp4,
            fps=self.fps,
            target_duration_sec=self.target_duration_sec,
            ffmpeg_exe=ffmpeg_exe,
            progress_callback=sub_callback,
            cancel_check=lambda: self._is_cancelled,
            proc_callback=register_proc
        )

        if self._is_cancelled or not success:
            if os.path.exists(out_mp4):
                try:
                    os.remove(out_mp4)
                except Exception:
                    pass

        self.file_finished.emit(row, success, log)
        if not self._is_cancelled:
            self.log_message.emit(f"[{'OK' if success else 'FAIL'}] {log}")
        return row, success, log

    def run(self):
        from concurrent.futures import ThreadPoolExecutor, as_completed

        try:
            ffmpeg_exe = get_ffmpeg_executable()
        except Exception as e:
            self.log_message.emit(f"[ERROR] {str(e)}")
            self.all_completed.emit(0, len(self.file_items))
            return

        total = len(self.file_items)
        success_count = 0
        fail_count = 0
        completed_so_far = 0

        workers = min(self.max_workers, total) if total > 0 else 1
        self.log_message.emit(f"🚀 Starting parallel batch processing with {workers} thread worker(s)...")

        with ThreadPoolExecutor(max_workers=workers) as executor:
            future_to_item = {
                executor.submit(self._process_single_file, idx, item, ffmpeg_exe, total): item
                for idx, item in enumerate(self.file_items)
            }

            for future in as_completed(future_to_item):
                if self._is_cancelled:
                    with self._proc_lock:
                        for p in self._active_procs:
                            try:
                                p.kill()
                            except Exception:
                                pass
                    executor.shutdown(wait=False, cancel_futures=True)
                    self.log_message.emit("[INFO] Conversion cancelled by user.")
                    break

                try:
                    row, success, log = future.result()
                    if success:
                        success_count += 1
                    else:
                        fail_count += 1
                except Exception as exc:
                    fail_count += 1
                    self.log_message.emit(f"[ERROR] Task generated an exception: {exc}")

                completed_so_far += 1
                self.overall_progress.emit(completed_so_far, total)

        self.overall_progress.emit(total, total)
        self.all_completed.emit(success_count, fail_count)



class DropZoneWidget(QFrame):
    """Interactive Drag-and-Drop Area for files and directories."""

    folder_dropped = Signal(list)  # Emits list of paths dropped
    clicked = Signal()            # Emits signal when user clicks the drop zone

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setObjectName("DropZone")
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.icon_label = QLabel("📁")
        self.icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.icon_label.setStyleSheet("font-size: 48px; background: transparent;")

        self.text_label = QLabel("Drag & Drop your Folder or .264 files here\nor click to browse files")
        self.text_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.text_label.setStyleSheet("font-size: 15px; font-weight: bold; color: #E0E6ED; background: transparent;")

        layout.addWidget(self.icon_label)
        layout.addWidget(self.text_label)

        self.setStyleSheet("""
            QFrame#DropZone {
                border: 1px solid #333333;
                border-radius: 0px;
                background-color: #242424;
                min-height: 80px;
            }
            QFrame#DropZone:hover {
                border-color: #38BDF8;
                background-color: #2D2D2D;
            }
        """)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)

    def dragEnterEvent(self, event: QDragEnterEvent):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
            self.setStyleSheet("""
                QFrame#DropZone {
                    border: 2px solid #38BDF8;
                    border-radius: 0px;
                    background-color: #2D2D2D;
                    min-height: 80px;
                }
            """)

    def dragLeaveEvent(self, event):
        self.setStyleSheet("""
            QFrame#DropZone {
                border: 1px solid #333333;
                border-radius: 0px;
                background-color: #242424;
                min-height: 80px;
            }
        """)


    def dropEvent(self, event: QDropEvent):
        self.dragLeaveEvent(None)
        urls = event.mimeData().urls()
        paths = [url.toLocalFile() for url in urls if url.isLocalFile()]
        if paths:
            self.folder_dropped.emit(paths)



class MainWindow(QMainWindow):

    def __init__(self):
        super().__init__()
        self.setWindowTitle("H.264 to MP4 Folder Converter")
        self.resize(920, 680)

        self.input_base_dir: Optional[str] = None
        self.output_dir: Optional[str] = None
        self.file_items: List[dict] = []
        self.worker_thread: Optional[QThread] = None
        self.worker: Optional[ConversionWorker] = None
        self.is_conversion_started: bool = False

        self.apply_dark_theme()
        self.init_ui()

    def eventFilter(self, source, event):
        if source == self.table.viewport() and event.type() == QEvent.Type.ToolTip:
            pos = event.pos()
            item = self.table.itemAt(pos)
            if item and item.toolTip():
                rect = self.table.visualItemRect(item)
                QToolTip.showText(event.globalPos(), item.toolTip(), self.table.viewport(), rect)
                return True
        return super().eventFilter(source, event)

    def apply_dark_theme(self):
        self.setStyleSheet("""
            QMainWindow {
                background-color: #1C1C1C;
                color: #F8FAFC;
            }
            QWidget {
                font-family: 'Segoe UI', Arial, sans-serif;
                font-size: 13px;
                color: #F8FAFC;
            }
            QLabel {
                color: #E2E8F0;
                background-color: transparent;
                border: none;
            }
            QMessageBox QLabel {
                color: black;
                background-color: transparent;
                border: none;
            }
            QLineEdit {
                background-color: #2D2D2D;
                border: 1px solid #444444;
                border-radius: 0px;
                padding: 2px 8px;
                height: 20px;
                max-height: 22px;
                font-size: 12px;
                color: #F8FAFC;
            }
            QLineEdit:focus {
                border-color: #38BDF8;
            }
            QPushButton {
                background-color: #333333;
                color: #E2E8F0;
                font-size: 12px;
                font-weight: 600;
                border: none;
                border-radius: 0px;
                padding: 2px 10px;
                height: 20px;
                max-height: 22px;
            }
            QPushButton:hover {
                background-color: #444444;
            }
            QPushButton:pressed {
                background-color: #222222;
            }
            QPushButton:disabled {
                background-color: #222222;
                color: #555555;
            }
            QPushButton#SecondaryBtn {
                background-color: #333333;
                color: #E2E8F0;
                border: none;
                border-radius: 0px;
                height: 20px;
                max-height: 22px;
            }
            QPushButton#SecondaryBtn:hover {
                background-color: #444444;
            }
            QPushButton#StartBtn {
                background-color: #10B981;
                color: white;
                font-size: 12px;
                font-weight: 600;
                border: none;
                border-radius: 0px;
                padding: 2px 10px;
                height: 20px;
                max-height: 22px;
            }
            QPushButton#StartBtn:hover {
                background-color: #059669;
            }
            QPushButton#CancelBtn {
                background-color: #EF4444;
                color: white;
                font-size: 12px;
                font-weight: 600;
                border: none;
                border-radius: 0px;
                padding: 2px 10px;
                height: 20px;
                max-height: 22px;
            }
            QPushButton#CancelBtn:hover {
                background-color: #DC2626;
            }
            QComboBox {
                background-color: #2D2D2D;
                border: 1px solid #444444;
                border-radius: 0px;
                padding: 2px 6px;
                height: 20px;
                font-size: 12px;
                color: #F8FAFC;
            }
            QComboBox QAbstractItemView {
                background-color: #2D2D2D;
                color: #F8FAFC;
                selection-background-color: #38BDF8;
                selection-color: #000000;
                border: 1px solid #444444;
                outline: 0px;
            }
            QToolTip {
                background-color: #2D2D2D;
                color: #F8FAFC;
                border: 1px solid #444444;
                border-radius: 0px;
                padding: 4px 8px;
                font-family: 'Segoe UI', sans-serif;
                font-size: 12px;
            }
            QTableWidget {
                background-color: #242424;
                border: 1px solid #333333;
                border-radius: 0px;
                gridline-color: #333333;
                color: #F8FAFC;
            }
            QTableWidget::item {
                padding: 4px;
                color: #F8FAFC;
                background-color: #242424;
            }
            QTableCornerButton::section {
                background-color: #1C1C1C;
                border: none;
            }
            QHeaderView::section {
                background-color: #1C1C1C;
                color: #94A3B8;
                font-size: 13px;
                font-weight: bold;
                padding: 6px 12px;
                border: none;
                border-bottom: 1px solid #333333;
                text-align: center;
            }
            QProgressBar {
                border: 1px solid #333333;
                border-radius: 0px;
                text-align: center;
                background-color: #242424;
                color: #F8FAFC;
                font-weight: bold;
            }
            QProgressBar::chunk {
                background-color: #38BDF8;
                border-radius: 0px;
            }
            QTextEdit {
                background-color: #141414;
                border: 1px solid #333333;
                border-radius: 0px;
                color: #38BDF8;
                font-family: 'Consolas', 'Courier New', monospace;
                font-size: 12px;
            }
            QCheckBox {
                spacing: 8px;
                background-color: transparent;
                border: none;
            }
            QCheckBox::indicator {
                width: 18px;
                height: 18px;
                border-radius: 0px;
                border: 1px solid #555555;
                background-color: #2D2D2D;
            }
            QCheckBox::indicator:checked {
                background-color: #38BDF8;
                border-color: #38BDF8;
            }
        """)


    def init_ui(self):
        central_widget = QWidget()
        self.setCentralWidget(central_widget)

        main_layout = QVBoxLayout(central_widget)
        main_layout.setSpacing(10)
        main_layout.setContentsMargins(14, 14, 14, 14)

        # Header Title
        header_layout = QHBoxLayout()
        title_label = QLabel("H.264 to MP4 Folder Converter")
        title_label.setStyleSheet("font-size: 18px; font-weight: bold; color: #F8FAFC;")
        header_layout.addWidget(title_label)
        header_layout.addStretch()
        main_layout.addLayout(header_layout)

        # --- TOP HALF (Compact 2 Stretch) ---
        top_split_layout = QHBoxLayout()
        top_split_layout.setSpacing(10)        # Top-Left Card: Folder Selectors & Settings
        controls_frame = QFrame()
        controls_frame.setStyleSheet("background-color: #242424; border-radius: 0px; border: 1px solid #333333; padding: 10px;")
        controls_layout = QVBoxLayout(controls_frame)
        controls_layout.setSpacing(2)
        controls_layout.setContentsMargins(10, 10, 10, 10)

        # Input Section Label
        input_label_row = QHBoxLayout()
        input_label_row.setContentsMargins(0, 0, 0, 0)
        lbl_input_title = QLabel("Input Folder/Files:")
        lbl_input_title.setStyleSheet("font-size: 13px; font-weight: 600; color: #E2E8F0; background: transparent; border: none; padding: 0px; margin: 0px;")
        input_label_row.addWidget(lbl_input_title)

        self.lbl_file_count = QLabel("0 files found")
        self.lbl_file_count.setStyleSheet("color: #94A3B8; font-weight: 600; font-size: 12px; background: transparent; border: none; padding: 0px; margin: 0px;")
        input_label_row.addStretch()
        input_label_row.addWidget(self.lbl_file_count)
        controls_layout.addLayout(input_label_row)

        # Input Section Entry Row
        input_row = QHBoxLayout()
        input_row.setContentsMargins(0, 0, 0, 0)
        self.input_path_edit = QLineEdit()
        self.input_path_edit.setPlaceholderText("No folder or file selected")
        self.input_path_edit.setReadOnly(True)
        input_row.addWidget(self.input_path_edit)

        self.btn_browse_folder = QPushButton("Browse Folder")
        self.btn_browse_folder.setObjectName("SecondaryBtn")
        self.btn_browse_folder.clicked.connect(self.browse_input_folder)
        input_row.addWidget(self.btn_browse_folder)
        controls_layout.addLayout(input_row)

        controls_layout.addSpacing(8)

        # Output Section Label
        output_label_row = QHBoxLayout()
        output_label_row.setContentsMargins(0, 0, 0, 0)
        lbl_output_title = QLabel("Output Folder:")
        lbl_output_title.setStyleSheet("font-size: 13px; font-weight: 600; color: #E2E8F0; background: transparent; border: none; padding: 0px; margin: 0px;")
        output_label_row.addWidget(lbl_output_title)
        output_label_row.addStretch()
        controls_layout.addLayout(output_label_row)

        # Output Section Entry Row
        output_row = QHBoxLayout()
        output_row.setContentsMargins(0, 0, 0, 0)
        self.output_path_edit = QLineEdit()
        self.output_path_edit.setPlaceholderText("Select folder where MP4 files will be saved...")
        output_row.addWidget(self.output_path_edit)

        self.btn_browse_output = QPushButton("Select Output Folder")
        self.btn_browse_output.setObjectName("SecondaryBtn")
        self.btn_browse_output.clicked.connect(self.browse_output_folder)
        output_row.addWidget(self.btn_browse_output)
        controls_layout.addLayout(output_row)

        controls_layout.addSpacing(8)

        # Settings Options
        settings_grid = QHBoxLayout()
        settings_grid.setContentsMargins(0, 0, 0, 0)
        settings_grid.setSpacing(10)

        self.chk_preserve_structure = QCheckBox("Preserve subfolder structure in output directory")
        self.chk_preserve_structure.setChecked(True)
        settings_grid.addWidget(self.chk_preserve_structure)

        settings_grid.addStretch()

        speed_thread_row = QHBoxLayout()
        speed_thread_row.setContentsMargins(0, 0, 0, 0)
        speed_thread_row.addWidget(QLabel("Threads:"))
        self.cmb_threads = QComboBox()
        self.cmb_threads.setView(QListView())
        self.cmb_threads.setMaxVisibleItems(10)
        self.cmb_threads.addItems([
            "1 Worker (Linear)",
            "2 Workers",
            "4 Workers (Fast)",
            "8 Workers (Max)"
        ])
        self.cmb_threads.setCurrentIndex(2)  # Default to 4 Workers (Fast)
        self.cmb_threads.setStyleSheet("background-color: #2D2D2D; border: 1px solid #444444; border-radius: 0px; padding: 2px 6px; height: 20px; font-size: 12px; color: #F8FAFC;")
        speed_thread_row.addWidget(self.cmb_threads)

        settings_grid.addLayout(speed_thread_row)
        controls_layout.addLayout(settings_grid)


        top_split_layout.addWidget(controls_frame, stretch=1)

        # Top-Right Box: Drag & Drop Zone (Single-Click Directory Browser)
        self.drop_zone = DropZoneWidget()
        self.drop_zone.folder_dropped.connect(self.handle_dropped_paths)
        self.drop_zone.clicked.connect(self.browse_input_folder)
        top_split_layout.addWidget(self.drop_zone, stretch=1)

        main_layout.addLayout(top_split_layout, stretch=2)

        # --- BOTTOM HALF (Expanded 5 Stretch) ---
        bottom_split_layout = QHBoxLayout()
        bottom_split_layout.setSpacing(10)

        # Bottom-Left: File Directory Queue Table
        self.table = QTableWidget()
        self.table.setColumnCount(5)
        self.table.setHorizontalHeaderLabels(["", "File Name", "Size", "Relative Path", "Status"])
        self.table.horizontalHeader().setDefaultAlignment(Qt.AlignmentFlag.AlignCenter)
        self.table.horizontalHeader().setFixedHeight(32)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        self.table.setColumnWidth(0, 36)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setMouseTracking(True)
        self.table.viewport().installEventFilter(self)
        bottom_split_layout.addWidget(self.table, stretch=1)

        # Bottom-Right: Conversion Log Console Panel
        log_frame = QFrame()
        log_frame.setObjectName("LogFrame")
        log_frame.setStyleSheet("""
            QFrame#LogFrame {
                background-color: #242424;
                border: 1px solid #333333;
                border-radius: 0px;
            }
        """)
        log_layout = QVBoxLayout(log_frame)
        log_layout.setContentsMargins(0, 0, 0, 0)
        log_layout.setSpacing(0)

        log_header = QLabel("Output")
        log_header.setFixedHeight(32)
        log_header.setStyleSheet("""
            background-color: #1C1C1C;
            color: #94A3B8;
            font-size: 13px;
            font-weight: bold;
            padding: 6px 12px;
            border-bottom: 1px solid #333333;
        """)
        log_layout.addWidget(log_header)

        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setStyleSheet("""
            QTextEdit {
                background-color: #242424;
                border: none;
                border-radius: 0px;
                color: #38BDF8;
                font-family: 'Consolas', 'Courier New', monospace;
                font-size: 12px;
                padding: 6px;
            }
        """)
        log_layout.addWidget(self.log_text)

        bottom_split_layout.addWidget(log_frame, stretch=1)

        main_layout.addLayout(bottom_split_layout, stretch=5)

        # --- BOTTOM FOOTER ---

        # Progress Bar
        self.progress_bar = QProgressBar()
        self.progress_bar.setValue(0)
        self.progress_bar.setFixedHeight(20)
        main_layout.addWidget(self.progress_bar)

        # Action Buttons Row Along Bottom
        actions_row = QHBoxLayout()

        self.btn_clear = QPushButton("Clear Queue")
        self.btn_clear.setObjectName("SecondaryBtn")
        self.btn_clear.clicked.connect(self.clear_queue)
        actions_row.addWidget(self.btn_clear)

        self.btn_open_output = QPushButton("Open Output Folder")
        self.btn_open_output.setObjectName("SecondaryBtn")
        self.btn_open_output.clicked.connect(self.open_output_folder)
        actions_row.addWidget(self.btn_open_output)

        actions_row.addStretch()

        self.btn_start = QPushButton("🚀 Start Conversion")
        self.btn_start.setObjectName("StartBtn")
        self.btn_start.clicked.connect(self.on_start_stop_clicked)
        actions_row.addWidget(self.btn_start)

        main_layout.addLayout(actions_row)

    def handle_drop_zone_click(self):
        """Shows a popup menu at cursor allowing user to pick Folder OR Files."""
        menu = QMenu(self)
        menu.setStyleSheet("""
            QMenu {
                background-color: #2D2D2D;
                color: #F8FAFC;
                border: 1px solid #444444;
                border-radius: 0px;
                font-size: 13px;
                padding: 4px;
            }
            QMenu::item {
                padding: 8px 20px;
                border-radius: 0px;
            }
            QMenu::item:selected {
                background-color: #38BDF8;
                color: #000000;
            }
        """)
        action_folder = menu.addAction("📁 Browse Folder...")
        action_files = menu.addAction("📄 Browse .264 Files...")

        chosen = menu.exec(QCursor.pos())
        if chosen == action_folder:
            self.browse_input_folder()
        elif chosen == action_files:
            self.browse_input_files()

    def handle_dropped_paths(self, paths: List[str]):
        """Triggered when user drops files/folders into drop zone."""
        found_files = []
        base_dir = None

        for p in paths:
            if os.path.isdir(p):
                if base_dir is None:
                    base_dir = p
                scanned = scan_for_264_files(p)
                found_files.extend(scanned)
            elif os.path.isfile(p):
                if p.lower().endswith((".264", ".h264")):
                    found_files.append(p)
                    if base_dir is None:
                        base_dir = os.path.dirname(p)

        if not found_files:
            QMessageBox.warning(self, "No .264 Files Found", "No .264 or .h264 video files were found in the dropped path.")
            return

        # Auto-clear existing queue before loading new dropped files
        self.clear_queue()

        self.input_base_dir = base_dir
        self.input_path_edit.setText(paths[0] if len(paths) == 1 else f"{len(paths)} items dropped")

        if not self.output_path_edit.text() and base_dir:
            default_out = os.path.join(os.path.dirname(base_dir) if os.path.isfile(base_dir) else base_dir, "converted_mp4")
            self.output_path_edit.setText(default_out)
            self.output_dir = default_out

        self.populate_queue(found_files, base_dir)

    def browse_input_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Select Input Folder Containing .264 Files")
        if folder:
            files = scan_for_264_files(folder)
            if files:
                # Auto-clear existing queue before loading new files from folder
                self.clear_queue()

                self.input_base_dir = folder
                self.input_path_edit.setText(folder)

                if not self.output_path_edit.text():
                    default_out = os.path.join(folder, "converted_mp4")
                    self.output_path_edit.setText(default_out)
                    self.output_dir = default_out

                self.populate_queue(files, folder)

    def browse_input_files(self):
        files, _ = QFileDialog.getOpenFileNames(
            self, "Select .264 Files", "", "H.264 Video Files (*.264 *.h264);;All Files (*)"
        )
        if files:
            # Auto-clear existing queue before loading new files
            self.clear_queue()

            base_dir = os.path.dirname(files[0])
            self.input_base_dir = base_dir
            self.input_path_edit.setText(f"{len(files)} files selected")

            if not self.output_path_edit.text():
                default_out = os.path.join(base_dir, "converted_mp4")
                self.output_path_edit.setText(default_out)
                self.output_dir = default_out

            self.populate_queue(files, base_dir)

    def browse_output_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Select Output Destination Folder for MP4 files")
        if folder:
            self.output_path_edit.setText(folder)
            self.output_dir = folder

    def remove_file_from_queue(self, file_path: str):
        """Removes a single file from the queue without resetting existing statuses."""
        if self.is_conversion_started or (self.worker_thread and self.worker_thread.isRunning()):
            return

        target_row = -1
        for idx, item in enumerate(self.file_items):
            if item["file"] == file_path:
                target_row = idx
                break

        if target_row != -1:
            self.table.removeRow(target_row)
            self.file_items.pop(target_row)

            for idx, item in enumerate(self.file_items):
                item["row"] = idx

            total_count = len(self.file_items)
            self.lbl_file_count.setText(f"{total_count} file(s) ready")
            completed_count = 0
            for r in range(self.table.rowCount()):
                st_item = self.table.item(r, 4)
                if st_item and "Completed ✅" in st_item.text():
                    completed_count += 1

            if total_count > 0:
                pct = int((completed_count / total_count) * 100)
                self.progress_bar.setValue(pct)
                self.progress_bar.setFormat(f"{pct}% ({completed_count}/{total_count})")
            else:
                self.progress_bar.setValue(0)
                self.progress_bar.setFormat("0%")

    def populate_queue(self, files: List[str], base_dir: Optional[str]):
        """Populates queue table with scanned .264 files."""
        self.file_items = []
        self.table.setRowCount(0)
        self.table.showColumn(0)

        for i, file_path in enumerate(files):
            item_data = {
                "row": i,
                "file": file_path,
                "base_dir": base_dir
            }
            self.file_items.append(item_data)
            self.table.insertRow(i)

            # Column 0: Red X Remove Button
            btn_remove = QPushButton("❌")
            btn_remove.setFixedSize(20, 20)
            btn_remove.setCursor(Qt.CursorShape.PointingHandCursor)
            btn_remove.setToolTip("Remove file from queue")
            btn_remove.setStyleSheet("""
                QPushButton {
                    background-color: transparent;
                    color: #EF4444;
                    border: none;
                    font-size: 11px;
                    padding: 0px;
                }
                QPushButton:hover {
                    background-color: #333333;
                    color: #F87171;
                }
            """)
            target_path = file_path
            btn_remove.clicked.connect(lambda _, p=target_path: self.remove_file_from_queue(p))

            cell_widget = QWidget()
            cell_layout = QHBoxLayout(cell_widget)
            cell_layout.setContentsMargins(0, 0, 0, 0)
            cell_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
            cell_layout.addWidget(btn_remove)
            self.table.setCellWidget(i, 0, cell_widget)

            # Column 1: Name
            filename = os.path.basename(file_path)
            item_name = QTableWidgetItem(filename)
            item_name.setToolTip(filename)
            self.table.setItem(i, 1, item_name)

            # Column 2: Size
            size_mb = os.path.getsize(file_path) / (1024 * 1024)
            size_str = f"{size_mb:.2f} MB"
            item_size = QTableWidgetItem(size_str)
            item_size.setToolTip(size_str)
            self.table.setItem(i, 2, item_size)

            # Column 3: Relative path
            rel_p = ""
            if base_dir:
                try:
                    rel_p = str(Path(file_path).relative_to(Path(base_dir)))
                except ValueError:
                    rel_p = filename
            item_rel = QTableWidgetItem(rel_p)
            item_rel.setToolTip(rel_p)
            self.table.setItem(i, 3, item_rel)

            # Column 4: Status
            status_item = QTableWidgetItem("Pending ⏳")
            status_item.setForeground(QColor("#94A3B8"))
            self.table.setItem(i, 4, status_item)

        total_count = len(files)
        self.lbl_file_count.setText(f"{total_count} file(s) ready")
        if total_count > 0:
            self.progress_bar.setFormat(f"0% (0/{total_count})")
        else:
            self.progress_bar.setFormat("0%")
        self.progress_bar.setValue(0)
        self.log_text.clear()
        self.log_text.append(f"Loaded {total_count} file(s) into conversion queue.")

    def clear_queue(self):
        self.is_conversion_started = False
        self.file_items = []
        self.table.setRowCount(0)
        self.table.showColumn(0)
        self.input_path_edit.clear()
        self.lbl_file_count.setText("0 files found")
        self.progress_bar.setFormat("0%")
        self.progress_bar.setValue(0)
        self.log_text.clear()

    def on_start_stop_clicked(self):
        if self.is_conversion_started:
            self.cancel_conversion()
        else:
            self.start_conversion()

    def start_conversion(self):
        if not self.file_items:
            QMessageBox.warning(self, "Empty Queue", "Please select or drop a folder containing .264 files first.")
            return

        out_dir = self.output_path_edit.text().strip()
        if not out_dir:
            QMessageBox.warning(self, "Output Folder Required", "Please select an output folder for converted MP4 files.")
            return

        self.is_conversion_started = True
        self.output_dir = out_dir
        os.makedirs(self.output_dir, exist_ok=True)

        # Mark files already completed in UI so worker skips them
        for row in range(self.table.rowCount()):
            st_item = self.table.item(row, 4)
            if row < len(self.file_items):
                if st_item and "Completed ✅" in st_item.text():
                    self.file_items[row]["already_completed"] = True
                else:
                    self.file_items[row]["already_completed"] = False

        # Update button to Stop Conversion mode
        self.btn_start.setText("🛑 Stop Conversion")
        self.btn_start.setStyleSheet("background-color: #EF4444; color: #FFFFFF; font-weight: bold; border-radius: 0px; height: 20px; max-height: 22px;")

        # Completely hide column 0 (remove buttons) on table during conversion
        self.table.hideColumn(0)

        # Disable UI controls during processing
        self.btn_clear.setEnabled(False)
        self.btn_browse_folder.setEnabled(False)
        self.btn_browse_output.setEnabled(False)

        # Parse Workers choice
        num_workers = 4
        threads_text = self.cmb_threads.currentText()
        if "8 Workers" in threads_text:
            num_workers = 8
        elif "2 Workers" in threads_text:
            num_workers = 2
        elif "1 Worker" in threads_text:
            num_workers = 1
        elif "4 Workers" in threads_text:
            num_workers = 4

        # Setup Thread & Worker
        self.worker_thread = QThread()
        self.worker = ConversionWorker(
            file_items=self.file_items,
            output_dir=self.output_dir,
            preserve_structure=self.chk_preserve_structure.isChecked(),
            fps=None,
            target_duration_sec=None,
            max_workers=num_workers
        )
        self.worker.moveToThread(self.worker_thread)

        # Connect Signals
        self.worker_thread.started.connect(self.worker.run)
        self.worker.file_started.connect(self.on_file_started)
        self.worker.file_finished.connect(self.on_file_finished)
        self.worker.overall_progress.connect(self.on_progress)
        self.worker.log_message.connect(self.log_text.append)
        self.worker.all_completed.connect(self.on_all_completed)

        self.worker_thread.start()

    def cancel_conversion(self):
        if self.worker:
            self.worker.cancel()
            self.log_text.append("[WARNING] Stopping conversion...")
            self.btn_start.setEnabled(False)

    def restore_queue_interactivity(self):
        """Unhides column 0 and reveals remove buttons only for uncompleted items."""
        self.table.showColumn(0)
        for row in range(self.table.rowCount()):
            status_item = self.table.item(row, 4)
            cell_w = self.table.cellWidget(row, 0)
            if cell_w:
                if status_item and "Completed ✅" in status_item.text():
                    cell_w.setVisible(False)
                else:
                    cell_w.setVisible(True)
                    cell_w.setEnabled(True)

    def on_file_started(self, row: int, file_path: str):
        status_item = QTableWidgetItem("Converting... ⚙️")
        status_item.setForeground(QColor("#38BDF8"))
        self.table.setItem(row, 4, status_item)

    def on_file_finished(self, row: int, success: bool, log_msg: str):
        if "Cancelled" in log_msg or (self.worker and getattr(self.worker, "_is_cancelled", False) and not success):
            status_item = QTableWidgetItem("Pending ⏳")
            status_item.setForeground(QColor("#94A3B8"))
        elif success:
            status_item = QTableWidgetItem("Completed ✅")
            status_item.setForeground(QColor("#34D399"))
        else:
            status_item = QTableWidgetItem("Failed ❌")
            status_item.setForeground(QColor("#F87171"))
        self.table.setItem(row, 4, status_item)

    def on_progress(self, current: int, total: int):
        if total > 0:
            pct = int((current / total) * 100)
            self.progress_bar.setValue(pct)
            self.progress_bar.setFormat(f"{pct}% ({current}/{total})")

    def on_all_completed(self, success_count: int, fail_count: int):
        self.is_conversion_started = False
        self.log_text.append(f"\n✨ Batch finished: {success_count} succeeded, {fail_count} failed.")

        # Clean up thread
        if self.worker_thread:
            self.worker_thread.quit()
            self.worker_thread.wait()

        # Reset action button & re-enable UI controls
        self.btn_start.setText("🚀 Start Conversion")
        self.btn_start.setStyleSheet("background-color: #10B981; color: #FFFFFF; font-weight: bold; border-radius: 0px; height: 20px; max-height: 22px;")
        self.btn_start.setEnabled(True)
        self.btn_clear.setEnabled(True)
        self.btn_browse_folder.setEnabled(True)
        self.btn_browse_output.setEnabled(True)

        # Restore Queue Interactivity (Reveals X buttons for remaining pending files)
        self.restore_queue_interactivity()

        was_stopped = False
        if self.worker and getattr(self.worker, "_is_cancelled", False):
            was_stopped = True

        if was_stopped:
            remaining_count = len(self.file_items) - success_count
            QMessageBox.information(
                self,
                "Conversion Stopped",
                f"Conversion process was stopped successfully.\n\n"
                f"• {success_count} file(s) converted successfully.\n"
                f"• {remaining_count} file(s) remain in queue.\n\n"
                f"Output folder:\n{self.output_dir}"
            )
        elif fail_count == 0:
            QMessageBox.information(
                self,
                "Conversion Finished",
                f"Successfully converted all {success_count} .264 video files into MP4 format!\n\nOutput folder:\n{self.output_dir}"
            )
        else:
            QMessageBox.warning(
                self,
                "Conversion Finished with Warnings",
                f"Converted {success_count} files successfully.\n{fail_count} file(s) failed. Check log for details."
            )

    def open_output_folder(self):
        out_dir = self.output_path_edit.text().strip()
        if out_dir and os.path.exists(out_dir):
            if sys.platform == "win32":
                os.startfile(out_dir)
            elif sys.platform == "darwin":
                subprocess.run(["open", out_dir])
            else:
                subprocess.run(["xdg-open", out_dir])
        else:
            QMessageBox.information(self, "Output Folder", "Output folder does not exist yet.")
