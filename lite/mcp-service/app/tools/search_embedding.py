"""MCP tool: search_by_embedding — semantic search on routines and metadata objects."""

import json
import logging

from app.services.embedding import get_embedder, get_store, get_reranker

log = logging.getLogger(__name__)

DEFAULT_LIMIT = 7
MIN_RESULTS = 5

# Category hints in query text → boost matching category
_CATEGORY_HINTS = {
    "справочник": "Справочники", "справочника": "Справочники",
    "каталог": "Справочники", "catalog": "Справочники",
    "документ": "Документы", "документа": "Документы", "document": "Документы",
    "регистр": "РегистрыСведений", "register": "РегистрыСведений",
    "регистр сведений": "РегистрыСведений", "регистр накопления": "РегистрыНакопления",
    "перечисление": "Перечисления", "enum": "Перечисления",
    "обработка": "Обработки", "отчет": "Отчеты", "отчёт": "Отчеты",
    "модуль": "ОбщиеМодули",
    "план видов": "ПланыВидовХарактеристик",
    "бизнес-процесс": "БизнесПроцессы", "задача": "Задачи",
}

_BOOST_FACTOR = 1.3

# Words that signal the query is about a metadata object (not a routine)
_OBJECT_SIGNAL_WORDS = frozenset({
    "справочник", "справочника", "документ", "документа", "регистр", "регистра",
    "перечисление", "перечисления", "обработка", "обработки", "отчет", "отчёт",
    "отчета", "план", "константа", "хранение", "хранения", "архив", "архива",
    "реестр", "реестра", "журнал", "журнала", "каталог", "каталога",
    "интеграция", "интеграции", "бизнес-процесс", "процесс", "процесса",
    "настройки", "настройка", "подсистема", "подсистемы",
})

# Query expansion: domain-specific synonyms
_QUERY_EXPANSIONS = {
    "штрихкод": "баркод barcode маркировка",
    "штрихкодирован": "баркод barcode маркировка",
    "наклейк": "этикетка ярлык",
    "проводк": "движение",
    "контрагент": "поставщик покупатель клиент партнёр",
    "номенклатур": "товар продукция",
    "сотрудник": "работник персонал кадры",
    "уведомлен": "оповещение рассылка напоминание",
    "согласован": "визирование утверждение подписание",
    "корреспонденц": "письма почта входящие исходящие",
    "подчинённ": "руководитель иерархия оргструктура",
    "подчиненн": "руководитель иерархия оргструктура",
    "график": "расписание план отпуск",
    "статус": "состояние этап",
    "остатк": "запас наличие склад",
    "цен": "прайс стоимость тариф",
    "скидк": "наценка бонус",
    "оплат": "платёж расчёт",
    "продаж": "реализация отгрузка",
    "закупк": "поступление снабжение",
    "задач": "исполнитель поручение назначение",
    "исполнител": "задача поручение ответственный",
    "письм": "корреспонденция почта рассылка",
    "файл": "вложение документ хранилище",
    "печат": "макет шаблон этикетка ценник",
    "доступ": "право роль разрешение",
    "движен": "проводка регистрация запись",
    "обмен": "синхронизация выгрузка загрузка",
}


def _is_object_query(text: str) -> bool:
    """Check if query is asking about a metadata object (not a routine)."""
    words = set(text.lower().split())
    return bool(words & _OBJECT_SIGNAL_WORDS)


def _expand_query(text: str) -> str:
    text_lower = text.lower()
    expansions = []
    for keyword, synonyms in _QUERY_EXPANSIONS.items():
        if keyword in text_lower:
            expansions.append(synonyms)
    if expansions:
        return text + " " + " ".join(expansions)
    return text


def _boost_by_category(results: list[dict], query_text: str) -> list[dict]:
    query_lower = query_text.lower()
    target_category = None
    for hint, cat in _CATEGORY_HINTS.items():
        if hint in query_lower:
            target_category = cat
            break
    if not target_category:
        return results
    for r in results:
        if r.get("category") == target_category:
            r["score"] = round(r["score"] * _BOOST_FACTOR, 4)
    results.sort(key=lambda x: x.get("score", 0), reverse=True)
    return results


