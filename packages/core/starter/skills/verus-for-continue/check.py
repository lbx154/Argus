#!/usr/bin/env python3
"""Distinguish unsupported for-continue lowering from ordinary loop proof failure."""
from pathlib import Path
import subprocess,sys,json
skill=Path(__file__).resolve().parent
if len(sys.argv)!=3:raise SystemExit('usage: check.py VERUS OUTPUT_DIRECTORY')
v=Path(sys.argv[1]).resolve();out=Path(sys.argv[2]).resolve();out.mkdir(parents=True,exist_ok=True)
records=[]
for name in ['continue-control','else-control']:
 cmd=[str(v),'--edition=2021',str(skill/(name+'.rs'))]
 p=subprocess.run(cmd,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True)
 (out/(name+'.log')).write_text(p.stdout)
 records.append(dict(name=name,argv=cmd,exit_code=p.returncode))
 if name=='continue-control':assert p.returncode!=0 and 'for-loops do not yet support continue' in p.stdout,p.stdout
 else:assert p.returncode==0 and '2 verified, 0 errors' in p.stdout,p.stdout
(out/'commands.json').write_text(json.dumps(records,indent=2)+'\n')
print('Confirmed unsupported for-continue; equivalent primitive else-control checks. This is diagnostic only.')
