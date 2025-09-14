#!/usr/bin/env bash
set -e  # hata olursa script dursun

echo "🔄 Bağımlılıklar kuruluyor..."
sudo apt update
sudo apt install -y \
    python3 python3-pip python3-venv \
    python3-gi python3-gi-cairo gir1.2-gtk-4.0 gir1.2-adw-1 \
    libgirepository1.0-dev libcairo2-dev build-essential \
    fonts-noto-core fonts-liberation2 \
    python3-numpy python3-dev

echo "✅ Tüm bağımlılıklar kuruldu."