def _boost_exact_name_match(results: list[dict], query_text: str) -> list[dict]:
    """Boost results where query words closely match the object name.

    Uses precision (matched/name_words) × coverage (matched/query_words)
    to rank exact matches. Higher combined score = more relevant.
    Also gives category bonus when query hints at specific category.
    """
    if not results:
        return results
    q_words = [w.lower() for w in query_text.split() if len(w) >= 4]
    if not q_words:
        return results

    # Detect category hint from query
    query_lower = " ".join(q_words)
    target_cat = None
    for hint, cat in _CATEGORY_HINTS.items():
        if hint in query_lower:
            target_cat = cat
            break

    for r in results:
        name = r.get("name", "") or ""
        name_lower = name.lower()
        matches = sum(1 for qw in q_words if qw[:5] in name_lower)
        if matches == 0:
            continue

        name_words = max(1, sum(1 for c in name if c.isupper()))
        precision = matches / name_words   # how much of name is covered
        coverage = matches / len(q_words)  # how much of query is in name

        # Combined relevance: precision × (1 + coverage)
        # "ВерсииФайлов" (precision=1.0, coverage=0.4) >
        # "ОбращенияКВерсиямФайлов" (precision=0.67, coverage=0.4)
        relevance = precision * (1 + coverage)

        # Category bonus: if query says "справочник" and this is Справочники
        cat_bonus = 1.3 if (target_cat and r.get("category") == target_cat) else 1.0

        if relevance >= 1.2:
            r["score"] = round(r.get("score", 0) * 3.0 * cat_bonus, 4)
        elif relevance >= 0.8:
            r["score"] = round(r.get("score", 0) * 2.0 * cat_bonus, 4)
        elif relevance >= 0.5:
            r["score"] = round(r.get("score", 0) * 1.3 * cat_bonus, 4)

    results.sort(key=lambda x: x.get("score", 0), reverse=True)
    return results


def _penalize_short_names(results: list[dict]) -> list[dict]:
    """Penalize results with very short names — only if they have no category boost."""
    for r in results:
        name = r.get("name", "") or ""
        # Don't penalize if category matched (it's a real object, just short name)
        if len(name) < 8 and not r.get("category"):
            r["score"] = round(r.get("score", 0) * 0.5, 4)
    results.sort(key=lambda x: x.get("score", 0), reverse=True)
    return results


def _count_query_words(result: dict, query_text: str) -> int:
    """Count how many query stems (first 5 chars) appear in result name/description/synonym."""
    stems = [w.lower()[:5] for w in query_text.split() if len(w) >= 4]
    name = (result.get("name", "") or "").lower()
    desc = (result.get("description", "") or "").lower()
    synonym = (result.get("synonym", "") or "").lower()
    category = (result.get("category", "") or "").lower()
    text = name + " " + desc + " " + synonym + " " + category
    return sum(1 for s in stems if s in text)


def _apply_stem_boost(results: list[dict], query_text: str) -> list[dict]:
    """Boost results where query stems appear in name/description/synonym.

    This compensates for weak embedding scores on exact term matches.
    Applied AFTER hybrid scoring to lift relevant results that BM25 found
    but embedding scored poorly.
    """
    if not results:
        return results
    stems = [w.lower()[:5] for w in query_text.split() if len(w) >= 4]
    if not stems:
        return results
    for r in results:
        matches = _count_query_words(r, query_text)
        if matches >= 2:
            # Strong term overlap — significant boost
            r["score"] = round(r.get("score", 0) * (1 + 0.3 * matches), 4)
        elif matches == 1:
            # Weak overlap — small boost
            r["score"] = round(r.get("score", 0) * 1.15, 4)
    results.sort(key=lambda x: x.get("score", 0), reverse=True)
    return results


