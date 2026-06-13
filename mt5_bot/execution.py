"""Execution flow orchestration."""

from __future__ import annotations

from dataclasses import dataclass
import logging

from mt5_bot.mt5_client import MT5Client, OrderResult, ProtectionResult


@dataclass(slots=True)
class ExecutionPlan:
    symbol: str
    side: str
    volume: float
    target_tp_distance: float
    effective_tp_distance: float
    entry_comment: str = "ENTRY"
    protection_comment: str = "SET_TP_AFTER_ENTRY"


@dataclass(slots=True)
class ExecutionReceipt:
    ok: bool
    order_result: OrderResult
    protection_result: ProtectionResult | None
    message: str


class ExecutionEngine:
    """Satu pintu eksekusi untuk menjaga flow broker tetap konsisten."""

    def __init__(self, client: MT5Client, logger: logging.Logger) -> None:
        self.client = client
        self.logger = logger.getChild("execution")

    def execute_entry_plan(self, plan: ExecutionPlan) -> ExecutionReceipt:
        order_result = self.client.send_market_order(
            symbol=plan.symbol,
            side=plan.side,
            volume=plan.volume,
            comment=plan.entry_comment,
        )
        if not order_result.ok:
            return ExecutionReceipt(
                ok=False,
                order_result=order_result,
                protection_result=None,
                message="entry order failed before TP attachment",
            )

        if order_result.position_ticket is None:
            return ExecutionReceipt(
                ok=False,
                order_result=order_result,
                protection_result=None,
                message="entry order succeeded but live position ticket could not be resolved",
            )

        protection_result = self.client.attach_tp_after_entry(
            symbol=plan.symbol,
            position_ticket=order_result.position_ticket,
            side=plan.side,
            fill_price=float(order_result.fill_price or 0.0),
            target_distance=plan.target_tp_distance,
            comment=plan.protection_comment,
        )

        if not protection_result.ok:
            return ExecutionReceipt(
                ok=False,
                order_result=order_result,
                protection_result=protection_result,
                message="entry succeeded but TP attach/verification failed",
            )

        self.logger.info(
            "Execution complete | symbol=%s side=%s order=%s position=%s tp=%.5f",
            plan.symbol,
            plan.side,
            order_result.order_ticket,
            order_result.position_ticket,
            protection_result.tp_attached or 0.0,
        )
        return ExecutionReceipt(
            ok=True,
            order_result=order_result,
            protection_result=protection_result,
            message="entry and TP attachment successful",
        )
