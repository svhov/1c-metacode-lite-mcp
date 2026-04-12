"""Benchmark pipeline for 1C Litecode MCP embedding search quality.

Usage:
    python bench_pipeline.py                        # DO AME on port 6005
    python bench_pipeline.py --port 6001 --server ERP_MAIN
"""
import argparse
import json
import statistics
import threading
import time
import sseclient
import requests

# ---------------------------------------------------------------------------
# Test suites (from update_2.txt section 7)
# ---------------------------------------------------------------------------

TESTS_DO_AME = [
    ("search_objects",  "справочник видов операций с документами",
     ["АМЕ_ВидыОпераций"]),
    ("search_routines", "создать задачу для исполнителя по документу",
     ["ПолучитьЗадачуИсполнителя", "СоздатьЗадачу"]),
    ("search_all",      "архив документов и хранение файлов",
     ["АМЕ_АрхивДокументов"]),
    ("search_routines", "отправить уведомление пользователю",
     []),
    ("search_objects",  "регистр для хранения статусов согласования",
     []),
    ("search_all",      "работа с письмами и входящей корреспонденцией",
     ["РеестрВходящихПисем", "АМЭ_ОтчётПоВходящимПисьмам"]),
    ("search_routines", "получить список подчиненных сотрудников",
     []),
    ("search_objects",  "регистр с графиками сотрудников",
     ["АМЕ_ГрафикиОтпусков"]),
]

TESTS_ERP_MAIN = [
    ("search_routines", "получить остатки товаров на складе",
     []),
    ("search_objects",  "справочник контраг��нтов и партнеров",
     ["Контрагенты", "Партнеры"]),
    ("search_routines", "расчет скидок и наценок для клиента",
     ["ПрименитьРезультатРасчета"]),
    ("search_objects",  "документ для оформления продажи покупателю",
     ["РеализацияТоваровУслуг"]),
    ("search_all",      "печать ценников и этикеток на товары",
     ["ДанныеДляПечатиЦенников", "ПечатьЭтикетокИЦенников"]),
    ("search_routines", "отправить электронное письмо с вложением",
     []),
    ("search_objects",  "регистр для хранения цен номенклатуры",
     ["ЦеныНоменклатуры"]),
]

SUITES = {"DO_AME": TESTS_DO_AME, "ERP_MAIN": TESTS_ERP_MAIN}


# ---------------------------------------------------------------------------
# MCP SSE transport
# ---------------------------------------------------------------------------

def call_tool(base_url, tool_name, query_dict, timeout=25):
    resp = requests.get(f"{base_url}/sse", stream=True, timeout=30)
    client = sseclient.SSEClient(resp)
    session_id = None
    results = []
    initialized = threading.Event()
    got_result = threading.Event()

    def listen():
        for event in client.events():
            if event.event == "endpoint":
                nonlocal session_id
                session_id = event.data.split("session_id=")[-1]
            elif event.event == "message":
                data = json.loads(event.data)
                if data.get("id") == "init":
                    initialized.set()
                elif data.get("id") == "call":
                    results.append(data)
                    got_result.set()
                    return

    t = threading.Thread(target=listen, daemon=True)
    t.start()
    for _ in range(50):
        if session_id:
            break
        time.sleep(0.1)
    if not session_id:
        return None

    post = f"{base_url}/messages/?session_id={session_id}"
    requests.post(post, json={
        "jsonrpc": "2.0", "id": "init", "method": "initialize",
        "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                   "clientInfo": {"name": "bench", "version": "1.0"}},
    }, timeout=10)
    initialized.wait(timeout=10)
    requests.post(post, json={
        "jsonrpc": "2.0", "method": "notifications/initialized",
    }, timeout=10)
    time.sleep(0.3)
    requests.post(post, json={
        "jsonrpc": "2.0", "id": "call", "method": "tools/call",
        "params": {"name": tool_name,
                   "arguments": {"query": json.dumps(query_dict, ensure_ascii=False)}},
    }, timeout=10)
    got_result.wait(timeout=timeout)
    try:
        resp.close()
    except Exception:
        pass
    return results[0] if results else None


def extract_results(raw):
    if not raw:
        return []
    content = raw.get("result", {}).get("content", [])
    for item in content:
        if item.get("type") == "text":
            parsed = json.loads(item["text"])
            for key in ("objects", "routines", "results"):
                if key in parsed:
                    return parsed[key]
    return []


def match_expected(name, expected_list):
    """Check if name matches any expected result (case-insensitive substring)."""
    name_low = (name or "").lower()
    for exp in expected_list:
        if exp.lower() in name_low:
            return True
    return False


