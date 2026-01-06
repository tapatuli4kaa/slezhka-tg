import asyncio
import os
import sys
import time
import logging
from datetime import datetime, timedelta
from telethon import TelegramClient, events
from telethon.tl.types import UserStatusOnline, UserStatusOffline
from telethon.tl.functions.users import GetFullUserRequest
from telethon.tl.types import InputUser, MessageMediaPhoto, MessageMediaDocument, DocumentAttributeVideo, DocumentAttributeAnimated, DocumentAttributeSticker, DocumentAttributeAudio, GeoPoint, DocumentAttributeFilename, PeerUser
from dotenv import load_dotenv

load_dotenv()

api_id = int(os.getenv('API_ID'))
api_hash = os.getenv('API_HASH')
target_user_id = int(os.getenv('TARGET_USER_ID'))

# Создаем папку для логов если её нет
log_dir = "logs"
if not os.path.exists(log_dir):
    os.makedirs(log_dir)

# Настройка логгера ТОЛЬКО для файла
logger = logging.getLogger('telegram_monitor')
logger.setLevel(logging.INFO)
logger.propagate = False

log_format = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s', 
                               datefmt='%Y-%m-%d %H:%M:%S')

log_filename = f"{log_dir}/monitor_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
file_handler = logging.FileHandler(log_filename, encoding='utf-8')
file_handler.setFormatter(log_format)
logger.addHandler(file_handler)

client = TelegramClient('session', api_id, api_hash)

# Для отслеживания статуса
session_start_time = None
last_status_time = 0

# Для отслеживания профиля
last_profile = {}
profile_check_counter = 0
last_photo_id = None

# Для отслеживания набора сообщений
last_typing_time = 0
typing_cooldown = 5

# Для хранения истории сообщений (чтобы знать, что удалено)
message_history = {}  # {message_id: {type, content, caption, timestamp, sender_id}}

def log_info(message):
    logger.info(message)

def log_warning(message):
    logger.warning(message)

def log_error(message):
    logger.error(message)

def log_profile_change(change_type, details):
    message = f"ПРОФИЛЬ: {change_type} - {details}"
    logger.info(message)

