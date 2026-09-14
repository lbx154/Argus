; Tauri's default check terminates processes by executable name, including
; independent previews. Replace it for BOTH installation and uninstallation.
; The embedded metadata-only guard waits for this installation to exit, never
; terminates processes, and blocks replacement when identity is unavailable.
!ifmacrodef CheckIfAppIsRunning
  !macroundef CheckIfAppIsRunning
!else
  !error "Tauri process-check macro changed; review installer isolation before building."
!endif

!define ARGUS_INSTALL_GUARD_SOURCE "${__FILEDIR__}\..\scripts\installer-preflight.ps1"

!macro CheckIfAppIsRunning executableName productName
  !define ArgusCheckID ${__LINE__}
  Push $R0
  Push $R1
  InitPluginsDir
  File /oname=$PLUGINSDIR\argus-installer-preflight.ps1 "${ARGUS_INSTALL_GUARD_SOURCE}"
  argus_check_retry_${ArgusCheckID}:
    nsExec::ExecToStack /TIMEOUT=30000 '"$SYSDIR\WindowsPowerShell\v1.0\powershell.exe" -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "$PLUGINSDIR\argus-installer-preflight.ps1" -InstallDirectory "$INSTDIR" -WaitSeconds 10'
    Pop $R0
    Pop $R1
    StrCmp $R0 "0" argus_check_done_${ArgusCheckID}
    IfSilent argus_check_abort_${ArgusCheckID}
    MessageBox MB_RETRYCANCEL|MB_ICONEXCLAMATION "This Argus installation is still running, or its identity could not be verified. Use 'Stop backend and quit' in this installation, then retry. Other installations will not be terminated." IDRETRY argus_check_retry_${ArgusCheckID}
  argus_check_abort_${ArgusCheckID}:
    Pop $R1
    Pop $R0
    SetErrorLevel 2
    Abort "Argus installation is not safely stopped. No process was terminated."
  argus_check_done_${ArgusCheckID}:
    Pop $R1
    Pop $R0
  !undef ArgusCheckID
!macroend
