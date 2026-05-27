from io import BytesIO

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import cm
from reportlab.pdfgen import canvas
from reportlab.platypus import Paragraph


PAGE_W, PAGE_H = A4


def _draw_paragraph(c_obj, text, x, y, width, style):
    paragraph = Paragraph(text or '', style)
    _, height = paragraph.wrapOn(c_obj, width, 9999)
    paragraph.drawOn(c_obj, x, y - height)
    return height


def generate_experience_certificate_pdf(certificate, company=None):
    company = company or {}
    company_name = company.get('name') or 'Company'
    company_address = company.get('address') or ''

    buffer = BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)
    c.setTitle(f'Experience Certificate - {certificate.employee_name}')

    margin = 2.0 * cm
    left = margin
    right = PAGE_W - margin
    content_w = right - left

    c.setFillColor(colors.white)
    c.rect(0, 0, PAGE_W, PAGE_H, fill=1, stroke=0)
    c.setStrokeColor(colors.black)
    c.setLineWidth(1.2)
    c.rect(1.2 * cm, 1.2 * cm, PAGE_W - 2.4 * cm, PAGE_H - 2.4 * cm, fill=0, stroke=1)
    c.setLineWidth(0.4)
    c.rect(1.45 * cm, 1.45 * cm, PAGE_W - 2.9 * cm, PAGE_H - 2.9 * cm, fill=0, stroke=1)

    c.setFillColor(colors.black)
    c.setFont('Helvetica-Bold', 17)
    c.drawCentredString(PAGE_W / 2, PAGE_H - 2.8 * cm, company_name.upper())

    if company_address:
        c.setFont('Helvetica', 8)
        c.drawCentredString(PAGE_W / 2, PAGE_H - 3.25 * cm, company_address[:120])

    c.setLineWidth(0.8)
    c.line(left, PAGE_H - 3.75 * cm, right, PAGE_H - 3.75 * cm)

    c.setFont('Helvetica-Bold', 18)
    c.drawCentredString(PAGE_W / 2, PAGE_H - 5.2 * cm, 'EXPERIENCE CERTIFICATE')

    c.setFont('Helvetica', 8.5)
    c.drawString(left, PAGE_H - 6.15 * cm, f'Certificate No: {certificate.certificate_number}')
    c.drawRightString(right, PAGE_H - 6.15 * cm, f'Date: {certificate.issue_date.strftime("%d %B %Y")}')

    body_style = ParagraphStyle(
        'Body',
        fontName='Helvetica',
        fontSize=10.5,
        leading=17,
        alignment=TA_JUSTIFY,
        textColor=colors.black,
    )
    center_style = ParagraphStyle(
        'Center',
        fontName='Helvetica',
        fontSize=10.5,
        leading=16,
        alignment=TA_CENTER,
        textColor=colors.black,
    )

    start = certificate.start_date.strftime('%d %B %Y')
    end = certificate.end_date.strftime('%d %B %Y')
    department = f' in the {certificate.department} department' if certificate.department else ''
    employment_type = f' as a {certificate.employment_type}' if certificate.employment_type else ''

    body = (
        f'This is to certify that <b>{certificate.employee_name}</b> '
        f'({certificate.employee_code}) was employed with <b>{company_name}</b> '
        f'from <b>{start}</b> to <b>{end}</b>{employment_type}. '
        f'During this period, the employee served as <b>{certificate.designation}</b>'
        f'{department}.'
    )

    cursor = PAGE_H - 7.35 * cm
    height = _draw_paragraph(c, body, left, cursor, content_w, body_style)
    cursor -= height + 0.8 * cm

    if certificate.responsibilities:
        responsibilities = (
            f'The major responsibilities handled during the employment period were: '
            f'{certificate.responsibilities}'
        )
        height = _draw_paragraph(c, responsibilities, left, cursor, content_w, body_style)
        cursor -= height + 0.8 * cm

    conduct = certificate.conduct or 'good'
    remarks = certificate.remarks or (
        'We found the employee to be sincere, dedicated, and professional in assigned duties.'
    )
    closing = (
        f'The employee maintained <b>{conduct}</b> conduct throughout the tenure. '
        f'{remarks} We wish the employee every success in future endeavors.'
    )
    height = _draw_paragraph(c, closing, left, cursor, content_w, body_style)
    cursor -= height + 1.1 * cm

    _draw_paragraph(c, 'This certificate is issued upon request for official use.', left, cursor, content_w, center_style)

    sig_y = 4.4 * cm
    c.setLineWidth(0.7)
    c.line(right - 5.5 * cm, sig_y, right - 1.0 * cm, sig_y)
    c.setFont('Helvetica-Bold', 9.5)
    c.drawString(right - 5.5 * cm, sig_y - 0.45 * cm, certificate.signatory_name or 'Authorized Signatory')
    c.setFont('Helvetica', 8.5)
    c.drawString(right - 5.5 * cm, sig_y - 0.85 * cm, certificate.signatory_designation or 'HR Manager')

    c.showPage()
    c.save()
    pdf_bytes = buffer.getvalue()
    buffer.close()
    return pdf_bytes
