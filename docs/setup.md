# Setup & Installation

## Prerequisites
- Python 3.11 (project uses `pyenv local 3.11` in development)
- Red-DiscordBot installed in its own virtual environment
- Optional: Red-Web-Dashboard in a separate virtual environment

## Installation
1. Create virtual environments:
   - `python -m venv "$env:USERPROFILE\\.venvs\\redbot"`
   - `python -m venv "$env:USERPROFILE\\.venvs\\reddashboard"`
2. Install dependencies:
   - In the RedBot env: `python -m pip install -U pip wheel` then `python -m pip install -U Red-DiscordBot`
   - In the dashboard env: `python -m pip install -U pip setuptools wheel` then `python -m pip install -U Red-Web-Dashboard`
3. Run `redbot-setup` to configure your bot (InteractRed.ps1 assumes the bot is named `dissentindev`).

## Configure Red-DiscordBot
1. Start the bot instance and in Discord run: `[p]load downloader`
2. Required cogs:
   - `[p]repo add aaa3a-cogs https://github.com/AAA3A-AAA3A/AAA3A-cogs`
   - `[p]cog install aaa3a-cogs dashboard`
   - `[p]load dashboard`
3. Add this repository path so Red can load local cogs:
   - `[p]addpath <path/to/your/cogs>`

## Optional utilities
- Add SeaCogs hotreload support:
  - `[p]repo add sea-cogs https://coastalcommits.com/Sea/Cogs`
  - `[p]cog install sea-cogs hotreload`
  - `[p]load hotreload`
  - `[p]hotreload notifychannel <#channelname>`

Setup complete. Use your task runner to start the Discord bot and dashboard for development.
