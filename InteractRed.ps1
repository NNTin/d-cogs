<#
  .DESCRIPTION
    This script interfaces with Cog-Creators/Red. It is used as a script allowing you to
    easily incorporate it into your workflow (e.g. VSC launch.json).

  .PARAMETER redinstance
    optional redinstance name, default = athenadev
    this assumes you have already run redbot-setup

  .PARAMETER AddCog
    Flag: if enabled adds a new cog via Cog-Cookiecutter

  .PARAMETER Start
    Flag: if enabled starts a Red-DiscordBot instance

  .OUTPUTS
    <None>

  .NOTES
    Version:        1.0
    Author:         Nguyen Tin
    Creation Date:  2025-06-15
    Purpose/Change: Interface to Red-DiscordBot
    
  .EXAMPLE
    .\StartBot.ps1
#>

param (
    [string]$redinstance = "athenadev",
    [switch]$AddCog,
    [switch]$Start
)

#------------------------------------------------------ Preparation -----------------------------------------------#

# python path to red environment
$python = "$env:USERPROFILE\redenv\Scripts\python.exe"

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

if ($Start) {
    Assert-Errors $python -m redbot $redinstance --dev
}
