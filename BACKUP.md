1. Take note of the installed cogs and cog repositories with `[p]cogs`, then `[p]load downloader`, then `[p]repo list`.
2. Stop the bot, ideally with `[p]shutdown`.
3. Run `redbot-setup backup <instancename>` in your venv.
4. Copy the backup file to the new machine/location.
5. Extract the file to a location of your choice (remember the full path and make sure that the user you are going to install/run Red under can access this path).
6. Install Red as normal on the new machine/location.
7. Run `redbot-setup` to create a new instance, except use the path you remembered above as your data path.
8. Start your new instance.
9. Re-add the Repos using the same names as before.
10. Do `[p]cog update`
11. Re-add any cogs that were not re-installed (you may have to uninstall them first as Downloader may think they are still installed)
_Note: The config (data) from cogs has been saved, but not the code itself._