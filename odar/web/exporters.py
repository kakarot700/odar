"""Report rendering for the web app: Markdown, DOCX and PDF exports.

DOCX is written directly as OOXML (no python-docx dependency); PDF uses the
optional ``fpdf2`` package with a Unicode TTF font when one is available.
"""

from __future__ import annotations

import io
import os
import re
import zipfile
from typing import Any, Dict, List, Tuple
from xml.sax.saxutils import escape

from odar.check import CONTRADICTED

FONT_CANDIDATES = (
    os.environ.get("ODAR_PDF_FONT", ""),
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/TTF/DejaVuSans.ttf",
)


# ---------------------------------------------------------------------- #
# Markdown
# ---------------------------------------------------------------------- #
def check_markdown(result: Dict[str, Any], title: str = "") -> str:
    score = result.get("trust_score")
    score_s = "n/a (no checkable citations)" if score is None else f"{score}/100"
    lines = [f"# ODAR Check: {title}" if title else "# ODAR Check report", ""]
    lines += [f"**Trust score: {score_s}** ({result.get('grade', '')})", ""]
    counts = result.get("counts") or {}
    lines.append(" · ".join(f"{k}: {v}" for k, v in counts.items() if v) or "no claims found")
    lines.append("")
    for i, claim in enumerate(result.get("claims") or [], 1):
        lines.append(f"## {i}. {claim['verdict']}")
        lines.append(f"> {claim['claim']}")
        lines.append("")
        for cit in claim.get("citations") or []:
            label = cit.get("url") or f"{cit.get('marker')} (no reference entry)"
            extra = f"; contradiction {cit['contradiction']:.2f}" if cit["verdict"] == CONTRADICTED else ""
            kind = f"{cit['source_type']}; " if cit.get("source_type") else ""
            lines.append(
                f"- {cit.get('marker') or 'link'} {label}: **{cit['verdict']}** "
                f"({kind}link: {cit.get('link_state') or 'n/a'}; entailment {cit.get('entailment', 0):.2f}{extra})"
            )
            if cit.get("quote"):
                lines.append(f'  - quote: "{cit["quote"]}"')
            if cit.get("note"):
                lines.append(f"  - note: {cit['note']}")
        rep = claim.get("replacement")
        if rep:
            lines.append(f"- suggested source: {rep.get('citation') or rep['url']}")
            lines.append(f'  - quote: "{rep["quote"]}"')
        elif claim.get("replacement_searched"):
            lines.append("- suggested source: none found that supports this claim")
        if claim.get("note"):
            lines.append(f"- note: {claim['note']}")
        lines.append("")
    if result.get("references"):
        lines += [
            "## Reference check",
            "",
            references_markdown({"references": result["references"]}, heading=False),
        ]
    return "\n".join(lines).rstrip() + "\n"


def references_markdown(result: Dict[str, Any], heading: bool = True) -> str:
    lines = []
    if heading:
        style = str(result.get("style", "apa")).upper()
        lines += [f"# Reference check ({style})", ""]
        counts = result.get("counts") or {}
        lines += [" · ".join(f"{k}: {v}" for k, v in counts.items() if v) or "no references found", ""]
    for i, ref in enumerate(result.get("references") or [], 1):
        lines.append(f"{i}. **{ref['status']}**: {ref['raw']}")
        for problem in ref.get("problems") or []:
            lines.append(f"  - {problem}")
        if ref.get("formatted"):
            lines.append(f"  - corrected: {ref['formatted']}")
        for sug in (ref.get("suggestions") or [])[:3]:
            lines.append(
                f"  - real alternative: {sug.get('title')} ({sug.get('year') or 'n.d.'}) {sug.get('link', '')}"
            )
    if heading and result.get("bibliography"):
        lines += ["", "## Bibliography", ""]
        lines += [f"- {entry}" for entry in result["bibliography"]]
    return "\n".join(lines) + "\n"


def research_markdown(result: Dict[str, Any], title: str = "") -> str:
    body = (result.get("synthesis") or "").strip() or "(no report was produced)"
    if not body.lstrip().startswith("#"):
        body = f"# {title or 'Research report'}\n\n{body}"
    meta = f"\n\n---\nstatus: {result.get('status', '')} | uncertainty: {result.get('uncertainty', '')}"
    check = result.get("citation_check")
    if check:
        meta += "\n\n" + check_markdown(check, "citations in this report").replace(
            "# ODAR Check", "## Citation check", 1
        )
    return body + meta + "\n"


def run_markdown(run: Dict[str, Any]) -> str:
    result = run.get("result") or {}
    if run["mode"] == "check":
        return check_markdown(result, run.get("title", ""))
    if run["mode"] == "references":
        return references_markdown(result)
    return research_markdown(result, run.get("title", ""))


