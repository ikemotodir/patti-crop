' PATTI CROP launcher - no console window.
' ASCII only on purpose: wscript misreads UTF-8 Japanese and breaks parsing.
' Launches with the bundled embedded Python (python\pythonw.exe).
' Falls back to system pythonw if the bundle is missing.
' Any arguments (e.g. from right-click "Send To") are forwarded to server.py.
Set ws = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
appDir = fso.GetParentFolderName(WScript.ScriptFullName)
ws.CurrentDirectory = appDir
q = Chr(34)
pyw = appDir & "\python\pythonw.exe"
srv = appDir & "\server.py"

argStr = ""
For i = 0 To WScript.Arguments.Count - 1
  argStr = argStr & " " & q & WScript.Arguments(i) & q
Next

If fso.FileExists(pyw) Then
  ws.Run q & pyw & q & " " & q & srv & q & argStr, 0, False
Else
  ws.Run "pythonw " & q & srv & q & argStr, 0, False
End If
