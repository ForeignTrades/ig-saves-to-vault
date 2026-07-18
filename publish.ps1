# One-shot publisher: creates the public GitHub repo and pushes this folder.
# Run:  powershell -ExecutionPolicy Bypass -File publish.ps1
$ErrorActionPreference = "Stop"
$dir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $dir
$name = "ig-saves-to-vault"

if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    Write-Host "git not found - install Git for Windows first: https://git-scm.com" -ForegroundColor Red
    exit 1
}

if (-not (Test-Path "$dir\.git")) {
    git init -b main | Out-Null
    if (-not (git config user.email)) {
        git config user.name "Daniel Somiah"
        git config user.email "daniel.somiah@gmail.com"
    }
    git add -A
    git commit -m "Initial release: Instagram saves to Obsidian vault pipeline" | Out-Null
    Write-Host "Local repo created."
}

$gh = Get-Command gh -ErrorAction SilentlyContinue
if ($gh) {
    gh repo create $name --public --source . --push
    $login = gh api user -q .login
    Write-Host "Published: https://github.com/$login/$name" -ForegroundColor Green
} else {
    Write-Host "GitHub CLI not found - two quick steps instead:" -ForegroundColor Yellow
    Write-Host "  1) Create an empty PUBLIC repo named '$name' at https://github.com/new"
    Write-Host "     (no README, no .gitignore - this folder already has them)."
    $user = Read-Host "  2) Enter your GitHub username"
    git remote remove origin 2>$null
    git remote add origin "https://github.com/$user/$name.git"
    git push -u origin main
    Write-Host "Published: https://github.com/$user/$name" -ForegroundColor Green
}

# publish.ps1 is a local helper - keep it out of the public repo.
git rm --cached publish.ps1 2>$null | Out-Null
Add-Content "$dir\.gitignore" "publish.ps1"
git add .gitignore
git commit -m "Ignore local publish helper" | Out-Null
git push 2>$null
