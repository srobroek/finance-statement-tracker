from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from .cashback import CardProgram, PaymentIntent, Recommendation, recommend
from .models import Transaction
from .rules import RuleEngine, RuleTrace, StaticRule


@dataclass(slots=True)
class BatchResult:
    transactions: list[Transaction]
    traces: list[RuleTrace]
    recommendations: list[Recommendation]


class DeterministicWorker:
    def __init__(self, rules: Iterable[StaticRule], programs: Iterable[CardProgram]):
        self.rule_engine = RuleEngine(rules)
        self.programs = tuple(programs)

    def process(
        self,
        new_transactions: Iterable[Transaction],
        period_transactions: Iterable[Transaction] = (),
        intents: Iterable[PaymentIntent] = (),
    ) -> BatchResult:
        processed: list[Transaction] = []
        traces: list[RuleTrace] = []
        for transaction in new_transactions:
            traces.extend(self.rule_engine.apply(transaction))
            processed.append(transaction)
        current = [*period_transactions, *processed]
        recommendations = [recommend(self.programs, current, intent) for intent in intents]
        return BatchResult(processed, traces, recommendations)

