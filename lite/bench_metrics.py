"""Extended metrics system for 1C Litecode MCP search quality + resource efficiency.

Metrics framework:
  1. QUALITY METRICS — how good are search results
  2. RESOURCE METRICS — RAM, disk, latency
  3. EFFICIENCY SCORE — quality per resource unit

Usage:
    python bench_metrics.py                          # Both servers
    python bench_metrics.py --only do_ame            # Just DO_AME
    python bench_metrics.py --only do_main           # Just DO_MAIN
    python bench_metrics.py --json                   # Machine-readable output
"""
import argparse
import json
import os
import statistics
import subprocess
import threading
import time
import sseclient
import requests

# ---------------------------------------------------------------------------
# Test suites
# ---------------------------------------------------------------------------

TESTS_DO_AME = [
    ("search_objects",  "справочник видов операций с документами",        ["АМЕ_ВидыОпераций"]),
    ("search_routines", "создать задачу для исполнителя по документу",    ["ПолучитьЗадачуИсполнителя", "СоздатьЗадачу"]),
    ("search_all",      "архив документов и хранение файлов",            ["АМЕ_АрхивДокументов"]),
    ("search_routines", "отправить уведомление пользователю",            []),
    ("search_objects",  "регистр для хранения статусов согласования",    []),
    ("search_all",      "работа с письмами и входящей корреспонденцией", ["РеестрВходящихПисем", "АМЭ_ОтчётПоВходящимПисьмам"]),
    ("search_routines", "получить список подчиненных сотрудников",       []),
    ("search_objects",  "регистр с графиками сотрудников",               ["АМЕ_ГрафикиОтпусков"]),
]

TESTS_DO_MAIN = [
    ("search_objects",  "справочник видов документов",                    ["ВидыДокументов", "ВидыВнутреннихДокументов", "ВидыДокументовЭДО"]),
    ("search_objects",  "справочник контрагентов",                        ["Контрагенты", "ГруппыДоступаКонтрагентов"]),
    ("search_objects",  "справочник сотрудников организации",             ["Сотрудники", "Пользователи", "ПолномочияСотрудников"]),
    ("search_objects",  "регистр учета файлов и версий",                  ["ВерсииФайлов", "Файлы", "ТекстыВерсийФайлов"]),
    ("search_objects",  "категории документов для хранения",              ["КатегорииДокументов", "ДелаХраненияДокументов", "КатегорииДанных"]),
    ("search_objects",  "журнал регистрации входящих документов",         ["ВходящиеДокументы", "РегистрацияВходящих", "ЖурналРегистрации", "ЖурналПередачиДокументов"]),
    ("search_objects",  "процесс согласования и утверждения",            ["ПроцессыСогласования", "Согласование", "СпособСогласования", "ОтчетПоСогласованиям"]),
    ("search_objects",  "настройки прав доступа пользователей",          ["ПраваДоступа", "НастройкиДоступа", "ГруппыДоступа", "НастройкиДоступаПользователей"]),
    ("search_objects",  "шаблоны исходящих документов",                   ["ШаблоныДокументов", "Шаблоны", "ШаблоныДляСоздания"]),
    ("search_objects",  "организационная структура предприятия",          ["СтруктураПредприятия", "Подразделения"]),
    ("search_routines", "отправить документ на согласование",             ["ОтправитьНаСогласование", "НачатьСогласование", "Согласован"]),
    ("search_routines", "проверить права доступа к документу",            ["ПроверитьПраваДоступа", "ПроверитьДоступ", "ПроверитьПрава", "ЗапросДляРасчетаПрав"]),
    ("search_routines", "создать копию файла в хранилище",               ["СоздатьКопию", "СкопироватьФайл", "СоздатьОписаниеФайла"]),
    ("search_routines", "получить список задач пользователя",             ["ПолучитьЗадачи", "СписокЗадач", "ЗадачиПользователя", "ПолучитьПроектныеЗадачи"]),
    ("search_routines", "сформировать печатную форму документа",          ["СформироватьПечатнуюФорму", "ПечатьДокумента", "Печать"]),
    ("search_all",      "работа с электронной подписью",                  ["ЭлектроннаяПодпись", "ЭП", "Подписание", "НачалоРаботыСЭлектроннойПодписью"]),
    ("search_all",      "интеграция с почтовым сервером",                ["ЭлектроннаяПочта", "Почта", "Email", "СоединениеСПочтовым"]),
    ("search_all",      "штрихкодирование документов",                   ["Штрихкодирование", "Штрихкод"]),
    ("search_all",      "мероприятия и протоколы совещаний",              ["Мероприятия", "ПротоколыМероприятий"]),
    ("search_all",      "бизнес-процесс обработки входящего письма",     ["ОбработкаВходящегоДокумента", "ВходящийДокумент", "ОбработатьСобытиеЗаписиЗадачи"]),
]

