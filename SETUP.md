pyenv local 3.11
python -m pip 

python -m venv "$env:USERPROFILE\.venvs\redbot"
python -m venv "$env:USERPROFILE\.venvs\reddashboard"

Launch: Activate Red Env
python -m pip install -U pip wheel
python -m pip install -U Red-DiscordBot

Launch: Activate Red Dashboard Env
python -m pip install -U pip setuptools wheel
python -m pip install -U Red-Web-Dashboard


redbot-setup     # InteractRed.ps1 assumes bot is named dissentindev

Run Discord Bot instance, in discord: 
[p]load downloader

required:
[p]repo add aaa3a-cogs https://github.com/AAA3A-AAA3A/AAA3A-cogs
[p]cog install aaa3a-cogs dashboard
[p]load dashboard

[p]addpath <path/to/your/cogs>

optional:
[p]repo add sea-cogs https://coastalcommits.com/Sea/Cogs
[p]cog install sea-cogs hotreload
[p]load hotreload
[p]hotreload notifychannel <#channelname>

Setup complete. Now we can start develop the cogs with:
Run Task: Start Discord Bot
Run Task: Start Dashboard