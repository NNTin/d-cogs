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

  .OUTPUTS
    <None>

  .NOTES
    Version:        1.1
    Author:         Nguyen Tin
    Creation Date:  2025-09-20
    Purpose/Change: dev script is used for a new bot
    
  .EXAMPLE
    .\StartBot.ps1
#>

param (
    [string]$redinstance = "dissentindev",
    [switch]$AddCog,
    [switch]$StartBot,
    [switch]$StartDashboard
)

#------------------------------------------------------ Preparation -----------------------------------------------#

# python path to red environment
$python = "$env:USERPROFILE\.venvs\redbot\Scripts\python.exe"
$pythonDashboard = "$env:USERPROFILE\.venvs\reddashboard\Scripts\python.exe"

#-------------------------------------------------------- Functions -----------------------------------------------#

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

if ($AddCog) {
    Assert-Errors $python -m cookiecutter https://github.com/Cog-Creators/cog-cookiecutter
}

if ($StartBot) {
    Assert-Errors $python -m redbot $redinstance --dev --rpc
}

if ($StartDashboard) {
    Assert-Errors $pythonDashboard -m reddash
}