SUITES = {
    "do_ame": (TESTS_DO_AME, 6005, "DO_AME"),
    "do_main": (TESTS_DO_MAIN, 6004, "DO_MAIN"),
}


# ---------------------------------------------------------------------------
# MCP transport
# ---------------------------------------------------------------------------

def call_tool(base_url, tool_name, query_dict, timeout=25):
    """Call MCP tool and return (result, latency_ms)."""
    t0 = time.time()
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
        return None, 0

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
    time.sleep(0.1)
    requests.post(post, json={
        "jsonrpc": "2.0", "id": "call", "method": "tools/call",
        "params": {"name": tool_name,
                   "arguments": {"query": json.dumps(query_dict, ensure_ascii=False)}},
    }, timeout=10)
    got_result.wait(timeout=timeout)
    latency = (time.time() - t0) * 1000
    try:
        resp.close()
    except Exception:
        pass
    return (results[0] if results else None), latency


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
    name_low = (name or "").lower()
    for exp in expected_list:
        if exp.lower() in name_low:
            return True
    return False


# ---------------------------------------------------------------------------
# Resource metrics collection
# ---------------------------------------------------------------------------

def get_container_memory(container_name):
    """Get container memory usage in MB via docker stats."""
    try:
        out = subprocess.check_output(
            ["docker", "stats", container_name, "--no-stream",
             "--format", "{{.MemUsage}}"],
            timeout=10, text=True
        ).strip()
        # Format: "123.4MiB / 1.5GiB" or "1.2GiB / 3.0GiB"
        usage = out.split("/")[0].strip()
        if "GiB" in usage:
            return float(usage.replace("GiB", "").strip()) * 1024
        elif "MiB" in usage:
            return float(usage.replace("MiB", "").strip())
        elif "KiB" in usage:
            return float(usage.replace("KiB", "").strip()) / 1024
        return 0
    except Exception:
        return 0


def get_embedding_db_size(data_path):
    """Get embeddings.db file size in MB."""
    db_path = os.path.join(data_path, "embeddings.db")
    if os.path.exists(db_path):
        return os.path.getsize(db_path) / (1024 * 1024)
    return 0


# ---------------------------------------------------------------------------
# Quality metrics calculation
# ---------------------------------------------------------------------------

