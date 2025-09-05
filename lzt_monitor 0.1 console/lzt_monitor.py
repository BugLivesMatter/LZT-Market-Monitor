
# Конфигурация
itemID = 0
TELEGRAM_BOT_TOKEN = '8283395405:AAFNyzWlWsNyUm-_peZZpuDTZMBAGOWgXhs'
TELEGRAM_CHAT_ID = 5952347965
CHECK_INTERVAL = 60  # Интервал проверки в секундах

URL = 'https://lzt.market/user/6140497/items?pmin=100&pmax=600&title=EU&show=active&order_by=pdate_to_down_upload&published_startDate=2017-09-05T03%3A00%3A00%2B03%3A00&published_endDate=2025-07-28T02%3A59%3A59%2B03%3A00'
#URL = 'https://lzt.market/user/6140497/items?show=active&order_by=pdate_to_down_upload&published_startDate=2017-09-05T03%3A00%3A00%2B03%3A00&published_endDate=2025-07-28T02%3A59%3A59%2B03%3A00'
#URL = 'https://lzt.market/riot?pmax=600&tel=no&valorant_region[]=EU&inv_min=3000&order_by=pdate_to_down_upload'
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from bs4 import BeautifulSoup
import time
import os
import json
import requests
from datetime import datetime
import traceback
import re
import threading
import certifi
import urllib3

# Настройки Selenium
SELENIUM_HEADLESS = True
USER_AGENT = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
DATA_FILE = 'last_item.json'

# Глобальные переменные для управления ботом
latest_item = None
bot_active = True


def init_driver():
    options = Options()
    options.add_argument('--disable-gpu')
    options.add_argument('--disable-software-rasterizer')
    if SELENIUM_HEADLESS:
        options.add_argument('--headless=new')
    options.add_argument('--no-sandbox')
    options.add_argument('--disable-dev-shm-usage')
    options.add_argument(f'user-agent={USER_AGENT}')
    options.add_argument('--disable-blink-features=AutomationControlled')
    options.add_argument('--window-size=1920,1080')
    driver = webdriver.Chrome(options=options)
    return driver


def send_telegram_message(chat_id, message, reply_markup=None):
    url = f'https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage'
    payload = {
        'chat_id': chat_id,
        'text': message,
        'parse_mode': 'HTML',
        'disable_web_page_preview': True
    }
    if reply_markup:
        payload['reply_markup'] = reply_markup

    try:
        response = requests.post(url, json=payload)
        response.raise_for_status()
        return True
    except Exception as e:
        print(f"Ошибка отправки в Telegram: {e}")
        return False


def load_last_item():
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except:
            return None
    return None


def save_last_item(item):
    with open(DATA_FILE, 'w', encoding='utf-8') as f:
        json.dump(item, f, ensure_ascii=False, indent=2)


def get_page_html(driver, url=URL):
    try:
        driver.get(url)
        WebDriverWait(driver, 20).until(
            EC.presence_of_element_located((By.CLASS_NAME, 'marketIndex--itemsContainer')))
        driver.execute_script("window.scrollTo(0, 500);")
        time.sleep(1)
        return driver.page_source
    except Exception as e:
        print(f"Ошибка загрузки страницы: {e}")
        return None


