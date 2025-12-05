<#
  .DESCRIPTION
    This script interfaces with Cog-Creators/Red. It is used as a script allowing you to
    easily incorporate it into your workflow (e.g. VSC launch.json).

  .PARAMETER redinstance
    optional redinstance name, default = dissentindev
    this assumes you have already run redbot-setup

  .PARAMETER AddCog
    Flag: if enabled adds a new cog via Cog-Cookiecutter

  .PARAMETER StartBot
    Flag: if enabled starts a Red-DiscordBot instance

  .PARAMETER StartDashboard
    Flag: if enabled starts a Red-Web-Dashboard instance

  .PARAMETER InstallRequirements
    Flag: if enabled creates virtual environments and installs Red-DiscordBot and Red-Web-Dashboard dependencies

  .OUTPUTS
    <None>

  .NOTES
    Author:         Nguyen Tin
    Creation Date:  2025-09-20
    
  .EXAMPLE
    .\StartBot.ps1
#>

param (
    [string]$redinstance = "dissentindev",
    [switch]$AddCog,
    [switch]$StartBot,
    [switch]$StartDashboard,
    [switch]$InstallRequirements
)

#------------------------------------------------------ Preparation -----------------------------------------------#

# Detect OS
$IsWindowsOS = $IsWindows -or ($PSVersionTable.PSVersion.Major -lt 6) -or ($PSVersionTable.Platform -eq 'Win32NT')
$IsLinuxOS = $IsLinux -or ($PSVersionTable.Platform -eq 'Unix')

# Set base venv directory based on OS
if ($IsLinuxOS) {
    $venvBase = Join-Path $env:HOME ".venvs"
} else {
    $venvBase = Join-Path $env:USERPROFILE ".venvs"
}

# Set OS-specific paths for executables
if ($IsLinuxOS) {
    $pythonSuffix = "bin/python"
    $pipSuffix = "bin/pip"
    $activateSuffix = "bin/activate"
} else {
    $pythonSuffix = "Scripts\python.exe"
    $pipSuffix = "Scripts\pip.exe"
    $activateSuffix = "Scripts\Activate.ps1"
}

# python path to red environment
$python = Join-Path -Path (Join-Path -Path $venvBase -ChildPath "redbot") -ChildPath $pythonSuffix
$pythonDashboard = Join-Path -Path (Join-Path -Path $venvBase -ChildPath "reddashboard") -ChildPath $pythonSuffix

#-------------------------------------------------------- Functions -----------------------------------------------#

