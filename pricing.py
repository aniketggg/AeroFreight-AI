"""
Dynamic, value-anchored service fee for the route-optimization + compliance
document package.

The fee is never a flat constant. It is derived from the savings Riya's route
optimization produced over a naive baseline route, plus small surcharges for
documentation complexity and high-value handling, with a floor and a ceiling
so it scales sensibly for both tiny and very large shipments.

This is the agent's product: it sells the optimization + paperwork service,
never the shipment's value itself.
"""

from __future__ import annotations

from dataclasses import dataclass

from models import EconData, RouteData

FEE_PCT_OF_SAVINGS = 0.10  # the agent keeps 10% of demonstrated savings
FLOOR_FEE_USD = 4.99  # minimum charge for any rendered service
CEILING_FEE_USD = 250.00  # cap so a huge shipment doesn't produce an absurd fee
COMPLEXITY_SURCHARGE_PER_EXTRA_COUNTRY = 1.50  # more transit countries -> more docs
HIGH_VALUE_SURCHARGE_USD = 5.00  # extra handling for declared_value_usd > $2,500


@dataclass
class FeeBreakdown:
    baseline_cost_usd: float
    optimized_cost_usd: float
    savings_usd: float
    base_fee_usd: float
    complexity_surcharge_usd: float
    high_value_surcharge_usd: float
    total_fee_usd: float

    def as_markdown(self) -> str:
        lines = [
            f"- Baseline (naive single-mode) cost: **${self.baseline_cost_usd:,.2f}**",
            f"- Optimized cost (this route): **${self.optimized_cost_usd:,.2f}**",
            f"- Savings found by the agent: **${self.savings_usd:,.2f}**",
            f"- Service fee (10% of savings, min ${FLOOR_FEE_USD:.2f}): "
            f"**${self.base_fee_usd:,.2f}**",
        ]
        if self.complexity_surcharge_usd:
            lines.append(
                f"- Multi-country documentation surcharge: "
                f"**${self.complexity_surcharge_usd:,.2f}**"
            )
        if self.high_value_surcharge_usd:
            lines.append(
                f"- High-value handling surcharge: **${self.high_value_surcharge_usd:,.2f}**"
            )
        lines.append(f"- **Total service fee: ${self.total_fee_usd:,.2f}**")
        return "\n".join(lines)


def compute_service_fee(econ: EconData, route: RouteData) -> FeeBreakdown:
    # Guard against a bad/missing baseline making "savings" negative or huge.
    baseline = max(route.baseline_cost_usd, route.total_cost_usd)
    optimized = route.total_cost_usd
    savings = max(0.0, baseline - optimized)

    base_fee = max(FLOOR_FEE_USD, savings * FEE_PCT_OF_SAVINGS)

    extra_countries = max(0, len(route.countries_visited) - 1)
    complexity_surcharge = extra_countries * COMPLEXITY_SURCHARGE_PER_EXTRA_COUNTRY

    high_value_surcharge = HIGH_VALUE_SURCHARGE_USD if econ.is_high_value else 0.0

    total = min(
        CEILING_FEE_USD, base_fee + complexity_surcharge + high_value_surcharge
    )

    return FeeBreakdown(
        baseline_cost_usd=round(baseline, 2),
        optimized_cost_usd=round(optimized, 2),
        savings_usd=round(savings, 2),
        base_fee_usd=round(base_fee, 2),
        complexity_surcharge_usd=round(complexity_surcharge, 2),
        high_value_surcharge_usd=round(high_value_surcharge, 2),
        total_fee_usd=round(total, 2),
    )
