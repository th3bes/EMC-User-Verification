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


BOT_DEVELOPER_ID = getenv("BOT_DEVELOPER_ID", -1)
DATABASE = getenv("DATABASE_NAME", '')
GUILD = discord.Object(id=getenv("GUILD_ID", ''))
VERIFIER_ROLE_ID = int(getenv("VERIFIER_ROLE_ID", -1))
VERIFIED_ROLE_ID = int(getenv("VERIFIED_ROLE_ID", -1))

LOG_CHANNEL_ID = int(getenv("LOG_CHANNEL_ID", -1))
LOG_CHANNEL: discord.TextChannel

intents = discord.Intents.all() # discord.Intents(105495251968)

bot = commands.Bot(command_prefix='!', intents=intents)

#    /[=================]\
#    {[=== UTILITIES ===]}
#    \[=================]/

# logs content to console with timestamp in system's local time
def log(*content):
    now = datetime.now()
    time = f'[{str(now.hour).rjust(2, '0')}:{str(now.minute).rjust(2, '0')}:{str(now.second).rjust(2, '0')}]'
    print(f'{time} {' '.join(map(str, content))}')


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

# gets the database entry of a user given any one of discord_id, minecraft_username, or minecraft_id
def get_user_database_entry(discord_id: int = 0, minecraft_username: str = '', minecraft_uuid: str = '', database_id: int = -1):
    """Get the users database entry of a user given any **ONE** of the following values:
        
    ### Args:
        **discord_id** *(int, optional)*.
        **minecraft_username** *(str, optional)*.
        **minecraft_id** *(str, optional)*.
        **database_id** *(str, optional)*.
    """
    try:
        with sqlite3.connect(DATABASE) as conn:
            cursor = conn.cursor()
            
            chosenIdentifier: str
            chosenValue: str | int
            
            if discord_id != -1:
                chosenIdentifier = 'discord_id'
                chosenValue = discord_id
            elif minecraft_username != '':
                chosenIdentifier = 'minecraft_username'
                chosenValue = minecraft_username
            elif minecraft_uuid != '':
                chosenIdentifier = 'minecraft_uuid'
                chosenValue = minecraft_uuid
            elif database_id != 0:
                chosenIdentifier = 'id'
                chosenValue = database_id
            else:
                return None
            
            cursor.execute(f'SELECT * FROM verified_users WHERE {chosenIdentifier} = ?', (chosenValue,)) # type: ignore
            row = cursor.fetchone()
            
            if row:
                return row
            
    except Exception as e:
        log(f'ERROR GETTING USER ENTRY FROM DATABASE: {e}')
    
    return None

# gets the database id of a user given any one of discord_id, minecraft_username, or minecraft_id
def get_user_database_id(discord_id: int = 0, minecraft_username: str = '', minecraft_id: str = ''):
    """Get the users database id of a user given any **ONE** of the following values:
    
    ### Args:
        **discord_id** *(int, optional)*.
        **minecraft_username** *(str, optional)*.
        **minecraft_id** *(str, optional)*.
        **database_id** *(str, optional)*.
    """
    id = get_user_database_entry(discord_id, minecraft_username, minecraft_id)
    if id:
        return id[0]
    return None


#    {[=== DISCORD ===]}

def create_embed(*content_lines, title: str = '', color: discord.Color = discord.Color.blue(), inline: bool = False):
    if isinstance(content_lines[0], list):
        content_lines = content_lines[0]
    else:
        content_lines = list(content_lines)
    
    embed = discord.Embed(title=title, color=color)
    
    for line in content_lines:
        embed.add_field(name='', value=line, inline=inline)
    
    return embed

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
    
    if any(role.id == needed_role_id for role in command_member.roles):
        log(f'Successfully Validated USER: {interaction.user.name} !')
        return command_member
    
    log("TERMINATED. User does not have permissions.")
    await interaction.response.send_message('You do not have sufficient access to use this command.', ephemeral=True)
    return None

async def validate_minecraft_username(mc_name: str, verifier_name: str = 'SYSTEM'):
    log(f'{verifier_name} | Attempting to validate Minecraft USERNAME: {mc_name} ...')
    mc_uuid: str | None
    try:
        mc_uuid, _ = await request_mojang_player_info(mc_name)
        if not mc_uuid:
            log(f'{verifier_name} | Failed validation for {mc_name}: Minecraft Username is not valid')
            return None
    except Exception as e:
        log(f'{verifier_name} | AN UNKNOWN ERROR OCCURRED WHILE VALIDATING MINECRAFT USERNAME:\n{e}')
        return e

    log(f'{verifier_name} | Successfully validated Minecraft USERNAME: {mc_name} ( {mc_uuid} )')
    return mc_uuid

