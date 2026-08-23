.PHONY: check up down reset logs ps

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