def get_message_type_and_content(event):
    """Определяет тип сообщения и его содержимое"""
    # Текстовое сообщение
    if event.text and not event.media:
        content = event.text
        if len(content) > 500:
            content = content[:500] + "..."
        return "текст", content, ""
    
    # Если есть медиа
    if event.media:
        # Фото
        if isinstance(event.media, MessageMediaPhoto):
            caption = event.text or ""
            return "фото", "", caption
        
        # Файл (может быть видео, голосовое и т.д.)
        elif isinstance(event.media, MessageMediaDocument):
            doc = event.media.document
            caption = event.text or ""
            
            # Сначала проверяем все атрибуты
            has_sticker = False
            has_animated = False
            is_video = False
            is_video_message = False
            is_voice = False
            is_audio = False
            
            for attr in doc.attributes:
                # Стикер
                if isinstance(attr, DocumentAttributeSticker):
                    has_sticker = True
                
                # Анимированный (для GIF и анимированных стикеров)
                elif isinstance(attr, DocumentAttributeAnimated):
                    has_animated = True
                
                # Видео
                elif isinstance(attr, DocumentAttributeVideo):
                    is_video = True
                    if attr.round_message:
                        is_video_message = True
                
                # Аудио/голосовое
                elif isinstance(attr, DocumentAttributeAudio):
                    if attr.voice:
                        is_voice = True
                    else:
                        is_audio = True
            
            # Теперь определяем тип по приоритету
            if has_sticker and has_animated:
                return "анимированный стикер", "", caption
            elif has_sticker:
                return "стикер", "", caption
            elif is_video_message:
                return "кружок", "", caption
            elif is_video and has_animated:
                return "GIF", "", caption
            elif is_video:
                return "видео", "", caption
            elif is_voice:
                return "голосовое сообщение", "", caption
            elif is_audio:
                return "аудио", "", caption
            elif has_animated:
                return "GIF", "", caption
            
            # Проверяем по имени файла
            filename = None
            for attr in doc.attributes:
                if isinstance(attr, DocumentAttributeFilename):
                    filename = attr.file_name.lower()
                    break
            
            if filename:
                if filename.endswith('.webp'):
                    return "стикер", "", caption
                elif filename.endswith('.tgs'):
                    return "анимированный стикер", "", caption
                elif filename.endswith('.gif'):
                    return "GIF", "", caption
                elif filename.endswith('.mp4') or filename.endswith('.mov'):
                    return "видео", "", caption
                elif filename.endswith('.mp3') or filename.endswith('.ogg'):
                    if 'voice' in filename or 'audio' in filename:
                        return "голосовое сообщение", "", caption
                    else:
                        return "аудио", "", caption
                elif filename.endswith('.jpg') or filename.endswith('.png') or filename.endswith('.jpeg'):
                    return "фото", "", caption
            
            # Если не определили тип, но есть mime_type
            if doc.mime_type:
                if 'video' in doc.mime_type:
                    return f"видео-файл", "", caption
                elif 'audio' in doc.mime_type:
                    if 'ogg' in doc.mime_type:
                        return "голосовое сообщение", "", caption
                    else:
                        return f"аудио-файл", "", caption
                elif 'image' in doc.mime_type:
                    if 'gif' in doc.mime_type or 'webp' in doc.mime_type:
                        return "GIF", "", caption
                    else:
                        return f"изображение", "", caption
                else:
                    return f"файл", "", caption
            
            return "файл", "", caption
    
    # Геопозиция
    if hasattr(event, 'geo') and event.geo:
        if isinstance(event.geo, GeoPoint):
            coords = f"{event.geo.lat}, {event.geo.long}"
            return "геолокация", coords, ""
    
    # Контакт
    if hasattr(event, 'contact') and event.contact:
        contact_name = f"{event.contact.first_name or ''} {event.contact.last_name or ''}".strip()
        contact_phone = event.contact.phone_number or ""
        contact_info = f"{contact_name} ({contact_phone})" if contact_name else contact_phone
        return "контакт", contact_info, ""
    
    # Опрос
    if hasattr(event, 'poll') and event.poll:
        return "опрос", event.poll.question or "", ""
    
    # Если ничего не определили
    return "сообщение", "", ""

def format_message_info(msg_type, content, caption, sender_name=""):
    """Форматирует информацию о сообщении для вывода"""
    result = f"Тип: {msg_type}"
    
    if content:
        if len(content) > 200:
            content = content[:200] + "..."
        result += f" | Содержимое: {content}"
    
    if caption:
        if len(caption) > 200:
            caption = caption[:200] + "..."
        result += f" | Подпись: {caption}"
    
    return result

