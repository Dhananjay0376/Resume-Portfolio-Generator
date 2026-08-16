"""
Shared pipeline for the AI-Assisted Resume Portfolio Generator.

Both entry points use this module so there is exactly one implementation of
the graded workflow (clean -> prompt -> Gemini -> JSON -> render):

  main.py  - CLI: reads resume.txt directly (the flow described in the brief)
  app.py   - Web: lets you upload a resume PDF, which is converted to text
             and written into resume.txt, then runs this SAME pipeline

Every function here raises PortfolioError on failure instead of exiting the
process, so each front end can decide how to present the error (main.py
prints it and exits; app.py shows it on the upload page).
"""

import json
import os
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from google import genai
from google.genai import types
from google.genai import errors as genai_errors

# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent
RESUME_FILE = BASE_DIR / "resume.txt"
TEMPLATE_FILE = "template.html"
STYLE_FILE = BASE_DIR / "style.css"
OUTPUT_FILE = BASE_DIR / "portfolio.html"

MIN_RESUME_LENGTH = 200              # characters, after cleaning, minimum accepted
DEFAULT_MODEL = "gemini-2.5-flash"   # override with GEMINI_MODEL in .env if your instructor approves a different model

# The shape every portfolio record must have. Used to fill in safe empty
# values whenever Gemini's JSON is missing a key or the key is the wrong type.
EMPTY_PORTFOLIO = {
    "name": "",
    "headline": "",
    "summary": "",
    "skills": [],
    "education": [],   # [{institution, qualification, duration}]
    "experience": [],  # [{role, organization, duration, description}]
    "projects": [],    # [{title, description, technologies:[]}]
    "achievements": [],
    "contact": {"email": "", "phone": "", "linkedin": "", "github": "", "links": []},
}


class PortfolioError(Exception):
    """Raised for any handled failure in the pipeline. Message is safe to show to a user."""


# --------------------------------------------------------------------------
# Step 1: Clean + validate resume text
# --------------------------------------------------------------------------
def clean_resume_text(text: str) -> str:
    """Strip unnecessary spaces and drop blank lines before sending text to Gemini."""
    lines = [line.strip() for line in text.splitlines()]
    non_empty_lines = [line for line in lines if line]
    return "\n".join(non_empty_lines)


def validate_resume_text(cleaned_text: str) -> None:
    if not cleaned_text.strip():
        raise PortfolioError("The resume text is empty. Add resume content and try again.")
    if len(cleaned_text) < MIN_RESUME_LENGTH:
        raise PortfolioError(
            f"The resume text only has {len(cleaned_text)} characters after cleaning "
            f"(minimum required is {MIN_RESUME_LENGTH}). Add more resume detail "
            "(summary, skills, experience, projects) and try again."
        )


def load_resume_text() -> str:
    """Read, clean, and validate resume.txt. Used by both the CLI and the web app
    (the web app writes freshly-extracted PDF text into resume.txt first, then
    calls this exact same function, so both flows genuinely read from the file)."""
    if not RESUME_FILE.exists():
        raise PortfolioError(
            f"'{RESUME_FILE.name}' was not found in {BASE_DIR}. "
            "Create a resume.txt file next to main.py and add resume content."
        )
    raw = RESUME_FILE.read_text(encoding="utf-8", errors="ignore")
    cleaned = clean_resume_text(raw)
    validate_resume_text(cleaned)
    return cleaned


def write_resume_text(text: str) -> None:
    """Overwrite resume.txt with new content (used by the web upload flow)."""
    RESUME_FILE.write_text(text, encoding="utf-8")


# --------------------------------------------------------------------------
# Step 2: Build a controlled, extraction-only prompt
# --------------------------------------------------------------------------
def build_prompt(resume_text: str) -> str:
    return f"""You extract structured portfolio data from ONE resume.

STRICT RULES:
- Use only information explicitly present in the resume text below.
- Do NOT invent or guess skills, experience, projects, achievements, companies, dates, or links.
- If a piece of information is missing, use an empty string "" or an empty list [], never a
  placeholder such as "N/A", "Not specified", or "Unknown".
- Keep the professional summary concise (2-3 sentences) and strictly factual.
- Return valid JSON only. No markdown, no code fences, no explanation, no extra text.

Return JSON matching exactly this structure and key names:
{{
  "name": "string - full name",
  "headline": "string - short professional identity (e.g. role or field of study)",
  "summary": "string - concise, factual professional summary",
  "skills": ["string", "..."],
  "education": [
    {{"institution": "string", "qualification": "string", "duration": "string"}}
  ],
  "experience": [
    {{"role": "string", "organization": "string", "duration": "string", "description": "string"}}
  ],
  "projects": [
    {{"title": "string", "description": "string", "technologies": ["string", "..."]}}
  ],
  "achievements": ["string", "..."],
  "contact": {{
    "email": "string", "phone": "string", "linkedin": "string", "github": "string",
    "links": ["string", "..."]
  }}
}}

RESUME TEXT:
\"\"\"
{resume_text}
\"\"\"
"""


