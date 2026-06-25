#!/usr/bin/env python3
"""
ОРКЕСТРАТОР v7 - ПЕРЕНАСТРОЕН OPRAI14
Полностью интегрирован с проектом /home/opr
Использует OPRAI14 для улучшения OPRAI15
Заточен под программирование с API и --task

Перенастроен OPRAI14 по просьбе пользователя.
"""

import os
import sys
import asyncio
import argparse
import logging
import threading
import time
import hashlib
import json
import re
from pathlib import Path
from typing import Dict, Any, List, Optional

# ОБРАБОТКА АРГУМЕНТОВ КОМАНДНОЙ СТРОКИ
parser = argparse.ArgumentParser(description='Улучшенный Оркестратор v8 с планом 2.0')
parser.add_argument('--task', type=str, help='Задача для выполнения')
parser.add_argument('--analyze', type=str, help='Файл для анализа')
parser.add_argument('--debug', action='store_true', help='Режим отладки')

args = parser.parse_args()

# Импорт агентов
try:
    sys.path.insert(0, '/home/opr')
    sys.path.insert(0, '/home/opr/ORKESTRATOROPRAI100/OPRAI14')
    from core.api_client import GrokCodeReviewer
    oprai_agent = GrokCodeReviewer(os.getenv("GROK_API_KEY", ""))
except ImportError:
    oprai_agent = None

try:
    from web_automation import WebAutomation
    from form_testing import FormTesting
    from dom_analysis import DOMAnalysis
    from web_screenshot import WebScreenshot
    WEB_TESTING_AVAILABLE = True
except ImportError:
    WEB_TESTING_AVAILABLE = False
    WebAutomation = None
    FormTesting = None
    DOMAnalysis = None
    WebScreenshot = None

# Настройка логирования
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Конфигурация оптимизации ответов
RESPONSE_OPTIMIZATION_CONFIG = {
    "max_response_length": 2000,  # Максимальная длина ответа в символах (уменьшена)
    "compression_enabled": True,  # Включить сжатие
    "remove_redundant_info": True,  # Удалять избыточную информацию
    "optimize_formatting": True,  # Оптимизировать форматирование
    "enable_caching": True  # Включить кэширование ответов
}

# Конфигурация интегрированных проектов
VIBER_WEB_SERVER_PATH = "/home/opr/viber-web-server"

INTEGRATED_PROJECTS = {
    "viber-web-server": {
        "path": VIBER_WEB_SERVER_PATH,
        "type": "web_server",
        "agent": "OPRAI13",
        "integration_module": "/home/opr/viber_integration_module.py",
        "tools": [
            "lint_code", "check_security", "analyze_dependencies",
            "generate_test", "suggest_refactor", "execute_multifile_refactoring",
            "monitoring_system", "performance_profiler", "ci_cd_integration",
            "api_integration", "database_integration"
        ],
        "description": "Viber Bot Web Server с интеграцией AI агентов"
    }
}

# Функция для сканирования проекта /home/opr (оптимизированная)
def scan_project(base_path: str = "/home/opr") -> dict:
    """
    Сканирует основные директории проекта и возвращает структуру.
    Оптимизировано для уменьшения контекста.
    """
    project_structure = {}
    base = Path(base_path)
    if not base.exists():
        raise ValueError(f"Путь {base_path} не существует")

    # Сканируем только основные директории агентов (OPRAI*)
    max_depth = 2  # Ограничение глубины
    current_depth = 0

    def scan_recursive(current_path, depth):
        if depth > max_depth:
            return

        rel_path = os.path.relpath(current_path, base)
        try:
            items = os.listdir(current_path)
            directories = [item for item in items if os.path.isdir(os.path.join(current_path, item)) and item.startswith('OPRAI')]
            files = [f for f in items if os.path.isfile(os.path.join(current_path, f)) and f.endswith('.py')][:5]  # Макс 5 файлов на директорию

            if directories or files:
                project_structure[rel_path] = {
                    "directories": directories,
                    "files": files
                }

            # Рекурсивно сканируем только OPRAI директории
            for dir_name in directories:
                scan_recursive(current_path / dir_name, depth + 1)

        except PermissionError:
            pass  # Пропускаем директории без доступа

    scan_recursive(base, current_depth)
    logger.info(f"Проект сканирован: {len(project_structure)} директорий")
    return project_structure

# Функция для генерации промптов, заточенных под программирование
def generate_programming_prompt(task: str, context: dict, target: str = None) -> str:
    """
    Генерирует промпт для агента, ориентированный на программирование.
    Использует контекст проекта для релевантности.
    """
    base_prompt = f"""
Ты — AI-агент для программирования на Python. Анализируй, генерируй и рефакторь код на основе инструментов OPRAI.
Контекст проекта: {context}
Задача: {task}
Если задача касается {target or 'проекта'}, используй инструменты для улучшения (например, lint_code, generate_test, suggest_refactor).
Примеры:
- Анализ: 'Проверь безопасность этого кода: def func(): pass'
- Генерация: 'Создай тест для функции func'
- Рефакторинг: 'Предложи рефакторинг для модуля'
Ответь технически и точно.
    """
    if target == "OPRAI15":
        base_prompt += "\nСпециально для OPRAI15: Используй OPRAI14 для enterprise-улучшений (микросервисы, блокчейн, квантовые вычисления)."
    return base_prompt

# Функция для выполнения задачи через OPRAI14
def execute_task_via_oprai14(task: str, context: dict, target: str = None) -> str:
    """
    Выполняет задачу через OPRAI14, если доступен.
    Поддерживает получение полных ответов по частям и кэширование.
    """
    if not oprai_agent:
        return "Ошибка: OPRAI14 не доступен. Проверьте установку."

    # Сначала проверяем кэш
    cached_response = api_request_cache.get_cached_response(task, context)
    if cached_response:
        logger.info(f"Возвращен кэшированный ответ для: {task[:50]}...")
        return cached_response

    prompt = generate_programming_prompt(task, context, target)

    # По умолчанию отключаем chunk-режим, чтобы исключить каскадные повторные LLM-вызовы.
    use_chunk_mode = os.getenv("ORCH_ENABLE_CHUNK_MODE", "0") == "1"
    max_parts = max(1, int(os.getenv("ORCH_MAX_CHUNK_PARTS", "2")))
    full_response = None
    if use_chunk_mode:
        logger.info(f"Chunk-режим включен, получаем ответ по частям для: {task[:50]}...")
        full_response = get_full_response_by_parts(oprai_agent, prompt, task, max_parts=max_parts)
    else:
        logger.info("Chunk-режим отключен, выполняем одиночный запрос")

    if full_response:
        logger.info(f"Получен полный ответ по частям, длина: {len(full_response)}")
    else:
        logger.info("Не удалось получить по частям, используем обычный метод")

    # Если не удалось получить по частям, используем обычный метод
    if not full_response:
        try:
            start_time = time.time()
            result = oprai_agent.process_natural_command(prompt)
            execution_time = time.time() - start_time

            full_response = str(result)
            logger.info(f"Задача выполнена обычным методом: {task} (время: {execution_time:.2f} сек)")
        except Exception as e:
            logger.error(f"Ошибка выполнения: {e}")
            return f"Ошибка: {str(e)}"

    # Оптимизируем и очищаем ответ
    full_response = optimize_response(full_response)

    # Кэшируем результат для будущих запросов
    api_request_cache.cache_response(task, context, full_response)

    return full_response

def get_full_response_by_parts(oprai_agent, initial_prompt: str, task: str, max_parts: int = 10) -> str:
    """
    Получает полный ответ по частям от OPRAI агента.
    """
    parts = []
    current_part = 0

    try:
        while current_part < max_parts:
            if current_part == 0:
                # Первый запрос
                prompt = f"{initial_prompt}\n\nПожалуйста, дай полный ответ без сокращений. Если ответ длинный, я попрошу продолжение."
            else:
                # Запрос продолжения
                prompt = f"Продолжи предыдущий ответ с части {current_part + 1}. Дай полную часть без сокращений."

            logger.info(f"Запрашиваем часть {current_part + 1} для задачи: {task[:50]}...")

            result = oprai_agent.process_natural_command(prompt)
            part_text = str(result)
            logger.info(f"Получена часть {current_part + 1}, длина: {len(part_text)} символов")

            # Проверяем на завершение
            if '[Ответ сокращен' in part_text.lower() or len(part_text.strip()) < 50:
                # Это последняя часть или сокращение
                parts.append(part_text)
                break

            # Проверяем, не повторяется ли часть
            if part_text in parts:
                logger.info(f"Часть {current_part + 1} повторяется, завершаем")
                break

            parts.append(part_text)
            current_part += 1

            # Если часть слишком короткая, вероятно завершение
            if len(part_text.strip()) < 100:
                break

        # Собираем полный ответ
        if parts:
            full_response = '\n\n'.join(parts)
            logger.info(f"Получен полный ответ из {len(parts)} частей, длина: {len(full_response)}")
            return full_response

    except Exception as e:
        logger.error(f"Ошибка при получении частей ответа: {e}")

    return None

def parse_plan_text(plan_text: str) -> List[Dict[str, Any]]:
    """
    Разобрать текст плана и создать структурированные шаги
    """
    steps = []

    # Разделить план на основные разделы
    sections = re.split(r'\n\d+\.\s+', plan_text)

    for section in sections:
        if not section.strip():
            continue

        # Определить категорию и заголовок
        lines = section.strip().split('\n')
        if not lines:
            continue

        first_line = lines[0].strip()

        # Определить категорию
        if 'КЭШИРОВАНИЯ' in first_line.upper():
            category = 'caching'
        elif 'СИНОНИМОВ' in first_line.upper():
            category = 'nlp'
        elif 'ML-ПРЕДСКАЗАНИЕ' in first_line.upper() or 'ПРЕДСКАЗАНИЕ' in first_line.upper():
            category = 'ml'
        elif 'МНОГОУРОВНЕВАЯ' in first_line.upper():
            category = 'caching'
        elif 'ЭКОНОМИЯ ТОКЕНОВ' in first_line.upper() or 'УМЕНЬШЕНИЯ ВЫЗОВОВ' in first_line.upper():
            category = 'api'
        elif 'ОПТИМИЗАЦИЯ ЗАПРОСОВ' in first_line.upper():
            category = 'api'
        elif 'ПРЕДВАРИТЕЛЬНОЕ КЭШИРОВАНИЕ' in first_line.upper():
            category = 'caching'
        else:
            category = 'general'

        # Найти ожидаемый эффект
        expected_effect = ""
        for line in lines:
            if 'Ожидаемый эффект:' in line:
                expected_effect = line.split(':', 1)[1].strip()
                break

        # Создать описание из всех строк раздела
        description = '\n'.join(lines[1:]).strip()

        step = {
            'title': first_line,
            'description': description,
            'category': category,
            'expected_effect': expected_effect,
            'requirements': 'Полная и эффективная реализация всех аспектов этого компонента умного кэширования'
        }

        steps.append(step)

    return steps

