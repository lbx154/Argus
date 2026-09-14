import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';

/** Read only. SPI_GETCLIENTAREAANIMATION, verified against the installed Windows SDK. */
export function windowsMotionPreference() {
  assert.equal(process.platform, 'win32');
  const script = String.raw`
$ErrorActionPreference='Stop'
$ProgressPreference='SilentlyContinue'
[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new()
Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
public static class ArgusMotionQuery {
 [DllImport("user32.dll", SetLastError=true)]
 public static extern bool SystemParametersInfo(uint action,uint value,out int result,uint flags);
}
'@
$value=0
$ok=[ArgusMotionQuery]::SystemParametersInfo(0x1042,0,[ref]$value,0)
if (-not $ok) { throw 'Read-only Windows animation preference query failed' }
[pscustomobject]@{clientAreaAnimationEnabled=($value -ne 0);reducedMotion=($value -eq 0)} | ConvertTo-Json -Compress
`;
  const result = spawnSync('powershell.exe', ['-NoProfile', '-NonInteractive', '-EncodedCommand',
    Buffer.from(script, 'utf16le').toString('base64')], { encoding: 'utf8', windowsHide: true, timeout: 20000 });
  assert.equal(result.status, 0, 'Could not read Windows animation preference; do not infer a default.');
  const value = JSON.parse(result.stdout.trim().replace(/^\uFEFF/, ''));
  assert.equal(typeof value.reducedMotion, 'boolean');
  return value;
}
