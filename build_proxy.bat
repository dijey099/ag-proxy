@echo off
color 0a

title AG IDE Proxy
mode con:cols=100 lines=90
cls
pyinstaller -c --clean -F ag_proxy.py
pause