# Imports, used later in bot
import discord
from discord.ext import commands
from discord import app_commands
from discord.app_commands import Choice
from discord.ext import tasks
import logging
from dotenv import load_dotenv
import os
import json
import webserver
import firebase_admin
from firebase_admin import credentials, firestore
from datetime import date, time

# TO DO LIST
# REMINDER MESSAGES WHEN THEIR STREAK IS ABOUT TO RUN OUT
# MAYBE USER DMS OR SOMEWAY TO INDICATE THAT THEY GAINED POINTS OR STREAK
# HELP COMMAND

# Loads the discord token and the firebase creds
load_dotenv()
token = os.getenv("DISCORD_TOKEN")
firebase_creds_string = os.getenv("FIREBASE_CREDS")
if not all([token, firebase_creds_string]):
    print("Missing one or more environment variables")
    exit()

firebase_dict = json.loads(firebase_creds_string)
creds = credentials.Certificate(firebase_dict)
firebase_admin.initialize_app(creds)
db = firestore.client()

# Discord intents and logging handling
handler = logging.FileHandler(filename='discord.log', encoding='utf-8', mode='w')
intents = discord.Intents.default()
intents.presences = True
intents.message_content = True
intents.voice_states = True
intents.members = True
intents.reactions = True
bot = commands.Bot(command_prefix='.', intents=intents)

# Functions Section

def load_config(): #function for loading the config, used to create local var config
    config = {} #create an empty config dict
    default_config = { #fallback config if there is an empty key/whole db is empty
        "server_id" : None,
        "admin1" : None,
        "admin2" : None,
        "arabic_channel_id" : None,
        "franco_channel_id": None,
        "speaking_channel_id" : None,
        "dictation_channel_id" : None,
        "worksheet_channel_id" : None,
        "leaderboard_channel_id": None,
        "leaderboard_message_id": None,
        "weekly_leaderboard_id": None,
        "log_channel_id" : None
    }
    raw_config = db.collection('config').document('settings').get()
    if raw_config.exists: #ensures that the config exists, then converts the snapshot to a dict
        data = raw_config.to_dict()
        for key in default_config.keys(): #convers the server id to int, and in the future any extra id
            if data.get(key) is not None: #.get not [key] to prevent crashing
                config[key] = int(data[key])
            else:  #if not found, set the key from the default config to the db, and load from local the default config for the key
                db.collection('config').document('settings').set({key:default_config[key]}, merge=True)
                config[key] = default_config[key]
        print("Config loaded successfully")
    else: #if the document doesn't exist, create it using the default config
        print(f"Failed to load config, loading default config")
        db.collection('config').document('settings').set(default_config)
        config = default_config.copy()
    return config

config = load_config()

async def check_user(message): #it checks that the message author is not the bot, or one of the admins, if not, it returns the user data as a dict
    if message.author == bot.user or message.author.id == config["admin1"] or message.author.id == config["admin2"]:
        return None

    doc_ref = db.collection('users').document(f'{str(message.author.id)}')
    doc = doc_ref.get()

    default_user = {
        'points': 0,
        'streak': 0,
        'last_worksheet_date': "2000-01-01",  # Placeholder dates
        'first_worksheet_thisWeek_date': "2000-01-01",
        'last_writing_date': "2000-01-01",
        'last_speaking_date': "2000-01-01"
    }

    if not doc.exists: #writes a new document with default values in case the author wasn't in the db
        doc_ref.set(default_user)
        return default_user
    user_data = doc.to_dict()
    needs_update = False

    for key, value in default_user.items():
        if key not in user_data:
            user_data[key] = value
            needs_update = True
    if needs_update:
        doc_ref.set(user_data, merge=True)
        await log(f"Healed document {message.author.mention}'s data from missing fields\nDocument ID: {message.author.id}")
    return user_data


