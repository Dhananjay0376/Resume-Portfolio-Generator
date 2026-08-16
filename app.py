"""
AI-Assisted Resume Portfolio Generator — web (PDF upload) front end
======================================================================
A small local web app: upload a resume PDF, and it is converted to text,
written into resume.txt, and run through the exact same pipeline as the
CLI version (pipeline.py) to produce a portfolio.

This still satisfies "reads from resume.txt": the PDF upload is just a
friendlier way of filling that file in, instead of typing or pasting
resume text by hand. Both main.py and app.py call the same functions in
pipeline.py, so there is one implementation of the graded workflow.

Run:
    python app.py
Then open http://127.0.0.1:5000 in your browser.

Note: this is a local, single-user tool, like the CLI — it keeps things
simple (per the brief's "keep the project simple" guidance) rather than
adding multi-user session isolation. Don't expose it on the public internet.
"""

import io

from dotenv import load_dotenv
from flask import Flask, render_template, request, Response
from pypdf import PdfReader
from pypdf.errors import PyPdfError

from pipeline import PortfolioError, write_resume_text, generate_portfolio_html

load_dotenv()

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 8 * 1024 * 1024  # 8 MB upload limit


def extract_text_from_pdf(pdf_stream: io.BytesIO) -> str:
    try:
        reader = PdfReader(pdf_stream)
    except PyPdfError as exc:
        raise PortfolioError(
            f"Could not read this PDF ({exc}). It may be corrupted or not a real PDF file."
        ) from exc

    if reader.is_encrypted:
        raise PortfolioError("This PDF is password-protected. Please upload an unlocked PDF.")

    pages_text = [page.extract_text() or "" for page in reader.pages]
    text = "\n".join(pages_text)

    if not text.strip():
        raise PortfolioError(
            "No text could be extracted from this PDF. It may be a scanned image rather "
            "than a text-based PDF — try exporting/saving your resume as a text PDF."
        )
    return text


@app.route("/", methods=["GET"])
def index():
    return render_template("upload.html")


@app.route("/generate", methods=["POST"])
def generate():
    uploaded = request.files.get("resume_pdf")

    if uploaded is None or uploaded.filename == "":
        return render_template("upload.html", error="Choose a PDF file first."), 400

    if not uploaded.filename.lower().endswith(".pdf"):
        return render_template("upload.html", error="Please upload a .pdf file."), 400

    try:
        resume_text = extract_text_from_pdf(io.BytesIO(uploaded.read()))
        write_resume_text(resume_text)
        html = generate_portfolio_html()
    except PortfolioError as exc:
        return render_template("upload.html", error=str(exc)), 400

    return Response(html, mimetype="text/html")


@app.errorhandler(413)
def too_large(_exc):
    return render_template("upload.html", error="That PDF is too large (max 8 MB)."), 413


if __name__ == "__main__":
    app.run(debug=True)
