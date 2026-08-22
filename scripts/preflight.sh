#!/bin/bash
# Предполётная проверка окружения. Запуск: make check
set -u

FAIL=0

echo "== Проверка окружения ab-platform =="

# 1. Docker установлен и демон запущен
if ! command -v docker >/dev/null 2>&1; then
    echo "❌ Docker не установлен. Поставь Docker Desktop: https://www.docker.com/products/docker-desktop/"
    exit 1
fi

MEM_BYTES=$(docker info --format '{{.MemTotal}}' 2>/dev/null || echo 0)
if [ "${MEM_BYTES:-0}" = "0" ]; then
    echo "❌ Docker-демон не запущен. Открой Docker Desktop и дождись зелёного кита, затем повтори make check."
    exit 1
fi
echo "✅ Docker запущен"

# 2. Памяти, выделенной докеру, хватает
MEM_GB=$(( MEM_BYTES / 1024 / 1024 / 1024 ))
if [ "$MEM_GB" -lt 4 ]; then
    echo "❌ Докеру выделено ${MEM_GB} ГБ — мало даже для лёгкого профиля (нужно 4+, для полного 8+)."
    FAIL=1
elif [ "$MEM_GB" -lt 8 ]; then
    echo "⚠️  Докеру выделено ${MEM_GB} ГБ: лёгкий профиль (make up) поместится, полный (make up-full) — нет."
    echo "   Как поднять лимит: Mac — Docker Desktop → Settings → Resources → Memory;"
    echo "   Windows — файл %UserProfile%\\.wslconfig, секция [wsl2], memory=8GB, затем wsl --shutdown."
else
    echo "✅ Докеру выделено ${MEM_GB} ГБ — хватает на полный профиль"
fi

# 3. Свободный диск (образы + данные ≈ 15 ГБ с запасом)
DISK_FREE_GB=$(df -g / 2>/dev/null | awk 'NR==2 {print $4}')
if [ -z "$DISK_FREE_GB" ]; then
    DISK_FREE_GB=$(df -BG / | awk 'NR==2 {gsub("G","",$4); print $4}')
fi
if [ "${DISK_FREE_GB:-0}" -lt 15 ]; then
    echo "⚠️  Свободно ${DISK_FREE_GB:-?} ГБ на диске — может не хватить (нужно ~15 ГБ под образы и данные)."
else
    echo "✅ Диск: свободно ${DISK_FREE_GB} ГБ"
fi

# 4. Свободны ли порты
for PORT in 5434 8000 8081 8089 8123 9001; do
    if lsof -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; then
        echo "⚠️  Порт $PORT занят другим процессом — соответствующий сервис не поднимется."
    fi
done

# 5. .env
if [ ! -f .env ]; then
    cp .env.example .env
    echo "✅ Создал .env из .env.example"
else
    echo "✅ .env на месте"
fi

if [ "$FAIL" -eq 1 ]; then
    echo ""
    echo "Проверка не пройдена — исправь пункты с ❌ и повтори."
    exit 1
fi
echo ""
echo "Всё готово. Дальше: make up (лёгкий профиль) или make up-full (со всеми сервисами)."