async def update_leaderboard(): #function to update the leaderboard
    user_data = db.collection('users').get()
    docs = [{ 'id': doc.id, **doc.to_dict()} for doc in user_data] # added the discord ID (name of the document) to the user_data dict
    sorted_data = sorted(docs, key=lambda x: x['points'], reverse=True) #sorts the data by points, reverse to get descending order
    channel = bot.get_channel(config["leaderboard_channel_id"])
    embed = discord.Embed(title="🏆 Leaderboard", color=discord.Color.gold()) #creates an embed with only the title and colour
    embed.set_thumbnail(url="https://i.ibb.co/BKLCTWv5/4ab2bbcfa5b9a10891406d2a84e94004.webp") #sets a thumbnail to the embed
    embed.timestamp = discord.utils.utcnow() #adds a timestamp at the bottom of the embed
    for i, user in enumerate(sorted_data): #enumerate adds numbers to each item in the dict, used to display the rankings
        member = bot.get_user(int(user['id']))
        display_name = member.display_name if member else "Unknown User" # fallback if the member isn't the bot's memory for some reason
        embed.add_field( #adds field with each user to the existing embed
            name=f"#{i + 1} {display_name}",
            value=f"Points: {int(user['points'])} | Worksheet Streak: {int(user['streak'])}",
            inline=False
        )
    if config.get('leaderboard_message_id'): #checks if the message_id exists from before to update the message
        try:
            msg = channel.get_partial_message(config['leaderboard_message_id'])
            await msg.edit(embed=embed)
        except discord.NotFound: #if the message id isn't found (incorrect), it will send it again
            msg = await channel.send(embed=embed)
            config['leaderboard_message_id'] = msg.id
            db.collection('config').document('settings').set({'leaderboard_message_id': str(msg.id)}, merge=True)
    else: #sends a new message incase there wasn't an old one on setup or if it was deleted
        msg = await channel.send(embed=embed)
        config['leaderboard_message_id'] = msg.id
        db.collection('config').document('settings').set({'leaderboard_message_id': str(msg.id)}, merge=True)

def missed_last_week(date_str): #function to check if 7 days have passed from the input date
    record_date = date.fromisoformat(date_str)
    return (date.today() - record_date).days > 7

def get_guild(): #function to the get the guild ID, used in slash commands to sync quickly
    serverID =config.get("server_id")
    return discord.Object(id=serverID) if serverID else None

async def log(msg):
    print(msg)
    channel_id = config.get("log_channel_id")
    if not channel_id: #exits the function if log_channel isn't set up yet
        return
    logging_channel = bot.get_channel(channel_id)
    if logging_channel: #checks if the channel exists first, to prevent expectation spam
        try:
            await logging_channel.send(msg)
        except Exception as e:
            print(f"Failed to send log to Discord: {e}")

@bot.event
async def on_ready(): # on ready event, essential for the bot, and has the loop checks such as the streaks reset and the monthly and weekly leaderboards
    print(f"✅ {bot.user} is online")
    if config["server_id"] is not None:
        try:
            guild = discord.Object(id = config.get("server_id"))
            synced = await bot.tree.sync(guild = guild)
            print(f"synced {len(synced)} commands to {config['server_id']}")
        except Exception as e:
            print(f"Error: {e}")
    else:
        print("Server ID not set, run .setserver to set the ID")
    monthly_leaderboard.start()
    weekly_leaderboard.start()
    check_streaks.start()

# Commands Section

@bot.command() #.setserver sets the server ID in the config, used elsewhere to instantly sync commands.
@commands.has_permissions(administrator=True)
async def setserver(ctx):
    try:
        server_id = str(ctx.guild.id)
        config_ref = db.collection('config').document('settings')
        config_ref.set({'server_id': server_id}, merge=True)
        config["server_id"] = int(server_id)
        await ctx.author.send(f"✅ Server has been set. Commands will now sync to **{ctx.guild.name}**.")
        await log(f"✅ Server has been set. Commands will now sync to **{ctx.guild.name}**.")

    except Exception as e:
        await ctx.author.send("❌ Failed to set server ID.")
        await log(f"Error setting server ID: {e}")

@bot.tree.command(name="cfg", description="prints the config", guild=get_guild()) #/cfg prints the config, used mostly for debugging
@discord.app_commands.checks.has_permissions(administrator=True)
async def cfg(interaction):
    try:
        config_data = db.collection('config').document('settings').get().to_dict()
        await interaction.response.send_message(config_data, ephemeral=True)
    except Exception as e:
        print(f"Error: {e}")

