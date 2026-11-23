#!/usr/bin/env python3
"""Reproduce anonymous-const signature mismatch; never certify a trusted wrapper."""
from pathlib import Path
import argparse,subprocess,json
p=argparse.ArgumentParser();p.add_argument('verus',type=Path);p.add_argument('output',type=Path);a=p.parse_args();a.output.mkdir(parents=True,exist_ok=True)
s='''use vstd::prelude::*;
verus! {
ATTR
pub fn endian(x:u32)->(r:[u8;4])
    ensures r[0] == (x >> 24) as u8,
{ x.to_be_bytes() }
fn consume(x:u32) { let r=endian(x); assert(r[0] == (x>>24) as u8); }
}
fn main() {}
'''
records=[]
for name,attr in [('direct','#[verifier::external_fn_specification]'),('library-wrapper','#[verifier::external_body]')]:
 src=a.output/(name+'.rs');src.write_text(s.replace('ATTR',attr));cmd=[str(a.verus.resolve()),'--edition=2021','--verify-root',str(src.resolve())]
 r=subprocess.run(cmd,capture_output=True,text=True);log=r.stdout+r.stderr;(a.output/(name+'.log')).write_text(log)
 if name=='direct':assert r.returncode and 'requires function type signature to match' in log and 'constant#0' in log,log
 else:assert r.returncode==0 and '1 verified, 0 errors' in log,log
 records.append(dict(name=name,argv=cmd,exit_code=r.returncode))
(a.output/'commands.json').write_text(json.dumps(records,indent=2)+'\n')
print('PASS: direct anonymous-const signature mismatch reproduced; trusted library wrapper translated and consumed. Wrapper semantics remain TCB.')
