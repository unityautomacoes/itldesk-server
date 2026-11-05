# Compila o ITLDesk Server para Windows e copia os binários para itldesk\dist
# Requisitos: Rust (https://rustup.rs) + Visual Studio Build Tools (C++)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Push-Location $root
try {
    cargo build --release --bin hbbs --bin hbbr --bin rustdesk-utils
    if ($LASTEXITCODE -ne 0) { throw "cargo build falhou ($LASTEXITCODE)" }
    $dist = Join-Path $PSScriptRoot 'dist'
    New-Item -ItemType Directory -Force $dist | Out-Null
    Copy-Item target\release\hbbs.exe, target\release\hbbr.exe $dist -Force
    Copy-Item target\release\rustdesk-utils.exe (Join-Path $dist 'itldesk-utils.exe') -Force
    Write-Host "Binarios gerados em $dist"
} finally {
    Pop-Location
}
