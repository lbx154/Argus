; The desktop normally hides to the tray, so a graceful close cannot prove
; Argus has released its files. This hook only runs in an explicit installer or
; verified Tauri updater transaction, where replacement is intentional.
!macro NSIS_HOOK_PREINSTALL
  nsExec::ExecToLog '"$SYSDIR\taskkill.exe" /F /T /IM "Argus.exe"'
  nsExec::ExecToLog '"$SYSDIR\taskkill.exe" /F /T /IM "argus-backend.exe"'
  Sleep 750
!macroend
