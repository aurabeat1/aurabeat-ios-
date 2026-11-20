Set WshShell = CreateObject("WScript.Shell")
Dim fso, scriptDir
Set fso = CreateObject("Scripting.FileSystemObject")
scriptDir = fso.GetParentFolderName(WScript.ScriptFullName)
WshShell.Run "cmd /c python -m uvicorn main:app --app-dir """ & scriptDir & """ --port 3000 --host 0.0.0.0", 0, False
