import json,os,collections,sys
K='/tmp/claude-0/-home-user-poiujnbhy/da6b639e-7ecd-5b07-9c36-bf7a3fd312ff/scratchpad/katalog'
A=['kosmos','mobilnaya','efir','provod','obshchie']
allf=json.load(open('all_sha.json')); repo=json.load(open('repo_sha.json')); szh=json.load(open('szh.json'))
loc2sha={}
for h,(sz,locs) in allf.items():
    for a,rel in locs: loc2sha[(a,rel)]=h
def norm(a,f):
    if f.startswith('../'):
        b,rest=f[3:].split('/',1); return b,rest
    return a,f
def shas_of(a,f):
    a,f=norm(a,f)
    if (a,f) in loc2sha: return [loc2sha[(a,f)]]
    p=f'{K}/{a}/{f}'
    if os.path.isdir(p):
        return [loc2sha[(a,os.path.relpath(os.path.join(r,x),f'{K}/{a}'))] for r,_,fs in os.walk(p) for x in fs if (a,os.path.relpath(os.path.join(r,x),f'{K}/{a}')) in loc2sha]
    return []
cost=lambda h: szh.get(h, allf[h][0])
entries=[]
cit=collections.Counter()
for a in A:
    for z in json.load(open(f'{K}/{a}/katalog.json')):
        hs=set()
        for s in z['источник']:
            if s.get('файл'): hs.update(shas_of(a,s['файл']))
        entries.append(hs)
        for h in hs: cit[h]+=1
B=float(sys.argv[1])*1e6
chosen=set(h for h in cit if cost(h)==0)
used=0
# licenses
lic=[h for h,(sz,locs) in allf.items() if any(os.path.basename(r).upper().startswith(('LICENSE','COPYING','NOTICE','COPYRIGHT','LICENCE')) for a,r in locs)]
for h in lic:
    if h not in chosen: chosen.add(h); used+=cost(h)
print('lic',len(lic),used/1e6)
for hs in sorted(entries,key=lambda hs: min([cost(h) for h in hs] or [0])):
    if not hs or hs&chosen: continue
    h=min(hs,key=cost)
    if used+cost(h)>B: continue
    chosen.add(h); used+=cost(h)
print('min-per-entry used',used/1e6, 'entries w/o stored', sum(1 for hs in entries if hs and not hs&chosen))
for h in sorted(cit,key=lambda h: -cit[h]/max(cost(h),1)):
    if h in chosen: continue
    if used+cost(h)>B: continue
    chosen.add(h); used+=cost(h)
nref=sum(1 for h in cit if h in chosen)
print('used',used/1e6,'chosen ref',nref,'of',len(cit),'entries w/o stored', sum(1 for hs in entries if hs and not hs&chosen))
json.dump(sorted(chosen),open('chosen.json','w'))