async def check_profile_once():
    global last_profile, profile_check_counter, last_photo_id
    
    try:
        profile_check_counter += 1
        now_time = datetime.now().strftime('%H:%M:%S')
        log_info(f"Проверка профиля #{profile_check_counter}")
        
        user = await client.get_entity(target_user_id)
        
        bio = ""
        bio_success = False
        
        try:
            user_full = await client.get_entity(target_user_id)
            if hasattr(user_full, 'access_hash'):
                input_user = InputUser(user_id=user_full.id, access_hash=user_full.access_hash)
                result = await client(GetFullUserRequest(input_user))
                
                if hasattr(result, 'full_user') and hasattr(result.full_user, 'about'):
                    bio = result.full_user.about or ""
                    bio_success = True
                else:
                    log_warning("Нет поля 'about' в ответе")
            else:
                log_warning("Нет access_hash у пользователя")
                
        except Exception as e:
            error_msg = f"Ошибка GetFullUserRequest: {type(e).__name__}: {str(e)[:80]}"
            log_warning(error_msg)
        
        current_photo_id = None
        if user.photo:
            if hasattr(user.photo, 'photo_id'):
                current_photo_id = user.photo.photo_id
            elif hasattr(user.photo, 'id'):
                current_photo_id = user.photo.id
        
        current = {
            'first_name': user.first_name or "",
            'last_name': user.last_name or "",
            'username': user.username or "",
            'bio': bio,
            'bio_success': bio_success,
            'has_photo': bool(user.photo),
            'photo_id': current_photo_id
        }
        
        if not last_profile:
            last_profile = current
            last_photo_id = current_photo_id
            
            initial_data = {
                'first_name': current['first_name'],
                'last_name': current['last_name'],
                'username': current['username'],
                'bio_length': len(current['bio']),
                'has_photo': current['has_photo']
            }
            log_info(f"Начальные данные профиля: {initial_data}")
            
            print(f"\n[#{profile_check_counter} {now_time}] Начальная проверка профиля")
            return
        
        changed = False
        
        if last_profile.get('first_name') != current['first_name']:
            changed = True
            details = f"Было: '{last_profile['first_name'] or 'пусто'}'; Стало: '{current['first_name'] or 'пусто'}'"
            log_profile_change("Имя изменено (в контактах)", details)
            print(f"\n[#{profile_check_counter} {now_time}] ИМЯ ИЗМЕНЕНО (в контактах)")
            print(f"Было: '{last_profile['first_name'] or 'пусто'}'")
            print(f"Стало: '{current['first_name'] or 'пусто'}'")
        
        if last_profile.get('last_name') != current['last_name']:
            changed = True
            details = f"Было: '{last_profile['last_name'] or 'пусто'}'; Стало: '{current['last_name'] or 'пусто'}'"
            log_profile_change("Фамилия изменена (в контактах)", details)
            print(f"\n[#{profile_check_counter} {now_time}] ФАМИЛИЯ ИЗМЕНЕНА (в контактах)")
            print(f"Было: '{last_profile['last_name'] or 'пусто'}'")
            print(f"Стало: '{current['last_name'] or 'пусто'}'")
        
        if last_profile.get('username') != current['username']:
            changed = True
            details = f"Было: @{last_profile['username'] or 'нет'}; Стало: @{current['username'] or 'нет'}"
            log_profile_change("Username изменен", details)
            print(f"\n[#{profile_check_counter} {now_time}] USERNAME ИЗМЕНЕН")
            print(f"Было: @{last_profile['username'] or 'нет'}")
            print(f"Стало: @{current['username'] or 'нет'}")
        
        if bio_success and last_profile.get('bio_success'):
            old_bio = last_profile.get('bio', '')
            new_bio = current['bio']
            
            if old_bio != new_bio:
                changed = True
                old_preview = old_bio[:60] + '...' if len(old_bio) > 60 else old_bio or 'пусто'
                new_preview = new_bio[:60] + '...' if len(new_bio) > 60 else new_bio or 'пусто'
                details = f"Было: {old_preview}; Стало: {new_preview}"
                log_profile_change("Био изменено", details)
                print(f"\n[#{profile_check_counter} {now_time}] БИО ИЗМЕНЕНО")
                print(f"Было: {old_preview}")
                print(f"Стало: {new_preview}")
        
        elif not bio_success and last_profile.get('bio_success'):
            log_info("Био стало недоступно (возможно, изменены настройки приватности)")
            print(f"\n[#{profile_check_counter} {now_time}] Био стало недоступно")
            changed = True
        elif bio_success and not last_profile.get('bio_success'):
            log_info("Био стало доступно")
            print(f"\n[#{profile_check_counter} {now_time}] Био стало доступно")
            changed = True
        
        photo_changed = False
        
        if last_profile.get('has_photo') != current['has_photo']:
            changed = True
            photo_changed = True
            if current['has_photo']:
                log_profile_change("Аватарка добавлена", "")
                print(f"\n[#{profile_check_counter} {now_time}] АВАТАРКА ДОБАВЛЕНА")
            else:
                log_profile_change("Аватарка удалена", "")
                print(f"\n[#{profile_check_counter} {now_time}] АВАТАРКА УДАЛЕНА")
        
        elif (current_photo_id and last_photo_id and 
              current_photo_id != last_photo_id):
            changed = True
            photo_changed = True
            details = f"Старый ID: {last_photo_id}; Новый ID: {current_photo_id}"
            log_profile_change("Аватарка изменена (новое фото)", details)
            print(f"\n[#{profile_check_counter} {now_time}] АВАТАРКА ИЗМЕНЕНА")
        
        if photo_changed:
            last_photo_id = current_photo_id
        
        if changed:
            log_info("Обнаружены изменения в профиле")
        else:
            log_info("Изменений в профиле нет")
        
        last_profile = current
        
    except Exception as e:
        error_msg = f"Ошибка проверки профиля: {type(e).__name__}: {str(e)[:100]}"
        log_error(error_msg)
        print(f"\n[#{profile_check_counter}] Ошибка: {error_msg}")

