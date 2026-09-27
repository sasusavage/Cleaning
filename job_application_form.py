"""
Done-Well Cleaning — Employee/Worker Application Form.

Single source of truth for both:
  - the blank, downloadable fillable PDF (AcroForm widgets, mode='interactive')
  - the completed-application PDF generated from an online submission
    (static answer text + checkmarks + embedded signature, mode='filled')

Run directly to (re)generate the blank downloadable PDF:
    python3 job_application_form.py
"""
import io
import re

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.colors import HexColor
from reportlab.pdfgen import canvas
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.lib.utils import ImageReader

PAGE_W, PAGE_H = A4
MARGIN_L = 20 * mm
MARGIN_R = 20 * mm
MARGIN_TOP = 18 * mm
MARGIN_BOTTOM = 18 * mm
CONTENT_W = PAGE_W - MARGIN_L - MARGIN_R

NAVY = HexColor('#1a2b4a')
BLUE = HexColor('#1a6fce')
GREEN = HexColor('#15803d')
GREY = HexColor('#4b5563')
LINE = HexColor('#cbd5e1')
BOX_BG = HexColor('#f8fafc')
BLACK = HexColor('#000000')
DARK = HexColor('#111827')

FONT = 'Helvetica'
FONT_B = 'Helvetica-Bold'
FONT_I = 'Helvetica-Oblique'
FONT_BI = 'Helvetica-BoldOblique'


def slugify(text):
    s = re.sub(r'[^a-z0-9]+', '_', text.strip().lower()).strip('_')
    return s or 'field'


