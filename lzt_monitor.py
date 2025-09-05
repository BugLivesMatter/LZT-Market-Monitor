import sys
import os
import json
import time
import threading
import traceback
import winreg
from datetime import datetime
import requests
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from bs4 import BeautifulSoup
from PyQt5.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QTabWidget,
                             QLabel, QLineEdit, QPushButton, QTextEdit, QCheckBox, QSystemTrayIcon,
                             QAction, QMenu, QMessageBox, QGroupBox, QFormLayout, QSpinBox, QFrame)
from PyQt5.QtGui import QIcon, QColor, QPalette, QFont
from PyQt5.QtCore import Qt, QTimer, pyqtSignal, QObject

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


class MonitorWorker(QObject):
    update_log = pyqtSignal(str)
    update_status = pyqtSignal(str)
    new_item = pyqtSignal(dict)
    update_last_check = pyqtSignal(str)
    update_last_item = pyqtSignal(str)
    monitoring_stopped = pyqtSignal()

    def __init__(self, config):
        super().__init__()
        self.config = config
        self.bot_active = False
        self.driver = None
        self.monitor_event = threading.Event()

    def start_monitoring(self):
        if self.bot_active:
            self.update_log.emit("Мониторинг уже запущен")
            return

        self.bot_active = True
        self.update_log.emit("Запуск мониторинга...")

        # Инициализация драйвера
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
            self.update_log.emit("Браузер инициализирован")
        except Exception as e:
            self.update_log.emit(f"Ошибка инициализации браузера: {str(e)}")
            self.stop_monitoring()
            return

        # Запуск потока мониторинга
        self.monitor_thread = threading.Thread(target=self.run_monitoring, daemon=True)
        self.monitor_thread.start()

    def stop_monitoring(self):
        if not self.bot_active:
            return

        self.bot_active = False
        self.update_log.emit("Остановка мониторинга...")

        # Закрытие браузера
        if self.driver:
            try:
                self.driver.quit()
                self.update_log.emit("Браузер закрыт")
            except:
                pass
            finally:
                self.driver = None

        self.monitoring_stopped.emit()

    def run_monitoring(self):
        last_item = None

        while self.bot_active:
            try:
                current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                self.update_log.emit(f"Проверка нового товара...")
                self.update_last_check.emit(f"Последняя проверка: {current_time}")

                current_item = self.fetch_current_item()
                if current_item:
                    self.new_item.emit(current_item)

                    if last_item is None:
                        self.update_log.emit(f"Инициализация: товар #{current_item['id']}")
                        last_item = current_item
                    elif current_item['id'] != last_item['id']:
                        self.update_log.emit(f"Обнаружен новый товар! ID: {current_item['id']}")
                        last_item = current_item
                        self.send_telegram_notification(current_item)

                    else:
                        self.update_log.emit("Изменений нет, тот же товар")
                else:
                    self.update_log.emit("Не удалось распознать товар")

                # Ожидание следующей проверки
                self.update_log.emit(f"Ожидание {self.config.get('check_interval', 60)} секунд...")
                self.monitor_event.wait(self.config.get('check_interval', 60))
                self.monitor_event.clear()

            except Exception as e:
                self.update_log.emit(f"Ошибка в мониторинге: {str(e)}")
                time.sleep(10)


    def get_page_html(self):
        try:
            url = self.config.get('url', '')
            if not url:
                self.update_log.emit("URL не указан в настройках")
                return None

            self.driver.get(url)
            WebDriverWait(self.driver, 20).until(
                EC.presence_of_element_located((By.CLASS_NAME, 'marketIndex--itemsContainer')))
            self.driver.execute_script("window.scrollTo(0, 500);")
            time.sleep(1)
            return self.driver.page_source
        except Exception as e:
            self.update_log.emit(f"Ошибка загрузки страницы: {str(e)}")
            return None

    def parse_second_item(self, html, itemid):
        """Парсинг товара с пропуском спонсорских объявлений"""
        if not html:
            return None

        soup = BeautifulSoup(html, 'html.parser')
        container = soup.find('div', class_='marketIndex--itemsContainer')
        if not container:
            self.update_log.emit("Контейнер с товарами не найден!")
            return None

        # Если нужно, можно также искать с пробелом в конце (на всякий случай)
        items = container.find_all('div', class_='marketIndexItem PopupItemLink')

        print(items)

        if len(items) < itemid + 1:
            self.update_log.emit(f"Недостаточно товаров на странице! Найдено: {len(items)}, требуется: {itemid + 1}")
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

            # Извлекаем статусы
            statuses = []
            status_container = item.find('div', class_='marketIndexItem--Badges stats')
            if status_container:
                for status in status_container.find_all('span', class_='stat'):
                    statuses.append(status.text.strip())

            # Собираем характеристики
            all_badges = []
            badges_container = None

            # Поиск контейнера характеристик
            for container in item.find_all('div', class_='marketIndexItem--Badges'):
                if container.find('div', class_='marketIndexItem-Badge'):
                    badges_container = container
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
            self.update_log.emit(f"Ошибка парсинга товара: {str(e)}")
            return None

    def fetch_current_item(self):
        html = self.get_page_html()
        return self.parse_second_item(html, (self.config.get('itemID', 0)) if html else None)

    def format_telegram_message(self, item):
        """Форматирование сообщения для Telegram"""
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
            self.update_log.emit("Не настроен Telegram бот")
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
            self.update_log.emit("Уведомление отправлено в Telegram")
            return True
        except Exception as e:
            self.update_log.emit(f"Ошибка отправки в Telegram: {str(e)}")
            return False


