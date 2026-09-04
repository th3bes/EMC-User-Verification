import discord
from discord import app_commands
from discord.ext import commands
import asyncio
import json
import httpx
import requests
import uuid
import sqlite3
from httpx_sse import aconnect_sse
from datetime import datetime, timezone
from dotenv import load_dotenv
from os import getenv

load_dotenv()

#DISCORD_TOKEN = 
DATABASE = getenv("DATABASE_NAME", '')

GUILD = discord.Object(id=getenv("GUILD_ID", ''))
VERIFIER_ROLE_ID = int(getenv("VERIFIER_ROLE_ID", -1))
#VERIFIER_ROLE: discord.Role
VERIFIED_ROLE_ID = int(getenv("VERIFIED_ROLE_ID", -1))
#VERIFIED_ROLE: discord.Role

intents = discord.Intents.all() # discord.Intents(105495251968)

bot = commands.Bot(command_prefix='!', intents=intents)

#    /[=================]\
#    {[=== UTILITIES ===]}
#    \[=================]/

#    {[=== MOJANG ===]}

# get player info from Mojang. Returns -1 if uuid is invalid, or None if some other error occurred
async def request_mojang_player_info(input: str, is_uuid: bool = False):
    """Request Username and UUID from Mojang.

    Args:
        input (str): Minecraft user's Username OR UUID
        is_uuid (bool): Pass True if input is a UUID

    Returns:
        id (str): UUID of requested Minecraft User
        name (str): Username of requested Minecraft User
    """
    if not is_uuid:
        input = 'name/' + input
    
    response = requests.get('https://api.minecraftservices.com/minecraft/profile/lookup/' + input)
    if response.status_code >= 200 and response.status_code < 300:
        result = response.json()
        return result['id'], result['name']
    elif response.status_code == 404:
        return None, None
    else:
        return None, None

async def request_mojang_bulk_player_info(*names):
    """Can take either a single list (array) of usernames or n number of names not in a list

    Returns:
        json for requested usernames with their minecraft uuid and username
    """
    # if input is a list of names in a tuple instead of just a tuple of names,
    # convert it to just a tuple of names.
    # otherwise, cast tuple to list
    if isinstance(names[0], list):
        names = names[0]
    else:
        names = list(names)
    
    response = requests.post('https://api.mojang.com/profiles/minecraft', json=names)
    if response.status_code >= 200 and response.status_code < 300:
        return response.json()
    else:
        return None


#    {[=== EarthMC ===]}

# get player info from EMC. Returns None if requested user has API permissions OFF
def request_emc_player_info(*usernames: str):
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

def get_guild_member_from_id(interaction: discord.Interaction, discord_id: int):
    """Get a member of a Guild from their discord_id. Returns None if ID is not valid."""
    if interaction.guild is None: return # should never happen in this implementation
    
    return interaction.guild.get_member(discord_id)

# checks if the command was used in a server and if the user has the necessary role
async def validate_command_user(interaction: discord.Interaction, needed_role_id: int):
    """Checks if the command was used in a Server/Guild and if the User has the necessary Role"""
    
    log(f"Attempting to Validate command usage for USER: {interaction.user.name} ...")
    
    command_member = get_guild_member_from_id(interaction, interaction.user.id)
    
    if command_member is None:
        log("TERMINATED. User does not exist.")
        await interaction.response.send_message("It appears you do not exist. Try again and good luck.", ephemeral=True)
        return None
    
    if not any(role.id == needed_role_id for role in command_member.roles):
        log("TERMINATED. User does not have permissions.")
        await interaction.response.send_message('You do not have sufficient access to use this command.')
        return None
    
    log(f'Successfully Validated USER: {interaction.user.name} !')
    
    return command_member


#    {[=== AUTOCOMPLETE FUNCTIONS ===]}


#    {[=== MISC. ===]}

# logs content to console with timestamp in system's local time
def log(*content):
    now = datetime.now()
    time = f'[{str(now.hour).rjust(2, '0')}:{str(now.minute).rjust(2, '0')}:{str(now.second).rjust(2, '0')}]'
    print(f'{time} {' '.join(map(str, content))}')


#    /[================]\
#    {[=== COMMANDS ===]}
#    \[================]/


#    {[=== REGISTRATION ===]}