class FormBuilder:
    """mode='interactive' draws empty AcroForm widgets (the blank downloadable form).
    mode='filled' draws the submitted answers as static text/checkmarks/signature image."""

    def __init__(self, out, title, subtitle, mode='interactive', answers=None, signature_bytes=None):
        assert mode in ('interactive', 'filled')
        self.c = canvas.Canvas(out, pagesize=A4)
        self.form = self.c.acroForm
        self.mode = mode
        self.answers = answers or {}
        self.signature_bytes = signature_bytes
        self.y = PAGE_H - MARGIN_TOP
        self.page_no = 1
        self.title = title
        self.subtitle = subtitle
        self.MARGIN_L = MARGIN_L
        self.CONTENT_W = CONTENT_W
        self._used_names = set()
        self._draw_header(first=True)

    # ---------- low level helpers ----------
    def _draw_footer(self):
        self.c.setFont(FONT, 7.5)
        self.c.setFillColor(GREY)
        footer_label = 'Completed Application (copy for your records)' if self.mode == 'filled' else 'Employee / Worker Application Form (Private and Confidential)'
        self.c.drawString(MARGIN_L, 10 * mm, f'Done-Well Cleaning Limited — {footer_label}')
        self.c.drawRightString(PAGE_W - MARGIN_R, 10 * mm, f'Page {self.page_no}')
        self.c.setFillColor(BLACK)

    def _draw_header(self, first=False):
        if first:
            self.c.setFillColor(NAVY)
            self.c.setFont(FONT_B, 17)
            self.c.drawString(MARGIN_L, self.y, self.title)
            self.y -= 9 * mm
            self.c.setFont(FONT, 10.5)
            self.c.setFillColor(GREY)
            for line in self.subtitle:
                self.c.drawString(MARGIN_L, self.y, line)
                self.y -= 5 * mm
            self.y -= 2 * mm
            self.c.setStrokeColor(LINE)
            self.c.line(MARGIN_L, self.y, PAGE_W - MARGIN_R, self.y)
            self.y -= 8 * mm
            self.c.setFillColor(BLACK)

    def new_page(self):
        self._draw_footer()
        self.c.showPage()
        self.page_no += 1
        self.y = PAGE_H - MARGIN_TOP
        self.c.setFont(FONT, 9)
        self.c.setFillColor(GREY)
        self.c.drawString(MARGIN_L, self.y, self.title)
        self.y -= 8 * mm
        self.c.setStrokeColor(LINE)
        self.c.line(MARGIN_L, self.y, PAGE_W - MARGIN_R, self.y)
        self.y -= 6 * mm
        self.c.setFillColor(BLACK)

    def ensure_space(self, needed):
        if self.y - needed < MARGIN_BOTTOM:
            self.new_page()

    def _name(self, base):
        assert base not in self._used_names, f'duplicate field name: {base}'
        self._used_names.add(base)
        return base

    def _wrap(self, text, font, size, max_w):
        words = text.split(' ')
        lines, cur = [], ''
        for w in words:
            trial = (cur + ' ' + w).strip()
            if stringWidth(trial, font, size) <= max_w:
                cur = trial
            else:
                if cur:
                    lines.append(cur)
                cur = w
        if cur:
            lines.append(cur)
        return lines or ['']

    def _answer_text(self, field_name, deferred_note=None):
        val = self.answers.get(field_name)
        val = str(val).strip() if val is not None else ''
        if val:
            return val, False
        if deferred_note:
            return deferred_note, True
        return '—', True

    # ---------- content elements ----------
    def section(self, number, title):
        self.ensure_space(14 * mm)
        self.y -= 2 * mm
        self.c.setFillColor(BLUE)
        self.c.setFont(FONT_B, 13)
        self.c.drawString(MARGIN_L, self.y, f'{number}. {title}')
        self.y -= 4 * mm
        self.c.setStrokeColor(BLUE)
        self.c.setLineWidth(1.2)
        self.c.line(MARGIN_L, self.y, MARGIN_L + 38 * mm, self.y)
        self.c.setLineWidth(1)
        self.y -= 7 * mm
        self.c.setFillColor(BLACK)

    def subheading(self, text):
        self.ensure_space(8 * mm)
        self.c.setFont(FONT_B, 10)
        self.c.setFillColor(DARK)
        self.c.drawString(MARGIN_L, self.y, text)
        self.y -= 6 * mm
        self.c.setFillColor(BLACK)

    def paragraph(self, text, size=9, color=GREY, italic=False, gap=None):
        font = FONT_I if italic else FONT
        gap = gap if gap is not None else size * 1.35
        lines = self._wrap(text, font, size, CONTENT_W)
        self.ensure_space(len(lines) * gap + 2)
        self.c.setFont(font, size)
        self.c.setFillColor(color)
        for line in lines:
            self.c.drawString(MARGIN_L, self.y, line)
            self.y -= gap
        self.y -= 1.5 * mm
        self.c.setFillColor(BLACK)

    def bullet_list(self, items, size=9):
        line_h = size * 1.35
        for it in items:
            lines = self._wrap(it, FONT, size, CONTENT_W - 5 * mm)
            self.ensure_space(len(lines) * line_h + 1)
            self.c.setFont(FONT, size)
            self.c.setFillColor(GREY)
            self.c.drawString(MARGIN_L, self.y, u'•')
            for line in lines:
                self.c.drawString(MARGIN_L + 4 * mm, self.y, line)
                self.y -= line_h
            self.y -= 0.5 * mm
        self.c.setFillColor(BLACK)

    def _answer_block(self, field_name, width, deferred_note=None, size=9.5):
        """Draws wrapped answer text + a bottom rule. Returns nothing; advances self.y."""
        text, is_placeholder = self._answer_text(field_name, deferred_note)
        font = FONT_I if is_placeholder else FONT
        color = HexColor('#9ca3af') if is_placeholder else DARK
        lines = self._wrap(text, font, size, width - 2 * mm)
        self.c.setFont(font, size)
        self.c.setFillColor(color)
        line_h = size * 1.3
        for line in lines:
            self.c.drawString(self._cur_x, self.y, line)
            self.y -= line_h
        self.c.setStrokeColor(LINE)
        self.c.line(self._cur_x, self.y - 0.5 * mm, self._cur_x + width, self.y - 0.5 * mm)
        self.y -= 3.5 * mm
        self.c.setFillColor(BLACK)

    def text_field(self, label, field_name, width=None, height=7 * mm, multiline=False, note=None, deferred_note=None):
        width = width or CONTENT_W
        self._name(field_name)
        needed = height + 9 * mm + (4 * mm if note else 0)
        self.ensure_space(needed if self.mode == 'interactive' else 16 * mm)
        self.c.setFont(FONT_B, 9)
        self.c.setFillColor(DARK)
        self.c.drawString(MARGIN_L, self.y, label)
        self.y -= 5 * mm
        if note:
            self.c.setFont(FONT_I, 7.5)
            self.c.setFillColor(GREY)
            for line in self._wrap(note, FONT_I, 7.5, CONTENT_W):
                self.c.drawString(MARGIN_L, self.y, line)
                self.y -= 3.6 * mm
            self.y -= 0.5 * mm

        if self.mode == 'interactive':
            self.form.textfield(
                name=field_name, tooltip=label,
                x=MARGIN_L, y=self.y - height, width=width, height=height,
                borderStyle='underlined', borderColor=LINE, fillColor=BOX_BG,
                textColor=DARK, fontSize=9, forceBorder=True,
                fieldFlags='multiline' if multiline else '',
            )
            self.y -= height + 4 * mm
        else:
            self._cur_x = MARGIN_L
            self._answer_block(field_name, width, deferred_note=deferred_note)
        self.c.setFillColor(BLACK)

    def field_row(self, labels_bases, col_gap=6 * mm, height=7 * mm):
        """Two or three fields side by side: each tuple is (label, field_name, weight?)."""
        n = len(labels_bases)
        total_gap = col_gap * (n - 1)
        self.ensure_space(height + 9 * mm if self.mode == 'interactive' else 16 * mm)
        weights = [lb[2] if len(lb) > 2 else 1 for lb in labels_bases]
        wsum = sum(weights)
        avail = CONTENT_W - total_gap
        x = MARGIN_L
        self.c.setFont(FONT_B, 9)
        self.c.setFillColor(DARK)
        widths, xs = [], []
        for (label, name, *_rest), w in zip(labels_bases, weights):
            self._name(name)
            cw = avail * (w / wsum)
            widths.append(cw)
            xs.append(x)
            self.c.drawString(x, self.y, label)
            x += cw + col_gap
        self.y -= 5 * mm
        row_top_y = self.y
        max_drop = 0
        for (label, name, *_rest), cw, cx in zip(labels_bases, widths, xs):
            if self.mode == 'interactive':
                self.form.textfield(
                    name=name, tooltip=label,
                    x=cx, y=self.y - height, width=cw, height=height,
                    borderStyle='underlined', borderColor=LINE, fillColor=BOX_BG,
                    textColor=DARK, fontSize=9, forceBorder=True,
                )
            else:
                self.y = row_top_y
                self._cur_x = cx
                self._answer_block(name, cw)
                max_drop = max(max_drop, row_top_y - self.y)
        if self.mode == 'interactive':
            self.y -= height + 4 * mm
        else:
            self.y = row_top_y - max_drop
        self.c.setFillColor(BLACK)

    def checkbox_group(self, question, options, field_base, size=8, other=False, deferred_note=None):
        """options: list of label strings. Field name per option = '{field_base}__{slug(option)}'."""
        self.ensure_space(12 * mm)
        self.c.setFont(FONT_B, 9)
        self.c.setFillColor(DARK)
        for line in self._wrap(question, FONT_B, 9, CONTENT_W):
            self.c.drawString(MARGIN_L, self.y, line)
            self.y -= 5 * mm
        self.y -= 1 * mm

        box = 3.6 * mm
        gap_after_box = 2.2 * mm
        col_gap = 8 * mm
        row_gap = 8 * mm
        x = MARGIN_L
        self.c.setFont(FONT, size + 0.5)
        any_checked = False
        for opt in options:
            name = self._name(f'{field_base}__{slugify(opt)}')
            w = stringWidth(opt, FONT, size + 0.5) + box + gap_after_box + col_gap
            if x + w - col_gap > MARGIN_L + CONTENT_W and x != MARGIN_L:
                x = MARGIN_L
                self.y -= row_gap
                self.ensure_space(row_gap)
            checked = bool(self.answers.get(name)) if self.mode == 'filled' else False
            any_checked = any_checked or checked
            self._draw_checkbox(name, x, self.y - box + 0.5 * mm, box, opt, checked)
            self.c.setFillColor(DARK)
            self.c.drawString(x + box + gap_after_box, self.y - box + 1.3 * mm, opt)
            x += w
        self.y -= row_gap
        if other:
            other_name = self._name(f'{field_base}__other_text')
            self.c.setFont(FONT_I, 8)
            self.c.setFillColor(GREY)
            self.c.drawString(MARGIN_L, self.y, 'If Other, please specify:')
            self.y -= 4.5 * mm
            if self.mode == 'interactive':
                self.form.textfield(
                    name=other_name, tooltip='Other — please specify',
                    x=MARGIN_L, y=self.y - 6.5 * mm, width=CONTENT_W, height=6.5 * mm,
                    borderStyle='underlined', borderColor=LINE, fillColor=BOX_BG,
                    textColor=DARK, fontSize=9, forceBorder=True,
                )
                self.y -= 6.5 * mm + 4 * mm
            else:
                self._cur_x = MARGIN_L
                self._answer_block(other_name, CONTENT_W)
        elif self.mode == 'filled' and not any_checked and deferred_note:
            self.c.setFont(FONT_I, 8)
            self.c.setFillColor(HexColor('#9ca3af'))
            self.c.drawString(MARGIN_L, self.y, deferred_note)
            self.y -= 5 * mm
        else:
            self.y -= 1 * mm
        self.c.setFillColor(BLACK)

    def _draw_checkbox(self, tooltip_name, x, y, box, tooltip, checked):
        if self.mode == 'interactive':
            self.form.checkbox(
                name=tooltip_name, tooltip=tooltip,
                x=x, y=y, size=box,
                borderColor=HexColor('#64748b'), fillColor=BOX_BG,
                buttonStyle='check', borderWidth=1,
            )
        else:
            self.c.setStrokeColor(HexColor('#64748b'))
            self.c.setLineWidth(1)
            if checked:
                self.c.setFillColor(GREEN)
                self.c.rect(x, y, box, box, fill=1, stroke=1)
                self.c.setStrokeColor(HexColor('#ffffff'))
                self.c.setLineWidth(1.1)
                self.c.line(x + box * 0.2, y + box * 0.5, x + box * 0.42, y + box * 0.25)
                self.c.line(x + box * 0.42, y + box * 0.25, x + box * 0.82, y + box * 0.78)
            else:
                self.c.setFillColor(HexColor('#ffffff'))
                self.c.rect(x, y, box, box, fill=1, stroke=1)
            self.c.setFillColor(BLACK)

    def checkbox_grid(self, row_labels, col_labels, field_base, row_h=8 * mm, header_h=7 * mm, label_w=32 * mm):
        total_h = header_h + len(row_labels) * row_h
        self.ensure_space(total_h + 4 * mm)
        top_y = self.y
        grid_w = CONTENT_W - label_w
        col_w = grid_w / len(col_labels)

        self.c.setFillColor(NAVY)
        self.c.rect(MARGIN_L, top_y - header_h, CONTENT_W, header_h, fill=1, stroke=0)
        self.c.setFillColor(HexColor('#ffffff'))
        self.c.setFont(FONT_B, 8.5)
        for i, cl in enumerate(col_labels):
            cx = MARGIN_L + label_w + i * col_w
            self.c.drawCentredString(cx + col_w / 2, top_y - header_h + 2.4 * mm, cl)

        y_cursor = top_y - header_h
        for ri, rl in enumerate(row_labels):
            if ri % 2 == 1:
                self.c.setFillColor(BOX_BG)
                self.c.rect(MARGIN_L, y_cursor - row_h, CONTENT_W, row_h, fill=1, stroke=0)
            self.c.setFillColor(DARK)
            self.c.setFont(FONT, 8.5)
            self.c.drawString(MARGIN_L + 2 * mm, y_cursor - row_h / 2 - 1 * mm, rl)
            for ci, cl in enumerate(col_labels):
                cx = MARGIN_L + label_w + ci * col_w + col_w / 2
                box = 3.6 * mm
                name = self._name(f'{field_base}__{slugify(rl)}__{slugify(cl)}')
                checked = bool(self.answers.get(name)) if self.mode == 'filled' else False
                self._draw_checkbox(name, cx - box / 2, y_cursor - row_h / 2 - box / 2, box, f'{rl} — {cl}', checked)
            y_cursor -= row_h

        self.c.setStrokeColor(LINE)
        self.c.rect(MARGIN_L, y_cursor, CONTENT_W, top_y - y_cursor, fill=0, stroke=1)
        self.c.line(MARGIN_L + label_w, top_y, MARGIN_L + label_w, y_cursor)
        for i in range(1, len(col_labels)):
            cx = MARGIN_L + label_w + i * col_w
            self.c.line(cx, top_y, cx, y_cursor)
        yy = top_y - header_h
        for _ in row_labels[:-1]:
            yy -= row_h
            self.c.line(MARGIN_L, yy, MARGIN_L + CONTENT_W, yy)

        self.y = y_cursor - 4 * mm
        self.c.setFillColor(BLACK)

    def table(self, headers, n_rows, field_base, col_weights=None, row_h=8 * mm, header_h=7 * mm):
        col_weights = col_weights or [1] * len(headers)
        wsum = sum(col_weights)
        col_widths = [CONTENT_W * (w / wsum) for w in col_weights]
        total_h = header_h + n_rows * row_h
        self.ensure_space(total_h + 4 * mm)
        top_y = self.y
        x = MARGIN_L
        self.c.setFillColor(NAVY)
        self.c.rect(MARGIN_L, top_y - header_h, CONTENT_W, header_h, fill=1, stroke=0)
        self.c.setFillColor(HexColor('#ffffff'))
        self.c.setFont(FONT_B, 8.5)
        for h, w in zip(headers, col_widths):
            self.c.drawString(x + 2 * mm, top_y - header_h + 2.4 * mm, h)
            x += w
        y_cursor = top_y - header_h
        for r in range(n_rows):
            x = MARGIN_L
            if r % 2 == 1:
                self.c.setFillColor(BOX_BG)
                self.c.rect(MARGIN_L, y_cursor - row_h, CONTENT_W, row_h, fill=1, stroke=0)
            for ci, w in enumerate(col_widths):
                name = self._name(f'{field_base}_r{r}_c{ci}')
                if self.mode == 'interactive':
                    self.form.textfield(
                        name=name, tooltip=headers[ci],
                        x=x + 1.5 * mm, y=y_cursor - row_h + 1.2 * mm,
                        width=w - 3 * mm, height=row_h - 2.4 * mm,
                        borderStyle='underlined', borderColor=LINE, fillColor=HexColor('#ffffff'),
                        textColor=DARK, fontSize=8.5, forceBorder=True,
                    )
                else:
                    val = str(self.answers.get(name) or '').strip()
                    if val:
                        self.c.setFont(FONT, 8.5)
                        self.c.setFillColor(DARK)
                        max_w = w - 3 * mm
                        line = val
                        while stringWidth(line, FONT, 8.5) > max_w and len(line) > 3:
                            line = line[:-2]
                        if line != val:
                            line = line[:-1] + '…'
                        self.c.drawString(x + 1.5 * mm, y_cursor - row_h + 2.6 * mm, line)
                        self.c.setFillColor(BLACK)
                x += w
            y_cursor -= row_h
        self.c.setStrokeColor(LINE)
        self.c.rect(MARGIN_L, y_cursor, CONTENT_W, top_y - y_cursor, fill=0, stroke=1)
        x = MARGIN_L
        for w in col_widths[:-1]:
            x += w
            self.c.line(x, top_y, x, y_cursor)
        self.y = y_cursor - 4 * mm
        self.c.setFillColor(BLACK)

    def divider(self, gap_before=2 * mm, gap_after=4 * mm):
        self.ensure_space(gap_before + gap_after + 1)
        self.y -= gap_before
        self.c.setStrokeColor(LINE)
        self.c.line(MARGIN_L, self.y, PAGE_W - MARGIN_R, self.y)
        self.y -= gap_after

    def note_box(self, text, size=8.5):
        lines = self._wrap(text, FONT_I, size, CONTENT_W - 6 * mm)
        h = len(lines) * 4.6 * mm + 4 * mm
        self.ensure_space(h + 4 * mm)
        self.c.setFillColor(HexColor('#eef4fd'))
        self.c.roundRect(MARGIN_L, self.y - h, CONTENT_W, h, 2, fill=1, stroke=0)
        ty = self.y - 5.5 * mm
        self.c.setFont(FONT_I, size)
        self.c.setFillColor(HexColor('#1e40af'))
        for line in lines:
            self.c.drawString(MARGIN_L + 3 * mm, ty, line)
            ty -= 4.6 * mm
        self.y -= h + 4 * mm
        self.c.setFillColor(BLACK)

    def signature_block(self, label, field_name):
        """Interactive: a typed-name text field. Filled: the actual drawn signature image,
        falling back to the typed name in a script-style font if no image was captured."""
        self._name(field_name)
        if self.mode == 'interactive':
            self.ensure_space(7 * mm + 9 * mm)
            self.c.setFont(FONT_B, 9)
            self.c.setFillColor(DARK)
            self.c.drawString(MARGIN_L, self.y, label)
            self.y -= 5 * mm
            self.form.textfield(
                name=field_name, tooltip=label,
                x=MARGIN_L, y=self.y - 7 * mm, width=CONTENT_W, height=7 * mm,
                borderStyle='underlined', borderColor=LINE, fillColor=BOX_BG,
                textColor=DARK, fontSize=9, forceBorder=True,
            )
            self.y -= 7 * mm + 4 * mm
            self.c.setFillColor(BLACK)
            return

        box_h = 20 * mm
        self.ensure_space(box_h + 10 * mm)
        self.c.setFont(FONT_B, 9)
        self.c.setFillColor(DARK)
        self.c.drawString(MARGIN_L, self.y, 'Signature:')
        self.y -= 5 * mm
        self.c.setStrokeColor(LINE)
        self.c.rect(MARGIN_L, self.y - box_h, 80 * mm, box_h, fill=0, stroke=1)
        if self.signature_bytes:
            try:
                img = ImageReader(io.BytesIO(self.signature_bytes))
                iw, ih = img.getSize()
                scale = min((80 * mm - 4 * mm) / iw, (box_h - 4 * mm) / ih)
                dw, dh = iw * scale, ih * scale
                self.c.drawImage(
                    img, MARGIN_L + (80 * mm - dw) / 2, self.y - box_h + (box_h - dh) / 2,
                    width=dw, height=dh, mask='auto',
                )
            except Exception:
                self._draw_typed_signature(field_name)
        else:
            self._draw_typed_signature(field_name)
        self.y -= box_h + 4 * mm
        self.c.setFillColor(BLACK)

    def _draw_typed_signature(self, field_name):
        name = str(self.answers.get(field_name) or self.answers.get('decl_name') or '').strip()
        if name:
            self.c.setFont(FONT_BI, 16)
            self.c.setFillColor(NAVY)
            self.c.drawString(MARGIN_L + 4 * mm, self.y - 13 * mm, name)
        else:
            self.c.setFont(FONT_I, 9)
            self.c.setFillColor(HexColor('#9ca3af'))
            self.c.drawString(MARGIN_L + 4 * mm, self.y - 11 * mm, 'Not signed')

    def finish(self):
        self._draw_footer()
        self.c.save()