class LZTMonitor(QMainWindow):
    def __init__(self):
        super().__init__()

        # Инициализация переменных
        self.config = self.load_config()
        self.latest_item = None
        self.monitor_worker = None
        self.tray_icon = None

        # Настройка окна
        self.setWindowTitle("LZT Market Monitor")
        self.setWindowIcon(QIcon(self.create_icon()))
        self.setGeometry(100, 100, 900, 700)

        # Установка цветовой схемы
        self.setStyleSheet(f"""
            QMainWindow {{
                background-color: {BACKGROUND_COLOR};
                color: {TEXT_COLOR};
                font-family: 'Segoe UI', Arial, sans-serif;
            }}
            QTabWidget::pane {{
                border: none;
                background-color: {BACKGROUND_COLOR};
            }}
            QTabBar::tab {{
                background-color: {BACKGROUND_COLOR};
                color: {TEXT_COLOR};
                padding: 10px 20px;
                border: none;
                border-bottom: 2px solid transparent;
                font-size: 12px;
            }}
            QTabBar::tab:selected {{
                color: {PRIMARY_COLOR};
                border-bottom: 2px solid {PRIMARY_COLOR};
                font-weight: bold;
            }}
            QGroupBox {{
                border: 1px solid {HIGHLIGHT_COLOR};
                border-radius: 8px;
                margin-top: 15px;
                padding-top: 15px;
                font-size: 12px;
                color: {PRIMARY_COLOR};
                background-color: {BACKGROUND_COLOR};
            }}
            QGroupBox::title {{
                subcontrol-origin: margin;
                subcontrol-position: top left;
                padding: 0 8px;
                left: 10px;
            }}
            QPushButton {{
                background-color: {SECONDARY_COLOR};
                color: #ffffff;
                border: none;
                border-radius: 4px;
                padding: 8px 16px;
                font-weight: bold;
                min-height: 30px;
            }}
            QPushButton:hover {{
                background-color: {PRIMARY_COLOR};
            }}
            QPushButton:pressed {{
                background-color: #006e48;
            }}
            QPushButton:disabled {{
                background-color: #3a3a3a;
                color: #7a7a7a;
            }}
            QLineEdit, QTextEdit, QSpinBox {{
                background-color: {HIGHLIGHT_COLOR};
                color: {TEXT_COLOR};
                border: 1px solid #3a3a3a;
                border-radius: 4px;
                padding: 8px;
                font-size: 12px;
            }}
            QSpinBox::up-button, QSpinBox::down-button {{
                width: 20px;
            }}
            QCheckBox {{
                spacing: 8px;
            }}
            QCheckBox::indicator {{
                width: 18px;
                height: 18px;
                border: 1px solid #3a3a3a;
                border-radius: 3px;
            }}
            QCheckBox::indicator:checked {{
                background-color: {PRIMARY_COLOR};
                border: 1px solid {PRIMARY_COLOR};
            }}
            QCheckBox::indicator:unchecked {{
                background-color: {HIGHLIGHT_COLOR};
            }}
            QTextEdit {{
                font-family: 'Consolas', 'Courier New', monospace;
                font-size: 11px;
            }}
            QLabel {{
                font-size: 12px;
            }}
        """)

        # Создание виджетов
        self.init_ui()

        # Загрузка конфигурации
        self.load_config_to_ui()

        # Создание трей-иконки
        self.create_tray_icon()

        # Проверка автозапуска
        self.check_autostart()

    def create_icon(self):
        # Создание простой иконки с логотипом
        from PyQt5.QtGui import QPixmap, QPainter, QBrush, QPen
        pixmap = QPixmap(64, 64)
        pixmap.fill(Qt.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.Antialiasing)

        # Рисуем круг с градиентом
        gradient = QColor(PRIMARY_COLOR)
        painter.setBrush(QBrush(gradient))
        painter.setPen(Qt.NoPen)
        painter.drawEllipse(8, 8, 48, 48)

        # Буква "L" внутри
        painter.setPen(QPen(QColor(BACKGROUND_COLOR), 6))
        painter.setFont(QFont("Arial", 24, QFont.Bold))
        painter.drawText(20, 40, "L")

        painter.end()
        return pixmap

    def init_ui(self):
        # Создание вкладок
        self.tabs = QTabWidget()
        self.tabs.setTabPosition(QTabWidget.North)
        self.tabs.setDocumentMode(True)
        self.tabs.setStyleSheet("QTabBar::tab { height: 40px; }")
        self.setCentralWidget(self.tabs)

        # Вкладка мониторинга
        self.monitor_tab = QWidget()
        self.tabs.addTab(self.monitor_tab, "Мониторинг")
        self.setup_monitor_tab()

        # Вкладка настроек
        self.settings_tab = QWidget()
        self.tabs.addTab(self.settings_tab, "Настройки")
        self.setup_settings_tab()

        # Вкладка журнала
        self.log_tab = QWidget()
        self.tabs.addTab(self.log_tab, "Журнал")
        self.setup_log_tab()

        # Статус бар
        self.status_bar = self.statusBar()
        self.status_label = QLabel("Готов к работе")
        self.status_label.setStyleSheet(f"color: {PRIMARY_COLOR};")
        self.status_bar.addWidget(self.status_label)

    def setup_monitor_tab(self):
        layout = QVBoxLayout()
        layout.setContentsMargins(15, 15, 15, 15)
        layout.setSpacing(15)

        # Статус мониторинга
        status_group = QGroupBox("Статус мониторинга")
        status_layout = QVBoxLayout()
        status_layout.setContentsMargins(15, 20, 15, 15)

        self.status_text = QLabel("Мониторинг не запущен")
        self.status_text.setStyleSheet(f"font-size: 14px; color: {PRIMARY_COLOR}; font-weight: bold;")
        status_layout.addWidget(self.status_text)

        self.last_check_label = QLabel("Последняя проверка: никогда")
        status_layout.addWidget(self.last_check_label)

        self.last_item_label = QLabel("Последний товар: нет")
        status_layout.addWidget(self.last_item_label)

        status_group.setLayout(status_layout)
        layout.addWidget(status_group)

        # Управление
        control_layout = QHBoxLayout()
        control_layout.setSpacing(10)

        self.start_btn = QPushButton("Запустить мониторинг")
        self.start_btn.clicked.connect(self.start_monitoring)
        control_layout.addWidget(self.start_btn)

        self.stop_btn = QPushButton("Остановить мониторинг")
        self.stop_btn.clicked.connect(self.stop_monitoring)
        self.stop_btn.setEnabled(False)
        control_layout.addWidget(self.stop_btn)

        self.check_now_btn = QPushButton("Проверить сейчас")
        self.check_now_btn.clicked.connect(self.check_now)
        control_layout.addWidget(self.check_now_btn)

        layout.addLayout(control_layout)

        # Разделитель
        separator = QFrame()
        separator.setFrameShape(QFrame.HLine)
        separator.setFrameShadow(QFrame.Sunken)
        separator.setStyleSheet(f"color: {HIGHLIGHT_COLOR};")
        layout.addWidget(separator)

        # Текущий товар
        item_group = QGroupBox("Текущий товар")
        item_layout = QVBoxLayout()
        item_layout.setContentsMargins(15, 20, 15, 15)

        self.item_info = QTextEdit()
        self.item_info.setReadOnly(True)
        self.item_info.setMinimumHeight(200)
        item_layout.addWidget(self.item_info)

        btn_layout = QHBoxLayout()
        self.send_test_btn = QPushButton("Отправить в Telegram")
        self.send_test_btn.clicked.connect(self.send_test_message)
        btn_layout.addWidget(self.send_test_btn)

        item_layout.addLayout(btn_layout)
        item_group.setLayout(item_layout)
        layout.addWidget(item_group)

        self.monitor_tab.setLayout(layout)

    def setup_settings_tab(self):
        layout = QVBoxLayout()
        layout.setContentsMargins(15, 15, 15, 15)
        layout.setSpacing(15)

        # Настройки мониторинга
        monitor_group = QGroupBox("Настройки мониторинга")
        monitor_layout = QFormLayout()
        monitor_layout.setContentsMargins(15, 20, 15, 15)
        monitor_layout.setSpacing(10)
        monitor_layout.setHorizontalSpacing(15)

        self.url_edit = QLineEdit()
        self.url_edit.setPlaceholderText("https://lzt.market/...")
        monitor_layout.addRow("URL страницы:", self.url_edit)

        self.item_id_spin = QSpinBox()
        self.item_id_spin.setRange(0, 20)
        self.item_id_spin.setToolTip("Позиция товара на странице (обычно 0 или 1)")
        monitor_layout.addRow("ID товара (позиция):", self.item_id_spin)

        self.interval_spin = QSpinBox()
        self.interval_spin.setRange(10, 3600)
        self.interval_spin.setSuffix(" сек")
        self.interval_spin.setToolTip("Интервал между проверками в секундах")
        monitor_layout.addRow("Интервал проверки:", self.interval_spin)

        self.headless_check = QCheckBox("Режим без интерфейса (Headless)")
        self.headless_check.setToolTip("Запускать браузер в фоновом режиме")
        monitor_layout.addRow(self.headless_check)

        monitor_group.setLayout(monitor_layout)
        layout.addWidget(monitor_group)

        # Настройки Telegram
        telegram_group = QGroupBox("Настройки Telegram")
        telegram_layout = QFormLayout()
        telegram_layout.setContentsMargins(15, 20, 15, 15)
        telegram_layout.setSpacing(10)
        telegram_layout.setHorizontalSpacing(15)

        self.telegram_token_edit = QLineEdit()
        self.telegram_token_edit.setPlaceholderText("123456789:ABC-DEF1234ghIkl-zyx57W2v1u123ew11")
        telegram_layout.addRow("Токен бота:", self.telegram_token_edit)

        self.telegram_chat_edit = QLineEdit()
        self.telegram_chat_edit.setPlaceholderText("123456789")
        telegram_layout.addRow("ID чата:", self.telegram_chat_edit)

        self.test_telegram_btn = QPushButton("Проверить подключение к Telegram")
        self.test_telegram_btn.clicked.connect(self.test_telegram)
        telegram_layout.addRow(self.test_telegram_btn)

        telegram_group.setLayout(telegram_layout)
        layout.addWidget(telegram_group)

        # Настройки приложения
        app_group = QGroupBox("Настройки приложения")
        app_layout = QFormLayout()
        app_layout.setContentsMargins(15, 20, 15, 15)
        app_layout.setSpacing(10)
        app_layout.setHorizontalSpacing(15)

        self.autostart_check = QCheckBox("Запускать при старте Windows")
        app_layout.addRow(self.autostart_check)

        app_group.setLayout(app_layout)
        layout.addWidget(app_group)

        # Кнопки сохранения
        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(10)

        self.save_btn = QPushButton("Сохранить настройки")
        self.save_btn.clicked.connect(self.save_settings)
        btn_layout.addWidget(self.save_btn)

        self.default_btn = QPushButton("Сбросить настройки")
        self.default_btn.clicked.connect(self.reset_settings)
        btn_layout.addWidget(self.default_btn)

        layout.addLayout(btn_layout)

        self.settings_tab.setLayout(layout)

    def setup_log_tab(self):
        layout = QVBoxLayout()
        layout.setContentsMargins(15, 15, 15, 15)
        layout.setSpacing(15)

        self.log_area = QTextEdit()
        self.log_area.setReadOnly(True)
        self.log_area.setStyleSheet("font-family: 'Consolas', 'Courier New', monospace; font-size: 11px;")

        layout.addWidget(self.log_area)

        # Кнопки управления журналом
        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(10)

        self.clear_log_btn = QPushButton("Очистить журнал")
        self.clear_log_btn.clicked.connect(self.clear_log)
        btn_layout.addWidget(self.clear_log_btn)

        self.save_log_btn = QPushButton("Сохранить журнал")
        self.save_log_btn.clicked.connect(self.save_log)
        btn_layout.addWidget(self.save_log_btn)

        layout.addLayout(btn_layout)

        self.log_tab.setLayout(layout)

    def create_tray_icon(self):
        # Создание иконки для трея
        self.tray_icon = QSystemTrayIcon(self)
        self.tray_icon.setIcon(QIcon(self.create_icon()))

        # Создание контекстного меню
        tray_menu = QMenu()

        show_action = QAction("Открыть", self)
        show_action.triggered.connect(self.show)
        tray_menu.addAction(show_action)

        quit_action = QAction("Выход", self)
        quit_action.triggered.connect(self.close_app)
        tray_menu.addAction(quit_action)

        self.tray_icon.setContextMenu(tray_menu)
        self.tray_icon.show()

    def close_app(self):
        self.stop_monitoring()
        QApplication.quit()

    def closeEvent(self, event):
        # При нажатии на крестик закрываем приложение
        self.close_app()
        event.accept()

    def changeEvent(self, event):
        # При нажатии на свернуть - сворачиваем в трей
        if event.type() == event.WindowStateChange:
            if self.windowState() & Qt.WindowMinimized:
                event.ignore()
                self.hide()
                self.tray_icon.showMessage(
                    "LZT Monitor",
                    "Приложение свернуто в трей",
                    QSystemTrayIcon.Information,
                    2000
                )

    def update_log(self, message):
        timestamp = datetime.now().strftime("[%H:%M:%S]")
        self.log_area.append(f"{timestamp} {message}")
        self.log_area.verticalScrollBar().setValue(self.log_area.verticalScrollBar().maximum())

    def update_status(self, message):
        self.status_label.setText(message)

    def load_config(self):
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except:
                self.update_log("Ошибка загрузки конфигурации, используются настройки по умолчанию")
                return DEFAULT_CONFIG.copy()
        return DEFAULT_CONFIG.copy()

    def save_config(self):
        with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
            json.dump(self.config, f, ensure_ascii=False, indent=2)
        self.update_log("Конфигурация сохранена")

    def load_config_to_ui(self):
        self.url_edit.setText(self.config.get('url', ''))
        self.item_id_spin.setValue(self.config.get('itemID', 0))
        self.interval_spin.setValue(self.config.get('check_interval', 60))
        self.telegram_token_edit.setText(self.config.get('telegram_bot_token', ''))
        self.telegram_chat_edit.setText(self.config.get('telegram_chat_id', ''))
        self.headless_check.setChecked(self.config.get('headless', True))
        self.autostart_check.setChecked(self.config.get('autostart', False))

    def save_settings(self):
        self.config['url'] = self.url_edit.text()
        self.config['itemID'] = self.item_id_spin.value()
        self.config['check_interval'] = self.interval_spin.value()
        self.config['telegram_bot_token'] = self.telegram_token_edit.text()
        self.config['telegram_chat_id'] = self.telegram_chat_edit.text()
        self.config['headless'] = self.headless_check.isChecked()
        self.config['autostart'] = self.autostart_check.isChecked()

        self.save_config()
        self.update_autostart()
        self.update_log("Настройки сохранены")

        # Обновление работы мониторинга при необходимости
        if self.monitor_worker and self.monitor_worker.bot_active:
            self.stop_monitoring()
            self.start_monitoring()

    def reset_settings(self):
        self.config = DEFAULT_CONFIG.copy()
        self.load_config_to_ui()
        self.save_config()
        self.update_log("Настройки сброшены до значений по умолчания")

    def update_autostart(self):
        autostart = self.config.get('autostart', False)
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
        if self.config.get('autostart', False):
            self.start_monitoring()
            self.hide()

    def start_monitoring(self):
        if self.monitor_worker and self.monitor_worker.bot_active:
            self.update_log("Мониторинг уже запущен")
            return

        self.monitor_worker = MonitorWorker(self.config)

        # Подключаем сигналы
        self.monitor_worker.update_log.connect(self.update_log)
        self.monitor_worker.update_status.connect(self.update_status)
        self.monitor_worker.new_item.connect(self.handle_new_item)
        self.monitor_worker.update_last_check.connect(self.last_check_label.setText)
        self.monitor_worker.update_last_item.connect(self.last_item_label.setText)
        self.monitor_worker.monitoring_stopped.connect(self.on_monitoring_stopped)

        self.start_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.status_text.setText("Мониторинг запущен")
        self.status_text.setStyleSheet(f"color: {PRIMARY_COLOR}; font-weight: bold;")

        # Запускаем мониторинг
        self.monitor_worker.start_monitoring()

    def on_monitoring_stopped(self):
        self.start_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self.status_text.setText("Мониторинг остановлен")
        self.status_text.setStyleSheet("color: #ff5555; font-weight: bold;")

    def stop_monitoring(self):
        if self.monitor_worker:
            self.monitor_worker.stop_monitoring()

    def check_now(self):
        if self.monitor_worker and self.monitor_worker.bot_active:
            self.update_log("Принудительная проверка...")
            self.monitor_worker.monitor_event.set()
        else:
            self.update_log("Мониторинг не запущен")

    def handle_new_item(self, item):
        self.latest_item = item
        self.last_item_label.setText(f"Последний товар: ID {item['id']}")

        # Форматирование информации о товаре
        item_info = f"<b>ID:</b> {item['id']}<br>"
        item_info += f"<b>Название:</b> {item['title']}<br>"
        item_info += f"<b>Цена:</b> {item['price']} RUB<br>"
        item_info += f"<b>Продавец:</b> {item['seller']}<br>"
        item_info += f"<b>Добавлено:</b> {item['time']}<br><br>"

        item_info += "<b>Характеристики:</b><br>"
        for badge in item.get('all_badges', []):
            item_info += f"- {badge}<br>"

        item_info += "<br><b>Статусы:</b><br>"
        for status in item.get('statuses', []):
            item_info += f"- {status}<br>"

        self.item_info.setHtml(item_info)

    def send_test_message(self):
        if not self.latest_item:
            self.update_log("Нет информации о товаре для отправки")
            return

        if self.monitor_worker:
            self.monitor_worker.send_telegram_notification(self.latest_item)

    def test_telegram(self):
        token = self.telegram_token_edit.text()
        chat_id = self.telegram_chat_edit.text()

        if not token or not chat_id:
            self.update_log("Заполните настройки Telegram")
            return

        self.update_log("Проверка подключения к Telegram...")

        # Проверка токена
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

        # Проверка отправки сообщения
        test_msg = "✅ Тестовое сообщение от LZT Market Monitor"

        # Отправляем через worker, если он есть
        if self.monitor_worker:
            if self.monitor_worker.send_telegram_message(chat_id, test_msg):
                self.update_log("Тестовое сообщение успешно отправлено!")
        else:
            # Если worker не создан, отправляем напрямую
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
        self.log_area.clear()
        self.update_log("Журнал очищен")

    def save_log(self):
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        log_file = f"log_{timestamp}.txt"

        try:
            with open(log_file, 'w', encoding='utf-8') as f:
                f.write(self.log_area.toPlainText())
            self.update_log(f"Журнал сохранен в файл: {log_file}")
        except Exception as e:
            self.update_log(f"Ошибка сохранения журнала: {str(e)}")


