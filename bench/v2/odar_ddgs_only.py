import os
# Benchmark harness: run ODAR's CLI with search limited to the DDGS tier
# (the same backend GPT Researcher uses). The DDG-HTML and Wikipedia tiers are
# blocked by this sandbox's egress proxy (502/403) and only add retry latency.
import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))
import odar.retrieval as R
R.ZeroCostSearch._search_html_fallback = lambda self, q, n: []
R.ZeroCostSearch._search_wikipedia = lambda self, q, n: []
import run_research
sys.exit(run_research.main(sys.argv[1:]))
