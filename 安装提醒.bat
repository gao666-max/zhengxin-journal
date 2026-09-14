@echo off
set "PYW=pythonw.exe"
for /f "delims=" %%i in ('where pythonw.exe 2^>nul') do set "PYW=%%i"
schtasks /Create /F /TN "ZhengXinReminder" /TR "%PYW% %~dp0remind.py" /SC DAILY /ST 21:00
pause
