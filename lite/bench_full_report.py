"""Full benchmark report for 1C Litecode MCP — DO_AME + DO_MAIN.

Runs comprehensive test suite and produces a detailed report.
"""
import argparse
import json
import statistics
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
    # Точные доменные запросы
    ("search_objects",  "справочник видов документов",                    ["ВидыДокументов", "ВидыВнутреннихДокументов", "ВидыДокументовЭДО"]),
    ("search_objects",  "справочник контрагентов",                        ["Контрагенты", "ГруппыДоступаКонтрагентов"]),
    ("search_objects",  "справочник сотрудников организации",             ["Сотрудники", "Пользователи", "ПолномочияСотрудников"]),
    ("search_objects",  "регистр учета файлов и версий",                  ["ВерсииФайлов", "Файлы", "ТекстыВерсийФайлов"]),
    ("search_objects",  "категории документов для хранения",              ["КатегорииДокументов", "ДелаХраненияДокументов", "КатегорииДанных"]),
    # Описательные запросы
    ("search_objects",  "журнал регистрации входящих документов",         ["ВходящиеДокументы", "РегистрацияВходящих", "ЖурналРегистрации", "ЖурналПередачиДокументов"]),
    ("search_objects",  "процесс согласования и утверждения",            ["ПроцессыСогласования", "Согласование", "СпособСогласования", "ОтчетПоСогласованиям"]),
    ("search_objects",  "настройки прав доступа пользователей",          ["ПраваДоступа", "НастройкиДоступа", "ГруппыДоступа", "НастройкиДоступаПользователей"]),
    ("search_objects",  "шаблоны исходящих документов",                   ["ШаблоныДокументов", "Шаблоны", "ШаблоныДляСоздания"]),
    ("search_objects",  "организационная структура предприятия",          ["СтруктураПредприятия", "Подразделения"]),
    # Рутины
    ("search_routines", "отправить документ на согласование",             ["ОтправитьНаСогласование", "НачатьСогласование", "Согласован"]),
    ("search_routines", "проверить права доступа к документу",            ["ПроверитьПраваДоступа", "ПроверитьДоступ", "ПроверитьПрава", "ЗапросДляРасчетаПрав"]),
    ("search_routines", "создать копию файла в хранилище",               ["СоздатьКопию", "СкопироватьФайл", "СоздатьОписаниеФайла"]),
    ("search_routines", "получить список задач пользователя",             ["ПолучитьЗадачи", "СписокЗадач", "ЗадачиПользователя", "ПолучитьПроектныеЗадачи"]),
    ("search_routines", "сформировать печатную форму документа",          ["СформироватьПечатнуюФорму", "ПечатьДокумента", "Печать"]),
    # search_all
    ("search_all",      "работа с электронной подписью",                  ["ЭлектроннаяПодпись", "ЭП", "Подписание", "НачалоРаботыСЭлектроннойПодписью"]),
    ("search_all",      "интеграция с почтовым сервером",                ["ЭлектроннаяПочта", "Почта", "Email", "СоединениеСПочтовым"]),
    ("search_all",      "штрихкодирование документов",                   ["Штрихкодирование", "Штрихкод"]),
    ("search_all",      "мероприятия и протоколы совещаний",              ["Мероприятия", "ПротоколыМероприятий"]),
    ("search_all",      "бизнес-процесс обработки входящего письма",     ["ОбработкаВходящегоДокумента", "ВходящийДокумент", "ОбработатьСобытиеЗаписиЗадачи"]),
]


# ---------------------------------------------------------------------------
# MCP transport
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
    name_low = (name or "").lower()
    for exp in expected_list:
        if exp.lower() in name_low:
            return True
    return False


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------