@bot.tree.command(name="configure", description="sets the admins and the channels", guild=get_guild()) # sets the settings document in the config collection in the DB
@discord.app_commands.checks.has_permissions(administrator=True)
async def configure(interaction: discord.Interaction, franco_channel: discord.TextChannel, arabic_channel: discord.TextChannel, speaking_channel : discord.TextChannel,
                    dictation_channel :discord.TextChannel, worksheet_channel :discord.TextChannel,leaderboard_channel: discord.TextChannel, weekly_leaderboard: discord.TextChannel ,
                    log_channel: discord.TextChannel, admin1: discord.Member, admin2: discord.Member):
    if config.get("server_id") is None:
        await interaction.response.send_message("Please set the server ID first by typing .setserver", ephemeral = True)
        return
    try:
        db.collection('config').document('settings').set({'franco_channel_id' : str(franco_channel.id), 'arabic_channel_id' : str(arabic_channel.id), "speaking_channel_id" : str(speaking_channel.id)
                                                             , "dictation_channel_id" : str(dictation_channel.id),"worksheet_channel_id": str(worksheet_channel.id) ,
                                                           'leaderboard_channel_id' : str(leaderboard_channel.id), 'weekly_leaderboard_id':str(weekly_leaderboard.id),
                                                          'log_channel_id' : str(log_channel.id), 'admin1' : str(admin1.id), 'admin2' : str(admin2.id)},merge=True)
        config.update(load_config())
        await interaction.response.send_message("config updated successfully", ephemeral = True)
        await log(f"Server Settings updated successfully by {interaction.user.mention}")
    except Exception as e:
        print(f"Error: {e}")


@bot.tree.command(name="leaderboard", description="Tests the leaderboard", guild=get_guild()) #force updates the leaderboard
@discord.app_commands.checks.has_permissions(administrator=True)
async def leaderboard(interaction: discord.Interaction):
    await update_leaderboard()
    await interaction.response.send_message(
        f"Leaderboard successfully updated in <#{config['leaderboard_channel_id']}>", ephemeral=True)


@bot.tree.command(name="add_points", description="adds points to a user", guild=get_guild()) #command for adding points
@discord.app_commands.checks.has_permissions(administrator=True)
async def add_points(interaction: discord.Interaction, user: discord.User, points: int):
    db.collection('users').document(str(user.id)).update({
        'points': firestore.Increment(points)
    })
    await interaction.response.send_message(f"{points} points added to {user.mention}", ephemeral=True)
    await log(f"{points} points added to {user.mention}")
    await update_leaderboard()


@bot.tree.command(name="remove_points", description="removes points from a user", guild=get_guild()) #command for removing points
@discord.app_commands.checks.has_permissions(administrator=True)
async def remove_points(interaction: discord.Interaction, user: discord.User, points: int):
    db.collection('users').document(str(user.id)).update({
        'points': firestore.Increment(-points)
    })
    await interaction.response.send_message(f"{points} points removed from {user.mention}", ephemeral=True)
    await log(f"{points} points removed from {user.mention}")
    await update_leaderboard()

@bot.tree.command(name="set_streak", description="sets the streak for a certain user", guild=get_guild())
@discord.app_commands.checks.has_permissions(administrator=True)
async def set_streak(interaction: discord.Interaction, user: discord.Member, streak: int):
    try:
        db.collection('users').document(f'{str(user.id)}').set({'streak': streak}, merge=True)
        await log(f"set streak for {user.mention} to {streak} by {interaction.user.mention}")
        await interaction.response.send_message(f"set streak for {user.mention} to {streak}", ephemeral=True)
        await update_leaderboard()
    except Exception as e:
        await log(f"Error setting streak for {user.name}: {e}")

@bot.tree.command(name="reset_date", description="resets a select date for a certain user", guild=get_guild())
@discord.app_commands.checks.has_permissions(administrator=True)
@app_commands.choices(date=[
    Choice(name="Date of the first worksheet sent this week", value="first_worksheet_thisWeek_date"),
    Choice(name="Date of the last worksheet sent", value="last_worksheet_date"),
    Choice(name=f"Date of the last voice note in the speaking channel", value="last_speaking_date"),
    Choice(name="Date of the last message sent in either writing channel", value="last_writing_date")
])
async def reset_date(interaction: discord.Interaction, user: discord.Member, date: Choice[str]):
    date_to_reset = date.value
    db.collection('users').document(f'{str(user.id)}').set({f'{date_to_reset}': "2000-01-01"}, merge = True)
    await interaction.response.send_message(f"{date.name} was reset for {user.mention}", ephemeral=True)
    await log(f"{date.name} was reset for {user.mention} by {interaction.user.mention}")