Function Install-RedEnvironments {
    Write-Host "`n========================================" -ForegroundColor Cyan
    Write-Host "Red-DiscordBot Installation Script" -ForegroundColor Cyan
    Write-Host "========================================`n" -ForegroundColor Cyan
    
    # Initialize error tracking
    $hadError = $false
    $errorMessages = @()
    
    # Log target paths
    $redbotPath = Join-Path $venvBase "redbot"
    $dashboardPath = Join-Path $venvBase "reddashboard"
    Write-Host "Target virtual environment paths:" -ForegroundColor Cyan
    Write-Host "  - redbot: $redbotPath" -ForegroundColor Cyan
    Write-Host "  - reddashboard: $dashboardPath`n" -ForegroundColor Cyan
    
    # Ensure Python 3.11 via uv
    Write-Host "[$(Get-Date -Format 'HH:mm:ss')] Ensuring Python 3.11 via uv..." -ForegroundColor Cyan
    $uvPath = Get-UvPath
    if (-not $uvPath) {
        Write-Host "[$(Get-Date -Format 'HH:mm:ss')] Error: uv command not found. Please install uv first or add it to PATH." -ForegroundColor Red
        Write-Host "Current PATH: $($env:PATH)" -ForegroundColor Yellow
        $hadError = $true
        $errorMessages += "uv command missing"
        return $false
    }

    try {
        & $uvPath python install 3.11 --managed-python | Out-Null
    } catch {
        Write-Host "[$(Get-Date -Format 'HH:mm:ss')] Error: Failed to install Python 3.11 via uv." -ForegroundColor Red
        $hadError = $true
        $errorMessages += "uv python install failed"
        return $false
    }

    $pythonCmd = $null
    try {
        $pythonCmd = (& $uvPath python find 3.11 --managed-python).Trim()
        if (-not (Test-Path $pythonCmd)) {
            $pythonCmd = $null
        }
    } catch {
        $pythonCmd = $null
    }

    if (-not $pythonCmd) {
        Write-Host "[$(Get-Date -Format 'HH:mm:ss')] Error: Could not locate uv-managed Python 3.11." -ForegroundColor Red
        $hadError = $true
        $errorMessages += "Python 3.11 not found after uv install"
        return $false
    }

    try {
        $pythonVersion = & $pythonCmd --version 2>&1
        Write-Host "[$(Get-Date -Format 'HH:mm:ss')] Using: $pythonCmd ($pythonVersion)" -ForegroundColor Green
    } catch {
        Write-Host "[$(Get-Date -Format 'HH:mm:ss')] Error: Failed to run Python 3.11 from uv." -ForegroundColor Red
        $hadError = $true
        $errorMessages += "Failed to execute uv-managed Python"
        return $false
    }
    
    # Create venv directory structure
    Write-Host "`n[$(Get-Date -Format 'HH:mm:ss')] Creating virtual environment directory..." -ForegroundColor Cyan
    if (-not (Test-Path $venvBase)) {
        try {
            New-Item -ItemType Directory -Path $venvBase -Force | Out-Null
            Write-Host "[$(Get-Date -Format 'HH:mm:ss')] Successfully created directory: $venvBase" -ForegroundColor Green
        } catch {
            Write-Host "[$(Get-Date -Format 'HH:mm:ss')] Error: Failed to create directory $venvBase" -ForegroundColor Red
            Write-Host "Error details: $_" -ForegroundColor Red
            $hadError = $true
            $errorMessages += "Failed to create venv base directory"
            return $false
        }
    } else {
        Write-Host "[$(Get-Date -Format 'HH:mm:ss')] Directory already exists: $venvBase" -ForegroundColor Green
    }
    
    # Create redbot virtual environment
    Write-Host "`n[$(Get-Date -Format 'HH:mm:ss')] Creating redbot virtual environment..." -ForegroundColor Cyan
    $redbotPythonPath = Join-Path $redbotPath $pythonSuffix
    if (Test-Path $redbotPythonPath) {
        Write-Host "[$(Get-Date -Format 'HH:mm:ss')] Redbot virtual environment already exists, skipping creation" -ForegroundColor Yellow
    } else {
        & $uvPath venv --python $pythonCmd --managed-python --seed $redbotPath
        if ($LASTEXITCODE -ne 0) {
            Write-Host "[$(Get-Date -Format 'HH:mm:ss')] Error: Failed to create redbot virtual environment" -ForegroundColor Red
            $hadError = $true
            $errorMessages += "Failed to create redbot virtual environment"
            return $false
        }
        Write-Host "[$(Get-Date -Format 'HH:mm:ss')] Successfully created redbot virtual environment" -ForegroundColor Green
    }
    
    # Install redbot dependencies
    Write-Host "`n[$(Get-Date -Format 'HH:mm:ss')] Installing pip and wheel in redbot environment via uv..." -ForegroundColor Cyan
    & $uvPath pip install --python $redbotPythonPath -U pip wheel
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[$(Get-Date -Format 'HH:mm:ss')] Warning: Failed to upgrade pip and wheel. Continuing anyway..." -ForegroundColor Yellow
    } else {
        Write-Host "[$(Get-Date -Format 'HH:mm:ss')] Successfully installed pip and wheel" -ForegroundColor Green
    }
    
    Write-Host "`n[$(Get-Date -Format 'HH:mm:ss')] Installing Red-DiscordBot and d-back via uv... (this may take a few minutes)" -ForegroundColor Cyan
    & $uvPath pip install --python $redbotPythonPath -U Red-DiscordBot d-back
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[$(Get-Date -Format 'HH:mm:ss')] Error: Failed to install Red-DiscordBot and d-back. Manual installation may be required." -ForegroundColor Red
        Write-Host "You can try manually by activating the venv and running: pip install -U Red-DiscordBot d-back" -ForegroundColor Yellow
        $hadError = $true
        $errorMessages += "Failed to install Red-DiscordBot and d-back"
    } else {
        Write-Host "[$(Get-Date -Format 'HH:mm:ss')] Successfully installed Red-DiscordBot and d-back" -ForegroundColor Green
    }
    
    # Create reddashboard virtual environment
    Write-Host "`n[$(Get-Date -Format 'HH:mm:ss')] Creating reddashboard virtual environment..." -ForegroundColor Cyan
    $dashboardPythonPath = Join-Path $dashboardPath $pythonSuffix
    if (Test-Path $dashboardPythonPath) {
        Write-Host "[$(Get-Date -Format 'HH:mm:ss')] Reddashboard virtual environment already exists, skipping creation" -ForegroundColor Yellow
    } else {
        & $uvPath venv --python $pythonCmd --managed-python --seed $dashboardPath
        if ($LASTEXITCODE -ne 0) {
            Write-Host "[$(Get-Date -Format 'HH:mm:ss')] Error: Failed to create reddashboard virtual environment" -ForegroundColor Red
            $hadError = $true
            $errorMessages += "Failed to create reddashboard virtual environment"
            return $false
        }
        Write-Host "[$(Get-Date -Format 'HH:mm:ss')] Successfully created reddashboard virtual environment" -ForegroundColor Green
    }
    
    # Install reddashboard dependencies
    Write-Host "`n[$(Get-Date -Format 'HH:mm:ss')] Installing pip, setuptools, and wheel in reddashboard environment via uv..." -ForegroundColor Cyan
    & $uvPath pip install --python $dashboardPythonPath -U pip setuptools wheel
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[$(Get-Date -Format 'HH:mm:ss')] Warning: Failed to upgrade pip, setuptools, and wheel. Continuing anyway..." -ForegroundColor Yellow
    } else {
        Write-Host "[$(Get-Date -Format 'HH:mm:ss')] Successfully installed pip, setuptools, and wheel" -ForegroundColor Green
    }
    
    Write-Host "`n[$(Get-Date -Format 'HH:mm:ss')] Installing Red-Web-Dashboard via uv... (this may take a few minutes)" -ForegroundColor Cyan
    & $uvPath pip install --python $dashboardPythonPath -U Red-Web-Dashboard
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[$(Get-Date -Format 'HH:mm:ss')] Error: Failed to install Red-Web-Dashboard. Manual installation may be required." -ForegroundColor Red
        Write-Host "You can try manually by activating the venv and running: pip install -U Red-Web-Dashboard" -ForegroundColor Yellow
        $hadError = $true
        $errorMessages += "Failed to install Red-Web-Dashboard"
    } else {
        Write-Host "[$(Get-Date -Format 'HH:mm:ss')] Successfully installed Red-Web-Dashboard" -ForegroundColor Green
    }
    
    # Display post-installation instructions
    Write-Host "`n========================================" -ForegroundColor $(if ($hadError) { "Yellow" } else { "Green" })
    if ($hadError) {
        Write-Host "Installation completed with errors!" -ForegroundColor Yellow
        Write-Host "========================================`n" -ForegroundColor Yellow
        Write-Host "The following errors occurred during installation:" -ForegroundColor Yellow
        foreach ($errMsg in $errorMessages) {
            Write-Host "  - $errMsg" -ForegroundColor Red
        }
        Write-Host "`nPlease address the errors above before proceeding.`n" -ForegroundColor Yellow
    } else {
        Write-Host "Installation completed successfully!" -ForegroundColor Green
        Write-Host "========================================`n" -ForegroundColor Green
    }
    
    # Only show next steps if no errors occurred
    if (-not $hadError) {
        Write-Host "Next steps:" -ForegroundColor Cyan
        Write-Host "1. Activate the redbot virtual environment:" -ForegroundColor Cyan
        
        if ($IsLinuxOS) {
            $activateCmd = "source $(Join-Path $redbotPath 'bin/activate')"
            Write-Host "   $activateCmd" -ForegroundColor Yellow
        } else {
            $activateCmd = Join-Path $redbotPath $activateSuffix
            Write-Host "   & `"$activateCmd`"" -ForegroundColor Yellow
        }
        
        Write-Host "`n2. Run the Red-DiscordBot setup:" -ForegroundColor Cyan
        Write-Host "   redbot-setup" -ForegroundColor Yellow
        
        Write-Host "`n3. This script assumes your bot name is 'dissentindev'" -ForegroundColor Cyan
        Write-Host "   If you use a different name, update the `$redinstance parameter" -ForegroundColor Cyan
        
        Write-Host "`n4. For Discord bot configuration, refer to SETUP.md" -ForegroundColor Cyan
        Write-Host "   Additional configuration steps for intents and privileged gateway intents are documented there.`n" -ForegroundColor Cyan
    }
    
    return (-not $hadError)
}

