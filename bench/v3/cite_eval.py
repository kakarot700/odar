import os
"""Depth + citation-accuracy metrics for a markdown report.

usage: cite_eval.py KIND REPORT.md [max_sentences]
KIND = odar (answer section only, links [[n]](url)) | gptr (links ([label](url)))
Metrics:
  words          body words of the answer (links stripped; Sources/References excluded)
  sentences      body sentences (>= 6 words)
  cited_rate     share of body sentences carrying >= 1 citation link
  entailed_rate  of sampled cited sentences, share whose cited page(s) entail it
                 (max NLI entailment >= 0.5, cross-encoder/nli-deberta-v3-small,
                 best 8 spans per page) - the same verifier family ODAR uses
  unreachable    sampled cited sentences whose every cited page failed to fetch
"""

import json
import re
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))
from odar.citation_auditor import CitationAuditor  # noqa: E402
from odar.retrieval import PageExtractor  # noqa: E402
from odar.citation_auditor import content_tokens  # noqa: E402

kind, path = sys.argv[1], sys.argv[2]
limit = int(sys.argv[3]) if len(sys.argv) > 3 else 25
text = open(path, encoding="utf-8").read()
if kind == "odar":
    text = text.split("## Certified Findings")[0].split("## Answer", 1)[-1].split("\n", 1)[-1]
    link_re = re.compile(r"\[\[\d+\]\]\((https?://[^)\s]+)\)")
else:
    text = re.split(r"\n#+\s*(References|Sources)\b", text)[0]
    link_re = re.compile(r"\[[^\]]*\]\((https?://[^)\s]+)\)")

body = [ln for ln in text.splitlines() if ln.strip() and not ln.lstrip().startswith(("#", "|", "---"))]
body_text = " ".join(body)
plain = re.sub(r"\(?\[[^\]]*\]\([^)]*\)\)?", "", body_text)
words = len(plain.split())
sentences = [s for s in re.split(r"(?<=[.!?])\s+(?=[A-Z(])", body_text) if len(re.sub(r"\[[^\]]*\]\([^)]*\)", "", s).split()) >= 6]
cited = [(re.sub(r"\(?\[[^\]]*\]\([^)]*\)\)?", "", s).strip(), link_re.findall(s)) for s in sentences]
cited = [(s, urls) for s, urls in cited if urls]
# GPT Researcher often puts the citation in the NEXT sentence fragment; attach trailing link-only pieces
sample = cited[:limit]

extractor = PageExtractor()
auditor = CitationAuditor()
auditor.max_spans = 24
cache = {}


def page(url):
    if url not in cache:
        try:
            p = extractor.extract(url)
            cache[url] = p.text if p.ok and p.chars >= 200 else None
        except Exception:
            cache[url] = None
    return cache[url]


entailed = unreachable = grounded = 0
detail = []
for sent, urls in sample:
    texts = [t for t in (page(u) for u in dict.fromkeys(urls)) if t]
    if not texts:
        unreachable += 1
        detail.append({"s": sent[:120], "p": None})
        continue
    res = auditor.audit(sent, texts)
    toks = set(content_tokens(sent))
    page_toks = set(content_tokens(" ".join(texts)))
    g = len(toks & page_toks) / max(1, len(toks))
    grounded += g >= 0.7
    ok = res.entailment_probability >= 0.5
    entailed += ok
    detail.append({"s": sent[:120], "p": round(res.entailment_probability, 2)})
checked = len(sample) - unreachable
print(
    json.dumps(
        {
            "report": path,
            "words": words,
            "sentences": len(sentences),
            "cited_rate": round(len(cited) / max(1, len(sentences)), 2),
            "sampled": len(sample),
            "unreachable": unreachable,
            "entailed_rate": round(entailed / max(1, checked), 2),
            "lexically_grounded_rate": round(grounded / max(1, checked), 2),
            "unique_cited_urls": len({u for _s, us in cited for u in us}),
            "detail": detail,
        }
    )
)
