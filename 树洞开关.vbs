' Treehole toggle (silent): double-click to START if not running, STOP if running.
' No console window, no popup. Log: server.log (same folder).

Set sh  = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
base = fso.GetParentFolderName(WScript.ScriptFullName)
tmp  = sh.ExpandEnvironmentStrings("%TEMP%") & "\treehole_port.txt"
logf = base & "\server.log"

Function IsRunning()
    Dim f, txt
    IsRunning = False
    If fso.FileExists(tmp) Then fso.DeleteFile tmp
    sh.Run "cmd /c netstat -ano -p tcp | findstr "":8311"" | findstr LISTENING > """ & tmp & """", 0, True
    If fso.FileExists(tmp) Then
        If fso.GetFile(tmp).Size > 0 Then
            Set f = fso.OpenTextFile(tmp, 1)
            txt = f.ReadAll
            f.Close
            IsRunning = (InStr(txt, "LISTENING") > 0)
        End If
        fso.DeleteFile tmp
    End If
End Function

If IsRunning() Then
    ' ---- STOP: kill every process listening on 8311 ----
    sh.Run "cmd /c netstat -ano -p tcp | findstr "":8311"" | findstr LISTENING > """ & tmp & """", 0, True
    If fso.FileExists(tmp) Then
        Dim f, line, parts, pid
        Set f = fso.OpenTextFile(tmp, 1)
        Do Until f.AtEndOfStream
            line = Trim(f.ReadLine)
            If InStr(line, "LISTENING") > 0 Then
                parts = Split(line)
                pid = parts(UBound(parts))
                If IsNumeric(pid) And pid <> "0" Then sh.Run "taskkill /F /PID " & pid, 0, True
            End If
        Loop
        f.Close
        fso.DeleteFile tmp
    End If
Else
    ' ---- START: hidden window ----
    sh.CurrentDirectory = base
    sh.Run "cmd /c chcp 65001 >nul & python app.py --no-browser >> """ & logf & """ 2>&1", 0, False
    Dim i
    For i = 1 To 30
        WScript.Sleep 500
        If IsRunning() Then Exit For
    Next
End If
