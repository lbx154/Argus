#!/usr/bin/env python3
"""Executable, project-independent FromSpec discriminator; no project edits."""
import sys, subprocess, json, shlex
from pathlib import Path
v=Path(sys.argv[1]).resolve();out=Path(sys.argv[2]).resolve();out.mkdir(parents=True,exist_ok=True)
base='''use vstd::prelude::*;
verus! {
pub enum Input { Byte(usize, u8), Padding }
pub enum Output { Error(Input), TooSmall }
impl From<Input> for Output {
    fn from(e: Input) -> Self { Output::Error(e) }
}
INTERFACE
}
fn main() {}
'''
interface='''impl vstd::std_specs::convert::FromSpecImpl<Input> for Output {
    open spec fn obeys_from_spec() -> bool { true }
    open spec fn from_spec(e: Input) -> Self { Output::Error(e) }
}'''
records=[]
for name,model,expected in [('missing','',1),('exact',interface,0),('wrong',interface.replace('Output::Error(e)','Output::Error(Input::Padding)'),1)]:
 src=out/(name+'.rs');src.write_text(base.replace('INTERFACE',model))
 cmd=[str(v),'--edition=2021',str(src)]
 q=subprocess.run(cmd,cwd=out,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True)
 (out/(name+'.log')).write_text(q.stdout)
 records.append(dict(name=name,command=shlex.join(cmd),exit_code=q.returncode,expected=expected))
 (out/'commands.json').write_text(json.dumps(records,indent=2)+'\n')
 assert q.returncode==expected,(name,q.stdout)
 if expected:assert 'postcondition not satisfied' in q.stdout
print(json.dumps(records,indent=2))
