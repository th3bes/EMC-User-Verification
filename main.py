import discord
from discord import app_commands
from discord.ext import commands
import asyncio
import json
import httpx
import requests
import uuid
from httpx_sse import aconnect_sse
from datetime import datetime
from dotenv import load_dotenv
from os import getenv



DATABASE = getenv("DATABASE_NAME")

GUILD = discord.Object(id=getenv("GUILD_ID")) # type: ignore

intents = discord.Intents.all()
bot = commands.Bot(command_prefix='!', intents=intents)

#    /[=================]\
#    {[=== UTILITIES ===]}
#    \[=================]/


#    {[=== EarthMC ===]}

# get player info from EMC. Returns None if requested user has API permissions OFF
def request_player_info(*usernames: str):
    """Sends POST request to EMC Player API for inputted user(s).

    :Returns: List of User(s): JSON data or None if their API is off.
    """
    URL = "https://api.earthmc.net/v4/players"
    PAYLOAD = { "query": usernames }

    response = requests.post(URL, json=PAYLOAD)
    if response.status_code >= 200 and response.status_code < 300:
        # some valid usernames requested, insert None values into indices where data was not sent by server
        users = response.json()
        for i, name in enumerate(usernames):
            if users[i]['name'] != name:
                users.insert(i, None)
        return users
    else:
        # no valid usernames requested, return list of None with len equal to inputted usernames
        return [None] * len(usernames)


#    {[=== SQLITE3 DATABASE HELPERS ===]}


#    {[=== DISCORD COMMAND HELPERS ===]}


#    {[=== AUTOCOMPLETE FUNCTIONS ===]}


#    {[=== MISC. ===]}

# logs content to console with timestamp in system's local time
def log(content):
    now = datetime.now()
    time = f'[{str(now.hour).rjust(2, '0')}:{str(now.minute).rjust(2, '0')}:{str(now.second).rjust(2, '0')}]'
    print(f'{time} {content}')


#    /[================]\
#    {[=== COMMANDS ===]}
#    \[================]/


#    {[=== REGISTRATION ===]}

@bot.tree.command(name='verify', description='Verify User and register to database for tracking', guild=GUILD)
@app_commands.describe(mc_name='User\'s Minecraft username', discord_id='User\'s Discord ID')
async def register_user(interaction: discord.Interaction, mc_name: str, discord_id: int):
    pass

@bot.tree.command(name='unverify', description='Remove verification for registered User and stop tracking')
@app_commands.describe(mc_name='User\'s Minecraft username', discord_id='User\'s Discord ID')
async def unregister_user(interaction: discord.Interaction, mc_name: str = "", discord_id: int = -1):
    pass

#    /[===============]\
#    {[=== GENERAL ===]}
#    \[===============]/


# startup bot
@bot.event
async def on_ready():
    log(f'Bot initializing...')
    
    # sync commands
    await asyncio.create_task(bot.tree.sync(guild=GUILD))
    
    
    
    log(f'---------- BOT STARTUP COMPLETE ----------')






#bot.run(getenv("DISCORD_TOKEN")) # type: ignore