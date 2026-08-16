"""
AI-Assisted Resume Portfolio Generator — CLI entry point
==========================================================
Reads resume.txt -> sends a controlled prompt to the Gemini API ->
receives structured JSON -> renders portfolio.html.

This is the workflow described in the project brief: place your resume
content in resume.txt yourself, then run this script.

(Prefer to upload a resume PDF instead? Run `python app.py` for the web
version — it writes your PDF's text into this same resume.txt and calls
the exact same pipeline in pipeline.py.)

Run:
    python main.py
"""

import sys
import webbrowser

from dotenv import load_dotenv

from pipeline import PortfolioError, OUTPUT_FILE, generate_portfolio_html


def main() -> None:
    load_dotenv()

    try:
        print("Reading and validating resume.txt ...")
        print("Sending resume to Gemini ...")
        print("Parsing structured JSON response ...")
        print("Generating portfolio.html ...")
        generate_portfolio_html()
    except PortfolioError as exc:
        print(f"\n[ERROR] {exc}\n")
        sys.exit(1)

    print(f"\nDone. Portfolio saved to: {OUTPUT_FILE}")
    print("Opening it in your browser now — verify every fact against the original resume.")
    webbrowser.open(OUTPUT_FILE.as_uri())


if __name__ == "__main__":
    main()