TITLE = 'Done-Well Cleaning Limited'
BLANK_SUBTITLE = [
    'Employee / Worker Application Form — Zero-Hours / Casual Cleaning Position',
    'Private and Confidential. Please complete this form in full; it will be used for recruitment and selection purposes.',
]
FILLED_SUBTITLE = [
    'Employee / Worker Application — Copy of Your Submitted Application',
    'This is a record of the answers you submitted online. Please keep it for your reference.',
]

NI_DEFERRED_NOTE = 'Not requested at this application stage — you will be asked for this only if you progress further.'
DBS_DEFERRED_NOTE = 'Not requested at this application stage — you will be asked for this only if you progress further.'


def populate(f, include_office_section=True):
    f.paragraph(
        'This application form is not an employment contract and does not guarantee that work will be offered. '
        'If you are appointed, Done-Well Cleaning Limited will provide the required written statement of employment '
        'particulars before or on your first day of work.'
    )

    # 1. Position applied for
    f.section('1', 'Position Applied For')
    f.checkbox_group(
        'Position:',
        ['Cleaning Operative', 'Domestic Cleaner', 'Commercial Cleaner', 'Deep Cleaning Operative', 'Office Cleaner', 'Other'],
        'pos', other=True,
    )
    f.checkbox_group(
        'Type of work preferred:',
        ['Domestic cleaning', 'Office/commercial cleaning', 'End-of-tenancy cleaning', 'Deep cleaning', 'Communal areas', 'Other'],
        'worktype', other=True,
    )
    f.text_field('Preferred area(s) of work:', 'pref_area', height=12 * mm, multiline=True)

    # 2. Personal details
    f.section('2', 'Personal Details')
    f.field_row([('Full legal name:', 'full_name', 2), ('Preferred name:', 'preferred_name', 1)])
    f.text_field('Current address:', 'address', height=14 * mm, multiline=True)
    f.field_row([('Postcode:', 'postcode', 1), ('Telephone:', 'phone', 1), ('Email:', 'email', 1)])
    f.text_field(
        'National Insurance Number:', 'ni_number',
        note='This may be requested as part of the pre-employment/payroll process. It does not need to be '
             'provided at the initial application stage if the company does not require it at that point.',
        deferred_note=NI_DEFERRED_NOTE,
    )

    # 3. Right to work
    f.section('3', 'Right to Work in the UK')
    f.checkbox_group('Do you currently have the right to work in the UK?', ['Yes', 'No', 'Unsure'], 'rtw')
    f.text_field('If applicable — type of immigration/right-to-work status:', 'rtw_status')
    f.field_row([('Share code:', 'share_code', 1), ('Expiry date, if applicable:', 'rtw_expiry', 1)])
    f.paragraph('Done-Well Cleaning Limited will complete the appropriate right-to-work check before employment begins.',
                italic=True, size=8.5)

    # 4. Zero hours
    f.section('4', 'Zero-Hours / Casual Work')
    f.paragraph(
        'Done-Well Cleaning Limited may engage successful applicants on a zero-hours/casual basis, depending on the '
        'role and agreed employment status. Under a genuine zero-hours arrangement:'
    )
    f.bullet_list([
        'there is no guaranteed minimum number of hours;',
        "work may be offered according to the company's business requirements;",
        'the worker is not required to accept work offered;',
        'working hours may vary from week to week; and',
        'the worker may generally work for other employers. An exclusivity clause cannot lawfully be imposed on a '
        'zero-hours worker.',
    ])
    f.subheading('Availability')
    f.checkbox_grid(
        ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday'],
        ['Morning', 'Afternoon', 'Evening'],
        'avail',
    )
    f.text_field('How many hours would you ideally like to work each week?', 'hours_per_week')
    f.checkbox_group('Are you available for short-notice work?', ['Yes', 'No', 'Sometimes'], 'short_notice')
    f.text_field('If sometimes, please explain:', 'short_notice_explain', height=10 * mm, multiline=True)

    # 5. Employment history
    f.section('5', 'Employment History')
    f.paragraph('Please give details of your most recent employment and relevant experience.')
    for i in (1, 2, 3):
        f.subheading(f'Employer {i}')
        f.field_row([('Company:', f'emp{i}_company', 2), ('Position:', f'emp{i}_position', 1)])
        f.field_row([('From:', f'emp{i}_from', 1), ('To:', f'emp{i}_to', 1)])
        f.text_field('Reason for leaving:', f'emp{i}_reason', height=9 * mm, multiline=True)
        f.text_field('Main duties:', f'emp{i}_duties', height=12 * mm, multiline=True)

    # 6. Cleaning experience
    f.section('6', 'Cleaning Experience')
    f.checkbox_group(
        "How many years' cleaning experience do you have?",
        ['None', 'Less than 1 year', '1–2 years', '3–5 years', 'More than 5 years'],
        'years_exp',
    )
    f.checkbox_group(
        'Please tick areas in which you have experience:',
        ['Domestic cleaning', 'Commercial cleaning', 'Office cleaning', 'Hotel cleaning', 'End-of-tenancy cleaning',
         'Deep cleaning', 'Communal-area cleaning', 'Carpet cleaning', 'Floor cleaning/polishing', 'Window cleaning',
         'Cleaning chemicals', 'COSHH procedures', 'Infection-control cleaning', 'Manual handling',
         'Use of cleaning machinery/equipment'],
        'exp_area',
    )
    f.text_field('Other relevant experience:', 'other_exp', height=12 * mm, multiline=True)

    # 7. Qualifications
    f.section('7', 'Qualifications and Training')
    f.paragraph('Please list any relevant qualifications or training.')
    f.table(['Qualification / Training', 'Provider', 'Date'], 3, 'qual', col_weights=[2, 2, 1])
    f.paragraph(
        'Examples include: COSHH, Health & Safety, Manual Handling, Infection Control, First Aid, Safeguarding, '
        'Cleaning equipment, Other relevant training.', italic=True, size=8.5,
    )

    # 8. DBS
    f.section('8', 'DBS / Criminal Record Check')
    f.note_box(
        'Done-Well Cleaning Limited may require a criminal record check as part of its recruitment process where '
        'this is lawful and appropriate for the position. The level of DBS check will depend on the duties and legal '
        'eligibility of the particular role. Standard and Enhanced DBS checks cannot simply be requested for every '
        'job; the employer must establish that the role is legally eligible.'
    )
    f.checkbox_group(
        'Have you ever been convicted of a criminal offence or received a caution, reprimand or warning that is '
        'required to be disclosed for the purposes of the applicable DBS process?',
        ['Yes', 'No', 'I am unsure'], 'dbs_disclosure', deferred_note=DBS_DEFERRED_NOTE,
    )
    f.paragraph(
        'If yes, do not provide detailed information on this application form unless requested by Done-Well Cleaning '
        'Limited as part of the appropriate DBS process. You will be given the appropriate DBS documentation and '
        'instructions if a DBS check is required. You may be required to provide original identity documents or '
        'other information necessary to complete the check.', italic=True, size=8.5,
    )
    f.paragraph(
        'A criminal record will not automatically mean that an applicant cannot be employed. Any information '
        'disclosed will be considered lawfully and fairly, taking into account the nature and relevance of the '
        'information, the role and applicable legal requirements.', italic=True, size=8.5,
    )

    # 9. References
    f.section('9', 'References')
    f.paragraph('Please provide two referees.')
    for i in (1, 2):
        f.subheading(f'Reference {i}')
        f.field_row([('Name:', f'ref{i}_name', 2), ('Organisation:', f'ref{i}_org', 1)])
        f.field_row([('Position:', f'ref{i}_position', 1), ('Relationship to applicant:', f'ref{i}_relationship', 1)])
        f.field_row([('Telephone:', f'ref{i}_phone', 1), ('Email:', f'ref{i}_email', 1)])
    f.checkbox_group('May Done-Well Cleaning Limited contact your referees before making an offer?', ['Yes', 'No'], 'ref_contact')
    f.text_field('If no, please explain:', 'ref_contact_explain', height=9 * mm, multiline=True)
    f.paragraph('A job offer may be made conditional upon satisfactory references and any required criminal-record checks.',
                italic=True, size=8.5)

    # 10. Travel
    f.section('10', 'Travel and Availability')
    f.checkbox_group('How will you normally travel to work?',
                      ['Car', 'Public transport', 'Bicycle', 'Walking', 'Other'], 'travel_mode', other=True)
    f.checkbox_group('Do you have regular access to a vehicle?', ['Yes', 'No'], 'has_vehicle')
    f.checkbox_group('Do you hold a current driving licence?', ['Yes', 'No', 'Not applicable'], 'driving_licence')
    f.checkbox_group('Are you willing to travel between different cleaning locations?', ['Yes', 'No'], 'willing_travel')

    # 11. Reasonable adjustments
    f.section('11', 'Reasonable Adjustments')
    f.checkbox_group('Do you require any reasonable adjustment to enable you to participate in the recruitment process?',
                      ['Yes', 'No'], 'adjustment')
    f.text_field('If yes, please explain:', 'adjustment_explain', height=12 * mm, multiline=True)
    f.paragraph(
        'This section is intended to allow Done-Well Cleaning Limited to support applicants during recruitment. UK '
        'employers can ask about health/disability at recruitment for limited lawful purposes, including arranging '
        'reasonable adjustments or establishing whether an essential job requirement can be met.', italic=True, size=8.5,
    )

    # 12. Emergency contact
    f.section('12', 'Emergency Contact')
    f.field_row([('Name:', 'ec_name', 2), ('Relationship:', 'ec_relationship', 1)])
    f.field_row([('Telephone:', 'ec_phone', 1), ('Alternative telephone:', 'ec_alt_phone', 1)])

    # 13. Declaration
    f.section('13', 'Applicant Declaration')
    f.paragraph('I declare that the information contained in this application is true and complete to the best of my knowledge. I understand that:')
    f.bullet_list([
        'This application form does not constitute a contract of employment.',
        'Completion of this form does not guarantee employment or any minimum number of hours.',
        'If appointed, I will receive the appropriate written employment/worker particulars.',
        'Any appointment may be conditional upon satisfactory references, right-to-work verification and, where '
        'legally required, a satisfactory DBS/criminal-record check.',
        'I will provide accurate information and genuine documentation.',
        'Done-Well Cleaning Limited may verify information supplied by me as part of its recruitment process.',
        'The company may require me to complete relevant induction, health and safety and cleaning training.',
        'If engaged under a zero-hours arrangement, the arrangement does not guarantee a minimum number of working hours.',
        'I am not prevented from working for another employer under a zero-hours arrangement.',
        'Employment rights depend on my legal employment status and are not removed merely because the arrangement '
        'is described as "zero-hours".',
    ])
    f.field_row([('Applicant name:', 'decl_name', 1), ('Date:', 'decl_date', 1)])
    f.signature_block('Signature (type your full name to sign):', 'decl_signature')

    if not include_office_section:
        return

    f.divider()
    f.paragraph('FOR DONE-WELL CLEANING LIMITED USE ONLY', size=9)
    f.subheading('Office Use — Recruitment Record')
    f.field_row([('Application received:', 'office_received', 1), ('Interview date:', 'office_interview_date', 1)])
    f.text_field('Interviewer:', 'office_interviewer')
    f.checkbox_group('Application outcome:', ['Successful', 'Unsuccessful', 'Further consideration', 'Waiting list'], 'office_outcome')
    f.text_field('Proposed position:', 'office_proposed_position')
    f.checkbox_group('Employment status:', ['Employee', 'Worker', 'To be confirmed'], 'office_status')
    f.field_row([('Proposed pay rate (£ per hour):', 'office_pay_rate', 1), ('Proposed start date:', 'office_start_date', 1)])

    f.subheading('Pre-employment checks')
    f.table(['Check', 'Status', 'Date', 'Checked by'], 6, 'office_check', col_weights=[2, 2, 1, 1.5])
    f.paragraph(
        'Rows above: Identity, Right to Work, References, DBS, Qualifications, Other checks.', italic=True, size=8,
    )

    f.checkbox_group('DBS level required:', ['Basic', 'Standard', 'Enhanced', 'Enhanced + Barred List', 'None'], 'office_dbs_level')
    f.field_row([('DBS eligibility confirmed by:', 'office_dbs_confirmed_by', 2), ('Date:', 'office_dbs_date', 1)])
    f.checkbox_group('Written statement/contract issued:', ['Yes', 'No'], 'office_statement_issued')
    f.text_field('Date issued:', 'office_statement_date')
    f.checkbox_group('Induction completed:', ['Yes', 'No'], 'office_induction')
    f.text_field('Notes:', 'office_notes', height=16 * mm, multiline=True)
    f.field_row([('Authorised by:', 'office_authorised_by', 2), ('Date:', 'office_authorised_date', 1)])
    f.text_field('Signature:', 'office_signature')


def render_blank_pdf(out_path):
    """Generates the blank, downloadable fillable PDF (AcroForm widgets)."""
    f = FormBuilder(out_path, TITLE, BLANK_SUBTITLE, mode='interactive')
    populate(f, include_office_section=True)
    f.finish()


def render_filled_pdf(answers, signature_png_bytes=None):
    """Generates a completed-application PDF (static text/checks/signature) from a
    dict of {field_name: value}. Checkbox fields are truthy when checked.
    Returns raw PDF bytes."""
    buf = io.BytesIO()
    f = FormBuilder(buf, TITLE, FILLED_SUBTITLE, mode='filled', answers=answers or {}, signature_bytes=signature_png_bytes)
    populate(f, include_office_section=False)
    f.finish()
    return buf.getvalue()


if __name__ == '__main__':
    import sys
    out = sys.argv[1] if len(sys.argv) > 1 else 'done-well-job-application-form-fillable.pdf'
    render_blank_pdf(out)
    print('Wrote', out)
