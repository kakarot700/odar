import re,json,glob
def sec_parse(block):
    lines=block.split("\n")
    s=next(i for i,l in enumerate(lines) if re.search(r"Answer.*(verbatim)",l,re.I))
    e=next((i for i in range(s+1,len(lines)) if re.match(r"\s*-?\s*(Cited sources|Citation chip \d|Sources \(|Sources:|- Citation chips|- Expanded|Citation markers|Timing:|Approx seconds|More cards|Read More|Searched:|Note:)",lines[i]) ),len(lines))
    ans="\n".join(lines[s+1:e]).strip().strip('"').strip()
    rest="\n".join(lines[e:])
    urls=[]
    for u in re.findall(r"https?://[^\s)\]\"'>,]+",rest):
        u=u.rstrip(".")
        if u not in urls: urls.append(u)
    return ans,urls
out={}
for f in sorted(glob.glob("duck/gpt-a/q*.md")):
    t=open(f).read(); q=int(re.search(r"Q(\d+)",t).group(1)); out[("duckgpt",q)]=sec_parse(t)
for f,tag in [("duck/gpt-b/findings.md","duckgpt"),("duck/claude-a/findings.md","duckclaude")]:
    t=open(f).read()
    parts=re.split(r"\n(?=#{2,3} Q\d+|Q\d+ — )",t)
    for p in parts:
        m=re.match(r"#*\s*Q(\d+)",p.strip())
        if m and re.search(r"Answer.*verbatim",p,re.I): out[(tag,int(m.group(1)))]=sec_parse(p)
for f in glob.glob("duck/claude-b/*.json"):
    d=json.load(open(f)); out[("duckclaude",d["q"])]=(d["answer"],d.get("sources_in_citation_order") or d.get("sources"))
for (tag,q),(a,u) in sorted(out.items()):
    json.dump({"answer":a,"sources":u},open(f"{tag}_q{q}.json","w"),indent=1)
    print(tag,q,len(a),len(u),u[:1])