# Event handling part
@bot.event
async def on_message(message):
    if message.author == bot.user:
        return

    if isinstance(message.channel, discord.Thread): #prevents it from reading messages in threads
        return

    # Check if the message is in a tracked channel
    tracked_channels = [
        config.get("franco_channel_id"),
        config.get("arabic_channel_id"),
        config.get("speaking_channel_id"),
        config.get("dictation_channel_id"),
        config.get("worksheet_channel_id")
    ]
    if message.channel.id not in tracked_channels:
        await bot.process_commands(message)
        return

    try:
        user_data = await check_user(message)
    except Exception as e:
        print(f"check_user error: {e}")
        await bot.process_commands(message)
        return

    if user_data is None:
        await bot.process_commands(message)
        return
    # Variables Section
    effective_streak = min(user_data.get('streak'), 4)
    text_points = 10
    voice_points = 15
    worksheet_points = 20
    weekly_bonuspercent = 10
    min_worksheet_length = 100
    min_dictation_length = 10
    min_dictation_voice_length = 3
    min_written_length = 20
    min_speaking_length = 5

    if message.channel.id == config["franco_channel_id"] or message.channel.id == config["arabic_channel_id"]: #handles messages sent in the franco channel or the arabic channel
        if user_data.get('last_writing_date') != str(date.today()):
            if len(message.content) >=min_written_length:
                await log(f"Valid Message detected in {message.channel.mention} from {message.author.mention}, points awarded: {text_points}")
                db.collection('users').document(str(message.author.id)).update({
                    'points': firestore.Increment(text_points),
                    'last_writing_date': str(date.today())
                })
                await update_leaderboard()
            elif message.attachments:
                for attachment in message.attachments:
                    if attachment.content_type and attachment.content_type.startswith("image"):
                        db.collection('users').document(str(message.author.id)).update({
                            'points': firestore.Increment(text_points),
                            'last_writing_date':  str(date.today())
                        })
                        await update_leaderboard()
                        await log(f"Image detected in {message.channel.mention}, points awarded: {text_points}")
                        break
        else:
            await log(f"Message Detected in {message.channel.mention} from {message.author.mention}, but they already wrote one today.")
    if message.channel.id == config["speaking_channel_id"] and message.attachments:
        if user_data.get('last_speaking_date') != str(date.today()):
            for attachment in message.attachments:
                if attachment.is_voice_message() and attachment.duration >= min_speaking_length: #checks if the user sent a voicenote, and if it is over 5 seconds
                    db.collection('users').document(str(message.author.id)).update({
                        'points': firestore.Increment(voice_points),
                        'last_speaking_date': str(date.today())
                    })
                    await update_leaderboard()
                    await log(f"{message.author.mention} sent a voice message in {message.channel.mention}, points awarded: {voice_points}")
                else:
                    await log(f"{message.author.mention} sent a voice message in {message.channel.mention}, but it was shorter than {min_speaking_length}")
        else:
            await log(f"{message.author.mention} sent a message in {message.channel.mention}, but they already sent one today ")
    if message.channel.id == config['worksheet_channel_id'] and len(message.content) >= min_worksheet_length:
        first_date = user_data.get('first_worksheet_thisWeek_date')
        if missed_last_week(first_date):
            # new window, streak +1
            db.collection('users').document(str(message.author.id)).update({
                'points': firestore.Increment(int(worksheet_points * (1 + effective_streak * weekly_bonuspercent/100))),
                'last_worksheet_date': str(date.today()),
                'first_worksheet_thisWeek_date': str(date.today()),
                'streak': firestore.Increment(1)
            })
            await log(f"{message.author.mention} sent a worksheet answer in {message.channel.mention}, points awarded: {worksheet_points}, streak: increased by 1")
        else:
            # within window, points with streak bonus but no streak increment
            db.collection('users').document(str(message.author.id)).update({
                'points': firestore.Increment(int(worksheet_points * (1 + effective_streak * weekly_bonuspercent/100))),
                'last_worksheet_date': str(date.today()),
            })
            await log(f"{message.author.mention} sent a worksheet answer in {message.channel.mention}, points awarded: {worksheet_points}, streak: not increased because their last one was within 7 days ")
        await update_leaderboard()
    if message.channel.id == config['dictation_channel_id']:
        if message.attachments and message.attachments[0].is_voice_message() and message.attachments[0].duration >= min_dictation_voice_length:
            db.collection('users').document(str(message.author.id)).update({
                'points': firestore.Increment(voice_points)
            })
            await update_leaderboard()
            await log(f"{message.author.mention} sent a voice message in {message.channel.mention} with over {min_dictation_voice_length} seconds of duration, points awarded: {voice_points}")
        if len(message.content) >=min_dictation_length:
            db.collection('users').document(str(message.author.id)).update({
                'points': firestore.Increment(text_points),
                'last_writing_date': str(date.today())
            })
            await update_leaderboard()
            await log(f"{message.author.mention} sent a text message in {message.channel.mention} with over {min_dictation_length} chars, points awarded: {text_points}")

    await bot.process_commands(message) #crucial so the bot can process written commands like .setserver