Function Assert-Errors {
    param (
        [string]$Command,
        [parameter(ValueFromRemainingArguments = $true)][string[]]$Arguments
    )

    # Resolve either an absolute path or something on PATH
    $resolvedCommand = $null

    if (Test-Path $Command) {
        $resolvedCommand = $Command
    } else {
        $commandInfo = Get-Command $Command -ErrorAction SilentlyContinue
        if ($commandInfo) {
            $resolvedCommand = if ($commandInfo.Source) { $commandInfo.Source } elseif ($commandInfo.Definition) { $commandInfo.Definition } else { $commandInfo.Path }
        }
    }

    if (-not $resolvedCommand) {
        Write-Host "[$(Get-Date -Format 'HH:mm:ss')] Error: Command not found: $Command" -ForegroundColor Red
        return
    }

    & $resolvedCommand @Arguments

    if ($LASTEXITCODE -ne 0) {
        Write-Host ("$($TXT_RED)Error calling {0} {1}$($TXT_CLEAR)" -f $Command, [System.String]::Join(" ", $Arguments))
    }
}

Function Get-UvPath {
    $cmd = Get-Command uv -ErrorAction SilentlyContinue
    if ($cmd) {
        $path = $cmd.Source
        if (-not $path) { $path = $cmd.Path }
        if (-not $path) { $path = $cmd.Definition }
        if ($path) { return $path }
    }

    $candidates = @()
    if ($IsLinuxOS) {
        $candidates += @(
            (Join-Path $env:HOME ".local/bin/uv"),
            "/usr/local/bin/uv",
            "/usr/bin/uv"
        )
    } else {
        $candidates += @(
            (Join-Path $env:LOCALAPPDATA "uv/uv.exe"),
            (Join-Path $env:USERPROFILE "AppData/Local/uv/uv.exe"),
            (Join-Path $env:USERPROFILE "AppData/Local/Programs/Python/Scripts/uv.exe"),
            (Join-Path $env:USERPROFILE "AppData/Roaming/Python/Scripts/uv.exe")
        )
    }

    foreach ($candidate in $candidates) {
        if ($candidate -and (Test-Path $candidate)) {
            return $candidate
        }
    }

    return $null
}

