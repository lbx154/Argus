"""Compare a const-initialization remedy while retaining normal lifetime checks."""
from pathlib import Path
import subprocess, sys, json
out=Path(sys.argv[1]).resolve();out.mkdir(parents=True,exist_ok=True)
original,patched=sys.argv[2:4]
source='''use vstd::prelude::*;
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
'''
cases={'minimal':source+'fn main() {}\n','borrow-positive':source+"#[verifier::verify]\npub fn borrowed<'a>(input: &'a u8) -> &'a u8 { input }\nfn main() {}\n",'borrow-negative':source+"#[verifier::verify]\npub fn dangling<'a>() -> &'a u8 { let local = 7u8; &local }\nfn main() {}\n",'invalid-unused-const':source+'pub const INVALID_UNUSED: u8 = 1u8 / 0u8;\nfn main() {}\n'}
records=[];results={}
for label,exe,case in [('original',original,'minimal')]+[('patched',patched,c) for c in cases]:
 p=out/(case+'.rs');p.write_text(cases[case]);name=label+'-'+case;cmd=[exe,'--edition=2021',str(p)]
 r=subprocess.run(cmd,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True);(out/(name+'.log')).write_text(r.stdout)
 records.append(dict(name=name,argv=cmd,exit_code=r.returncode,log=name+'.log'));results[name]=r
(out/'commands.json').write_text(json.dumps(records,indent=2)+'\n')
assert results['original-minimal'].returncode!=0 and 'VerusErasureCtxt has not been initialized' in results['original-minimal'].stdout
for c in ['minimal','borrow-positive']:assert results['patched-'+c].returncode==0,c
for c,code in [('borrow-negative','E0515'),('invalid-unused-const','E0080')]:
 r=results['patched-'+c];assert r.returncode!=0 and 'error['+code+']' in r.stdout,c
print('PASS: original crash, normal-lifetime remedy succeeds, dangling borrow and unused invalid const rejected.')
