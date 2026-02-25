from io import BytesIO
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.units import cm, mm
from reportlab.pdfgen import canvas
from reportlab.platypus import Paragraph
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_JUSTIFY
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont


# ── Palette ──────────────────────────────────────────────────────────────────
NAVY       = colors.HexColor("#0D1B2A")   # deep navy – headers / accents
GOLD       = colors.HexColor("#C9A84C")   # warm gold – decorative rule
SLATE      = colors.HexColor("#2C3E50")   # section titles
BODY_TEXT  = colors.HexColor("#3D3D3D")   # body paragraphs
MUTED      = colors.HexColor("#7F8C8D")   # labels / meta text
ROW_ALT    = colors.HexColor("#F4F6F8")   # alternating table rows
ROW_WHITE  = colors.HexColor("#FFFFFF")
DIVIDER    = colors.HexColor("#DDE2E8")   # subtle horizontal rules
BG_SIDEBAR = colors.HexColor("#0D1B2A")   # left sidebar strip
GOLD_LIGHT = colors.HexColor("#F5E6C8")   # sidebar accent band

PAGE_W, PAGE_H = A4


# ── Helper: draw text with automatic line wrapping via Paragraph ──────────────
def draw_paragraph(c_obj, text, x, y, width, style):
    p = Paragraph(text, style)
    w, h = p.wrapOn(c_obj, width, 9999)
    p.drawOn(c_obj, x, y - h)
    return h   # consumed height


# ── Background chrome ─────────────────────────────────────────────────────────
def draw_chrome(c_obj):
    """Draw the decorative page background elements."""

    # Left navy sidebar strip (full height)
    STRIP_W = 1.4 * cm
    c_obj.setFillColor(BG_SIDEBAR)
    c_obj.rect(0, 0, STRIP_W, PAGE_H, fill=1, stroke=0)

    # Gold accent bar inside sidebar (top third)
    c_obj.setFillColor(GOLD)
    c_obj.rect(0, PAGE_H - 6 * cm, STRIP_W, 6 * cm, fill=1, stroke=0)

    # Bottom footer band
    FOOTER_H = 1.5 * cm
    c_obj.setFillColor(NAVY)
    c_obj.rect(0, 0, PAGE_W, FOOTER_H, fill=1, stroke=0)

    # Thin gold rule just above footer
    c_obj.setStrokeColor(GOLD)
    c_obj.setLineWidth(2)
    c_obj.line(STRIP_W + 0.3 * cm, FOOTER_H + 1, PAGE_W - 0.8 * cm, FOOTER_H + 1)


def draw_footer(c_obj, company_name, candidate_name):
    c_obj.setFillColor(colors.white)
    c_obj.setFont("Helvetica", 7.5)
    footer_text = f"{company_name}  ·  Confidential  ·  Prepared for {candidate_name}"
    c_obj.drawCentredString(PAGE_W / 2, 0.52 * cm, footer_text)


# ── Section divider ───────────────────────────────────────────────────────────
def draw_section_title(c_obj, title, y, left=2.2 * cm):
    right = PAGE_W - 1.2 * cm
    # Title text
    c_obj.setFont("Helvetica-Bold", 9)
    c_obj.setFillColor(NAVY)
    c_obj.drawString(left, y, title.upper())

    title_w = c_obj.stringWidth(title.upper(), "Helvetica-Bold", 9)

    # Gold rule that runs from after title to right margin
    gap = 6
    c_obj.setStrokeColor(GOLD)
    c_obj.setLineWidth(1.2)
    c_obj.line(left + title_w + gap, y + 3, right, y + 3)

    return y - 10   # return next y position


# ── Details table ─────────────────────────────────────────────────────────────
def draw_details_table(c_obj, rows, x, y, col1_w=4.5 * cm, col2_w=10.5 * cm, row_h=0.72 * cm):
    """rows: list of (label, value) tuples"""
    label_style = ParagraphStyle(
        "TL", fontName="Helvetica-Bold", fontSize=8,
        textColor=MUTED, leading=10,
    )
    value_style = ParagraphStyle(
        "TV", fontName="Helvetica", fontSize=9.5,
        textColor=NAVY, leading=12,
    )

    for i, (label, value) in enumerate(rows):
        row_color = ROW_ALT if i % 2 == 0 else ROW_WHITE
        c_obj.setFillColor(row_color)
        c_obj.rect(x, y - row_h, col1_w + col2_w, row_h, fill=1, stroke=0)

        # Thin border
        c_obj.setStrokeColor(DIVIDER)
        c_obj.setLineWidth(0.4)
        c_obj.rect(x, y - row_h, col1_w + col2_w, row_h, fill=0, stroke=1)

        # Label
        lp = Paragraph(label, label_style)
        lp.wrapOn(c_obj, col1_w - 0.4 * cm, row_h)
        lp.drawOn(c_obj, x + 0.25 * cm, y - row_h + 0.1 * cm)

        # Value
        vp = Paragraph(str(value), value_style)
        vp.wrapOn(c_obj, col2_w - 0.4 * cm, row_h)
        vp.drawOn(c_obj, x + col1_w + 0.25 * cm, y - row_h + 0.08 * cm)

        y -= row_h

    return y   # new cursor y


