"""
================================================================================
Project: Modern PDF Reader & Note Studio 
         (Smart Voice Reader อ่านลงล่างเฉพาะเนื้อหาใต้คำค้นหา จบแล้วหยุดทันที ไม่วนลูป)
Tool: Python 3, PyQt6, PyMuPDF, Ollama, macOS Speech
================================================================================
"""

import sys
import os
import re
import subprocess
import pymupdf as fitz
import ollama

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLabel, QListWidget, QListWidgetItem, QTextEdit,
    QLineEdit, QFileDialog, QSplitter, QGroupBox, QScrollArea,
    QTabWidget, QMessageBox, QToolBar, QSpinBox
)
from PyQt6.QtCore import Qt, QSize, QRectF, QThread, pyqtSignal
from PyQt6.QtGui import (
    QPixmap, QImage, QIcon, QFont, QAction, QKeySequence, 
    QPainter, QColor
)


# ==============================================================================
# 1. เธรดทำงานเบื้องหลังสำหรับ Ollama AI
# ==============================================================================
class OllamaWorker(QThread):
    finished = pyqtSignal(str)
    error = pyqtSignal(str)

    def __init__(self, text, model="qwen2.5:7b"):
        super().__init__()
        self.text = text
        self.model = model
        self.is_cancelled = False

    def run(self):
        try:
            prompt = (
                "คุณคือผู้ช่วยวิเคราะห์เอกสารอัจฉริยะ "
                "โปรดสรุปใจความสำคัญและวิเคราะห์เนื้อหาต่อไปนี้เป็นภาษาไทย "
                "ให้อ่านเข้าใจง่าย กระชับ และจัดเป็นหัวข้อย่อย:\n\n"
                f"{self.text}"
            )
            response = ollama.chat(
                model=self.model,
                messages=[{'role': 'user', 'content': prompt}]
            )
            if not self.is_cancelled:
                self.finished.emit(response['message']['content'])
        except Exception as e:
            if not self.is_cancelled:
                self.error.emit(str(e))


# ==============================================================================
# 2. เธรดอ่านเสียง TTS (ตรวจจับภาษาไทยและเลือกเสียง Kanya ให้อัตโนมัติ)
# ==============================================================================
class TTSWorker(QThread):
    finished = pyqtSignal()
    error = pyqtSignal(str)

    def __init__(self, text):
        super().__init__()
        self.text = text
        self.process = None

    def run(self):
        try:
            has_thai = any('\u0e00' <= char <= '\u0e7f' for char in self.text)
            cmd = ["say"]
            if has_thai:
                thai_voice = self.find_thai_voice()
                cmd.extend(["-v", thai_voice])

            cmd.append(self.text)
            self.process = subprocess.Popen(cmd)
            self.process.wait()
            self.finished.emit()
        except Exception as e:
            self.error.emit(str(e))

    def find_thai_voice(self):
        try:
            out = subprocess.check_output(["say", "-v", "?"], text=True)
            for line in out.splitlines():
                if "th_TH" in line or "th-TH" in line:
                    return line.split()[0]
        except Exception:
            pass
        return "Kanya"

    def stop(self):
        if self.process and self.process.poll() is None:
            self.process.terminate()
            self.process.wait()


