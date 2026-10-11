"""Transparent hybrid reranking of model-derived cosine candidates.

Lexical signals supplement the real model; they are never a substitute for an
unavailable encoder or an unindexed menu. Numeric/allergen filtering still runs
independently and before result selection.
"""
from __future__ import annotations

import re

STOP_WORDS = {
    "хочу", "хотим", "хочется", "мне", "пожалуйста", "посоветуй", "порекомендуй",
    "найди", "подбери", "предложи", "что", "нибудь", "какое", "какие", "можно",
    "есть", "съесть", "заказать", "попробовать", "к", "с", "со", "и", "в", "на",
    "для", "по", "у", "около", "рядом", "недалеко", "ближайший", "обед", "ужин",
    "завтрак", "еда", "еды", "блюдо", "блюда", "ресторан", "рестораны", "руб",
    "рублей", "рубля", "ккал", "бюджет", "до", "от", "не", "без", "очень",
    "острый", "острое", "острая", "неострое", "неострый", "острых", "грамм",
    "калорий", "калории", "i", "want", "would", "like", "please", "recommend",
    "find", "near", "nearby", "a", "the", "with", "and", "for", "to", "some",
    "food", "meal", "lunch", "dinner", "breakfast", "restaurant", "rub", "kcal",
    "without", "not", "spicy", "mild", "under", "budget",
}
NEGATIONS = {"без", "не", "исключая", "without", "except", "not"}


def tokens(text):
    return re.findall(r"[^\W\d_]+", text.casefold().replace("ё", "е"), re.UNICODE)


def stem(token):
    """Small explicit inflection normalization, not a semantic synonym model."""
    if re.fullmatch(r"[а-я]+", token):
        for ending in ("ами", "ями", "ого", "ему", "ому", "ах", "ях", "ов", "ев", "ой", "ей", "ые", "ый", "ая", "ое", "ы", "и", "а", "я", "у", "ю"):
            if token.endswith(ending) and len(token) - len(ending) >= 4:
                return token[:-len(ending)]
    if re.fullmatch(r"[a-z]+", token) and token.endswith("s") and len(token) >= 5:
        return token[:-1]
    return token


def intent_terms(query):
    terms = []
    negated_next = False
    for token in tokens(query):
        if token in NEGATIONS:
            negated_next = True
            continue
        if negated_next:
            negated_next = False
            continue
        if token not in STOP_WORDS and len(token) >= 3 and token not in terms:
            terms.append(token)
    return terms


def semantic_query(query):
    # Remove only conversational lead-ins; keep constraints and dish modifiers.
    normalized = query.strip()
    normalized = re.sub(r"^(?:(?:я\s+)?(?:хочу|пожалуйста|посоветуй|порекомендуй|найди|подбери|предложи)\b[\s,!:]*)+", "", normalized, flags=re.I)
    return normalized or query


def lexical_signals(query, item):
    terms = intent_terms(query)
    name_tokens = {stem(x) for x in tokens(item.name)}
    tag_tokens = {stem(x) for x in tokens(" ".join(item.tags or []))}
    name_matches = [x for x in terms if stem(x) in name_tokens]
    tag_matches = [x for x in terms if stem(x) in tag_tokens]
    bonus = (0.55 + 0.15 * len(name_matches)/len(terms)) if name_matches else 0.0
    if tag_matches:
        bonus += 0.15 * len(tag_matches)/len(terms)
    return bonus, list(dict.fromkeys(name_matches + tag_matches))


def ranking(query, item, cosine_similarity):
    bonus, matches = lexical_signals(query, item)
    return max(0.0, cosine_similarity) + bonus, matches