def parse_second_item(html, itemid):
    """Парсинг товара с надежным поиском контейнера характеристик"""
    if not html:
        return None

    soup = BeautifulSoup(html, 'html.parser')
    container = soup.find('div', class_='marketIndex--itemsContainer')
    if not container:
        print("Контейнер с товарами не найден!")
        return None

    items = container.find_all('div', class_='marketIndexItem')
    if len(items) < 2:
        print("Менее 2 товаров на странице!")
        return None

    item = items[itemid]  # Второй товар (первый неспонсорский)

    try:
        # Извлечение базовой информации (остается без изменений)
        item_id = item.get('id', '').replace('marketItem--', '')
        if not item_id:
            return None

        title_elem = item.find('a', class_='marketIndexItem--Title')
        title = title_elem.text.strip() if title_elem else "Без названия"

        price_elem = item.find('span', class_='Value')
        price = price_elem.text.strip() if price_elem else "Цена не указана"

        relative_link = title_elem.get('href', '') if title_elem else ''
        link = f'https://lzt.market/{relative_link}' if relative_link else ''

        seller_elem = item.find('a', class_='username')
        seller = seller_elem.text.strip() if seller_elem else "Продавец не указан"

        time_elem = item.find('span', class_='muted')
        time_text = time_elem.text.strip() if time_elem else "Время не указано"

        # Извлекаем статусы
        statuses = []
        status_container = item.find('div', class_='marketIndexItem--Badges stats')
        if status_container:
            for status in status_container.find_all('span', class_='stat'):
                statuses.append(status.text.strip())

        # НАДЕЖНЫЙ ПОИСК КОНТЕЙНЕРА ХАРАКТЕРИСТИК
        all_badges = []
        badges_container = None

        # Поиск по содержимому - ищем контейнер, содержащий элементы с классом marketIndexItem-Badge
        for container in item.find_all('div', class_='marketIndexItem--Badges'):
            if container.find('div', class_='marketIndexItem-Badge'):
                badges_container = container
                break

        # Если не нашли, попробуем найти по структуре
        if not badges_container:
            # Ищем все контейнеры marketIndexItem--Badges
            all_containers = item.find_all('div', class_='marketIndexItem--Badges')
            # Если их больше одного, берем последний (обычно характеристики идут после статусов)
            if len(all_containers) > 1:
                badges_container = all_containers[-1]
            elif len(all_containers) == 1:
                # Проверим, содержит ли он характеристики
                if not all_containers[0].find('div', class_='marketIndexItem-Badge'):
                    badges_container = None

        # Если нашли контейнер, парсим характеристики
        if badges_container:
            print(f"Найден контейнер характеристик: {badges_container}")

            # Собираем все элементы marketIndexItem-Badge внутри контейнера
            for badge in badges_container.find_all('div', class_='marketIndexItem-Badge'):
                # Для элементов с иконками игр
                if 'iconGameWithBadge' in badge.get('class', []):
                    game_name = badge.get('data-cachedtitle', '')
                    count = badge.get_text(strip=True)
                    if game_name and count:
                        all_badges.append(f"{game_name} ({count})")
                    elif game_name:
                        all_badges.append(game_name)
                    elif count:
                        all_badges.append(count)
                # Для обычных бейджей
                else:
                    badge_text = badge.get_text(strip=True)
                    if badge_text:
                        all_badges.append(badge_text)
        else:
            print("Контейнер характеристик не найден!")

        return {
            'id': item_id,
            'title': title,
            'price': price,
            'link': link,
            'seller': seller,
            'time': time_text,
            'all_badges': all_badges,
            'statuses': statuses
        }
    except Exception as e:
        print(f"Ошибка парсинга товара: {e}")
        traceback.print_exc()

        # Сохраняем HTML товара для отладки
        with open(f'error_item_{itemid}.html', 'w', encoding='utf-8') as f:
            f.write(str(item))

        return None
def format_message(item):
    """Форматирование сообщения со всеми бейджами"""
    all_badges = item.get('all_badges', [])
    statuses = item.get('statuses', [])

    # Форматирование бейджей
    badges_text = "\n".join([f"• {badge}" for badge in all_badges]) if all_badges else "❌ Характеристики не указаны"

    # Форматирование статусов
    statuses_text = '\n'.join([f"• {status}" for status in statuses]) if statuses else "Нет статусов"

    # Кнопка "Посмотреть товар"
    keyboard = {
        "inline_keyboard": [[{
            "text": "🔗 Посмотреть товар",
            "url": item['link']
        }]]
    }

    return (
        f"🔥 <b>АКТУАЛЬНЫЙ ТОВАР НА LZT.MARKET</b>\n\n"
        f"🏷️ <b>Название:</b> {item['title']}\n"
        f"💰 <b>Цена:</b> {item['price']} RUB\n"
        f"👤 <b>Продавец:</b> {item['seller']}\n"
        f"⏱️ <b>Добавлено:</b> {item['time']}\n\n"
        f"📊 <b>Все характеристики:</b>\n{badges_text}\n\n"
        f"🛡️ <b>Статусы:</b>\n{statuses_text}"
    ), keyboard

