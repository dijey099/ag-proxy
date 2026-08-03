@echo off
color 0a

title AG IDE Patcher
mode con:cols=100 lines=90
cls
pyinstaller -c --clean -F ag_patcher.py
pause