if __name__ == "__main__":
    app = QApplication(sys.argv)

    # Настройка палитры для темной темы
    palette = QPalette()
    palette.setColor(QPalette.Window, QColor(BACKGROUND_COLOR))
    palette.setColor(QPalette.WindowText, QColor(TEXT_COLOR))
    palette.setColor(QPalette.Base, QColor(HIGHLIGHT_COLOR))
    palette.setColor(QPalette.AlternateBase, QColor("#252525"))
    palette.setColor(QPalette.ToolTipBase, QColor(PRIMARY_COLOR))
    palette.setColor(QPalette.ToolTipText, QColor("#ffffff"))
    palette.setColor(QPalette.Text, QColor(TEXT_COLOR))
    palette.setColor(QPalette.Button, QColor(SECONDARY_COLOR))
    palette.setColor(QPalette.ButtonText, QColor("#ffffff"))
    palette.setColor(QPalette.BrightText, QColor("#ff5555"))
    palette.setColor(QPalette.Highlight, QColor(PRIMARY_COLOR))
    palette.setColor(QPalette.HighlightedText, QColor("#000000"))
    palette.setColor(QPalette.Disabled, QPalette.ButtonText, QColor("#7a7a7a"))
    app.setPalette(palette)

    window = LZTMonitor()
    window.show()
    sys.exit(app.exec_())