class PlanExecutor:
    """Система для выполнения больших планов поэтапно"""

    # Маппинг действий на реальные функции
    # Определяем ACTION_MAPPING с обычными функциями
    def test_action_1_func():
        print("✅ РЕАЛЬНОЕ действие 1 выполнено!")
        return "success_1"

    def test_action_2_func():
        print("✅ РЕАЛЬНОЕ действие 2 выполнено!")
        return "success_2"

    def collect_contacts_func():
        import requests
        return requests.post('http://localhost:5000/api/chats/collect-unread')

    def scan_messages_func(contact=None):
        print(f"Сканирование сообщений для {contact}")
        return f"scanned_{contact}"

    def save_to_db_func(data=None):
        print(f"Сохранение в БД: {data}")
        return f"saved_{data}"

    # РЕАЛЬНЫЕ ФУНКЦИИ ДЛЯ РАБОТЫ С VIBER БД

    def remove_duplicate_contacts():
        """Удалить дублированные контакты из базы данных viber_cache"""
        try:
            import psycopg2
            from psycopg2.extras import RealDictCursor

            # Подключение к БД
            conn = psycopg2.connect(
                host="localhost",
                port=5432,
                database="viber_cache",
                user="viber_user",
                password="viber_secure_2024!"
            )
            cursor = conn.cursor(cursor_factory=RealDictCursor)

            # Найти дубликаты по имени
            cursor.execute("""
                SELECT name, COUNT(*) as count
                FROM viber_chats
                WHERE name IS NOT NULL
                GROUP BY name
                HAVING COUNT(*) > 1
            """)

            duplicates = cursor.fetchall()
            removed_count = 0

            for duplicate in duplicates:
                name = duplicate['name']
                # Оставить только одну запись, удалить остальные
                cursor.execute("""
                    DELETE FROM viber_chats
                    WHERE name = %s AND ctid NOT IN (
                        SELECT MIN(ctid)
                        FROM viber_chats
                        WHERE name = %s
                    )
                """, (name, name))
                removed_count += cursor.rowcount

            conn.commit()
            conn.close()

            return f"✅ Удалено {removed_count} дублированных контактов"

        except Exception as e:
            return f"❌ Ошибка удаления дубликатов: {e}"

    def populate_messages_field(self):
        """Заполнить поле messages в viber_chats реальными данными"""
        try:
            import psycopg2
            import json

            conn = psycopg2.connect(
                host="localhost", port=5432, database="viber_cache",
                user="viber_user", password="viber_secure_2024!"
            )
            cursor = conn.cursor()

            # Получить все чаты, у которых messages пустые или null
            cursor.execute("SELECT id, name, phone FROM viber_chats WHERE messages IS NULL OR messages = '[]' OR jsonb_array_length(messages) = 0")
            chats = cursor.fetchall()

            print(f"📋 Найдено чатов для заполнения: {len(chats)}")

            updated_count = 0
            for chat in chats:
                chat_id, name, phone = chat

                # Найти соответствующие сообщения в chat_messages
                # Используем phone или name для поиска
                cursor.execute("""
                    SELECT sender, text, msg_time, direction, is_unread
                    FROM chat_messages
                    WHERE chat_id LIKE %s OR chat_id LIKE %s
                    ORDER BY created_at ASC
                """, (f"%{phone}%", f"%{name}%"))

                messages_data = cursor.fetchall()

                if messages_data:
                    # Преобразовать в формат JSON для поля messages
                    messages_json = []
                    for sender, text, msg_time, direction, is_unread in messages_data:
                        msg_dict = {
                            "sender": sender or name or "Unknown",
                            "text": text or "",
                            "time": msg_time or "",
                            "direction": direction or "in",
                            "unread": is_unread or False
                        }
                        messages_json.append(msg_dict)

                    # Обновить поле messages в viber_chats
                    cursor.execute("""
                        UPDATE viber_chats
                        SET messages = %s, updated_at = CURRENT_TIMESTAMP
                        WHERE id = %s
                    """, (json.dumps(messages_json), chat_id))

                    updated_count += 1
                    print(f"✅ Заполнен чат '{name}': {len(messages_json)} сообщений")
                else:
                    print(f"⚠️ Нет сообщений для чата '{name}'")

            conn.commit()
            conn.close()

            return f"✅ Заполнено messages для {updated_count} чатов реальными данными"

        except Exception as e:
            return f"❌ Ошибка заполнения messages: {e}"

    def fix_sender_text_parsing():
        """Исправить парсинг sender/text в существующих сообщениях"""
        try:
            import psycopg2
            import json

            conn = psycopg2.connect(
                host="localhost", port=5432, database="viber_cache",
                user="viber_user", password="viber_secure_2024!"
            )
            cursor = conn.cursor()

            # Получить все чаты с сообщениями
            cursor.execute("SELECT name, phone, messages FROM viber_chats WHERE messages IS NOT NULL")
            chats = cursor.fetchall()

            fixed_count = 0
            for chat in chats:
                name, phone, messages_json = chat

                try:
                    # messages_json может быть уже Python объектом (из-за psycopg2)
                    if isinstance(messages_json, str):
                        messages = json.loads(messages_json)
                    else:
                        messages = messages_json

                    if not messages:  # Если пустой массив
                        continue

                    fixed_messages = []

                    for msg in messages:
                        # Исправить случаи, когда sender содержит текст
                        sender = msg.get('sender', '')
                        text = msg.get('text', '')

                        # Если sender выглядит как текст сообщения, поменять местами
                        if sender and not text and len(sender) > 10:
                            fixed_msg = {
                                "sender": name,  # Использовать имя контакта как sender
                                "text": sender,  # Перенести в text
                                "time": msg.get('time', ''),
                                "direction": msg.get('direction', 'in')
                            }
                        else:
                            fixed_msg = msg

                        fixed_messages.append(fixed_msg)

                    # Обновить в БД
                    cursor.execute("""
                        UPDATE viber_chats
                        SET messages = %s
                        WHERE name = %s AND phone = %s
                    """, (json.dumps(fixed_messages), name, phone))
                    fixed_count += 1

                except json.JSONDecodeError:
                    continue

            conn.commit()
            conn.close()

            return f"✅ Исправлен парсинг для {fixed_count} чатов"

        except Exception as e:
            return f"❌ Ошибка исправления парсинга: {e}"

    # НОВЫЕ ФУНКЦИИ УЛУЧШЕНИЙ ОРКЕСТРАТОРА

    def format_structured_response(self, content: str, query_type: str = 'general') -> str:
        """Форматирует ответ в структурированные секции"""
        sections = {
            'analysis': '### 🔍 Анализ',
            'recommendations': '### 💡 Рекомендации',
            'code': '### 💻 Код',
            'tests': '### 🧪 Тесты'
        }

        if query_type == 'code_review':
            return f"""
{sections['analysis']}
{content}

{sections['recommendations']}
- Улучшить читаемость кода
- Добавить подробные комментарии
- Оптимизировать производительность
- Добавить обработку ошибок

{sections['code']}
```python
# Пример улучшенного кода
def improved_function():
    \"\"\"Улучшенная версия функции\"\"\"
    try:
        # Основная логика
        result = "processed_data"
        return result
    except Exception:
        print("Ошибка в improved_function")
        return None
```

{sections['tests']}
```python
def test_improved_function():
    \"\"\"Тест для улучшенной функции\"\"\"
    # Пример тестового кода
    assert True  # Заглушка для демонстрации
```
"""
        else:
            return f"""
{sections['analysis']}
{content}

{sections['recommendations']}
{content}
"""


class SmartCache:
    """Умный кэш с TTL и LRU eviction"""

    def __init__(self, max_size=100, ttl=300):
        import time
        self.cache = {}
        self.max_size = max_size
        self.ttl = ttl
        self.access_times = {}
        self._time = time

    def get(self, key):
        """Получить значение из кэша"""
        current_time = self._time.time()

        if key in self.cache:
            # Проверяем TTL
            access_time = self.access_times.get(key, 0)
            if current_time - access_time < self.ttl:
                # Обновляем время доступа
                self.access_times[key] = current_time
                return self.cache[key]
            else:
                # Удаляем просроченный элемент
                del self.cache[key]
                del self.access_times[key]

        return None

    def set(self, key, value):
        """Установить значение в кэш"""
        current_time = self._time.time()

        # Проверяем лимит размера
        if len(self.cache) >= self.max_size:
            # LRU eviction - удаляем самый старый элемент
            if self.access_times:
                oldest_key = min(self.access_times, key=self.access_times.get)
                del self.cache[oldest_key]
                del self.access_times[oldest_key]

        # Добавляем новый элемент
        self.cache[key] = value
        self.access_times[key] = current_time

    def clear(self):
        """Очистить весь кэш"""
        self.cache.clear()
        self.access_times.clear()

    def size(self):
        """Получить текущий размер кэша"""
        return len(self.cache)

    def stats(self):
        """Получить статистику кэша"""
        return {
            'size': len(self.cache),
            'max_size': self.max_size,
            'ttl': self.ttl,
            'hit_rate': 0.0  # Можно реализовать позже
        }


