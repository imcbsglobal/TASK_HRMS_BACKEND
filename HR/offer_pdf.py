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
from PIL import Image

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
            if "svg" in header:
                return None  # ReportLab can't render SVG directly
            raw = base64.b64decode(b64data)

            # Browser uploads are compressed to WebP in the frontend; convert
            # everything to PNG so ReportLab can draw it reliably.
            image = Image.open(BytesIO(raw))
            if image.mode not in ("RGB", "RGBA"):
                image = image.convert("RGBA")
            tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".png")
            image.save(tmp, format="PNG")
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
    footer = f"{company_name}  -  Confidential  -  Prepared for {candidate_name}"
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
# Legacy generator
# ─────────────────────────────────────────────────────────────────────────────
def _legacy_generate_offer_letter_pdf(offer, candidate, company: dict = None):
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
    company = {**company, "name": company_name}
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
        if addr:    info_parts.append(f"Location: {addr}")
        if website: info_parts.append(f"Website: {website}")
        c.setFont("Helvetica", 7.5)
        c.setFillColor(pal["muted"])
        c.drawCentredString(LEFT_X + CONTENT_W / 2, cursor - info_h + 0.24 * cm, "  -  ".join(info_parts))
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


def _bw_palette():
    return {
        "black": colors.black,
        "charcoal": colors.HexColor("#2B2B2B"),
        "dark_gray": colors.HexColor("#5E5E5E"),
        "mid_gray": colors.HexColor("#9A9A9A"),
        "line": colors.HexColor("#D8D8D8"),
        "pale": colors.HexColor("#F2F2F2"),
        "white": colors.white,
    }


def _draw_polygon(c_obj, points, fill_color):
    c_obj.setFillColor(fill_color)
    path = c_obj.beginPath()
    path.moveTo(*points[0])
    for point in points[1:]:
        path.lineTo(*point)
    path.close()
    c_obj.drawPath(path, fill=1, stroke=0)


def _draw_bw_chrome(c_obj, pal):
    margin = 1.05 * cm

    c_obj.setFillColor(pal["white"])
    c_obj.rect(0, 0, PAGE_W, PAGE_H, fill=1, stroke=0)

    c_obj.setStrokeColor(colors.HexColor("#C8CDD2"))
    c_obj.setLineWidth(1)
    c_obj.roundRect(
        margin,
        0.7 * cm,
        PAGE_W - margin * 2,
        PAGE_H - 1.4 * cm,
        10,
        fill=0,
        stroke=1,
    )

    # Light paper folds around the page, like the reference but monochrome.
    facets = [
        [(margin, PAGE_H - 0.7 * cm), (PAGE_W - margin, PAGE_H - 0.7 * cm), (PAGE_W - 3.5 * cm, PAGE_H - 3.8 * cm), (3.2 * cm, PAGE_H - 3.8 * cm)],
        [(margin, 0.7 * cm), (3.6 * cm, 0.7 * cm), (margin, 3.2 * cm)],
        [(PAGE_W - margin, 0.7 * cm), (PAGE_W - 4.2 * cm, 0.7 * cm), (PAGE_W - margin, 3.7 * cm)],
        [(PAGE_W - margin, PAGE_H - 0.7 * cm), (PAGE_W - margin, PAGE_H - 4.2 * cm), (PAGE_W - 3.3 * cm, PAGE_H - 0.7 * cm)],
    ]
    for facet in facets:
        _draw_polygon(c_obj, facet, pal["pale"])

    _draw_polygon(c_obj, [(margin, PAGE_H - 0.7 * cm), (4.1 * cm, PAGE_H - 0.7 * cm), (2.4 * cm, PAGE_H - 7.2 * cm), (margin, PAGE_H - 9.1 * cm)], pal["black"])
    _draw_polygon(c_obj, [(margin, PAGE_H - 2.0 * cm), (3.0 * cm, PAGE_H - 1.0 * cm), (1.4 * cm, PAGE_H - 5.0 * cm), (margin, PAGE_H - 6.0 * cm)], pal["charcoal"])
    _draw_polygon(c_obj, [(2.6 * cm, PAGE_H - 1.0 * cm), (3.25 * cm, PAGE_H - 1.0 * cm), (1.6 * cm, PAGE_H - 4.2 * cm), (1.15 * cm, PAGE_H - 4.2 * cm)], pal["mid_gray"])

    _draw_polygon(c_obj, [(PAGE_W - margin, 0.7 * cm), (PAGE_W - 4.2 * cm, 0.7 * cm), (PAGE_W - 2.3 * cm, 6.3 * cm), (PAGE_W - margin, 8.1 * cm)], pal["black"])
    _draw_polygon(c_obj, [(PAGE_W - 2.15 * cm, 1.5 * cm), (PAGE_W - 3.0 * cm, 1.5 * cm), (PAGE_W - 1.25 * cm, 4.8 * cm), (PAGE_W - margin, 4.8 * cm)], pal["mid_gray"])
    _draw_polygon(c_obj, [(PAGE_W - 3.45 * cm, 2.8 * cm), (PAGE_W - 2.85 * cm, 2.8 * cm), (PAGE_W - 1.5 * cm, 6.4 * cm), (PAGE_W - 2.1 * cm, 6.4 * cm)], pal["charcoal"])

    c_obj.setStrokeColor(pal["dark_gray"])
    c_obj.setLineWidth(0.7)
    for x, y in [
        (2.45 * cm, PAGE_H - 1.55 * cm),
        (2.8 * cm, PAGE_H - 2.4 * cm),
        (2.1 * cm, PAGE_H - 8.0 * cm),
        (PAGE_W - 3.0 * cm, 2.1 * cm),
        (PAGE_W - 2.35 * cm, 6.4 * cm),
    ]:
        c_obj.line(x, y, x + 0.35 * cm, y + 1.0 * cm)


