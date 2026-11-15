# MATH110 Discrete Mathematics - Lecture Notes

## Unit 1: Logic and Proofs

### Propositional logic

A proposition is a statement that is either true or false. The connectives are negation ($\neg p$), conjunction ($p \land q$, "and"), disjunction ($p \lor q$, "or") and implication ($p \to q$, "if p then q"). An implication is false only when p is true and q is false.

To translate "If it rains, the match is cancelled" let p = "it rains" and q = "the match is cancelled"; the statement is $p \to q$. A truth table lists every combination of truth values: with two propositions there are $2^2 = 4$ rows.

| p | q | p → q |
|---|---|-------|
| T | T | T |
| T | F | F |
| F | T | T |
| F | F | T |

### Proofs

A direct proof assumes the hypothesis and derives the conclusion step by step. Example: if n is even then n squared is even. Write n = 2k, so n^2 = 4k^2 = 2(2k^2), which is even. A proof by contradiction assumes the statement is false and derives something impossible.

## Unit 2: Sets and Counting

The rule of sum: if a task can be done in one of m ways or one of n other ways, there are m + n ways. The rule of product: if a task has two stages with m and n choices, there are m x n ways. The number of ordered arrangements (permutations) of r items chosen from n distinct items is $P(n, r) = \frac{n!}{(n-r)!}$. For example, 3 people can be arranged in 3! = 6 orders, and choosing a president and secretary from 5 people gives P(5, 2) = 20 ways.
