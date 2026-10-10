import json,sys,os
for s in sys.argv[1:]:
    T=[];C={}
    for q in range(1,11):
        f=f"score_{s}_q{q}.json"
        if not os.path.exists(f): T.append(None); continue
        d=json.load(open(f)); T.append(d.get("trust_score"))
        for k,v in (d.get("counts") or {}).items(): C[k]=C.get(k,0)+v
    tot=sum(C.values()); unc=C.get("NO CITATION",0); cited=tot-unc
    sup=C.get("SUPPORTED",0); part=C.get("PARTIALLY SUPPORTED",0); bad=C.get("WRONG SOURCE",0)+C.get("CONTRADICTED",0)
    t=[x for x in T if x is not None]
    pc=lambda a,b: round(100*a/b) if b else 0
    print(s,T,"avg",round(sum(t)/len(t),1) if t else None,"n",len(t),"claims",tot,"cited%",pc(cited,tot),f"({cited}/{tot})","sup%",pc(sup,cited),"sup+part%",pc(sup+part,cited),"bad%",pc(bad,cited),"contra",C.get("CONTRADICTED",0),C)
