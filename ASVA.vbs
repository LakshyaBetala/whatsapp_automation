' ASVA — daily launcher. Double-click this (or pin it to the taskbar).
' Starts the whole app with NO console window. Everything runs inside ASVA:
' backend, both WhatsApp numbers, the Tally watcher, and the dashboard.
Dim sh, fso, here
Set sh = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
here = Left(WScript.ScriptFullName, InStrRev(WScript.ScriptFullName, "\"))

If Not fso.FileExists(here & "desktop\node_modules\.bin\electron.cmd") Then
  MsgBox "ASVA setup abhi adhura hai." & vbCrLf & _
         "Pehle SETUP.bat chalayein, phir ASVA.vbs.", 48, "ASVA"
Else
  sh.CurrentDirectory = here & "desktop"
  sh.Run "cmd /c "".\node_modules\.bin\electron.cmd"" .", 0, False
End If
