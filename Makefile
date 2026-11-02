.PHONY: check up down reset logs ps notebook notebook-token

# Предполётная проверка: докер запущен, памяти хватает, .env на месте
check:
	@bash scripts/preflight.sh

# Весь стек: Postgres + ClickHouse + сплитовалка + генератор
up: check
	docker compose up -d --build

down:
	docker compose down

# Полный сброс: удаляет ВСЕ данные (базы, историю, эксперименты)
reset:
	docker compose down -v

logs:
	docker compose logs -f --tail=100

ps:
	docker compose ps

# JupyterLab с готовыми библиотеками; базы поднимаются как зависимости
notebook: check
	docker compose --profile notebooks up -d --build jupyter
	@echo "Открой http://localhost:8888. Токен: make notebook-token (после старта сервера)."

notebook-token:
	docker compose exec -T jupyter jupyter server list
