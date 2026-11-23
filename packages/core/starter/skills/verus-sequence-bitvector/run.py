#!/usr/bin/env python3
import argparse,json,subprocess
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--verus',default='verus');p.add_argument('--output-dir',type=Path,required=True);a=p.parse_args();a.output_dir.mkdir(parents=True,exist_ok=True)
records=[]
for name in ['positive','negative']:
 cmd=[a.verus,'--edition=2021',str(Path(__file__).with_name(name+'.rs'))]
 r=subprocess.run(cmd,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True)
 (a.output_dir/(name+'.log')).write_text(r.stdout)
 records.append(dict(name=name,argv=cmd,exit_code=r.returncode));print(name,r.returncode)
 assert (r.returncode==0 and '0 errors' in r.stdout) if name=='positive' else (r.returncode!=0 and 'unsupported for bit-vector' in r.stdout)
(a.output_dir/'commands.json').write_text(json.dumps(records,indent=2)+'\n')
