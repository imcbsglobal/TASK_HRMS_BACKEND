"""
offer_pdf.py
============
Beautiful offer letter PDF generator with full Company Settings integration.

Company settings dict shape (from CompanyContext / your backend):
    company = {
        "name":         "Acme Corporation",
        "tagline":      "Building the future",
        "email":        "hr@acme.com",
        "phone":        "+91 98765 43210",
        "website":      "www.acme.com",
        "address":      "123 Main St, Bengaluru, Karnataka 560001",
        "logo":         "<base64 data-URI string OR local file path OR None>",
        "primaryColor": "#0D1B2A",   # hex – drives the whole palette
        "currency":     "INR",
    }
"""

from io import BytesIO
import base64
import os
import tempfile
import colorsys

from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.units import cm, mm
from reportlab.pdfgen import canvas
from reportlab.platypus import Paragraph
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_JUSTIFY


PAGE_W, PAGE_H = A4


# ─────────────────────────────────────────────────────────────────────────────
# Palette builder  – derives the full colour set from one primary hex
# ─────────────────────────────────────────────────────────────────────────────
def build_palette(primary_hex: str):
    """Generate a harmonious palette from the brand primary colour."""
    primary = colors.HexColor(primary_hex)

    def hex_blend(h, pct):
        """Blend hex colour toward white by pct (0=original, 1=white)."""
        c2 = colors.HexColor(h)
        r = c2.red   + (1 - c2.red)   * pct
        g = c2.green + (1 - c2.green) * pct
        b = c2.blue  + (1 - c2.blue)  * pct
        return colors.Color(r, g, b)

    def darken(h, pct):
        c2 = colors.HexColor(h)
        r = c2.red   * (1 - pct)
        g = c2.green * (1 - pct)
        b = c2.blue  * (1 - pct)
        return colors.Color(r, g, b)

    # Derive gold/accent as a warm-shifted lighter version
    r, g, b = primary.red, primary.green, primary.blue
    h_hls, l_hls, s_hls = colorsys.rgb_to_hls(r, g, b)
    # Rotate hue +30° toward warm gold, increase lightness
    ah = (h_hls + 0.083) % 1.0
    ar, ag, ab = colorsys.hls_to_rgb(ah, min(l_hls + 0.35, 0.75), max(s_hls, 0.55))
    accent = colors.Color(ar, ag, ab)

    return {
        "primary":      primary,
        "primary_dark": darken(primary_hex, 0.15),
        "accent":       accent,
        "accent_light": colors.Color(ar * 0.3 + 0.7, ag * 0.3 + 0.7, ab * 0.3 + 0.7),
        "sidebar_bg":   primary,
        "header_bg":    primary,
        "body":         colors.HexColor("#3D3D3D"),
        "muted":        colors.HexColor("#6B7280"),
        "divider":      colors.HexColor("#E5E7EB"),
        "row_alt":      colors.HexColor("#F9FAFB"),
        "row_white":    colors.white,
        "badge_text":   colors.white,
        "light_tint":   hex_blend(primary_hex, 0.92),
        "mid_tint":     hex_blend(primary_hex, 0.80),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Logo helper – handles base64 data-URI and file paths
# ─────────────────────────────────────────────────────────────────────────────
def resolve_logo(logo_value) -> str | None:
    """
    Accepts:
      - None / ""            → returns None
      - "data:image/...;base64,..." → writes to temp file, returns path
      - A valid file path    → returns as-is
    """
    if not logo_value:
        return None

    if isinstance(logo_value, str) and logo_value.startswith("data:image"):
        try:
            header, b64data = logo_value.split(",", 1)
            ext = "png"
            if "jpeg" in header or "jpg" in header:
                ext = "jpg"
            elif "svg" in header:
                return None  # ReportLab can't render SVG directly
            raw = base64.b64decode(b64data)
            tmp = tempfile.NamedTemporaryFile(delete=False, suffix=f".{ext}")
            tmp.write(raw)
            tmp.close()
            return tmp.name
        except Exception:
            return None

    if isinstance(logo_value, str) and os.path.exists(logo_value):
        return logo_value

    return None


# ─────────────────────────────────────────────────────────────────────────────
# Paragraph helper
# ─────────────────────────────────────────────────────────────────────────────
def draw_paragraph(c_obj, text, x, y, width, style):
    p = Paragraph(text, style)
    _, h = p.wrapOn(c_obj, width, 9999)
    p.drawOn(c_obj, x, y - h)
    return h


# ─────────────────────────────────────────────────────────────────────────────
# Page chrome
# ─────────────────────────────────────────────────────────────────────────────
def draw_chrome(c_obj, pal, logo_path):
    """
    Modern design:
    • Tall gradient-style header band at top
    • Thin accent stripe on left
    • Footer band
    • Company logo in header if available
    """

    HEADER_H = 3.6 * cm
    STRIPE_W = 0.45 * cm
    FOOTER_H = 1.2 * cm

    # ── Top header band ───────────────────────────────────────────────────────
    c_obj.setFillColor(pal["header_bg"])
    c_obj.rect(0, PAGE_H - HEADER_H, PAGE_W, HEADER_H, fill=1, stroke=0)

    # Accent stripe inside header (right portion)
    c_obj.setFillColor(pal["accent"])
    c_obj.rect(PAGE_W - 2.2 * cm, PAGE_H - HEADER_H, 2.2 * cm, HEADER_H, fill=1, stroke=0)

    # Thin accent rule under header
    c_obj.setStrokeColor(pal["accent"])
    c_obj.setLineWidth(3)
    c_obj.line(0, PAGE_H - HEADER_H, PAGE_W, PAGE_H - HEADER_H)

    # ── Left thin stripe (accent colour) ─────────────────────────────────────
    c_obj.setFillColor(pal["accent"])
    c_obj.rect(0, FOOTER_H, STRIPE_W, PAGE_H - HEADER_H - FOOTER_H, fill=1, stroke=0)

    # ── Footer band ───────────────────────────────────────────────────────────
    c_obj.setFillColor(pal["header_bg"])
    c_obj.rect(0, 0, PAGE_W, FOOTER_H, fill=1, stroke=0)

    # Thin accent line just above footer
    c_obj.setStrokeColor(pal["accent"])
    c_obj.setLineWidth(1.5)
    c_obj.line(STRIPE_W + 0.2 * cm, FOOTER_H, PAGE_W - 0.5 * cm, FOOTER_H)

    # ── Logo in top-right of header ───────────────────────────────────────────
    if logo_path:
        try:
            logo_size = 1.8 * cm
            logo_x = PAGE_W - logo_size - 0.2 * cm
            logo_y = PAGE_H - (HEADER_H / 2) - (logo_size / 2)
            c_obj.drawImage(
                logo_path, logo_x, logo_y,
                width=logo_size, height=logo_size,
                preserveAspectRatio=True, mask="auto"
            )
        except Exception:
            pass


def draw_header_text(c_obj, pal, company, left_x, right_x):
    """Company name, tagline, contact inside header band."""
    HEADER_H = 3.6 * cm
    top = PAGE_H - 0.55 * cm

    # Company name
    c_obj.setFont("Helvetica-Bold", 20)
    c_obj.setFillColor(colors.white)
    c_obj.drawString(left_x, top, company.get("name") or "Company Name")

    # Tagline
    tagline = company.get("tagline", "")
    if tagline:
        c_obj.setFont("Helvetica-Oblique", 9)
        c_obj.setFillColor(pal["accent_light"])
        c_obj.drawString(left_x, top - 0.55 * cm, tagline)

    # Contact line
    parts = []
    if company.get("phone"):   parts.append(company["phone"])
    if company.get("email"):   parts.append(company["email"])
    if company.get("website"): parts.append(company["website"])
    contact_str = "  |  ".join(parts)
    if contact_str:
        c_obj.setFont("Helvetica", 7.5)
        c_obj.setFillColor(pal["accent_light"])
        c_obj.drawString(left_x, PAGE_H - HEADER_H + 0.45 * cm, contact_str)

    # Address on second contact line
    addr = company.get("address", "")
    if addr:
        c_obj.setFont("Helvetica", 7.5)
        c_obj.setFillColor(pal["accent_light"])
        c_obj.drawString(left_x, PAGE_H - HEADER_H + 0.2 * cm, addr)


def draw_footer(c_obj, pal, company_name, candidate_name):
    c_obj.setFillColor(colors.white)
    c_obj.setFont("Helvetica", 7.5)
    footer = f"{company_name}  ·  Confidential  ·  Prepared for {candidate_name}"
    c_obj.drawCentredString(PAGE_W / 2, 0.42 * cm, footer)


# ─────────────────────────────────────────────────────────────────────────────
# Section title
# ─────────────────────────────────────────────────────────────────────────────
def draw_section_title(c_obj, pal, title, y, left):
    right = PAGE_W - 1.2 * cm

    # Background pill/tag
    title_w = c_obj.stringWidth(title.upper(), "Helvetica-Bold", 8.5)
    pill_pad = 0.3 * cm
    pill_h   = 0.55 * cm
    c_obj.setFillColor(pal["light_tint"])
    c_obj.roundRect(left, y - pill_h + 0.12 * cm, title_w + pill_pad * 2, pill_h, 3, fill=1, stroke=0)

    # Accent left edge of pill
    c_obj.setFillColor(pal["accent"])
    c_obj.rect(left, y - pill_h + 0.12 * cm, 0.18 * cm, pill_h, fill=1, stroke=0)

    # Title text
    c_obj.setFont("Helvetica-Bold", 8.5)
    c_obj.setFillColor(pal["primary"])
    c_obj.drawString(left + pill_pad, y, title.upper())

    # Rule after pill
    rule_x = left + title_w + pill_pad * 2 + 0.2 * cm
    c_obj.setStrokeColor(pal["accent"])
    c_obj.setLineWidth(1.0)
    c_obj.line(rule_x, y - pill_h / 2 + 0.12 * cm, right, y - pill_h / 2 + 0.12 * cm)

    return y - 0.65 * cm


# ─────────────────────────────────────────────────────────────────────────────
# Details table
# ─────────────────────────────────────────────────────────────────────────────
def draw_details_table(c_obj, pal, rows, x, y, col1_w=4.2 * cm, col2_w=11 * cm, row_h=0.70 * cm):
    label_style = ParagraphStyle(
        "TL", fontName="Helvetica-Bold", fontSize=7.5,
        textColor=pal["muted"], leading=10,
    )
    value_style = ParagraphStyle(
        "TV", fontName="Helvetica", fontSize=9,
        textColor=pal["primary"], leading=11,
    )

    for i, (label, value) in enumerate(rows):
        bg = pal["row_alt"] if i % 2 == 0 else pal["row_white"]
        c_obj.setFillColor(bg)
        c_obj.rect(x, y - row_h, col1_w + col2_w, row_h, fill=1, stroke=0)

        # Left accent dash for odd rows
        if i % 2 == 0:
            c_obj.setFillColor(pal["mid_tint"])
            c_obj.rect(x, y - row_h, 0.12 * cm, row_h, fill=1, stroke=0)

        # Border
        c_obj.setStrokeColor(pal["divider"])
        c_obj.setLineWidth(0.3)
        c_obj.rect(x, y - row_h, col1_w + col2_w, row_h, fill=0, stroke=1)

        lp = Paragraph(label, label_style)
        lp.wrapOn(c_obj, col1_w - 0.5 * cm, row_h)
        lp.drawOn(c_obj, x + 0.3 * cm, y - row_h + 0.1 * cm)

        vp = Paragraph(str(value), value_style)
        vp.wrapOn(c_obj, col2_w - 0.4 * cm, row_h)
        vp.drawOn(c_obj, x + col1_w + 0.25 * cm, y - row_h + 0.09 * cm)

        y -= row_h

    return y


# ─────────────────────────────────────────────────────────────────────────────
# Main generator
# ─────────────────────────────────────────────────────────────────────────────
def generate_offer_letter_pdf(offer, candidate, company: dict = None):
    """
    Generate a beautiful offer letter PDF.

    Parameters
    ----------
    offer     : object with fields:
                  offer_date, joining_date, position, department, salary,
                  work_location, work_hours, additional_benefits,
                  hr_name, hr_designation
                  (company_name is now pulled from `company` dict)

    candidate : object with field:
                  name

    company   : dict from CompanyContext / your backend (see module docstring).
                Falls back to offer.company_name if not provided.

    Returns bytes of the generated PDF.
    """

    # ── Resolve company data ──────────────────────────────────────────────────
    if company is None:
        company = {}

    company_name  = company.get("name")  or getattr(offer, "company_name", None) or "Company"
    primary_color = company.get("primaryColor") or "#0D1B2A"
    logo_path     = resolve_logo(company.get("logo"))

    # Build dynamic palette from brand colour
    pal = build_palette(primary_color)

    # ── Layout constants ──────────────────────────────────────────────────────
    HEADER_H  = 3.6 * cm
    STRIPE_W  = 0.45 * cm
    LEFT_X    = STRIPE_W + 0.9 * cm
    RIGHT_X   = PAGE_W - 1.2 * cm
    CONTENT_W = RIGHT_X - LEFT_X

    # ── Dates ─────────────────────────────────────────────────────────────────
    offer_date_str   = offer.offer_date.strftime("%B %d, %Y")  if getattr(offer, "offer_date", None)   else ""
    joining_date_str = offer.joining_date.strftime("%B %d, %Y") if getattr(offer, "joining_date", None) else "To be confirmed"

    # ── Paragraph styles ──────────────────────────────────────────────────────
    body_style = ParagraphStyle(
        "Body", fontName="Helvetica", fontSize=9.5,
        textColor=pal["body"], leading=15, alignment=TA_JUSTIFY,
    )
    terms_style = ParagraphStyle(
        "Terms", fontName="Helvetica", fontSize=9,
        textColor=pal["body"], leading=14, alignment=TA_JUSTIFY,
    )

    # ═════════════════════════════════════════════════════════════════════════
    buffer = BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)
    c.setTitle(f"Offer Letter – {candidate.name}")

    # ── Background chrome ─────────────────────────────────────────────────────
    draw_chrome(c, pal, logo_path)
    draw_header_text(c, pal, company, LEFT_X, RIGHT_X)
    draw_footer(c, pal, company_name, candidate.name)

    # ── "OFFER OF EMPLOYMENT" badge – sits just below header ──────────────────
    cursor = PAGE_H - HEADER_H - 0.45 * cm

    badge_h = 0.80 * cm
    c.setFillColor(pal["accent"])
    c.roundRect(LEFT_X, cursor - badge_h, CONTENT_W * 0.48, badge_h, 4, fill=1, stroke=0)
    c.setFillColor(colors.white)
    c.setFont("Helvetica-Bold", 10)
    c.drawCentredString(LEFT_X + CONTENT_W * 0.24, cursor - badge_h + 0.23 * cm,
                        "OFFER  OF  EMPLOYMENT")

    # Date right-aligned
    c.setFont("Helvetica", 8.5)
    c.setFillColor(pal["muted"])
    c.drawRightString(RIGHT_X, cursor - badge_h + 0.23 * cm, f"Date: {offer_date_str}")

    cursor -= badge_h + 0.65 * cm

    # ── Salutation ────────────────────────────────────────────────────────────
    c.setFont("Helvetica-Bold", 11)
    c.setFillColor(pal["primary"])
    c.drawString(LEFT_X, cursor, f"Dear {candidate.name},")
    cursor -= 0.65 * cm

    dept_clause = f" in the <b>{offer.department}</b> department" if getattr(offer, "department", None) else ""
    intro = (
        f"We are delighted to extend this formal Offer of Employment for the position of "
        f"<b>{offer.position}</b>{dept_clause} at <b>{company_name}</b>. "
        f"Following a thorough evaluation of your qualifications, experience, and performance "
        f"throughout our selection process, we are confident you will be an exceptional addition "
        f"to our team. We look forward to your positive response."
    )
    h = draw_paragraph(c, intro, LEFT_X, cursor, CONTENT_W, body_style)
    cursor -= h + 0.6 * cm

    # ── Employment Details ────────────────────────────────────────────────────
    cursor = draw_section_title(c, pal, "Employment Details", cursor, LEFT_X)
    cursor -= 0.2 * cm

    currency = company.get("currency", "")
    salary_display = f"{currency} {offer.salary}".strip() if currency else str(offer.salary)

    detail_rows = [
        ("Position / Role",      getattr(offer, "position", "—")),
        ("Department",           getattr(offer, "department", None) or "—"),
        ("Offered CTC",          salary_display),
        ("Date of Joining",      joining_date_str),
        ("Work Location",        getattr(offer, "work_location", None) or "To be confirmed"),
        ("Working Hours",        getattr(offer, "work_hours", None) or "9:00 AM – 6:00 PM"),
    ]
    cursor = draw_details_table(c, pal, detail_rows, LEFT_X, cursor)
    cursor -= 0.6 * cm

    # ── Company Info strip (address / website) ────────────────────────────────
    addr    = company.get("address", "")
    website = company.get("website", "")
    if addr or website:
        info_h = 0.75 * cm
        c.setFillColor(pal["light_tint"])
        c.roundRect(LEFT_X, cursor - info_h, CONTENT_W, info_h, 4, fill=1, stroke=0)
        c.setStrokeColor(pal["mid_tint"])
        c.setLineWidth(0.5)
        c.roundRect(LEFT_X, cursor - info_h, CONTENT_W, info_h, 4, fill=0, stroke=1)

        info_parts = []
        if addr:    info_parts.append(f"📍 {addr}")
        if website: info_parts.append(f"🌐 {website}")
        c.setFont("Helvetica", 7.5)
        c.setFillColor(pal["muted"])
        c.drawCentredString(LEFT_X + CONTENT_W / 2, cursor - info_h + 0.24 * cm, "  ·  ".join(info_parts))
        cursor -= info_h + 0.55 * cm

    # ── Terms & Conditions ────────────────────────────────────────────────────
    cursor = draw_section_title(c, pal, "Terms & Conditions", cursor, LEFT_X)
    cursor -= 0.2 * cm

    terms = (
        "This offer is subject to the successful completion of background verification, "
        "reference checks, and submission of all required documents prior to your joining date. "
        "By accepting this offer, you agree to abide by the company's policies, code of conduct, "
        "and all applicable laws and regulations. This offer is non-transferable and lapses if "
        "not accepted within the specified time."
    )
    h = draw_paragraph(c, terms, LEFT_X, cursor, CONTENT_W, terms_style)
    cursor -= h + 0.55 * cm

    # ── Additional Benefits ───────────────────────────────────────────────────
    if getattr(offer, "additional_benefits", None):
        cursor = draw_section_title(c, pal, "Benefits & Perks", cursor, LEFT_X)
        cursor -= 0.2 * cm
        h = draw_paragraph(c, offer.additional_benefits, LEFT_X, cursor, CONTENT_W, body_style)
        cursor -= h + 0.55 * cm

    # ── Acceptance note box ───────────────────────────────────────────────────
    box_h = 1.0 * cm
    c.setFillColor(pal["light_tint"])
    c.setStrokeColor(pal["accent"])
    c.setLineWidth(1.0)
    c.roundRect(LEFT_X, cursor - box_h, CONTENT_W, box_h, 5, fill=1, stroke=1)

    # Accent left tab on the box
    c.setFillColor(pal["accent"])
    c.roundRect(LEFT_X, cursor - box_h, 0.35 * cm, box_h, 3, fill=1, stroke=0)

    c.setFont("Helvetica-Bold", 9)
    c.setFillColor(pal["primary"])
    note = "Please sign and return a copy of this letter within 7 days of receipt to confirm your acceptance."
    c.drawCentredString(LEFT_X + CONTENT_W / 2, cursor - box_h + 0.32 * cm, note)
    cursor -= box_h + 0.75 * cm

    # ── Signature block ───────────────────────────────────────────────────────
    cursor = draw_section_title(c, pal, "Authorisation & Acceptance", cursor, LEFT_X)
    cursor -= 0.45 * cm

    col_w  = CONTENT_W / 2 - 0.6 * cm
    col1_x = LEFT_X
    col2_x = LEFT_X + CONTENT_W / 2 + 0.3 * cm

    for cx, label in [(col1_x, f"For  {company_name}"), (col2_x, "Accepted by Candidate")]:
        # Label
        c.setFont("Helvetica-Bold", 8)
        c.setFillColor(pal["muted"])
        c.drawString(cx, cursor, label)

        # Signature box (shaded area)
        sig_box_h = 1.0 * cm
        c.setFillColor(pal["row_alt"])
        c.setStrokeColor(pal["divider"])
        c.setLineWidth(0.5)
        c.roundRect(cx, cursor - sig_box_h - 0.4 * cm, col_w, sig_box_h, 3, fill=1, stroke=1)

        # Signature line inside box
        c.setStrokeColor(pal["mid_tint"])
        c.setLineWidth(0.7)
        c.line(cx + 0.2 * cm, cursor - 0.75 * cm, cx + col_w - 0.2 * cm, cursor - 0.75 * cm)

    cursor -= 1.6 * cm

    # Names & designations
    hr_name  = getattr(offer, "hr_name", None)  or "Authorized Signatory"
    hr_desig = getattr(offer, "hr_designation", None) or "HR Manager"

    c.setFont("Helvetica-Bold", 9)
    c.setFillColor(pal["primary"])
    c.drawString(col1_x, cursor, hr_name)
    c.drawString(col2_x, cursor, candidate.name)
    cursor -= 0.32 * cm

    c.setFont("Helvetica", 8)
    c.setFillColor(pal["muted"])
    c.drawString(col1_x, cursor, hr_desig)
    c.drawString(col2_x, cursor, "Date: _______________")

    # ── Finalise ──────────────────────────────────────────────────────────────
    c.showPage()
    c.save()

    # Cleanup temp logo file if created
    if logo_path and logo_path.startswith(tempfile.gettempdir()):
        try:
            os.unlink(logo_path)
        except Exception:
            pass

    pdf_bytes = buffer.getvalue()
    buffer.close()
    return pdf_bytes