def compute_quality_metrics(test_results):
    """Compute comprehensive quality metrics from test results.

    Returns dict with:
      - precision_at_k: P@1, P@3, P@5
      - mrr: Mean Reciprocal Rank
      - score_separation: ratio of hit avg score to miss avg score
      - confidence: % of queries with top-1 score > 0.3
      - score_stats: min, max, median, mean of top-1 scores
      - false_positive_rate: high-scoring (>0.5) wrong answers
    """
    countable = [r for r in test_results if r["has_gt"]]
    if not countable:
        return {}

    n = len(countable)
    hits_1 = sum(1 for r in countable if r["found_pos"] == 1)
    hits_3 = sum(1 for r in countable if r["found_pos"] and r["found_pos"] <= 3)
    hits_5 = sum(1 for r in countable if r["found_pos"] and r["found_pos"] <= 5)

    # MRR - Mean Reciprocal Rank
    rr_sum = 0
    for r in countable:
        if r["found_pos"]:
            rr_sum += 1.0 / r["found_pos"]
    mrr = rr_sum / n

    # Score separation
    hit_scores = [r["top1_score"] for r in countable if r["found_pos"] == 1]
    miss_scores = [r["top1_score"] for r in countable if not r["found_pos"] or r["found_pos"] > 1]
    hit_avg = statistics.mean(hit_scores) if hit_scores else 0
    miss_avg = statistics.mean(miss_scores) if miss_scores else 0
    score_separation = hit_avg / max(miss_avg, 0.001)

    # Confidence: queries with decisive score (>0.3)
    all_scores = [r["top1_score"] for r in test_results]
    confident = sum(1 for s in all_scores if s > 0.3)
    confidence = confident / len(all_scores) if all_scores else 0

    # False positive rate: high score but wrong answer
    false_positives = sum(1 for r in countable
                         if r["top1_score"] > 0.5 and (not r["found_pos"] or r["found_pos"] > 1))
    fp_rate = false_positives / n

    # Score distribution
    score_stats = {}
    if all_scores:
        score_stats = {
            "min": min(all_scores),
            "max": max(all_scores),
            "median": statistics.median(all_scores),
            "mean": statistics.mean(all_scores),
            "stdev": statistics.stdev(all_scores) if len(all_scores) > 1 else 0,
        }

    return {
        "precision_at_1": hits_1 / n,
        "precision_at_3": hits_3 / n,
        "precision_at_5": hits_5 / n,
        "mrr": mrr,
        "score_separation": score_separation,
        "confidence": confidence,
        "false_positive_rate": fp_rate,
        "hit_avg_score": hit_avg,
        "miss_avg_score": miss_avg,
        "score_stats": score_stats,
        "total_queries": len(test_results),
        "queries_with_gt": n,
        "hits_at_1": hits_1,
        "hits_at_3": hits_3,
        "hits_at_5": hits_5,
    }


def compute_efficiency_score(quality_metrics, resource_metrics):
    """Compute combined efficiency score (0-100).

    Formula: quality_score * (1 - ram_penalty)
    - quality_score (0-100): weighted combination of P@1, MRR, separation
    - ram_penalty: 0 if RAM < 500MB, scales linearly up to 0.3 at 2GB

    Target: maximize quality while keeping RAM reasonable.
    """
    if not quality_metrics:
        return 0

    # Quality score (0-100)
    p1 = quality_metrics.get("precision_at_1", 0)
    p3 = quality_metrics.get("precision_at_3", 0)
    mrr = quality_metrics.get("mrr", 0)
    separation = min(quality_metrics.get("score_separation", 0) / 10, 1.0)  # cap at 10x
    fp_penalty = quality_metrics.get("false_positive_rate", 0)

    quality_score = (
        p1 * 40 +          # P@1 most important (40%)
        p3 * 20 +          # P@3 (20%)
        mrr * 25 +         # MRR (25%)
        separation * 15    # Score separation (15%)
    ) * (1 - fp_penalty)   # Penalize false positives

    # RAM penalty (0-0.3)
    ram_mb = resource_metrics.get("container_ram_mb", 0)
    if ram_mb <= 500:
        ram_penalty = 0
    elif ram_mb <= 2000:
        ram_penalty = 0.3 * (ram_mb - 500) / 1500
    else:
        ram_penalty = 0.3

    efficiency = quality_score * (1 - ram_penalty)
    return round(efficiency, 1)


# ---------------------------------------------------------------------------
# Run benchmark suite
# ---------------------------------------------------------------------------

