import re


SECTION_HEADERS = {
    "summary", "objective", "profile", "experience", "work experience",
    "employment", "education", "academic", "skills", "technical skills",
    "projects", "certifications", "languages", "interests", "declaration",
}

LOCATION_LABELS = (
    "location", "address", "current location", "residence", "city",
)

ROLE_LABELS = (
    "role", "position", "job title", "designation", "current role",
    "current position", "applied for",
)

EDUCATION_PATTERNS = [
    r"\b(?:b\.?\s?tech|bachelor\s+of\s+technology|btech)\b[^,\n]*",
    r"\b(?:m\.?\s?tech|master\s+of\s+technology|mtech)\b[^,\n]*",
    r"\b(?:b\.?\s?e\.?|bachelor\s+of\s+engineering)\b[^,\n]*",
    r"\b(?:m\.?\s?e\.?|master\s+of\s+engineering)\b[^,\n]*",
    r"\b(?:b\.?\s?sc|bachelor\s+of\s+science|bsc)\b[^,\n]*",
    r"\b(?:m\.?\s?sc|master\s+of\s+science|msc)\b[^,\n]*",
    r"\b(?:b\.?\s?com|bachelor\s+of\s+commerce|bcom)\b[^,\n]*",
    r"\b(?:m\.?\s?com|master\s+of\s+commerce|mcom)\b[^,\n]*",
    r"\b(?:b\.?\s?ca|bachelor\s+of\s+computer\s+applications|bca)\b[^,\n]*",
    r"\b(?:m\.?\s?ca|master\s+of\s+computer\s+applications|mca)\b[^,\n]*",
    r"\b(?:mba|master\s+of\s+business\s+administration)\b[^,\n]*",
    r"\b(?:diploma|degree|graduate|post\s+graduate)\b[^,\n]*",
]

LOCATION_HINTS = [
    "kozhikode", "calicut", "kerala", "ernakulam", "kochi", "thrissur",
    "trivandrum", "thiruvananthapuram", "malappuram", "kannur", "bangalore",
    "bengaluru", "chennai", "hyderabad", "mumbai", "pune", "delhi", "india",
]

SKILLS = [
    "python", "django", "flask", "fastapi", "react", "javascript",
    "typescript", "node", "express", "html", "css", "tailwind",
    "bootstrap", "sql", "mysql", "postgresql", "mongodb", "rest api",
    "docker", "aws", "azure", "git", "java", "spring", "php", "laravel",
    "figma", "excel", "power bi", "communication", "recruitment",
]


def extract_text(file):
    text = ""

    if file.name.lower().endswith(".pdf"):
        import pdfplumber

        with pdfplumber.open(file) as pdf:
            for page in pdf.pages:
                text += (page.extract_text(x_tolerance=1, y_tolerance=3) or "") + "\n"
    elif file.name.lower().endswith(".docx"):
        import docx

        doc = docx.Document(file)
        for paragraph in doc.paragraphs:
            text += paragraph.text + "\n"

    return _normalize_text(text)


def extract_fields(text):
    normalized = _normalize_text(text)
    lower = normalized.lower()
    lines = _meaningful_lines(normalized)

    email = re.search(r"[\w.+-]+@[\w.-]+\.\w+", normalized)
    phone = re.search(r"(?:\+?\d{1,3}[\s-]?)?(?:\d[\s-]?){9,14}", normalized)

    return {
        "name": _extract_name(lines, email.group() if email else ""),
        "email": email.group() if email else "",
        "phone": _clean_phone(phone.group()) if phone else "",
        "location": _extract_location(normalized, lower, lines),
        "role": _extract_role(normalized, lower, lines),
        "experience": _extract_experience(normalized, lower),
        "education": _extract_education(normalized, lower),
        "skills": _extract_skills(lower),
    }


def _normalize_text(text):
    text = text.replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _meaningful_lines(text):
    return [
        line.strip(" -|•\t")
        for line in text.split("\n")
        if line.strip(" -|•\t")
    ]


def _clean_phone(value):
    value = re.sub(r"\s+", " ", value).strip()
    return value.strip(" ,;|")


def _clean_value(value):
    value = re.sub(r"\s+", " ", value or "").strip(" -:|,;")
    return value[:120].strip()


def _title(value):
    keep_upper = {"BCA", "MCA", "MBA", "BSC", "MSC", "BTECH", "MTECH", "BE", "ME"}
    words = []
    for word in _clean_value(value).split():
        compact = re.sub(r"[^A-Za-z]", "", word).upper()
        words.append(compact if compact in keep_upper else word.capitalize())
    return " ".join(words)


