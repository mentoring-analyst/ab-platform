.PHONY: check up up-full down reset logs ps

# Предполётная проверка: докер запущен, памяти хватает, .env на месте
check:
	@bash scripts/preflight.sh

# Лёгкий профиль: Postgres + ClickHouse + сплитовалка + генератор (~3 ГБ)
up: check
	docker compose up -d --build

# Полный профиль: + Superset для бонусного дашборда
up-full: check
	docker compose --profile full up -d --build

down:
	docker compose --profile full down

# Полный сброс: удаляет ВСЕ данные (базы, историю, эксперименты)
reset:
	docker compose --profile full down -v

logs:
	docker compose logs -f --tail=100

ps:
	docker compose --profile full ps