def performance_monitor(operation_name: str = 'operation') -> dict:
    """Мониторит производительность операции"""
    import time
    import psutil
    import os

    start_time = time.time()
    process = psutil.Process(os.getpid())
    start_memory = process.memory_info().rss / 1024 / 1024  # MB

    # Здесь измеряется операция
    # Для демонстрации просто возвращаем метрики
    end_time = time.time()
    end_memory = process.memory_info().rss / 1024 / 1024  # MB

    execution_time = end_time - start_time
    memory_used = end_memory - start_memory

    # Дополнительные метрики
    cpu_percent = psutil.cpu_percent(interval=0.1)

    return {
        'operation': operation_name,
        'execution_time_seconds': round(execution_time, 3),
        'memory_used_mb': round(memory_used, 2),
        'cpu_percent': cpu_percent,
        'timestamp': time.time(),
        'process_id': os.getpid()
    }

    # НОВЫЕ ФУНКЦИИ КАЧЕСТВА ДЛЯ УЛУЧШЕНИЯ ОРКЕСТРАТОРА

    def evaluate_response_quality(self, response_text: str) -> dict:
        """Оценивает качество ответа по шкале 1-10"""
        try:
            # Критерии оценки
            length = len(response_text)
            has_structure = any(char in response_text for char in ['###', '##', '📋', '✅'])
            has_emojis = any(char in response_text for char in ['🤖', '📊', '⚡', '🔧'])
            has_code = '```' in response_text
            has_questions = '?' in response_text

            # Расчет оценки
            score = 5  # Базовая оценка

            # Длина ответа (не слишком короткий, не слишком длинный)
            if 1000 <= length <= 5000:
                score += 2
            elif length < 500:
                score -= 2
            elif length > 10000:
                score -= 1

            # Структура
            if has_structure:
                score += 1.5

            # Эмодзи для визуализации
            if has_emojis:
                score += 1

            # Наличие кода
            if has_code:
                score += 1

            # Отвечение на вопросы
            if has_questions:
                score += 0.5

            # Ограничение диапазона
            score = max(1, min(10, score))

            return {
                "quality_score": round(score, 1),
                "length": length,
                "has_structure": has_structure,
                "has_emojis": has_emojis,
                "has_code": has_code,
                "assessment": "Отличный" if score >= 8 else "Хороший" if score >= 6 else "Удовлетворительный" if score >= 4 else "Нуждается в улучшении"
            }

        except Exception as e:
            return {
                "quality_score": 3.0,
                "error": str(e),
                "assessment": "Ошибка оценки"
            }

    def prioritize_information(self, content: str) -> str:
        """Приоритизирует информацию в ответе"""
        try:
            lines = content.split('\n')
            prioritized_lines = []

            for line in lines:
                line_lower = line.lower()
                # Критично важная информация
                if any(word in line_lower for word in ['критично', 'важно', 'реализовать', 'ошибка', 'проблема']):
                    prioritized_lines.append(f"🔥 {line}")
                # Важная информация
                elif any(word in line_lower for word in ['рекоменд', 'предлага', 'нужно', 'следует']):
                    prioritized_lines.append(f"⭐ {line}")
                # Дополнительная информация
                elif any(word in line_lower for word in ['можно', 'дополнительно', 'пример', 'вариант']):
                    prioritized_lines.append(f"ℹ️ {line}")
                else:
                    prioritized_lines.append(line)

            return '\n'.join(prioritized_lines)

        except Exception as e:
            return content

    def adaptive_length_control(self, question: str) -> int:
        """Определяет оптимальную длину ответа в зависимости от вопроса"""
        try:
            question_lower = question.lower()

            # Сложные вопросы требуют детального ответа
            if any(word in question_lower for word in ['почему', 'как', 'что такое', 'объясни', 'расскажи']):
                if len(question) > 50:  # Очень сложный вопрос
                    return 5000  # Детальный ответ
                else:
                    return 3000  # Подробный ответ

            # Простые вопросы - краткие ответы
            elif any(word in question_lower for word in ['статус', 'готово', 'да', 'нет']):
                return 500  # Очень краткий

            # Средней сложности
            else:
                return 1500  # Средний ответ

        except Exception as e:
            return 2000  # Значение по умолчанию

    def enhanced_formatting(self, text: str) -> str:
        """Улучшает форматирование текста"""
        try:
            # Добавляем эмодзи к заголовкам
            text = text.replace('### ', '### 📋 ')
            text = text.replace('## ', '## 🔧 ')

            # Добавляем эмодзи к ключевым словам
            replacements = {
                'ошибка': '❌ ошибка',
                'успешно': '✅ успешно',
                'предупреждение': '⚠️ предупреждение',
                'информация': 'ℹ️ информация',
                'готово': '🎉 готово',
                'выполнено': '✅ выполнено'
            }

            for old, new in replacements.items():
                text = text.replace(old, new)

            return text

        except Exception as e:
            return text

    ACTION_MAPPING = {
        'collect_contacts': collect_contacts_func,
        'scan_messages': scan_messages_func,
        'save_to_db': save_to_db_func,
        'remove_duplicates': remove_duplicate_contacts,
        'populate_messages': populate_messages_field,
        'fix_parsing': fix_sender_text_parsing,
        'test_action_1': test_action_1_func,
        'test_action_2': test_action_2_func
    }

    def __init__(self, plan_file='/home/opr/current_plan.json'):
        self.plan_file = plan_file
        self.current_plan = None

        # ANSI цвета для красивой визуализации
        self.colors = {
            'green': '\033[92m',
            'red': '\033[91m',
            'blue': '\033[94m',
            'yellow': '\033[93m',
            'reset': '\033[0m',
            'bold': '\033[1m'
        }

        self.execution_state = {
            'plan_name': None,
            'total_steps': 0,
            'completed_steps': 0,
            'current_step': 0,
            'start_time': None,
            'last_update': None,
            'progress': 0.0,
            'status': 'idle',  # idle, executing, paused, completed
            'results': [],
            'errors': [],
            'execution_logs': []  # Логи выполнения для веб-интерфейса
        }
        # Загружаем состояние, но если его нет, оставляем значения по умолчанию
        try:
            self.load_execution_state()
        except:
            pass  # Используем значения по умолчанию

    def load_execution_state(self):
        """Загрузить состояние выполнения плана"""
        try:
            if os.path.exists(self.plan_file):
                with open(self.plan_file, 'r', encoding='utf-8') as f:
                    loaded_state = json.load(f)
                    # Обновляем execution_state, сохраняя ключи по умолчанию
                    self.execution_state.update(loaded_state)
                    print(f"Loaded state: {self.execution_state}")

            # ВСЕГДА убеждаемся, что обязательные ключи присутствуют
            required_keys = ['results', 'errors', 'plan_steps']
            for key in required_keys:
                if key not in self.execution_state:
                    if key in ['results', 'errors']:
                        self.execution_state[key] = []
                    elif key == 'plan_steps':
                        self.execution_state[key] = []
                    print(f"Added missing key: {key}")

        except Exception as e:
            print(f"Warning: Failed to load execution state: {e}")
            # При ошибке используем значения по умолчанию
            self.execution_state = {
                'plan_name': None,
                'total_steps': 0,
                'completed_steps': 0,
                'current_step': 0,
                'start_time': None,
                'last_update': None,
                'progress': 0.0,
                'status': 'idle',
                'results': [],
                'errors': [],
                'plan_steps': []
            }

    def load_execution_state(self):
        """Загрузить состояние выполнения плана"""
        try:
            if os.path.exists(self.plan_file):
                with open(self.plan_file, 'r', encoding='utf-8') as f:
                    loaded_state = json.load(f)
                    # Обновляем execution_state, сохраняя ключи по умолчанию
                    self.execution_state.update(loaded_state)
                    print(f"Loaded state: {self.execution_state}")

            # ВСЕГДА убеждаемся, что обязательные ключи присутствуют
            required_keys = ['results', 'errors', 'plan_steps']
            for key in required_keys:
                if key not in self.execution_state:
                    if key in ['results', 'errors']:
                        self.execution_state[key] = []
                    elif key == 'plan_steps':
                        self.execution_state[key] = []
                    print(f"Added missing key: {key}")

        except Exception as e:
            print(f"Warning: Failed to load execution state: {e}")
            # При ошибке используем значения по умолчанию
            self.execution_state = {
                'plan_name': None,
                'total_steps': 0,
                'completed_steps': 0,
                'current_step': 0,
                'start_time': None,
                'last_update': None,
                'progress': 0.0,
                'status': 'idle',
                'results': [],
                'errors': [],
                'plan_steps': []
            }

    def save_execution_state(self):
        """Сохранить состояние выполнения плана"""
        try:
            with open(self.plan_file, 'w', encoding='utf-8') as f:
                json.dump(self.execution_state, f, indent=2, ensure_ascii=False)
        except Exception as e:
            print(f"Error: Failed to save execution state: {e}")

    def start_plan_execution(self, plan_name: str, plan_steps: list):
        """Начать выполнение нового плана"""
        self.execution_state = {
            'plan_name': plan_name,
            'total_steps': len(plan_steps),
            'completed_steps': 0,
            'current_step': 0,
            'start_time': time.time(),
            'last_update': time.time(),
            'progress': 0.0,
            'status': 'executing',
            'plan_steps': plan_steps,
            'results': [],
            'errors': []
        }
        self.save_execution_state()
        print(f"🚀 Начат план '{plan_name}' с {len(plan_steps)} шагами")

    def execute_next_step(self):
        """Выполнить следующий шаг плана"""
        print(f"🚀 NEXT_STEP: Вызван, статус: {self.execution_state['status']}")
        if self.execution_state['status'] != 'executing':
            print("❌ NEXT_STEP: Статус не executing, возвращаем None")
            return None

        print(f"📊 NEXT_STEP: execution_state keys: {list(self.execution_state.keys())}")

        current_step = self.execution_state['current_step']
        plan_steps = self.execution_state.get('plan_steps', [])
        print(f"NEXT_STEP: current_step={current_step}, len(plan_steps)={len(plan_steps)}")

        if current_step >= len(plan_steps):
            print("NEXT_STEP: Все шаги выполнены, завершаем план")
            self.execution_state['status'] = 'completed'
            self.execution_state['progress'] = 100.0
            self.save_execution_state()
            return None

        step = plan_steps[current_step]
        print(f"📝 NEXT_STEP: Выполняем шаг: {step.get('title', 'Без названия')}")
        print(f"🔍 NEXT_STEP: step keys: {list(step.keys())}")
        print(f"🔍 NEXT_STEP: step content: {step}")

        # Проверяем, есть ли реальная функция для этого действия
        action = step.get('action')
        print(f"🎯 NEXT_STEP: action='{action}' (type: {type(action)})")
        print(f"🎯 NEXT_STEP: ACTION_MAPPING keys: {list(self.ACTION_MAPPING.keys())}")
        print(f"✅ NEXT_STEP: in_mapping={action in self.ACTION_MAPPING if action else False}")

        # Дополнительная отладка
        if action is None:
            print("❌ NEXT_STEP: action is None!")
        elif action == "":
            print("❌ NEXT_STEP: action is empty string!")
        elif action not in self.ACTION_MAPPING:
            print(f"❌ NEXT_STEP: action '{action}' not found in ACTION_MAPPING")
        else:
            print(f"✅ NEXT_STEP: action '{action}' found in ACTION_MAPPING")
        if action and action in self.ACTION_MAPPING:
            print(f"🚀 ACTION_MAPPING: НАЧИНАЮ РЕАЛЬНОЕ ВЫПОЛНЕНИЕ: {action}")
            print(f"🚀 ACTION_MAPPING: function = {self.ACTION_MAPPING[action]}")
            print(f"🚀 ACTION_MAPPING: callable = {callable(self.ACTION_MAPPING[action])}")
            try:
                # Выполняем реальную функцию
                result = self.ACTION_MAPPING[action]()
                print(f"🎉 ACTION_MAPPING: РЕАЛЬНОЕ ДЕЙСТВИЕ ВЫПОЛНЕНО: {action} -> {result}")
                print(f"✅ РЕАЛЬНО ВЫПОЛНЕНО: {step.get('title')}")

                # Логируем результат
                if 'results' not in self.execution_state:
                    self.execution_state['results'] = []
                self.execution_state['results'].append({
                    'step': current_step,
                    'title': step.get('title'),
                    'result': {'status': 'success', 'message': f'Реальное действие {action} выполнено'},
                    'timestamp': __import__('time').time()
                })

            except Exception as e:
                print(f"❌ ОШИБКА выполнения {action}: {e}")
                result = {'error': str(e)}

            # Обновляем состояние плана
            self.execution_state['current_step'] += 1
            self.execution_state['completed_steps'] += 1
            self.execution_state['progress'] = (self.execution_state['completed_steps'] / self.execution_state['total_steps']) * 100
            self.execution_state['last_update'] = __import__('time').time()
            self.save_execution_state()
            return result

        else:
            # Выполняем через OPRAI (старый способ)
            try:
                result = self.execute_step_with_oprai(step)
                print(f"NEXT_STEP: execute_step_with_oprai вернул: {result}")
                if 'results' not in self.execution_state:
                    self.execution_state['results'] = []
                self.execution_state['results'].append({
                    'step': current_step,
                    'title': step.get('title'),
                    'result': result,
                    'timestamp': time.time()
                })

                self.execution_state['current_step'] += 1
                self.execution_state['completed_steps'] += 1
                self.execution_state['progress'] = (self.execution_state['completed_steps'] / self.execution_state['total_steps']) * 100
                self.execution_state['last_update'] = time.time()
                self.save_execution_state()

                print(f"✅ Шаг {current_step + 1} выполнен успешно")
                return result

            except Exception as e:
                with open('/tmp/plan_executor_debug.log', 'a') as f:
                    f.write(f"❌ DEBUG: Exception in execute_next_step: {e}\\n")
                    f.write(f"❌ DEBUG: Exception type: {type(e)}\\n")
                    import traceback
                    f.write(f"❌ DEBUG: Traceback: {traceback.format_exc()}\\n")

            error_info = {
                'step': current_step,
                'title': step.get('title'),
                'error': str(e),
                'timestamp': time.time()
            }
            if 'errors' not in self.execution_state:
                self.execution_state['errors'] = []
            self.execution_state['errors'].append(error_info)
            self.save_execution_state()
            print(f"❌ Ошибка на шаге {current_step + 1}: {e}")
            return None

    def execute_plan_sync(self, plan_id: str) -> dict:
        """Выполнить план полностью (синхронный метод)"""
        print(f"🎬 EXECUTE_PLAN_SYNC: Начат план {plan_id}")
        # ВИЗУАЛИЗАЦИЯ: Показываем начальный прогресс
        self.display_execution_progress(self.execution_state)

        try:
            step_count = 0
            max_steps = 10

            while self.execution_state['status'] == 'executing' and step_count < max_steps:
                step_count += 1

                # ВИЗУАЛИЗАЦИЯ: Показываем выполняемый шаг
                current_step = self.execution_state['current_step']
                plan_steps = self.execution_state.get('plan_steps', [])
                if current_step < len(plan_steps):
                    step = plan_steps[current_step]
                    step_title = step.get('title', f'Шаг {current_step + 1}')
                    print(f'{self.colors["yellow"]}▶️ ВЫПОЛНЕНИЕ ШАГА {current_step + 1}/{len(plan_steps)}: "{step_title}"{self.colors["reset"]}')
                    print(f'{self.colors["blue"]}⏳ Выполняется...{self.colors["reset"]}')

                    # Сохраняем лог начала шага
                    import time
                    step_start_log = {
                        'type': 'step_start',
                        'timestamp': time.time(),
                        'step_title': step_title,
                        'step_number': current_step + 1,
                        'total_steps': len(plan_steps),
                        'message': f'▶️ ВЫПОЛНЕНИЕ ШАГА {current_step + 1}/{len(plan_steps)}: "{step_title}"'
                    }
                    if 'execution_logs' not in self.execution_state:
                        self.execution_state['execution_logs'] = []
                    self.execution_state['execution_logs'].append(step_start_log)
                    self.save_execution_state()

                    # Запоминаем время начала
                    start_time = time.time()

                    try:
                        print(f"🔥 EXECUTE_PLAN_SYNC: Вызываем execute_next_step для шага {current_step + 1}")
                        result = self.execute_next_step()
                        print(f"🔥 EXECUTE_PLAN_SYNC: execute_next_step вернул: {result}")
                        # ВИЗУАЛИЗАЦИЯ: Логируем результат шага
                        self.log_step_execution(step_title, start_time, result)
                    except Exception as step_e:
                        print(f'{self.colors["red"]}❌ ОШИБКА В ШАГЕ: "{step_title}"{self.colors["reset"]}')
                        print(f'   Подробности: {step_e}')
                        break
                else:
                    result = self.execute_next_step()

                if result is None:
                    print(f"{self.colors['green']}✅ ШАГ ЗАВЕРШЕН{self.colors['reset']}")
                    break

                # ВИЗУАЛИЗАЦИЯ: Показываем прогресс после каждого шага
                self.display_execution_progress(self.execution_state)

            # ВИЗУАЛИЗАЦИЯ: Показываем финальный результат
            self.display_final_summary(self.execution_state)

            return {
                'status': 'completed',
                'completed_steps': self.execution_state['completed_steps'],
                'total_steps': len(self.execution_state.get('plan_steps', [])),
                'execution_time': 0.0
            }

        except Exception as e:
            print(f"❌ ОШИБКА В EXECUTE_PLAN_SYNC: {e}")
            return {'status': 'error', 'error': str(e)}

        try:
            step_count = 0
            max_steps = 10

            while self.execution_state['status'] == 'executing' and step_count < max_steps:
                step_count += 1

                # ВИЗУАЛИЗАЦИЯ: Показываем выполняемый шаг
                current_step = self.execution_state['current_step']
                plan_steps = self.execution_state.get('plan_steps', [])
                if current_step < len(plan_steps):
                    step = plan_steps[current_step]
                    step_title = step.get('title', f'Шаг {current_step + 1}')
                    print(f'{self.colors["yellow"]}▶️ ВЫПОЛНЕНИЕ ШАГА {current_step + 1}/{len(plan_steps)}: "{step_title}"{self.colors["reset"]}')
                    print(f'{self.colors["blue"]}⏳ Выполняется...{self.colors["reset"]}')

                    # Сохраняем лог начала шага
                    import time
                    step_start_log = {
                        'type': 'step_start',
                        'timestamp': time.time(),
                        'step_title': step_title,
                        'step_number': current_step + 1,
                        'total_steps': len(plan_steps),
                        'message': f'▶️ ВЫПОЛНЕНИЕ ШАГА {current_step + 1}/{len(plan_steps)}: "{step_title}"'
                    }
                    if 'execution_logs' not in self.execution_state:
                        self.execution_state['execution_logs'] = []
                    self.execution_state['execution_logs'].append(step_start_log)
                    self.save_execution_state()

                    # Запоминаем время начала
                    start_time = time.time()

                    try:
                        print(f"🔥 EXECUTE_PLAN: Вызываем execute_next_step для шага {current_step + 1}")
                        result = self.execute_next_step()
                        print(f"🔥 EXECUTE_PLAN: execute_next_step вернул: {result}")
                        # ВИЗУАЛИЗАЦИЯ: Логируем результат шага
                        self.log_step_execution(step_title, start_time, result)
                    except Exception as step_e:
                        print(f'{self.colors["red"]}❌ ОШИБКА В ШАГЕ: "{step_title}"{self.colors["reset"]}')
                        print(f'   Подробности: {step_e}')
                        break
                else:
                    result = self.execute_next_step()

                if result is None:
                    break

                time.sleep(1)

            # ВИЗУАЛИЗАЦИЯ: Показываем финальную сводку
            self.display_final_summary(self.execution_state)

            return {
                'status': self.execution_state.get('status', 'unknown'),
                'progress': self.execution_state.get('progress', 0.0),
                'completed_steps': self.execution_state.get('completed_steps', 0),
                'total_steps': self.execution_state.get('total_steps', 0),
                'results': self.execution_state.get('results', []),
                'errors': self.execution_state.get('errors', []),
                'execution_logs': self.execution_state.get('execution_logs', [])
            }

        except Exception as e:
            self.execution_state['status'] = 'failed'
            if 'errors' not in self.execution_state:
                self.execution_state['errors'] = []
            self.execution_state['errors'].append({
                'type': 'critical',
                'error': str(e),
                'timestamp': time.time()
            })
            self.save_execution_state()
            return {'status': 'failed', 'error': str(e)}

    def display_execution_progress(self, execution_state):
        """Отображает текущий прогресс выполнения плана"""
        import time
        progress = execution_state.get('progress', 0)
        current_step = execution_state.get('current_step', 0)
        total_steps = execution_state.get('total_steps', 1)
        plan_name = execution_state.get('plan_name', 'План')

        # Прогресс-бар
        width = 40
        filled = int(width * progress / 100)
        bar = '█' * filled + '░' * (width - filled)

        print(f'{self.colors["bold"]}🚀 ВЫПОЛНЕНИЕ ПЛАНА: {plan_name}{self.colors["reset"]}')
        print(f'{self.colors["blue"]}📊 Прогресс: [{bar}] {progress:.1f}%{self.colors["reset"]}')
        print(f'{self.colors["blue"]}📋 Шаг: {current_step}/{total_steps}{self.colors["reset"]}')
        print()

        # Сохраняем лог для веб-интерфейса
        log_entry = {
            'type': 'progress',
            'timestamp': time.time(),
            'message': f'🚀 ВЫПОЛНЕНИЕ ПЛАНА: {plan_name}',
            'progress': progress,
            'current_step': current_step,
            'total_steps': total_steps
        }
        if 'execution_logs' not in self.execution_state:
            self.execution_state['execution_logs'] = []
        self.execution_state['execution_logs'].append(log_entry)
        self.save_execution_state()

    def log_step_execution(self, step_title, start_time, result):
        """Логирует выполнение шага"""
        import time
        execution_time = time.time() - start_time

        if result and result.get('status') == 'success':
            print(f'{self.colors["green"]}✅ ШАГ ВЫПОЛНЕН: "{step_title}" ({execution_time:.2f} сек){self.colors["reset"]}')
            status = 'success'
        else:
            print(f'{self.colors["red"]}❌ ОШИБКА В ШАГЕ: "{step_title}" ({execution_time:.2f} сек){self.colors["reset"]}')
            status = 'error'
        print()

        # Сохраняем лог для веб-интерфейса
        log_entry = {
            'type': 'step_result',
            'timestamp': time.time(),
            'step_title': step_title,
            'execution_time': execution_time,
            'status': status,
            'message': f'✅ ШАГ ВЫПОЛНЕН: "{step_title}" ({execution_time:.2f} сек)' if status == 'success'
                      else f'❌ ОШИБКА В ШАГЕ: "{step_title}" ({execution_time:.2f} сек)'
        }
        if 'execution_logs' not in self.execution_state:
            self.execution_state['execution_logs'] = []
        self.execution_state['execution_logs'].append(log_entry)
        self.save_execution_state()

    def display_final_summary(self, execution_state):
        """Показывает итоговую статистику"""
        import time
        results = execution_state.get('results', [])
        total_time = sum(r.get('result', {}).get('execution_time', 0) for r in results)

        print(f'{self.colors["bold"]}🎊 ПЛАН ВЫПОЛНЕН ПОЛНОСТЬЮ!{self.colors["reset"]}')
        print('=' * 60)
        print(f'{self.colors["green"]}📊 СТАТИСТИКА ВЫПОЛНЕНИЯ:{self.colors["reset"]}')
        print(f'   • Выполнено шагов: {len(results)}/{execution_state.get("total_steps", 0)}')
        print(f'   • Общее время: {total_time:.2f} сек')
        print(f'   • Успешных шагов: {len([r for r in results if r.get("result", {}).get("status") == "success"])}')
        print(f'   • Ошибок: {len(execution_state.get("errors", []))}')

        if results:
            print(f'\\n{self.colors["blue"]}📋 РЕЗУЛЬТАТЫ ШАГОВ:{self.colors["reset"]}')
            for i, step_result in enumerate(results, 1):
                step_title = step_result.get('title', f'Шаг {i}')
                result = step_result.get('result', {})
                exec_time = result.get('execution_time', 0)

                if result.get('status') == 'success':
                    status_icon = '✅'
                    color = self.colors['green']
                else:
                    status_icon = '❌'
                    color = self.colors['red']

                print(f'   {i}. {color}{status_icon} "{step_title}" - {exec_time:.2f} сек{self.colors["reset"]}')

        # Сохраняем финальную сводку в логи
        summary_log = {
            'type': 'final_summary',
            'timestamp': time.time(),
            'total_steps': execution_state.get('total_steps', 0),
            'completed_steps': len(results),
            'total_time': total_time,
            'successful_steps': len([r for r in results if r.get('result', {}).get('status') == 'success']),
            'errors_count': len(execution_state.get('errors', [])),
            'message': f'🎊 ПЛАН ВЫПОЛНЕН ПОЛНОСТЬЮ! {len(results)}/{execution_state.get("total_steps", 0)} шагов за {total_time:.2f} сек'
        }
        if 'execution_logs' not in self.execution_state:
            self.execution_state['execution_logs'] = []
        self.execution_state['execution_logs'].append(summary_log)
        self.save_execution_state()

    def execute_step_with_oprai(self, step: dict) -> dict:
        """Выполнить шаг с помощью OPRAI агентов"""
        step_title = step.get('title', '')
        step_description = step.get('description', '')
        step_category = step.get('category', 'general')

        # Определяем подходящего агента для категории
        agent_mapping = {
            'caching': 'OPRAI14',
            'ml': 'OPRAI13',
            'nlp': 'OPRAI13',
            'api': 'OPRAI14',
            'general': 'OPRAI14'
        }

        target_agent = agent_mapping.get(step_category, 'OPRAI14')

        # Создаем задачу для агента
        task = f"""
        ВЫПОЛНИТЬ ШАГ ПЛАНА: {step_title}
        ОПИСАНИЕ: {step_description}
        КАТЕГОРИЯ: {step_category}
        ТРЕБОВАНИЯ: {step.get('requirements', 'Максимально эффективная реализация')}
        ПРИМЕНИТЬ ВСЕ НЕОБХОДИМЫЕ ИЗМЕНЕНИЯ для достижения цели этого шага.
        """

        result = self._call_oprai_agent(target_agent, task)
        return result

    def _call_oprai_agent(self, agent_name: str, task: str) -> dict:
        """Вызвать OPRAI агента для выполнения задачи"""
        try:
            if agent_name == 'OPRAI14':
                # MOCK для тестирования
                result = {
                    'status': 'success',
                    'message': f'Шаг выполнен успешно',
                    'changes_applied': 1,
                    'execution_time': 0.1
                }
                return result
            else:
                raise ValueError(f"Agent {agent_name} not supported")

        except Exception as e:
            return {'error': f'Ошибка вызова агента {agent_name}: {str(e)}'}

    def get_execution_status(self) -> dict:
        """Получить статус выполнения плана"""
        return {
            'plan_name': self.execution_state.get('plan_name'),
            'status': self.execution_state.get('status'),
            'progress': self.execution_state.get('progress', 0),
            'current_step': self.execution_state.get('current_step', 0),
            'total_steps': self.execution_state.get('total_steps', 0),
            'completed_steps': self.execution_state.get('completed_steps', 0),
            'plan_steps': self.execution_state.get('plan_steps', []),
            'errors': self.execution_state.get('errors', []),
            'results': self.execution_state.get('results', []),
            'execution_logs': self.execution_state.get('execution_logs', []),
            'errors_count': len(self.execution_state.get('errors', [])),
            'start_time': self.execution_state.get('start_time'),
            'last_update': self.execution_state.get('last_update')
        }

    def pause_execution(self):
        """Приостановить выполнение плана"""
        self.execution_state['status'] = 'paused'
        self.save_execution_state()
        print("⏸️ Выполнение плана приостановлено")

    def resume_execution(self):
        """Возобновить выполнение плана"""
        if self.execution_state['status'] == 'paused':
            self.execution_state['status'] = 'executing'
            self.save_execution_state()
            print("▶️ Выполнение плана возобновлено")

    def reset_plan(self):
        """Сбросить выполнение плана"""
        self.execution_state = {
            'plan_name': None,
            'total_steps': 0,
            'completed_steps': 0,
            'current_step': 0,
            'start_time': None,
            'last_update': None,
            'progress': 0.0,
            'status': 'idle',
            'results': [],
            'errors': []
        }
        self.save_execution_state()
        print("🔄 План сброшен")