@bot.tree.command(name='verify', description='Verify User and register to database for tracking', guild=GUILD)
@app_commands.describe(mc_name='User\'s Minecraft username (case sensitive)', discord_id='User\'s Discord ID')
async def command_verify_user(interaction: discord.Interaction, mc_name: str, discord_id: int):
    command_member = await validate_command_user(interaction, VERIFIER_ROLE_ID)
    if not command_member or not interaction.guild: return # no need to send a response as validate_command_user handles all exit cases
    
    ####################
    
    # get Minecraft UUID of requested User and ensure an account exists with that name
    log(f'{command_member.name} | Attempting to validate Minecraft USERNAME: {mc_name} ...')
    mc_uuid: str | None
    try:
        mc_uuid, _ = await request_mojang_player_info(mc_name)
        if not mc_uuid:
            log(f'{command_member.name} | Supplied Username is not valid.')
            await interaction.response.send_message('The supplied Username is not linked to a Minecraft account. Check spelling/letter case and try again.', ephemeral=True)
            return
    except Exception as e:
        raise e # return
    log(f'{command_member.name} | Successfully validated Minecraft USERNAME: {mc_name} ( {mc_uuid} )')
    
    ####################
    
    # check if Discord ID is valid and in the server
    log(f'{command_member.name} | Attempting to validate Discord ID: {discord_id} ...')
    target_member: discord.Member | None
    try:
        target_member = get_guild_member_from_id(interaction, discord_id)
        if not target_member:
            log(f'{command_member.name} | Supplied Discord ID is not valid.')
            await interaction.response.send_message('The supplied Discord ID is not valid. Ensure that the ID is correct, the User is in the Server, and try again.', ephemeral=True)
            return
    except Exception as e:
        raise e # return
    
    log(f'{command_member.name} | Successfully validated Discord ID: {discord_id} ( {target_member.name} )')
    
    ####################
    
    # create database entry for validated user
    log(f'{command_member.name} | Attempting to create DATA ENTRY for: {mc_name}, {discord_id}')
    try:
        with sqlite3.connect(DATABASE) as conn:
            cursor = conn.cursor()
            
            cursor.execute("""
                INSERT INTO verified_users(minecraft_username, minecraft_uuid, discord_id, timestamp)
                VALUES(?, ?, ?, ?)
            """, (mc_name, mc_uuid, discord_id, int(datetime.now(timezone.utc).timestamp())))
            conn.commit()
    except Exception as e:
        raise e # return
    log(f'{command_member.name} | Successfully created DATA ENTRY for: {mc_name}, {discord_id}')
    
    ####################
    
    # give user "validated" role
    log(f'{command_member.name} | Attempting to grant VERIFIED ROLE: {mc_name}, {discord_id}')
    try:
        role = interaction.guild.get_role(VERIFIED_ROLE_ID)
        if not role: return # should never happen in properly configured server
        
        await target_member.add_roles(role, reason=f'{command_member.name} Verified {target_member.name} with Minecraft Account: {mc_name} ( {mc_uuid} )')
    except Exception as e:
        raise e
    log(f'{command_member.name} | Successfully granted VERIFIED ROLE: {mc_name}, {discord_id}')
    


@bot.tree.command(name='unverify', description='Remove verification for registered User and stop tracking. Provide EITHER Minecraft Username OR Discord ID, you don\'t need both.')
@app_commands.describe(mc_name='User\'s Minecraft username (case sensitive)', discord_id='User\'s Discord ID')
async def command_unverify_user(interaction: discord.Interaction, mc_name: str = "", discord_id: int = -1):
    command_member = await validate_command_user(interaction, VERIFIER_ROLE_ID)
    if not command_member: return # no need to send a response as validate_command_user handles all exit cases

#    /[===============]\
#    {[=== GENERAL ===]}
#    \[===============]/


# startup bot
@bot.event
async def on_ready():
    #global GUILD
    #global VERIFIER_ROLE
    #global VERIFIED_ROLE
    
    log(f'Bot initializing...')
    
    # sync commands
    synced = await bot.tree.sync(guild=GUILD)
    log(synced)
    
    #GUILD = await bot.fetch_guild(int(getenv("GUILD_ID", -1)))
    
    #VERIFIER_ROLE = GUILD.get_role(VERIFIER_ROLE_ID) # type: ignore
    #VERIFIED_ROLE = GUILD.get_role(VERIFIED_ROLE_ID) # type: ignore
    
    
    log(f'---------- BOT STARTUP COMPLETE ----------')


bot.run(getenv("DISCORD_TOKEN", ''))
#bot.run(str(getenv("DISCORD_TOKEN", '')))