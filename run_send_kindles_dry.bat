@echo off 
REM Temporary environment vars for this run (available to the python process)
set "SMTP_SERVER=smtp.gmail.com" 
set "SMTP_PORT=587" 
set "EMAIL_USER=evelynma.b7@gmail.com" 
set "EMAIL_PASS=wrxhcolzuaaqbkcg" 
REM Run script with --dry-run and capture stdout+stderr into the log 

python "C:\Users\vinig\OneDrive\Documentos\aaaaa\scripts\send_kindles.py" --dry-run >> "C:\Users\vinig\OneDrive\Documentos\aaaaa\scripts\send_kindles.log" 2>&1