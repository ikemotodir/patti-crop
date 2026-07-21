' PATTI CROP launcher - no console window
Set ws = CreateObject("WScript.Shell")
ws.CurrentDirectory = CreateObject("Scripting.FileSystemObject").GetParentFolderName(WScript.ScriptFullName)
ws.Run "pythonw server.py", 0, False
