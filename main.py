# Imports, used later in bot
import discord
from discord.ext import commands
from discord import app_commands
from discord.ext import tasks
import logging
from dotenv import load_dotenv
import os
import json
import webserver
import firebase_admin
from firebase_admin import credentials, firestore
from datetime import date, timedelta, time

# TO DO LIST
# START GITHUB REPO
# WEEKLY LEADERBOARD
# COMMENT
# LOG CHANNEL
# RESET WRITING, SPEAKING, AND DICTATION DATE COMMANDS
# MAKE THE MONTHLY AND WEEKLY LEADERBOARD STUFF WORK PERIODICALLY NOT WITH COMMANDS
# SET UP THE BOT ON KOYEB
# RECORD VIDEO, GET FEEDBACK, MAKE CHANGES,THEN DEPLOY TO THE SERVER



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

def load_config():
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
        "weekly_leaderboard_id": None
    }
    raw_config = db.collection('config').document('settings').get()
    if raw_config.exists:
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

async def check_user(message):
    if message.author == bot.user or message.author == config["admin1"] or message.author == config["admin2"]:
        return None
    if not db.collection('users').document(f'{str(message.author.id)}').get().exists:
        default_user = {
            'points': 0,
            'streak': 0,
            'last_worksheet_date': "2000-01-01",  # Placeholder dates
            'first_worksheet_thisWeek_date': "2000-01-01",
            'last_writing_date': "2000-01-01",
            'last_speaking_date': "2000-01-01"
        }
        db.collection('users').document(f'{str(message.author.id)}').set(default_user)
        return default_user
    return db.collection('users').document(str(message.author.id)).get().to_dict()

async def update_leaderboard():
    user_data = db.collection('users').get()
    docs = [{ 'id': doc.id, **doc.to_dict()} for doc in user_data]
    sorted_data = sorted(docs, key=lambda x: x['points'], reverse=True)
    channel = bot.get_channel(config["leaderboard_channel_id"])
    embed = discord.Embed(title="🏆 Leaderboard", color=discord.Color.gold())
    embed.set_thumbnail(url="https://i.ibb.co/BKLCTWv5/4ab2bbcfa5b9a10891406d2a84e94004.webp")
    embed.timestamp = discord.utils.utcnow()
    for i, user in enumerate(sorted_data):
        member = await bot.fetch_user(int(user['id']))
        embed.add_field(
            name=f"#{i + 1} {member.display_name}",
            value=f"Points: {int(user['points'])} | Worksheet Streak: {int(user['streak'])}",
            inline=False
        )
    if config.get('leaderboard_message_id'):
        try:
            msg = await channel.fetch_message(config['leaderboard_message_id'])
            await msg.edit(embed=embed)
        except discord.NotFound:
            msg = await channel.send(embed=embed)
            config['leaderboard_message_id'] = msg.id
            db.collection('config').document('settings').set({'leaderboard_message_id': str(msg.id)}, merge=True)
    else:
        msg = await channel.send(embed=embed)
        config['leaderboard_message_id'] = msg.id
        db.collection('config').document('settings').set({'leaderboard_message_id': str(msg.id)}, merge=True)


def missed_last_week(date_str):
    record_date = date.fromisoformat(date_str)
    return (date.today() - record_date).days > 7
@bot.event
async def on_ready():
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
    # monthly_leaderboard.start()
    check_streaks.start()
def get_guild():
    serverID =config.get("server_id")
    return discord.Object(id=serverID) if serverID else None
@bot.command()
@commands.has_permissions(administrator=True)
async def setserver(ctx):
    try:
        server_id = str(ctx.guild.id)
        config_ref = db.collection('config').document('settings')
        config_ref.set({'server_id': server_id}, merge=True)
        config["server_id"] = int(server_id)
        await ctx.author.send(f"✅ Server has been set. Commands will now sync to **{ctx.guild.name}**.")
        print(f"Server ID saved to Firestore: {server_id}")

    except Exception as e:
        print(f"Error setting server ID: {e}")
        await ctx.author.send("❌ Failed to set server ID.")

@bot.tree.command(name="cfg", description="prints the config", guild=get_guild())
@discord.app_commands.checks.has_permissions(administrator=True)
async def cfg(interaction):
    try:
        config_data = db.collection('config').document('settings').get().to_dict()
        await interaction.response.send_message(config_data, ephemeral=True)
    except Exception as e:
        print(f"Error: {e}")