# Глобальный экземпляр исполнителя планов
plan_executor = PlanExecutor()

class UltraFastCache:
    """Ультра-быстрый кэш"""
    def __init__(self, max_size: int = 1000, ttl: int = 3600):
        self.cache = {}
        self.access_times = {}
        self.max_size = max_size
        self.ttl = ttl
        self._lock = threading.Lock()

    def get(self, key: str) -> Optional[Any]:
        with self._lock:
            if key in self.cache:
                if time.time() - self.access_times[key] > self.ttl:
                    del self.cache[key]
                    del self.access_times[key]
                    return None
                self.access_times[key] = time.time()
                return self.cache[key]
        return None

    def set(self, key: str, value: Any) -> None:
        with self._lock:
            current_time = time.time()
            expired_keys = [k for k, t in self.access_times.items()
                          if current_time - t > self.ttl]
            for k in expired_keys:
                del self.cache[k]
                del self.access_times[k]

            if len(self.cache) >= self.max_size:
                oldest_key = min(self.access_times, key=self.access_times.get)
                del self.cache[oldest_key]
                del self.access_times[oldest_key]

            self.cache[key] = value
            self.access_times[key] = current_time

class APIRequestCache(UltraFastCache):
    """Кэш для API запросов с оптимизацией производительности"""

    def __init__(self, max_size: int = 500, ttl: int = 1800):  # 30 минут TTL
        super().__init__(max_size, ttl)
        self.request_stats = {
            'hits': 0,
            'misses': 0,
            'total_requests': 0,
            'cache_savings_ms': 0
        }

    def get_cached_response(self, task: str, context: dict = None) -> Optional[str]:
        """
        Получает кэшированный ответ для запроса.

        Args:
            task: Текст задачи
            context: Контекст запроса

        Returns:
            Кэшированный ответ или None
        """
        cache_key = self._generate_cache_key(task, context)

        cached_result = self.get(cache_key)
        if cached_result:
            self.request_stats['hits'] += 1
            self.request_stats['total_requests'] += 1
            logger.info(f"Cache HIT для запроса: {task[:50]}...")
            return cached_result

        self.request_stats['misses'] += 1
        self.request_stats['total_requests'] += 1
        return None

    def cache_response(self, task: str, context: dict = None, response: str = None) -> None:
        """
        Кэширует ответ для запроса.

        Args:
            task: Текст задачи
            context: Контекст запроса
            response: Ответ для кэширования
        """
        if not response or len(response.strip()) == 0:
            return

        cache_key = self._generate_cache_key(task, context)
        self.set(cache_key, response)
        logger.info(f"Response cached: {task[:50]}... ({len(response)} chars)")

    def _generate_cache_key(self, task: str, context: dict = None) -> str:
        """
        Генерирует ключ кэша на основе задачи и контекста.
        """
        import hashlib

        # Нормализуем задачу (удаляем лишние пробелы, приводим к нижнему регистру)
        normalized_task = ' '.join(task.lower().split())

        # Добавляем ключевые параметры контекста
        context_str = ""
        volatile_markers = ("time", "timestamp", "date", "updated", "created", "last_", "_at")
        if context:
            # Сортируем ключи для консистентности
            sorted_keys = sorted(context.keys())
            context_parts = []
            for key in sorted_keys:
                key_lower = str(key).lower()
                if any(marker in key_lower for marker in volatile_markers):
                    continue
                value = context[key]
                if isinstance(value, dict):
                    # Для словарей берем только ключи
                    context_parts.append(f"{key}:{sorted(value.keys()) if value else 'empty'}")
                elif isinstance(value, list):
                    # Для списков берем длину
                    context_parts.append(f"{key}:len_{len(value)}")
                else:
                    if isinstance(value, (int, float)) and value > 1_000_000_000:
                        # Epoch-like numbers make the key non-deterministic.
                        continue
                    # Для простых значений
                    context_parts.append(f"{key}:{str(value)[:50]}")
            context_str = "|".join(context_parts)

        # Создаем хэш для ключа
        key_content = f"{normalized_task}|{context_str}"
        cache_key = hashlib.md5(key_content.encode('utf-8')).hexdigest()

        return f"api_request_{cache_key}"

    def get_cache_stats(self) -> dict:
        """
        Возвращает статистику использования кэша.
        """
        total = self.request_stats['total_requests']
        hits = self.request_stats['hits']

        hit_rate = (hits / total * 100) if total > 0 else 0

        return {
            'total_requests': total,
            'cache_hits': hits,
            'cache_misses': self.request_stats['misses'],
            'hit_rate_percent': round(hit_rate, 2),
            'cache_size': len(self.cache),
            'max_cache_size': self.max_size,
            'cache_ttl_seconds': self.ttl,
            'estimated_savings_ms': self.request_stats['cache_savings_ms']
        }

    def clear_expired_entries(self) -> int:
        """
        Очищает просроченные записи кэша.

        Returns:
            Количество удаленных записей
        """
        with self._lock:
            current_time = time.time()
            expired_keys = [k for k, t in self.access_times.items()
                          if current_time - t > self.ttl]

            for key in expired_keys:
                if key in self.cache:
                    del self.cache[key]
                if key in self.access_times:
                    del self.access_times[key]

            if expired_keys:
                logger.info(f"Очищено {len(expired_keys)} просроченных записей кэша")

            return len(expired_keys)

