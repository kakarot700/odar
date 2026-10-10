import json,re,glob
from urllib.parse import urlparse
ALIAS={"world health organization (who)":"who.int","who":"who.int","university of oxford":"ox.ac.uk","nobel prize":"nobelprize.org",
"harvard university":"harvard","nrdc":"nrdc.org","wmo.int":"wmo.int","wikipedia":"wikipedia.org","european union":"europa.eu",
"american heart association":"heart.org","trading economics":"tradingeconomics","rutgers university":"rutgers","sciencedirect":"sciencedirect",
"cuanschutz.edu":"cuanschutz","climatecentral.org":"climatecentral","nasa":"nasa.gov","bbc":"bbc.com","world economic forum":"weforum",
"frontiers":"frontiersin","goodwinlaw.com":"goodwinlaw","acc.org":"acc.org","escardio.org":"escardio","ajmc.com":"ajmc","docwirenews.com":"docwirenews",
"novavax.com":"novavax","2minutemedicine.com":"2minutemedicine"}
def idx(name,srcs):
    k=ALIAS.get(name.lower().strip())
    if not k: return None
    for i,u in enumerate(srcs,1):
        if k in u: return i
def convert(ans,srcs):
    names=sorted(ALIAS,key=len,reverse=True)
    pat=re.compile(r"\[?\b("+"|".join(re.escape(n) for n in names)+r")\]?(?:\s*\[?\d\]?)?(?=\s*(?:\n|$|\.?\s*$))",re.I|re.M)
    hits=0
    def rep(m):
        nonlocal hits
        i=idx(m.group(1),srcs)
        if i is None: return m.group(0)
        hits+=1; return f" [{i}]"
    out=pat.sub(rep,ans)
    # bracketed chips mid-paragraph like [World Health Organization (WHO)]
    def rep2(m):
        nonlocal hits
        i=idx(m.group(1),srcs)
        if i is None: return m.group(0)
        hits+=1; return f"[{i}]"
    out=re.sub(r"\[([^\]\d][^\]]*)\]\d?",rep2,out)
    if hits==0: out=out.rstrip()+" "+"".join(f"[{i}]" for i in range(1,len(srcs)+1))
    return out,hits
import subprocess
data={}
for h,tag in [("duck-gpt-a","duckgpt"),("duck-gpt-b","duckgpt"),("duck-claude-a","duckclaude"),("duck-claude-b","duckclaude")]:
    pass