def run_suite(base_url, tests, server_name):
    print(f"\n{'='*90}")
    print(f"  SERVER: {server_name} ({base_url})")
    print(f"  Queries: {len(tests)} total, {sum(1 for _,_,e in tests if e)} with ground truth")
    print(f"{'='*90}")

    hits_at_1 = 0
    hits_at_3 = 0
    hits_at_5 = 0
    countable = 0
    top1_scores = []
    hit_scores = []
    miss_scores = []
    details = []

    for i, (op, text, expected) in enumerate(tests, 1):
        query = {"op": op, "text": text, "limit": 7}
        raw = call_tool(base_url, "search_by_embedding", query)
        items = extract_results(raw)

        has_gt = bool(expected)
        if has_gt:
            countable += 1

        found_pos = None
        if has_gt and items:
            for j, item in enumerate(items):
                if match_expected(item.get("name", ""), expected):
                    found_pos = j + 1
                    break

        top1_score = items[0].get("score", 0) if items else 0
        top1_name = items[0].get("name", "?") if items else "NO RESULTS"
        top1_scores.append(top1_score)

        if has_gt:
            if found_pos == 1:
                hits_at_1 += 1; hits_at_3 += 1; hits_at_5 += 1
                hit_scores.append(top1_score)
                status = "HIT@1"
            elif found_pos and found_pos <= 3:
                hits_at_3 += 1; hits_at_5 += 1
                miss_scores.append(top1_score)
                status = f"HIT@{found_pos}"
            elif found_pos and found_pos <= 5:
                hits_at_5 += 1
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

        t3 = ", ".join(f"{r.get('name','?')}({r.get('score',0):.2f})" for r in items[:3]) if items else ""
        details.append((i, op, text, status, top1_score, top1_name, t3, expected))

    # Print detailed results
    print(f"\n  {'#':<3} {'Status':<8} {'Score':<7} {'Op':<18} {'Query':<50} {'Top-1 Result'}")
    print(f"  {'-'*3} {'-'*8} {'-'*7} {'-'*18} {'-'*50} {'-'*40}")
    for i, op, text, status, score, top1, t3, expected in details:
        print(f"  {i:<3} {status:<8} {score:<7.4f} {op:<18} {text:<50} {top1}")
        if expected:
            print(f"      expected: {' | '.join(expected)}")
        print(f"      top-3: {t3}")
        print()

    # Summary
    p1 = 100 * hits_at_1 / countable if countable else 0
    p3 = 100 * hits_at_3 / countable if countable else 0
    p5 = 100 * hits_at_5 / countable if countable else 0

    print(f"\n{'='*90}")
    print(f"  SUMMARY: {server_name}")
    print(f"{'='*90}")
    print(f"  Precision@1: {hits_at_1}/{countable} ({p1:.0f}%)")
    print(f"  Precision@3: {hits_at_3}/{countable} ({p3:.0f}%)")
    print(f"  Precision@5: {hits_at_5}/{countable} ({p5:.0f}%)")
    print()
    if top1_scores:
        print(f"  Score statistics (all top-1):")
        print(f"    min={min(top1_scores):.4f}  max={max(top1_scores):.4f}  "
              f"median={statistics.median(top1_scores):.4f}  mean={statistics.mean(top1_scores):.4f}")
    if hit_scores:
        print(f"  Hit@1 avg score: {statistics.mean(hit_scores):.4f}")
    if miss_scores:
        print(f"  Miss avg score:  {statistics.mean(miss_scores):.4f}")
    print()
    print(f"  Targets:")
    print(f"    P@1 >= 70%: {'PASS' if p1 >= 70 else 'FAIL'} ({p1:.0f}%)")
    print(f"    P@3 >= 85%: {'PASS' if p3 >= 85 else 'FAIL'} ({p3:.0f}%)")
    print(f"    P@5 >= 90%: {'PASS' if p5 >= 90 else 'FAIL'} ({p5:.0f}%)")
    print(f"{'='*90}\n")

    return {"server": server_name, "p1": p1, "p3": p3, "p5": p5,
            "hits_at_1": hits_at_1, "countable": countable}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--do-ame-port", type=int, default=6005)
    parser.add_argument("--do-main-port", type=int, default=6004)
    parser.add_argument("--only", choices=["do_ame", "do_main", "both"], default="both")
    args = parser.parse_args()

    print("=" * 90)
    print("  1C LITECODE MCP - FULL BENCHMARK REPORT")
    print(f"  Date: {time.strftime('%Y-%m-%d %H:%M')}")
    print(f"  Model: e5-base (768 dim) + cross-encoder reranker")
    print(f"  Features: enriched text, hybrid BM25+KNN, category boost, query expansion")
    print("=" * 90)

    results = []

    if args.only in ("do_ame", "both"):
        try:
            r = requests.get(f"http://localhost:{args.do_ame_port}/sse", stream=True, timeout=5)
            r.close()
            res = run_suite(f"http://localhost:{args.do_ame_port}", TESTS_DO_AME, "DO_AME (927 routines, 201 objects)")
            results.append(res)
        except Exception as e:
            print(f"\n  SKIP DO_AME: {e}")

    if args.only in ("do_main", "both"):
        try:
            r = requests.get(f"http://localhost:{args.do_main_port}/sse", stream=True, timeout=5)
            r.close()
            res = run_suite(f"http://localhost:{args.do_main_port}", TESTS_DO_MAIN, "DO_MAIN (92K routines, 6K objects)")
            results.append(res)
        except Exception as e:
            print(f"\n  SKIP DO_MAIN: {e}")

    if len(results) >= 2:
        print("\n" + "=" * 90)
        print("  COMBINED RESULTS")
        print("=" * 90)
        total_hits = sum(r["hits_at_1"] for r in results)
        total_countable = sum(r["countable"] for r in results)
        combined_p1 = 100 * total_hits / total_countable if total_countable else 0
        for r in results:
            print(f"  {r['server']:<50} P@1={r['p1']:.0f}%  P@3={r['p3']:.0f}%  P@5={r['p5']:.0f}%")
        print(f"  {'COMBINED':<50} P@1={combined_p1:.0f}%")
        print("=" * 90)


if __name__ == "__main__":
    main()
