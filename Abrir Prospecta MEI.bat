@echo off
chcp 65001 >nul
title Prospecta MEI
cd /d "%~dp0"

if exist ".venv\Scripts\python.exe" goto abrir

set "PY="
where py >nul 2>nul && set "PY=py -3"
if not defined PY (
  where python >nul 2>nul && set "PY=python"
)
if not defined PY (
  echo.
  echo  O Python nao esta instalado neste computador.
  echo  Baixe em https://www.python.org/downloads/ e, na instalacao,
  echo  marque a opcao "Add python.exe to PATH". Depois abra este arquivo de novo.
  echo.
  pause
  exit /b 1
)

echo.
echo  Preparando o sistema pela primeira vez. Isso leva 1 ou 2 minutos...
echo.
%PY% -m venv .venv
if errorlevel 1 (
  echo  Nao foi possivel preparar o ambiente do Python.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -q -r requirements.txt
if errorlevel 1 (
  echo  Nao foi possivel instalar os componentes. Verifique a internet e tente de novo.
  rmdir /s /q .venv
  pause
  exit /b 1
)

:abrir
".venv\Scripts\python.exe" app.py
if errorlevel 1 pause