@client.on(events.UserUpdate)
async def status_handler(event):
    global session_start_time, last_status_time
    
    if event.user_id != target_user_id:
        return
    
    current_time = time.time()
    if current_time - last_status_time < 1:
        return
    
    last_status_time = current_time
    now_str = datetime.now().strftime('%H:%M:%S')
    
    if isinstance(event.status, UserStatusOnline):
        log_info(f"ПОЛЬЗОВАТЕЛЬ В ОНЛАЙНЕ")
        
        print(f"\n{'='*60}")
        print(f"{now_str} - ПОЛЬЗОВАТЕЛЬ В ОНЛАЙНЕ")
        session_start_time = time.time()
        print(f"{'='*60}")
    
    elif isinstance(event.status, UserStatusOffline):
        if session_start_time:
            duration = int(time.time() - session_start_time)
            log_info(f"ПОЛЬЗОВАТЕЛЬ В ОФЛАЙНЕ. Был(-а) онлайн: {duration//60} мин {duration%60} сек")
        else:
            log_info(f"ПОЛЬЗОВАТЕЛЬ В ОФЛАЙНЕ")
        
        print(f"\n{'='*60}")
        print(f"{now_str} - ПОЛЬЗОВАТЕЛЬ В ОФЛАЙНЕ")
        
        if session_start_time:
            duration = int(time.time() - session_start_time)
            print(f"Был(-а) онлайн: {duration//60} мин {duration%60} сек")
            session_start_time = None
        
        print(f"{'='*60}")

@client.on(events.Raw)
async def raw_handler(update):
    """Универсальный обработчик сырых событий"""
    global last_typing_time
    
    try:
        # Проверяем разные типы событий
        event_type = type(update).__name__
        
        # События набора текста, записи голосового, загрузки вложений и процесса отправки
        if event_type == 'UpdateUserTyping':
            if update.user_id == target_user_id:
                current_time = time.time()
                if current_time - last_typing_time < typing_cooldown:
                    return
                
                last_typing_time = current_time
                now_str = datetime.now().strftime('%H:%M:%S')
                
                action_desc = "печатает текст"
                if hasattr(update, 'action'):
                    action_type = type(update.action).__name__
                    if 'Audio' in action_type:
                        action_desc = "записывает голосовое"
                    elif 'Video' in action_type:
                        action_desc = "записывает видео"
                    elif 'Photo' in action_type:
                        action_desc = "загружает фото"
                    elif 'Document' in action_type:
                        action_desc = "загружает файл"
                    elif 'Geo' in action_type:
                        action_desc = "отправляет геолокацию"
                    elif 'Contact' in action_type:
                        action_desc = "отправляет контакт" 
                
                log_info(f"ПОЛЬЗОВАТЕЛЬ {action_desc.upper()} в {now_str}")
                print(f"\n{'='*60}")
                print(f"{now_str} - ПОЛЬЗОВАТЕЛЬ {action_desc.upper()}")
                print(f"{'='*60}")
        
        # Событие записи голосового
        elif event_type == 'UpdateUserRecordVoice':
            if update.user_id == target_user_id:
                current_time = time.time()
                if current_time - last_typing_time < typing_cooldown:
                    return
                
                last_typing_time = current_time
                now_str = datetime.now().strftime('%H:%M:%S')
                
                log_info(f"ПОЛЬЗОВАТЕЛЬ ЗАПИСЫВАЕТ ГОЛОСОВОЕ")
                print(f"\n{'='*60}")
                print(f"{now_str} - ПОЛЬЗОВАТЕЛЬ ЗАПИСЫВАЕТ ГОЛОСОВОЕ")
                print(f"{'='*60}")
        
        # Событие записи кружка
        elif event_type == 'UpdateUserRecordVideo':
            if update.user_id == target_user_id:
                current_time = time.time()
                if current_time - last_typing_time < typing_cooldown:
                    return
                
                last_typing_time = current_time
                now_str = datetime.now().strftime('%H:%M:%S')
                
                log_info(f"ПОЛЬЗОВАТЕЛЬ ЗАПИСЫВАЕТ КРУЖОК")
                print(f"\n{'='*60}")
                print(f"{now_str} - ПОЛЬЗОВАТЕЛЬ ЗАПИСЫВАЕТ КРУЖОК")
                print(f"{'='*60}")
        
        # ОБРАБОТЧИК УДАЛЕННЫХ СООБЩЕНИЙ
        elif event_type == 'UpdateDeleteMessages':
            if hasattr(update, 'messages'):
                for msg_id in update.messages:
                    if msg_id in message_history:
                        msg_data = message_history[msg_id]
                        if msg_data['sender_id'] == target_user_id:
                            now_str = datetime.now().strftime('%H:%M:%S')
                            sent_time = msg_data['timestamp'].strftime('%H:%M:%S')
                            sent_date = msg_data['timestamp'].strftime('%Y-%m-%d')
                            
                            msg_info = format_message_info(
                                msg_data['type'], 
                                msg_data['content'], 
                                msg_data['caption']
                            )
                            
                            log_info(f"СООБЩЕНИЕ УДАЛЕНО ПОЛЬЗОВАТЕЛЕМ | ID: {msg_id} | {msg_info} | Отправлено: {sent_date} {sent_time}")
                            
                            print(f"\n{'='*60}")
                            print(f"{now_str} - СООБЩЕНИЕ УДАЛЕНО ПОЛЬЗОВАТЕЛЕМ")
                            print(f"ID сообщения: {msg_id}")
                            print(f"Отправлено: {sent_date} {sent_time}")
                            print(f"{msg_info}")
                            print(f"{'='*60}")
                            
                            # Удаляем из истории
                            del message_history[msg_id]
                
    except Exception as e:
        # Игнорируем ошибки в raw обработчике
        pass