# check if Discord ID is valid and in the server
async def validate_member_in_guild(interaction: discord.Interaction, discord_id: int, verifier_name: str = 'SYSTEM'):
    log(f'{verifier_name} | Attempting to validate Discord ID: {discord_id} ...')
    target_member: discord.Member | None
    try:
        target_member = get_guild_member_from_id(interaction, discord_id) # type: ignore
        if not target_member:
            log(f'{verifier_name} | Failed validation for {discord_id}: Discord ID is not valid')
            return None
    except Exception as e:
        log(f'{verifier_name} | AN UNKNOWN ERROR OCCURRED WHILE VALIDATING DISCORD ID:\n{e}')
        return e
    
    log(f'{verifier_name} | Successfully validated Discord ID: {discord_id} ( {target_member.name} )')
    return target_member

#    {[=== AUTOCOMPLETE FUNCTIONS ===]}


#    {[=== MISC. ===]}


#    /[================]\
#    {[=== COMMANDS ===]}
#    \[================]/


#    {[=== REGISTRATION ===]}

@bot.tree.command(name='verify', description='Verify User and register to database for tracking', guild=GUILD)
@app_commands.describe(mc_name='User\'s Minecraft username (case sensitive)', discord_id='User\'s Discord ID')
async def command_verify_user(interaction: discord.Interaction, mc_name: str, discord_id: str):
    current_timestamp = int(datetime.now(timezone.utc).timestamp())
    
    command_member = await validate_command_user(interaction, VERIFIER_ROLE_ID)
    if not command_member or not interaction.guild: return # no need to send a response as validate_command_user handles all exit cases
    
    ####################
    
    # cast discord_id into int because the command input cant handle large enough numbers
    try:
        discord_id = int(discord_id) # type: ignore
        assert isinstance(discord_id, int)
    except Exception as e:
        raise e
    
    ####################
    
    # get Minecraft UUID of requested User and ensure an account exists with that name
    mc_uuid: str | None | Exception = await validate_minecraft_username(mc_name, command_member.name)
    if not mc_uuid:
        await interaction.response.send_message('The supplied Username is not linked to a Minecraft account. Check spelling/letter case and try again. No changes were made.', ephemeral=True)
        return
    elif isinstance(mc_uuid, Exception):
        await interaction.response.send_message(f'An unknown error occurred checking if {mc_name} is linked to a Minecraft account. Please contact <@{BOT_DEVELOPER_ID}>. No changes were made.')
        return
    
    ####################
    
    # check if Discord ID is valid and in the server
    target_member: discord.Member | None | Exception = await validate_member_in_guild(interaction, discord_id, command_member.name)
    if not target_member:
        await interaction.response.send_message('The supplied Discord ID is not valid. Ensure that the ID is correct, that the User is in the Server, and try again. No changes were made.', ephemeral=True)
        return
    elif isinstance(target_member, Exception):
        await interaction.response.send_message(f'An unknown error occurred checking if {discord_id} is in this Server. Please contact <@{BOT_DEVELOPER_ID}>. No changes were made.')
        return
    
    ####################
    
    # create database entry for validated user
    log(f'{command_member.name} | Attempting to create DATA ENTRY for: {mc_name}, {discord_id}')
    try:
        prev_verified = get_user_database_entry(discord_id=discord_id)
        if not prev_verified:
            with sqlite3.connect(DATABASE) as conn:
                cursor = conn.cursor()
                
                cursor.execute("""
                    INSERT INTO verified_users(minecraft_username, minecraft_uuid, discord_id, timestamp)
                    VALUES(?, ?, ?, ?)
                """, (mc_name, mc_uuid, discord_id, current_timestamp))
                conn.commit()
        else:
            log(f'{command_member.name} | Failed verification for {mc_name}, {discord_id}: Discord ID already exists in database')
            await interaction.response.send_message(f'Minecraft account **{prev_verified[1]}** is already linked to Discord ID **{discord_id}**. No changes were made.', ephemeral=True)
            return
    except Exception as e:
        log(f'{command_member.name} | An error occurred in VERIFY, DATABASE:\n{e}')
        await interaction.response.send_message(f'An unknown error occurred creating a database entry for {mc_name} ( {discord_id} ). Please contact <@{BOT_DEVELOPER_ID}>. No changes were made.')
        raise e
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
    
    ####################
    
    lines = [
        f'**CSIS AGENT:** <@{command_member.id}>',
        f'**USER:** <@{discord_id}>',
        f'__IGN__: `{mc_name}`',
        f'__MC-UUID__: `{mc_uuid}`',
        f'__DISCORD ID__: `{discord_id}`',
        f'<t:{current_timestamp}:F>'
    ]
    embed = create_embed(lines, title=f'ADDED VERIFIED USER', color=discord.Color.green())
    embed.set_footer(text=f'May the North be True, Strong, and Free')

    await interaction.response.send_message(f'You have successfully verified {target_member.name} (IGN: {mc_name}, DISCORD ID: {discord_id})', ephemeral=True)
    
    await asyncio.sleep(1)
    
    await LOG_CHANNEL.send(embed=embed)
    #await interaction.response.send_message(f'{command_member.name} has successfully verified {target_member.name} (IGN: {mc_name}, DISCORD ID: {discord_id})', ephemeral=True)
    log(f'{command_member.name} | VERIFICATION COMPLETE FOR {target_member.name} (IGN: {mc_name}, DISCORD ID: {discord_id})!')
    