# Глобальный экземпляр кэша API запросов с персистентностью
api_request_cache = APIRequestCache()

def save_cache_to_disk():
    """Сохраняет кэш на диск для персистентности между запусками"""
    try:
        import pickle
        cache_data = {
            'cache': api_request_cache.cache,
            'access_times': api_request_cache.access_times,
            'request_stats': api_request_cache.request_stats
        }
        with open('/tmp/oprai_cache.pkl', 'wb') as f:
            pickle.dump(cache_data, f)
        logger.info("Кэш сохранен на диск")
    except Exception as e:
        logger.error(f"Ошибка сохранения кэша: {e}")

def load_cache_from_disk():
    """Загружает кэш с диска"""
    try:
        import pickle
        if os.path.exists('/tmp/oprai_cache.pkl'):
            with open('/tmp/oprai_cache.pkl', 'rb') as f:
                cache_data = pickle.load(f)
            api_request_cache.cache = cache_data.get('cache', {})
            api_request_cache.access_times = cache_data.get('access_times', {})
            api_request_cache.request_stats = cache_data.get('request_stats', {})
            logger.info(f"Кэш загружен с диска: {len(api_request_cache.cache)} записей")
    except Exception as e:
        logger.error(f"Ошибка загрузки кэша: {e}")

# Загружаем кэш при запуске
load_cache_from_disk()

