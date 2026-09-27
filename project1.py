"""
================================================================================
Project: Modern PDF Reader & Note Studio (พร้อมระบบ Highlight คำค้นหา)
Tool: Python 3, PyQt6, PyMuPDF (fitz)
================================================================================
"""

import sys
import os
import fitz  

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLabel, QListWidget, QListWidgetItem, QTextEdit,
    QLineEdit, QFileDialog, QSplitter, QGroupBox, QScrollArea,
    QTabWidget, QMessageBox, QToolBar, QSpinBox
)
from PyQt6.QtCore import Qt, QSize, QRectF
from PyQt6.QtGui import (
    QPixmap, QImage, QIcon, QFont, QAction, QKeySequence, 
    QPainter, QColor
)


class PDFReaderApp(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Modern PDF Reader & Workspace")
        self.resize(1200, 800)

        # ตัวแปรจัดการเอกสาร
        self.doc = None
        self.current_page = 0
        self.total_pages = 0
        self.zoom_factor = 1.25

        # ตัวแปรเก็บคำค้นหาปัจจุบันสำหรับทำ Highlight
        self.current_search_query = ""

        self.init_ui()

    def init_ui(self):
        self.create_toolbar()

        main_widget = QWidget()
        self.setCentralWidget(main_widget)
        main_layout = QHBoxLayout(main_widget)
        main_layout.setContentsMargins(5, 5, 5, 5)

        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        main_layout.addWidget(self.splitter)

        # ----------------------------------------------------------------------
        # 1. แผงซ้าย: Thumbnails
        # ----------------------------------------------------------------------
        left_panel = QWidget()
        left_layout = QVBoxLayout(left_panel)
        left_layout.addWidget(QLabel("📑 หน้าเอกสาร (Pages)"))

        self.thumbnail_list = QListWidget()
        self.thumbnail_list.setIconSize(QSize(100, 140))
        self.thumbnail_list.currentRowChanged.connect(self.go_to_page)
        left_layout.addWidget(self.thumbnail_list)
        self.splitter.addWidget(left_panel)

        # ----------------------------------------------------------------------
        # 2. แผงกลาง: หน้าอ่าน PDF
        # ----------------------------------------------------------------------
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

        # ----------------------------------------------------------------------
        # 3. แผงขวา: มี 2 แท็บ (🔍 ค้นหาคำ และ 📝 โน้ตสรุป)
        # ----------------------------------------------------------------------
        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)

        tabs = QTabWidget()

        # --- แท็บ 1: ค้นหาคำ (ขนาดเท่ากันเป๊ะ) ---
        search_tab = QWidget()
        st_layout = QVBoxLayout(search_tab)
        
        search_bar = QHBoxLayout()
        search_bar.setSpacing(6)
        search_bar.setContentsMargins(0, 0, 0, 0)

        # ช่องพิมพ์ค้นหา
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

        # ปุ่มค้นหา
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

        # ปุ่มล้างไฮไลต์ (✖) จัตุรัส 38x38 px
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

        self.search_results = QListWidget()
        self.search_results.itemClicked.connect(self.on_search_result_clicked)
        st_layout.addWidget(self.search_results)
        tabs.addTab(search_tab, "🔍 ค้นหาคำ")

        # --- แท็บ 2: จดโน้ต (ลบปุ่มบันทึกลงไฟล์ออกแล้ว) ---
        note_tab = QWidget()
        nt_layout = QVBoxLayout(note_tab)
        self.txt_notes = QTextEdit()
        self.txt_notes.setPlaceholderText("จดสรุปเนื้อหาสำคัญที่นี่...")
        self.txt_notes.setStyleSheet("font-size: 13px;")
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
        """เรนเดอร์หน้า PDF และวาด Highlight ทับถ้ามีคำค้นหา"""
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
        self.render_page()

    def on_search_result_clicked(self, item):
        page_num = item.data(Qt.ItemDataRole.UserRole)
        if page_num is not None:
            self.go_to_page(page_num)


if __name__ == "__main__":
    app = QApplication(sys.argv)
    font = QFont("Helvetica Neue", 10)
    app.setFont(font)

    window = PDFReaderApp()
    window.show()
    sys.exit(app.exec())