# ---------------------------------------------------------------------- #
# Markdown -> simple blocks (shared by DOCX and PDF)
# ---------------------------------------------------------------------- #
_INLINE = re.compile(r"\*\*(.+?)\*\*|`([^`]+)`|\[([^\]]+)\]\((https?://[^)\s]+)\)")


def _plain(text: str) -> str:
    return _INLINE.sub(lambda m: m.group(1) or m.group(2) or f"{m.group(3)} ({m.group(4)})", text)


def blocks(markdown: str) -> List[Tuple[str, str]]:
    out: List[Tuple[str, str]] = []
    for raw in markdown.splitlines():
        line = raw.rstrip()
        if not line.strip() or set(line.strip()) <= {"-", "|", ":", " "}:
            continue
        if line.startswith("#"):
            level = len(line) - len(line.lstrip("#"))
            out.append((f"h{min(level, 3)}", _plain(line.lstrip("# ").strip())))
        elif line.lstrip().startswith(("- ", "* ")):
            indent = (len(line) - len(line.lstrip())) // 2
            out.append(("li2" if indent else "li", _plain(line.lstrip()[2:])))
        elif line.startswith(">"):
            out.append(("quote", _plain(line.lstrip("> "))))
        elif line.startswith("|"):
            cells = [c.strip() for c in line.strip("|").split("|")]
            out.append(("p", _plain("  ·  ".join(c for c in cells if c))))
        else:
            out.append(("p", _plain(line)))
    return out


# ---------------------------------------------------------------------- #
# DOCX
# ---------------------------------------------------------------------- #
_CT = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
    '<Default Extension="xml" ContentType="application/xml"/>'
    '<Override PartName="/word/document.xml" '
    'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
    "</Types>"
)
_RELS = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Id="rId1" '
    'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
    'Target="word/document.xml"/></Relationships>'
)
_STYLE = {"h1": (36, True, ""), "h2": (28, True, ""), "h3": (24, True, ""), "quote": (22, False, "i")}


def _docx_para(kind: str, text: str) -> str:
    size, bold, italic = _STYLE.get(kind, (21, False, ""))
    rpr = f'<w:rPr>{"<w:b/>" if bold else ""}{"<w:i/>" if italic else ""}<w:sz w:val="{size}"/></w:rPr>'
    indent = {"li": 360, "li2": 720, "quote": 360}.get(kind, 0)
    prefix = "• " if kind.startswith("li") else ""
    ind = f'<w:ind w:left="{indent}"/>' if indent else ""
    ppr = f'<w:pPr><w:spacing w:after="120"/>{ind}</w:pPr>'
    return f'<w:p>{ppr}<w:r>{rpr}<w:t xml:space="preserve">{escape(prefix + text)}</w:t></w:r></w:p>'


def to_docx(markdown: str) -> bytes:
    body = "".join(_docx_para(kind, text) for kind, text in blocks(markdown))
    document = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"<w:body>{body}<w:sectPr/></w:body></w:document>"
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", _CT)
        archive.writestr("_rels/.rels", _RELS)
        archive.writestr("word/document.xml", document)
    return buf.getvalue()


# ---------------------------------------------------------------------- #
# PDF
# ---------------------------------------------------------------------- #
def _font_path() -> str:
    for path in FONT_CANDIDATES:
        if path and os.path.exists(path):
            return path
    return ""


def to_pdf(markdown: str) -> bytes:
    try:
        from fpdf import FPDF
    except ImportError as exc:  # pragma: no cover - optional dependency
        raise RuntimeError("PDF export needs the optional 'fpdf2' package (pip install odar[web])") from exc
    pdf = FPDF(format="A4")
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()
    font = _font_path()
    if font:
        pdf.add_font("body", "", font)
        family = "body"
    else:  # core font: Latin-1 only
        family = "helvetica"
    sizes = {"h1": 17, "h2": 14, "h3": 12, "quote": 10.5}
    width = pdf.w - pdf.l_margin - pdf.r_margin
    for kind, text in blocks(markdown):
        if not font:
            text = text.encode("latin-1", "replace").decode("latin-1")
        pdf.set_font(family, size=sizes.get(kind, 10.5))
        indent = {"li": 4, "li2": 9, "quote": 5}.get(kind, 0)
        pdf.set_x(pdf.l_margin + indent)
        prefix = "• " if kind.startswith("li") and font else ("- " if kind.startswith("li") else "")
        pdf.multi_cell(
            width - indent,
            5.6 if kind in ("p", "li", "li2", "quote") else 8,
            prefix + text,
            new_x="LMARGIN",
            new_y="NEXT",
        )
        pdf.ln(1.5 if kind.startswith("h") else 0.8)
    return bytes(pdf.output())