def run_suite(tests, port, server_name):
    base_url = f"http://localhost:{port}"

    # Connectivity check
    try:
        r = requests.get(f"{base_url}/sse", stream=True, timeout=5)
        r.close()
    except Exception:
        return None

    test_results = []
    latencies = []

    for i, (op, text, expected) in enumerate(tests, 1):
        query = {"op": op, "text": text, "limit": 7}
        raw, latency = call_tool(base_url, "search_by_embedding", query)
        items = extract_results(raw)
        latencies.append(latency)

        has_gt = bool(expected)
        found_pos = None
        if has_gt and items:
            for j, item in enumerate(items):
                if match_expected(item.get("name", ""), expected):
                    found_pos = j + 1
                    break

        top1_score = items[0].get("score", 0) if items else 0
        top1_name = items[0].get("name", "?") if items else "NO RESULTS"

        test_results.append({
            "query": text,
            "op": op,
            "has_gt": has_gt,
            "expected": expected,
            "found_pos": found_pos,
            "top1_score": top1_score,
            "top1_name": top1_name,
            "latency_ms": latency,
            "results_count": len(items),
        })

    # Resource metrics
    container_name = f"litecode-group-litecode-{server_name.lower().replace('_', '_')}-1"
    ram_mb = get_container_memory(container_name)

    # Try to find data path for DB size
    data_paths = {
        "DO_AME": "C:/sukhov_ae/1c-metacode-mcp/do_ame",
        "DO_MAIN": "C:/sukhov_ae/1c-metacode-mcp/do_main",
    }
    db_size_mb = get_embedding_db_size(data_paths.get(server_name, ""))

    resource_metrics = {
        "container_ram_mb": ram_mb,
        "embedding_db_mb": db_size_mb,
        "avg_latency_ms": statistics.mean(latencies) if latencies else 0,
        "p95_latency_ms": sorted(latencies)[int(len(latencies) * 0.95)] if latencies else 0,
        "max_latency_ms": max(latencies) if latencies else 0,
    }

    quality_metrics = compute_quality_metrics(test_results)
    efficiency = compute_efficiency_score(quality_metrics, resource_metrics)

    return {
        "server": server_name,
        "test_results": test_results,
        "quality": quality_metrics,
        "resources": resource_metrics,
        "efficiency_score": efficiency,
    }


# ---------------------------------------------------------------------------
# Report formatting
# ---------------------------------------------------------------------------

