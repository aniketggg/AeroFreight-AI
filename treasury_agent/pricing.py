"""
Dynamic, value-anchored service fee for the route-optimization + compliance
document package, mapped to central shared_models fields.
"""

from __future__ import annotations

from dataclasses import dataclass

from shared_models import EconData, RouteData

FEE_PCT_OF_SAVINGS = 0.10
FLOOR_FEE_USD = 4.99
CEILING_FEE_USD = 250.00
COMPLEXITY_SURCHARGE_PER_EXTRA_COUNTRY = 1.50
HIGH_VALUE_SURCHARGE_USD = 5.00


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
                f"- High-value handling surcharge: "
                f"**${self.high_value_surcharge_usd:,.2f}**"
            )
        lines.append(f"- **Total service fee: ${self.total_fee_usd:,.2f}**")
        return "\n".join(lines)


def compute_service_fee(econ: EconData, route: RouteData) -> FeeBreakdown:
    """Compute the AeroFreight service fee from central EconData and RouteData."""
    # Central RouteData does not include baseline_cost_usd. Use total_landed_cost_usd
    # as the optimized cost and derive baseline from transport + entry tax so savings
    # are never negative when the route already includes those components.
    optimized = route.total_landed_cost_usd
    transport_plus_entry = round(
        route.freight_and_toll_cost_usd + econ.base_entry_tax_usd,
        2,
    )
    baseline = max(optimized, transport_plus_entry)
    savings = max(0.0, baseline - optimized)

    base_fee = max(FLOOR_FEE_USD, savings * FEE_PCT_OF_SAVINGS)

    extra_countries = max(0, len(route.countries_visited) - 1)
    complexity_surcharge = extra_countries * COMPLEXITY_SURCHARGE_PER_EXTRA_COUNTRY

    high_value_surcharge = HIGH_VALUE_SURCHARGE_USD if econ.is_high_value else 0.0

    total = min(
        CEILING_FEE_USD,
        base_fee + complexity_surcharge + high_value_surcharge,
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
