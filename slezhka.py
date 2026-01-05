import asyncio
import os
import sys
import time
import logging
from datetime import datetime
from telethon import TelegramClient, events
from telethon.tl.types import UserStatusOnline, UserStatusOffline
from telethon.tl.functions.users import GetFullUserRequest
from telethon.tl.types import InputUser
from dotenv import load_dotenv

load_dotenv()

api_id = int(os.getenv('API_ID'))
api_hash = os.getenv('API_HASH')
target_user_id = int(os.getenv('TARGET_USER_ID'))

# Создаем папку для логов если её нет
log_dir = "logs"
if not os.path.exists(log_dir):
    os.makedirs(log_dir)

# Настройка логгера только для файла
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

def log_info(message):
    """Логирование информационных сообщений только в файл"""
    logger.info(message)

def log_warning(message):
    """Логирование предупреждений только в файл"""
    logger.warning(message)

def log_error(message):
    """Логирование ошибок только в файл"""
    logger.error(message)

def log_profile_change(change_type, details):
    """Логирование изменений в профиле"""
    message = f"ПРОФИЛЬ: {change_type} - {details}"
    logger.info(message)

async def check_profile_once():
    """Однократная проверка профиля"""
    global last_profile, profile_check_counter, last_photo_id
    
    try:
        profile_check_counter += 1
        now_time = datetime.now().strftime('%H:%M:%S')
        log_info(f"Проверка профиля #{profile_check_counter} в {now_time}")
        
        # Получаем пользователя
        user = await client.get_entity(target_user_id)
        
        # Получаем био
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
                    log_info(f"Био получено ({len(bio)} символов)")
                else:
                    log_warning("Нет поля 'about' в ответе")
            else:
                log_warning("Нет access_hash у пользователя")
                
        except Exception as e:
            error_msg = f"Ошибка GetFullUserRequest: {type(e).__name__}: {str(e)[:80]}"
            log_warning(error_msg)
        
        # Получаем id фото
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
        
        # Если это первая проверка
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
            
            # Только факт проверки, без деталей
            print(f"\n[#{profile_check_counter} {now_time}] Начальная проверка профиля")
            return
        
        # Проверяем изменения
        changed = False
        
        # Проверяем имя
        if last_profile.get('first_name') != current['first_name']:
            changed = True
            details = f"Было: '{last_profile['first_name'] or 'пусто'}'; Стало: '{current['first_name'] or 'пусто'}'"
            log_profile_change("Имя изменено (в контактах)", details)
            print(f"\n[#{profile_check_counter} {now_time}] ▪ ИМЯ ИЗМЕНЕНО (в контактах)")
            print(f"   Было: '{last_profile['first_name'] or 'пусто'}'")
            print(f"   Стало: '{current['first_name'] or 'пусто'}'")
        
        # Проверяем фамилию
        if last_profile.get('last_name') != current['last_name']:
            changed = True
            details = f"Было: '{last_profile['last_name'] or 'пусто'}'; Стало: '{current['last_name'] or 'пусто'}'"
            log_profile_change("Фамилия изменена (в контактах)", details)
            print(f"\n[#{profile_check_counter} {now_time}] ▪ ФАМИЛИЯ ИЗМЕНЕНА (в контактах)")
            print(f"   Было: '{last_profile['last_name'] or 'пусто'}'")
            print(f"   Стало: '{current['last_name'] or 'пусто'}'")
        
        # Проверяем username
        if last_profile.get('username') != current['username']:
            changed = True
            details = f"Было: @{last_profile['username'] or 'нет'}; Стало: @{current['username'] or 'нет'}"
            log_profile_change("Username изменен", details)
            print(f"\n[#{profile_check_counter} {now_time}] ▪ USERNAME ИЗМЕНЕН")
            print(f"   Было: @{last_profile['username'] or 'нет'}")
            print(f"   Стало: @{current['username'] or 'нет'}")
        
        # Проверяем bio
        if bio_success and last_profile.get('bio_success'):
            old_bio = last_profile.get('bio', '')
            new_bio = current['bio']
            
            if old_bio != new_bio:
                changed = True
                old_preview = old_bio[:60] + '...' if len(old_bio) > 60 else old_bio or 'пусто'
                new_preview = new_bio[:60] + '...' if len(new_bio) > 60 else new_bio or 'пусто'
                details = f"Было: {old_preview}; Стало: {new_preview}"
                log_profile_change("Био изменено", details)
                print(f"\n[#{profile_check_counter} {now_time}] ▪ БИО ИЗМЕНЕНО")
                print(f"   Было: {old_preview}")
                print(f"   Стало: {new_preview}")
        
        elif not bio_success and last_profile.get('bio_success'):
            log_info("Био стало недоступно (возможно изменены настройки приватности)")
            print(f"\n[#{profile_check_counter} {now_time}] ▪ Био стало недоступно")
            changed = True
        elif bio_success and not last_profile.get('bio_success'):
            log_info("▪ Био стало доступно")
            print(f"\n[#{profile_check_counter} {now_time}] ▪ Био стало доступно")
            changed = True
        
        # Проверяем фото
        photo_changed = False
        
        if last_profile.get('has_photo') != current['has_photo']:
            changed = True
            photo_changed = True
            if current['has_photo']:
                log_profile_change("Аватарка добавлена", "")
                print(f"\n[#{profile_check_counter} {now_time}] ▪ АВАТАРКА ДОБАВЛЕНА")
            else:
                log_profile_change("Аватарка удалена", "")
                print(f"\n[#{profile_check_counter} {now_time}] ▪ АВАТАРКА УДАЛЕНА")
        
        elif (current_photo_id and last_photo_id and 
              current_photo_id != last_photo_id):
            changed = True
            photo_changed = True
            details = f"Старый ID: {last_photo_id}; Новый ID: {current_photo_id}"
            log_profile_change("Аватарка изменена (новое фото)", details)
            print(f"\n[#{profile_check_counter} {now_time}] ▪ АВАТАРКА ИЗМЕНЕНА")
        
        if photo_changed:
            last_photo_id = current_photo_id
        
        if changed:
            log_info("Обнаружены изменения в профиле")
        else:
            log_info("Изменений в профиле нет")
            # Можете закомментировать следующую строку, если не хотите видеть "Изменений нет"
            print(f"\n[#{profile_check_counter} {now_time}] Изменений в профиле нет")
        
        # Обновляем последний профиль
        last_profile = current
        
    except Exception as e:
        error_msg = f"Ошибка проверки профиля: {type(e).__name__}: {str(e)[:100]}"
        log_error(error_msg)
        print(f"\n[#{profile_check_counter}] ▲ Ошибка: {error_msg}")

