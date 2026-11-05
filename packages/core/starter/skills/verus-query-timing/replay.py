#!/usr/bin/env python3
"""Time unchanged generated Z3 queries. Echo markers add no solver settings.
This is serialized query replay, not an additional Verus proof result.
"""
import argparse,json,os,pathlib,re,subprocess,time
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--smt',type=pathlib.Path,required=True)
parser.add_argument('--out',type=pathlib.Path,required=True)
parser.add_argument('--z3',default=os.environ.get('VERUS_Z3_PATH','z3'))
args=parser.parse_args();src=args.smt;out=args.out;out.mkdir(parents=True,exist_ok=True)
lines=[]; queries=[]; function=span='';pending=False
for line in src.read_text().splitlines(True):
    if line.startswith(';; Function-Def '):function=line.strip()[3:]
    if line.startswith(';; src/'):span=line.strip()[3:]
    if line.strip()=='(check-sat)':
        n=len(queries);queries.append({'index':n,'function':function,'span':span})
        lines.append(f'(echo "T1_BEGIN_{n}")\n');pending=True
    lines.append(line)
    if pending and line.strip()=='(get-info :all-statistics)':
        lines.append(f'(echo "T1_END_{n}")\n');pending=False
instrumented=out/'baseline-loop-replay.smt2';instrumented.write_text(''.join(lines))
with instrumented.open() as inp, (out/'baseline-loop-replay.stdout').open('w') as log:
    p=subprocess.Popen([args.z3,'-smt2','-in'],stdin=inp,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True)
    latest_count=0;current=None;start=0
    for line in p.stdout:
        log.write(line)
        m=re.search(r':rlimit-count\s+(\d+)',line)
        if m:latest_count=int(m[1])
        if line.startswith('T1_BEGIN_'):
            current=queries[int(line.strip().rsplit('_',1)[1])];start=time.monotonic();current['rlimit_before']=latest_count;current['response']=''
        elif line.startswith('T1_END_'):
            current['seconds']=time.monotonic()-start;current['rlimit_after']=latest_count
            current['rlimit_delta']=latest_count-current['rlimit_before'];current=None
        elif current is not None:current['response']+=line
    status=p.wait()
(out/'baseline-loop-queries.json').write_text(json.dumps({'exit_code':status,'queries':queries},indent=2)+'\n')
for q in queries:
    print(json.dumps({k:v for k,v in q.items() if k != 'response'}))