@bot.tree.command(name='unverify', description='Remove Verification for User and stop tracking. Provide EITHER Minecraft Username OR Discord ID.', guild=GUILD)
@app_commands.describe(mc_name='Minecraft username (case sensitive). Do not provide Discord ID if using this.', discord_id='Discord ID. Do not provide Minecraft Username if using this.')
async def command_unverify_user(interaction: discord.Interaction, mc_name: str = "", discord_id: str = '-1'):
    current_timestamp = int(datetime.now(timezone.utc).timestamp())
    
    command_member = await validate_command_user(interaction, VERIFIER_ROLE_ID)
    if not command_member or not interaction.guild: return # no need to send a response as validate_command_user handles all exit cases
    
    ####################
    
    # cast discord_id into int because the command input cant handle large enough numbers
    try:
        discord_id = int(discord_id) # type: ignore
        assert isinstance(discord_id, int)
    except Exception as e:
        raise e
    
    ####################
    
    # check if database entry exists for supplied mc_name or discord_id
    target_user_entry: tuple | None = get_user_database_entry(discord_id=discord_id, minecraft_username=mc_name)
    if not target_user_entry:
        log(f'{command_member.name} | Requested user does not exist in database')
        await interaction.response.send_message(f'The requested MC Username/Discord ID is not registered in the database. No changes were made.', ephemeral=True)
        return
    
    mc_name = mc_name if mc_name != '' else target_user_entry[1]
    discord_id = discord_id if discord_id != -1 else target_user_entry[3]
    
    ####################
    
    # get discord.Member object
    target_member: discord.Member | None | Exception = await validate_member_in_guild(interaction, discord_id, command_member.name)
    if not target_member:
        await interaction.response.send_message('The supplied Discord ID is not valid. Ensure that the ID is correct, that the User is in the Server, and try again. No changes were made.', ephemeral=True)
        return
    elif isinstance(target_member, Exception):
        await interaction.response.send_message(f'An unknown error occurred checking if {discord_id} is in this Server. Please contact <@{BOT_DEVELOPER_ID}>. No changes were made.')
        return
    
    ####################
    
    # remove user's database entry
    try:
        with sqlite3.connect(DATABASE) as conn:
            cursor = conn.cursor()
            
            cursor.execute("""
                DELETE FROM verified_users WHERE id = ?
            """, (target_user_entry[0],))
            
            conn.commit()
    except Exception as e:
        raise e
    
    ####################
    
    # remove user "validated" role
    log(f'{command_member.name} | Attempting to remove VERIFIED ROLE: {mc_name}, {discord_id}')
    try:
        role = interaction.guild.get_role(VERIFIED_ROLE_ID)
        if not role: return # should never happen in properly configured server
        await target_member.remove_roles(role, reason=f'{command_member.name} Un-verified {target_member.name} with Minecraft Account: {mc_name} ( {target_user_entry[2]} )')
    except Exception as e:
        raise e
    log(f'{command_member.name} | Successfully removed VERIFIED ROLE: {mc_name}, {discord_id}')
    
    ####################
    
    lines = [
        f'**CSIS AGENT:** <@{command_member.id}>',
        f'**USER:** <@{discord_id}>',
        f'__IGN__: `{mc_name}`',
        f'__MC-UUID__: `{target_user_entry[2]}`',
        f'__DISCORD ID__: `{discord_id}`',
        f'<t:{current_timestamp}:F>'
    ]
    embed = create_embed(lines, title=f'REMOVED VERIFIED USER', color=discord.Color.red())
    embed.set_footer(text=f'May the North be True, Strong, and Free')
    
    await interaction.response.send_message(f'You have successfully unverified {target_member.name} (IGN: {mc_name}, DISCORD ID: {discord_id})', ephemeral=True)
    
    await asyncio.sleep(1)
    
    await LOG_CHANNEL.send(embed=embed)
    log(f'{command_member.name} | UN-VERIFICATION COMPLETE FOR {'placeholder'} (IGN: {mc_name}, DISCORD ID: {discord_id})!')

#    /[===============]\
#    {[=== GENERAL ===]}
#    \[===============]/

# startup bot
@bot.event
async def on_ready():
    global LOG_CHANNEL
    log(f'Bot initializing...')
    
    # sync commands
    log(f'Syncing command tree...')
    await bot.tree.sync(guild=GUILD)
    log(f'Command tree synced!')
    
    log(f'Fetching channels...')
    LOG_CHANNEL = await bot.fetch_channel(LOG_CHANNEL_ID) # type: ignore
    log(f'All channels fetched!')
    
    log(f'---------- BOT STARTUP COMPLETE ----------')


bot.run(getenv("DISCORD_TOKEN", ''))
#bot.run(str(getenv("DISCORD_TOKEN", '')))