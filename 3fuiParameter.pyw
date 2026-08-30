#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
视频比例检测与分类工具
功能：检测视频分辨率、码流、色彩空间，判断宽/高哪个是短板
配合 FFmpegFreeUI (3FUI) 使用
"""

import sys
import os
import subprocess
import shutil
import math
import copy
from pathlib import Path
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QLineEdit, QPushButton, QFileDialog, QTextEdit,
    QSplitter, QGroupBox, QMessageBox
)
from PySide6.QtCore import Qt, QThread, Signal, QMutex, QWaitCondition
from PySide6.QtGui import QFont, QColor, QPalette

# Windows 专用：禁止创建命令行窗口
if sys.platform == 'win32':
    CREATE_NO_WINDOW = 0x08000000
else:
    CREATE_NO_WINDOW = 0

# FFmpeg 支持的所有视频格式
VIDEO_EXTENSIONS = {
    '.mp4', '.m4v', '.m4p', '.m4b', '.mkv', '.webm',
    '.avi', '.divx', '.mov', '.qt', '.flv', '.f4v',
    '.wmv', '.asf', '.mpeg', '.mpg', '.mpe', '.m2v', '.vob',
    '.ts', '.m2ts', '.mts', '.m2t', '.3gp', '.3g2',
    '.ogv', '.ogm', '.rm', '.rmvb', '.amv', '.dpg',
    '.mxf', '.nut', '.swf', '.wtv', '.yuv',
    '.h264', '.h265', '.hevc', '.av1', '.vvc', '.evc',
    '.gif', '.mjpg', '.mjpeg',
}


def get_video_filter():
    ext_list = sorted(VIDEO_EXTENSIONS)
    all_ext = " ".join([f"*{ext}" for ext in ext_list])
    group_filters = []
    for ext in ext_list:
        name = ext[1:].upper()
        if name in ['M4V', 'M4P', 'M4B']:
            name = 'MP4 衍生'
        elif name in ['M2V', 'VOB']:
            name = 'MPEG 衍生'
        group_filters.append(f"{name} 文件 (*{ext})")
    return f"所有支持格式 ({all_ext});;{';;'.join(group_filters)};;所有文件 (*.*)"


class VideoDetectThread(QThread):
    progress = Signal(str, int, int, str, str)
    finished = Signal(list, list, list)
    error = Signal(str)

    def __init__(self, file_list, target_width, target_height):
        super().__init__()
        self.file_list = file_list
        self.target_width = target_width
        self.target_height = target_height
        self._mutex = QMutex()
        self._stop = False

    def stop(self):
        self._mutex.lock()
        self._stop = True
        self._mutex.unlock()

    def get_bitrate_display(self, bitrate):
        if bitrate is None or bitrate == 'N/A' or bitrate == '':
            return 'N/A'
        try:
            b = int(bitrate)
            if b >= 1000000:
                return f"{b / 1000000:.1f} Mbps"
            elif b >= 1000:
                return f"{b / 1000:.1f} kbps"
            else:
                return f"{b} bps"
        except:
            return 'N/A'

    def get_color_space(self, primaries, trc):
        if primaries == 'bt2020':
            if trc in ['smpte2084', 'arib-std-b67', 'bt2020_10bit', 'bt2020_12bit']:
                return 'HDR'
            elif trc == 'bt709' or trc is None:
                return 'SDR'
            else:
                return 'SDR'
        return 'SDR'

    def run(self):
        matched = []
        width_short = []
        height_short = []

        target_ratio = self.target_width / self.target_height

        for file_path in self.file_list:
            if self._stop:
                break

            try:
                cmd = [
                    'ffprobe',
                    '-v', 'error',
                    '-select_streams', 'v:0',
                    '-show_entries', 
                    'stream=width,height,bit_rate,color_primaries,color_trc',
                    '-of', 'csv=p=0',
                    file_path
                ]

                result = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=30,
                    encoding='utf-8',
                    creationflags=CREATE_NO_WINDOW
                )

                if result.returncode != 0:
                    self.error.emit(f"无法读取: {os.path.basename(file_path)}")
                    continue

                raw_output = result.stdout.strip()
                if not raw_output:
                    self.error.emit(f"ffprobe 无输出: {os.path.basename(file_path)}")
                    continue

                parts = raw_output.split(',')

                if len(parts) < 2:
                    self.error.emit(f"数据不足: {os.path.basename(file_path)} - {raw_output}")
                    continue

                try:
                    width = int(parts[0]) if parts[0] else 0
                    height = int(parts[1]) if parts[1] else 0
                except ValueError:
                    self.error.emit(f"分辨率解析失败: {os.path.basename(file_path)} - {raw_output}")
                    continue

                if width == 0 or height == 0:
                    self.error.emit(f"无效分辨率: {os.path.basename(file_path)}")
                    continue

                bitrate = 'N/A'
                primaries = None
                trc = None

                for val in parts[2:]:
                    val = val.strip()
                    if not val:
                        continue
                    if val.isdigit():
                        bitrate = val
                    elif val.startswith('bt') or val.startswith('smpte') or val.startswith('arib'):
                        if primaries is None:
                            primaries = val
                        else:
                            trc = val
                    else:
                        if trc is None:
                            trc = val

                bitrate_display = self.get_bitrate_display(bitrate)
                color_space = self.get_color_space(primaries, trc)

                actual_ratio = width / height
                tolerance = 0.001

                if abs(actual_ratio - target_ratio) < tolerance:
                    matched.append((file_path, width, height, bitrate_display, color_space))
                else:
                    required_width = height * target_ratio
                    required_height = width / target_ratio
                    px_tolerance = 2.0

                    if width < required_width - px_tolerance:
                        width_short.append((file_path, width, height, bitrate_display, color_space))
                    elif height < required_height - px_tolerance:
                        height_short.append((file_path, width, height, bitrate_display, color_space))
                    else:
                        if actual_ratio < target_ratio:
                            width_short.append((file_path, width, height, bitrate_display, color_space))
                        else:
                            height_short.append((file_path, width, height, bitrate_display, color_space))

                self.progress.emit(file_path, width, height, bitrate_display, color_space)

            except subprocess.TimeoutExpired:
                self.error.emit(f"超时: {os.path.basename(file_path)}")
            except Exception as e:
                self.error.emit(f"错误 {os.path.basename(file_path)}: {str(e)}")

        self.finished.emit(matched, width_short, height_short)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("视频比例检测与分类工具 (FFmpeg)")
        self.setGeometry(100, 100, 1100, 700)

        self.matched_files = []
        self.width_short_files = []
        self.height_short_files = []
        self.detect_thread = None
        self.is_detecting = False

        self.backup_matched = []
        self.backup_width_short = []
        self.backup_height_short = []
        self.has_backup = False

        self.init_ui()

    def init_ui(self):
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)
        main_layout.setSpacing(8)
        main_layout.setContentsMargins(8, 8, 8, 8)

        # 第一行：参数输入
        param_layout = QHBoxLayout()
        param_layout.setSpacing(5)

        param_layout.addWidget(QLabel("裁剪比例"))
        self.ratio_w = QLineEdit()
        self.ratio_w.setFixedWidth(40)
        self.ratio_w.setText("16")
        self.ratio_w.textChanged.connect(self.on_ratio_changed)
        param_layout.addWidget(self.ratio_w)
        param_layout.addWidget(QLabel(":"))
        self.ratio_h = QLineEdit()
        self.ratio_h.setFixedWidth(40)
        self.ratio_h.setText("9")
        self.ratio_h.textChanged.connect(self.on_ratio_changed)
        param_layout.addWidget(self.ratio_h)

        param_layout.addSpacing(20)

        param_layout.addWidget(QLabel("宽:高"))
        self.target_w = QLineEdit()
        self.target_w.setFixedWidth(50)
        self.target_w.setText("1280")
        self.target_w.textChanged.connect(self.on_target_changed)
        param_layout.addWidget(self.target_w)
        param_layout.addWidget(QLabel(":"))
        self.target_h = QLineEdit()
        self.target_h.setFixedWidth(50)
        self.target_h.setText("720")
        self.target_h.textChanged.connect(self.on_target_changed)
        param_layout.addWidget(self.target_h)

        param_layout.addStretch()
        main_layout.addLayout(param_layout)

        # 第二行：按钮
        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(8)

        self.btn_add_files = QPushButton("添加文件")
        self.btn_add_files.clicked.connect(self.add_files)
        btn_layout.addWidget(self.btn_add_files)

        self.btn_add_folder = QPushButton("添加文件夹")
        self.btn_add_folder.clicked.connect(self.add_folder)
        btn_layout.addWidget(self.btn_add_folder)

        self.btn_classify = QPushButton("分类素材")
        self.btn_classify.clicked.connect(self.classify_files)
        self.btn_classify.setEnabled(False)
        btn_layout.addWidget(self.btn_classify)

        self.btn_clear = QPushButton("清空列表")
        self.btn_clear.clicked.connect(self.clear_all)
        btn_layout.addWidget(self.btn_clear)

        self.btn_undo = QPushButton("撤销清空")
        self.btn_undo.clicked.connect(self.undo_clear)
        self.btn_undo.setEnabled(False)
        btn_layout.addWidget(self.btn_undo)

        btn_layout.addStretch()
        main_layout.addLayout(btn_layout)

        # 第三行：左右区域
        splitter = QSplitter(Qt.Horizontal)

        # 左侧：文件列表
        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)
        left_layout.setContentsMargins(0, 0, 0, 0)

        self.left_text = QTextEdit()
        self.left_text.setReadOnly(True)
        self.left_text.setFont(QFont("Consolas", 10))

        palette = self.left_text.palette()
        palette.setColor(QPalette.Base, QColor(0, 0, 0))
        palette.setColor(QPalette.Text, QColor(255, 255, 255))
        self.left_text.setPalette(palette)

        self.left_text.setStyleSheet("""
            QTextEdit {
                background-color: #000000;
                color: #ffffff;
                border: 1px solid #333333;
                border-radius: 3px;
                padding: 5px;
            }
        """)

        self.left_text.setHtml(self.get_placeholder())
        left_layout.addWidget(self.left_text)
        splitter.addWidget(left_widget)

        # 右侧：参数显示（缩小宽度）
        right_widget = QWidget()
        right_widget.setMaximumWidth(380)
        right_layout = QVBoxLayout(right_widget)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(5)

        # 白色区域
        self.white_group = QGroupBox("符合比例 (白色)")
        self.white_group.setStyleSheet("""
            QGroupBox { border: 2px solid #ffffff; border-radius: 5px; margin-top: 8px; padding-top: 8px; }
            QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 5px 0 5px; color: #ffffff; font-weight: bold; }
        """)
        white_layout = QVBoxLayout(self.white_group)
        self.white_cmd = QLineEdit()
        self.white_cmd.setReadOnly(True)
        self.white_cmd.setStyleSheet("color: #ffffff; font-weight: bold; font-size: 11px; background-color: #1a1a1a;")
        white_layout.addWidget(self.white_cmd)
        self.white_btn = QPushButton("复制参数且打开文件夹")
        self.white_btn.clicked.connect(lambda: self.copy_and_open(self.white_cmd, self.matched_files))
        self.white_btn.setEnabled(False)
        white_layout.addWidget(self.white_btn)
        right_layout.addWidget(self.white_group)

        # 绿色区域
        self.green_group = QGroupBox("宽度不够 (绿色)")
        self.green_group.setStyleSheet("""
            QGroupBox { border: 2px solid #2ecc71; border-radius: 5px; margin-top: 8px; padding-top: 8px; }
            QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 5px 0 5px; color: #2ecc71; font-weight: bold; }
        """)
        green_layout = QVBoxLayout(self.green_group)
        self.green_cmd = QLineEdit()
        self.green_cmd.setReadOnly(True)
        self.green_cmd.setStyleSheet("color: #2ecc71; font-weight: bold; font-size: 11px; background-color: #0a1a0a;")
        green_layout.addWidget(self.green_cmd)
        self.green_btn = QPushButton("复制参数且打开文件夹")
        self.green_btn.clicked.connect(lambda: self.copy_and_open(self.green_cmd, self.width_short_files))
        self.green_btn.setEnabled(False)
        green_layout.addWidget(self.green_btn)
        right_layout.addWidget(self.green_group)

        # 蓝色区域
        self.blue_group = QGroupBox("高度不够 (蓝色)")
        self.blue_group.setStyleSheet("""
            QGroupBox { border: 2px solid #3498db; border-radius: 5px; margin-top: 8px; padding-top: 8px; }
            QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 5px 0 5px; color: #3498db; font-weight: bold; }
        """)
        blue_layout = QVBoxLayout(self.blue_group)
        self.blue_cmd = QLineEdit()
        self.blue_cmd.setReadOnly(True)
        self.blue_cmd.setStyleSheet("color: #3498db; font-weight: bold; font-size: 11px; background-color: #0a0a1a;")
        blue_layout.addWidget(self.blue_cmd)
        self.blue_btn = QPushButton("复制参数且打开文件夹")
        self.blue_btn.clicked.connect(lambda: self.copy_and_open(self.blue_cmd, self.height_short_files))
        self.blue_btn.setEnabled(False)
        blue_layout.addWidget(self.blue_btn)
        right_layout.addWidget(self.blue_group)

        # 添加弹性空间，让内容顶在上面
        right_layout.addStretch()

        splitter.addWidget(right_widget)
        splitter.setSizes([700, 350])

        main_layout.addWidget(splitter)

        # 状态栏
        self.status_label = QLabel("就绪")
        self.statusBar().addWidget(self.status_label)

    def get_placeholder(self):
        return """
        <style>
            .placeholder { color: #555555; font-size: 13px; font-family: Consolas, monospace; }
        </style>
        <span class="placeholder">📂 点击「添加文件」或「添加文件夹」选择素材</span>
        """

    def display_classified_files(self):
        """显示分类后的文件列表 - 使用 HTML 表格确保对齐"""
        lines = []
        
        lines.append("""
        <style>
            .file-table { width: 100%; border-collapse: collapse; font-family: Consolas, monospace; font-size: 12px; color: #ffffff; }
            .file-table th { color: #88aaff; text-align: left; padding: 2px 5px; border-bottom: 1px solid #444; }
            .file-table td { padding: 2px 5px; white-space: nowrap; }
            .file-table .col-path { width: 48%; }
            .file-table .col-ext { width: 10%; }
            .file-table .col-res { width: 16%; }
            .file-table .col-bitrate { width: 14%; }
            .file-table .col-color { width: 12%; }
            .group-title { font-weight: bold; margin-top: 8px; display: block; }
            .group-green { color: #2ecc71; }
            .group-blue { color: #3498db; }
            .text-green { color: #2ecc71; }
            .text-blue { color: #3498db; }
            .placeholder { color: #555555; font-size: 13px; }
        </style>
        """)
        
        lines.append("""
        <table class="file-table">
        <tr>
            <th class="col-path">文件名（不含扩展名）</th>
            <th class="col-ext">扩展名</th>
            <th class="col-res">分辨率</th>
            <th class="col-bitrate">平均码流</th>
            <th class="col-color">色彩空间</th>
        </tr>
        """)
        
        has_content = False
        
        # 符合比例 - 白色
        if self.matched_files:
            lines.append('<tr><td colspan="5" style="padding-top:10px;"><span class="group-title">⬜ 符合比例 (白色) — 保留在原文件夹</span></td></tr>')
            for f, w, h, bitrate, color in sorted(self.matched_files, key=lambda x: x[0].lower()):
                # 不含扩展名的完整路径
                base = f
                path_without_ext = os.path.splitext(base)[0]
                ext = os.path.splitext(base)[1] or ''
                if len(path_without_ext) > 50:
                    path_without_ext = path_without_ext[:47] + "..."
                lines.append(f"""
                <tr>
                    <td class="col-path">{path_without_ext}</td>
                    <td class="col-ext">{ext}</td>
                    <td class="col-res">{w}x{h}</td>
                    <td class="col-bitrate">{bitrate}</td>
                    <td class="col-color">{color}</td>
                </tr>
                """)
            has_content = True
        
        # 宽度不够 - 全部绿色
        if self.width_short_files:
            lines.append('<tr><td colspan="5" style="padding-top:10px;"><span class="group-title text-green">🟢 宽度不够 (绿色) → 将移至 nWe(green)</span></td></tr>')
            for f, w, h, bitrate, color in sorted(self.width_short_files, key=lambda x: x[0].lower()):
                base = f
                path_without_ext = os.path.splitext(base)[0]
                ext = os.path.splitext(base)[1] or ''
                if len(path_without_ext) > 50:
                    path_without_ext = path_without_ext[:47] + "..."
                lines.append(f"""
                <tr class="text-green">
                    <td class="col-path">{path_without_ext}</td>
                    <td class="col-ext">{ext}</td>
                    <td class="col-res">{w}x{h}</td>
                    <td class="col-bitrate">{bitrate}</td>
                    <td class="col-color">{color}</td>
                </tr>
                """)
            has_content = True
        
        # 高度不够 - 全部蓝色
        if self.height_short_files:
            lines.append('<tr><td colspan="5" style="padding-top:10px;"><span class="group-title text-blue">🔵 高度不够 (蓝色) → 将移至 nTe(blue)</span></td></tr>')
            for f, w, h, bitrate, color in sorted(self.height_short_files, key=lambda x: x[0].lower()):
                base = f
                path_without_ext = os.path.splitext(base)[0]
                ext = os.path.splitext(base)[1] or ''
                if len(path_without_ext) > 50:
                    path_without_ext = path_without_ext[:47] + "..."
                lines.append(f"""
                <tr class="text-blue">
                    <td class="col-path">{path_without_ext}</td>
                    <td class="col-ext">{ext}</td>
                    <td class="col-res">{w}x{h}</td>
                    <td class="col-bitrate">{bitrate}</td>
                    <td class="col-color">{color}</td>
                </tr>
                """)
            has_content = True
        
        lines.append("</table>")
        
        if not has_content:
            lines.append('<span class="placeholder">📂 点击「添加文件」或「添加文件夹」选择素材</span>')
        
        self.left_text.setHtml("".join(lines))

    # ==================== 文件/文件夹添加 ====================

    def add_files(self):
        if self.is_detecting:
            QMessageBox.information(self, "提示", "正在检测中，请稍候...")
            return

        files, _ = QFileDialog.getOpenFileNames(
            self,
            "选择视频文件",
            "",
            get_video_filter()
        )

        if not files:
            return

        self.add_and_detect_files(files)

    def add_folder(self):
        if self.is_detecting:
            QMessageBox.information(self, "提示", "正在检测中，请稍候...")
            return

        folder = QFileDialog.getExistingDirectory(
            self,
            "选择包含视频文件的文件夹",
            "",
            QFileDialog.ShowDirsOnly
        )

        if not folder:
            return

        files = self.get_video_files_from_folder(folder)

        if not files:
            QMessageBox.information(self, "提示", f"该文件夹中未找到 FFmpeg 支持的视频文件\n\n{folder}")
            return

        self.add_and_detect_files(files)

    def get_video_files_from_folder(self, folder_path):
        video_files = []
        for root, dirs, files in os.walk(folder_path):
            for file in files:
                ext = os.path.splitext(file)[1].lower()
                if ext in VIDEO_EXTENSIONS:
                    video_files.append(os.path.join(root, file))
        return video_files

    def add_and_detect_files(self, files):
        existing_paths = set()
        for f, _, _, _, _ in self.matched_files:
            existing_paths.add(f)
        for f, _, _, _, _ in self.width_short_files:
            existing_paths.add(f)
        for f, _, _, _, _ in self.height_short_files:
            existing_paths.add(f)

        new_files = [f for f in files if f not in existing_paths]
        if not new_files:
            QMessageBox.information(self, "提示", "所有文件已在列表中，无需重复添加")
            return

        self.is_detecting = True
        self.btn_add_files.setEnabled(False)
        self.btn_add_folder.setEnabled(False)
        self.btn_classify.setEnabled(False)
        self.status_label.setText(f"正在检测 {len(new_files)} 个文件...")

        self.left_text.setHtml("<b style='color:#ffffff;'>⏳ 正在检测视频分辨率...</b><br>")

        try:
            tw = int(self.target_w.text()) if self.target_w.text() else 1280
            th = int(self.target_h.text()) if self.target_h.text() else 720
        except ValueError:
            QMessageBox.warning(self, "错误", "请输入有效的目标分辨率")
            self.is_detecting = False
            self.btn_add_files.setEnabled(True)
            self.btn_add_folder.setEnabled(True)
            return

        self.detect_thread = VideoDetectThread(new_files, tw, th)
        self.detect_thread.progress.connect(self.on_detect_progress)
        self.detect_thread.finished.connect(self.on_detect_finished)
        self.detect_thread.error.connect(self.on_detect_error)
        self.detect_thread.start()

    def on_detect_progress(self, file_path, width, height, bitrate, color_space):
        pass

    def on_detect_error(self, msg):
        current = self.left_text.toHtml()
        self.left_text.setHtml(current + f"<br><font color='#ff6b6b'>❌ {msg}</font>")

    def on_detect_finished(self, matched, width_short, height_short):
        self.matched_files.extend(matched)
        self.width_short_files.extend(width_short)
        self.height_short_files.extend(height_short)

        self.is_detecting = False
        self.btn_add_files.setEnabled(True)
        self.btn_add_folder.setEnabled(True)

        if self.matched_files or self.width_short_files or self.height_short_files:
            self.btn_classify.setEnabled(True)

        self.display_classified_files()
        self.generate_parameters()

        total = len(self.matched_files) + len(self.width_short_files) + len(self.height_short_files)
        self.status_label.setText(
            f"检测完成: 共 {total} 个文件 | 符合 {len(self.matched_files)} 个 | 宽度不够 {len(self.width_short_files)} 个 | 高度不够 {len(self.height_short_files)} 个"
        )

    # ==================== 参数联动 ====================

    def ceil_to_even(self, value):
        return int(math.ceil(value / 2.0)) * 2

    def on_ratio_changed(self):
        try:
            rw_text = self.ratio_w.text().strip()
            rh_text = self.ratio_h.text().strip()
            if not rw_text or not rh_text:
                return
            rw = float(rw_text)
            rh = float(rh_text)
            if rw <= 0 or rh <= 0:
                return

            tw_text = self.target_w.text().strip()
            th_text = self.target_h.text().strip()
            if not tw_text and not th_text:
                return

            ratio = rw / rh

            if tw_text:
                tw = float(tw_text)
                if tw > 0:
                    new_h = tw / ratio
                    if new_h > 0:
                        new_h_even = self.ceil_to_even(new_h)
                        current_h = float(th_text) if th_text else 0
                        if abs(current_h - new_h_even) > 0.5:
                            self.target_h.blockSignals(True)
                            self.target_h.setText(str(new_h_even))
                            self.target_h.blockSignals(False)
            elif th_text:
                th = float(th_text)
                if th > 0:
                    new_w = th * ratio
                    if new_w > 0:
                        new_w_even = self.ceil_to_even(new_w)
                        current_w = float(tw_text) if tw_text else 0
                        if abs(current_w - new_w_even) > 0.5:
                            self.target_w.blockSignals(True)
                            self.target_w.setText(str(new_w_even))
                            self.target_w.blockSignals(False)

        except (ValueError, ZeroDivisionError):
            pass

    def on_target_changed(self):
        try:
            tw_text = self.target_w.text().strip()
            th_text = self.target_h.text().strip()
            if not tw_text or not th_text:
                return

            tw = int(tw_text)
            th = int(th_text)
            if tw <= 0 or th <= 0:
                return

            from math import gcd
            g = gcd(tw, th)
            rw = tw // g
            rh = th // g

            if rw > 0 and rh > 0:
                self.ratio_w.blockSignals(True)
                self.ratio_h.blockSignals(True)
                self.ratio_w.setText(str(rw))
                self.ratio_h.setText(str(rh))
                self.ratio_w.blockSignals(False)
                self.ratio_h.blockSignals(False)

        except (ValueError, ZeroDivisionError):
            pass

    # ==================== 参数生成 ====================

    def generate_parameters(self):
        tw = int(self.target_w.text()) if self.target_w.text() else 1280
        th = int(self.target_h.text()) if self.target_h.text() else 720

        ratio_w = int(self.ratio_w.text()) if self.ratio_w.text() else 16
        ratio_h = int(self.ratio_h.text()) if self.ratio_h.text() else 9

        if self.matched_files:
            white_cmd = f"scale={tw}:{th}"
            self.white_cmd.setText(white_cmd)
            self.white_btn.setEnabled(True)
        else:
            self.white_cmd.setText("无符合比例的文件")
            self.white_btn.setEnabled(False)

        if self.width_short_files:
            green_cmd = f"crop=iw:iw*{ratio_h}/{ratio_w},scale={tw}:{th}"
            self.green_cmd.setText(green_cmd)
            self.green_btn.setEnabled(True)
        else:
            self.green_cmd.setText("无宽度不够的文件")
            self.green_btn.setEnabled(False)

        if self.height_short_files:
            blue_cmd = f"crop=ih*{ratio_w}/{ratio_h}:ih,scale={tw}:{th}"
            self.blue_cmd.setText(blue_cmd)
            self.blue_btn.setEnabled(True)
        else:
            self.blue_cmd.setText("无高度不够的文件")
            self.blue_btn.setEnabled(False)

    # ==================== 分类素材 ====================

    def classify_files(self):
        total = len(self.width_short_files) + len(self.height_short_files)
        if total == 0:
            QMessageBox.information(self, "提示", "没有需要分类的文件（符合比例的文件保留在原位置）")
            return

        detail_msg = ""
        if self.width_short_files:
            detail_msg += f"🟢 宽度不够 ({len(self.width_short_files)} 个) → nWe(green)\n"
            for f, _, _, _, _ in self.width_short_files[:3]:
                detail_msg += f"    {os.path.basename(f)}\n"
            if len(self.width_short_files) > 3:
                detail_msg += f"    ... 还有 {len(self.width_short_files) - 3} 个\n"
            detail_msg += "\n"

        if self.height_short_files:
            detail_msg += f"🔵 高度不够 ({len(self.height_short_files)} 个) → nTe(blue)\n"
            for f, _, _, _, _ in self.height_short_files[:3]:
                detail_msg += f"    {os.path.basename(f)}\n"
            if len(self.height_short_files) > 3:
                detail_msg += f"    ... 还有 {len(self.height_short_files) - 3} 个\n"
            detail_msg += "\n"

        detail_msg += f"⬜ 符合比例 ({len(self.matched_files)} 个) 保留在原文件夹"

        reply = QMessageBox.question(
            self,
            "确认分类",
            f"将移动 {total} 个文件到对应的文件夹中：\n\n{detail_msg}\n\n确认？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )

        if reply != QMessageBox.StandardButton.Yes:
            return

        moved_count = 0
        moved_details = []

        for file_path, w, h, bitrate, color in self.width_short_files:
            dir_path = os.path.dirname(file_path)
            new_dir = os.path.join(dir_path, "nWe(green)")
            os.makedirs(new_dir, exist_ok=True)
            dest = os.path.join(new_dir, os.path.basename(file_path))
            try:
                shutil.move(file_path, dest)
                moved_count += 1
                moved_details.append(f"  {os.path.basename(file_path)} → {new_dir}")
            except Exception as e:
                self.status_label.setText(f"移动失败: {os.path.basename(file_path)} - {str(e)}")

        for file_path, w, h, bitrate, color in self.height_short_files:
            dir_path = os.path.dirname(file_path)
            new_dir = os.path.join(dir_path, "nTe(blue)")
            os.makedirs(new_dir, exist_ok=True)
            dest = os.path.join(new_dir, os.path.basename(file_path))
            try:
                shutil.move(file_path, dest)
                moved_count += 1
                moved_details.append(f"  {os.path.basename(file_path)} → {new_dir}")
            except Exception as e:
                self.status_label.setText(f"移动失败: {os.path.basename(file_path)} - {str(e)}")

        updated_matched = [(f, w, h, bitrate, color) for f, w, h, bitrate, color in self.matched_files]

        updated_width_short = []
        for f, w, h, bitrate, color in self.width_short_files:
            dir_path = os.path.dirname(f)
            new_path = os.path.join(dir_path, "nWe(green)", os.path.basename(f))
            updated_width_short.append((new_path, w, h, bitrate, color))

        updated_height_short = []
        for f, w, h, bitrate, color in self.height_short_files:
            dir_path = os.path.dirname(f)
            new_path = os.path.join(dir_path, "nTe(blue)", os.path.basename(f))
            updated_height_short.append((new_path, w, h, bitrate, color))

        self.matched_files = updated_matched
        self.width_short_files = updated_width_short
        self.height_short_files = updated_height_short

        self.display_classified_files()
        self.generate_parameters()

        self.btn_classify.setEnabled(False)

        self.status_label.setText(f"分类完成: 移动了 {moved_count} 个文件，{len(self.matched_files)} 个符合比例的文件保留在原位置")

        detail = "\n".join(moved_details) if moved_details else "无文件移动"
        QMessageBox.information(
            self,
            "完成",
            f"✅ 成功移动 {moved_count} 个文件\n\n{detail}\n\n"
            f"⬜ {len(self.matched_files)} 个符合比例的文件保留在原文件夹"
        )

    # ==================== 复制参数并打开文件夹 ====================

    def copy_and_open(self, line_edit, file_list):
        if not file_list:
            QMessageBox.information(self, "提示", "没有文件可操作")
            return

        cmd = line_edit.text()
        if cmd and not cmd.startswith("无"):
            clipboard = QApplication.clipboard()
            clipboard.setText(cmd)
            self.status_label.setText(f"已复制: {cmd}")

        folders = set()
        for file_path, _, _, _, _ in file_list:
            folder = os.path.dirname(file_path)
            if os.path.exists(folder):
                folders.add(folder)

        if not folders:
            QMessageBox.warning(self, "提示", "没有找到可打开的文件夹")
            return

        for folder in folders:
            if sys.platform == "win32":
                try:
                    os.startfile(folder)
                except Exception as e:
                    self.status_label.setText(f"打开文件夹失败: {folder} - {str(e)}")

        if len(folders) > 1:
            self.status_label.setText(f"已打开 {len(folders)} 个文件夹")

    # ==================== 清空与撤销 ====================

    def clear_all(self):
        if (not self.matched_files and not self.width_short_files and not self.height_short_files):
            QMessageBox.information(self, "提示", "列表已为空")
            return

        self.backup_matched = copy.deepcopy(self.matched_files)
        self.backup_width_short = copy.deepcopy(self.width_short_files)
        self.backup_height_short = copy.deepcopy(self.height_short_files)
        self.has_backup = True
        self.btn_undo.setEnabled(True)

        self.matched_files = []
        self.width_short_files = []
        self.height_short_files = []
        self.white_cmd.clear()
        self.green_cmd.clear()
        self.blue_cmd.clear()
        self.white_btn.setEnabled(False)
        self.green_btn.setEnabled(False)
        self.blue_btn.setEnabled(False)
        self.btn_classify.setEnabled(False)
        self.status_label.setText("已清空（可点击「撤销清空」恢复）")

        self.left_text.setHtml(self.get_placeholder())

    def undo_clear(self):
        if not self.has_backup:
            QMessageBox.information(self, "提示", "没有可恢复的数据")
            return

        self.matched_files = copy.deepcopy(self.backup_matched)
        self.width_short_files = copy.deepcopy(self.backup_width_short)
        self.height_short_files = copy.deepcopy(self.backup_height_short)

        if self.matched_files or self.width_short_files or self.height_short_files:
            self.btn_classify.setEnabled(True)

        self.display_classified_files()
        self.generate_parameters()

        self.has_backup = False
        self.btn_undo.setEnabled(False)
        self.status_label.setText("已恢复清空前的状态")

        total = len(self.matched_files) + len(self.width_short_files) + len(self.height_short_files)
        QMessageBox.information(
            self,
            "恢复完成",
            f"已恢复 {total} 个文件\n\n"
            f"符合: {len(self.matched_files)} 个\n"
            f"宽度不够: {len(self.width_short_files)} 个\n"
            f"高度不够: {len(self.height_short_files)} 个"
        )


def main():
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()