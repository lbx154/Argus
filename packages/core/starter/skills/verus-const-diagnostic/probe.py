from pathlib import Path
import subprocess,sys,json
out=Path(sys.argv[1]).resolve();out.mkdir(parents=True,exist_ok=True)
verus=sys.argv[2] if len(sys.argv)>2 else 'verus'
p=out/'probe.rs'
p.write_text('''use vstd::prelude::*;
#[verifier::verify]
pub struct Config { enabled: bool }
impl Config {
    #[verifier::external_body]
    pub const fn new() -> Self { Self { enabled: true } }
}
#[verifier::verify]
#[verifier::exec]
pub const PAD: Config = Config::new();
pub const OTHER: Config = PAD;
fn main() {}
''')
records=[]
for name,flags in [('lifetime',[]),('no-lifetime',['--no-lifetime'])]:
 cmd=[verus,*flags,'--edition=2021',str(p)]
 with (out/(name+'.log')).open('w') as stream:
  r=subprocess.run(cmd,stdout=stream,stderr=subprocess.STDOUT)
 records.append({'argv':cmd,'exit_code':r.returncode,'log':name+'.log'})
(out/'commands.json').write_text(json.dumps(records,indent=2)+'\n')
print(json.dumps(records,indent=2))
