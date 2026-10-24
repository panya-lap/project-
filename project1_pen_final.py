"""
================================================================================
Project: Modern PDF Reader & Note Studio 
         (PDF Viewer + Full Search & Highlight + Ollama AI Analysis)
Tool: Python 3, PyQt6, PyMuPDF, Ollama
================================================================================
"""

import sys
import os
import re
import math
from dataclasses import dataclass
import pymupdf as fitz
import ollama

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLabel, QListWidget, QListWidgetItem, QTextEdit,
    QLineEdit, QFileDialog, QSplitter, QScrollArea,
    QTabWidget, QMessageBox, QToolBar, QSpinBox, QMenu, QColorDialog
)
from PyQt6.QtCore import Qt, QSize, QRectF, QPointF, QThread, pyqtSignal
from PyQt6.QtGui import (
    QPixmap, QImage, QIcon, QFont, QAction, QKeySequence, 
    QPainter, QColor, QActionGroup, QPainterPath, QPen, QCursor
)


# ==============================================================================
# เธรดทำงานเบื้องหลังสำหรับ Ollama AI (รองรับการยกเลิกกลางคัน)
# ==============================================================================
class OllamaWorker(QThread):
    finished = pyqtSignal(str)
    error = pyqtSignal(str)

    def __init__(self, text, model="qwen2.5:7b", prompt=None):
        super().__init__()
        self.text = text
        self.prompt = prompt  # ถ้าไม่ส่งมา จะใช้พรอมต์สรุปเนื้อหาแบบเดิม
        self.model = model
        self.is_cancelled = False

    def run(self):
        try:
            prompt = self.prompt or (
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
# ระบบปากกา: โครงสร้างข้อมูล + ตัวช่วยหาข้อความใต้เส้นที่วง + Canvas วาดเส้นทับหน้า PDF
# ==============================================================================
# ===== BEGIN PEN CANVAS (โค้ดส่วนนี้เหมือนกันทั้งแบบแยกเดี่ยวและแบบในโปรเจกต์) =====
HIGHLIGHT_ALPHA = 110     # ความทึบของไฮไลต์ (0-255) ยิ่งน้อยยิ่งโปร่งแสง


@dataclass
class Stroke:
    points: list          # [(x, y), ...] พิกัดบนหน้า PDF (หน่วย pt) ซูมเข้า/ออกแล้วเส้นไม่เพี้ยน
    color: QColor
    width: float          # ความหนา หน่วย pt
    kind: str = "pen"     # "pen" = ปากกา, "highlight" = ไฮไลต์, "circle" = ปากกาวงคำ


def _dist_point_segment(p, a, b):
    ax, ay = a
    bx, by = b
    dx, dy = bx - ax, by - ay
    seg_len2 = dx * dx + dy * dy
    if seg_len2 == 0:
        return math.hypot(p[0] - ax, p[1] - ay)
    t = max(0.0, min(1.0, ((p[0] - ax) * dx + (p[1] - ay) * dy) / seg_len2))
    return math.hypot(p[0] - (ax + t * dx), p[1] - (ay + t * dy))


class PDFCanvas(QLabel):
    """
    แสดงภาพหน้า PDF + วาดเส้นปากกาทับ
    (วาดซ้อนตอน paint จึงไม่ต้องเรนเดอร์ PDF ใหม่ทุกครั้งที่ลากเมาส์)
    """
    strokes_modified = pyqtSignal()           # มีการเพิ่ม/ลบเส้น
    circle_drawn = pyqtSignal(object)         # วาดเส้น 'วงคำ' เสร็จ (ส่ง Stroke ที่วาด)

    def __init__(self, text=""):
        super().__init__(text)
        self.tool = "none"                    # none / pen / highlighter / circle / eraser
        self.pen_color = QColor(220, 38, 38)
        self.pen_width = 2.0
        self.hl_color = QColor(255, 235, 59)
        self.hl_width = 14.0
        self.scale = 1.0                      # จำนวนพิกเซลต่อ 1 pt ของ PDF
        self.strokes = []
        self._live_points = []
        self._drawing = False
        self._last_erase = None
        self.selection_rects = []

    # ---------- การตั้งค่าจากหน้าต่างหลัก ----------
    def set_tool(self, tool):
        self.tool = tool
        self.setCursor(Qt.CursorShape.ArrowCursor if tool == "none" else Qt.CursorShape.CrossCursor)

    def set_pen(self, color, width):
        self.pen_color = QColor(color)
        self.pen_width = float(width)

    def set_highlighter(self, color, width):
        self.hl_color = QColor(color)
        self.hl_width = float(width)

    def set_scale(self, scale):
        self.scale = scale if scale > 0 else 1.0

    def set_strokes(self, strokes):
        self.strokes = strokes                # อ้างอิงลิสต์เดียวกับที่เก็บไว้ในหน้าต่างหลัก
        self.update()

    def set_selection_rects(self, rects):
        self.selection_rects = list(rects)
        self.update()

    def clear_selection(self):
        if self.selection_rects:
            self.selection_rects = []
            self.update()

    # ---------- ตัวช่วยพิกัด ----------
    def _has_page(self):
        pm = self.pixmap()
        return pm is not None and not pm.isNull()

    def _offset(self):
        # QLabel จัดภาพไว้กึ่งกลาง ต้องรู้ระยะเยื้องเพื่อแปลงตำแหน่งเมาส์ให้ตรงกับภาพ
        pm = self.pixmap()
        return QPointF(max(0, (self.width() - pm.width()) // 2),
                       max(0, (self.height() - pm.height()) // 2))

    def _to_pdf(self, pos):
        off = self._offset()
        return ((pos.x() - off.x()) / self.scale, (pos.y() - off.y()) / self.scale)

    # ---------- เมาส์ ----------
    def mousePressEvent(self, event):
        if (event.button() == Qt.MouseButton.LeftButton
                and self.tool != "none" and self._has_page()):
            p = self._to_pdf(event.position())
            if self.tool == "eraser":
                self._last_erase = None
                self._erase_along(p)
            else:
                self._drawing = True
                self._live_points = [p]
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if (self.tool == "none" or not self._has_page()
                or not (event.buttons() & Qt.MouseButton.LeftButton)):
            super().mouseMoveEvent(event)
            return
        p = self._to_pdf(event.position())
        if self.tool == "eraser":
            self._erase_along(p)
        elif self._drawing:
            if self.tool == "highlighter" and (event.modifiers() & Qt.KeyboardModifier.ShiftModifier):
                p = (p[0], self._live_points[0][1])      # ค้าง Shift = ไฮไลต์เป็นเส้นตรงแนวนอน
            lx, ly = self._live_points[-1]
            if math.hypot(p[0] - lx, p[1] - ly) * self.scale >= 1.5:
                self._live_points.append(p)
                self.update()
        event.accept()

    def mouseReleaseEvent(self, event):
        self._last_erase = None
        if self._drawing and event.button() == Qt.MouseButton.LeftButton:
            self._drawing = False
            pts, self._live_points = self._live_points, []
            length = sum(math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(pts, pts[1:]))
            stroke = None
            if len(pts) >= 2 and length * self.scale >= 3:
                if self.tool == "highlighter":
                    stroke = Stroke(pts, QColor(self.hl_color), self.hl_width, "highlight")
                elif self.tool == "circle":
                    stroke = Stroke(pts, QColor(self.pen_color), self.pen_width, "circle")
                else:
                    stroke = Stroke(pts, QColor(self.pen_color), self.pen_width, "pen")
                self.strokes.append(stroke)
                self.strokes_modified.emit()
            self.update()
            event.accept()
            if stroke is not None and stroke.kind == "circle":
                self.circle_drawn.emit(stroke)      # ส่งหลังวาดเสร็จ เพราะหน้าต่างหลักจะเปิดเมนู
            return
        super().mouseReleaseEvent(event)

    def _erase_along(self, p):
        """ลบตลอดแนวที่ลากยางลบ (ลากเร็วก็ไม่ข้ามเส้น)"""
        last = self._last_erase
        pts = [p]
        if last is not None:
            step = 2.0 / self.scale
            n = int(math.hypot(p[0] - last[0], p[1] - last[1]) / step)
            pts = [(last[0] + (p[0] - last[0]) * i / (n + 1),
                    last[1] + (p[1] - last[1]) * i / (n + 1)) for i in range(1, n + 1)] + [p]
        self._last_erase = p
        for q in pts:
            self._erase_at(q)

    def _erase_at(self, p):
        radius = 8.0 / self.scale
        kept = []
        for s in self.strokes:
            reach = radius + s.width / 2
            hit = any(_dist_point_segment(p, a, b) <= reach
                      for a, b in zip(s.points, s.points[1:]))
            if not hit:
                kept.append(s)
        if len(kept) != len(self.strokes):
            self.strokes[:] = kept
            self.strokes_modified.emit()
            self.update()

    # ---------- วาดทับภาพ PDF ----------
    def _draw_points(self, painter, points, color, width_pt, highlight=False):
        if len(points) < 2:
            return
        sc = self.scale
        pts = [QPointF(x * sc, y * sc) for x, y in points]
        path = QPainterPath()
        path.moveTo(pts[0])
        for i in range(1, len(pts) - 1):          # ทำเส้นให้เนียนด้วยจุดกึ่งกลาง
            mid = QPointF((pts[i].x() + pts[i + 1].x()) / 2, (pts[i].y() + pts[i + 1].y()) / 2)
            path.quadTo(pts[i], mid)
        path.lineTo(pts[-1])
        c = QColor(color)
        if highlight:
            c.setAlpha(HIGHLIGHT_ALPHA)           # โปร่งแสง ยังเห็นตัวอักษรด้านล่าง
        cap = Qt.PenCapStyle.FlatCap if highlight else Qt.PenCapStyle.RoundCap
        pen = QPen(c, max(1.0, width_pt * sc), Qt.PenStyle.SolidLine, cap, Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(path)

    def paintEvent(self, event):
        super().paintEvent(event)
        if not self._has_page():
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        off = self._offset()
        painter.translate(off.x(), off.y())
        sc = self.scale
        # กรอบสีฟ้าแสดงข้อความที่ 'วงได้'
        for x0, y0, x1, y1 in self.selection_rects:
            r = QRectF(x0 * sc, y0 * sc, (x1 - x0) * sc, (y1 - y0) * sc)
            painter.fillRect(r, QColor(0, 122, 204, 70))
            painter.setPen(QPen(QColor(0, 122, 204, 200), 1))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(r)
        for s in self.strokes:                    # ไฮไลต์วาดก่อน เส้นปากกาจะอยู่ทับด้านบน
            if s.kind == "highlight":
                self._draw_points(painter, s.points, s.color, s.width, True)
        for s in self.strokes:
            if s.kind != "highlight":
                self._draw_points(painter, s.points, s.color, s.width)
        if self._drawing:
            hl = self.tool == "highlighter"
            self._draw_points(painter, self._live_points,
                              self.hl_color if hl else self.pen_color,
                              self.hl_width if hl else self.pen_width, hl)
        painter.end()
# ===== END PEN CANVAS =====


@dataclass
class TextSelection:
    page: int
    text: str             # ข้อความที่วงได้ (รวมทุกบรรทัด)
    rects: list           # [(x0, y0, x1, y1), ...] กรอบของข้อความต่อบรรทัด


def _point_in_polygon(x, y, poly):
    inside = False
    j = len(poly) - 1
    for i in range(len(poly)):
        xi, yi = poly[i]
        xj, yj = poly[j]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
            inside = not inside
        j = i
    return inside


def _simplify(points, max_points):
    if len(points) <= max_points:
        return list(points)
    step = len(points) / max_points
    out = [points[int(i * step)] for i in range(max_points)]
    out.append(points[-1])
    return out


def find_text_under_stroke(page, points):
    """
    หาข้อความที่อยู่ใต้เส้นที่ผู้ใช้วาด (ระดับ 'ตัวอักษร' จึงใช้กับภาษาไทยที่ไม่มีเว้นวรรคได้)
      - เส้นแบนๆ (สูงน้อยกว่า ~8pt)  -> ถือเป็น 'ขีดเส้นใต้' เลือกตัวอักษรที่อยู่เหนือเส้น
      - เส้นอื่นๆ (วงกลม/วงรี/สี่เหลี่ยม) -> เลือกตัวอักษรที่อยู่ 'ในวง' เป็นส่วนใหญ่
    คืนค่า TextSelection หรือ None ถ้าไม่พบข้อความ
    """
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
    w, h = x1 - x0, y1 - y0
    if w < 4 and h < 4:
        return None

    if h < 8 and w > 6:
        y_mid = sum(ys) / len(ys)

        def hit(b):
            cx, cy = (b[0] + b[2]) / 2, (b[1] + b[3]) / 2
            return x0 - 1 <= cx <= x1 + 1 and cy <= y_mid <= b[3] + 6
    else:
        poly = _simplify(points, 80)

        def hit(b):
            # ตัวอักษรต้องอยู่ 'ในวง' เป็นส่วนใหญ่ (สุ่มตรวจ 9 จุดในกรอบตัวอักษร ต้องอยู่ในวงอย่างน้อย 5 จุด)
            # วงหลวมๆ จึงไม่เก็บตัวอักษรข้างเคียงที่โผล่เข้ามาแค่ปลายวง
            if b[2] < x0 - 1 or b[0] > x1 + 1 or b[3] < y0 - 1 or b[1] > y1 + 1:
                return False
            inside = 0
            for fx in (0.15, 0.5, 0.85):
                for fy in (0.15, 0.5, 0.85):
                    if _point_in_polygon(b[0] + (b[2] - b[0]) * fx, b[1] + (b[3] - b[1]) * fy, poly):
                        inside += 1
            return inside >= 5

    parts, rects = [], []
    for block in page.get_text("rawdict")["blocks"]:
        if block.get("type") != 0:
            continue
        for line in block["lines"]:
            chars = []
            bx0 = by0 = 1e9
            bx1 = by1 = -1e9
            for span in line["spans"]:
                for ch in span["chars"]:
                    b = ch["bbox"]
                    if not hit(b):
                        continue
                    chars.append(ch["c"])
                    if ch["c"].strip():
                        bx0, by0 = min(bx0, b[0]), min(by0, b[1])
                        bx1, by1 = max(bx1, b[2]), max(by1, b[3])
            text = "".join(chars).strip()
            if text:
                parts.append(text)
                rects.append((bx0, by0, bx1, by1))

    if not parts:
        return None

    junk = " \t\n.,;:!?()[]{}\"'“”‘’…"
    full = re.sub(r"\s+", " ", " ".join(parts)).strip(junk)
    if not full:
        return None
    return TextSelection(page=-1, text=full, rects=rects)


# ==============================================================================
# หน้าต่างหลักของโปรแกรม
# ==============================================================================
class PDFReaderApp(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Modern PDF Reader & Note Studio (AI Analysis)")
        self.resize(1200, 800)

        self.doc = None
        self.current_page = 0
        self.total_pages = 0
        self.zoom_factor = 1.25
        self.current_search_query = ""

        self.ai_worker = None

        # --- ระบบปากกา ---
        self.pdf_path = ""
        self.annotations = {}              # {index หน้า: [Stroke, ...]}
        self.annotations_dirty = False
        self.current_tool = "none"
        self.pen_color = QColor(220, 38, 38)
        self.pen_width = 2
        self.hl_color = QColor(255, 235, 59)
        self.hl_width = 14

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
        self.lbl_pdf_page = PDFCanvas("ยังไม่ได้เปิดไฟล์ PDF\nกดที่เมนู 'เปิดไฟล์ PDF' เพื่อเริ่มต้น")
        self.lbl_pdf_page.strokes_modified.connect(self.mark_dirty)
        self.lbl_pdf_page.circle_drawn.connect(self.handle_circle_stroke)
        self.sync_canvas()
        self.lbl_pdf_page.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_pdf_page.setStyleSheet("color: #666; font-size: 14px;")
        self.scroll_area.setWidget(self.lbl_pdf_page)
        self.scroll_area.setWidgetResizable(True)
        center_layout.addWidget(self.scroll_area)

        self.splitter.addWidget(center_panel)

        # 3. แผงขวา: ค้นหาคำ + จดโน้ตและควบคุม AI
        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)

        tabs = self.tabs = QTabWidget()

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

        self.search_results = QListWidget()
        self.search_results.itemClicked.connect(self.on_search_result_clicked)
        st_layout.addWidget(self.search_results)
        tabs.addTab(search_tab, "🔍 ค้นหาคำ")

        # --- แท็บ 2: จดโน้ต + AI สรุปเนื้อหา ---
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

        self.txt_notes = QTextEdit()
        self.txt_notes.setPlaceholderText("จดสรุปเนื้อหา หรือกดปุ่มด้านบนเพื่อให้ AI ช่วยสรุปเนื้อหาหน้านี้...")
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

        self.add_pen_tools(toolbar)

    def open_pdf_dialog(self):
        if not self.maybe_save_annotations():
            return
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
            self.pdf_path = path
            self.annotations = {}
            self.annotations_dirty = False

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
        self.lbl_pdf_page.set_scale(pixmap.width() / page.rect.width)
        self.lbl_pdf_page.set_strokes(self.annotations.setdefault(self.current_page, []))
        self.lbl_pdf_page.clear_selection()

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
    # ระบบปากกา (ปากกา / ไฮไลต์ / วงคำ / ยางลบ)
    # ==========================================================================
    def add_pen_tools(self, toolbar):
        toolbar.addSeparator()

        group = QActionGroup(self)
        group.setExclusive(True)
        self.tool_actions = {}
        for key, text, tip in [
            ("none", "🖐️ เลื่อนดู", "โหมดอ่านปกติ (ไม่วาด)"),
            ("pen", "🖊️ ปากกา", "ลากเพื่อขีดเขียน / ขีดเส้นใต้"),
            ("highlighter", "🖍️ ไฮไลต์", "ลากทับข้อความเพื่อไฮไลต์ (ค้าง Shift = เส้นตรง)"),
            ("circle", "⭕ วงคำ", "วงหรือขีดเส้นใต้คำ แล้วเลือกคำสั่งได้ (ค้นหา / AI อธิบาย / โน้ต)"),
            ("eraser", "🧽 ยางลบ", "ลากทับเส้นที่ต้องการลบ"),
        ]:
            act = QAction(text, self)
            act.setCheckable(True)
            act.setToolTip(tip)
            act.triggered.connect(lambda checked, k=key: self.set_tool(k))
            group.addAction(act)
            toolbar.addAction(act)
            self.tool_actions[key] = act
        self.tool_actions["none"].setChecked(True)

        self.act_color = QAction("🎨 สี", self)
        self.act_color.setToolTip("เลือกสีของเครื่องมือที่ใช้อยู่ (ปากกากับไฮไลต์แยกสีกัน)")
        self.act_color.triggered.connect(self.choose_pen_color)
        toolbar.addAction(self.act_color)
        self.update_color_icon()

        self.spin_width = QSpinBox()
        self.spin_width.setRange(1, 40)
        self.spin_width.setValue(int(self.pen_width))
        self.spin_width.setPrefix("หนา ")
        self.spin_width.setSuffix(" pt")
        self.spin_width.valueChanged.connect(self.set_pen_width)
        toolbar.addWidget(self.spin_width)

        act_undo = QAction("↩️ ย้อน", self)
        act_undo.setToolTip("ย้อนเส้นล่าสุด (Ctrl+Z)")
        act_undo.setShortcut(QKeySequence("Ctrl+Z"))
        act_undo.triggered.connect(self.undo_stroke)
        toolbar.addAction(act_undo)

        act_clear = QAction("🗑️ ล้างหน้า", self)
        act_clear.setToolTip("ลบลายเส้นทั้งหมดในหน้านี้")
        act_clear.triggered.connect(self.clear_page_strokes)
        toolbar.addAction(act_clear)

        act_save = QAction("💾 บันทึก", self)
        act_save.setShortcut(QKeySequence("Ctrl+S"))
        act_save.setToolTip("บันทึกเป็นไฟล์ PDF ใหม่ พร้อมลายเส้นปากกา (ไม่ทับไฟล์ต้นฉบับ)")
        act_save.triggered.connect(self.save_annotated_pdf)
        toolbar.addAction(act_save)

    def sync_canvas(self):
        canvas = getattr(self, "lbl_pdf_page", None)
        if canvas is None:
            return
        canvas.set_tool(self.current_tool)
        canvas.set_pen(self.pen_color, self.pen_width)
        canvas.set_highlighter(self.hl_color, self.hl_width)

    def set_tool(self, tool):
        self.current_tool = tool
        if tool in self.tool_actions:
            self.tool_actions[tool].setChecked(True)
        self.sync_canvas()
        self.refresh_tool_controls()
        hints = {
            "none": "โหมดเลื่อนดู",
            "pen": "ปากกา: ลากบนหน้าเอกสารเพื่อขีดเขียน",
            "highlighter": "ไฮไลต์: ลากทับข้อความ (ค้าง Shift = ลากเป็นเส้นตรงแนวนอน)",
            "circle": "ปากกาวงคำ: วงรอบคำ หรือขีดเส้นใต้คำ แล้วเลือกคำสั่ง",
            "eraser": "ยางลบ: ลากทับเส้นที่ต้องการลบ",
        }
        self.statusBar().showMessage(hints.get(tool, ""), 5000)

    def refresh_tool_controls(self):
        """ช่องความหนา/ไอคอนสี แสดงค่าของเครื่องมือที่เลือกอยู่ (ปากกากับไฮไลต์แยกกัน)"""
        hl = self.current_tool == "highlighter"
        self.spin_width.blockSignals(True)
        self.spin_width.setValue(int(self.hl_width if hl else self.pen_width))
        self.spin_width.blockSignals(False)
        self.update_color_icon()

    def choose_pen_color(self):
        hl = self.current_tool == "highlighter"
        color = QColorDialog.getColor(self.hl_color if hl else self.pen_color, self,
                                      "เลือกสีไฮไลต์" if hl else "เลือกสีปากกา")
        if color.isValid():
            if hl:
                self.hl_color = color
            else:
                self.pen_color = color
            self.update_color_icon()
            self.sync_canvas()

    def update_color_icon(self):
        pix = QPixmap(16, 16)
        pix.fill(self.hl_color if self.current_tool == "highlighter" else self.pen_color)
        self.act_color.setIcon(QIcon(pix))

    def set_pen_width(self, value):
        if self.current_tool == "highlighter":
            self.hl_width = value
        else:
            self.pen_width = value
        self.sync_canvas()

    def mark_dirty(self):
        self.annotations_dirty = True

    def has_annotations(self):
        return any(self.annotations.values())

    def undo_stroke(self):
        strokes = self.annotations.get(self.current_page)
        if strokes:
            strokes.pop()
            self.annotations_dirty = True
            self.lbl_pdf_page.update()

    def clear_page_strokes(self):
        strokes = self.annotations.get(self.current_page)
        if not strokes:
            return
        reply = QMessageBox.question(
            self, "ล้างลายเส้น", f"ลบลายเส้นทั้งหมดในหน้า {self.current_page + 1} ใช่หรือไม่?"
        )
        if reply == QMessageBox.StandardButton.Yes:
            strokes.clear()
            self.annotations_dirty = True
            self.lbl_pdf_page.update()

    # ---------- ปากกาวงคำ: เปลี่ยนเส้นที่วงเป็น "คำค้นหา" ----------
    def handle_circle_stroke(self, stroke):
        if not self.doc:
            return
        page = self.doc.load_page(self.current_page)
        sel = find_text_under_stroke(page, stroke.points)
        if sel is None:
            self.statusBar().showMessage(
                "ไม่พบข้อความใต้เส้นที่วง (ลองวงให้คลุมตัวอักษร หรือถ้าเป็นเอกสารสแกนจะยังไม่รองรับ)", 6000
            )
            return
        sel.page = self.current_page
        self.lbl_pdf_page.set_selection_rects(sel.rects)
        self.show_selection_menu(sel, stroke)
        self.lbl_pdf_page.clear_selection()

    def show_selection_menu(self, sel, stroke):
        short = sel.text if len(sel.text) <= 40 else sel.text[:40] + "…"
        menu = QMenu(self)
        header = menu.addAction(f"“{short}”")
        header.setEnabled(False)
        menu.addSeparator()
        act_search = menu.addAction("🔍 ค้นหาคำนี้ทั้งเอกสาร")
        act_ai = menu.addAction("✨ ให้ AI อธิบายคำนี้")
        act_note = menu.addAction("📝 เก็บคำนี้ลงโน้ต")
        menu.addSeparator()
        act_del = menu.addAction("🗑️ ลบเส้นที่วงนี้")

        chosen = menu.exec(QCursor.pos())
        if chosen is None:
            return
        if chosen == act_search:
            self.search_from_selection(sel)
        elif chosen == act_ai:
            self.explain_selection_with_ai(sel)
        elif chosen == act_note:
            self.txt_notes.append(f"\n📌 [หน้า {sel.page + 1}] “{sel.text}”")
            self.statusBar().showMessage("เพิ่มลงโน้ตแล้ว", 3000)
        elif chosen == act_del:
            strokes = self.annotations.get(sel.page, [])
            strokes[:] = [s for s in strokes if s is not stroke]
            self.annotations_dirty = True
            self.lbl_pdf_page.update()

    def search_from_selection(self, sel):
        """ใช้ระบบค้นหาเดิมเลย: ไฮไลต์เหลืองทุกจุด + รายการหน้าที่พบ"""
        origin = self.current_page
        self.tabs.setCurrentIndex(0)
        self.txt_search.setText(sel.text)
        self.search_text()
        # ระบบค้นหาเดิมจะกระโดดไปหน้าแรกที่พบ ถ้าหน้าที่วงมีคำนี้ด้วย ให้อยู่หน้าเดิม
        if self.current_page != origin and self.doc.load_page(origin).search_for(sel.text):
            self.go_to_page(origin)

    def explain_selection_with_ai(self, sel):
        if self.ai_worker and self.ai_worker.isRunning():
            QMessageBox.information(self, "แจ้งเตือน", "AI กำลังประมวลผลงานก่อนหน้าอยู่ กรุณารอให้เสร็จก่อน")
            return

        page_text = self.doc.load_page(sel.page).get_text().strip()
        prompt = (
            "คุณคือผู้ช่วยอธิบายเนื้อหาในเอกสาร "
            "โปรดอธิบายความหมายของคำหรือข้อความที่ผู้ใช้วงไว้ต่อไปนี้เป็นภาษาไทย "
            "ให้เข้าใจง่ายและกระชับ โดยอ้างอิงบริบทจากหน้าเอกสารที่ให้มา\n\n"
            f"ข้อความที่วงไว้: {sel.text}\n\n"
            f"บริบทจากหน้าเอกสาร:\n{page_text[:3000]}"
        )

        self.tabs.setCurrentIndex(1)
        self.btn_ai.setEnabled(False)
        self.btn_ai.setText("⏳ กำลังวิเคราะห์...")
        self.btn_cancel_ai.setEnabled(True)
        self.txt_notes.append(f"\n--- 🤖 [AI อธิบายคำว่า “{sel.text[:40]}” หน้า {sel.page + 1}] ---")
        self.txt_notes.append("กำลังประมวลผล กรุณารอสักครู่ (กด 'ยกเลิก' ได้)...\n")

        self.ai_worker = OllamaWorker(text=sel.text, model="qwen2.5:7b", prompt=prompt)
        self.ai_worker.finished.connect(self.on_ai_success)
        self.ai_worker.error.connect(self.on_ai_failed)
        self.ai_worker.start()

    # ---------- บันทึก / ปิดโปรแกรม ----------
    def save_annotated_pdf(self):
        """บันทึกเป็น PDF ใหม่ พร้อมเส้นปากกา (ไม่แตะไฟล์ต้นฉบับ). คืน True ถ้าบันทึกสำเร็จ"""
        if not self.doc:
            QMessageBox.warning(self, "แจ้งเตือน", "กรุณาเปิดไฟล์ PDF ก่อน")
            return False
        if not self.has_annotations():
            QMessageBox.information(self, "แจ้งเตือน", "ยังไม่มีลายเส้นปากกาให้บันทึก")
            return False

        default = os.path.splitext(self.pdf_path)[0] + "_annotated.pdf" if self.pdf_path else ""
        path, _ = QFileDialog.getSaveFileName(self, "บันทึก PDF พร้อมลายเส้น", default, "PDF Files (*.pdf)")
        if not path:
            return False
        if not path.lower().endswith(".pdf"):
            path += ".pdf"
        if os.path.abspath(path) == os.path.abspath(self.pdf_path or ""):
            QMessageBox.warning(self, "แจ้งเตือน", "ไม่สามารถบันทึกทับไฟล์ที่เปิดอยู่ได้ กรุณาตั้งชื่อใหม่")
            return False

        try:
            out = fitz.open()
            out.insert_pdf(self.doc)
            for pno, strokes in self.annotations.items():
                if not strokes:
                    continue
                page = out[pno]
                # ไฮไลต์ลงก่อน เส้นปากกาจะได้อยู่ทับด้านบน
                for st in sorted(strokes, key=lambda x: x.kind != "highlight"):
                    pts = []
                    for x, y in st.points:
                        q = fitz.Point(x, y) * page.derotation_matrix   # รองรับหน้าที่ถูกหมุน
                        pts.append((q.x, q.y))
                    annot = page.add_ink_annot([pts])
                    annot.set_colors(stroke=(st.color.redF(), st.color.greenF(), st.color.blueF()))
                    annot.set_border(width=st.width)
                    if st.kind == "highlight":
                        annot.set_opacity(HIGHLIGHT_ALPHA / 255)
                    annot.update()
            out.save(path, garbage=3, deflate=True)
            out.close()
        except Exception as e:
            QMessageBox.critical(self, "เกิดข้อผิดพลาด", f"บันทึกไฟล์ไม่สำเร็จ:\n{str(e)}")
            return False

        self.annotations_dirty = False
        self.statusBar().showMessage(f"บันทึกแล้ว: {path}", 6000)
        return True

    def maybe_save_annotations(self):
        """ถามก่อนทิ้งลายเส้นที่ยังไม่บันทึก คืน True = ไปต่อได้, False = ยกเลิก"""
        if not (self.annotations_dirty and self.has_annotations()):
            return True
        btn = QMessageBox.StandardButton
        reply = QMessageBox.question(
            self, "ลายเส้นยังไม่ได้บันทึก",
            "มีลายเส้นปากกาที่ยังไม่ได้บันทึกลง PDF ต้องการบันทึกก่อนหรือไม่?",
            btn.Save | btn.Discard | btn.Cancel, btn.Save
        )
        if reply == btn.Save:
            return self.save_annotated_pdf()
        return reply == btn.Discard

    def closeEvent(self, event):
        if not self.maybe_save_annotations():
            event.ignore()
            return
        event.accept()


if __name__ == "__main__":
    app = QApplication(sys.argv)
    font = QFont("Helvetica Neue", 10)
    app.setFont(font)

    window = PDFReaderApp()
    window.show()
    sys.exit(app.exec())
