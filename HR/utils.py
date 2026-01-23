import pdfplumber
import docx
import re

import re
import pdfplumber
import docx

def extract_text(file):
    text = ""

    if file.name.lower().endswith(".pdf"):
        with pdfplumber.open(file) as pdf:
            for page in pdf.pages:
                text += page.extract_text() or ""

    elif file.name.lower().endswith(".docx"):
        doc = docx.Document(file)
        for p in doc.paragraphs:
            text += p.text + "\n"

    return text.lower()


def extract_fields(text):
    email = re.search(r"[\w\.-]+@[\w\.-]+", text)
    phone = re.search(r"\+?\d[\d\s\-]{8,}", text)

    # Name (first line heuristic)
    lines = text.split("\n")
    name = lines[0].strip().title() if lines else ""

    # Location
    location = ""
    loc_match = re.search(r"(location|address)\s*[:\-]?\s*(.+)", text)
    if loc_match:
        location = loc_match.group(2).strip().title()

    # Experience
    exp_match = re.search(r"(\d+\+?\s*(years|yrs))", text)
    experience = exp_match.group(1) if exp_match else ""

    # Education
    edu_match = re.search(
        r"(b\.?tech|m\.?tech|bachelor|master|degree|bsc|msc|be|me)",
        text
    )
    education = edu_match.group(0).upper() if edu_match else ""

    # Skills
    SKILLS = [
        "python", "django", "react", "javascript", "sql", "postgresql",
        "html", "css", "rest api", "docker", "aws"
    ]
    skills = [s.title() for s in SKILLS if s in text]

    return {
        "name": name,
        "email": email.group() if email else "",
        "phone": phone.group() if phone else "",
        "location": location,
        "experience": experience,
        "education": education,
        "skills": skills,
    }

