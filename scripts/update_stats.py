#!/usr/bin/env python3
from __future__ import annotations
import json, os, sys, time, urllib.error, urllib.parse, urllib.request
from pathlib import Path

API='https://api.github.com'
GRAPHQL_URL='https://api.github.com/graphql'
USERNAME=os.environ.get('GH_USERNAME','finixtavh')
TOKEN=os.environ.get('GH_TOKEN','')
OUTPUT=Path(os.environ.get('STATS_OUTPUT','profile/github-stats.svg'))
LOC_MAX_PAGES=int(os.environ.get('LOC_MAX_PAGES','20'))  # 100 commits/page -> up to 2000 commits/repo
HEADERS={'Accept':'application/vnd.github+json','X-GitHub-Api-Version':'2022-11-28','User-Agent':f'{USERNAME}-github-stats'}
if TOKEN: HEADERS['Authorization']=f'Bearer {TOKEN}'


def api(path, params=None, retries=5):
    url=API+path
    if params: url+='?'+urllib.parse.urlencode(params)
    last=None
    for attempt in range(retries):
        try:
            req=urllib.request.Request(url,headers=HEADERS)
            with urllib.request.urlopen(req,timeout=45) as r: return json.load(r)
        except urllib.error.HTTPError as e:
            body=e.read().decode('utf-8','replace'); last=RuntimeError(f'{e.code}: {body[:300]}')
            if e.code==202 or e.code>=500:
                time.sleep(2*(attempt+1)); continue
            if e.code in (403,429):
                reset=e.headers.get('X-RateLimit-Reset')
                if reset:
                    wait=max(1,int(reset)-int(time.time())+1)
                    if wait<=60: time.sleep(wait); continue
            break
        except (urllib.error.URLError,TimeoutError) as e:
            last=e; time.sleep(2*(attempt+1))
    raise RuntimeError(f'GitHub API request failed: {url}\n{last}')


def graphql(query, variables=None, retries=5):
    payload=json.dumps({'query':query,'variables':variables or {}}).encode()
    gh_headers={**HEADERS,'Content-Type':'application/json'}
    last=None
    for attempt in range(retries):
        try:
            req=urllib.request.Request(GRAPHQL_URL,data=payload,headers=gh_headers)
            with urllib.request.urlopen(req,timeout=60) as r: resp=json.load(r)
            if resp.get('errors'): raise RuntimeError(resp['errors'])
            return resp['data']
        except urllib.error.HTTPError as e:
            body=e.read().decode('utf-8','replace'); last=RuntimeError(f'{e.code}: {body[:300]}')
            if e.code>=500:
                time.sleep(2*(attempt+1)); continue
            if e.code in (403,429):
                reset=e.headers.get('X-RateLimit-Reset')
                if reset:
                    wait=max(1,int(reset)-int(time.time())+1)
                    if wait<=60: time.sleep(wait); continue
            break
        except (urllib.error.URLError,TimeoutError) as e:
            last=e; time.sleep(2*(attempt+1))
    raise RuntimeError(f'GraphQL request failed: {last}')


def pages(path, params=None, max_pages=10):
    p=dict(params or {}); p.setdefault('per_page',100); out=[]
    for page in range(1,max_pages+1):
        p['page']=page; chunk=api(path,p)
        if isinstance(chunk,dict): chunk=chunk.get('items') or []
        if not chunk: break
        out.extend(chunk)
        if len(chunk)<p['per_page']: break
    return out


def repos():
    found={}
    try:
        for r in pages('/user/repos',{'visibility':'all','affiliation':'owner,collaborator,organization_member','sort':'full_name'},20):
            found[r['full_name']]=r
    except Exception as e: print('warning:',e,file=sys.stderr)
    try:
        for c in pages('/search/commits',{'q':f'author:{USERNAME}'},10):
            r=c.get('repository') or {}; name=r.get('full_name')
            if name and name not in found: found[name]={'full_name':name,'html_url':r.get('html_url',f'https://github.com/{name}')}
    except Exception as e: print('warning:',e,file=sys.stderr)
    return sorted(found.values(),key=lambda x:x['full_name'].lower())


def viewer_id():
    return graphql('query { viewer { id } }')['viewer']['id']


LOC_QUERY='''
query($owner:String!,$name:String!,$id:ID!,$cursor:String){
  repository(owner:$owner,name:$name){
    defaultBranchRef{ target{ ... on Commit{
      history(first:100,author:{id:$id},after:$cursor){
        pageInfo{ hasNextPage endCursor }
        nodes{ additions deletions }
      }
    } } }
  }
}'''


def contributor(full_name, user_id):
    owner,name=full_name.split('/',1)
    commits=additions=deletions=0
    cursor=None
    for _ in range(LOC_MAX_PAGES):
        data=graphql(LOC_QUERY,{'owner':owner,'name':name,'id':user_id,'cursor':cursor})
        repo=data.get('repository')
        ref=(repo or {}).get('defaultBranchRef')
        if not ref: break  # empty repo, or default branch has no commits
        h=ref['target']['history']
        nodes=h['nodes']
        commits+=len(nodes)
        additions+=sum(n['additions'] for n in nodes)
        deletions+=sum(n['deletions'] for n in nodes)
        if not h['pageInfo']['hasNextPage']: break
        cursor=h['pageInfo']['endCursor']
    if commits==0: return None
    return {'commits':commits,'additions':additions,'deletions':deletions}


def search_count(q): return int(api('/search/issues',{'q':q}).get('total_count') or 0)

