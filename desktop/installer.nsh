; Custom NSIS hooks for ASVA.
; The problem this solves: on a SILENT auto-update, electron-builder's default
; shortcut step can be skipped, leaving the owner with no Start-menu / Desktop
; entry - so searching "ASVA" surfaces only the old Setup zip in Downloads and
; they reinstall an OLD version. These macros run during BOTH a fresh install AND
; every (silent) update, so the real app is always findable, independent of
; whether the app itself later runs its own self-heal.

!macro customInstall
  ; Per-user install (perMachine:false) -> $SMPROGRAMS / $DESKTOP are the user's.
  CreateShortCut "$SMPROGRAMS\ASVA.lnk" "$INSTDIR\ASVA.exe" "" "$INSTDIR\ASVA.exe" 0
  CreateShortCut "$DESKTOP\ASVA.lnk" "$INSTDIR\ASVA.exe" "" "$INSTDIR\ASVA.exe" 0
!macroend

!macro customUnInstall
  Delete "$SMPROGRAMS\ASVA.lnk"
  Delete "$DESKTOP\ASVA.lnk"
!macroend
