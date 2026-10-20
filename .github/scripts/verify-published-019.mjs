import assert from 'node:assert/strict';
import fs from 'node:fs';
import { join } from 'node:path';
import { createHash } from 'node:crypto';
import { verifyTauriSignature } from '../../desktop-tauri/scripts/verify-updater-signature.mjs';
const [directory, planFile, output]=process.argv.slice(2);
const plan=JSON.parse(fs.readFileSync(planFile,'utf8'));
const config=JSON.parse(fs.readFileSync(join(process.env.GITHUB_WORKSPACE,'desktop-tauri/src-tauri/tauri.conf.json'),'utf8'));
for(const [name,digest] of Object.entries(plan.assets)) {
 assert.equal(createHash('sha256').update(fs.readFileSync(join(directory,name))).digest('hex'),digest,`Published bytes changed: ${name}`);
}
const name='Argus-0.1.9-setup.exe';
const signature=fs.readFileSync(join(directory,name+'.sig'),'utf8').trim();
const verified=verifyTauriSignature(join(directory,name),signature,config.plugins.updater.pubkey);
const manifest=JSON.parse(fs.readFileSync(join(directory,'latest.json'),'utf8'));
assert.equal(manifest.version,'0.1.9');
assert.deepEqual(Object.keys(manifest.platforms).sort(),['darwin-aarch64','darwin-x86_64','windows-x86_64']);
assert.equal(manifest.platforms['windows-x86_64'].signature,signature);
assert.equal(manifest.platforms['windows-x86_64'].url,'https://github.com/lbx154/Argus/releases/download/v0.1.9/'+name);
for(const [arch,entry] of Object.entries(plan.preserved_mac_platforms))assert.deepEqual(manifest.platforms[arch],entry);
const result={...verified,public_download_signature_verified:true,public_download_hashes_match_build:true,mac_update_entries_preserved:true,version:'0.1.9',platforms:Object.keys(manifest.platforms),source_commit:plan.source_commit};
fs.writeFileSync(output,JSON.stringify(result,null,2)+'\n',{flag:'wx'});
console.log(JSON.stringify(result,null,2));