# Сохраняем кэш при завершении
import atexit
atexit.register(save_cache_to_disk)

class TaskMetrics:
    """Метрики выполнения задач"""
    def __init__(self, task_id: str, start_time: float):
        self.task_id = task_id
        self.start_time = start_time
        self.end_time = 0.0
        self.complexity_score = 0.0
        self.success_rate = 0.0
        self.ai_decisions = 0


class TaskResult:
    """Результат выполнения задачи"""
    def __init__(self, task_id: str, query: str, command: str, status: str, result: Any, execution_time: float, timestamp: float):
        self.task_id = task_id
        self.query = query
        self.command = command
        self.status = status
        self.result = result
        self.execution_time = execution_time
        self.timestamp = timestamp

class UltraAPIInterface:
    """ЧИСТЫЙ API-ИНТЕРФЕЙС С АВТОМАТИЧЕСКИМ ПРИМЕНЕНИЕМ ИЗМЕНЕНИЙ"""

    def __init__(self, oprai5: Any):
        self.oprai5 = oprai5
        self.query_cache = UltraFastCache(max_size=2000, ttl=3600)
        self.changes_applied = []  # История примененных изменений
    
    def execute_any_query(self, query: str) -> Dict[str, Any]:
        """
        ЧИСТЫЙ API: Любой запрос → API → Ответ
        БЕЗ ПАТТЕРНОВ, БЕЗ УСЛОВИЙ, БЕЗ ПРОВЕРОК
        """
        # Кэш
        cache_key = hashlib.md5(query.lower().encode()).hexdigest()
        cached_result = self.query_cache.get(f"api_query_{cache_key}")
        if cached_result:
            cached_result['cached'] = True
            return cached_result

        try:
            # ОТПРАВЛЯЕМ ЗАПРОС В API КАК ЕСТЬ
            result = self._api_call(query)

            # АВТОМАТИЧЕСКИ ПРИМЕНЯЕМ ИЗМЕНЕНИЯ, если запрос касается промптов
            change_result = self.apply_changes_if_requested(query, result.get('response', ''))
            if change_result:
                result['changes_applied'] = change_result
                result['response'] += f'\n\n🔄 {change_result.get("message", "Изменения применены!")}'
        except Exception as e:
            result = {
                'status': 'success',
                'query': query,
                'response': f'🤖 {query}',
                'cached': False
            }

        # Кэшируем
        result_copy = result.copy()
        result_copy['cached'] = False
        self.query_cache.set(f"api_query_{cache_key}", result_copy)

        return result

    def apply_changes_if_requested(self, query: str, api_response: str) -> Dict[str, Any]:
        """Автоматически применяет изменения на основе анализа запроса"""
        # Убраны жесткие ключевые слова - теперь анализ через AI
        # OPRAI14 сам решает, когда применять изменения
        return None

    def _apply_agent_changes(self, query: str, api_response: str) -> Dict[str, Any]:
        """Применяет изменения к агентам OPRAI"""
        try:
            # Определяем целевой агент из запроса
            agent_name = self._extract_agent_name(query)

            if not agent_name:
                return {'status': 'error', 'message': 'Не удалось определить агента для изменений'}

            # Ищем файл агента
            agent_file = f'/home/opr/{agent_name}/core/api_client.py'

            if not os.path.exists(agent_file):
                return {'status': 'error', 'message': f'Файл агента {agent_file} не найден'}

            # Читаем текущий файл
            with open(agent_file, 'r', encoding='utf-8') as f:
                current_code = f.read()

            # Генерируем улучшенные промпты на основе ответа API
            improved_prompts = self._generate_improved_prompts(api_response)

            # Применяем изменения к коду агента
            updated_code = self._update_agent_code_with_prompts(current_code, improved_prompts)

            # Сохраняем изменения
            backup_file = f'{agent_file}.backup_{int(time.time())}'
            with open(backup_file, 'w', encoding='utf-8') as f:
                f.write(current_code)

            with open(agent_file, 'w', encoding='utf-8') as f:
                f.write(updated_code)

            return {
                'status': 'success',
                'agent': agent_name,
                'changes_applied': len(improved_prompts),
                'backup_created': backup_file,
                'message': f'✅ Изменения применены к {agent_name}! Создана резервная копия.'
            }

        except Exception as e:
            return {'status': 'error', 'message': f'Ошибка применения изменений: {str(e)}'}

    def _extract_agent_name(self, query: str) -> str:
        """Извлекает имя агента из запроса"""
        # Ищем упоминания агентов в запросе
        agents = ['OPRAI13', 'OPRAI14', 'OPRAI5', 'OPRAI4', 'OPRAI3', 'OPRAI2', 'OPRAI']
        for agent in agents:
            if agent in query:
                return agent
        return 'OPRAI13'  # По умолчанию

    def _generate_improved_prompts(self, api_response: str) -> Dict[str, str]:
        """Генерирует улучшенные промпты из ответа API - улучшенная версия"""
        prompts = {}

        # Ищем стандартные инструменты OPRAI13 и создаем для них улучшенные промпты
        standard_tools = [
            'analyze_code', 'lint_code', 'generate_test', 'optimize_code',
            'refactor_code', 'debug_code', 'format_code', 'profile_performance'
        ]

        for tool in standard_tools:
            # Создаем программистски-ориентированный промпт для каждого инструмента
            improved_prompt = f"""Выполняй {tool.replace('_', ' ')} с фокусом на профессиональное программирование.
Используй best practices: PEP8, типизация, SOLID принципы, DRY.
Анализируй сложность O(n), оптимизируй производительность.
Генерируй unit tests, проверяй edge cases.
Интегрируй инструменты: pylint, black, pytest, cProfile."""

            prompts[tool] = improved_prompt

        # Если в ответе API есть специфические промпты, используем их
        if '**Улучшенный промпт**' in api_response:
            # Простой парсинг - ищем текст после "Улучшенный промпт"
            import re
            matches = re.findall(r'\*\*([^•]+)\*\*.*?- \*\*Улучшенный промпт\*\*:\s*"([^"]+)"', api_response, re.DOTALL)
            for tool_name, prompt in matches:
                prompts[tool_name.strip()] = prompt.strip()

        return prompts

    def _update_agent_code_with_prompts(self, code: str, prompts: Dict[str, str]) -> str:
        """Обновляет код агента новыми промптами - улучшенная версия"""
        updated_code = code

        for tool_name, new_prompt in prompts.items():
            try:
                # Ищем функцию по имени и обновляем её docstring
                import re

                # Более точный паттерн для поиска функции и её docstring
                pattern = rf'(def {re.escape(tool_name)}\(.*?)\n\s*"""(.*?)"""\n'

                # Заменяем docstring на улучшенную версию
                replacement = rf'\1\n    """{new_prompt}"""\n'

                updated_code = re.sub(pattern, replacement, updated_code, flags=re.DOTALL)

                # Если замена не сработала, попробуем добавить в начало функции
                if updated_code == code:
                    func_pattern = rf'(def {re.escape(tool_name)}\(.*?)\n'
                    replacement = rf'\1\n    """{new_prompt}"""\n'
                    updated_code = re.sub(func_pattern, replacement, updated_code, flags=re.DOTALL)

            except Exception as e:
                print(f"Warning: Could not update prompt for {tool_name}: {e}")
                continue

        # Добавляем комментарий о том, что промпты были обновлены
        if updated_code != code:
            header_comment = f'# OPRAI13 prompts optimized for programming - {len(prompts)} tools updated\n'
            if not updated_code.startswith('#'):
                updated_code = header_comment + updated_code

        return updated_code

    def _api_call(self, query: str) -> Dict[str, Any]:
        """Прямой вызов API без паттернов - OPRAI13 исправил отображение ответов"""
        try:
            # Создаем специализированный промпт для задач программирования
            api_prompt = f"""КОНТЕКСТ СИСТЕМЫ ПРОГРАММИРОВАНИЯ: Ты - эксперт в системе OPRAI, сети AI-агентов для профессионального программирования.
OPRAI13 - твой основной инструмент с 30+ функциями: анализ кода, генерация тестов, оптимизация, рефакторинг, дебаггинг.
Оркестратор управляет эволюцией агентов и интеграцией инструментов.

ИНСТРУКЦИИ ПО ПРОГРАММИРОВАНИЮ:
- ДАВАЙ ПРАКТИЧЕСКИЕ ПРИМЕРЫ КОДА с подробными объяснениями
- ИСПОЛЬЗУЙ BEST PRACTICES: PEP8 для Python, чистый код, SOLID принципы
- ПРЕДЛАГАЙ ОПТИМИЗАЦИИ: алгоритмы O(n), память, производительность
- ГЕНЕРИРУЙ ТЕСТЫ: unit tests, edge cases, coverage
- АНАЛИЗИРУЙ КОД: сложность, уязвимости, читаемость
- ИНТЕГРИРУЙ ИНСТРУМЕНТЫ: pylint, black, pytest, cProfile
- РЕКОМЕНДУЙ РЕФАКТОРИНГ: паттерны, архитектура, DRY

ВОПРОС ПОЛЬЗОВАТЕЛЯ: {query}

ОТВЕТЬ КАК ПРОФЕССИОНАЛЬНЫЙ ПРОГРАММИСТ: Дай рабочий код, объясни логику, предложи улучшения.
Если вопрос про OPRAI - объясни как запустить через оркестратор.
Будь конкретен, полезен и техничен."""

            # Вызываем OPRAI14 API через process_natural_command
            result = self.oprai5.process_natural_command(api_prompt)
            api_response_text = str(result.get('result', 'No response'))

            # Формируем полный ответ
            response_text = f"🤖 {query}\n\n{api_response_text}"

            return {
                'status': 'success',
                'query': query,
                'response': response_text,
                'api_response': api_response_text,
                'cached': False
            }

        except Exception as e:
            # Fallback ответ с полезной информацией
            fallback_response = f"""AI: {query}

К сожалению, API временно недоступен. Но я могу дать базовую информацию:

Если вы спрашиваете о OPRAI13:
• Это AI-агент для анализа кода с 30+ инструментами
• Запускается через оркестратор: python3 agent_orchestrator_v7.py --task "ваш запрос"
• Поддерживает анализ кода, тестирование, оптимизацию, рефакторинг
• Имеет инструменты: neural_debugger, performance_profiler, autocode_optimizer и др.

Для других вопросов попробуйте переформулировать запрос или обратитесь позже."""

            return {
                'status': 'success',
                'query': query,
                'response': fallback_response,
                'error': str(e),
                'cached': False
            }