@bot.tree.command(name="configure", description="sets the admins and the channels", guild=get_guild())
@discord.app_commands.checks.has_permissions(administrator=True)
async def configure(interaction: discord.Interaction, franco_channel: discord.TextChannel, arabic_channel: discord.TextChannel, speaking_channel : discord.TextChannel,
                    dictation_channel :discord.TextChannel, worksheet_channel :discord.TextChannel,leaderboard_channel: discord.TextChannel, weekly_leaderboard: discord.TextChannel , admin1: discord.Member, admin2: discord.Member):
    if config.get("server_id") is None:
        await interaction.response.send_message("Please set the server ID first by typing .setserver", ephemeral = True)
        return
    try:
        db.collection('config').document('settings').set({'franco_channel_id' : str(franco_channel.id), 'arabic_channel_id' : str(arabic_channel.id), "speaking_channel_id" : str(speaking_channel.id)
                                                             , "dictation_channel_id" : str(dictation_channel.id),"worksheet_channel_id": str(worksheet_channel.id) ,
                                                           'leaderboard_channel_id' : str(leaderboard_channel.id), 'weekly_leaderboard_id':str(weekly_leaderboard.id), 'admin1' : str(admin1.id), 'admin2' : str(admin2.id)},merge=True)
        config.update(db.collection('config').document('settings').get().to_dict())
        await interaction.response.send_message("config updated successfully", ephemeral = True)
    except Exception as e:
        print(f"Error: {e}")


@bot.tree.command(name="leaderboard", description="Tests the leaderboard", guild=get_guild())
@discord.app_commands.checks.has_permissions(administrator=True)
async def leaderboard(interaction: discord.Interaction):
    await update_leaderboard()
    await interaction.response.send_message(
        f"Leaderboard successfully updated in <#{config['leaderboard_channel_id']}>", ephemeral=True)


@bot.tree.command(name="add_points", description="adds points to a user", guild=get_guild())
@discord.app_commands.checks.has_permissions(administrator=True)
async def add_points(interaction: discord.Interaction, user: discord.User, points: int):
    db.collection('users').document(str(user.id)).update({
        'points': firestore.Increment(points)
    })
    await update_leaderboard()
    await interaction.response.send_message(f"{points} points added to {user.mention}", ephemeral=True)


@bot.tree.command(name="remove_points", description="removes points from a user", guild=get_guild())
@discord.app_commands.checks.has_permissions(administrator=True)
async def add_points(interaction: discord.Interaction, user: discord.User, points: int):
    db.collection('users').document(str(user.id)).update({
        'points': firestore.Increment(-points)
    })
    await update_leaderboard()
    await interaction.response.send_message(f"{points} points removed from {user.mention}", ephemeral=True)