def print_report(all_results):
    print()
    print("=" * 100)
    print("  1C LITECODE MCP - EXTENDED METRICS REPORT")
    print(f"  Date: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  Stack: e5-base (768d) + cross-encoder + hybrid BM25/KNN + enriched text")
    print("=" * 100)

    for result in all_results:
        if not result:
            continue
        srv = result["server"]
        q = result["quality"]
        r = result["resources"]
        eff = result["efficiency_score"]

        print(f"\n{'='*100}")
        print(f"  SERVER: {srv}")
        print(f"{'='*100}")

        # Per-query results
        print(f"\n  {'#':<3} {'Status':<8} {'Score':<7} {'Latency':<8} {'Op':<15} {'Query':<42} {'Top-1'}")
        print(f"  {'-'*95}")
        for i, tr in enumerate(result["test_results"], 1):
            if tr["has_gt"]:
                if tr["found_pos"] == 1:
                    status = "HIT@1"
                elif tr["found_pos"] and tr["found_pos"] <= 3:
                    status = f"HIT@{tr['found_pos']}"
                elif tr["found_pos"]:
                    status = f"@{tr['found_pos']}"
                else:
                    status = "MISS"
            else:
                status = "---"
            lat = f"{tr['latency_ms']:.0f}ms"
            print(f"  {i:<3} {status:<8} {tr['top1_score']:<7.3f} {lat:<8} "
                  f"{tr['op']:<15} {tr['query']:<42} {tr['top1_name'][:35]}")

        # Quality metrics
        print(f"\n  QUALITY METRICS:")
        print(f"  {'-'*50}")
        print(f"  Precision@1:        {q['precision_at_1']*100:5.1f}%  ({q['hits_at_1']}/{q['queries_with_gt']})")
        print(f"  Precision@3:        {q['precision_at_3']*100:5.1f}%  ({q['hits_at_3']}/{q['queries_with_gt']})")
        print(f"  Precision@5:        {q['precision_at_5']*100:5.1f}%  ({q['hits_at_5']}/{q['queries_with_gt']})")
        print(f"  MRR:                {q['mrr']:5.3f}")
        print(f"  Score Separation:   {q['score_separation']:5.1f}x  (hit_avg={q['hit_avg_score']:.3f} / miss_avg={q['miss_avg_score']:.3f})")
        print(f"  Confidence (>0.3):  {q['confidence']*100:5.1f}%")
        print(f"  False Positive Rate:{q['false_positive_rate']*100:5.1f}%")

        ss = q.get("score_stats", {})
        if ss:
            print(f"  Score Distribution: min={ss['min']:.3f} med={ss['median']:.3f} "
                  f"mean={ss['mean']:.3f} max={ss['max']:.3f} std={ss['stdev']:.3f}")

        # Resource metrics
        print(f"\n  RESOURCE METRICS:")
        print(f"  {'-'*50}")
        print(f"  Container RAM:      {r['container_ram_mb']:6.0f} MB")
        print(f"  Embedding DB:       {r['embedding_db_mb']:6.1f} MB")
        print(f"  Avg Latency:        {r['avg_latency_ms']:6.0f} ms")
        print(f"  P95 Latency:        {r['p95_latency_ms']:6.0f} ms")
        print(f"  Max Latency:        {r['max_latency_ms']:6.0f} ms")

        # Efficiency score
        print(f"\n  EFFICIENCY SCORE:   {eff}/100")
        print(f"  (quality * RAM_efficiency, higher = better)")

    # Combined summary
    if len(all_results) >= 2:
        valid = [r for r in all_results if r]
        print(f"\n{'='*100}")
        print(f"  COMBINED SUMMARY")
        print(f"{'='*100}")
        print(f"\n  {'Server':<25} {'P@1':<8} {'P@3':<8} {'MRR':<8} {'RAM':<10} {'Latency':<10} {'Efficiency'}")
        print(f"  {'-'*80}")
        for r in valid:
            q = r["quality"]
            res = r["resources"]
            print(f"  {r['server']:<25} {q['precision_at_1']*100:<7.0f}% "
                  f"{q['precision_at_3']*100:<7.0f}% {q['mrr']:<7.3f}  "
                  f"{res['container_ram_mb']:<9.0f}MB {res['avg_latency_ms']:<9.0f}ms "
                  f"{r['efficiency_score']}/100")

        # Targets
        print(f"\n  TARGETS:")
        for r in valid:
            q = r["quality"]
            p1_ok = "PASS" if q["precision_at_1"] >= 0.7 else "FAIL"
            p3_ok = "PASS" if q["precision_at_3"] >= 0.85 else "FAIL"
            mrr_ok = "PASS" if q["mrr"] >= 0.7 else "FAIL"
            ram_ok = "PASS" if r["resources"]["container_ram_mb"] <= 1500 else "FAIL"
            print(f"  {r['server']}: P@1>70%={p1_ok} P@3>85%={p3_ok} "
                  f"MRR>0.7={mrr_ok} RAM<1.5GB={ram_ok}")

    print(f"\n{'='*100}")


def main():
    parser = argparse.ArgumentParser(description="Extended metrics for 1C Litecode MCP")
    parser.add_argument("--only", choices=["do_ame", "do_main", "both"], default="both")
    parser.add_argument("--json", action="store_true", help="Output JSON instead of text")
    args = parser.parse_args()

    suites_to_run = []
    if args.only in ("do_ame", "both"):
        suites_to_run.append("do_ame")
    if args.only in ("do_main", "both"):
        suites_to_run.append("do_main")

    all_results = []
    for suite_key in suites_to_run:
        tests, port, name = SUITES[suite_key]
        result = run_suite(tests, port, name)
        if result:
            all_results.append(result)
        else:
            print(f"  SKIP {name}: server not reachable on port {port}")

    if args.json:
        # Machine-readable output (strip test_results for brevity)
        output = []
        for r in all_results:
            output.append({
                "server": r["server"],
                "quality": r["quality"],
                "resources": r["resources"],
                "efficiency_score": r["efficiency_score"],
            })
        print(json.dumps(output, indent=2, ensure_ascii=False))
    else:
        print_report(all_results)


if __name__ == "__main__":
    main()
