#!/usr/bin/env python3
"""
Диагностика проблем с загрузкой документов.
Запуск: python scripts/diagnose.py
"""

import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv()

async def check_openai():
    """Проверка OpenAI API"""
    print("\n=== Проверка OpenAI API ===")
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        print("❌ OPENAI_API_KEY не установлен!")
        return False
    
    print("✓ OPENAI_API_KEY установлен (значение скрыто)")
    
    try:
        from openai import AsyncOpenAI
        client = AsyncOpenAI(api_key=api_key)
        resp = await client.embeddings.create(
            input=["test"],
            model="text-embedding-3-small"
        )
        print(f"✓ OpenAI embeddings работают! Размерность: {len(resp.data[0].embedding)}")
        return True
    except Exception as e:
        print(f"❌ Ошибка OpenAI: {type(e).__name__}: {e}")
        return False

async def check_neo4j():
    """Проверка Neo4j"""
    print("\n=== Проверка Neo4j ===")
    uri = os.getenv("NEO4J_URI")
    user = os.getenv("NEO4J_USER")
    password = os.getenv("NEO4J_PASSWORD")
    
    if not all([uri, user, password]):
        print("❌ NEO4J_URI/USER/PASSWORD не установлены!")
        return False
    
    print(f"✓ Neo4j настройки: {uri}, user={user}")
    
    try:
        from neo4j import AsyncGraphDatabase
        driver = AsyncGraphDatabase.driver(uri, auth=(user, password))
        async with driver.session() as session:
            result = await session.run("RETURN 1 AS test")
            record = await result.single()
            print(f"✓ Neo4j подключение работает! Тест: {record['test']}")
        await driver.close()
        return True
    except Exception as e:
        print(f"❌ Ошибка Neo4j: {type(e).__name__}: {e}")
        return False

async def check_graphiti():
    """Проверка Graphiti add_episode"""
    print("\n=== Проверка Graphiti add_episode ===")
    
    try:
        from core.graphiti_client import get_graphiti_client
        from datetime import datetime, timezone
        
        client = get_graphiti_client()
        graphiti = await client.ensure_ready()
        print("✓ Graphiti клиент инициализирован")
        
        # Пробуем добавить тестовый эпизод
        print("  Добавляем тестовый эпизод...")
        episode_result = await graphiti.add_episode(
            name="Diagnostic Test",
            episode_body="This is a diagnostic test episode",
            source_description="diagnostic",
            reference_time=datetime.now(timezone.utc),
        )
        print("✓ add_episode работает!")
        
        # Удаляем тестовый эпизод
        driver = graphiti.driver
        episode_uuid = getattr(getattr(episode_result, "episode", None), "uuid", None)
        if not episode_uuid:
            raise RuntimeError("Cannot safely clean diagnostic episode without exact UUID")
        await driver.execute_query(
            "MATCH (e:Episodic {uuid: $uuid}) DETACH DELETE e", uuid=episode_uuid
        )
        print("✓ Тестовый эпизод удалён")
        return True
        
    except Exception as e:
        import traceback
        print(f"❌ Ошибка Graphiti: {type(e).__name__}: {e}")
        print(traceback.format_exc())
        return False

async def main():
    print("=" * 60)
    print("ДИАГНОСТИКА FRACTAL MEMORY")
    print("=" * 60)
    
    openai_ok = await check_openai()
    neo4j_ok = await check_neo4j()
    
    if openai_ok and neo4j_ok:
        graphiti_ok = await check_graphiti()
    else:
        print("\n⚠️ Пропускаем проверку Graphiti из-за предыдущих ошибок")
        graphiti_ok = False
    
    print("\n" + "=" * 60)
    print("РЕЗУЛЬТАТ:")
    print(f"  OpenAI:   {'✓' if openai_ok else '❌'}")
    print(f"  Neo4j:    {'✓' if neo4j_ok else '❌'}")
    print(f"  Graphiti: {'✓' if graphiti_ok else '❌'}")
    print("=" * 60)
    
    if not all([openai_ok, neo4j_ok, graphiti_ok]):
        print("\n💡 Рекомендации:")
        if not openai_ok:
            print("  - Проверьте OPENAI_API_KEY в .env файле")
            print("  - Убедитесь, что ключ действителен и имеет баланс")
        if not neo4j_ok:
            print("  - Проверьте, что Neo4j запущен (docker-compose up -d)")
            print("  - Проверьте NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD в .env")

if __name__ == "__main__":
    asyncio.run(main())
