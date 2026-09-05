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
    accent='#7ee787'; bg='#0a0e12'; chrome='#11151b'; border='#242c36'; dim='#5b6472'; text='#d0d7de'
    font='"JetBrains Mono","SFMono-Regular",Consolas,monospace'

    def trunc(s,n): return s if len(s)<=n else s[:n-1]+'…'

    labels=[
        ('repos with activity',str(d['repos_with_activity'])),
        ('commits',compact(d['commits'])),
        ('lines added','+'+compact(d['additions'])),
        ('lines removed','-'+compact(d['deletions'])),
        ('pull requests',compact(d['prs'])),
    ]
    lines=[('prompt',f'{USERNAME}@github:~$ fetch-stats')]
    for label,val in labels: lines.append(('out',f'{label.ljust(22)}{val}'))
    lines.append(('blank',''))
    lines.append(('dim','top repos by lines changed'))
    if d['top_repos']:
        for i,r in enumerate(d['top_repos'],1):
            name=trunc(r['full_name'].split('/',1)[-1],22)
            lines.append(('out',f'  {i}. {name.ljust(22)} +{compact(r["additions"])} -{compact(r["deletions"])}'))
    else:
        lines.append(('dim','  no repository activity found yet'))
    lines.append(('blank',''))
    lines.append(('dim',f'updated {d["generated_at"]}'))
    lines.append(('prompt',f'{USERNAME}@github:~$ _'))

    font_size=13; line_h=21; pad_x=24; pad_top=22; bar_h=34; width=680
    height=bar_h+pad_top+len(lines)*line_h+18

    out=[f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" font-family=\'{font}\' font-size="{font_size}px">']
    out.append(f'<rect x="0.5" y="0.5" width="{width-1}" height="{height-1}" rx="10" fill="{bg}" stroke="{border}"/>')
    out.append(f'<path d="M0.5 {bar_h} L0.5 10 Q0.5 0.5 10 0.5 L{width-10} 0.5 Q{width-0.5} 0.5 {width-0.5} 10 L{width-0.5} {bar_h} Z" fill="{chrome}"/>')
    out.append(f'<line x1="0.5" y1="{bar_h}" x2="{width-0.5}" y2="{bar_h}" stroke="{border}"/>')
    for i,c in enumerate(('#ff5f56','#ffbd2e','#27c93f')):
        out.append(f'<circle cx="{20+i*18}" cy="{bar_h/2}" r="6" fill="{c}"/>')
    out.append(f'<text x="{width/2}" y="{bar_h/2+4}" fill="{dim}" text-anchor="middle" font-size="12px">{esc(USERNAME)}@github — stats</text>')

    y=bar_h+pad_top
    prompt_prefix=f'{USERNAME}@github:~$ '
    for kind,content in lines:
        if kind=='blank': y+=line_h; continue
        if kind=='prompt':
            rest=content[len(prompt_prefix):] if content.startswith(prompt_prefix) else content
            out.append(f'<text x="{pad_x}" y="{y}" xml:space="preserve"><tspan fill="{accent}">{esc(prompt_prefix)}</tspan><tspan fill="{text}">{esc(rest)}</tspan></text>')
        elif kind=='dim':
            out.append(f'<text x="{pad_x}" y="{y}" fill="{dim}" xml:space="preserve">{esc(content)}</text>')
        else:
            out.append(f'<text x="{pad_x}" y="{y}" fill="{text}" xml:space="preserve">{esc(content)}</text>')
        y+=line_h
    out.append('</svg>')
    return '\n'.join(out)


def main():
    if not TOKEN: print('GH_TOKEN is empty',file=sys.stderr); return 1
    d=collect(); OUTPUT.parent.mkdir(parents=True,exist_ok=True); OUTPUT.write_text(render(d),encoding='utf-8'); print('Wrote',OUTPUT); return 0
if __name__=='__main__': raise SystemExit(main())
