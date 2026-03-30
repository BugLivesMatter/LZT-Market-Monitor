import sys
import os
import json
import time
import threading
import winreg
from datetime import datetime
from functools import partial
import tkinter as tk
from tkinter import ttk, scrolledtext

import requests
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from bs4 import BeautifulSoup

try:
    import pystray
    from PIL import Image, ImageDraw, ImageFont

    _TRAY_AVAILABLE = True
except ImportError:
    _TRAY_AVAILABLE = False

# Глобальные переменные
CONFIG_FILE = "config.json"
DEFAULT_CONFIG = {
    "itemID": 0,
    "telegram_bot_token": "",
    "telegram_chat_id": "",
    "check_interval": 60,
    "url": "",
    "autostart": False,
    "headless": True
}

# Цветовая схема
BACKGROUND_COLOR = "#1e1e1e"
PRIMARY_COLOR = "#00ffa3"
SECONDARY_COLOR = "#228e5d"
TEXT_COLOR = "#e0e0e0"
HIGHLIGHT_COLOR = "#2c2c2c"


def _app_dir():
    return os.path.dirname(os.path.abspath(sys.argv[0]))


def _config_path():
    return os.path.join(_app_dir(), CONFIG_FILE)


class MonitorWorker:
    def __init__(self, config, callbacks):
        self.config = config
        self.cb = callbacks
        self.bot_active = False
        self.driver = None
        self.monitor_event = threading.Event()

    def start_monitoring(self):
        if self.bot_active:
            self.cb["log"]("Мониторинг уже запущен")
            return

        self.bot_active = True
        self.cb["log"]("Запуск мониторинга...")

        try:
            options = Options()
            options.add_argument('--disable-gpu')
            options.add_argument('--disable-software-rasterizer')
            options.add_argument('--log-level=3')
            options.add_argument('--disable-dev-shm-usage')
            options.add_argument('--no-sandbox')
            if self.config.get('headless', True):
                options.add_argument('--headless=new')
            options.add_argument(
                f'user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36')
            options.add_argument('--disable-blink-features=AutomationControlled')
            options.add_argument('--window-size=1920,1080')

            self.driver = webdriver.Chrome(options=options)
            self.cb["log"]("Браузер инициализирован")
        except Exception as e:
            self.cb["log"](f"Ошибка инициализации браузера: {str(e)}")
            self.stop_monitoring()
            return

        self.monitor_thread = threading.Thread(target=self.run_monitoring, daemon=True)
        self.monitor_thread.start()

    def stop_monitoring(self):
        if not self.bot_active:
            return

        self.bot_active = False
        self.cb["log"]("Остановка мониторинга...")

        if self.driver:
            try:
                self.driver.quit()
                self.cb["log"]("Браузер закрыт")
            except Exception:
                pass
            finally:
                self.driver = None

        self.cb["monitoring_stopped"]()

    def run_monitoring(self):
        last_item = None

        while self.bot_active:
            try:
                current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                self.cb["log"](f"Проверка нового товара...")
                self.cb["last_check"](f"Последняя проверка: {current_time}")

                current_item = self.fetch_current_item()
                if current_item:
                    self.cb["new_item"](current_item)

                    if last_item is None:
                        self.cb["log"](f"Инициализация: товар #{current_item['id']}")
                        last_item = current_item
                    elif current_item['id'] != last_item['id']:
                        self.cb["log"](f"Обнаружен новый товар! ID: {current_item['id']}")
                        last_item = current_item
                        self.send_telegram_notification(current_item)

                    else:
                        self.cb["log"]("Изменений нет, тот же товар")
                else:
                    self.cb["log"]("Не удалось распознать товар")

                self.cb["log"](f"Ожидание {self.config.get('check_interval', 60)} секунд...")
                self.monitor_event.wait(self.config.get('check_interval', 60))
                self.monitor_event.clear()

            except Exception as e:
                self.cb["log"](f"Ошибка в мониторинге: {str(e)}")
                time.sleep(10)

    def get_page_html(self):
        try:
            url = self.config.get('url', '')
            if not url:
                self.cb["log"]("URL не указан в настройках")
                return None

            self.driver.get(url)
            WebDriverWait(self.driver, 20).until(
                EC.presence_of_element_located((By.CLASS_NAME, 'marketIndex--itemsContainer')))
            self.driver.execute_script("window.scrollTo(0, 500);")
            time.sleep(1)
            return self.driver.page_source
        except Exception as e:
            self.cb["log"](f"Ошибка загрузки страницы: {str(e)}")
            return None

    def parse_second_item(self, html, itemid):
        if not html:
            return None

        soup = BeautifulSoup(html, 'html.parser')
        container = soup.find('div', class_='marketIndex--itemsContainer')
        if not container:
            self.cb["log"]("Контейнер с товарами не найден!")
            return None

        items = container.find_all('div', class_='marketIndexItem PopupItemLink')

        if len(items) < itemid + 1:
            self.cb["log"](f"Недостаточно товаров на странице! Найдено: {len(items)}, требуется: {itemid + 1}")
            return None

        item = items[itemid]

        try:
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

            statuses = []
            status_container = item.find('div', class_='marketIndexItem--Badges stats')
            if status_container:
                for status in status_container.find_all('span', class_='stat'):
                    statuses.append(status.text.strip())

            all_badges = []
            badges_container = None

            for c in item.find_all('div', class_='marketIndexItem--Badges'):
                if c.find('div', class_='marketIndexItem-Badge'):
                    badges_container = c
                    break

            if not badges_container:
                all_containers = item.find_all('div', class_='marketIndexItem--Badges')
                if len(all_containers) > 1:
                    badges_container = all_containers[-1]

            if badges_container:
                for badge in badges_container.find_all('div', class_='marketIndexItem-Badge'):
                    if 'iconGameWithBadge' in badge.get('class', []):
                        game_name = badge.get('data-cachedtitle', '')
                        count = badge.get_text(strip=True)
                        if game_name and count:
                            all_badges.append(f"{game_name} ({count})")
                        elif game_name:
                            all_badges.append(game_name)
                        elif count:
                            all_badges.append(count)
                    else:
                        badge_text = badge.get_text(strip=True)
                        if badge_text:
                            all_badges.append(badge_text)

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
            self.cb["log"](f"Ошибка парсинга товара: {str(e)}")
            return None

    def fetch_current_item(self):
        html = self.get_page_html()
        return self.parse_second_item(html, self.config.get('itemID', 0)) if html else None

    def format_telegram_message(self, item):
        all_badges = item.get('all_badges', [])
        statuses = item.get('statuses', [])

        badges_text = "\n".join([f"• {badge}" for badge in all_badges]) if all_badges else "❌ Характеристики не указаны"
        statuses_text = '\n'.join([f"• {status}" for status in statuses]) if statuses else "Нет статусов"

        return (
            f"🔥 <b>АКТУАЛЬНЫЙ ТОВАР НА LZT.MARKET</b>\n\n"
            f"🏷️ <b>Название:</b> {item['title']}\n"
            f"💰 <b>Цена:</b> {item['price']} RUB\n"
            f"👤 <b>Продавец:</b> {item['seller']}\n"
            f"⏱️ <b>Добавлено:</b> {item['time']}\n\n"
            f"📊 <b>Все характеристики:</b>\n{badges_text}\n\n"
            f"🛡️ <b>Статусы:</b>\n{statuses_text}"
        )

    def send_telegram_notification(self, item):
        message = self.format_telegram_message(item)
        keyboard = {
            "inline_keyboard": [[{
                "text": "🔗 Посмотреть товар",
                "url": item['link']
            }]]
        }

        self.send_telegram_message(self.config.get('telegram_chat_id', ''), message, keyboard)

    def send_telegram_message(self, chat_id, message, reply_markup=None):
        token = self.config.get('telegram_bot_token', '')
        if not token or not chat_id:
            self.cb["log"]("Не настроен Telegram бот")
            return False

        url = f'https://api.telegram.org/bot{token}/sendMessage'
        payload = {
            'chat_id': chat_id,
            'text': message,
            'parse_mode': 'HTML',
            'disable_web_page_preview': True
        }

        if reply_markup:
            payload['reply_markup'] = json.dumps(reply_markup)

        try:
            response = requests.post(url, json=payload)
            response.raise_for_status()
            self.cb["log"]("Уведомление отправлено в Telegram")
            return True
        except Exception as e:
            self.cb["log"](f"Ошибка отправки в Telegram: {str(e)}")
            return False


