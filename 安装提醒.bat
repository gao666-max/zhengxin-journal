@echo off
schtasks /Create /F /TN "ZhengXinReminder" /TR "D:\mini\pythonw.exe %~dp0remind.py" /SC DAILY /ST 21:00
pause
