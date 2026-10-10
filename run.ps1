$ErrorActionPreference = 'Stop'
Push-Location $PSScriptRoot
try {
    uv run python -m si
} finally {
    Pop-Location
}
