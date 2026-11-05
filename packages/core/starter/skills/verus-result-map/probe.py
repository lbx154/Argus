#!/usr/bin/env python3
"""Run three self-contained Result::map projection controls with selected Verus."""
from pathlib import Path
import argparse,subprocess,json
p=argparse.ArgumentParser();p.add_argument('verus',type=Path);p.add_argument('output',type=Path);a=p.parse_args();a.output.mkdir(exist_ok=True,parents=True)
start='''use vstd::prelude::*;
verus! {
struct Metadata { count: usize }
fn project(r: Result<Metadata, ()>) -> (out: Result<usize, ()>)
    requires r.is_ok(),
    ensures out == Ok(r.unwrap().count),
{ r.map(CLOSURE) }
}
fn main() {}
'''
cases=[('unannotated','|m| m.count',False),('annotated','|m: Metadata| -> (n: usize) ensures n == m.count, { m.count }',True),('wrong-projection','|m: Metadata| -> (n: usize) ensures n == m.count, { 0usize }',False)]
records=[]
for name,closure,success in cases:
 src=a.output/(name+'.rs');src.write_text(start.replace('CLOSURE',closure));cmd=[str(a.verus.resolve()),'--edition=2021',str(src.resolve())]
 r=subprocess.run(cmd,capture_output=True,text=True);log=r.stdout+r.stderr;(a.output/(name+'.log')).write_text(log)
 assert (r.returncode==0)==success and 'verification results::' in log,(name,log)
 records.append(dict(name=name,argv=cmd,exit_code=r.returncode,expect_success=success))
(a.output/'commands.json').write_text(json.dumps(records,indent=2)+'\n');print('PASS: unconstrained map result rejected; annotated projection checked; wrong projection rejected')