# Event handling part
@bot.event
async def on_message(message):
    try:
        user_data = await check_user(message)
    except Exception as e:
        print(f"check_user error: {e}")
        await bot.process_commands(message)
        return

    if user_data is None:  # 👈 add this
        await bot.process_commands(message)
        return

    effective_streak = min(user_data.get('streak'), 4)
    text_points = 10
    voice_points = 15
    worksheet_points = 20
    weekly_bonuspercent = 10

    if message.channel.id == config["franco_channel_id"] or message.channel.id == config["arabic_channel_id"]:
        print("Message detected in either franco or arabic channel")
        if user_data.get('last_writing_date') != str(date.today()):
            print("Message detected in franco or arabic channel, and the user didnt write one today")
            if len(message.content) >=20:
                print("valid length Message detected in either franco or arabic channel")
                db.collection('users').document(str(message.author.id)).update({
                    'points': firestore.Increment(text_points),
                    'last_writing_date': str(date.today())
                })
                await update_leaderboard()
            elif message.attachments:
                for attachment in message.attachments:
                    if attachment.content_type and attachment.content_type.startswith("image"):
                        print("user sent an image in one of the writing channels")
                        db.collection('users').document(str(message.author.id)).update({
                            'points': firestore.Increment(text_points),
                            'last_writing_date':  str(date.today())
                        })
                        await update_leaderboard()
    if message.channel.id == config["speaking_channel_id"] and message.attachments:
        print("voice message detected in speaking channel")
        if user_data.get('last_speaking_date') != str(date.today()):
            print("Message detected in speaking channel, and the user didnt send a voicenote today")
            for attachment in message.attachments:
                if message.is_voice_message and message.attachment.duration >= 5:
                    db.collection('users').document(str(message.author.id)).update({
                        'points': firestore.Increment(voice_points),
                        'last_speaking_date': str(date.today())
                    })
                    await update_leaderboard()
    if message.channel.id == config['worksheet_channel_id'] and len(message.content) >= 30:
        print("Message detected in worksheet channel")
        first_date = user_data.get('first_worksheet_thisWeek_date')
        if missed_last_week(first_date):
            # new window, streak +1
            db.collection('users').document(str(message.author.id)).update({
                'points': firestore.Increment(int(worksheet_points * (1 + effective_streak * weekly_bonuspercent/100))),
                'last_worksheet_date': str(date.today()),
                'first_worksheet_thisWeek_date': str(date.today()),
                'streak': firestore.Increment(1)
            })
        else:
            # within window, points with streak bonus but no streak increment
            db.collection('users').document(str(message.author.id)).update({
                'points': firestore.Increment(int(worksheet_points * (1 + effective_streak * weekly_bonuspercent/100))),
                'last_worksheet_date': str(date.today()),
            })
        await update_leaderboard()
    if message.channel.id == config['dictation_channel_id']:
        print("Message detected in dictation channel that has over 10 chars")
        if message.attachments and message.is_voice_message and message.attachments[0].duration >= 3:
            print("voice message detected in dictation channel with over 3 seconds of duration")
            db.collection('users').document(str(message.author.id)).update({
                'points': firestore.Increment(voice_points)
            })
            await update_leaderboard()
        if len(message.content) >=10:
            print("user that sent a message in dictation channel that has over 10 chars")
            db.collection('users').document(str(message.author.id)).update({
                'points': firestore.Increment(text_points),
                'last_writing_date': str(date.today())
            })
            await update_leaderboard()
    await bot.process_commands(message)

# monthly leaderboard handling
# @tasks.loop(time= time(hour = 0, minute = 0, second = 0))
@bot.tree.command(name="monthly_leaderboard", description="Tests the monthly leaderboard", guild=get_guild())
async def monthly_leaderboard(interaction: discord.Interaction):
    #if date.today().day != 1:
        #return
    user_data = db.collection('users').get()
    docs = [{ 'id': doc.id, **doc.to_dict()} for doc in user_data]
    sorted_data = sorted(docs, key=lambda x: x['points'], reverse=True)
    channel = bot.get_channel(config["weekly_leaderboard_id"])
    embed = discord.Embed(title=f"🏆 {date.today().strftime('%B')} Leaderboard", color=discord.Color.gold())
    embed.set_thumbnail(url="https://i.ibb.co/BKLCTWv5/4ab2bbcfa5b9a10891406d2a84e94004.webp")
    embed.timestamp = discord.utils.utcnow()
    for i, user in enumerate(sorted_data):
        member = await bot.fetch_user(int(user['id']))
        embed.add_field(
            name=f"#{i + 1} {member.display_name}",
            value=f"Points: {int(user['points'])} | Worksheet Streak: {int(user['streak'])}",
            inline=False
        )
    await channel.send(embed=embed)
    winner = await bot.fetch_user(int(sorted_data[0]['id']))
    await channel.send(f"🎉 Congratulations {winner.mention}! You won this month! Please open a ticket or message us on WhatsApp.")
    all_users = db.collection('users').get()
    for user in all_users:
        db.collection('users').document(user.id).update({'points': 0})
    await interaction.response.send_message("test", ephemeral=True)
# daily check streaks
@tasks.loop(time=time(hour=0, minute=0, second=0))
# @bot.tree.command(name="check_streaks", description="checks the streaks and resets if they haven't posted within a week", guild=get_guild())
async def check_streaks(interaction: discord.Interaction):
    all_users = db.collection('users').get()
    for user in all_users:
        user_data = user.to_dict()
        if missed_last_week(user_data.get('last_worksheet_date')):
            db.collection('users').document(user.id).update({'streak': 0})
            print(f"Reset streak for {user.id}")
    await interaction.response.send_message("tested", ephemeral=True)




bot.run(token, log_handler=handler, log_level=logging.DEBUG)