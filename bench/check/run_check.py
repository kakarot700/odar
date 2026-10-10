import os
import sys, json
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))
import odar.retrieval as R
R.ZeroCostSearch._search_html_fallback = lambda self, q, n: []
R.ZeroCostSearch._search_wikipedia = lambda self, q, n: []
from odar.check import check_text, CheckLimits
src, out = sys.argv[1], sys.argv[2]
llm = '--llm' in sys.argv
r = check_text(open(src).read(), use_llm=llm, limits=CheckLimits(max_replacement_searches=int(sys.argv[3]) if len(sys.argv) > 3 and sys.argv[3].isdigit() else 3))
open(out + '.md', 'w').write(r.to_markdown()); open(out + '.json', 'w').write(r.to_json())
print(r.trust_score, r.counts, r.usage, round(r.elapsed_s))
