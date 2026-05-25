# Music Deduplicator

[🇷🇺 Русский](#русский) | [🇬🇧 English](#english)

> **AI-generated** — all code written via [OpenCode](https://github.com/anomalyco/opencode) (DeepSeek).  
> **Создано с помощью ИИ** — весь код написан через [OpenCode](https://github.com/anomalyco/opencode) (DeepSeek).

---

## 🇷🇺 Русский

GUI-приложение для поиска и удаления дубликатов MP3-файлов. Находит одинаковые песни и оставляет версию с лучшим качеством (наивысшим битрейтом).

### Возможности

- **3 режима поиска дубликатов:**
  - **Tags only** — сравнение по ID3-тегам (исполнитель + название)
  - **Acoustic** — сравнение по аудио-содержанию (акустический фингерпринт)
  - **Hybrid** — теги + акустическая проверка (рекомендуется)
- Оставляет файл с наивысшим битрейтом, остальные помечает как дубликаты
- Удаление или перемещение дубликатов в папку `_duplicates`
- Кеширование акустических отпечатков для быстрого повторного сканирования
- Работает без ffmpeg (использует Windows Media Foundation через miniaudio)

### Скриншот

![Music Deduplicator](screenshot.png)

### Скачать

Готовый `.exe` — Python не требуется:  
[find_duplicates.exe](https://github.com/velosipedf-cell/music-deduplicator/releases/latest)

### Установка (из исходников)

```bash
git clone https://github.com/velosipedf-cell/music-deduplicator.git
cd music-deduplicator
pip install -r requirements.txt
```

### Использование

```bash
python music_dedup.py
```

Консольная версия:

```bash
# Сухой прогон (только показать дубликаты):
python find_duplicates.py "D:\Music" -n

# Удалить дубликаты:
python find_duplicates.py "D:\Music"

# Переместить в _duplicates:
python find_duplicates.py "D:\Music" -m
```

### Как работает акустический режим

1. Декодирует MP3 в PCM через Windows Media Foundation (ffmpeg не требуется)
2. Берёт отрезок с 10-й по 90-ю секунду (пропускает тишину в начале)
3. Строит mel-спектрограмму (32 полосы, 200 Гц – 11 кГц)
4. Вычисляет бинарный отпечаток: разница энергии между соседними кадрами
5. Сравнивает отпечатки по расстоянию Хэмминга (порог сходства: 80%)
6. Фингерпринты кешируются в `%TEMP%\music_dedup_cache\`

### Зависимости

- **mutagen** — чтение ID3-тегов и битрейта MP3
- **numpy + scipy** — обработка сигналов, спектрограмма
- **miniaudio** — декодирование аудио (Windows Media Foundation)
- **tkinter** — GUI (входит в стандартную поставку Python)

---

## 🇬🇧 English

A GUI application for finding and removing duplicate MP3 files. Finds identical songs and keeps the best quality version (highest bitrate).

### Features

- **3 duplicate detection modes:**
  - **Tags only** — compare by ID3 tags (artist + title)
  - **Acoustic** — compare by audio content (acoustic fingerprint)
  - **Hybrid** — tags first, then acoustic verification (recommended)
- Keeps the highest bitrate file, marks the rest as duplicates
- Delete or move duplicates to a `_duplicates` folder
- Caches acoustic fingerprints for fast re-scanning
- Works without ffmpeg (uses Windows Media Foundation via miniaudio)

### Screenshot

![Music Deduplicator](screenshot.png)

### Download

Ready-to-use `.exe` — no Python required:  
[find_duplicates.exe](https://github.com/velosipedf-cell/music-deduplicator/releases/latest)

### Installation (from source)

```bash
git clone https://github.com/velosipedf-cell/music-deduplicator.git
cd music-deduplicator
pip install -r requirements.txt
```

### Usage

```bash
python music_dedup.py
```

CLI version also available:

```bash
# Dry run (show duplicates only):
python find_duplicates.py "D:\Music" -n

# Delete duplicates:
python find_duplicates.py "D:\Music"

# Move to _duplicates:
python find_duplicates.py "D:\Music" -m
```

### How acoustic mode works

1. Decodes MP3 to PCM via Windows Media Foundation (no ffmpeg needed)
2. Extracts a segment from 10 s to 90 s (skips leading silence)
3. Computes a mel-spectrogram (32 bands, 200 Hz – 11 kHz)
4. Produces a binary fingerprint: energy difference between adjacent frames
5. Compares fingerprints using Hamming distance (similarity threshold: 80%)
6. Fingerprints are cached in `%TEMP%\music_dedup_cache\`

### Dependencies

- **mutagen** — ID3 tag and bitrate reading
- **numpy + scipy** — signal processing, spectrogram
- **miniaudio** — audio decoding (Windows Media Foundation)
- **tkinter** — GUI (included with Python)