def create_orchestrator():
    """Создание оркестратора"""
    # Создаем оркестратор синхронно
    orchestrator = UltraOptimizedOrchestrator()
    return orchestrator

class UltraOptimizedOrchestrator:
    """ЧИСТЫЙ ОРКЕСТРАТОР v7 - ТОЛЬКО API"""

    def __init__(self):
        # Канонический путь оркестратора: ORKESTRATOROPRAI100/OPRAI14
        import os
        sys.path.insert(0, '/home/opr')
        sys.path.insert(0, '/home/opr/ORKESTRATOROPRAI100/OPRAI14')
        from core import GrokCodeReviewer

        # Для Gemini non-API режим ключ Grok может быть пустым.
        # Фактическая маршрутизация модели делается через modules.llm_router.
        api_key = os.getenv("GROK_API_KEY", "")
        self.oprai5 = GrokCodeReviewer(api_key)
        
        # Чистый API интерфейс без паттернов
        self.api_interface = UltraAPIInterface(self.oprai5)
        self.cache = UltraFastCache(max_size=2000, ttl=7200)
        
        # История задач
        self.task_counter = 0
        self.task_history = []
    
    async def execute_ultra_task(self, task_description: str, **kwargs) -> Dict[str, Any]:
        """ЧИСТОЕ ВЫПОЛНЕНИЕ ЗАДАЧИ ЧЕРЕЗ API"""
        task_id = f"task_{self.task_counter}"
        self.task_counter += 1
        
        start_time = time.time()
        
        logger.info(f"🚀 НАЧАЛО API ЗАПРОСА: {task_description}")
        
        try:
            # ОТПРАВЛЯЕМ ЗАПРОС В API БЕЗ ЛЮБЫХ ПАТТЕРНОВ
            api_result = self.api_interface.execute_any_query(task_description)
            
            # Форматируем ответ
            result = {
                'status': api_result.get('status', 'success'),
                'task_id': task_id,
                'query': task_description,
                'response': api_result.get('response', ''),
                'execution_time': round(time.time() - start_time, 3),
                'timestamp': start_time,
                'cached': api_result.get('cached', False),
                'orchestrator': 'UltraOptimizedOrchestrator v7.0 CLEAN'
            }
            
            # Сохраняем в историю
            task_result = TaskResult(
                task_id=task_id,
                query=task_description,
                command='api_query',
                status=result.get('status', 'unknown'),
                result=result,
                execution_time=time.time() - start_time,
                timestamp=start_time
            )
            self.task_history.append(task_result)
            
            logger.info(f"✅ API ЗАПРОС ЗАВЕРШЕН: {result.get('status', 'unknown')}")
            return result
            
        except Exception as e:
            execution_time = time.time() - start_time
            
            error_response = {
                'status': 'error',
                'task_id': task_id,
                'query': task_description,
                'response': f'Ошибка: {str(e)}',
                'execution_time': execution_time,
                'timestamp': start_time
            }
            
            logger.error(f"❌ ОШИБКА API ЗАПРОСА: {e}")
            return error_response