# Обработчик входящих сообщений
@client.on(events.NewMessage(incoming=True))
async def message_handler(event):
    """Обработчик входящих сообщений"""
    try:
        # Если сообщение от целевого пользователя
        if event.sender_id == target_user_id:
            now = datetime.now()
            now_str = now.strftime('%H:%M:%S')
            
            # Получаем информацию о сообщении
            msg_type, content, caption = get_message_type_and_content(event)
            
            # Сохраняем в историю
            message_history[event.id] = {
                'type': msg_type,
                'content': content,
                'caption': caption,
                'timestamp': now,
                'sender_id': event.sender_id
            }
            
            # Очищаем старые сообщения (старше 24 часов)
            old_keys = []
            for msg_id, msg_data in message_history.items():
                if now - msg_data['timestamp'] > timedelta(hours=24):
                    old_keys.append(msg_id)
            for key in old_keys:
                del message_history[key]
            
            # Получаем имя отправителя
            sender_name = ""
            try:
                sender = await event.get_sender()
                if sender:
                    sender_name = f"{sender.first_name or ''} {sender.last_name or ''}".strip()
                    if not sender_name:
                        sender_name = f"@{sender.username}" if sender.username else f"ID: {sender.id}"
            except:
                pass
            
            # Форматируем информацию о сообщении
            msg_info = format_message_info(msg_type, content, caption, sender_name)
            
            log_info(f"ПОЛУЧЕНО СООБЩЕНИЕ | {msg_info}")
            
            # Разный вывод для разных типов сообщений
            if "анимированный стикер" in msg_type:
                print(f"\n{'='*60}")
                print(f"{now_str} - ПОЛУЧЕН {msg_type.upper()}")
                if caption:
                    print(f"Подпись: {caption}")
                print(f"{'='*60}")
            
            elif "стикер" in msg_type:
                print(f"\n{'='*60}")
                print(f"{now_str} - ПОЛУЧЕН {msg_type.upper()}")
                if caption:
                    print(f"Подпись: {caption}")
                print(f"{'='*60}")
            
            elif "кружок" in msg_type:
                print(f"\n{'='*60}")
                print(f"{now_str} - ПОЛУЧЕН {msg_type.upper()}")
                if caption:
                    print(f"Подпись: {caption}")
                print(f"{'='*60}")
            
            elif "GIF" in msg_type:
                print(f"\n{'='*60}")
                print(f"{now_str} - ПОЛУЧЕН {msg_type.upper()}")
                if caption:
                    print(f"Подпись: {caption}")
                print(f"{'='*60}")
            
            elif "видео" in msg_type:
                print(f"\n{'='*60}")
                print(f"{now_str} - ПОЛУЧЕНО {msg_type.upper()}")
                if caption:
                    print(f"Подпись: {caption}")
                print(f"{'='*60}")
            
            elif "голосовое" in msg_type:
                print(f"\n{'='*60}")
                print(f"{now_str} - ПОЛУЧЕНО {msg_type.upper()}")
                if caption:
                    print(f"Подпись: {caption}")
                print(f"{'='*60}")
            
            elif "геолокация" in msg_type:
                print(f"\n{'='*60}")
                print(f"{now_str} - ПОЛУЧЕНА {msg_type.upper()}")
                if content:
                    print(f"Координаты: {content}")
                if caption:
                    print(f"Подпись: {caption}")
                print(f"{'='*60}")
            
            elif "фото" in msg_type or "изображение" in msg_type:
                print(f"\n{'='*60}")
                print(f"{now_str} - ПОЛУЧЕНО {msg_type.upper()}")
                if caption:
                    print(f"Подпись: {caption}")
                print(f"{'='*60}")
            
            elif "текст" in msg_type:
                print(f"\n{'='*60}")
                print(f"{now_str} - ПОЛУЧЕНО {msg_type.upper()}")
                if content:
                    print(f"Текст: {content}")
                print(f"{'='*60}")
            
            elif "аудио" in msg_type:
                print(f"\n{'='*60}")
                print(f"{now_str} - ПОЛУЧЕНО {msg_type.upper()}")
                if caption:
                    print(f"Подпись: {caption}")
                print(f"{'='*60}")
            
            else:
                print(f"\n{'='*60}")
                print(f"{now_str} - ПОЛУЧЕНО {msg_type.upper()}")
                if content:
                    print(f"Содержимое: {content}")
                if caption:
                    print(f"Подпись: {caption}")
                print(f"{'='*60}")
            
    except Exception as e:
        # Логируем ошибку
        log_error(f"Ошибка в message_handler: {e}")

