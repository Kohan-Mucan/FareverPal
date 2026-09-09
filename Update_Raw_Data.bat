@echo off
setlocal
set "SRC=%~1"
if "%SRC%"=="" set "SRC=."

echo Select action:
echo  [1] Compile game data (raw_*.py embedded shims, no dev copies)
echo      - shims: raw_data, raw_codex, raw_units, raw_items, raw_skills,
echo               raw_craft, raw_item_drops, raw_locs
echo  [2] Compile game data and Prune assets/data
echo(
set /p "CHOICE=Choice (1-2): "

echo(
echo Updating game data sheets from: %SRC%
call ".\.venv\Scripts\python.exe" compiler.py "%SRC%"

if "%CHOICE%"=="2" (
    echo(
    echo Pruning unused JSON files...
    REM Deletes only sheets absent from compiler.py's prune_unused_jsons
    REM `required` set, which is kept a SUPERSET of the sheets verify_assets.py
    REM requires (item_drops.json / craft.json / job.json included) - so a prune
    REM can never remove a JSON the next build's verify step needs. That
    REM relation is pinned by tests/test_data_layer_sheets.py.
    call ".\.venv\Scripts\python.exe" compiler.py --prune
)

echo(
echo Data update complete.
pause
