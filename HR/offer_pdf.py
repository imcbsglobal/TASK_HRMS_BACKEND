from io import BytesIO
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import cm
from reportlab.lib import colors
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT, TA_JUSTIFY


def generate_offer_letter_pdf(offer, candidate):
    """
    Generate a professional offer letter PDF using reportlab.
    Returns bytes of the generated PDF.
    """
    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        rightMargin=2.5 * cm,
        leftMargin=2.5 * cm,
        topMargin=2 * cm,
        bottomMargin=2 * cm,
    )

    styles = getSampleStyleSheet()

    # Custom styles
    company_style = ParagraphStyle(
        "CompanyName",
        parent=styles["Normal"],
        fontSize=22,
        fontName="Helvetica-Bold",
        textColor=colors.HexColor("#1a1a2e"),
        alignment=TA_CENTER,
        spaceAfter=4,
    )

    tagline_style = ParagraphStyle(
        "Tagline",
        parent=styles["Normal"],
        fontSize=10,
        fontName="Helvetica",
        textColor=colors.HexColor("#555555"),
        alignment=TA_CENTER,
        spaceAfter=12,
    )

    heading_style = ParagraphStyle(
        "OfferHeading",
        parent=styles["Normal"],
        fontSize=16,
        fontName="Helvetica-Bold",
        textColor=colors.HexColor("#1a1a2e"),
        alignment=TA_CENTER,
        spaceAfter=6,
    )

    subheading_style = ParagraphStyle(
        "SubHeading",
        parent=styles["Normal"],
        fontSize=10,
        fontName="Helvetica",
        textColor=colors.HexColor("#777777"),
        alignment=TA_CENTER,
        spaceAfter=20,
    )

    body_style = ParagraphStyle(
        "Body",
        parent=styles["Normal"],
        fontSize=10.5,
        fontName="Helvetica",
        textColor=colors.HexColor("#2d2d2d"),
        alignment=TA_JUSTIFY,
        leading=16,
        spaceAfter=10,
    )

    label_style = ParagraphStyle(
        "Label",
        parent=styles["Normal"],
        fontSize=9,
        fontName="Helvetica-Bold",
        textColor=colors.HexColor("#555555"),
        alignment=TA_LEFT,
    )

    value_style = ParagraphStyle(
        "Value",
        parent=styles["Normal"],
        fontSize=10,
        fontName="Helvetica",
        textColor=colors.HexColor("#1a1a2e"),
        alignment=TA_LEFT,
    )

    section_title_style = ParagraphStyle(
        "SectionTitle",
        parent=styles["Normal"],
        fontSize=11,
        fontName="Helvetica-Bold",
        textColor=colors.HexColor("#1a1a2e"),
        spaceAfter=6,
        spaceBefore=12,
    )

    footer_style = ParagraphStyle(
        "Footer",
        parent=styles["Normal"],
        fontSize=9,
        fontName="Helvetica",
        textColor=colors.HexColor("#888888"),
        alignment=TA_CENTER,
    )

    sign_style = ParagraphStyle(
        "Sign",
        parent=styles["Normal"],
        fontSize=10,
        fontName="Helvetica",
        textColor=colors.HexColor("#2d2d2d"),
        alignment=TA_LEFT,
    )

    sign_bold_style = ParagraphStyle(
        "SignBold",
        parent=styles["Normal"],
        fontSize=10,
        fontName="Helvetica-Bold",
        textColor=colors.HexColor("#1a1a2e"),
        alignment=TA_LEFT,
    )

    # Accent color bar (simulated with a colored table row)
    accent_color = colors.HexColor("#4a4aaa")

    # Offer date formatting
    offer_date_str = offer.offer_date.strftime("%B %d, %Y") if offer.offer_date else ""
    joining_date_str = offer.joining_date.strftime("%B %d, %Y") if offer.joining_date else "To be confirmed"

    content = []

    # ── HEADER ──────────────────────────────────────────────────────────────
    content.append(Paragraph(offer.company_name or "Company Name", company_style))
    content.append(Paragraph("Confidential Employment Offer", tagline_style))

    # Accent rule
    content.append(HRFlowable(width="100%", thickness=3, color=accent_color, spaceAfter=14))

    # Offer heading
    content.append(Paragraph("OFFER OF EMPLOYMENT", heading_style))
    content.append(Paragraph(f"Date: {offer_date_str}", subheading_style))

    # ── RECIPIENT ────────────────────────────────────────────────────────────
    content.append(Paragraph(f"Dear <b>{candidate.name}</b>,", body_style))

    intro = (
        f"We are delighted to extend this formal offer of employment to you for the position of "
        f"<b>{offer.position}</b>"
        + (f" in the <b>{offer.department}</b> department" if offer.department else "")
        + f" at <b>{offer.company_name}</b>. After careful consideration of your qualifications and "
        f"interview performance, we are confident that you will be a valuable addition to our team."
    )
    content.append(Paragraph(intro, body_style))

    # ── EMPLOYMENT DETAILS TABLE ──────────────────────────────────────────────
    content.append(Paragraph("Employment Details", section_title_style))
    content.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor("#dddddd"), spaceAfter=8))

    details_data = [
        [Paragraph("Position / Role", label_style), Paragraph(offer.position, value_style)],
        [Paragraph("Department", label_style), Paragraph(offer.department or "—", value_style)],
        [Paragraph("Offered Salary (CTC)", label_style), Paragraph(offer.salary, value_style)],
        [Paragraph("Date of Joining", label_style), Paragraph(joining_date_str, value_style)],
        [Paragraph("Work Location", label_style), Paragraph(offer.work_location or "To be confirmed", value_style)],
        [Paragraph("Working Hours", label_style), Paragraph(offer.work_hours or "9:00 AM – 6:00 PM", value_style)],
    ]

    details_table = Table(
        details_data,
        colWidths=[5 * cm, 11 * cm],
        hAlign="LEFT",
    )
    details_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#f4f4fb")),
        ("BACKGROUND", (1, 0), (1, -1), colors.white),
        ("ROWBACKGROUNDS", (0, 0), (-1, -1), [colors.HexColor("#f9f9fd"), colors.white]),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#e0e0e0")),
        ("LEFTPADDING", (0, 0), (-1, -1), 10),
        ("RIGHTPADDING", (0, 0), (-1, -1), 10),
        ("TOPPADDING", (0, 0), (-1, -1), 7),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]))
    content.append(details_table)
    content.append(Spacer(1, 14))

    # ── TERMS ─────────────────────────────────────────────────────────────────
    content.append(Paragraph("Terms & Conditions", section_title_style))
    content.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor("#dddddd"), spaceAfter=8))

    terms_text = (
        "This offer is contingent upon the successful completion of background verification, "
        "reference checks, and submission of required documents prior to your joining date. "
        "By accepting this offer, you agree to abide by the company's policies, code of conduct, "
        "and all applicable laws and regulations."
    )
    content.append(Paragraph(terms_text, body_style))

    # ── ADDITIONAL BENEFITS ───────────────────────────────────────────────────
    if offer.additional_benefits:
        content.append(Paragraph("Additional Benefits & Notes", section_title_style))
        content.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor("#dddddd"), spaceAfter=8))
        content.append(Paragraph(offer.additional_benefits, body_style))

    # ── ACCEPTANCE ────────────────────────────────────────────────────────────
    content.append(Spacer(1, 10))
    content.append(Paragraph(
        "We kindly request you to confirm your acceptance of this offer by signing and returning "
        "a copy of this letter within <b>7 days</b> of receipt. We look forward to welcoming you "
        "to our team and wish you a rewarding career ahead.",
        body_style
    ))

    # ── SIGNATURE BLOCK ───────────────────────────────────────────────────────
    content.append(Spacer(1, 24))
    sig_data = [
        [
            Paragraph("For " + (offer.company_name or "Company"), sign_style),
            Paragraph("Accepted by Candidate", sign_style),
        ],
        [Spacer(1, 30), Spacer(1, 30)],
        [
            Paragraph("____________________________", sign_style),
            Paragraph("____________________________", sign_style),
        ],
        [
            Paragraph(offer.hr_name or "Authorized Signatory", sign_bold_style),
            Paragraph(candidate.name, sign_bold_style),
        ],
        [
            Paragraph(offer.hr_designation or "HR Manager", sign_style),
            Paragraph("Date: ________________", sign_style),
        ],
    ]
    sig_table = Table(sig_data, colWidths=[8 * cm, 8 * cm], hAlign="LEFT")
    sig_table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
    ]))
    content.append(sig_table)

    # ── FOOTER ────────────────────────────────────────────────────────────────
    content.append(Spacer(1, 20))
    content.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor("#dddddd"), spaceAfter=8))
    content.append(Paragraph(
        f"{offer.company_name} · Confidential · This document is intended solely for {candidate.name}",
        footer_style,
    ))

    doc.build(content)
    pdf_bytes = buffer.getvalue()
    buffer.close()
    return pdf_bytes