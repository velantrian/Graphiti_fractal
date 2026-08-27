#!/usr/bin/env python3
"""
Скрипт для тестирования стабильности /chat API.
Отправляет последовательные и параллельные запросы для выявления проблем.
"""

import asyncio
import aiohttp
import json
import time
import sys
from datetime import datetime
from typing import List, Dict, Any

API_BASE = "http://localhost:8001"

async def test_chat_request(session: aiohttp.ClientSession, message: str, user_id: str = "sergey", request_id: str = None) -> Dict[str, Any]:
    """Отправить один запрос к /chat и вернуть результат."""
    payload = {
        "message": message,
        "user_id": user_id
    }

    start_time = time.time()
    try:
        async with session.post(f"{API_BASE}/chat", json=payload, timeout=30) as response:
            end_time = time.time()
            duration_ms = (end_time - start_time) * 1000

            result = {
                "request_id": request_id,
                "message": message[:50] + "..." if len(message) > 50 else message,
                "status_code": response.status,
                "duration_ms": round(duration_ms, 2),
                "success": response.status == 200
            }

            if response.status == 200:
                try:
                    data = await response.json()
                    result["reply_length"] = len(data.get("reply", ""))
                    result["degraded"] = data.get("timing", {}).get("degraded_mode", False)
                    result["fallback"] = data.get("timing", {}).get("fallback_mode", False)
                except:
                    result["json_error"] = True
                    result["success"] = False
            else:
                try:
                    error_text = await response.text()
                    result["error"] = error_text[:200]
                except:
                    result["error"] = "Failed to read error response"

            return result

    except Exception as e:
        end_time = time.time()
        duration_ms = (end_time - start_time) * 1000
        return {
            "request_id": request_id,
            "message": message[:50] + "..." if len(message) > 50 else message,
            "status_code": None,
            "duration_ms": round(duration_ms, 2),
            "success": False,
            "error": str(e)
        }

async def test_sequential_requests(num_requests: int = 10) -> List[Dict[str, Any]]:
    """Тестирование последовательных запросов."""
    print(f"🧪 Testing {num_requests} sequential /chat requests...")

    messages = [
        "Как меня зовут?",
        "Расскажи о себе",
        "Что ты умеешь?",
        "Какие у тебя воспоминания?",
        "Что ты знаешь о Марке?",
        "Расскажи о проекте Fractal Memory",
        "Как работает память?",
        "Что такое Graphiti?",
        "Расскажи о Neo4j",
        "Какие технологии ты используешь?"
    ]

    results = []

    async with aiohttp.ClientSession() as session:
        for i in range(num_requests):
            message = messages[i % len(messages)]
            request_id = f"seq-{i+1:02d}"

            print(f"📤 Request {i+1:2d}: {message[:30]}...")
            result = await test_chat_request(session, message, request_id=request_id)
            results.append(result)

            if result["success"]:
                print(f"✅ OK ({result['duration_ms']}ms, reply: {result['reply_length']} chars)")
            else:
                print(f"❌ FAIL: {result.get('status_code', 'ERROR')} - {result.get('error', 'Unknown')}")

            # Небольшая пауза между запросами
            await asyncio.sleep(0.5)

    return results

