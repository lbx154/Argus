#!/usr/bin/env python3
"""Run a verified initializer, reject one literal mutation, and restore the file."""
from pathlib import Path
import argparse,subprocess,json,hashlib
p=argparse.ArgumentParser();p.add_argument('--source',type=Path,required=True);p.add_argument('--old',required=True);p.add_argument('--new',required=True);p.add_argument('--out',type=Path,required=True);p.add_argument('command',nargs=argparse.REMAINDER);a=p.parse_args()
cmd=a.command[1:] if a.command[:1]==['--'] else a.command
assert cmd and a.old!=a.new
source=a.source.resolve();original=source.read_bytes();s=original.decode();assert s.count(a.old)==1,'mutation must be unique'
a.out.mkdir(parents=True,exist_ok=True);records=[]
def run(name):
 r=subprocess.run(cmd,capture_output=True);(a.out/(name+'.log')).write_bytes(r.stdout+r.stderr);records.append({'name':name,'argv':cmd,'cwd':str(Path.cwd()),'exit_code':r.returncode});return r
r=run('positive');assert r.returncode==0 and b'verification results::' in r.stdout+r.stderr,'positive must verify'
try:
 source.write_text(s.replace(a.old,a.new));r=run('negative')
 assert r.returncode!=0 and b'postcondition not satisfied' in r.stdout+r.stderr,'require semantic rejection, not build failure'
finally:
 source.write_bytes(original)
 (a.out/'commands.json').write_text(json.dumps({'source':str(source),'sha256':hashlib.sha256(original).hexdigest(),'old':a.old,'new':a.new,'runs':records},indent=2)+'\n')
print('PASS: initializer mutation rejected by body postcondition; source restored.')