def _draw_letterhead(c_obj, company, logo_path, pal, center_x, top_y):
    logo_size = 1.55 * cm
    name = company.get("name") or "Company"
    address = company.get("address") or ""

    if logo_path:
        try:
            c_obj.drawImage(
                logo_path,
                center_x - 4.05 * cm,
                top_y - logo_size + 0.08 * cm,
                width=logo_size,
                height=logo_size,
                preserveAspectRatio=True,
                mask="auto",
            )
        except Exception:
            logo_path = None

    if not logo_path:
        c_obj.setStrokeColor(pal["black"])
        c_obj.setLineWidth(3)
        c_obj.circle(center_x - 3.25 * cm, top_y - 0.65 * cm, 0.55 * cm, fill=0, stroke=1)
        c_obj.setFont("Helvetica-Bold", 15)
        c_obj.setFillColor(pal["black"])
        c_obj.drawCentredString(center_x - 3.25 * cm, top_y - 0.82 * cm, name[:1].upper())

    c_obj.setFillColor(pal["black"])
    c_obj.setFont("Helvetica-Bold", 16)
    c_obj.drawString(center_x - 2.3 * cm, top_y - 0.47 * cm, name.upper())

    if address:
        c_obj.setFillColor(pal["charcoal"])
        c_obj.setFont("Helvetica", 8)
        c_obj.drawString(center_x - 2.3 * cm, top_y - 0.88 * cm, address)


def _draw_footer_contacts(c_obj, company, pal, left_x, right_x):
    y = 2.05 * cm
    parts = []
    if company.get("phone"):
        parts.append(company["phone"])
    if company.get("email"):
        parts.append(company["email"])
    if company.get("website"):
        parts.append(company["website"])

    if not parts:
        return

    usable_w = right_x - left_x
    gap = usable_w / len(parts)
    c_obj.setFillColor(pal["black"])
    c_obj.setFont("Helvetica", 7.5)
    for index, part in enumerate(parts):
        x = left_x + gap * index + gap / 2
        c_obj.circle(x - 0.32 * cm, y + 0.03 * cm, 0.10 * cm, fill=1, stroke=0)
        c_obj.drawCentredString(x + 0.45 * cm, y, part)


