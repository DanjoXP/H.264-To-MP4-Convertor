# 🎬 H.264 to MP4 Folder Converter

A modern desktop application built in Python using **PySide6 (Qt)** to convert `.264` / `.h264` raw video files into standard `.mp4` video format.

![App Features](https://img.shields.io/badge/Python-3.9%2B-blue)
![GUI](https://img.shields.io/badge/GUI-PySide6-green)
![FFmpeg](https://img.shields.io/badge/Video%20Engine-imageio--ffmpeg-orange)

## Why I built this
Little project I created because I couldn’t get H.264 files to play on any player I tried.  
I thought it would be a fun challenge to build a simple converter.

AI was used to assist with code generation and development throughout this project

---

## ✨ Features

- 📂 **Drag & Drop Folder Support**: Drop any folder directly onto the app window to automatically scan for `.264` / `.h264` files.
- 🎯 **Destination Selection**: Select any custom directory to output converted MP4 files.
- 🌳 **Preserve Folder Hierarchy**: Option to maintain original subfolder structures inside the output directory.
- ⚡ **Stream Copy (Instant Conversion)**: Uses `-c:v copy` muxing to convert files instantly without quality loss.
- 🔄 **Smart Transcode Fallback**: Automatically re-encodes (`libx264`) if raw streams lack standard stream headers.
- 🚀 **Smooth Multithreading**: Conversion runs on a background thread so the GUI never freezes.
- 📊 **Progress & Logging**: Real-time per-file progress, queue table, and detailed conversion log.

---

## 🛠️ Quick Start

### Step 1: Open in PyCharm & Install Dependencies
1. Open PyCharm and select **File -> Open...** -> choose this folder.
2. Run in terminal:
   ```bash
   pip install -r requirements.txt
   ```

### Step 2: Run the App
- Run `main.py` directly inside PyCharm (`Shift + F10`), or run:
  ```bash
  python main.py
  ```

---

## 📁 Project Structure

```
h264_converter/
├── main.py            # Main application launcher
├── gui.py             # PySide6 User Interface & Drag-and-Drop dropzone
├── converter.py       # H.264 file scanner & FFmpeg conversion engine
├── requirements.txt   # PySide6 & imageio-ffmpeg dependencies
├── README.md          # Project overview
├── INSTALL.md         # Step-by-step setup guide for PyCharm
└── run.bat            # One-click Windows launch script
```