# ---------------------------------------------------------------------------
# Run benchmark
# ---------------------------------------------------------------------------

def run_suite(base_url, tests, server_name):
    print(f"\n{'='*90}")
    print(f"  BENCHMARK: {server_name} ({base_url})")
    print(f"  {len(tests)} queries, {sum(1 for _,_,e in tests if e)} with ground truth")
    print(f"{'='*90}\n")

    hits_at_1 = 0
    hits_at_3 = 0
    countable = 0
    top1_scores = []
    hit_scores = []
    miss_scores = []

    for i, (op, text, expected) in enumerate(tests, 1):
        query = {"op": op, "text": text, "limit": 7}
        raw = call_tool(base_url, "search_by_embedding", query)
        items = extract_results(raw)

        has_gt = bool(expected)
        if has_gt:
            countable += 1

        # Find position of expected result
        found_pos = None
        if has_gt and items:
            for j, item in enumerate(items):
                if match_expected(item.get("name", ""), expected):
                    found_pos = j + 1
                    break

        top1_score = items[0].get("score", 0) if items else 0
        top1_scores.append(top1_score)

        if has_gt:
            if found_pos == 1:
                hits_at_1 += 1
                hits_at_3 += 1
                hit_scores.append(top1_score)
                status = "HIT@1"
            elif found_pos and found_pos <= 3:
                hits_at_3 += 1
                miss_scores.append(top1_score)
                status = f"HIT@{found_pos}"
            elif found_pos:
                miss_scores.append(top1_score)
                status = f"FOUND@{found_pos}"
            else:
                miss_scores.append(top1_score)
                status = "MISS"
        else:
            status = "---"

        # Print query result
        top1_name = items[0].get("name", "?") if items else "NO RESULTS"
        print(f"  #{i:<2} [{op:<17}] {status:<8} score={top1_score:<7.4f} {top1_name}")
        if has_gt:
            exp_str = " | ".join(expected)
            print(f"       expected: {exp_str}")
        # top-3
        if items:
            t3 = ", ".join(f"{r.get('name','?')}({r.get('score',0):.2f})" for r in items[:3])
            print(f"       top-3: {t3}")
        print()

    # Summary
    print(f"{'='*90}")
    print(f"  RESULTS: {server_name}")
    print(f"{'='*90}")
    p1 = 100 * hits_at_1 / countable if countable else 0
    p3 = 100 * hits_at_3 / countable if countable else 0
    print(f"  Precision@1: {hits_at_1}/{countable} ({p1:.0f}%)")
    print(f"  Precision@3: {hits_at_3}/{countable} ({p3:.0f}%)")
    print()

    if top1_scores:
        print(f"  Score distribution (top-1):")
        print(f"    min={min(top1_scores):.4f}  max={max(top1_scores):.4f}  "
              f"median={statistics.median(top1_scores):.4f}  "
              f"mean={statistics.mean(top1_scores):.4f}")
    if hit_scores:
        print(f"  Hit scores:  mean={statistics.mean(hit_scores):.4f}")
    if miss_scores:
        print(f"  Miss scores: mean={statistics.mean(miss_scores):.4f}")

    # Target check
    print()
    target_p1 = 70
    target_p3 = 85
    ok1 = "PASS" if p1 >= target_p1 else "FAIL"
    ok3 = "PASS" if p3 >= target_p3 else "FAIL"
    print(f"  Target P@1 >= {target_p1}%: {ok1} ({p1:.0f}%)")
    print(f"  Target P@3 >= {target_p3}%: {ok3} ({p3:.0f}%)")
    print(f"{'='*90}\n")

    return {"p1": p1, "p3": p3, "hits_at_1": hits_at_1, "countable": countable}


def main():
    parser = argparse.ArgumentParser(description="Benchmark 1C Litecode embedding search")
    parser.add_argument("--port", type=int, default=6005, help="MCP server port")
    parser.add_argument("--server", default="DO_AME",
                        choices=list(SUITES.keys()),
                        help="Test suite to run")
    args = parser.parse_args()

    base_url = f"http://localhost:{args.port}"
    tests = SUITES[args.server]

    # Quick connectivity check
    try:
        r = requests.get(f"{base_url}/sse", stream=True, timeout=5)
        r.close()
    except Exception:
        print(f"ERROR: Cannot connect to {base_url}")
        return

    run_suite(base_url, tests, args.server)


if __name__ == "__main__":
    main()