def _extract_name(lines, email):
    ignored = ("resume", "curriculum vitae", "cv", "profile")
    for line in lines[:8]:
        clean = _clean_value(line)
        low = clean.lower()
        if not clean or any(term == low for term in ignored):
            continue
        if email and email.lower() in low:
            continue
        if re.search(r"@|\d{4,}|www\.|linkedin|github|http", low):
            continue
        if len(clean.split()) <= 5 and re.fullmatch(r"[A-Za-z][A-Za-z .'-]+", clean):
            return _title(clean)
    return ""


def _extract_labeled_value(text, labels):
    label_expr = "|".join(re.escape(label) for label in labels)
    match = re.search(
        rf"(?im)^\s*(?:{label_expr})\s*[:\-]\s*(.+)$",
        text,
    )
    return _clean_value(match.group(1)) if match else ""


def _extract_location(text, lower, lines):
    labeled = _extract_labeled_value(text, LOCATION_LABELS)
    if labeled:
        return _title(labeled)

    for line in lines[:12]:
        low = line.lower()
        if any(hint in low for hint in LOCATION_HINTS) and not re.search(r"@|http|linkedin|github", low):
            parts = re.split(r"[|•]", line)
            for part in parts:
                if any(hint in part.lower() for hint in LOCATION_HINTS):
                    return _title(part)
            return _title(line)

    city_match = re.search(r"\b([A-Z][a-z]+(?:\s*,\s*[A-Z][a-z]+){1,2})\b", text)
    return _title(city_match.group(1)) if city_match else ""


def _extract_role(text, lower, lines):
    labeled = _extract_labeled_value(text, ROLE_LABELS)
    if labeled:
        return _title(labeled)

    role_match = re.search(
        r"(?i)\b(?:software|frontend|front end|backend|back end|full stack|"
        r"python|django|react|hr|account|sales|marketing|business|data|ui/?ux)"
        r"[\w\s/-]{0,45}\b(?:developer|engineer|manager|executive|analyst|designer|consultant|intern|trainee)\b",
        text,
    )
    if role_match:
        return _title(role_match.group(0))

    for line in lines[1:8]:
        low = line.lower()
        if low in SECTION_HEADERS:
            continue
        if re.search(r"\b(developer|engineer|manager|executive|analyst|designer|consultant|intern|trainee)\b", low):
            return _title(line)

    return ""


def _extract_experience(text, lower):
    total_match = re.search(
        r"(?i)\b(?:total\s+)?experience\s*[:\-]?\s*"
        r"(\d+(?:\.\d+)?)\s*\+?\s*(years?|yrs?)(?:\s+and\s+(\d+)\s*(months?|mos?))?",
        text,
    )
    if total_match:
        years = total_match.group(1)
        months = total_match.group(3)
        return f"{years} years" + (f" {months} months" if months else "")

    exp_match = re.search(
        r"(?i)\b(\d+(?:\.\d+)?)\s*\+?\s*(years?|yrs?)\s+"
        r"(?:of\s+)?(?:professional\s+)?experience\b",
        text,
    )
    if exp_match:
        return f"{exp_match.group(1)} years"

    month_match = re.search(r"(?i)\b(\d+)\s*(months?|mos?)\s+(?:of\s+)?experience\b", text)
    if month_match:
        return f"{month_match.group(1)} months"

    return ""


def _extract_education(text, lower):
    education_section = _extract_section(text, "education")
    search_text = education_section or text
    search_lower = search_text.lower()

    for pattern in EDUCATION_PATTERNS:
        match = re.search(pattern, search_lower)
        if match:
            source = search_text[match.start():match.end()]
            return _title(source)

    return ""


def _extract_section(text, section_name):
    pattern = re.compile(rf"(?im)^\s*{re.escape(section_name)}\s*$")
    match = pattern.search(text)
    if not match:
        return ""

    start = match.end()
    next_header = re.search(
        r"(?im)^\s*(?:summary|objective|profile|experience|work experience|employment|"
        r"education|academic|skills|technical skills|projects|certifications|languages|"
        r"interests|declaration)\s*$",
        text[start:],
    )
    end = start + next_header.start() if next_header else len(text)
    return text[start:end].strip()


def _extract_skills(lower):
    found = []
    for skill in SKILLS:
        if re.search(rf"(?<![a-z0-9]){re.escape(skill)}(?![a-z0-9])", lower):
            found.append(skill.title())
    return found
