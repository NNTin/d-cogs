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
    
    # Check Python availability
    Write-Host "[$(Get-Date -Format 'HH:mm:ss')] Checking Python availability..." -ForegroundColor Cyan
    
    # Try to find Python interpreter with fallbacks
    $pythonCmd = $null
    $pythonCandidates = @('python')
    
    if ($IsLinuxOS) {
        $pythonCandidates += 'python3'
    } else {
        $pythonCandidates += @('py -3', 'python3')
    }
    
    foreach ($candidate in $pythonCandidates) {
        try {
            $testVersion = $null
            if ($candidate -eq 'py -3') {
                $testVersion = & py -3 --version 2>&1
            } else {
                $testVersion = & $candidate --version 2>&1
            }
            
            if ($testVersion -match 'Python \d+\.\d+') {
                $pythonCmd = $candidate
                Write-Host "[$(Get-Date -Format 'HH:mm:ss')] Found Python using: $candidate" -ForegroundColor Green
                break
            }
        } catch {
            # Candidate not available, try next
            continue
        }
    }
    
    if (-not $pythonCmd) {
        Write-Host "[$(Get-Date -Format 'HH:mm:ss')] Error: Python not found in PATH. Please install Python 3.8-3.11 first." -ForegroundColor Red
        Write-Host "Tried: $($pythonCandidates -join ', ')" -ForegroundColor Red
        $hadError = $true
        $errorMessages += "Python interpreter not found"
        return $false
    }
    
    # Get and validate Python version
    try {
        if ($pythonCmd -eq 'py -3') {
            $pythonVersion = & py -3 --version 2>&1
        } else {
            $pythonVersion = & $pythonCmd --version 2>&1
        }
        Write-Host "[$(Get-Date -Format 'HH:mm:ss')] Found: $pythonVersion" -ForegroundColor Green
        
        # Parse version and check range
        if ($pythonVersion -match 'Python (\d+)\.(\d+)') {
            $major = [int]$matches[1]
            $minor = [int]$matches[2]
            if ($major -eq 3 -and $minor -ge 8 -and $minor -le 11) {
                Write-Host "[$(Get-Date -Format 'HH:mm:ss')] Python version is compatible (3.8-3.11)" -ForegroundColor Green
            } else {
                Write-Host "[$(Get-Date -Format 'HH:mm:ss')] Error: Python version $major.$minor is not supported. Red-DiscordBot requires Python 3.8-3.11." -ForegroundColor Red
                Write-Host "Please install a compatible Python version and try again." -ForegroundColor Red
                $hadError = $true
                $errorMessages += "Incompatible Python version ($major.$minor)"
                return $false
            }
        }
    } catch {
        Write-Host "[$(Get-Date -Format 'HH:mm:ss')] Error: Failed to retrieve Python version." -ForegroundColor Red
        $hadError = $true
        $errorMessages += "Failed to retrieve Python version"
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
        if ($pythonCmd -eq 'py -3') {
            & py -3 -m venv $redbotPath
        } else {
            & $pythonCmd -m venv $redbotPath
        }
        if ($LASTEXITCODE -ne 0) {
            Write-Host "[$(Get-Date -Format 'HH:mm:ss')] Error: Failed to create redbot virtual environment" -ForegroundColor Red
            $hadError = $true
            $errorMessages += "Failed to create redbot virtual environment"
            return $false
        }
        Write-Host "[$(Get-Date -Format 'HH:mm:ss')] Successfully created redbot virtual environment" -ForegroundColor Green
    }
    
    # Install redbot dependencies
    $redbotPip = Join-Path $redbotPath $pipSuffix
    Write-Host "`n[$(Get-Date -Format 'HH:mm:ss')] Installing pip and wheel in redbot environment..." -ForegroundColor Cyan
    & $redbotPip install -U pip wheel
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[$(Get-Date -Format 'HH:mm:ss')] Warning: Failed to upgrade pip and wheel. Continuing anyway..." -ForegroundColor Yellow
    } else {
        Write-Host "[$(Get-Date -Format 'HH:mm:ss')] Successfully installed pip and wheel" -ForegroundColor Green
    }
    
    Write-Host "`n[$(Get-Date -Format 'HH:mm:ss')] Installing Red-DiscordBot and d-back... (this may take a few minutes)" -ForegroundColor Cyan
    & $redbotPip install -U Red-DiscordBot d-back
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
        if ($pythonCmd -eq 'py -3') {
            & py -3 -m venv $dashboardPath
        } else {
            & $pythonCmd -m venv $dashboardPath
        }
        if ($LASTEXITCODE -ne 0) {
            Write-Host "[$(Get-Date -Format 'HH:mm:ss')] Error: Failed to create reddashboard virtual environment" -ForegroundColor Red
            $hadError = $true
            $errorMessages += "Failed to create reddashboard virtual environment"
            return $false
        }
        Write-Host "[$(Get-Date -Format 'HH:mm:ss')] Successfully created reddashboard virtual environment" -ForegroundColor Green
    }
    
    # Install reddashboard dependencies
    $dashboardPip = Join-Path $dashboardPath $pipSuffix
    Write-Host "`n[$(Get-Date -Format 'HH:mm:ss')] Installing pip, setuptools, and wheel in reddashboard environment..." -ForegroundColor Cyan
    & $dashboardPip install -U pip setuptools wheel
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[$(Get-Date -Format 'HH:mm:ss')] Warning: Failed to upgrade pip, setuptools, and wheel. Continuing anyway..." -ForegroundColor Yellow
    } else {
        Write-Host "[$(Get-Date -Format 'HH:mm:ss')] Successfully installed pip, setuptools, and wheel" -ForegroundColor Green
    }
    
    Write-Host "`n[$(Get-Date -Format 'HH:mm:ss')] Installing Red-Web-Dashboard... (this may take a few minutes)" -ForegroundColor Cyan
    & $dashboardPip install -U Red-Web-Dashboard
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

    & (Get-Command $Command -Type Application -TotalCount 1) $Arguments

    if ($LASTEXITCODE -ne 0) {
        Write-Host ("$($TXT_RED)Error calling {0} {1}$($TXT_CLEAR)" -f $Command, [System.String]::Join(" ", $Arguments))
    }
}

#-------------------------------------------------------- Script --------------------------------------------------#

if ($InstallRequirements) {
    Install-RedEnvironments
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