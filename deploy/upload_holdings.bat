@echo off
REM =========================================================
REM  UT Portfolio Dashboard — Upload Custodian File
REM  Run this each time you receive a new custodian file.
REM =========================================================

SET SERVER=http://YOUR-VM-IP-HERE
SET USERNAME=utfunds
SET PASSWORD=YOUR-PASSWORD-HERE

echo.
echo  UT Portfolio Dashboard — Upload Holdings File
echo  =============================================
echo.

IF "%~1"=="" (
    echo  Usage: Drag and drop a custodian file onto this script,
    echo         OR run: upload_holdings.bat "path\to\file.csv"
    echo.
    pause
    exit /b 1
)

SET FILE=%~1
SET FILENAME=%~nx1

echo  Uploading: %FILENAME%
echo  To server: %SERVER%
echo.

curl -u "%USERNAME%:%PASSWORD%" ^
     -F "file=@%FILE%" ^
     "%SERVER%/api/upload"

IF errorlevel 1 (
    echo.
    echo  ERROR: Upload failed. Check server address and credentials.
) ELSE (
    echo.
    echo  Upload successful! Dashboard will refresh automatically.
)

echo.
pause
