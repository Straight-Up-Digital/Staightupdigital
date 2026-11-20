"""Shared recurring-revenue calculations for hosting and bundled retainers."""
from __future__ import annotations

from collections.abc import Mapping, Sequence


def pricing_from_config(config: Mapping[str, object]) -> dict[str, object]:
    proposal = config.get("proposal_pricing")
    proposal = proposal if isinstance(proposal, Mapping) else {}
    offer = config.get("website_offer")
    offer = offer if isinstance(offer, Mapping) else {}
    return {
        "build": proposal.get("website_build_once_off_zar", offer.get("build_once_off_zar", 1500)),
        "pages": proposal.get("website_build_max_pages", 5),
        "hosting": proposal.get("website_hosting_monthly_zar", offer.get("hosting_monthly_zar", 129)),
        "point": proposal.get("point_rate_zar", proposal.get("audit_missing_point_zar", 40)),
        "edits": proposal.get("once_off_edits", {}),
        "hosting_tiers": proposal.get("hosting_tiers", []),
        "retainers": config.get("monthly_retainers", []),
    }


def monthly_recurring_revenue(
    contacts: Sequence[Mapping[str, object]],
    hosting_monthly_zar: float,
    retainers: Sequence[Mapping[str, object]],
) -> float:
    plans_by_name = {
        str(plan.get("name") or "").strip().casefold(): plan
        for plan in retainers
        if str(plan.get("name") or "").strip()
    }
    hosting_price = max(0.0, float(hosting_monthly_zar))
    total = 0.0
    for contact in contacts:
        if contact.get("pipeline_stage") != "live":
            continue
        monthly_fee = float(contact.get("monthly_fee") or 0)
        plan = plans_by_name.get(str(contact.get("retainer_name") or "").strip().casefold())
        hosting_is_included = bool(plan and plan.get("includes_hosting") is True)
        if contact.get("hosting_enabled") and not hosting_is_included:
            monthly_fee += hosting_price
        total += monthly_fee
    return total