def _apply_dynamic_threshold(results: list[dict], query_text: str = "",
                              min_override: int = 0) -> list[dict]:
    """Remove low-score tail with adaptive min_results based on score gap."""
    if not results:
        return results

    top_score = results[0].get("score", 0)
    threshold = max(0.05, top_score * 0.15)
    filtered = [r for r in results if r.get("score", 0) >= threshold]

    # Adaptive min_results based on score gap between #1 and #2
    if min_override > 0:
        effective_min = min_override
    elif len(results) >= 2:
        gap_ratio = top_score / max(results[1].get("score", 0), 0.001)
        if gap_ratio > 5:
            effective_min = max(1, MIN_RESULTS // 3)
        elif gap_ratio > 2:
            effective_min = max(3, MIN_RESULTS // 2)
        else:
            effective_min = MIN_RESULTS
    else:
        effective_min = 1

    if len(filtered) >= effective_min:
        return filtered

    # Fill up to effective_min with stem-matched results (1+ stem match)
    for r in results[len(filtered):]:
        if len(filtered) >= effective_min:
            break
        if _count_query_words(r, query_text) >= 1:
            filtered.append(r)

    # If still short, pad from results directly
    if len(filtered) < effective_min:
        for r in results:
            if r not in filtered:
                filtered.append(r)
            if len(filtered) >= effective_min:
                break

    return filtered


def _normalize_scores(results: list[dict]) -> list[dict]:
    """Normalize scores to 0..1 within the group."""
    if not results:
        return results
    max_score = max(r.get("score", 0) for r in results)
    if max_score <= 0:
        return results
    for r in results:
        r["score"] = round(r.get("score", 0) / max_score, 4)
    return results


def handle_search_embedding(query: str) -> str:
    """Semantic search: embed user query and find closest matches."""
    try:
        req = json.loads(query)
    except json.JSONDecodeError:
        return json.dumps({"error": "Invalid JSON"}, ensure_ascii=False)

    op = req.get("op", "search_all")
    text = req.get("text", req.get("query", ""))
    top_k = req.get("limit", DEFAULT_LIMIT)
    category_filter = req.get("category", "")

    if not text:
        return json.dumps({"error": "Provide 'text' to search"}, ensure_ascii=False)

    embedder = get_embedder()
    store = get_store()
    if embedder is None or store is None:
        return json.dumps({
            "error": "Embedding search is disabled. Set ENABLE_EMBEDDING=true.",
        }, ensure_ascii=False)

    try:
        expanded = _expand_query(text)
        query_vec = embedder.embed_single(expanded)
        fetch_k = max(top_k * 5, 30)

        reranker = get_reranker()

        if op == "search_routines":
            results = store.search_hybrid(query_vec, text, collection="routines", top_k=fetch_k)
            if reranker and results:
                results = reranker.rerank(text, results, top_k=fetch_k)
            results = _apply_dynamic_threshold(results, text, min_override=3)[:top_k]
            return json.dumps({"routines": results}, ensure_ascii=False, default=str)

        elif op == "search_objects":
            results = store.search_hybrid(query_vec, text, collection="objects",
                                          top_k=fetch_k, category_filter=category_filter)
            if reranker and results:
                results = reranker.rerank(text, results, top_k=fetch_k)
            results = _boost_by_category(results, text)
            results = _boost_exact_name_match(results, text)
            results = _penalize_short_names(results)
            results = _apply_dynamic_threshold(results, text, min_override=5)[:top_k]
            return json.dumps({"objects": results}, ensure_ascii=False, default=str)

        elif op == "search_all":
            routines = store.search_hybrid(query_vec, text, collection="routines", top_k=fetch_k)
            objects = store.search_hybrid(query_vec, text, collection="objects",
                                          top_k=fetch_k, category_filter=category_filter)
            if reranker:
                if routines:
                    routines = reranker.rerank(text, routines, top_k=fetch_k)
                if objects:
                    objects = reranker.rerank(text, objects, top_k=fetch_k)
            objects = _boost_by_category(objects, text)
            objects = _boost_exact_name_match(objects, text)

            # Normalize scores within each group before merging
            routines = _normalize_scores(routines)
            objects = _normalize_scores(objects)

            # Objects get a tiebreaker boost in search_all (1.05x base)
            # If query explicitly mentions object types, boost more (1.5x)
            obj_boost = 1.5 if _is_object_query(text) else 1.05
            for o in objects:
                o["score"] = round(o.get("score", 0) * obj_boost, 4)

            for r in routines:
                r["type"] = "routine"
            for o in objects:
                o["type"] = "metadata_object"

            combined = routines + objects
            combined = _penalize_short_names(combined)
            combined = _apply_dynamic_threshold(combined, text)[:top_k]

            return json.dumps({
                "results": combined,
                "stats": {
                    "routines_indexed": store.count("routines"),
                    "objects_indexed": store.count("objects"),
                },
            }, ensure_ascii=False, default=str)

        else:
            return json.dumps({
                "error": f"Unknown op: {op}",
                "available": ["search_routines", "search_objects", "search_all"],
            }, ensure_ascii=False)

    except Exception as e:
        log.exception("Error in search_embedding op=%s", op)
        return json.dumps({"error": str(e)}, ensure_ascii=False)