def esc(v):
    return str(v).replace('&','&amp;').replace('<','&lt;').replace('>','&gt;').replace('"','&quot;').replace("'",'&apos;')

def compact(n):
    n=int(n)
    if abs(n)>=1_000_000_000: return f'{n/1_000_000_000:.1f}B'
    if abs(n)>=1_000_000: return f'{n/1_000_000:.1f}M'
    if abs(n)>=1_000: return f'{n/1_000:.1f}k'
    return str(n)


def collect():
    total={'commits':0,'additions':0,'deletions':0}; stats=[]; rs=repos()
    print(f'Discovered {len(rs)} repositories.')
    uid=viewer_id()
    for i,r in enumerate(rs,1):
        name=r['full_name']
        try: s=contributor(name,uid)
        except Exception as e: print(f'skip {name}: {e}',file=sys.stderr); continue
        if not s: continue
        s.update(full_name=name,url=r.get('html_url',f'https://github.com/{name}'),changes=s['additions']+s['deletions'])
        stats.append(s)
        for k in total: total[k]+=s[k]
        print(f'[{i}/{len(rs)}] {name}: {s["commits"]} commits, +{s["additions"]} -{s["deletions"]}')
    try: prs=search_count(f'author:{USERNAME} is:pr')
    except Exception as e: print('warning PR:',e,file=sys.stderr); prs=0
    try: issues=search_count(f'author:{USERNAME} is:issue')
    except Exception as e: print('warning issues:',e,file=sys.stderr); issues=0
    stats.sort(key=lambda x:x['changes'],reverse=True)
    return {**total,'prs':prs,'issues':issues,'repos_with_activity':len(stats),'top_repos':stats[:5],'generated_at':time.strftime('%Y-%m-%d %H:%M UTC',time.gmtime())}


def render(d):
    cards=[('COMMITS',compact(d['commits']),'commit history'),('LINES +',compact(d['additions']),'additions'),('LINES -',compact(d['deletions']),'deletions'),('PULL REQUESTS',compact(d['prs']),'authored PRs')]
    xs=[30,245,460,675]; c=[]
    for x,(label,value,note) in zip(xs,cards):
        c.append(f'<rect x="{x}" y="82" width="195" height="112" rx="16" class="card"/><text x="{x+18}" y="112" class="label">{label}</text><text x="{x+18}" y="155" class="value">{value}</text><text x="{x+18}" y="178" class="muted">{note}</text>')
    rows=[]
    for i,r in enumerate(d['top_repos'],1):
        y=308+(i-1)*48
        rows.append(f'<text x="48" y="{y}" class="rank">{i}</text><text x="82" y="{y}" class="repo">{esc(r["full_name"])}</text><text x="550" y="{y}" class="plus">+{compact(r["additions"])}</text><text x="660" y="{y}" class="minus">-{compact(r["deletions"])}</text><text x="780" y="{y}" class="total">{compact(r["changes"])}</text>')
    if not rows: rows=['<text x="48" y="320" class="repo">No repository activity found yet.</text>']
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="900" height="590" viewBox="0 0 900 590"><style>text{{font-family:"JetBrains Mono","SFMono-Regular",Consolas,monospace}}.bg{{fill:#0d1117}}.panel{{fill:#161b22;stroke:#30363d;stroke-width:1}}.card{{fill:#11161c;stroke:#30363d;stroke-width:1}}.title{{fill:#f0f6fc;font-size:24px;font-weight:700}}.subtitle{{fill:#8b949e;font-size:13px}}.label{{fill:#8b949e;font-size:11px;font-weight:700;letter-spacing:1px}}.value{{fill:#f0f6fc;font-size:28px;font-weight:700}}.muted{{fill:#6e7681;font-size:11px}}.repo{{fill:#c9d1d9;font-size:13px}}.rank{{fill:#6e7681;font-size:13px}}.plus{{fill:#3fb950;font-size:13px;font-weight:700}}.minus{{fill:#f85149;font-size:13px;font-weight:700}}.total{{fill:#c9d1d9;font-size:13px;text-anchor:end}}.line{{stroke:#30363d;stroke-width:1}}</style><rect width="900" height="590" rx="22" class="bg"/><text x="30" y="42" class="title">GitHub Development Stats</text><text x="30" y="64" class="subtitle">@{esc(USERNAME)} · {d['repos_with_activity']} repos with activity · updated {esc(d['generated_at'])}</text>{''.join(c)}<rect x="30" y="220" width="840" height="335" rx="18" class="panel"/><text x="48" y="255" class="title" style="font-size:17px">Most changed repositories</text><text x="48" y="277" class="subtitle">Additions + deletions from the author's own commits on each repo's default branch</text><line x1="48" y1="286" x2="852" y2="286" class="line"/><text x="48" y="300" class="label">#</text><text x="82" y="300" class="label">REPOSITORY</text><text x="550" y="300" class="label">ADDED</text><text x="660" y="300" class="label">REMOVED</text><text x="780" y="300" class="label">CHANGED</text>{''.join(rows)}<text x="48" y="535" class="muted">Stats are generated automatically from a scheduled job.</text></svg>'''


def main():
    if not TOKEN: print('GH_TOKEN is empty',file=sys.stderr); return 1
    d=collect(); OUTPUT.parent.mkdir(parents=True,exist_ok=True); OUTPUT.write_text(render(d),encoding='utf-8'); print('Wrote',OUTPUT); return 0
if __name__=='__main__': raise SystemExit(main())
