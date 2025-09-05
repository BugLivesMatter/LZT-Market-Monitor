<img width="905" height="753" alt="Screenshot_2" src="https://github.com/user-attachments/assets/1f6fcc79-cf45-4372-acae-f658b99a8d4e" />
# LZT Market Monitor

**Монитор товаров на LZT.Market с уведомлениями в Telegram**

LZT Market Monitor — это приложение для мониторинга новых товаров на платформе [LZT.Market](https://lzt.market) с отправкой уведомлений в Telegram. Приложение поддерживает работу в фоне, автозапуск при старте Windows и конфигурируемые параметры проверки.

---

## Особенности

- Мониторинг выбранного товара по ID на странице LZT.Market
- Отправка уведомлений в Telegram с полной информацией о товаре
- Поддержка headless режима (браузер без GUI)
- Автозапуск приложения при старте Windows
- Удобный темный интерфейс на PyQt5
- Журнал событий с возможностью сохранения и очистки

---

## Требования

- Python 3.11+
- Браузер Chrome (или Chromium) и соответствующий ChromeDriver
- Библиотеки Python (установить через `pip`):
  ```bash
  pip install -r requirements.txt

## Установка
 - git clone https://github.com/BugLivesMatter/LZT-Market-Monitor.git
 - cd LZT-Market-Monitor
 - pip install -r requirements.txt
 - python main.py