# ── Main generator ────────────────────────────────────────────────────────────
def generate_offer_letter_pdf(offer, candidate):
    """
    Generate a professional, beautifully designed offer letter PDF.
    Returns bytes of the generated PDF.
    """
    buffer = BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)
    c.setTitle(f"Offer Letter – {candidate.name}")

    # ── Format dates ──────────────────────────────────────────────────────────
    offer_date_str   = offer.offer_date.strftime("%B %d, %Y")  if offer.offer_date   else ""
    joining_date_str = offer.joining_date.strftime("%B %d, %Y") if offer.joining_date else "To be confirmed"

    # ── Shared paragraph styles ───────────────────────────────────────────────
    LEFT_X  = 2.2 * cm       # content left edge (after sidebar)
    RIGHT_X = PAGE_W - 1.2 * cm
    CONTENT_W = RIGHT_X - LEFT_X

    body_style = ParagraphStyle(
        "Body",
        fontName="Helvetica", fontSize=10,
        textColor=BODY_TEXT, leading=15,
        alignment=TA_JUSTIFY,
    )
    bold_inline = ParagraphStyle(
        "BI",
        fontName="Helvetica", fontSize=10,
        textColor=BODY_TEXT, leading=15,
        alignment=TA_JUSTIFY,
    )

    # ════════════════════════════════════════════════════════════════════════
    # PAGE CHROME
    # ════════════════════════════════════════════════════════════════════════
    draw_chrome(c)
    draw_footer(c, offer.company_name or "Company", candidate.name)

    # ════════════════════════════════════════════════════════════════════════
    # HEADER BLOCK
    # ════════════════════════════════════════════════════════════════════════
    cursor = PAGE_H - 1.4 * cm   # start near top

    # Company name – large
    c.setFont("Helvetica-Bold", 22)
    c.setFillColor(NAVY)
    c.drawString(LEFT_X, cursor, offer.company_name or "Company Name")
    cursor -= 0.6 * cm

    # Gold underline beneath company name
    name_w = c.stringWidth(offer.company_name or "Company Name", "Helvetica-Bold", 22)
    c.setStrokeColor(GOLD)
    c.setLineWidth(2.5)
    c.line(LEFT_X, cursor, LEFT_X + name_w, cursor)
    cursor -= 0.55 * cm

    # "OFFER OF EMPLOYMENT" badge strip
    badge_h = 0.85 * cm
    c.setFillColor(NAVY)
    c.roundRect(LEFT_X, cursor - badge_h, CONTENT_W, badge_h, 4, fill=1, stroke=0)
    c.setFillColor(GOLD)
    c.setFont("Helvetica-Bold", 11)
    c.drawCentredString(LEFT_X + CONTENT_W / 2, cursor - badge_h + 0.22 * cm,
                        "OFFER  OF  EMPLOYMENT")
    cursor -= badge_h + 0.5 * cm

    # Date + Ref line
    c.setFont("Helvetica", 9)
    c.setFillColor(MUTED)
    c.drawString(LEFT_X, cursor, f"Date: {offer_date_str}")
    c.drawRightString(RIGHT_X, cursor, "Confidential")
    cursor -= 0.9 * cm

    # ════════════════════════════════════════════════════════════════════════
    # SALUTATION
    # ════════════════════════════════════════════════════════════════════════
    c.setFont("Helvetica", 10.5)
    c.setFillColor(BODY_TEXT)
    c.drawString(LEFT_X, cursor, f"Dear  {candidate.name},")
    cursor -= 0.7 * cm

    # Intro paragraph
    dept_clause = f" in the <b>{offer.department}</b> department" if offer.department else ""
    intro = (
        f"We are pleased to extend this formal Offer of Employment for the position of "
        f"<b>{offer.position}</b>{dept_clause} at <b>{offer.company_name}</b>. "
        f"After a thorough evaluation of your experience, skills, and interview performance, "
        f"we are confident that you will be a valued member of our growing team. "
        f"We look forward to your positive response."
    )
    h = draw_paragraph(c, intro, LEFT_X, cursor, CONTENT_W, body_style)
    cursor -= h + 0.5 * cm

    # ════════════════════════════════════════════════════════════════════════
    # EMPLOYMENT DETAILS
    # ════════════════════════════════════════════════════════════════════════
    cursor = draw_section_title(c, "Employment Details", cursor) - 0.25 * cm

    detail_rows = [
        ("Position / Role",      offer.position),
        ("Department",           offer.department or "—"),
        ("Offered Salary (CTC)", offer.salary),
        ("Date of Joining",      joining_date_str),
        ("Work Location",        offer.work_location or "To be confirmed"),
        ("Working Hours",        offer.work_hours or "9:00 AM – 6:00 PM"),
    ]
    cursor = draw_details_table(c, detail_rows, LEFT_X, cursor)
    cursor -= 0.55 * cm

    # ════════════════════════════════════════════════════════════════════════
    # TERMS & CONDITIONS
    # ════════════════════════════════════════════════════════════════════════
    cursor = draw_section_title(c, "Terms & Conditions", cursor) - 0.25 * cm

    terms = (
        "This offer is subject to the successful completion of background verification, "
        "reference checks, and submission of all required documents prior to your joining date. "
        "By accepting this offer, you agree to abide by the company's policies, code of conduct, "
        "and all applicable laws and regulations. This offer is not transferable and lapses if "
        "not accepted within the specified time."
    )
    h = draw_paragraph(c, terms, LEFT_X, cursor, CONTENT_W, body_style)
    cursor -= h + 0.5 * cm

    # ════════════════════════════════════════════════════════════════════════
    # ADDITIONAL BENEFITS (conditional)
    # ════════════════════════════════════════════════════════════════════════
    if offer.additional_benefits:
        cursor = draw_section_title(c, "Benefits & Perks", cursor) - 0.25 * cm
        h = draw_paragraph(c, offer.additional_benefits, LEFT_X, cursor, CONTENT_W, body_style)
        cursor -= h + 0.5 * cm

    # ════════════════════════════════════════════════════════════════════════
    # ACCEPTANCE NOTE
    # ════════════════════════════════════════════════════════════════════════
    # Subtle info box
    box_h = 1.15 * cm
    c.setFillColor(colors.HexColor("#EEF2FF"))   # very light indigo tint
    c.setStrokeColor(colors.HexColor("#A5B4FC"))
    c.setLineWidth(0.8)
    c.roundRect(LEFT_X, cursor - box_h, CONTENT_W, box_h, 5, fill=1, stroke=1)

    c.setFont("Helvetica", 9.5)
    c.setFillColor(colors.HexColor("#3730A3"))
    acceptance_note = (
        "Please sign and return a copy of this letter within  7 days  of receipt to confirm your acceptance."
    )
    c.drawCentredString(LEFT_X + CONTENT_W / 2, cursor - box_h + 0.35 * cm, acceptance_note)
    cursor -= box_h + 0.7 * cm

    # ════════════════════════════════════════════════════════════════════════
    # SIGNATURE BLOCK
    # ════════════════════════════════════════════════════════════════════════
    cursor = draw_section_title(c, "Authorisation & Acceptance", cursor) - 0.4 * cm

    col_w = CONTENT_W / 2 - 0.5 * cm
    col1_x = LEFT_X
    col2_x = LEFT_X + CONTENT_W / 2 + 0.5 * cm

    # Left: company signatory
    c.setFont("Helvetica-Bold", 8.5)
    c.setFillColor(MUTED)
    c.drawString(col1_x, cursor, "For " + (offer.company_name or "Company"))

    # Right: candidate
    c.drawString(col2_x, cursor, "Accepted by Candidate")
    cursor -= 0.35 * cm

    # Signature lines
    c.setStrokeColor(NAVY)
    c.setLineWidth(0.8)
    sig_line_len = col_w
    c.line(col1_x, cursor - 0.9 * cm, col1_x + sig_line_len, cursor - 0.9 * cm)
    c.line(col2_x, cursor - 0.9 * cm, col2_x + sig_line_len, cursor - 0.9 * cm)
    cursor -= 1.1 * cm

    # Name / designation labels
    c.setFont("Helvetica-Bold", 9.5)
    c.setFillColor(NAVY)
    c.drawString(col1_x, cursor, offer.hr_name or "Authorized Signatory")
    c.drawString(col2_x, cursor, candidate.name)
    cursor -= 0.35 * cm

    c.setFont("Helvetica", 8.5)
    c.setFillColor(MUTED)
    c.drawString(col1_x, cursor, offer.hr_designation or "HR Manager")
    c.drawString(col2_x, cursor, "Date: _______________")

    # ═══════════════════════════════════════════════════════════════════════
    # SAVE
    # ═══════════════════════════════════════════════════════════════════════
    c.showPage()
    c.save()

    pdf_bytes = buffer.getvalue()
    buffer.close()
    return pdf_bytes