[CmdletBinding()]
param(
    [string]$InstallDirectory,
    [ValidateRange(0, 20)][int]$WaitSeconds = 10
)

$ErrorActionPreference = "Stop"

# Metadata-only canonicalization: handle junctions, short paths and the Win32
# extended path prefix without reading files or trusting process command lines.
function Initialize-ArgusInstallPaths {
    if ("Argus.Installer.Paths" -as [type]) { return }
    Add-Type -TypeDefinition @'
using System;
using System.ComponentModel;
using System.Runtime.InteropServices;
using System.Text;
using Microsoft.Win32.SafeHandles;
namespace Argus.Installer {
    public static class Paths {
        [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
        private static extern SafeFileHandle CreateFile(
            string name, uint access, uint share, IntPtr security,
            uint disposition, uint flags, IntPtr template);
        [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
        private static extern uint GetFinalPathNameByHandle(
            SafeFileHandle handle, StringBuilder path, uint size, uint flags);
        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern SafeFileHandle OpenProcess(uint access, bool inherit, int pid);
        [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
        private static extern bool QueryFullProcessImageName(
            SafeFileHandle handle, uint flags, StringBuilder path, ref int size);
        public static string ProcessImage(int pid) {
            // Query-only access also works from a 32-bit NSIS/PowerShell host
            // against a 64-bit process; Process.MainModule does not.
            using (var handle = OpenProcess(0x1000, false, pid)) {
                if (handle.IsInvalid) throw new Win32Exception(Marshal.GetLastWin32Error());
                var buffer = new StringBuilder(32768);
                int size = buffer.Capacity;
                if (!QueryFullProcessImageName(handle, 0, buffer, ref size))
                    throw new Win32Exception(Marshal.GetLastWin32Error());
                return buffer.ToString();
            }
        }
        public static string Canonical(string path) {
            using (var handle = CreateFile(path, 0, 7, IntPtr.Zero, 3, 0x02000000, IntPtr.Zero)) {
                if (handle.IsInvalid) throw new Win32Exception(Marshal.GetLastWin32Error());
                var buffer = new StringBuilder(32768);
                uint size = GetFinalPathNameByHandle(handle, buffer, (uint)buffer.Capacity, 0);
                if (size == 0 || size >= buffer.Capacity)
                    throw new Win32Exception(Marshal.GetLastWin32Error());
                return buffer.ToString();
            }
        }
    }
}
'@
}

function Resolve-ArgusInstallPath {
    param([Parameter(Mandatory = $true)][string]$Path, [switch]$MustExist)
    $full = [System.IO.Path]::GetFullPath($Path)
    if ($MustExist -or (Test-Path -LiteralPath $full)) {
        Initialize-ArgusInstallPaths
        $full = [Argus.Installer.Paths]::Canonical($full)
    }
    if ($full.StartsWith('\\?\UNC\', [System.StringComparison]::OrdinalIgnoreCase)) {
        $full = '\\' + $full.Substring(8)
    } elseif ($full.StartsWith('\\?\', [System.StringComparison]::OrdinalIgnoreCase)) {
        $full = $full.Substring(4)
    }
    return $full.TrimEnd('\')
}

function Get-ArgusInstallBlockers {
    param(
        [Parameter(Mandatory = $true)][string]$Directory,
        [AllowEmptyCollection()][object[]]$Processes = @()
    )
    $root = Resolve-ArgusInstallPath -Path $Directory -MustExist
    $expected = @(
        'Argus.exe',
        'argus-backend\argus-backend.exe',
        'resources\argus-backend\argus-backend.exe'
    ) | ForEach-Object { Resolve-ArgusInstallPath -Path (Join-Path $root $_) }
    $blocking = 0
    $unknown = 0
    foreach ($process in $Processes) {
        try {
            if ($process.ProcessName -notin @('Argus', 'argus-backend')) { continue }
            $image = [string]$process.Path
            if ([string]::IsNullOrWhiteSpace($image)) { throw 'Process image unavailable' }
            $image = Resolve-ArgusInstallPath -Path $image -MustExist
            foreach ($target in $expected) {
                if ([string]::Equals($image, $target, [System.StringComparison]::OrdinalIgnoreCase)) {
                    $blocking += 1
                    break
                }
            }
        } catch {
            # Inaccessible/disappearing process metadata never authorizes a kill
            # or a replacement. The bounded caller can retry after it exits.
            $unknown += 1
        }
    }
    return [pscustomobject]@{ Blocking = $blocking; Unknown = $unknown }
}

function Invoke-ArgusInstallPreflight {
    param([string]$Directory, [int]$Seconds)
    if ([string]::IsNullOrWhiteSpace($Directory)) { throw 'Install directory required' }
    $resolved = Resolve-ArgusInstallPath -Path $Directory -MustExist
    if ($resolved -eq [System.IO.Path]::GetPathRoot($resolved).TrimEnd('\')) {
        throw 'An installation must not replace a volume root'
    }
    $deadline = [DateTime]::UtcNow.AddSeconds($Seconds)
    do {
        $processes = @()
        try {
            foreach ($name in @('Argus', 'argus-backend')) {
                $processes += [System.Diagnostics.Process]::GetProcessesByName($name)
            }
            $records = @(foreach ($process in $processes) {
                $image = ''
                try { $image = [Argus.Installer.Paths]::ProcessImage($process.Id) } catch { }
                [pscustomobject]@{ ProcessName = $process.ProcessName; Path = $image }
            })
            $result = Get-ArgusInstallBlockers -Directory $Directory -Processes $records
        } finally {
            foreach ($process in $processes) { $process.Dispose() }
        }
        if ($result.Blocking -eq 0 -and $result.Unknown -eq 0) { return 0 }
        if ([DateTime]::UtcNow -ge $deadline) { break }
        Start-Sleep -Milliseconds 200
    } while ($true)
    Write-Host 'Target installation is running, or its process identity cannot be verified. Use Stop backend and quit in that installation, then retry. No process was terminated.'
    return 2
}

# Dot-sourcing exposes only the read-only policy for isolated fixture tests.
# Installer and uninstaller both execute this embedded script with -File.
if ($MyInvocation.InvocationName -ne '.') {
    try {
        exit (Invoke-ArgusInstallPreflight -Directory $InstallDirectory -Seconds $WaitSeconds)
    } catch {
        Write-Host 'Installation process preflight failed. No process was terminated and replacement is blocked.'
        exit 3
    }
}