@client.on(events.UserUpdate)
async def status_handler(event):
    """Обработчик статуса (рабочий код - НЕ МЕНЯТЬ)"""
    global session_start_time, last_status_time
    
    if event.user_id != target_user_id:
        return
    
    current_time = time.time()
    if current_time - last_status_time < 1:
        return
    
    last_status_time = current_time
    now_str = datetime.now().strftime('%H:%M:%S')
    
    if isinstance(event.status, UserStatusOnline):
        log_info(f"ПОЛЬЗОВАТЕЛЬ В ОНЛАЙНЕ в {now_str}")
        
        print(f"\n{'⬛'*25}")
        print(f"▪ {now_str} - ПОЛЬЗОВАТЕЛЬ В ОНЛАЙНЕ")
        session_start_time = time.time()
        print(f"{'⬛'*25}")
    
    elif isinstance(event.status, UserStatusOffline):
        if session_start_time:
            duration = int(time.time() - session_start_time)
            log_info(f"ПОЛЬЗОВАТЕЛЬ В ОФФЛАЙНЕ в {now_str}. Был онлайн: {duration//60} мин {duration%60} сек")
        else:
            log_info(f"ПОЛЬЗОВАТЕЛЬ В ОФФЛАЙНЕ в {now_str}")
        
        print(f"\n{'⬛'*25}")
        print(f"▪ {now_str} - ПОЛЬЗОВАТЕЛЬ В ОФФЛАЙНЕ")
        
        if session_start_time:
            duration = int(time.time() - session_start_time)
            print(f"▪ Был онлайн: {duration//60} мин {duration%60} сек")
            session_start_time = None
        
        print(f"{'⬛'*25}")

async def main():
    log_info("=" * 60)
    log_info("ТЕЛЕГРАМ МОНИТОР ЗАПУЩЕН")
    log_info(f"Логи сохраняются в: {log_filename}")
    log_info("=" * 60)
    
    await client.start()
    
    user = await client.get_entity(target_user_id)
    
    user_info = {
        'id': user.id,
        'first_name': user.first_name,
        'last_name': user.last_name,
        'username': user.username
    }
    log_info(f"Целевой пользователь: {user_info}")
    
    print(f"\n{'='*60}")
    print(f"◆ СЛЕЖКА ЗАПУЩЕНА")
    print(f"▪ Логи сохраняются в: {log_filename}")
    print(f"▪ Целевой пользователь:")
    print(f"▪ ID: {user.id}")
    print(f"▪ Имя в контактах: {user.first_name} {user.last_name or ''}")
    print(f"▪ Username: @{user.username or 'нет'}")
    print(f"{'='*60}")
    print(f"\n◆ МОНИТОРИНГ:")
    print(f"   • Статус онлайн/офлайн - мгновенно")
    print(f"   • Username - каждые 30 секунд")
    print(f"   • Аватарка - каждые 30 секунд")
    print(f"   • Bio - каждые 30 секунд")
    print(f"   • Имя/фамилия в контактах - каждые 30 секунд")
    print(f"{'='*60}")
    print("\n◆ Ожидание событий... (Ctrl+C для выхода)\n")
    
    # Первая проверка профиля
    await check_profile_once()
    
    # Запускаем периодическую проверку
    async def periodic_profile_check():
        while True:
            await asyncio.sleep(30)
            await check_profile_once()
    
    profile_task = asyncio.create_task(periodic_profile_check())
    await client.run_until_disconnected()

if __name__ == '__main__':
    try:
        with client:
            client.loop.run_until_complete(main())
    except KeyboardInterrupt:
        log_info("=" * 60)
        log_info("МОНИТОРИНГ ОСТАНОВЛЕН ПОЛЬЗОВАТЕЛЕМ")
        log_info("=" * 60)
        
        print(f"\n\n{'='*60}")
        print("◆ МОНИТОРИНГ ОСТАНОВЛЕН")
        print(f"▪ Логи сохранены в: {log_filename}")
        print("=" * 60)
    except Exception as e:
        log_error(f"Критическая ошибка: {e}")
        print(f"\n▲ Ошибка: {e}")