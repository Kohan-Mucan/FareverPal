# release.ps1 - create the GitHub repo + tagged release for Farever Pal.
#
# PREREQUISITE: authenticate the GitHub CLI first:
#     gh auth login
#
# Run this script from the companion\ directory:
#     powershell -ExecutionPolicy Bypass -File release.ps1
#
# It is idempotent-ish: if the repo already exists it skips creation and just
# pushes; if the release already exists `gh release create` will error (delete
# it first with `gh release delete v<version>` if you need to re-cut).

$ErrorActionPreference = 'Stop'
Set-Location -Path $PSScriptRoot

# --- read version from the package ---
$initPy  = Join-Path $PSScriptRoot 'farever_companion\__init__.py'
$verLine = Select-String -Path $initPy -Pattern '__version__\s*=\s*"([^"]+)"' | Select-Object -First 1
if (-not $verLine) { throw "Could not find __version__ in $initPy" }
$version = $verLine.Matches[0].Groups[1].Value
$tag     = "v$version"
$exe     = Join-Path $PSScriptRoot 'dist\FareverPal.exe'

Write-Host "Farever Pal version: $version  (tag $tag)"

# --- pre-flight integrity guard ------------------------------------------
# The companion is read-only: it has no injector and no write path in the tree.
# Abort if:
#   1) the working tree is dirty (the released exe must come from committed
#      source, never local edits), or
#   2) any memory-WRITE primitive has crept into the Python package
#      (no inject module, no WriteProcessMemory / PROCESS_VM_WRITE).
$dirty = git status --porcelain
if ($LASTEXITCODE -ne 0) { throw "git status failed; cannot verify a clean tree." }
if ($dirty) {
    Write-Host $dirty
    throw "Working tree is dirty. Commit or stash all changes before releasing - the release exe must be built from committed source."
}

$pkgDir = Join-Path $PSScriptRoot 'farever_companion'
$injectPy = Join-Path $pkgDir 'core\inject.py'
if (Test-Path $injectPy) {
    throw "Write path regression: '$injectPy' exists. The companion is read-only - refusing to release a build with an injector."
}
$forbidden = 'WriteProcessMemory', 'PROCESS_VM_WRITE'
$hits = Select-String -Path (Join-Path $pkgDir '*.py') -Pattern $forbidden -Recurse -List
if ($hits) {
    $hits | ForEach-Object { Write-Host "  $($_.Filename): $($_.Pattern)" }
    throw "Write path regression: a memory-write token reappeared in the package source (see above). The companion is read-only - refusing to release."
}
Write-Host "Pre-flight OK: clean tree + no memory-write path present in the package."

# --- confirm auth ---
gh auth status
if ($LASTEXITCODE -ne 0) { throw "Not authenticated. Run 'gh auth login' first." }

# --- confirm the built exe exists ---
if (-not (Test-Path $exe)) {
    throw "Missing $exe. Build it first: build-mod.bat"
}
$exeMB = [math]::Round((Get-Item $exe).Length / 1MB, 1)
Write-Host "Release asset: $exe ($exeMB MB)"

# --- create the repo (public) + push, if origin doesn't exist yet ---
$hasOrigin = $false
git remote get-url origin 2>$null | Out-Null
if ($LASTEXITCODE -eq 0) { $hasOrigin = $true }

if (-not $hasOrigin) {
    Write-Host "Creating public repo 'FareverPal' and pushing main..."
    gh repo create FareverPal --public --source=. --remote=origin --push
    if ($LASTEXITCODE -ne 0) { throw "gh repo create failed." }
} else {
    Write-Host "origin already configured; pushing main..."
    git push -u origin main
    if ($LASTEXITCODE -ne 0) { throw "git push failed." }
}

# --- create the release with the exe attached ---
$notes = @"
Farever Pal $tag

A read-only, out-of-process live companion overlay for Farever
(entity/loot overlay, DPS meter, minimap, offline loot predictor).

Download ``FareverPal.exe`` below and run it. Self-contained, no install.
"@

Write-Host "Creating release $tag with FareverPal.exe attached..."
gh release create $tag $exe --title "Farever Pal $version" --notes $notes
if ($LASTEXITCODE -ne 0) { throw "gh release create failed." }

# --- report the asset download URL (for GITHUB_RELEASE_URL in the website .env) ---
$repo = (gh repo view --json nameWithOwner -q .nameWithOwner)
$assetUrl = "https://github.com/$repo/releases/download/$tag/FareverPal.exe"
Write-Host ""
Write-Host "DONE. Release asset download URL (use for GITHUB_RELEASE_URL):"
Write-Host "  $assetUrl"
