#!/usr/bin/python3
# -*- coding: utf-8 -*-
from telethon import events, TelegramClient, Button, errors, types, functions, utils
from telethon.tl.types import (MessageActionChatMigrateTo)
import asyncio
import logging
import boto3
from botocore.exceptions import ClientError
import io
import pickle
import base64
import os
import glob
from peewee import *
import aiocron

from welcome_db_model import *
from config import *
from translations import *
# from icecream import ic
# import mysql.connector as mysql
# from tg_file_id.file_id import FileId

logging.basicConfig(format='[%(levelname)s]: %(message)s',
                    level=logging.WARNING)


def logger(func):
    def decorator(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except Exception as err:
            logging.error(err, exc_info=True)
    return decorator



ACCESS_KEY = spaces_access_key
SECRET_KEY = spaces_secret_key

session = boto3.session.Session()
spaces_client = session.client('s3',
                                region_name='fra1',
                                endpoint_url='https://mrsnw.fra1.digitaloceanspaces.com',
                                aws_access_key_id=ACCESS_KEY,
                                aws_secret_access_key=SECRET_KEY)

new_message = {}
register_prompt = {}
chat_info = {}

# region Functions

def rotate_log_file(file_path, max_size_mb=5, max_files=5):
    """Rotate log files when they exceed max_size_mb, keeping max_files versions"""
    max_size_bytes = max_size_mb * 1024 * 1024
    
    # Check if current file exists and its size
    if os.path.exists(file_path) and os.path.getsize(file_path) >= max_size_bytes:
        # Find existing rotated files
        base_name = file_path.rsplit('.', 1)[0]
        extension = file_path.rsplit('.', 1)[1] if '.' in file_path else ''
        
        # Get list of existing rotated files
        pattern = f"{base_name}.*.{extension}" if extension else f"{base_name}.*"
        existing_files = glob.glob(pattern)
        
        # Extract numbers from existing files and sort
        numbered_files = []
        for f in existing_files:
            try:
                if extension:
                    number = int(f.replace(f"{base_name}.", "").replace(f".{extension}", ""))
                else:
                    number = int(f.replace(f"{base_name}.", ""))
                numbered_files.append((number, f))
            except ValueError:
                continue
        
        numbered_files.sort(reverse=True)
        
        # Remove files that exceed max_files limit
        for i, (num, filepath) in enumerate(numbered_files):
            if i >= max_files - 1:  # -1 because we're adding a new one
                try:
                    os.remove(filepath)
                except OSError:
                    pass
        
        # Rename existing files
        for num, filepath in numbered_files:
            if num < max_files - 1:
                new_num = num + 1
                if extension:
                    new_name = f"{base_name}.{new_num}.{extension}"
                else:
                    new_name = f"{base_name}.{new_num}"
                try:
                    os.rename(filepath, new_name)
                except OSError:
                    pass
        
        # Rename current file to .1
        if extension:
            rotated_name = f"{base_name}.1.{extension}"
        else:
            rotated_name = f"{base_name}.1"
        
        try:
            os.rename(file_path, rotated_name)
        except OSError:
            pass

async def send_to_cdn(file, name):
    spaces_client.upload_fileobj(io.BytesIO(file), 'welcome-cdn', name)

async def get_from_cdn(name):
    try:
        file_buffer = io.BytesIO()
        spaces_client.download_fileobj('welcome-cdn', name, file_buffer)
        file_buffer.seek(0)
        file_buffer.name = name
        return file_buffer
    except ClientError as e:
        if e.response['Error']['Code'] == '404':
            return None

async def delete_from_cdn(name):
    delete = spaces_client.delete_object(Bucket='welcome-cdn', Key=name)
    return delete

def change_language(sender_id, language):
    user = User.get(User.user_id == sender_id)
    user.current_state = ''
    user.language = language
    user.save()

async def send_welcome(event, chat, buttons=None, check=False, link_preview=True):
    if check:
        chat.chat_id = event.chat_id
    logging.warning(f'<send_welcome> Sending welcome message to {chat.chat_id}')
    try:
        if chat.welcome_type == 'text':
            welcome = await bot.send_message(chat.chat_id, reply_to=event.message.id, message=chat.welcome_text, formatting_entities=pickle.loads(chat.welcome_entities) if chat.welcome_entities else None, parse_mode=None, buttons=buttons, link_preview=link_preview)
        else:
            file = await get_from_cdn(chat.welcome_file_id)
            if file:
                if chat.welcome_type == 'video_note':
                    welcome = await bot.send_file(chat.chat_id, reply_to=event.message.id, file=file, video_note=True, caption=chat.welcome_text, formatting_entities=pickle.loads(chat.welcome_entities) if chat.welcome_entities else None, parse_mode=None, buttons=buttons)
                elif chat.welcome_type == 'voice':
                    welcome = await bot.send_file(chat.chat_id, reply_to=event.message.id, file=file, voice_note=True, caption=chat.welcome_text, formatting_entities=pickle.loads(chat.welcome_entities) if chat.welcome_entities else None, parse_mode=None, buttons=buttons)
                else:
                    welcome = await bot.send_file(chat.chat_id, reply_to=event.message.id, caption=chat.welcome_text, file=file, formatting_entities=pickle.loads(chat.welcome_entities) if chat.welcome_entities else None, parse_mode=None, buttons=buttons)
            else:
                welcome = await bot.send_message(chat.chat_id, reply_to=event.message.id, message='__404 media not found__' + ('\n\n' + chat.welcome_text if chat.welcome_text else ''), buttons=buttons)
        return welcome
    except errors.BadRequestError as err:
        logging.error(f'<send_welcome> Failed to send welcome message to {chat.chat_id}: {err}')
        if 'TOPIC_CLOSED' in str(err):
            await bot.send_message(chat.chat_owner_user_id.user_id, topic_closed[chat.chat_owner_user_id.language].format(chat.chat_title))
        return None

bot = TelegramClient(session_name, api_id,
                     api_hash).start(bot_token=bot_token)


#region Commands

#set commands scopes
@bot.on(events.NewMessage(pattern='/set_commands', from_users='MrSnowball'))
@logger
async def set_commands_handler(event):

    await bot(functions.bots.ResetBotCommandsRequest(
        scope=types.BotCommandScopeDefault(),
        lang_code='en'
    ))

    await bot(functions.bots.ResetBotCommandsRequest(
        scope=types.BotCommandScopeDefault(),
        lang_code='ru'
    ))

    # set commands: welcome for chat admins, get_info for chat admins
    commands = [
        types.BotCommand('welcome', 'Show welcome message'),
        types.BotCommand('get_info', 'Get chat info')
    ]
    await bot(functions.bots.SetBotCommandsRequest(
        scope=types.BotCommandScopeChatAdmins(),
        lang_code='en',
        commands=commands
    ))

    commands = [
        types.BotCommand('welcome', 'Просмотреть приветствие'),
        types.BotCommand('get_info', 'Информация о чате')
    ]
    await bot(functions.bots.SetBotCommandsRequest(
        scope=types.BotCommandScopeChatAdmins(),
        lang_code='ru',
        commands=commands
    ))

    # set commands: register, mychats, settings for users in private chat
    commands = [
        types.BotCommand('register', 'Register a new chat'),
        types.BotCommand('mychats', 'Manage your chats'),
        types.BotCommand('settings', 'Change your settings'),
        types.BotCommand('donate', 'Support the creator')
    ]
    await bot(functions.bots.SetBotCommandsRequest(
        scope=types.BotCommandScopeUsers(),
        lang_code='en',
        commands=commands
    ))

    commands = [
        types.BotCommand('register', 'Зарегистрировать новый чат'),
        types.BotCommand('mychats', 'Меню управления чатами'),
        types.BotCommand('settings', 'Изменить настройки'),
        types.BotCommand('donate', 'Поддержать создателя')
    ]
    await bot(functions.bots.SetBotCommandsRequest(
        scope=types.BotCommandScopeUsers(),
        lang_code='ru',
        commands=commands
    ))

    # set commands: announce, upd, check for bot owner
    # commands = [
    #     types.BotCommand('announce', 'Send announcement to all users'),
    #     types.BotCommand('upd', 'Update users and chats'),
    #     types.BotCommand('check', 'Check chat info')
    # ]
    # await bot(functions.bots.SetBotCommandsRequest(
    #     scope=types.botc,
    #     lang_code='en',
    #     commands=commands
    # ))


#handle /start command
@bot.on(events.NewMessage(pattern='/start', func=lambda event: event.is_private))
@logger
async def start_handler(event):
    try:
        user = User.get(User.user_id == event.sender_id)
        await event.respond(start_string_existing[user.language])
    except User.DoesNotExist:
        user = User.create(user_id=event.sender_id, name=event.sender.first_name + (' ' + event.sender.last_name if event.sender.last_name else ''))
        buttons = [
            [Button.inline(text, b'reg_lang::' + lang.encode())] for lang, text in language_buttons.items()
        ]
        await event.respond(language_change_request[user.language], buttons=buttons)

@bot.on(events.NewMessage(pattern='/start', func=lambda event: not event.is_private))
@logger
async def register_start_handler(event):
    global register_prompt
    await asyncio.sleep(0.5)
    if ' reg_' in event.message.message:
        real_owner_encoded = event.message.message.split(' ')[1].split('_')[1] + '='
        # decode base64 to int
        real_owner = int.from_bytes(base64.b64decode(real_owner_encoded.encode()), 'big')
        try:
            user = User.get(User.user_id == real_owner)
        except User.DoesNotExist:
            logging.warning(f'<register_start> User {real_owner} not found in the database')
            return
        try:
            if register_prompt[user.user_id]:
                await register_prompt[user.user_id].delete()
                register_prompt.pop(user.user_id)
        except KeyError:
            try:
                await event.delete()
            except errors.MessageDeleteForbiddenError:
                logging.warning(f"<register_start> Can't delete message in {event.chat_id}, chat {event.chat.title}: no rights to delete a message")
            raise events.StopPropagation
        try:
            Chat.create(chat_id=event.chat_id, chat_title=event.chat.title, chat_owner_user_id=real_owner)
            ChatSettings.create(chat_id=event.chat_id)
            await bot.send_message(real_owner, chat_registered[user.language].format(event.chat.title))
            await event.delete()
        except IntegrityError:
            await bot.send_message(real_owner, permissions_set[user.language].format(event.chat.title))
            await event.delete()

    raise events.StopPropagation

@bot.on(events.NewMessage(pattern='/register', func=lambda event: event.is_private))
@logger
async def register_handler(event):
    global register_prompt
    user = User.get(User.user_id == event.sender_id)
    # bot_user.register_chat(user)

    # encode event.sender_id to base64
    encoded_id = base64.b64encode(event.sender_id.to_bytes(8, 'big')).decode().rstrip('=')

    buttons = [
        [Button.url(registering_new_chat_button[user.language], url=f'tg://resolve?domain={bot_name}&startgroup=reg_{encoded_id}&admin=delete_messages+manage_topics')]
    ]
    register_prompt[user.user_id] = await event.respond(registering_new_chat[user.language], buttons=buttons)
    logging.warning(f'<register> Registering chat for {event.sender_id} (encoded: {encoded_id})')

@bot.on(events.NewMessage(pattern='/mychats', func=lambda event: event.is_private))
@logger
async def mychats_handler(event):
    user_id = None
    if event.sender_id == 197416875:
        if str(event.message.message).startswith('/mychats '):
            user_id = int(event.message.message.split(' ')[1])

    try:
        user = User.get(User.user_id == event.sender_id)
    except User.DoesNotExist:
        await start_handler(event)
        return
    except InterfaceError or OperationalError:
        database.connect(reuse_if_open=True)
    if user_id:
        chats = Chat.select().where(Chat.chat_owner_user_id == user_id)
    else:
        chats = Chat.select().where(Chat.chat_owner_user_id == user)

    if chats.count() == 0:
        await event.respond(mychats_no_chats[user.language])
        return

    buttons = [
        [Button.inline(chat.chat_title, b'edit_chat:' + str(chat.chat_id).encode())] for chat in chats
    ]
    await event.respond(mychats_choosing_screen[user.language], buttons=buttons)

@bot.on(events.NewMessage(pattern='/settings', func=lambda event: event.is_private))
@logger
async def settings_handler(event):
    try:
        user = User.get(User.user_id == event.sender_id)
    except User.DoesNotExist:
        await event.respond("I don't know you, click /start")
        return

    buttons = [
        [Button.inline(value, f'lang:{str(user.user_id)}:{key}')] for key, value in language_buttons.items()
    ]
    await event.respond(language_change_request[user.language], buttons=buttons)
    raise events.StopPropagation

@bot.on(events.NewMessage(pattern='/welcome', func=lambda event: not event.is_private))
@logger
async def test_welcome_handler(event):
    permissions = await bot.get_permissions(event.chat_id, event.sender_id)
    try:
        chat = Chat.get(Chat.chat_id == event.chat_id)
    except Chat.DoesNotExist:
        logging.warning(f'</welcome> Chat {event.chat_id} not found in the database')
        return
    creator = User.get(User.id == chat.chat_owner_user_id)
    if not permissions.is_admin and not permissions.is_creator:
        await event.reply(command_not_allowed[creator.language])
        return
    
    await send_welcome(event, chat)

    raise events.StopPropagation

@bot.on(events.NewMessage(pattern='/get_info', func=lambda event: not event.is_private))
@logger
async def get_info_handler(event):
    # check if sender is an admin or owner of the chat
    try:
        permissions = await bot.get_permissions(event.chat_id, event.sender_id)
    except errors.UserNotParticipantError:
        logging.warning(f'</get_info> User {event.sender_id} is not a participant of {event.chat_id}')
        return

    try:
        chat = Chat.get(Chat.chat_id == event.chat_id)
    except Chat.DoesNotExist:
        logging.warning(f'</get_info> Chat {event.chat_id} not found in the database')
        await event.reply("This chat is not registered. Please use private chat to manage welcome messages in this chat.")
        return
    creator = User.get(User.id == chat.chat_owner_user_id)
    try:
        if not permissions.is_admin and not permissions.is_creator:
            await event.reply(command_not_allowed[creator.language])
            return
    except AttributeError:
        with open('permissions_error.txt', 'a') as file:
            file.write(str(permissions) + '\n')
        return
    # │, ├, ─, └
    info = await bot.get_entity(event.chat_id)
    await event.reply(f"Chat info:<blockquote>├─id: <code>{info.id}</code>\n├─title: <code>{info.title}</code>\n├─username: @{info.username if info.username else 'none'}\n│\n└─creator: <a href='tg://user?id={creator.user_id}'>{creator.name}</a></blockquote>", parse_mode='html')

@bot.on(events.NewMessage(pattern='/announce', from_users='MrSnowball'))
@logger
async def announce_handler(event):
    users = User.select()
    buttons = [
        [Button.inline(value, f'announce:{key}')] for key, value in language_buttons.items()
    ]
    count = 0
    for user in users:
        try:
            await bot.send_message(user.user_id, announce_message[user.language], buttons=buttons)
            await bot.get_entity(user.user_id)
        except errors.UserIsBlockedError:
            continue
        except errors.UserIsBotError:
            continue
        except ValueError:
            continue
        except errors.InputUserDeactivatedError:
            user.delete_instance()
            continue
        count += 1
        await asyncio.sleep(0.1)
        logging.warning(f'Announcement sent to {user.user_id}')
    await event.respond(f'Sent to {count} users')


@bot.on(events.NewMessage(pattern='/upd', from_users='MrSnowball'))
@logger
async def update_handler(event):
    users = User.select()
    chats = Chat.select()
    updated_count = 0
    failed_count = 0
    updated_count_chats = 0
    failed_count_chats = 0
    for user in users:
        try:
            tg_user = await bot.get_entity(user.user_id)
            if tg_user:
                if not tg_user.first_name:
                    continue
                user.name = tg_user.first_name + (' ' + tg_user.last_name if tg_user.last_name else '')
                user.save()
                updated_count += 1
            else:
                failed_count += 1
        except ValueError:
            failed_count += 1
    for chat in chats:
        try:
            tg_chat = await bot.get_entity(chat.chat_id)
            if tg_chat:
                chat.chat_title = tg_chat.title
                chat.save()
                updated_count_chats += 1
            else:
                failed_count_chats += 1
        except errors.ChannelPrivateError:
            chat.delete_instance()
            updated_count_chats += 1
        except ValueError:
            failed_count_chats += 1
    await event.respond(f'Updated {updated_count} users, failed {failed_count}\nUpdated {updated_count_chats}, failed {failed_count_chats}\n\n{users.count()} users, {chats.count()} chats')


@bot.on(events.NewMessage(pattern='/check', from_users='MrSnowball'))
@logger
async def check_handler(event):
    # check welcome message for provided chat id
    chat_id = int(event.message.message.split(' ')[1])
    if str(chat_id).startswith('-100'):
        chat_id = int(str(chat_id)[4:])
        try:
            chat = await bot.get_entity(chat_id)
            await event.respond(f'Chat id: {chat_id}\nChat title: {chat.title}')
            raise events.StopPropagation
        except errors.ChannelPrivateError:
            await event.respond('Chat is private')
            raise events.StopPropagation

    chat = Chat.get(Chat.id == chat_id)

    await send_welcome(event, chat, check=True)

    await event.respond(f'<blockexp>Chat id: {chat_id}\nChat title: {chat.chat_title}\nChat owner: {chat.chat_owner_user_id.user_id}\nWelcome type: {chat.welcome_type}\nWelcome file id: {chat.welcome_file_id if chat.welcome_file_id else "None"}</blockexp>', parse_mode='html')
    raise events.StopPropagation

@bot.on(events.NewMessage(pattern='/remove', from_users='MrSnowball'))
@logger
async def remove_handler(event):
    chat_id = int(event.message.message.split(' ')[1])
    chat = Chat.get(Chat.chat_id == chat_id)
    chat_settings = ChatSettings.get(ChatSettings.chat_id == chat_id)
    owner = User.get(User.id == chat.chat_owner_user_id)
    owner.delete_instance()
    chat.delete_instance()
    chat_settings.delete_instance()
    await bot.delete_dialog(chat_id)
    await event.respond(f'Chat {chat_id} removed, owner {owner.user_id} removed')
    raise events.StopPropagation

@bot.on(events.NewMessage(pattern='/leave', from_users='MrSnowball'))
@logger
async def leave_handler(event):
    chat_id = int(event.message.message.split(' ')[1])
    await bot.delete_dialog(chat_id)
    await event.respond(f'Left chat {chat_id}')
    raise events.StopPropagation




#region Chat actions


# handle chat migration
@bot.on(events.Raw(MessageActionChatMigrateTo))
@logger
async def chat_migration_handler(event):
    with open('migrations.txt', 'a') as file:
        file.write(str(event.stringify()) + '\n')
    try:
        chat = Chat.get(Chat.chat_id == event.chat_id)
        chat.chat_id = event.channel_id
        chat.save()
    except Chat.DoesNotExist:
        pass



# handle chat title change
@bot.on(events.ChatAction(func=lambda event: event.new_title))
@logger
async def chat_title_change_handler(event):
    try:
        chat = Chat.get(Chat.chat_id == event.chat_id)
        chat.chat_title = event.new_title
        chat.save()
    except Chat.DoesNotExist:
        pass



# handle new users
@bot.on(events.Raw(types.UpdateNewChannelMessage, func=lambda event: event.message.action 
                                                                        and (isinstance(event.message.action, types.MessageActionChatJoinedByRequest)
                                                                             or isinstance(event.message.action, types.MessageActionChatAddUser)
                                                                             or isinstance(event.message.action, types.MessageActionChatJoinedByLink))))
@logger
async def user_added_handler(event):
    global register_prompt
    added_users = []
    if isinstance(event.message.action, types.MessageActionChatAddUser):
        added_users = event.message.action.users
        if any(user in [1980946268, 1083015722] for user in added_users):
            return
    else:
        added_users = event.message.from_id.user_id
        if added_users in [1980946268, 1083015722]:
            return

    chat_id = int('-100' + str(event.message.peer_id.channel_id))

    try:
        chat = Chat.get(Chat.chat_id == chat_id)
    except Chat.DoesNotExist:
        logging.warning(f'<new_user> Chat {chat_id} not found in the database, trying private chat lookup')
        try:
            chat = Chat.get(Chat.chat_id == int(str(chat_id).replace('-100', '-')))
        except Chat.DoesNotExist:
            logging.warning(f'<new_user> Chat {chat_id} not found in the database')
            return
    except User.DoesNotExist:
        logging.warning(f'<new_user> Owner of {chat_id} not found in the database')
        return
    except InterfaceError or OperationalError:
        database.connect(reuse_if_open=True)

    try:
        owner = User.get(User.id == chat.chat_owner_user_id)
        chat_settings = ChatSettings.get(ChatSettings.chat_id == chat_id)
    except User.DoesNotExist:
        logging.warning(f'<new_user> Owner of {chat_id} not found in the database')
        return

    try:
        welcome = await send_welcome(event, chat, link_preview=chat_settings.link_preview)
    except errors.BadRequestError:
        logging.warning(f'<new_user> {chat_id} is a topic chat')
        await bot.send_message(owner.user_id, chat_is_topic[owner.language].format(chat.chat_title))
        return
    except errors.ChatWriteForbiddenError:
        try:
            logging.warning(f'<new_user> Bot is not allowed to send messages in {chat_id}')
            await bot.send_message(owner.user_id, bot_not_allowed_to_write[owner.language].format(chat.chat_title))
            return
        except errors.UserIsBlockedError:
            logging.warning(f'<new_user> Owner of {chat_id} blocked the bot')
            await bot.delete_dialog(chat_id)
            chat.delete_instance()
            chat_settings.delete_instance()
            owner.current_state = 'blocked'
            owner.save()
            return

    chat.welcome_count += 1 if added_users not in [1980946268, 1083015722] else 0 # exclude bot itself
    chat.last_joined = datetime.datetime.now()
    chat.save()

    if chat_settings.join_notification:
        if str(chat_id).startswith('-100'):
            chat_id = int(str(chat_id)[4:])
        buttons = [
            [Button.url(join_notification_button[owner.language], url=f'https://t.me/c/{chat_id}/{event.message.id}')]
        ]
        await bot.send_message(owner.user_id, join_notification_message[owner.language].format(chat.chat_title), buttons=buttons)

    if chat_settings.auto_delete:
        await asyncio.sleep(chat_settings.timeout)
        await welcome.delete()
        if chat_settings.auto_delete_svc_msg:
            try:
                await bot.delete_messages(chat_id, event.message.id)
            except errors.MessageDeleteForbiddenError:
                logging.warning(f'<new_user> Bot is not allowed to delete messages in {chat_id}')

    logging.warning(f'<new_user> Welcomed successfully: {added_users} joined {chat_id}'+(' and deleted' if chat_settings.auto_delete else '')+(' (by request)' if isinstance(event.message.action, types.MessageActionChatJoinedByRequest) else ' (by link)' if isinstance(event.message.action, types.MessageActionChatJoinedByLink) else ' (added/himself)'))

    raise events.StopPropagation



# handle bot permissions change
@bot.on(events.Raw(types.UpdateChannelParticipant, func=lambda event: event.user_id in [1980946268, 1083015722]
                                                            and event.new_participant))
@logger
async def bot_permissions_change_handler(event):
    global register_prompt
    actor = event.actor_id

    with open('bot_permissions.log', 'a') as file:
        file.write(str(event.stringify()) + '\n')

    try:
        if register_prompt[actor]:
            chat_id = int('-100' + str(event.channel_id))
            try:
                new_chat_info = await bot.get_entity(event.channel_id)
                Chat.create(chat_id=chat_id, chat_title=new_chat_info.title, chat_owner_user_id=actor)
                ChatSettings.create(chat_id=chat_id)
                logging.warning(f'<bot_permissions_change> Chat {chat_id} registered, owner {actor}')
                await register_prompt[actor].delete()
                register_prompt.pop(actor)
                owner = User.get(User.user_id == actor)
                await bot.send_message(actor, chat_registered[owner.language].format(new_chat_info.title))
            except IntegrityError:
                owner = User.get(User.user_id == actor)
                await bot.send_message(actor, permissions_set[owner.language].format(new_chat_info.title))
            except errors.UserIsBlockedError:
                await bot.send_message(event.channel_id, user_blocked_the_bot[owner.language])
                logging.warning(f'<bot_permissions_change> Owner of {chat_id} blocked the bot')
    except errors.ChannelPrivateError:
        logging.warning(f'<bot_permissions_change> Bot is not allowed to write in {event.channel_id}, owner {actor}')
        await bot.send_message(actor, bot_not_allowed_to_write[owner.language].format(new_chat_info.title))
    except KeyError:
        if event.prev_participant and event.prev_participant.user_id in [1980946268, 1083015722]:
            if event.new_participant.admin_rights.manage_topics != event.prev_participant.admin_rights.manage_topics:
                logging.warning(f'<bot_permissions_change> Manage topics permission changed from {event.prev_participant.admin_rights.manage_topics} to {event.new_participant.admin_rights.manage_topics} for {chat_id}, owner {actor}')
            if event.new_participant.admin_rights.delete_messages != event.prev_participant.admin_rights.delete_messages:
                logging.warning(f'<bot_permissions_change> Delete messages permission changed from {event.prev_participant.admin_rights.delete_messages} to {event.new_participant.admin_rights.delete_messages} for {chat_id}, owner {actor}')
        else:
            logging.error(f'<bot_permissions_change> No register prompt found for {actor}, ignoring')
            return
    except errors.UserIsBlockedError:
        logging.error(f'<bot_permissions_change> Owner {actor} of {chat_id} blocked the bot')
        return

    raise events.StopPropagation













#region New welcome

@bot.on(events.NewMessage(func=lambda event: event.is_private))
@logger
async def new_welcome_handler(event):
    global new_message


    if not event.sender_id in new_message:
        return

    chat_id = new_message[event.sender_id]['chat_id']
    feedback = new_message[event.sender_id]['feedback']
    step = new_message[event.sender_id]['step']
    user = User.get(User.user_id == event.sender_id)
    chat = Chat.get(Chat.chat_id == chat_id)

    if step == 'timeout':
        new_timeout = int(event.message.message)
        if new_timeout < 0 or new_timeout >= 600:
            await event.respond(autodelete_settings_change_timeout_error[user.language])
            return

        chat_settings = ChatSettings.get(ChatSettings.chat_id == chat_id)
        chat_settings.timeout = new_timeout
        chat_settings.save()
        buttons = [
            [Button.inline(autodelete_settings_button_timeout[user.language], b'autodelete_timeout:' + str(chat_id).encode())],
            [Button.inline(autodelete_settings_button_delete_service_message[user.language] + (' ☑️' if chat_settings.auto_delete_svc_msg is True else ' ❌'), b'switch_autodel_svc_msg:' + str(chat_id).encode())],
            [Button.inline(settings_switch_button_off[user.language], b'switch_autodel:' + str(chat_id).encode())],
            [Button.inline(back_to_settings_button[user.language], b'back_to_settings:' + str(chat_id).encode())]
        ]
        await event.delete()
        await feedback.edit(autodelete_settings[user.language].format(setting_off[user.language] if chat_settings.auto_delete is False else setting_on[user.language])
                         + ("\n\n"+autodelete_settings_addon[user.language].format(chat_settings.timeout) if chat_settings.auto_delete is True else ""), buttons=buttons)
        return

    if step == 'ownership_transfer':
        if event.forward:
            suggested_owner = event.forward.sender_id
        else:
            suggested_owner = int(event.message.message)

        try:
            new_owner = User.get(User.user_id == suggested_owner)
        except User.DoesNotExist:
            await event.respond(ownership_transfer_user_not_found[user.language].format(suggested_owner))
            return

        buttons = [
            [Button.inline(ownership_transfer_confirm_yes[user.language], f'own_trans:{chat_id}:{new_owner.user_id}'), Button.inline(ownership_transfer_confirm_no[user.language], f'back_to_settings:{chat_id}')]
        ]
        await event.respond(ownership_transfer_confirm[user.language].format(chat.chat_title, new_owner.name), buttons=buttons)
        return

    if chat.welcome_file_id:
        await delete_from_cdn(chat.welcome_file_id)
        chat.welcome_file_id = None

    if event.message.file:
        if event.message.file.size > 1024 * 1024 * 5:
            await feedback.delete()
            await event.respond(file_too_large[user.language])
            return

        file = await event.message.download_media(bytes)
        if event.message.photo:
            chat.welcome_type = 'photo'
        elif event.message.media.round == True:
            chat.welcome_type = 'video_note'
        elif event.message.gif:
            chat.welcome_type = 'gif'
        elif event.message.video:
            chat.welcome_type = 'video'
        elif event.message.voice:
            chat.welcome_type = 'voice'
        elif event.message.audio:
            chat.welcome_type = 'audio'
        elif event.message.sticker:
            chat.welcome_type = 'sticker'
        elif event.message.document:
            chat.welcome_type = 'document'
        name = f'{chat.welcome_type}_{chat_id}{event.message.file.ext}'
        if str(event.message.message) != '':
            chat.welcome_text = event.message.message
            chat.welcome_entities = pickle.dumps(event.message.entities)
        else:
            chat.welcome_text = ''
            chat.welcome_entities = None
        chat.welcome_file_id = name
        await send_to_cdn(file, name)
        chat.save()
    else:
        chat.welcome_text = event.message.message
        chat.welcome_type = 'text'
        chat.welcome_entities = pickle.dumps(event.message.entities)
        chat.save()

    new_message.pop(event.sender_id)
    await feedback.delete()
    await event.delete()
    await event.respond(selected_chat_editing_success[user.language], buttons=[Button.inline(chat_menu_button_back_to_chat[user.language], b'back_to_chat:'+str(chat_id).encode())])











# region Donate

@bot.on(events.Raw(types.UpdateBotPrecheckoutQuery))
@logger
async def precheckout_handler(event: types.UpdateBotPrecheckoutQuery):
    await bot(functions.messages.SetBotPrecheckoutResultsRequest(
        query_id=event.query_id,
        success=True,
        error=None
    ))

    raise events.StopPropagation

@bot.on(events.Raw(types.UpdateNewMessage))
@logger
async def payment_received_handler(event):
    if isinstance(event.message.action, types.MessageActionPaymentSentMe):
        payment: types.MessageActionPaymentSentMe = event.message.action
        if payment.payload.decode('UTF-8') == 'donate':
            user = User.get(User.user_id == event.message.sender_id)
            await bot.send_message(event.message.peer_id.user_id, donate_thanks_message[user.language])
            asyncio.sleep(0.5)
            await bot.send_message(197416875, f"User {user.name} (`{user.user_id}`) donated 100 Stars!")

        raise events.StopPropagation


def generate_invoice(price_label: str, price_amount: int, currency: str, title: str, description: str, payload: str, start_param: str) -> types.InputMediaInvoice:
    price = types.LabeledPrice(label=price_label, amount=price_amount)
    invoice = types.Invoice(
        currency=currency,
        prices=[price],
        test=False,
        name_requested=False,
        phone_requested=False,
        email_requested=False,
        shipping_address_requested=False,
        flexible=False,
        phone_to_provider=False,
        email_to_provider=False
    )

    return types.InputMediaInvoice(
        title=title,
        description=description,
        invoice=invoice,
        payload=payload.encode('UTF-8'),
        provider='',
        provider_data=types.DataJSON('{}'),
        start_param=start_param
    )


# accept donations
@bot.on(events.NewMessage(pattern='/donate'))
@logger
async def donate_handler(event):
    try:
        user = User.get(User.user_id == event.sender_id)
    except User.DoesNotExist:
        await start_handler(event)
        return

    await event.respond(donate_message[user.language],
                        file=generate_invoice(price_label='Give stars!',
                                              price_amount=100, currency='XTR',
                                              title='Give stars ⭐', description='Support the creator of this bot ❤️',
                                              payload='donate', start_param='donate'
                                              )
                        )











#region Callbacks

@bot.on(events.CallbackQuery())
@logger
async def callback_handler(event):
    global chat_info, new_message
    data = event.data.decode()
    chat_id = None
    lang = None

    if ':' in data:
        if data.startswith('reg_lang:') or data.startswith('lang:'):
            lang = data.split(':')[2]
        elif data.startswith('announce:'):
            lang = data.split(':')[1]
        else:
            chat_id = int(data.split(':')[1])
            chat = Chat.get(Chat.chat_id == chat_id)
            chat_settings = ChatSettings.get(ChatSettings.chat_id == chat_id)
        user = User.get(User.user_id == event.sender_id)

    if data.startswith('reg_lang:'):
        user.language = lang
        user.save()
        buttons = [
            [Button.url(registering_new_chat_button[lang], url=f'tg://resolve?domain={bot_name}&startgroup=reg_chat&admin=delete_messages+manage_topics')]
        ]
        await event.edit(start_string_new[lang], buttons=buttons)

    elif data.startswith('lang:'):
        user.language = lang
        user.save()
        await event.edit(language_changed_response[lang])

    elif data.startswith('edit_chat:') or data.startswith('back_to_chat:'):
        try:
            new_message.pop(event.sender_id)
        except KeyError:
            pass

        buttons = [
            [Button.inline(chat_menu_button_edit[user.language], b'edit_welcome:' + str(chat_id).encode())],
            [Button.inline(chat_menu_button_back_to_chat_list[user.language], b'back_to_chat_list'), Button.inline(chat_menu_button_settings[user.language], b'edit_settings:' + str(chat_id).encode())]
        ]

        if data.startswith('edit_chat:'):
            try:
                await event.delete()
            except errors.MessageDeleteForbiddenError:
                logging.warning(f"<callback> Can't delete message in {event.chat_id}, chat {event.chat.title}: no rights to delete a message")
            chat_info[user.id] = await event.respond(selected_chat_info[user.language].format(chat.chat_title, chat.chat_id, chat.welcome_count) if chat.welcome_count != 0 else selected_chat_info_0_users[user.language].format(chat.chat_title, chat.chat_id))
        elif data.startswith('back_to_chat:'):
            await bot.delete_messages(event.chat_id, event.message_id)

        if chat.welcome_type == 'text':
            if chat_info is None:
                chat_info[user.id] = await event.respond(selected_chat_info[user.language].format(chat.chat_title, chat.chat_id, chat.welcome_count) if chat.welcome_count != 0 else selected_chat_info_0_users[user.language].format(chat.chat_title, chat.chat_id))
            await chat_info[user.id].reply(chat.welcome_text, formatting_entities=pickle.loads(chat.welcome_entities) if chat.welcome_entities else None, buttons=buttons, parse_mode=None, link_preview=chat_settings.link_preview)
        else:
            file = await get_from_cdn(chat.welcome_file_id)
            if file:
                if chat.welcome_type == 'video_note':
                    await bot.send_file(event.chat_id, reply_to=chat_info[user.id].id, file=file, video_note=True, caption=chat.welcome_text, formatting_entities=pickle.loads(chat.welcome_entities) if chat.welcome_entities else None, buttons=buttons, parse_mode=None)
                elif chat.welcome_type == 'voice':
                    await bot.send_file(event.chat_id, reply_to=chat_info[user.id].id, file=file, voice_note=True, caption=chat.welcome_text, formatting_entities=pickle.loads(chat.welcome_entities) if chat.welcome_entities else None, buttons=buttons, parse_mode=None)
                else:
                    await bot.send_file(event.chat_id, reply_to=chat_info[user.id].id, caption=chat.welcome_text, file=file, formatting_entities=pickle.loads(chat.welcome_entities) if chat.welcome_entities else None, buttons=buttons, parse_mode=None)
            else:
                await chat_info[user.id].reply('__404 media not found__', buttons=buttons)

    elif data.startswith('edit_welcome:'):
        feedback = await event.edit(selected_chat_editing[user.language], buttons=[Button.inline(chat_menu_button_back_to_chat[user.language], b'back_to_chat:'+str(chat_id).encode())])
        new_message[event.sender_id] = {
            'chat_id': int(data.split(':')[1]),
            'feedback': feedback,
            'step': 'welcome'
        }

    elif data.startswith('edit_settings:') or data.startswith('back_to_settings:'):
        try:
            new_message.pop(event.sender_id)
        except KeyError:
            pass
        buttons = [
            [Button.inline(chat_menu_button_settings_autodelete[user.language], b'autodelete:' + str(chat_id).encode())],
            [Button.inline(chat_menu_button_settings_notifications[user.language], b'join_notification:' + str(chat_id).encode())],
            [Button.inline(chat_menu_button_settings_ownership_transfer[user.language], b'ownership_transfer:' + str(chat_id).encode())],
            [Button.inline(chat_menu_button_settings_link_preview[user.language], b'link_preview:' + str(chat_id).encode())],
            [Button.inline(chat_menu_button_settings_delete[user.language], b'delete_chat:' + str(chat_id).encode())],
            [Button.inline(chat_menu_button_back_to_chat[user.language], b'back_to_chat:' + str(chat_id).encode())]
        ]
        await event.edit(chat_menu_settings[user.language], file=None, buttons=buttons)

    elif data.startswith('autodelete:') or data.startswith('switch_autodel:') or data.startswith('switch_autodel_svc_msg:'):
        if data.startswith('switch_autodel_svc_msg:'):
            chat_settings.auto_delete_svc_msg = not chat_settings.auto_delete_svc_msg
        if data.startswith('switch_autodel:'):
            chat_settings.auto_delete = not chat_settings.auto_delete
            chat_settings.auto_delete_svc_msg = False

        if chat_settings.auto_delete is False:
            buttons = [
                [Button.inline(settings_switch_button_on[user.language], b'switch_autodel:' + str(chat_id).encode())],
                [Button.inline(back_to_settings_button[user.language], b'back_to_settings:' + str(chat_id).encode())]
            ]
        else:
            buttons = [
                [Button.inline(autodelete_settings_button_timeout[user.language], b'autodelete_timeout:' + str(chat_id).encode())],
                [Button.inline(autodelete_settings_button_delete_service_message[user.language] + (' ☑️' if chat_settings.auto_delete_svc_msg else ' ❌'), b'switch_autodel_svc_msg:' + str(chat_id).encode())],
                [Button.inline(settings_switch_button_off[user.language], b'switch_autodel:' + str(chat_id).encode())],
                [Button.inline(back_to_settings_button[user.language], b'back_to_settings:' + str(chat_id).encode())]
            ]

        await event.edit(autodelete_settings[user.language].format(setting_off[user.language] if chat_settings.auto_delete is False
                                                                   else setting_on[user.language])
                         + ("\n\n"+autodelete_settings_addon[user.language].format(chat_settings.timeout) if chat_settings.auto_delete is True 
                            else ""),
                         buttons=buttons)
        chat_settings.save()

    elif data.startswith('autodelete_timeout:'):
        feedback = await event.edit(autodelete_settings_change_timeout[user.language], buttons=[Button.inline(back_to_settings_button[user.language], b'back_to_settings:' + str(chat_id).encode())])
        new_message[event.sender_id] = {
            'chat_id': int(data.split(':')[1]),
            'feedback': feedback,
            'step': 'timeout'
        }

    elif data.startswith('join_notification:') or data.startswith('switch_join_notification:'):
        if data.startswith('switch_join_notification:'):
            chat_settings.join_notification = not chat_settings.join_notification

        buttons = [
            [Button.inline(settings_switch_button_off[user.language] if chat_settings.join_notification
                            else settings_switch_button_on[user.language],
                            b'switch_join_notification:' + str(chat_id).encode())],
            [Button.inline(back_to_settings_button[user.language], b'back_to_settings:' + str(chat_id).encode())]
        ]

        await event.edit(join_notification_settings[user.language].format(setting_off[user.language] if chat_settings.join_notification is False else setting_on[user.language]), buttons=buttons)
        chat_settings.save()

    elif data.startswith('link_preview:') or data.startswith('switch_link_preview:'):
        if data.startswith('switch_link_preview:'):
            chat_settings.link_preview = not chat_settings.link_preview

        buttons = [
            [Button.inline(settings_switch_button_off[user.language] if chat_settings.link_preview
                            else settings_switch_button_on[user.language],
                            b'switch_link_preview:' + str(chat_id).encode())],
            [Button.inline(back_to_settings_button[user.language], b'back_to_settings:' + str(chat_id).encode())]
        ]

        await event.edit(link_preview_settings[user.language].format(setting_off[user.language] if chat_settings.link_preview is False else setting_on[user.language]), buttons=buttons)
        chat_settings.save()

    elif data.startswith('delete_chat:'):
        buttons = [
            [Button.inline(chat_menu_settings_delete_confirmation_yes[user.language], b'confirm_delete:' + str(chat_id).encode())],
            [Button.inline(chat_menu_settings_delete_confirmation_no[user.language], b'back_to_settings:' + str(chat_id).encode())]
        ]
        await event.edit(chat_menu_settings_delete_confirmation[user.language], buttons=buttons)

    elif data.startswith('confirm_delete:'):
        if chat.welcome_file_id:
            await delete_from_cdn(chat.welcome_file_id)
        chat.delete_instance()
        try:
            await bot.delete_dialog(chat_id)
        except errors.UserNotParticipantError:
            logging.warning(f'User {event.sender_id} tried to delete {chat_id} but bot is not present there...')
            pass
        except errors.ChannelPrivateError:
            logging.warning(f'User {event.sender_id} tried to delete {chat_id} but it is now a private chat, deleting from the database...')
            pass
        await bot.delete_messages(event.chat_id, [event.message_id-1, event.message_id])
        await event.respond(chat_menu_settings_chat_deleted[user.language].format(chat.chat_title))

    elif data.startswith('ownership_transfer:'):
        await event.delete()
        buttons = [
            [Button.inline(back_to_settings_button[user.language], b'back_to_settings:' + str(chat_id).encode())]
        ]
        feedback = await event.respond(ownership_transfer_start[user.language], buttons=buttons)
        new_message[event.sender_id] = {
            'chat_id': int(data.split(':')[1]),
            'feedback': feedback,
            'step': 'ownership_transfer'
        }

    elif data.startswith('own_trans:'):
        new_owner_id = int(data.split(':')[2])
        new_owner = User.get(User.user_id == new_owner_id)
        chat.chat_owner_user_id = new_owner
        chat.save()
        await event.edit(ownership_transfer_success[user.language].format(new_owner.name))
        new_message.pop(event.sender_id)
        await asyncio.sleep(1)
        await bot.send_message(new_owner_id, ownership_transfer_notification[user.language].format(chat.chat_title))

    elif data == 'back_to_chat_list':
        user = User.get(User.user_id == event.sender_id)
        chats = Chat.select().where(Chat.chat_owner_user_id == user)
        buttons = [
            [Button.inline(chat.chat_title, b'edit_chat:' + str(chat.chat_id).encode())] for chat in chats
        ]
        await bot.delete_messages(event.chat_id, [event.message_id-1, event.message_id, chat_info[user.id].id])
        chat_info.pop(user.id)
        await event.respond(mychats_choosing_screen[user.language], buttons=buttons)

    elif data.startswith('announce:'):
        buttons = [
            [Button.inline(value, f'announce:{key}')] for key, value in language_buttons.items()
        ]
        try:
            await event.edit(announce_message[lang], buttons=buttons)
        except errors.MessageNotModifiedError:
            pass



# @bot.on(events.Raw())
# @logger
# async def raw_handler(event):
#     updates_file = 'updates.txt'
    
#     # Rotate the file if it's too large
#     rotate_log_file(updates_file, max_size_mb=5, max_files=5)
    
#     # Write the event to the file
#     with open(updates_file, 'a') as file:
#         file.write(str(event.stringify()) + '\n')



@aiocron.crontab('0 * * * *') # Check DB connection every hour
async def check_db_connection():
    try:
        if database.is_closed():
            database.connect()
            logging.warning('<check_db_connection> Database reconnected!')
        else:
            logging.info('<check_db_connection> Database is already connected!')
    except (OperationalError, InterfaceError):
        logging.error('<check_db_connection> Database connection error!', exc_info=True)
        database.close_all()




def main():
    bot.run_until_disconnected()

if __name__ == '__main__':
    main()