Function Ensure-VirtualEnvironments {
    param(
        [switch]$RequireRedbot,
        [switch]$RequireDashboard
    )

    $missing = @()
    if ($RequireRedbot -and -not (Test-Path $python)) {
        $missing += "redbot"
    }
    if ($RequireDashboard -and -not (Test-Path $pythonDashboard)) {
        $missing += "reddashboard"
    }

    if ($missing.Count -eq 0) {
        return $true
    }

    Write-Host "`n[$(Get-Date -Format 'HH:mm:ss')] Missing virtual environment(s): $($missing -join ', ')." -ForegroundColor Yellow
    Write-Host "Running installer to create required environment(s)..." -ForegroundColor Yellow
    $installResult = Install-RedEnvironments

    if (-not $installResult) {
        Write-Host "[$(Get-Date -Format 'HH:mm:ss')] Unable to create required environment(s). Aborting." -ForegroundColor Red
        return $false
    }

    $postCheckFailed = ($RequireRedbot -and -not (Test-Path $python)) -or ($RequireDashboard -and -not (Test-Path $pythonDashboard))
    if ($postCheckFailed) {
        Write-Host "[$(Get-Date -Format 'HH:mm:ss')] Installation completed but required executables were not found." -ForegroundColor Red
        return $false
    }

    return $true
}

#-------------------------------------------------------- Script --------------------------------------------------#

if ($InstallRequirements) {
    if (-not (Install-RedEnvironments)) {
        exit 1
    }
}

$needsRedbot = $AddCog -or $StartBot
$needsDashboard = $StartDashboard

if ($needsRedbot -or $needsDashboard) {
    if (-not (Ensure-VirtualEnvironments -RequireRedbot:$needsRedbot -RequireDashboard:$needsDashboard)) {
        exit 1
    }
}

if ($AddCog) {
    Assert-Errors $python -m cookiecutter https://github.com/Cog-Creators/cog-cookiecutter
}

if ($StartBot) {
    Assert-Errors $python -m redbot $redinstance --dev --rpc
}

if ($StartDashboard) {
    Assert-Errors $pythonDashboard -m reddash
}