# --------------------------------------------------------------------------
# Step 3: Call Gemini (handle every failure mode without crashing)
# --------------------------------------------------------------------------
def call_gemini(prompt: str) -> str:
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise PortfolioError(
            "GEMINI_API_KEY is not set. Copy .env.example to .env and add your "
            "Gemini API key from Google AI Studio."
        )

    model_name = os.getenv("GEMINI_MODEL", DEFAULT_MODEL)

    try:
        client = genai.Client(api_key=api_key)
        response = client.models.generate_content(
            model=model_name,
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                temperature=0.2,
            ),
        )
    except genai_errors.ClientError as exc:
        raise PortfolioError(
            f"Gemini rejected the request ({exc}). Check your API key and GEMINI_MODEL in .env."
        ) from exc
    except genai_errors.ServerError as exc:
        raise PortfolioError(f"Gemini's servers had an error ({exc}). Please try again in a moment.") from exc
    except genai_errors.APIError as exc:
        raise PortfolioError(f"Gemini API request failed: {exc}") from exc
    except Exception as exc:  # network issues, timeouts, etc.
        raise PortfolioError(f"Could not reach the Gemini API: {exc}") from exc

    text = (response.text or "").strip()
    if not text:
        raise PortfolioError("Gemini returned an empty response. Please try again.")
    return text


# --------------------------------------------------------------------------
# Step 4: Parse + validate the JSON safely
# --------------------------------------------------------------------------
def parse_portfolio_json(raw_text: str) -> dict:
    # Gemini is asked for raw JSON, but strip accidental ```json fences defensively.
    text = raw_text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
        text = text.strip()

    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise PortfolioError(f"Gemini did not return valid JSON ({exc}). Please try again.") from exc

    if not isinstance(data, dict):
        raise PortfolioError("Gemini's JSON was not a portfolio object. Please try again.")

    return normalize_portfolio(data)


def normalize_portfolio(data: dict) -> dict:
    """Fill in any missing or wrongly-typed fields with safe empty values."""
    portfolio = {}

    for key, empty_value in EMPTY_PORTFOLIO.items():
        value = data.get(key, empty_value)
        if type(value) is not type(empty_value):
            value = empty_value
        portfolio[key] = value

    contact = portfolio["contact"]
    normalized_contact = {}
    for key, empty_value in EMPTY_PORTFOLIO["contact"].items():
        value = contact.get(key, empty_value) if isinstance(contact, dict) else empty_value
        if type(value) is not type(empty_value):
            value = empty_value
        normalized_contact[key] = value
    portfolio["contact"] = normalized_contact

    # Strings only in list fields; drop anything malformed rather than crash the template.
    portfolio["skills"] = [s for s in portfolio["skills"] if isinstance(s, str) and s.strip()]
    portfolio["achievements"] = [a for a in portfolio["achievements"] if isinstance(a, str) and a.strip()]
    portfolio["education"] = [e for e in portfolio["education"] if isinstance(e, dict)]
    portfolio["experience"] = [e for e in portfolio["experience"] if isinstance(e, dict)]
    portfolio["projects"] = [p for p in portfolio["projects"] if isinstance(p, dict)]
    for project in portfolio["projects"]:
        techs = project.get("technologies", [])
        project["technologies"] = [t for t in techs if isinstance(t, str) and t.strip()] if isinstance(techs, list) else []

    return portfolio


# --------------------------------------------------------------------------
# Step 5: Render portfolio.html (self-contained: CSS is inlined)
# --------------------------------------------------------------------------
def render_portfolio(portfolio: dict) -> str:
    env = Environment(
        loader=FileSystemLoader(str(BASE_DIR)),
        autoescape=select_autoescape(["html"]),
    )
    template = env.get_template(TEMPLATE_FILE)
    inline_css = STYLE_FILE.read_text(encoding="utf-8")
    style_tag = f"<style>{inline_css}</style>"
    return template.render(style_tag=style_tag, **portfolio)


# --------------------------------------------------------------------------
# High-level orchestration (used by main.py; app.py calls the same steps
# itself so it can show progress/errors on the web page)
# --------------------------------------------------------------------------
def generate_portfolio_html() -> str:
    """Run the full pipeline against the current resume.txt and return the
    rendered HTML. Also writes it to portfolio.html."""
    resume_text = load_resume_text()
    prompt = build_prompt(resume_text)
    raw_json_text = call_gemini(prompt)
    portfolio = parse_portfolio_json(raw_json_text)
    html = render_portfolio(portfolio)
    OUTPUT_FILE.write_text(html, encoding="utf-8")
    return html