def fetch_current_item(driver):
    """Получение текущего второго товара"""
    html = get_page_html(driver)
    return parse_second_item(html, itemID) if html else None


def bot_monitor(driver):
    """Мониторинг новых товаров в фоновом режиме"""
    global latest_item
    last_item = load_last_item()

    print(f"Мониторинг запущен. Отслеживаем второй товар...")

    try:
        while bot_active:
            try:
                current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                print(f"[{current_time}] Проверка нового товара...")

                current_item = fetch_current_item(driver)
                if current_item:
                    # Сохраняем текущий товар для команды /new
                    latest_item = current_item

                    if last_item is None:
                        print(f"Инициализация: товар #{current_item['id']}")
                        last_item = current_item
                        save_last_item(last_item)
                    elif current_item['id'] != last_item['id']:
                        print(f"Обнаружен новый товар! ID: {current_item['id']}")
                        message, keyboard = format_message(current_item)
                        if send_telegram_message(TELEGRAM_CHAT_ID, message, keyboard):
                            print(f"Уведомление отправлено: {current_item['title']}")
                            last_item = current_item
                            save_last_item(last_item)
                    else:
                        print("Изменений нет, тот же товар")
                else:
                    print("Не удалось распознать товар")

                print(f"Ожидание {CHECK_INTERVAL} секунд...")
                time.sleep(CHECK_INTERVAL)

            except Exception as e:
                print(f"Ошибка в основном цикле: {e}")
                traceback.print_exc()
                print("Повторная попытка через 10 секунд...")
                time.sleep(10)
    except KeyboardInterrupt:
        pass


def telegram_bot_listener():
    """Обработчик Telegram команд с исправленной обработкой SSL"""
    http = urllib3.PoolManager(cert_reqs='CERT_REQUIRED', ca_certs=certifi.where())
    offset = 0

    while bot_active:
        try:
            url = f'https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates'
            params = {'timeout': 30, 'offset': offset}
            response = http.request('GET', url, fields=params)
            data = json.loads(response.data.decode('utf-8'))
            updates = data.get('result', [])

            for update in updates:
                message = update.get('message', {})
                text = message.get('text', '').strip()
                chat_id = message.get('chat', {}).get('id')

                if text == '/new' and chat_id == TELEGRAM_CHAT_ID:
                    if latest_item:
                        message_text, keyboard = format_message(latest_item)
                        send_telegram_message(chat_id, message_text, keyboard)
                        print("Отправлен текущий товар по запросу /new")
                    else:
                        send_telegram_message(chat_id, "❌ Актуальный товар еще не загружен, попробуйте позже")

                offset = update['update_id'] + 1

        except Exception as e:
            print(f"Ошибка в обработчике Telegram: {e}")
            traceback.print_exc()
            time.sleep(5)


def main():
    global bot_active
    driver = init_driver()

    # Запускаем мониторинг товаров в основном потоке
    monitor_thread = threading.Thread(target=bot_monitor, args=(driver,))
    monitor_thread.daemon = True
    monitor_thread.start()

    # Запускаем обработчик Telegram команд в отдельном потоке
    bot_thread = threading.Thread(target=telegram_bot_listener)
    bot_thread.daemon = True
    bot_thread.start()

    try:
        # Бесконечный цикл для поддержания работы потоков
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nЗавершение работы...")
        bot_active = False
        monitor_thread.join(timeout=5)
        bot_thread.join(timeout=5)
        driver.quit()
        print("Браузер закрыт, потоки остановлены")


if __name__ == '__main__':
    main()