"""Per-student answer-option shuffling (diagnostic, formative and reteach quizzes).

* Each attempt gets its own stable option order per question (a page reload shows the same order), so students -
  and repeated attempts - see options in different orders.
* The position of the CORRECT option is balanced within an attempt: positions are dealt from shuffled blocks of
  A, B, C, D, so with 4 questions each letter is the answer exactly once (in a random order), and the answer is never
  "always B". The wrong options are shuffled around it.
* The student answers in the order they see; the server converts that back to the item's own option index before
  marking and storing it, so results, reviews and reports stay correct.
"""
from __future__ import annotations

import random


def order(key: str, position: int, n: int, answer: int) -> list[int]:
    """Option order for the question at `position` (0-based) of the attempt `key`: shown slot j holds original
    option order(...)[j]. The correct option lands on a balanced slot."""
    if n <= 1:
        return list(range(n))
    block, offset = divmod(position, n)
    slots = list(range(n))
    random.Random(f"{key}:block{block}").shuffle(slots)
    target = slots[offset]
    others = [i for i in range(n) if i != answer]
    random.Random(f"{key}:{position}:rest").shuffle(others)
    return others[:target] + [answer] + others[target:]


def shown(options: list, p: list[int]) -> list:
    return [options[i] for i in p]


def to_original(p: list[int], shown_index: int) -> int:
    return p[shown_index]


def to_shown(p: list[int], original_index: int | None) -> int | None:
    return None if original_index is None else p.index(original_index)


def quiz_key(attempt_id: int) -> str:
    return f"quiz:{attempt_id}"


def diagnostic_key(attempt_id: int) -> str:
    return f"diag:{attempt_id}"