# ==============================================================================
# 3. หน้าต่างหลักของโปรแกรม
# ==============================================================================
class PDFReaderApp(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Modern PDF Reader & Workspace (AI & Smart Voice)")
        self.resize(1200, 800)

        self.doc = None
        self.current_page = 0
        self.total_pages = 0
        self.zoom_factor = 1.25
        self.current_search_query = ""

        self.ai_worker = None
        self.tts_worker = None

        self.init_ui()

    def init_ui(self):
        self.create_toolbar()

        main_widget = QWidget()
        self.setCentralWidget(main_widget)
        main_layout = QHBoxLayout(main_widget)
        main_layout.setContentsMargins(5, 5, 5, 5)

        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        main_layout.addWidget(self.splitter)

        # 1. แผงซ้าย: Thumbnails
        left_panel = QWidget()
        left_layout = QVBoxLayout(left_panel)
        left_layout.addWidget(QLabel("📑 หน้าเอกสาร (Pages)"))

        self.thumbnail_list = QListWidget()
        self.thumbnail_list.setIconSize(QSize(100, 140))
        self.thumbnail_list.currentRowChanged.connect(self.go_to_page)
        left_layout.addWidget(self.thumbnail_list)
        self.splitter.addWidget(left_panel)

        # 2. แผงกลาง: หน้าอ่าน PDF
        center_panel = QWidget()
        center_layout = QVBoxLayout(center_panel)

        nav_layout = QHBoxLayout()
        self.btn_prev = QPushButton("◀ หน้าก่อนหน้า")
        self.btn_prev.clicked.connect(self.prev_page)
        nav_layout.addWidget(self.btn_prev)

        self.spin_page = QSpinBox()
        self.spin_page.setMinimum(1)
        self.spin_page.valueChanged.connect(lambda val: self.go_to_page(val - 1))
        nav_layout.addWidget(self.spin_page)

        self.lbl_page_total = QLabel("/ 0")
        nav_layout.addWidget(self.lbl_page_total)

        self.btn_next = QPushButton("หน้าถัดไป ▶")
        self.btn_next.clicked.connect(self.next_page)
        nav_layout.addWidget(self.btn_next)
        nav_layout.addStretch()

        center_layout.addLayout(nav_layout)

        self.scroll_area = QScrollArea()
        self.scroll_area.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_pdf_page = QLabel("ยังไม่ได้เปิดไฟล์ PDF\nกดที่เมนู 'เปิดไฟล์ PDF' เพื่อเริ่มต้น")
        self.lbl_pdf_page.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_pdf_page.setStyleSheet("color: #666; font-size: 14px;")
        self.scroll_area.setWidget(self.lbl_pdf_page)
        self.scroll_area.setWidgetResizable(True)
        center_layout.addWidget(self.scroll_area)

        self.splitter.addWidget(center_panel)

        # 3. แผงขวา: ค้นหาคำ + โน้ตสรุป + ควบคุม AI & อ่านเสียง
        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)

        tabs = QTabWidget()

        # --- แท็บ 1: ค้นหาคำ ---
        search_tab = QWidget()
        st_layout = QVBoxLayout(search_tab)
        
        search_bar = QHBoxLayout()
        search_bar.setSpacing(6)
        search_bar.setContentsMargins(0, 0, 0, 0)

        self.txt_search = QLineEdit()
        self.txt_search.setPlaceholderText("พิมพ์คำที่ต้องการค้นหา...")
        self.txt_search.returnPressed.connect(self.search_text)
        self.txt_search.setFixedHeight(38)
        self.txt_search.setStyleSheet("""
            QLineEdit {
                background-color: #1e1e1e;
                color: #ffffff;
                border: 1px solid #444444;
                border-radius: 6px;
                padding: 0 10px;
                font-size: 13px;
            }
            QLineEdit:focus {
                border: 1px solid #007acc;
            }
        """)

        self.btn_search = QPushButton("🔍 ค้นหา")
        self.btn_search.clicked.connect(self.search_text)
        self.btn_search.setFixedHeight(38)
        self.btn_search.setStyleSheet("""
            QPushButton {
                background-color: #333333;
                color: #ffffff;
                border: 1px solid #444444;
                border-radius: 6px;
                padding: 0 12px;
                font-size: 13px;
            }
            QPushButton:hover {
                background-color: #444444;
            }
            QPushButton:pressed {
                background-color: #222222;
            }
        """)

        self.btn_clear_search = QPushButton("✖")
        self.btn_clear_search.setToolTip("ล้างการค้นหาและเอาไฮไลต์ออก")
        self.btn_clear_search.clicked.connect(self.clear_search)
        self.btn_clear_search.setFixedSize(38, 38)
        self.btn_clear_search.setStyleSheet("""
            QPushButton {
                background-color: #333333;
                color: #ffffff;
                border: 1px solid #444444;
                border-radius: 6px;
                font-size: 13px;
            }
            QPushButton:hover {
                background-color: #444444;
            }
            QPushButton:pressed {
                background-color: #222222;
            }
        """)

        search_bar.addWidget(self.txt_search)
        search_bar.addWidget(self.btn_search)
        search_bar.addWidget(self.btn_clear_search)
        st_layout.addLayout(search_bar)

        # ปุ่มฟังเสียงในแท็บค้นหา
        self.btn_tts_search = QPushButton("🔊 ฟังเนื้อหาตรงคำค้นหา")
        self.btn_tts_search.setFixedHeight(34)
        self.btn_tts_search.setStyleSheet("""
            QPushButton {
                background-color: #059669;
                color: #ffffff;
                border: none;
                border-radius: 6px;
                font-weight: bold;
                font-size: 13px;
            }
            QPushButton:hover {
                background-color: #047857;
            }
        """)
        self.btn_tts_search.clicked.connect(self.toggle_tts)
        st_layout.addWidget(self.btn_tts_search)

        self.search_results = QListWidget()
        self.search_results.itemClicked.connect(self.on_search_result_clicked)
        st_layout.addWidget(self.search_results)
        tabs.addTab(search_tab, "🔍 ค้นหาคำ")

        # --- แท็บ 2: จดโน้ต + AI + เสียง ---
        note_tab = QWidget()
        nt_layout = QVBoxLayout(note_tab)

        ai_btn_layout = QHBoxLayout()
        ai_btn_layout.setSpacing(6)

        self.btn_ai = QPushButton("✨ วิเคราะห์หน้านี้ด้วย AI")
        self.btn_ai.setFixedHeight(38)
        self.btn_ai.setStyleSheet("""
            QPushButton {
                background-color: #2563EB;
                color: #ffffff;
                border: none;
                border-radius: 6px;
                font-weight: bold;
                font-size: 13px;
            }
            QPushButton:hover {
                background-color: #1D4ED8;
            }
            QPushButton:disabled {
                background-color: #3f3f46;
                color: #71717a;
            }
        """)
        self.btn_ai.clicked.connect(self.analyze_page_with_ollama)

        self.btn_cancel_ai = QPushButton("🛑 ยกเลิก")
        self.btn_cancel_ai.setFixedHeight(38)
        self.btn_cancel_ai.setEnabled(False)
        self.btn_cancel_ai.setStyleSheet("""
            QPushButton {
                background-color: #DC2626;
                color: #ffffff;
                border: none;
                border-radius: 6px;
                font-weight: bold;
                font-size: 13px;
                padding: 0 14px;
            }
            QPushButton:hover {
                background-color: #B91C1C;
            }
            QPushButton:disabled {
                background-color: #27272a;
                color: #52525b;
            }
        """)
        self.btn_cancel_ai.clicked.connect(self.cancel_ai_processing)

        ai_btn_layout.addWidget(self.btn_ai, stretch=3)
        ai_btn_layout.addWidget(self.btn_cancel_ai, stretch=1)
        nt_layout.addLayout(ai_btn_layout)

        self.btn_tts_note = QPushButton("🔊 ฟังเสียงอ่าน (TTS)")
        self.btn_tts_note.setFixedHeight(34)
        self.btn_tts_note.setStyleSheet("""
            QPushButton {
                background-color: #059669;
                color: #ffffff;
                border: none;
                border-radius: 6px;
                font-weight: bold;
                font-size: 13px;
            }
            QPushButton:hover {
                background-color: #047857;
            }
        """)
        self.btn_tts_note.clicked.connect(self.toggle_tts)
        nt_layout.addWidget(self.btn_tts_note)

        self.txt_notes = QTextEdit()
        self.txt_notes.setPlaceholderText("จดสรุปเนื้อหา หรือกดปุ่มด้านบนเพื่อให้ AI ช่วยสรุป/อ่านออกเสียงให้ฟัง...")
        self.txt_notes.setStyleSheet("font-size: 13px; padding: 6px;")
        nt_layout.addWidget(self.txt_notes)
        tabs.addTab(note_tab, "📝 โน้ตสรุป")

        right_layout.addWidget(tabs)
        self.splitter.addWidget(right_panel)

        self.splitter.setSizes([180, 680, 340])

    def create_toolbar(self):
        toolbar = QToolBar("เครื่องมือหลัก")
        toolbar.setMovable(False)
        self.addToolBar(toolbar)

        btn_open = QAction("📂 เปิดไฟล์ PDF", self)
        btn_open.setShortcut(QKeySequence("Ctrl+O"))
        btn_open.triggered.connect(self.open_pdf_dialog)
        toolbar.addAction(btn_open)

        toolbar.addSeparator()

        btn_zoom_in = QAction("➕ ซูมเข้า", self)
        btn_zoom_in.triggered.connect(self.zoom_in)
        toolbar.addAction(btn_zoom_in)

        btn_zoom_out = QAction("➖ ซูมออก", self)
        btn_zoom_out.triggered.connect(self.zoom_out)
        toolbar.addAction(btn_zoom_out)

        btn_zoom_reset = QAction("🔄 100%", self)
        btn_zoom_reset.triggered.connect(self.zoom_reset)
        toolbar.addAction(btn_zoom_reset)

    def open_pdf_dialog(self):
        file_path, _ = QFileDialog.getOpenFileNames(
            self, "เลือกเอกสาร PDF", "", "PDF Files (*.pdf)"
        )
        if file_path:
            self.load_pdf(file_path[0])

    def load_pdf(self, path):
        try:
            self.stop_tts()

            self.doc = fitz.open(path)
            self.total_pages = len(self.doc)
            self.current_page = 0
            self.current_search_query = ""

            self.spin_page.setMaximum(self.total_pages)
            self.spin_page.setValue(1)
            self.lbl_page_total.setText(f"/ {self.total_pages}")
            self.setWindowTitle(f"PDF Reader - {os.path.basename(path)}")

            self.thumbnail_list.clear()
            for i in range(self.total_pages):
                page = self.doc.load_page(i)
                pix = page.get_pixmap(dpi=36)
                qimg = QImage(
                    pix.samples, pix.width, pix.height, pix.stride,
                    QImage.Format.Format_RGB888
                )
                icon = QIcon(QPixmap.fromImage(qimg))
                item = QListWidgetItem(icon, f"หน้า {i + 1}")
                self.thumbnail_list.addItem(item)

            self.render_page()

        except Exception as e:
            QMessageBox.critical(self, "เกิดข้อผิดพลาด", f"ไม่สามารถเปิดไฟล์ได้:\n{str(e)}")

    def render_page(self):
        if not self.doc or self.current_page >= self.total_pages:
            return

        page = self.doc.load_page(self.current_page)
        zoom_dpi = int(100 * self.zoom_factor)
        pix = page.get_pixmap(dpi=zoom_dpi)

        qimg = QImage(
            pix.samples, pix.width, pix.height, pix.stride,
            QImage.Format.Format_RGB888
        )
        pixmap = QPixmap.fromImage(qimg)

        # วาดแถบไฮไลต์สีเหลือง
        if self.current_search_query:
            rects = page.search_for(self.current_search_query)
            if rects:
                painter = QPainter(pixmap)
                painter.setBrush(QColor(255, 235, 59, 130))
                painter.setPen(Qt.PenStyle.NoPen)

                scale_x = pixmap.width() / page.rect.width
                scale_y = pixmap.height() / page.rect.height

                for r in rects:
                    highlight_rect = QRectF(
                        r.x0 * scale_x,
                        r.y0 * scale_y,
                        (r.x1 - r.x0) * scale_x,
                        (r.y1 - r.y0) * scale_y
                    )
                    painter.drawRect(highlight_rect)

                painter.end()

        self.lbl_pdf_page.setPixmap(pixmap)

        self.thumbnail_list.blockSignals(True)
        self.thumbnail_list.setCurrentRow(self.current_page)
        self.thumbnail_list.blockSignals(False)

    def go_to_page(self, page_index):
        if self.doc and 0 <= page_index < self.total_pages:
            self.stop_tts()
            self.current_page = page_index
            self.spin_page.blockSignals(True)
            self.spin_page.setValue(page_index + 1)
            self.spin_page.blockSignals(False)
            self.render_page()

    def prev_page(self):
        if self.current_page > 0:
            self.go_to_page(self.current_page - 1)

    def next_page(self):
        if self.current_page < self.total_pages - 1:
            self.go_to_page(self.current_page + 1)

    def zoom_in(self):
        if self.zoom_factor < 3.0:
            self.zoom_factor += 0.2
            self.render_page()

    def zoom_out(self):
        if self.zoom_factor > 0.6:
            self.zoom_factor -= 0.2
            self.render_page()

    def zoom_reset(self):
        self.zoom_factor = 1.0
        self.render_page()

    def search_text(self):
        query = self.txt_search.text().strip()
        if not self.doc or not query:
            return

        self.current_search_query = query
        self.search_results.clear()
        found_count = 0
        first_match_page = None

        for i in range(self.total_pages):
            page = self.doc.load_page(i)
            rects = page.search_for(query)
            if rects:
                if first_match_page is None:
                    first_match_page = i
                found_count += len(rects)
                item = QListWidgetItem(f"📍 หน้า {i + 1} (พบ {len(rects)} จุด)")
                item.setData(Qt.ItemDataRole.UserRole, i)
                self.search_results.addItem(item)

        self.btn_tts_search.setText(f"🔊 ฟังเนื้อหาตรงคำค้นหา ('{query}')")

        if found_count == 0:
            self.search_results.addItem("❌ ไม่พบคำที่ค้นหา")
            self.render_page()
        else:
            if first_match_page is not None:
                self.go_to_page(first_match_page)
            else:
                self.render_page()

    def clear_search(self):
        self.current_search_query = ""
        self.txt_search.clear()
        self.search_results.clear()
        self.btn_tts_search.setText("🔊 ฟังเนื้อหาตรงคำค้นหา")
        self.render_page()

    def on_search_result_clicked(self, item):
        page_num = item.data(Qt.ItemDataRole.UserRole)
        if page_num is not None:
            self.go_to_page(page_num)

    # ==========================================================================
    # ระบบควบคุม Ollama AI
    # ==========================================================================
    def analyze_page_with_ollama(self):
        if not self.doc:
            QMessageBox.warning(self, "แจ้งเตือน", "กรุณาเปิดไฟล์ PDF ก่อนใช้งาน AI")
            return

        page = self.doc.load_page(self.current_page)
        page_text = page.get_text().strip()

        if not page_text:
            QMessageBox.warning(
                self, "แจ้งเตือน", 
                "ไม่พบข้อความในหน้านี้ (อาจเป็นรูปภาพล้วน หรือเอกสารสแกน)"
            )
            return

        self.btn_ai.setEnabled(False)
        self.btn_ai.setText("⏳ กำลังวิเคราะห์...")
        self.btn_cancel_ai.setEnabled(True)

        self.txt_notes.append(f"\n--- 🤖 [AI สรุปเนื้อหา หน้า {self.current_page + 1}] ---")
        self.txt_notes.append("กำลังประมวลผล กรุณารอสักครู่ (กด 'ยกเลิก' ได้)...\n")

        self.ai_worker = OllamaWorker(text=page_text, model="qwen2.5:7b")
        self.ai_worker.finished.connect(self.on_ai_success)
        self.ai_worker.error.connect(self.on_ai_failed)
        self.ai_worker.start()

    def cancel_ai_processing(self):
        if self.ai_worker and self.ai_worker.isRunning():
            self.ai_worker.is_cancelled = True
            self.ai_worker.terminate()
            self.ai_worker.wait(400)

            current_text = self.txt_notes.toPlainText()
            cleaned_text = current_text.replace(
                "กำลังประมวลผล กรุณารอสักครู่ (กด 'ยกเลิก' ได้)...\n", ""
            )
            self.txt_notes.setText(cleaned_text + "❌ [ยกเลิกการวิเคราะห์โดยผู้ใช้]\n")

        self.reset_ai_buttons()

    def on_ai_success(self, summary_text):
        current_text = self.txt_notes.toPlainText()
        cleaned_text = current_text.replace(
            "กำลังประมวลผล กรุณารอสักครู่ (กด 'ยกเลิก' ได้)...\n", ""
        )
        self.txt_notes.setText(cleaned_text + summary_text + "\n")
        self.reset_ai_buttons()

    def on_ai_failed(self, error_message):
        self.reset_ai_buttons()
        QMessageBox.critical(
            self, "เกิดข้อผิดพลาดจาก Ollama",
            f"ไม่สามารถเชื่อมต่อกับ Ollama ได้:\n{error_message}\n\n"
            "คำแนะนำ: ตรวจสอบว่าได้เปิดโปรแกรม Ollama ในเครื่องแล้วหรือไม่"
        )

    def reset_ai_buttons(self):
        self.btn_ai.setEnabled(True)
        self.btn_ai.setText("✨ วิเคราะห์หน้านี้ด้วย AI")
        self.btn_cancel_ai.setEnabled(False)

    # ==========================================================================
    # ระบบดึงเนื้อหาลงด้านล่างอย่างเดียว (ห้ามย้อนขึ้นบน + จบแล้วหยุดทันที)
    # ==========================================================================
    def extract_strictly_downward(self, full_text, query):
        """
        1. หาบรรทัดที่มีคำค้นหา
        2. อ่านเฉพาะเนื้อหาตั้งแต่บรรทัดนั้น 'ไล่ลงล่าง' ไปเรื่อยๆ
        3. หยุดทันทีเมื่อเจอหัวข้อใหม่ (เช่น 1.4, 2.) หรือหมดเนื้อหาหมวดนั้น
        4. ตัดเลขหน้าท้ายกระดาษทิ้ง และไม่วนกลับไปอ่านด้านบนเด็ดขาด
        """
        lines = [l.strip() for l in full_text.splitlines() if l.strip()]
        start_idx = -1

        for idx, line in enumerate(lines):
            if query.lower() in line.lower():
                start_idx = idx
                break

        if start_idx == -1:
            return None

        collected = []
        start_line = lines[start_idx]
        collected.append(start_line)

        # ไล่อ่านเฉพาะบรรทัดที่อยู่ถัดลงไปด้านล่างเท่านั้น
        for line in lines[start_idx + 1:]:
            # ตัดเลขท้ายกระดาษ เช่น "หน้าที่ 1", "Page 1"
            if re.match(r'^(หน้าที่|page)\s*\d+$', line.lower()):
                break

            # ตัดชื่อคำบรรยายภาพ เช่น "รูปที่ 1.1 ..."
            if re.match(r'^(รูปที่|figure)\s*\d+', line.lower()):
                break

            # ถ้าเจอรหัสหัวข้อใหม่ที่ใหญ่กว่า (เช่น ค้นหา 1.3 แล้วมาเจอ 1.4 หรือ 2.) ให้หยุดทันที!
            if re.match(r'^(\d+\.\d+|\d+\.)\s+', line) and not line.startswith(query):
                break

            collected.append(line)

        # คืนค่าเฉพาะเนื้อหาที่เก็บได้
        return " ".join(collected) if collected else None

    def toggle_tts(self):
        if self.tts_worker and self.tts_worker.isRunning():
            self.stop_tts()
            return

        if not self.doc:
            QMessageBox.warning(self, "แจ้งเตือน", "กรุณาเปิดไฟล์ PDF ก่อนใช้งาน")
            return

        page = self.doc.load_page(self.current_page)
        page_text = page.get_text()
        text_to_speak = ""

        # 🎯 โหมดค้นหา: อ่านลงข้างล่างอย่างเดียว 100% (ห้ามวนขึ้นบน ห้ามอ่านทั้งหน้า)
        if self.current_search_query:
            text_to_speak = self.extract_strictly_downward(
                page_text, self.current_search_query
            )
            if not text_to_speak:
                QMessageBox.information(
                    self, "แจ้งเตือน", 
                    f"ไม่พบเนื้อหาของคำว่า '{self.current_search_query}' ในหน้านี้"
                )
                return
        else:
            # 📖 โหมดไม่ได้ค้นหา: อ่านบทสรุปของโน้ต ถ้าไม่มีให้อ่านทั้งหน้า
            note_content = self.txt_notes.toPlainText().strip()
            if note_content:
                text_to_speak = note_content
            else:
                text_to_speak = page_text.strip()

        if not text_to_speak:
            QMessageBox.warning(self, "แจ้งเตือน", "ไม่พบข้อความสำหรับอ่านออกเสียง")
            return

        stop_style = """
            QPushButton {
                background-color: #DC2626;
                color: #ffffff;
                border: none;
                border-radius: 6px;
                font-weight: bold;
                font-size: 13px;
            }
            QPushButton:hover {
                background-color: #B91C1C;
            }
        """
        self.btn_tts_search.setText("⏹️ หยุดอ่านเสียง")
        self.btn_tts_search.setStyleSheet(stop_style)
        self.btn_tts_note.setText("⏹️ หยุดอ่านเสียง")
        self.btn_tts_note.setStyleSheet(stop_style)

        self.tts_worker = TTSWorker(text=text_to_speak)
        self.tts_worker.finished.connect(self.on_tts_finished)
        self.tts_worker.error.connect(self.on_tts_finished)
        self.tts_worker.start()

    def stop_tts(self):
        if self.tts_worker and self.tts_worker.isRunning():
            self.tts_worker.stop()
        self.on_tts_finished()

    def on_tts_finished(self):
        normal_style = """
            QPushButton {
                background-color: #059669;
                color: #ffffff;
                border: none;
                border-radius: 6px;
                font-weight: bold;
                font-size: 13px;
            }
            QPushButton:hover {
                background-color: #047857;
            }
        """
        self.btn_tts_search.setText("🔊 ฟังเนื้อหาตรงคำค้นหา")
        self.btn_tts_search.setStyleSheet(normal_style)
        self.btn_tts_note.setText("🔊 ฟังเสียงอ่าน (TTS)")
        self.btn_tts_note.setStyleSheet(normal_style)


if __name__ == "__main__":
    app = QApplication(sys.argv)
    font = QFont("Helvetica Neue", 10)
    app.setFont(font)

    window = PDFReaderApp()
    window.show()
    sys.exit(app.exec())