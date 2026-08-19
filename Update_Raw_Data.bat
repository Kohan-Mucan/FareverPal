@echo off
setlocal
set "SRC=%~1"
if "%SRC%"=="" set "SRC=."

echo Select action:
echo  [1] Compile game data (raw_*.py embedded shims + dev .json copies)
echo      - shims: raw_data, raw_codex, raw_units, raw_items, raw_skills,
echo               raw_craft (craft.json + job.json), raw_item_drops (item_drops.json)
echo  [2] Compile game data and Prune assets/data
echo(
set /p "CHOICE=Choice (1-2): "

echo(
echo Updating game data sheets from: %SRC%
call ".\.venv\Scripts\python.exe" compiler.py "%SRC%"

if "%CHOICE%"=="2" (
    echo(
    echo Pruning unused JSON files...
    call ".\.venv\Scripts\python.exe" compiler.py --prune
)

echo(
echo Data update complete.
pause
