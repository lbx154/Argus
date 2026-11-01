param(
  [Parameter(Mandatory=$true)][int]$TargetProcessId,
  [Parameter(Mandatory=$true)][string]$ExpectedExecutable,
  [ValidateSet('none','maximize','minimize','restore')][string]$Operation = 'none',
  [string]$Screenshot
)
$ErrorActionPreference = 'Stop'
$process = Get-Process -Id $TargetProcessId
if ([IO.Path]::GetFullPath($process.Path) -ne [IO.Path]::GetFullPath($ExpectedExecutable)) {
  throw 'Refusing a window outside the test-owned executable.'
}
Add-Type -AssemblyName System.Drawing
Add-Type -ReferencedAssemblies System.Drawing -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
public static class CaptionInspection {
  public delegate bool EnumWindowsProc(IntPtr hwnd, IntPtr value);
  [DllImport("user32.dll")] public static extern bool EnumWindows(EnumWindowsProc callback, IntPtr value);
  [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr hwnd, out uint pid);
  [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr hwnd);
  public static IntPtr FindMainWindow(int processId) {
    IntPtr selected = IntPtr.Zero; long largest = 0;
    EnumWindows(delegate(IntPtr hwnd, IntPtr unused) {
      uint pid; GetWindowThreadProcessId(hwnd, out pid);
      if (pid != processId || !IsWindowVisible(hwnd)) return true;
      if ((GetWindowLongW(hwnd, -16) & 0x00C00000) != 0x00C00000) return true;
      if (IsIconic(hwnd)) { selected = hwnd; largest = long.MaxValue; return true; }
      Rect rect; if (!GetWindowRect(hwnd, out rect)) return true;
      long area = (long)(rect.Right - rect.Left) * (rect.Bottom - rect.Top);
      if (area > largest && rect.Right - rect.Left >= 640 && rect.Bottom - rect.Top >= 400) { largest = area; selected = hwnd; }
      return true;
    }, IntPtr.Zero);
    return selected;
  }
  [StructLayout(LayoutKind.Sequential)] public struct Rect { public int Left, Top, Right, Bottom; }
  [DllImport("dwmapi.dll")] public static extern int DwmGetWindowAttribute(IntPtr hwnd, int attribute, out uint value, int size);
  [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr hwnd, out Rect rect);
  [DllImport("user32.dll")] public static extern bool PrintWindow(IntPtr hwnd, IntPtr hdc, uint flags);
  [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr hwnd, int command);
  [DllImport("user32.dll")] public static extern bool IsIconic(IntPtr hwnd);
  [DllImport("user32.dll")] public static extern bool IsZoomed(IntPtr hwnd);
  [DllImport("user32.dll")] public static extern int GetWindowLongW(IntPtr hwnd, int index);
  [DllImport("user32.dll")] public static extern uint GetDpiForWindow(IntPtr hwnd);
  [DllImport("user32.dll")] public static extern IntPtr SetThreadDpiAwarenessContext(IntPtr context);
  public static string CaptionColor(System.Drawing.Bitmap bitmap, uint dpi) {
    var counts = new System.Collections.Generic.Dictionary<int, int>();
    double scale = dpi / 96.0;
    int left = (int)(80 * scale), right = bitmap.Width - (int)(190 * scale);
    for (int y = (int)(12 * scale); y < Math.Min(bitmap.Height, (int)(28 * scale)); y += 2)
      for (int x = left; x < right; x += 4) {
        int color = bitmap.GetPixel(x, y).ToArgb() & 0xFFFFFF;
        if (!counts.ContainsKey(color)) counts[color] = 0;
        counts[color]++;
      }
    int selected = 0, maximum = 0;
    foreach (var pair in counts) if (pair.Value > maximum) { selected = pair.Key; maximum = pair.Value; }
    if (maximum == 0) throw new Exception("Caption sample is empty.");
    return "#" + selected.ToString("x6");
  }
}
'@
[void][CaptionInspection]::SetThreadDpiAwarenessContext([IntPtr](-4))
# Process.MainWindowHandle may select a debug console/helper, not Tauri's window.
$handle = [CaptionInspection]::FindMainWindow($TargetProcessId)
if ($handle -eq [IntPtr]::Zero) { throw 'The test-owned native app window was not found.' }
if ($Operation -ne 'none') {
  $command = @{ maximize = 3; minimize = 6; restore = 9 }[$Operation]
  [void][CaptionInspection]::ShowWindow($handle, $command)
  Start-Sleep -Milliseconds 250
}
function Read-Attribute([int]$attribute) {
  [uint32]$value = 0
  $result = [CaptionInspection]::DwmGetWindowAttribute($handle, $attribute, [ref]$value, 4)
  # Some DWM versions accept these SET attributes but reject GET with E_INVALIDARG.
  # Whole-window pixels below are the visual assertion, never a WebView screenshot.
  if ($result -lt 0) { return $null }
  return $value
}
function Hex-Color([uint32]$value) {
  return '#{0:x2}{1:x2}{2:x2}' -f ($value -band 255), (($value -shr 8) -band 255), (($value -shr 16) -band 255)
}
$style = [CaptionInspection]::GetWindowLongW($handle, -16)
$caption = Read-Attribute 35
$text = Read-Attribute 36
$border = Read-Attribute 34
$result = [ordered]@{
  caption = $null
  captionAttribute = $(if($null -ne $caption){Hex-Color $caption}else{$null})
  text = $(if($null -ne $text){Hex-Color $text}else{$null})
  border = $(if($null -ne $border){Hex-Color $border}else{$null})
  immersiveDark = Read-Attribute 20
  minimized = [CaptionInspection]::IsIconic($handle)
  maximized = [CaptionInspection]::IsZoomed($handle)
  nativeCaption = (($style -band 0x00C00000) -eq 0x00C00000)
  minimizeButton = (($style -band 0x00020000) -ne 0)
  maximizeButton = (($style -band 0x00010000) -ne 0)
  systemMenu = (($style -band 0x00080000) -ne 0)
}
if (-not $result.minimized) {
  if ($Screenshot -and (Test-Path -LiteralPath $Screenshot)) { throw 'Refusing to overwrite an existing screenshot.' }
  $rect = New-Object CaptionInspection+Rect
  if (-not [CaptionInspection]::GetWindowRect($handle, [ref]$rect)) { throw 'Window rectangle unavailable.' }
  $bitmap = New-Object Drawing.Bitmap(($rect.Right - $rect.Left), ($rect.Bottom - $rect.Top))
  $graphics = [Drawing.Graphics]::FromImage($bitmap)
  $dc = $graphics.GetHdc()
  try { if (-not [CaptionInspection]::PrintWindow($handle, $dc, 2)) { throw 'Whole-window capture failed.' } }
  finally { $graphics.ReleaseHdc($dc); $graphics.Dispose() }
  try {
    $dpi = [CaptionInspection]::GetDpiForWindow($handle)
    $result.width = $bitmap.Width; $result.height = $bitmap.Height; $result.dpi = $dpi
    $result.caption = [CaptionInspection]::CaptionColor($bitmap, $dpi)
    if ($Screenshot) { $bitmap.Save($Screenshot, [Drawing.Imaging.ImageFormat]::Png) }
  } finally { $bitmap.Dispose() }
  $result.captureIncludesNativeFrame = $true
}
$result | ConvertTo-Json -Compress
