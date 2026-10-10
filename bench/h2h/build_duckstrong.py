import os
import json,glob,re
N=os.environ.get('DUCK_NOTES', 'duck/strong/')
KEY={"world health organization (who)":"who.int","nobel prize":"nobelprize.org","rbi.org.in":"rbi.org.in","sciencedirect":"sciencedirect",
"stanford university":"stanford.edu","frontiers":"frontiersin","european union":"europa.eu","copernicus":"copernicus","wmo":"wmo.int",
"nature":"nature.com","american heart association":"heart.org","american college of cardiology":"acc.org"}
def urls(d):
    for k in ("sources_in_order","sources"):
        if k in d:
            return [s["url"] if isinstance(s,dict) else s for s in d[k]]
out={}
for f in glob.glob(N+'duckai-*/duckstrong_q*.json'):
    q=int(re.search(r'q(\d+)\.json',f).group(1)); d=json.load(open(f))
    ans=d.get("answer") or d["answer_verbatim_with_source_names"]; src=urls(d)
    def rep(m):
        body=m.group(1); idxs=[]
        mp=re.match(r'(.+?)\s*\+(\d+)$',body)
        parts=[mp.group(1)] if mp else [p.strip() for p in body.split('/')]
        for p in parts:
            k=KEY.get(p.lower().strip())
            if not k: continue
            for i,u in enumerate(src,1):
                if k in u: idxs.append(i); break
        if mp and idxs:
            idxs+= [i for i in range(idxs[0]+1,idxs[0]+1+int(mp.group(2))) if i<=len(src)]
        idxs=sorted(set(idxs))
        return "".join(f"[{i}]" for i in idxs)  # unresolved chip -> uncited
    new=re.sub(r'\[([^\]\d][^\]]*)\]',rep,ans)
    new=re.sub(r'\*\*','',new)
    if not re.search(r'\[\d+\]',new): new=new.rstrip()+" "+"".join(f"[{i}]" for i in range(1,len(src)+1))
    json.dump({"answer":new,"sources":src,"raw_answer":ans,"approx_seconds":d.get("approx_seconds"),"model":"GPT-6 Luna"},open(f"duckstrong_q{q}.json","w"),indent=1)
    print(q,len(src),re.findall(r'\[\d+\]',new))
# Q9/Q10: chip names are ambiguous; use the page's raw <citation src> numbers mapped to panel index
for q in (9,10):
    d=json.load(open(glob.glob(N+f'duckai-b-*/duckstrong_q{q}.json')[0]))
    src=[s["url"] for s in d["sources"]]; pos={s["index"]:i for i,s in enumerate(d["sources"],1)}
    def rep(m):
        return "".join(f"[{pos[int(x)]}]" for x in m.group(1).split(",") if int(x) in pos)
    new=re.sub(r'<citation src="([\d,]+)"></citation>',rep,d["answer_with_citation_nums_raw"]).replace("**","")
    o=json.load(open(f"duckstrong_q{q}.json")); o["answer"]=new; o["sources"]=src
    json.dump(o,open(f"duckstrong_q{q}.json","w"),indent=1); print(q,re.findall(r'\[\d+\]',new))