async def test_parallel_requests(num_concurrent: int = 5, num_requests: int = 15) -> List[Dict[str, Any]]:
    """Тестирование параллельных запросов."""
    print(f"🧪 Testing {num_requests} requests with {num_concurrent} concurrent...")

    messages = [
        "Привет",
        "Как дела?",
        "Что нового?",
        "Расскажи о себе",
        "Какие планы?",
        "Что ты думаешь о ИИ?",
        "Расскажи о памяти",
        "Как работает обучение?",
        "Что такое сознание?",
        "Какие книги ты читал?",
        "Расскажи о будущем",
        "Что такое счастье?",
        "Как решать проблемы?",
        "Расскажи о творчестве",
        "Какие цели у тебя?"
    ]

    async def worker(worker_id: int, session: aiohttp.ClientSession) -> List[Dict[str, Any]]:
        worker_results = []
        for i in range(num_requests // num_concurrent + (1 if worker_id < num_requests % num_concurrent else 0)):
            message = messages[(worker_id + i * num_concurrent) % len(messages)]
            request_id = f"par-{worker_id+1}-{i+1}"

            result = await test_chat_request(session, message, request_id=request_id)
            worker_results.append(result)

            # Пауза чтобы не перегружать
            await asyncio.sleep(0.2)

        return worker_results

    results = []

    async with aiohttp.ClientSession() as session:
        # Запускаем несколько воркеров параллельно
        tasks = [worker(i, session) for i in range(num_concurrent)]
        worker_results = await asyncio.gather(*tasks)

        # Собираем все результаты
        for worker_result in worker_results:
            results.extend(worker_result)

    # Сортируем по request_id для читаемого вывода
    results.sort(key=lambda x: x.get("request_id", ""))

    for result in results:
        if result["success"]:
            print(f"✅ {result['request_id']}: OK ({result['duration_ms']}ms)")
        else:
            print(f"❌ {result['request_id']}: FAIL - {result.get('error', 'Unknown')}")

    return results

async def test_health_check() -> Dict[str, Any]:
    """Проверка здоровья системы."""
    print("🏥 Checking system health...")

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{API_BASE}/health", timeout=10) as response:
                if response.status == 200:
                    data = await response.json()
                    print(f"✅ Health: {data}")
                    return {"healthy": True, "data": data}
                else:
                    error = await response.text()
                    print(f"❌ Health check failed: {response.status} - {error}")
                    return {"healthy": False, "status": response.status, "error": error}
    except Exception as e:
        print(f"❌ Health check error: {e}")
        return {"healthy": False, "error": str(e)}

async def main():
    """Основная функция тестирования."""
    print("🚀 Fractal Memory Chat Stability Test")
    print("=" * 50)

    # Проверка здоровья системы
    health = await test_health_check()
    if not health.get("healthy"):
        print("⚠️  System health check failed, but continuing with tests...")

    print()

    # Тест последовательных запросов
    print("📊 SEQUENTIAL REQUESTS TEST")
    print("-" * 30)
    sequential_results = await test_sequential_requests(10)

    # Анализ результатов последовательного теста
    successful = sum(1 for r in sequential_results if r["success"])
    total_time = sum(r["duration_ms"] for r in sequential_results)
    avg_time = total_time / len(sequential_results)

    print("\n📈 Sequential Results:")
    print(f"   Total: {len(sequential_results)}")
    print(f"   Successful: {successful}")
    print(f"   Failed: {len(sequential_results) - successful}")
    print(f"   Avg time: {avg_time:.2f}ms")
    print(f"   Success rate: {successful / len(sequential_results) * 100:.1f}%")

    # Проверка на "первый ок, второй падает"
    first_success = sequential_results[0]["success"] if sequential_results else False
    second_success = sequential_results[1]["success"] if len(sequential_results) > 1 else False

    if first_success and not second_success:
        print("🔴 PATTERN DETECTED: First request OK, second failed!")
        for i, result in enumerate(sequential_results[:5]):
            print(f"   {i+1}: {result['success']} ({result.get('status_code', 'ERROR')})")
    elif successful == len(sequential_results):
        print("🟢 All sequential requests successful!")
    else:
        print("🟡 Some sequential requests failed")

    print()

    # Тест параллельных запросов
    print("📊 PARALLEL REQUESTS TEST")
    print("-" * 30)
    parallel_results = await test_parallel_requests(5, 15)

    # Анализ результатов параллельного теста
    successful_parallel = sum(1 for r in parallel_results if r["success"])
    total_time_parallel = sum(r["duration_ms"] for r in parallel_results)
    avg_time_parallel = total_time_parallel / len(parallel_results)

    print("\n📈 Parallel Results:")
    print(f"   Total: {len(parallel_results)}")
    print(f"   Successful: {successful_parallel}")
    print(f"   Failed: {len(parallel_results) - successful_parallel}")
    print(f"   Avg time: {avg_time_parallel:.2f}ms")
    print(f"   Success rate: {successful_parallel / len(parallel_results) * 100:.1f}%")
    # Проверка degraded mode
    degraded_count = sum(1 for r in parallel_results if r.get("degraded", False))
    fallback_count = sum(1 for r in parallel_results if r.get("fallback", False))

    if degraded_count > 0:
        print(f"   Degraded mode used: {degraded_count} times")
    if fallback_count > 0:
        print(f"   Fallback mode used: {fallback_count} times")

    # Финальный вердикт
    print()
    print("🎯 FINAL VERDICT")
    print("-" * 20)

    all_results = sequential_results + parallel_results
    total_successful = sum(1 for r in all_results if r["success"])
    total_requests = len(all_results)

    if total_successful == total_requests:
        print("🟢 ALL TESTS PASSED - Chat is stable!")
    elif total_successful >= total_requests * 0.8:
        print("🟡 MOSTLY STABLE - Some failures but acceptable")
    else:
        print("🔴 UNSTABLE - Many failures detected")

    print(f"   Overall success rate: {total_successful / total_requests * 100:.1f}%")
    # Сохраняем детальные результаты
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    results_file = f"chat_stability_test_{timestamp}.json"

    with open(results_file, 'w', encoding='utf-8') as f:
        json.dump({
            "timestamp": timestamp,
            "health_check": health,
            "sequential_test": {
                "results": sequential_results,
                "summary": {
                    "total": len(sequential_results),
                    "successful": successful,
                    "failed": len(sequential_results) - successful,
                    "avg_time_ms": round(avg_time, 2)
                }
            },
            "parallel_test": {
                "results": parallel_results,
                "summary": {
                    "total": len(parallel_results),
                    "successful": successful_parallel,
                    "failed": len(parallel_results) - successful_parallel,
                    "avg_time_ms": round(avg_time_parallel, 2),
                    "degraded_count": degraded_count,
                    "fallback_count": fallback_count
                }
            },
            "overall": {
                "total_requests": total_requests,
                "successful": total_successful,
                "success_rate": round(total_successful / total_requests * 100, 1)
            }
        }, f, ensure_ascii=False, indent=2)

    print(f"📄 Detailed results saved to: {results_file}")

if __name__ == "__main__":
    asyncio.run(main())