# Monthly Leaderboard Handling
@tasks.loop(time = time(hour = 0, minute = 0, second = 0))
#@bot.tree.command(name="monthly_leaderboard", description="Tests the monthly leaderboard", guild=get_guild())
async def monthly_leaderboard():
    if date.today().day != 1:
        return
    user_data = db.collection('users').get()
    docs = [{ 'id': doc.id, **doc.to_dict()} for doc in user_data]
    sorted_data = sorted(docs, key=lambda x: x['points'], reverse=True)
    channel = bot.get_channel(config["weekly_leaderboard_id"])
    embed = discord.Embed(title=f"🏆 {date.today().strftime('%B')} Leaderboard", color=discord.Color.gold())
    embed.set_thumbnail(url="https://i.ibb.co/BKLCTWv5/4ab2bbcfa5b9a10891406d2a84e94004.webp")
    embed.timestamp = discord.utils.utcnow()

    for i, user in enumerate(sorted_data):
        member =  bot.get_user(int(user['id']))
        display_name = member.display_name if member else "Unknown User"  # fallback if the member isn't the bot's memory for some reason
        embed.add_field(
            name=f"#{i + 1} {display_name}",
            value=f"Points: {int(user['points'])} | Worksheet Streak: {int(user['streak'])}",
            inline=False
        )
    await channel.send(embed=embed)
    winner = await bot.fetch_user(int(sorted_data[0]['id']))
    await channel.send(f"🎉 Congratulations {winner.mention}! You won this month! Please open a ticket or message us on WhatsApp.")
    all_users = db.collection('users').get()
    for user in all_users:
        db.collection('users').document(user.id).update({'points': 0})
    # await interaction.response.send_message("test", ephemeral=True)
    await log(f"Monthly leaderboard for {date.today().strftime('%B')} sent, and all the points are reset! ")
# Weekly Leaderboard Handling
@tasks.loop(time= time(hour = 0, minute = 0, second = 0))
#@bot.tree.command(name="weekly_leaderboard", description="Tests the weekly leaderboard", guild=get_guild())
async def weekly_leaderboard():
    if date.today().weekday() != 0 or date.today().day == 1:
        return
    user_data = db.collection('users').get()
    docs = [{'id': doc.id, **doc.to_dict()} for doc in user_data]
    sorted_data = sorted(docs, key=lambda x: x['points'], reverse=True)
    channel = bot.get_channel(config["weekly_leaderboard_id"])
    embed = discord.Embed(title=f"🏆 Weekly Leaderboard", color=discord.Color.gold())
    embed.set_thumbnail(url="https://i.ibb.co/BKLCTWv5/4ab2bbcfa5b9a10891406d2a84e94004.webp")
    embed.timestamp = discord.utils.utcnow()
    for i, user in enumerate(sorted_data):
        member =  bot.get_user(int(user['id']))
        display_name = member.display_name if member else "Unknown User"  # fallback if the member isn't the bot's memory for some reason
        embed.add_field(
            name=f"#{i + 1} {display_name}",
            value=f"Points: {int(user['points'])} | Worksheet Streak: {int(user['streak'])}",
            inline=False
        )
    await channel.send(embed=embed)
    await log(f"Weekly Leaderboard sent")

# daily check streaks
@tasks.loop(time=time(hour=0, minute=0, second=0))
# @bot.tree.command(name="check_streaks", description="checks the streaks and resets if they haven't posted within a week", guild=get_guild())
async def check_streaks():
    all_users = db.collection('users').get()
    for user in all_users:
        user_data = user.to_dict()
        if missed_last_week(user_data.get('last_worksheet_date')) and user_data.get('streak', 0) > 0: #zero in the bracket is the fallback value
            db.collection('users').document(user.id).update({'streak': 0})
            await log(f"Reset streak for <@{user.id}>")



webserver.keep_alive()
bot.run(token, log_handler=handler, log_level=logging.DEBUG)