# Обработчик редактирования сообщений (ИСПРАВЛЕННАЯ ВЕРСИЯ)
@client.on(events.MessageEdited(incoming=True))
async def message_edited_handler(event):
    """Обработчик отредактированных сообщений"""
    try:
        # Если сообщение от целевого пользователя
        if event.sender_id == target_user_id:
            now_str = datetime.now().strftime('%H:%M:%S')
            
            # Получаем информацию о сообщении ПОСЛЕ редактирования
            new_msg_type, new_content, new_caption = get_message_type_and_content(event)
            
            # Получаем имя отправителя
            sender_name = ""
            try:
                sender = await event.get_sender()
                if sender:
                    sender_name = f"{sender.first_name or ''} {sender.last_name or ''}".strip()
                    if not sender_name:
                        sender_name = f"@{sender.username}" if sender.username else f"ID: {sender.id}"
            except:
                pass
            
            # Проверяем, было ли сообщение в истории
            if event.id in message_history:
                old_data = message_history[event.id]
                old_time = old_data['timestamp'].strftime('%H:%M:%S')
                old_date = old_data['timestamp'].strftime('%Y-%m-%d')
                
                # Сохраняем старые данные перед изменением
                old_content = old_data['content']
                old_caption = old_data['caption']
                old_type = old_data['type']
                
                # Инициализируем переменные для изменений
                content_changed = False
                caption_changed = False
                type_changed = False
                media_changed = False
                
                # Сравниваем содержание (только для текстовых сообщений)
                if old_content != new_content:
                    content_changed = True
                
                # Сравниваем подпись
                if old_caption != new_caption:
                    caption_changed = True
                
                # Сравниваем тип
                if old_type != new_msg_type:
                    type_changed = True
                    # Если тип изменился, значит изменилось медиа
                    media_changed = True
                
                # Обновляем в истории
                message_history[event.id].update({
                    'type': new_msg_type,
                    'content': new_content,
                    'caption': new_caption,
                    'timestamp': datetime.now()  # Обновляем время при редактировании
                })
                
                # Формируем заголовок
                header = f"СООБЩЕНИЕ ОТРЕДАКТИРОВАНО | ID: {event.id} | "
                header += f"Время отправки: {old_date} {old_time} | "
                header += f"Время редактирования: {now_str}"
                
                # Формируем детали для лога
                log_details = header
                
                # Добавляем информацию об изменениях
                if type_changed:
                    log_details += f" | Тип: {old_type} → {new_msg_type}"
                else:
                    log_details += f" | Тип: {new_msg_type}"
                
                if content_changed:
                    old_preview = old_content[:50] + '...' if len(old_content) > 50 else old_content
                    new_preview = new_content[:50] + '...' if len(new_content) > 50 else new_content
                    log_details += f" | Текст изменен: '{old_preview}' → '{new_preview}'"
                
                if caption_changed:
                    old_cap_preview = old_caption[:50] + '...' if len(old_caption) > 50 else old_caption
                    new_cap_preview = new_caption[:50] + '...' if len(new_caption) > 50 else new_caption
                    log_details += f" | Подпись изменена: '{old_cap_preview}' → '{new_cap_preview}'"
                
                # Логируем
                log_info(log_details)
                
                # Выводим информацию в консоль
                print(f"\n{'='*60}")
                print(f"{now_str} - СООБЩЕНИЕ ОТРЕДАКТИРОВАНО")
                print(f"ID сообщения: {event.id}")
                print(f"Отправлено: {old_date} {old_time}")
                print(f"Отредактировано: {now_str}")
                
                # Показываем изменение типа
                if type_changed:
                    print(f"Тип сообщения: {old_type} → {new_msg_type}")
                else:
                    print(f"Тип сообщения: {new_msg_type}")
                
                # Определяем, изменилось ли медиа
                if media_changed:
                    print(f"\nМЕДИА ИЗМЕНЕНО")
                    print(f"Было: {old_type}")
                    print(f"Стало: {new_msg_type}")
                
                # Показываем изменения содержания текста
                if content_changed:
                    print(f"\nТЕКСТ ИЗМЕНЕН:")
                    print(f"Было: {old_content[:100]}{'...' if len(old_content) > 100 else ''}")
                    print(f"Стало: {new_content[:100]}{'...' if len(new_content) > 100 else ''}")
                elif new_content and not media_changed:  # Если текст есть, но не изменился
                    print(f"\nТекст: {new_content[:100]}{'...' if len(new_content) > 100 else ''}")
                
                # Показываем изменения подписи
                if caption_changed:
                    print(f"\nПОДПИСЬ ИЗМЕНЕНА:")
                    print(f"Было: {old_caption[:100]}{'...' if len(old_caption) > 100 else ''}")
                    print(f"Стало: {new_caption[:100]}{'...' if len(new_caption) > 100 else ''}")
                elif new_caption:  # Если подпись есть, но не изменилась
                    print(f"\nПодпись: {new_caption[:100]}{'...' if len(new_caption) > 100 else ''}")
                
                # Если изменилось только медиа, но не подпись
                if media_changed and not caption_changed and new_caption:
                    print(f"\nПодпись (не изменилась): {new_caption[:100]}{'...' if len(new_caption) > 100 else ''}")
                
                # Если ничего не изменилось, кроме времени редактирования
                if not content_changed and not caption_changed and not media_changed:
                    print(f"Изменений в содержании не обнаружено")
                
                print(f"{'='*60}")
                
            else:
                # Если сообщения не было в истории - добавляем его
                message_history[event.id] = {
                    'type': new_msg_type,
                    'content': new_content,
                    'caption': new_caption,
                    'timestamp': datetime.now(),
                    'sender_id': event.sender_id
                }
                
                # Формируем информацию для лога
                log_details = f"СООБЩЕНИЕ ОТРЕДАКТИРОВАНО | ID: {event.id} | "
                log_details += f"Тип: {new_msg_type}"
                
                if new_content:
                    preview = new_content[:50] + '...' if len(new_content) > 50 else new_content
                    log_details += f" | Текст: '{preview}'"
                
                if new_caption:
                    caption_preview = new_caption[:50] + '...' if len(new_caption) > 50 else new_caption
                    log_details += f" | Подпись: '{caption_preview}'"
                
                # Логируем
                log_info(log_details)
                
                # Выводим информацию в консоль
                print(f"\n{'='*60}")
                print(f"{now_str} - СООБЩЕНИЕ ОТРЕДАКТИРОВАНО")
                print(f"ID: {event.id} | Тип: {new_msg_type}")
                
                if new_content:
                    print(f"Текст: {new_content[:100]}{'...' if len(new_content) > 100 else ''}")
                
                if new_caption:
                    print(f"Подпись: {new_caption[:100]}{'...' if len(new_caption) > 100 else ''}")
                
                print(f"{'='*60}")
                
    except Exception as e:
        log_error(f"Ошибка в message_edited_handler: {e}")