def generate_offer_letter_pdf(offer, candidate, company: dict = None):
    """
    Generate a black-and-white job offer letter PDF using the existing data.
    This definition intentionally overrides the earlier template only.
    """
    if company is None:
        company = {}

    company_name = company.get("name") or getattr(offer, "company_name", None) or "Company"
    company = {**company, "name": company_name}
    logo_path = resolve_logo(company.get("logo"))
    pal = _bw_palette()

    offer_date_str = offer.offer_date.strftime("%B %d, %Y") if getattr(offer, "offer_date", None) else ""
    joining_date_str = offer.joining_date.strftime("%B %d, %Y") if getattr(offer, "joining_date", None) else "To be confirmed"

    buffer = BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)
    c.setTitle(f"Offer Letter - {candidate.name}")

    left_x = 3.05 * cm
    right_x = PAGE_W - 2.35 * cm
    content_w = right_x - left_x

    _draw_bw_chrome(c, pal)
    _draw_letterhead(c, company, logo_path, pal, PAGE_W / 2, PAGE_H - 3.0 * cm)

    c.setStrokeColor(pal["black"])
    c.setLineWidth(1.5)
    c.line(left_x - 0.65 * cm, PAGE_H - 5.35 * cm, right_x, PAGE_H - 5.35 * cm)

    c.setFillColor(pal["black"])
    c.setFont("Helvetica-Bold", 19)
    c.drawCentredString(PAGE_W / 2, PAGE_H - 6.25 * cm, "JOB OFFER LETTER")

    cursor = PAGE_H - 7.05 * cm
    c.setFont("Helvetica-Bold", 8.5)
    c.drawString(left_x, cursor, "To:")
    c.setFont("Helvetica", 8.5)
    c.drawString(left_x, cursor - 0.36 * cm, candidate.name)
    candidate_location = getattr(candidate, "location", None)
    if candidate_location:
        c.drawString(left_x, cursor - 0.72 * cm, candidate_location)

    c.setFont("Helvetica", 8.5)
    c.drawRightString(right_x, cursor, offer_date_str)
    cursor -= 1.55 * cm

    body_style = ParagraphStyle(
        "BWBody",
        fontName="Helvetica",
        fontSize=8.7,
        textColor=pal["black"],
        leading=12.2,
        alignment=TA_JUSTIFY,
    )

    c.setFont("Helvetica", 8.7)
    c.drawString(left_x, cursor, f"Dear {candidate.name},")
    cursor -= 0.55 * cm

    dept_clause = f" in the {offer.department} department" if getattr(offer, "department", None) else ""
    intro = (
        f"We are pleased to extend an offer for the position of <b>{getattr(offer, 'position', '')}</b>"
        f"{dept_clause} at <b>{company_name}</b>. Your skills, communication ability, and dedication "
        "to people development will make a strong contribution to our team."
    )
    h = draw_paragraph(c, intro, left_x, cursor, content_w, body_style)
    cursor -= h + 0.45 * cm

    c.setFont("Helvetica", 8.7)
    c.setFillColor(pal["black"])
    c.drawString(left_x, cursor, "Offer Details:")
    cursor -= 0.36 * cm

    currency = company.get("currency", "")
    salary_value = getattr(offer, "salary", "") or "Based on company salary structure and internal guidelines"
    salary_display = f"{currency} {salary_value}".strip() if currency and getattr(offer, "salary", "") else salary_value
    employment_type = "Full-Time"
    benefits = getattr(offer, "additional_benefits", None) or "Health coverage, paid leave, and employee development programs"
    details = [
        ("Position", getattr(offer, "position", "") or "To be confirmed"),
        ("Start Date", joining_date_str),
        ("Work Location", getattr(offer, "work_location", None) or "Corporate Headquarters"),
        ("Employment Type", employment_type),
        ("Compensation", salary_display),
        ("Benefits", benefits),
    ]

    bullet_style = ParagraphStyle(
        "BWBullet",
        fontName="Helvetica",
        fontSize=8.4,
        textColor=pal["black"],
        leading=11,
        leftIndent=0.3 * cm,
        firstLineIndent=-0.2 * cm,
    )
    for label, value in details:
        text = f"<bullet>&bull;</bullet><b>{label}:</b> {value}"
        p = Paragraph(text, bullet_style, bulletText="*")
        _, h = p.wrapOn(c, content_w, 999)
        p.drawOn(c, left_x + 0.2 * cm, cursor - h)
        cursor -= h

    cursor -= 0.45 * cm
    terms = (
        "This offer is contingent upon the completion of onboarding documentation in accordance "
        "with company policy. Please confirm your acceptance by replying to this letter within "
        "7 days of receipt."
    )
    h = draw_paragraph(c, terms, left_x, cursor, content_w, body_style)
    cursor -= h + 0.45 * cm

    closing = (
        "We look forward to welcoming you and supporting your growth within our organization."
    )
    h = draw_paragraph(c, closing, left_x, cursor, content_w, body_style)
    cursor -= h + 0.75 * cm

    c.setFont("Helvetica-Bold", 8.7)
    c.drawString(left_x, cursor, "Warm regards,")
    cursor -= 1.15 * cm

    hr_name = getattr(offer, "hr_name", None) or "Authorized Signatory"
    hr_desig = getattr(offer, "hr_designation", None) or "HR Manager"
    c.setStrokeColor(pal["black"])
    c.setLineWidth(0.7)
    c.line(left_x, cursor + 0.35 * cm, left_x + 3.1 * cm, cursor + 0.35 * cm)
    c.setFont("Helvetica", 8.5)
    c.drawString(left_x, cursor, hr_name)
    c.drawString(left_x, cursor - 0.36 * cm, hr_desig)

    _draw_footer_contacts(c, company, pal, left_x, right_x)

    c.showPage()
    c.save()

    if logo_path and logo_path.startswith(tempfile.gettempdir()):
        try:
            os.unlink(logo_path)
        except Exception:
            pass

    pdf_bytes = buffer.getvalue()
    buffer.close()
    return pdf_bytes
