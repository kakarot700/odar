import json,sys,re,glob
for f in sys.argv[1:]:
    d=json.load(open(glob.glob(f+'/*.json')[0]))
    g=d['governance']; p=g.get('pipeline',{}); r=p.get('router') or {}
    syn=d.get('synthesis') or ''
    ans=syn.split('## Certified Findings')[0]
    words=len(re.sub(r'\[\[\d+\]\]\([^)]*\)','',ans).split())
    print(f, d['status'], (d.get('audit') or {}).get('status'), 'words',words,'certified',sum(1 for c in d['state']['claims'] if c['status']=='CERTIFIED') if 'state' in d else '')
    print(' counters',g['counters']); print(' roles',r.get('calls_by_role'),'fail',r.get('failures'),'disabled',list((r.get('disabled') or {}).keys()))
    for sq in p.get('subquestions',[]): print('  ',sq['sources'],sq['claims'],sq['question'][:90])