def _tray_image_pil():
    ico = os.path.join(_app_dir(), "icon.ico")
    if os.path.isfile(ico):
        try:
            im = Image.open(ico)
            if im.mode != "RGBA":
                im = im.convert("RGBA")
            return im
        except Exception:
            pass
    im = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.ellipse((8, 8, 56, 56), fill="#00ffa3")
    try:
        font = ImageFont.truetype("arial.ttf", 28)
    except OSError:
        font = ImageFont.load_default()
    d.text((22, 16), "L", fill="#1e1e1e", font=font)
    return im


class LZTMonitor(tk.Tk):
    def __init__(self):
        super().__init__()

        self.config_data = self.load_config()
        self.latest_item = None
        self.monitor_worker = None
        self.tray_icon = None
        self._unmap_guard = False
        self._startup_tray_hide = False

        self.title("LZT Market Monitor")
        self.geometry("900x700")
        self.minsize(640, 480)

        ico = os.path.join(_app_dir(), "icon.ico")
        if os.path.isfile(ico):
            try:
                self.iconbitmap(ico)
            except tk.TclError:
                pass

        self._apply_theme()
        self._build_ui()
        self.load_config_to_ui()

        self.protocol("WM_DELETE_WINDOW", self.close_app)
        self.bind("<Unmap>", self._on_unmap)

        self._init_tray()
        self.check_autostart()

    def _apply_theme(self):
        self.configure(bg=BACKGROUND_COLOR)
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass

        style.configure(".", background=BACKGROUND_COLOR, foreground=TEXT_COLOR)
        style.configure("TFrame", background=BACKGROUND_COLOR)
        style.configure("TLabel", background=BACKGROUND_COLOR, foreground=TEXT_COLOR)
        style.configure("TLabelframe", background=BACKGROUND_COLOR, foreground=PRIMARY_COLOR)
        style.configure("TLabelframe.Label", background=BACKGROUND_COLOR, foreground=PRIMARY_COLOR)
        style.configure("TNotebook", background=BACKGROUND_COLOR, borderwidth=0)
        style.configure("TNotebook.Tab", background=HIGHLIGHT_COLOR, foreground=TEXT_COLOR, padding=(16, 8))
        style.map("TNotebook.Tab",
                  background=[("selected", SECONDARY_COLOR)],
                  foreground=[("selected", PRIMARY_COLOR)])
        style.configure("TButton", background=SECONDARY_COLOR, foreground="#ffffff")
        style.map("TButton",
                  background=[("active", PRIMARY_COLOR), ("disabled", "#3a3a3a")],
                  foreground=[("disabled", "#7a7a7a")])
        style.configure("TCheckbutton", background=BACKGROUND_COLOR, foreground=TEXT_COLOR)
        style.configure("TSpinbox", fieldbackground=HIGHLIGHT_COLOR, foreground=TEXT_COLOR)

        self.option_add("*Entry.background", HIGHLIGHT_COLOR)
        self.option_add("*Entry.foreground", TEXT_COLOR)
        self.option_add("*Entry.insertBackground", TEXT_COLOR)

    def _entry(self, parent, **kw):
        e = tk.Entry(parent, bg=HIGHLIGHT_COLOR, fg=TEXT_COLOR, insertbackground=TEXT_COLOR,
                     relief=tk.FLAT, highlightthickness=1, highlightbackground="#3a3a3a",
                     highlightcolor=PRIMARY_COLOR, **kw)
        return e

    def _build_ui(self):
        nb = ttk.Notebook(self)
        nb.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)

        self.monitor_tab = ttk.Frame(nb)
        nb.add(self.monitor_tab, text="Мониторинг")
        self._setup_monitor_tab()

        self.settings_tab = ttk.Frame(nb)
        nb.add(self.settings_tab, text="Настройки")
        self._setup_settings_tab()

        self.log_tab = ttk.Frame(nb)
        nb.add(self.log_tab, text="Журнал")
        self._setup_log_tab()

        status_fr = ttk.Frame(self)
        status_fr.pack(fill=tk.X, side=tk.BOTTOM, padx=12, pady=(0, 8))
        self.status_label = ttk.Label(status_fr, text="Готов к работе", foreground=PRIMARY_COLOR)
        self.status_label.pack(side=tk.LEFT)

    def _setup_monitor_tab(self):
        outer = ttk.Frame(self.monitor_tab)
        outer.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)

        sg = ttk.LabelFrame(outer, text="Статус мониторинга")
        sg.pack(fill=tk.X, pady=(0, 10))

        self.status_text = ttk.Label(sg, text="Мониторинг не запущен", font=("", 11, "bold"),
                                    foreground=PRIMARY_COLOR)
        self.status_text.pack(anchor=tk.W, padx=12, pady=(8, 4))

        self.last_check_label = ttk.Label(sg, text="Последняя проверка: никогда")
        self.last_check_label.pack(anchor=tk.W, padx=12, pady=2)

        self.last_item_label = ttk.Label(sg, text="Последний товар: нет")
        self.last_item_label.pack(anchor=tk.W, padx=12, pady=(2, 10))

        ctrl = ttk.Frame(outer)
        ctrl.pack(fill=tk.X, pady=(0, 10))

        self.start_btn = ttk.Button(ctrl, text="Запустить мониторинг", command=self.start_monitoring)
        self.start_btn.pack(side=tk.LEFT, padx=(0, 8))

        self.stop_btn = ttk.Button(ctrl, text="Остановить мониторинг", command=self.stop_monitoring, state=tk.DISABLED)
        self.stop_btn.pack(side=tk.LEFT, padx=(0, 8))

        self.check_now_btn = ttk.Button(ctrl, text="Проверить сейчас", command=self.check_now)
        self.check_now_btn.pack(side=tk.LEFT)

        ttk.Separator(outer, orient=tk.HORIZONTAL).pack(fill=tk.X, pady=8)

        ig = ttk.LabelFrame(outer, text="Текущий товар")
        ig.pack(fill=tk.BOTH, expand=True)

        self.item_info = scrolledtext.ScrolledText(
            ig, height=12, wrap=tk.WORD, state=tk.DISABLED,
            bg=HIGHLIGHT_COLOR, fg=TEXT_COLOR, insertbackground=TEXT_COLOR,
            font=("Consolas", 10), relief=tk.FLAT, highlightthickness=1,
            highlightbackground="#3a3a3a")
        self.item_info.pack(fill=tk.BOTH, expand=True, padx=10, pady=(8, 4))

        bf = ttk.Frame(ig)
        bf.pack(fill=tk.X, padx=10, pady=(0, 10))
        self.send_test_btn = ttk.Button(bf, text="Отправить в Telegram", command=self.send_test_message)
        self.send_test_btn.pack(side=tk.LEFT)

    def _form_row(self, parent, label_text, widget):
        row = ttk.Frame(parent)
        row.pack(fill=tk.X, pady=4)
        ttk.Label(row, text=label_text, width=22, anchor=tk.W).pack(side=tk.LEFT, padx=(0, 8))
        widget.pack(side=tk.LEFT, fill=tk.X, expand=True)

    def _setup_settings_tab(self):
        outer = ttk.Frame(self.settings_tab)
        outer.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)

        mg = ttk.LabelFrame(outer, text="Настройки мониторинга")
        mg.pack(fill=tk.X, pady=(0, 10))
        mf = ttk.Frame(mg)
        mf.pack(fill=tk.X, padx=12, pady=10)

        self.url_edit = self._entry(mf)
        self._form_row(mf, "URL страницы:", self.url_edit)
        self.url_edit.insert(0, "")

        spin_fr = ttk.Frame(mf)
        self.item_id_spin = ttk.Spinbox(spin_fr, from_=0, to=20, width=8)
        self.item_id_spin.pack(side=tk.LEFT)
        ttk.Label(spin_fr, text=" (позиция на странице)").pack(side=tk.LEFT)
        self._form_row(mf, "ID товара (позиция):", spin_fr)

        int_fr = ttk.Frame(mf)
        self.interval_spin = ttk.Spinbox(int_fr, from_=10, to=3600, width=8)
        self.interval_spin.pack(side=tk.LEFT)
        ttk.Label(int_fr, text=" сек").pack(side=tk.LEFT)
        self._form_row(mf, "Интервал проверки:", int_fr)

        self.headless_check = ttk.Checkbutton(mf, text="Режим без интерфейса (Headless)")
        self.headless_check.pack(anchor=tk.W, pady=(8, 0))

        tg = ttk.LabelFrame(outer, text="Настройки Telegram")
        tg.pack(fill=tk.X, pady=(0, 10))
        tf = ttk.Frame(tg)
        tf.pack(fill=tk.X, padx=12, pady=10)

        self.telegram_token_edit = self._entry(tf)
        self._form_row(tf, "Токен бота:", self.telegram_token_edit)

        self.telegram_chat_edit = self._entry(tf)
        self._form_row(tf, "ID чата:", self.telegram_chat_edit)

        self.test_telegram_btn = ttk.Button(tf, text="Проверить подключение к Telegram", command=self.test_telegram)
        self.test_telegram_btn.pack(anchor=tk.W, pady=(8, 0))

        ag = ttk.LabelFrame(outer, text="Настройки приложения")
        ag.pack(fill=tk.X, pady=(0, 10))
        af = ttk.Frame(ag)
        af.pack(fill=tk.X, padx=12, pady=10)
        self.autostart_check = ttk.Checkbutton(af, text="Запускать при старте Windows")
        self.autostart_check.pack(anchor=tk.W)

        btn_row = ttk.Frame(outer)
        btn_row.pack(fill=tk.X, pady=(0, 8))
        self.save_btn = ttk.Button(btn_row, text="Сохранить настройки", command=self.save_settings)
        self.save_btn.pack(side=tk.LEFT, padx=(0, 8))
        self.default_btn = ttk.Button(btn_row, text="Сбросить настройки", command=self.reset_settings)
        self.default_btn.pack(side=tk.LEFT)

    def _setup_log_tab(self):
        outer = ttk.Frame(self.log_tab)
        outer.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)

        self.log_area = scrolledtext.ScrolledText(
            outer, wrap=tk.WORD, state=tk.NORMAL,
            bg=HIGHLIGHT_COLOR, fg=TEXT_COLOR, font=("Consolas", 10),
            relief=tk.FLAT, highlightthickness=1, highlightbackground="#3a3a3a")
        self.log_area.pack(fill=tk.BOTH, expand=True, pady=(0, 8))

        lr = ttk.Frame(outer)
        lr.pack(fill=tk.X)
        self.clear_log_btn = ttk.Button(lr, text="Очистить журнал", command=self.clear_log)
        self.clear_log_btn.pack(side=tk.LEFT, padx=(0, 8))
        self.save_log_btn = ttk.Button(lr, text="Сохранить журнал", command=self.save_log)
        self.save_log_btn.pack(side=tk.LEFT)

    def _init_tray(self):
        if not _TRAY_AVAILABLE:
            return

        def show_action(icon, item):
            self.after(0, self._show_from_tray)

        def quit_action(icon, item):
            self.after(0, self.close_app)

        menu = pystray.Menu(
            pystray.MenuItem("Открыть", show_action, default=True),
            pystray.MenuItem("Выход", quit_action),
        )
        self.tray_icon = pystray.Icon("lzt_monitor", _tray_image_pil(), "LZT Market Monitor", menu)
        self.tray_icon.run_detached()

    def _show_from_tray(self):
        self.deiconify()
        self.lift()
        self.focus_force()

    def _on_unmap(self, event):
        if event.widget != self:
            return
        if self._unmap_guard:
            return
        if self.state() != "iconic":
            return
        self._unmap_guard = True
        self.withdraw()
        self.after(150, lambda: setattr(self, "_unmap_guard", False))

        skip_balloon = self._startup_tray_hide
        self._startup_tray_hide = False

        if not skip_balloon and _TRAY_AVAILABLE and self.tray_icon:
            try:
                self.tray_icon.notify("Приложение свернуто в трей", "LZT Monitor")
            except Exception:
                pass
        elif not skip_balloon and not _TRAY_AVAILABLE:
            self.update_log("Свернуто. Установите pystray и Pillow для иконки в трее.")

    def _schedule(self, fn, *args, **kwargs):
        self.after(0, partial(fn, *args, **kwargs))

    def _worker_callbacks(self):
        def last_check_cb(t):
            self.after(0, lambda t=t: self.last_check_label.config(text=t))

        def last_item_cb(t):
            self.after(0, lambda t=t: self.last_item_label.config(text=t))

        return {
            "log": lambda m: self._schedule(self.update_log, m),
            "status": lambda m: self._schedule(self.update_status, m),
            "new_item": lambda item: self._schedule(self.handle_new_item, item),
            "last_check": last_check_cb,
            "last_item": last_item_cb,
            "monitoring_stopped": lambda: self._schedule(self.on_monitoring_stopped),
        }

    def update_log(self, message):
        timestamp = datetime.now().strftime("[%H:%M:%S]")
        self.log_area.insert(tk.END, f"{timestamp} {message}\n")
        self.log_area.see(tk.END)

    def update_status(self, message):
        self.status_label.config(text=message)

    def load_config(self):
        path = _config_path()
        if os.path.exists(path):
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception:
                return DEFAULT_CONFIG.copy()
        return DEFAULT_CONFIG.copy()

    def save_config(self):
        with open(_config_path(), 'w', encoding='utf-8') as f:
            json.dump(self.config_data, f, ensure_ascii=False, indent=2)
        self.update_log("Конфигурация сохранена")

    def load_config_to_ui(self):
        self.url_edit.delete(0, tk.END)
        self.url_edit.insert(0, self.config_data.get('url', ''))
        self.item_id_spin.delete(0, tk.END)
        self.item_id_spin.insert(0, str(self.config_data.get('itemID', 0)))
        self.interval_spin.delete(0, tk.END)
        self.interval_spin.insert(0, str(self.config_data.get('check_interval', 60)))
        self.telegram_token_edit.delete(0, tk.END)
        self.telegram_token_edit.insert(0, self.config_data.get('telegram_bot_token', ''))
        self.telegram_chat_edit.delete(0, tk.END)
        self.telegram_chat_edit.insert(0, self.config_data.get('telegram_chat_id', ''))
        if self.config_data.get('headless', True):
            self.headless_check.state(["selected"])
        else:
            self.headless_check.state(["!selected"])
        if self.config_data.get('autostart', False):
            self.autostart_check.state(["selected"])
        else:
            self.autostart_check.state(["!selected"])

    def _read_spin_int(self, spin, default):
        try:
            return int(spin.get())
        except ValueError:
            return default

    def save_settings(self):
        self.config_data['url'] = self.url_edit.get().strip()
        self.config_data['itemID'] = self._read_spin_int(self.item_id_spin, 0)
        self.config_data['check_interval'] = self._read_spin_int(self.interval_spin, 60)
        self.config_data['telegram_bot_token'] = self.telegram_token_edit.get().strip()
        self.config_data['telegram_chat_id'] = self.telegram_chat_edit.get().strip()
        self.config_data['headless'] = self.headless_check.instate(["selected"])
        self.config_data['autostart'] = self.autostart_check.instate(["selected"])

        self.save_config()
        self.update_autostart()
        self.update_log("Настройки сохранены")

        if self.monitor_worker and self.monitor_worker.bot_active:
            self.stop_monitoring()
            self.start_monitoring()

    def reset_settings(self):
        self.config_data = DEFAULT_CONFIG.copy()
        self.load_config_to_ui()
        self.save_config()
        self.update_log("Настройки сброшены до значений по умолчания")

    def update_autostart(self):
        autostart = self.config_data.get('autostart', False)
        app_name = "LZTMarketMonitor"
        app_path = f'"{os.path.abspath(sys.argv[0])}"'

        try:
            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Run",
                0, winreg.KEY_SET_VALUE
            )

            if autostart:
                winreg.SetValueEx(key, app_name, 0, winreg.REG_SZ, app_path)
            else:
                try:
                    winreg.DeleteValue(key, app_name)
                except FileNotFoundError:
                    pass

            winreg.CloseKey(key)
            self.update_log(f"Автозапуск {'включен' if autostart else 'отключен'}")
        except Exception as e:
            self.update_log(f"Ошибка настройки автозапуска: {str(e)}")

    def check_autostart(self):
        if self.config_data.get('autostart', False):
            self.start_monitoring()
            self._startup_tray_hide = True
            self.withdraw()

    def start_monitoring(self):
        if self.monitor_worker and self.monitor_worker.bot_active:
            self.update_log("Мониторинг уже запущен")
            return

        self.monitor_worker = MonitorWorker(self.config_data, self._worker_callbacks())

        self.start_btn.config(state=tk.DISABLED)
        self.stop_btn.config(state=tk.NORMAL)
        self.status_text.config(text="Мониторинг запущен", foreground=PRIMARY_COLOR)

        self.monitor_worker.start_monitoring()

    def on_monitoring_stopped(self):
        self.start_btn.config(state=tk.NORMAL)
        self.stop_btn.config(state=tk.DISABLED)
        self.status_text.config(text="Мониторинг остановлен", foreground="#ff5555")

    def stop_monitoring(self):
        if self.monitor_worker:
            self.monitor_worker.stop_monitoring()

    def check_now(self):
        if self.monitor_worker and self.monitor_worker.bot_active:
            self.update_log("Принудительная проверка...")
            self.monitor_worker.monitor_event.set()
        else:
            self.update_log("Мониторинг не запущен")

    def _set_item_info_text(self, text):
        self.item_info.config(state=tk.NORMAL)
        self.item_info.delete("1.0", tk.END)
        self.item_info.insert(tk.END, text)
        self.item_info.config(state=tk.DISABLED)

    def handle_new_item(self, item):
        self.latest_item = item
        self.last_item_label.config(text=f"Последний товар: ID {item['id']}")

        lines = [
            f"ID: {item['id']}",
            f"Название: {item['title']}",
            f"Цена: {item['price']} RUB",
            f"Продавец: {item['seller']}",
            f"Добавлено: {item['time']}",
            "",
            "Характеристики:",
        ]
        for badge in item.get('all_badges', []):
            lines.append(f"  - {badge}")
        lines.append("")
        lines.append("Статусы:")
        for status in item.get('statuses', []):
            lines.append(f"  - {status}")

        self._set_item_info_text("\n".join(lines))

    def send_test_message(self):
        if not self.latest_item:
            self.update_log("Нет информации о товаре для отправки")
            return

        if self.monitor_worker:
            self.monitor_worker.send_telegram_notification(self.latest_item)

    def test_telegram(self):
        token = self.telegram_token_edit.get().strip()
        chat_id = self.telegram_chat_edit.get().strip()

        if not token or not chat_id:
            self.update_log("Заполните настройки Telegram")
            return

        self.update_log("Проверка подключения к Telegram...")

        url = f'https://api.telegram.org/bot{token}/getMe'
        try:
            response = requests.get(url)
            if response.status_code == 200:
                bot_info = response.json().get('result', {})
                bot_name = bot_info.get('first_name', 'Unknown')
                self.update_log(f"Бот найден: {bot_name}")
            else:
                self.update_log(f"Ошибка: {response.json().get('description', 'Unknown error')}")
                return
        except Exception as e:
            self.update_log(f"Ошибка подключения: {str(e)}")
            return

        test_msg = "✅ Тестовое сообщение от LZT Market Monitor"

        if self.monitor_worker:
            if self.monitor_worker.send_telegram_message(chat_id, test_msg):
                self.update_log("Тестовое сообщение успешно отправлено!")
        else:
            url = f'https://api.telegram.org/bot{token}/sendMessage'
            payload = {
                'chat_id': chat_id,
                'text': test_msg,
                'parse_mode': 'HTML'
            }

            try:
                response = requests.post(url, json=payload)
                response.raise_for_status()
                self.update_log("Тестовое сообщение успешно отправлено!")
            except Exception as e:
                self.update_log(f"Ошибка отправки тестового сообщения: {str(e)}")

    def clear_log(self):
        self.log_area.delete("1.0", tk.END)
        self.update_log("Журнал очищен")

    def save_log(self):
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        log_file = os.path.join(_app_dir(), f"log_{timestamp}.txt")
        try:
            with open(log_file, 'w', encoding='utf-8') as f:
                f.write(self.log_area.get("1.0", tk.END))
            self.update_log(f"Журнал сохранен в файл: {log_file}")
        except Exception as e:
            self.update_log(f"Ошибка сохранения журнала: {str(e)}")

    def close_app(self):
        self.stop_monitoring()
        if self.tray_icon:
            try:
                self.tray_icon.stop()
            except Exception:
                pass
        self.destroy()


if __name__ == "__main__":
    app = LZTMonitor()
    app.mainloop()