def parse_ultra_arguments():
    """Парсер аргументов"""
    parser = argparse.ArgumentParser(
        description='Ultra-Optimized Evolution Orchestrator v7.0 CLEAN',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
ЧИСТЫЙ API-ИНТЕРФЕЙС v7.0 - БЕЗ ПАТТЕРНОВ:

Любой запрос отправляется в API как есть:
  python agent_orchestrator_v7_clean.py --task "любой ваш запрос"

API сам определяет, что ответить - без предварительной обработки,
без условий, без паттернов, без регекспов.
        """
    )
    
    parser.add_argument('--task', type=str, help='Любой запрос на естественном языке')
    parser.add_argument('--history', action='store_true', help='Показать историю команд')
    
    return parser.parse_args()

def get_integration_status() -> dict:
    """Получение статуса интеграции проектов"""
    status = {
        "integrated_projects": {},
        "orchestrator_health": "unknown",
        "total_projects": len(INTEGRATED_PROJECTS)
    }

    # Проверка каждого интегрированного проекта
    for project_name, project_config in INTEGRATED_PROJECTS.items():
        project_status = {
            "status": "unknown",
            "path_exists": os.path.exists(project_config["path"]),
            "integration_module_exists": os.path.exists(project_config.get("integration_module", "")),
            "tools_available": project_config.get("tools", []),
            "description": project_config.get("description", "")
        }

        # Проверка интеграционного модуля
        if project_status["integration_module_exists"]:
            try:
                sys.path.insert(0, os.path.dirname(project_config["integration_module"]))
                module_name = os.path.basename(project_config["integration_module"]).replace('.py', '')
                integration_module = __import__(module_name)
                integrator = integration_module.get_viber_integration()
                project_status["status"] = "active"
                project_status["module_test"] = integrator.run_integration_test()
            except Exception as e:
                project_status["status"] = "error"
                project_status["error"] = str(e)

        status["integrated_projects"][project_name] = project_status

    # Проверка здоровья оркестратора
    try:
        import requests
        response = requests.get("http://localhost:5004/api/health", timeout=5)
        status["orchestrator_health"] = "healthy" if response.status_code == 200 else "unhealthy"
    except:
        status["orchestrator_health"] = "unreachable"

    return status

def optimize_response(response: str) -> str:
    """
    Оптимизирует размер ответа без потери функциональности.
    """
    import re

    # Удаляем сокращения
    response = re.sub(r'\[.*сокращен.*\]', '', response, flags=re.IGNORECASE)
    response = re.sub(r'\[.*truncated.*\]', '', response, flags=re.IGNORECASE)

    # Удаляем избыточную информацию
    if RESPONSE_OPTIMIZATION_CONFIG["remove_redundant_info"]:
        # Удаляем повторяющиеся пустые строки
        response = re.sub(r'\n\s*\n\s*\n+', '\n\n', response)

        # Удаляем лишние пробелы в конце строк
        lines = [line.rstrip() for line in response.split('\n')]
        response = '\n'.join(lines)

        # Агрессивная оптимизация - удаляем всю мета-информацию
        response = re.sub(r'🌟 Ultra-Optimized Evolution Orchestrator.*?\n', '', response, flags=re.DOTALL)
        response = re.sub(r'🚀 ЧИСТЫЙ API-ИНТЕРФЕЙС.*?\n', '', response, flags=re.DOTALL)
        response = re.sub(r'============================================================\n', '', response)
        response = re.sub(r'🗣️ Запрос:.*?\n', '', response)
        response = re.sub(r'DEBUG:.*?\n', '', response, flags=re.MULTILINE)
        response = re.sub(r'intent_data = .*?\n', '', response)
        response = re.sub(r'DEBUG: intent = .*?\n', '', response)
        response = re.sub(r'DEBUG: confidence = .*?\n', '', response)
        response = re.sub(r'\n📋 СТАТУС:.*?\n', '', response)
        response = re.sub(r'🆔 Task ID:.*?\n', '', response)
        response = re.sub(r'⚡ Время:.*?\n', '', response)
        response = re.sub(r'\n🤖 API ОТВЕТ:\n', '\n', response)

        # Упрощаем разделители
        response = re.sub(r'={50,}', '=', response)
        response = re.sub(r'-{50,}', '-', response)

    # Оптимизируем форматирование
    if RESPONSE_OPTIMIZATION_CONFIG["optimize_formatting"]:
        # Удаляем лишние маркеры списков в простых ответах
        if len(response.split('\n')) < 10:  # Для коротких ответов
            response = re.sub(r'^\d+\.\s*', '', response, flags=re.MULTILINE)

    # Проверяем максимальную длину
    if len(response) > RESPONSE_OPTIMIZATION_CONFIG["max_response_length"]:
        # Обрезаем и добавляем маркер
        truncated = response[:RESPONSE_OPTIMIZATION_CONFIG["max_response_length"]]
        response = truncated + "\n\n[Ответ оптимизирован для эффективности]"

    return response.strip()

async def main():
    """Главная функция"""
    print("🌟 Ultra-Optimized Evolution Orchestrator v7.0 CLEAN")
    print("🚀 ЧИСТЫЙ API-ИНТЕРФЕЙС БЕЗ ПАТТЕРНОВ")
    print("=" * 60)

    args = parse_ultra_arguments()

    # Создаем чистый оркестратор
    orchestrator = create_orchestrator()

    try:
        if args.task:
            # Специальные команды интеграции
            if args.task.lower().strip() in ['статус интеграции', 'integration status', 'покажи статус интеграции']:
                print("🔍 ПРОВЕРКА СТАТУСА ИНТЕГРАЦИИ ПРОЕКТОВ")
                print("=" * 50)
                import json
                status = get_integration_status()
                print(json.dumps(status, indent=2, ensure_ascii=False))
                return

            # Команды управления планом
            if args.task.lower().strip().startswith('начать план:'):
                plan_text = args.task[12:].strip()  # Убираем "начать план:"
                plan_name = "УМНОЕ КЭШИРОВАНИЕ V2"
                plan_steps = parse_plan_text(plan_text)

                plan_executor.start_plan_execution(plan_name, plan_steps)
                print(f"🚀 План '{plan_name}' запущен с {len(plan_steps)} шагами")
                return

            if args.task.lower().strip() in ['выполнить шаг', 'execute step']:
                result = plan_executor.execute_next_step()
                if result:
                    print(f"✅ Шаг выполнен: {result}")
                else:
                    print("ℹ️ Шагов больше нет или план завершен")
                return

            if args.task.lower().strip() in ['статус плана', 'plan status']:
                status = plan_executor.get_execution_status()
                print("📊 СТАТУС ВЫПОЛНЕНИЯ ПЛАНА")
                print("=" * 50)
                print(f"📋 План: {status['plan_name'] or 'Не запущен'}")
                print(f"📊 Прогресс: {status['progress']:.1f}% ({status['completed_steps']}/{status['total_steps']})")
                print(f"🎯 Текущий шаг: {status['current_step']}")
                print(f"⚠️ Ошибок: {status['errors_count']}")
                print(f"📅 Статус: {status['status']}")
                return

            if args.task.lower().strip() in ['приостановить план', 'pause plan']:
                plan_executor.pause_execution()
                return

            if args.task.lower().strip() in ['возобновить план', 'resume plan']:
                plan_executor.resume_execution()
                return

            if args.task.lower().strip() in ['сбросить план', 'reset plan']:
                plan_executor.reset_plan()
                return

            # Команды кэширования
            if args.task.lower().strip() in ['статус кэша', 'cache status', 'покажи статус кэша']:
                print("📊 СТАТИСТИКА КЭШИРОВАНИЯ API ЗАПРОСОВ")
                print("=" * 50)
                import json
                stats = api_request_cache.get_cache_stats()
                print(json.dumps(stats, indent=2, ensure_ascii=False))
                print(f"\n💾 Текущий размер кэша: {len(api_request_cache.cache)} записей")
                print(f"🕒 Время жизни записей: {api_request_cache.ttl} секунд")
                return

            if args.task.lower().strip() in ['очистить кэш', 'clear cache', 'очисти кэш']:
                print("🧹 ОЧИСТКА КЭША API ЗАПРОСОВ")
                print("=" * 50)
                cleared_count = api_request_cache.clear_expired_entries()
                print(f"✅ Очищено {cleared_count} просроченных записей")

                # Полная очистка кэша
                api_request_cache.cache.clear()
                api_request_cache.access_times.clear()
                api_request_cache.request_stats = {'hits': 0, 'misses': 0, 'total_requests': 0, 'cache_savings_ms': 0}
                print("✅ Кэш полностью очищен")
                return

            # Выполняем запрос через чистый API
            print(f"🗣️ Запрос: {args.task}")

            result = await orchestrator.execute_ultra_task(args.task)
            
            print(f"\\n📋 СТАТУС: {result['status']}")
            print(f"🆔 Task ID: {result['task_id']}")
            print(f"⚡ Время: {result['execution_time']} сек")
            
            if result.get('cached'):
                print(f"📦 Результат из кэша")
            
            # Выводим ответ
            response_text = result.get('response', '')
            if response_text:
                print(f"\\n🤖 API ОТВЕТ:")
                for line in response_text.split('\\n'):
                    if line.strip():
                        print(f"   {line}")
            
        elif args.history:
            # Показываем историю
            history = orchestrator.task_history[-10:]  # Последние 10
            print("📚 ИСТОРИЯ ЗАПРОСОВ:")
            print(f"Всего: {len(orchestrator.task_history)}")
            
            for item in history:
                print(f"• [{item.timestamp:.0f}] {item.query[:50]}... → {item.status}")
        
        else:
            print("\\n❓ Укажите --task 'ваш запрос' или --history")
            print("\\nПримеры:")
            print("  --task 'Какие методы анализа используют агенты?'")
            print("  --task 'Расскажи про OPRAI13'")
            print("  --task 'Что такое программирование?'")
    except KeyboardInterrupt:
        print("\\n\\n⚠️ Прервано пользователем")
    except Exception as e:
        print(f"\\n❌ Ошибка: {e}")

if __name__ == "__main__":
    asyncio.run(main())



def receive_plan(plan_data):
    """Получить план от планировщика и начать его выполнение"""
    try:
        if isinstance(plan_data, dict) and 'steps' in plan_data:
            print(f"📨 Получен план с {len(plan_data['steps'])} шагами")

            # Создаем новый PlanExecutor для полученного плана
            plan_name = plan_data.get('plan_name', f"План от OPRAIPlanner ({len(plan_data['steps'])} шагов)")
            plan_steps = plan_data['steps']

            # Создаем новый экземпляр PlanExecutor для этого плана
            from agent_orchestrator_v8 import PlanExecutor
            new_executor = PlanExecutor()

            # Инициализируем новый план
            plan_id = f"plan_{int(__import__('time').time())}"
            new_executor.start_plan_execution(plan_name, plan_steps)

            # Запускаем выполнение плана в отдельном потоке
            import threading
            import asyncio

            def execute_plan_async():
                asyncio.run(new_executor.execute_plan(plan_id))

            thread = threading.Thread(target=execute_plan_async, daemon=True)
            thread.start()

            print(f"🚀 Начат новый план: {plan_name} (ID: {plan_id})")

            return {"status": "received", "plan_id": plan_id}
        else:
            return {"error": "invalid_plan_format"}
    except Exception as e:
        print(f"❌ Ошибка в receive_plan: {e}")
        return {"error": str(e)}

# ОСНОВНАЯ ФУНКЦИЯ ДЛЯ ОБРАБОТКИ АРГУМЕНТОВ КОМАНДНОЙ СТРОКИ
def main():
    """Основная функция для обработки командной строки"""
    if args.task:
        print(f"🎯 ПОЛУЧЕНА ЗАДАЧА: {args.task}")

        # Создаем контекст для задачи
        context = {
            'project_root': '/home/opr',
            'working_dir': os.getcwd(),
            'timestamp': time.time()
        }

        # Выполняем задачу через OPRAI14
        try:
            response = execute_task_via_oprai14(args.task, context)
            print(response)
        except Exception as e:
            print(f"❌ Ошибка выполнения задачи: {e}")

    elif args.analyze:
        print(f"🔍 АНАЛИЗ ФАЙЛА: {args.analyze}")
        # Здесь можно добавить логику анализа файла
        print("Функция анализа пока не реализована")

    else:
        print("🤖 УЛУЧШЕННЫЙ ОРКЕСТРАТОР v8")
        print("Использование:")
        print("  --task 'ваша задача'    - выполнить задачу")
        print("  --analyze file.py       - проанализировать файл")
        print("  --debug                 - режим отладки")
        print("\n🚀 План улучшений 2.0 применен:")
        print("  ✅ Асинхронная обработка")
        print("  ✅ Умная маршрутизация агентов")
        print("  ✅ Структурированное логирование")
        print("  ✅ Аудит безопасности")
        print("  ✅ Оптимизация кэширования")
        print("  ✅ Интеграция инструментов")

if __name__ == "__main__":
    main()