async def main():
    try:
        log_info("=" * 60)
        log_info("ТЕЛЕГРАМ МОНИТОРИНГ ЗАПУЩЕН")
        log_info("=" * 60)
        
        print(f"\n{'='*60}")
        print(f"ТЕЛЕГРАМ МОНИТОРИНГ ЗАПУСКАЕТСЯ...")
        print(f"{'='*60}")
        
        await client.start()
        
        user = await client.get_entity(target_user_id)
        
        user_info = {
            'id': user.id,
            'first_name': user.first_name,
            'last_name': user.last_name,
            'username': user.username
        }
        log_info(f"Целевой пользователь: {user_info}")
        
        print(f"\nТЕЛЕГРАМ МОНИТОРИНГ ЗАПУЩЕН")
        print(f"- Логи сохраняются в: {log_filename}")
        print(f"- Целевой пользователь:")
        print(f"• ID: {user.id}")
        print(f"• Имя в контактах: {user.first_name} {user.last_name or ''}")
        print(f"• Username: @{user.username or 'нет'}")
        print(f"{'='*60}")
        print(f"\n- МОНИТОРИНГ:")
        print(f"• Статус онлайн/офлайн.")
        print(f"• При каждом уведомлении об офлайне, сообщает сколько по времени цель находилась онлайн (бывают неточности).")
        print(f"• События, которые совершает пользователь (могут быть погрешности).")
        print(f"• Входящие, отредактированные и удалённые сообщения.")
        print(f"• Изменение username, аватарки, био, имени и фамилии в контактах (каждые 30 секунд).")
        print(f"\n- РАСПОЗНАВАНИЕ СООБЩЕНИЙ:")
        print(f"• Текстовые и голосовые сообщения, кружки.")
        print(f"• Фото, видео, GIF, стикеры.")
        print(f"• Геолокации, контакты, файлы.")
        print(f"• Опросы и прочее.")
        print(f"{'='*60}")
        print("\nОжидание событий... (Ctrl+C для выхода)\n")
        
        await check_profile_once()
        
        async def periodic_profile_check():
            while True:
                await asyncio.sleep(30)
                await check_profile_once()
        
        profile_task = asyncio.create_task(periodic_profile_check())
        await client.run_until_disconnected()
        
    except Exception as e:
        log_error(f"Ошибка в main: {e}")
        print(f"\nКритическая ошибка при запуске: {e}")
        input("\nНажмите Enter для выхода...")

if __name__ == '__main__':
    try:
        with client:
            client.loop.run_until_complete(main())
    except KeyboardInterrupt:
        log_info("=" * 60)
        log_info("МОНИТОРИНГ ОСТАНОВЛЕН ПОЛЬЗОВАТЕЛЕМ")
        log_info("=" * 60)
        
        print(f"\n\n{'='*60}")
        print("МОНИТОРИНГ ОСТАНОВЛЕН")
        print(f"Логи сохранены в: {log_filename}")
        print("=" * 60)
    except Exception as e:
        log_error(f"Критическая ошибка: {e}")
        print(f"\nНеобработанная ошибка: {e}")
        input("\nНажмите Enter для выхода...")
