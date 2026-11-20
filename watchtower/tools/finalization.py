from ..context import ExplorationContext, ToolError
from ..models import ExplorationResult, ExtractionMethod, FinalizationProposal, StopReason
from ..urls import sanitize
from .extraction import canonical_recipe

BLOCKED = {
    StopReason.CAPTCHA,
    StopReason.BOT_PROTECTION,
    StopReason.AUTHENTICATION_REQUIRED,
    StopReason.ACCESS_BLOCKED,
    StopReason.ROBOTS_DISALLOWED,
    StopReason.ROBOTS_UNAVAILABLE,
    StopReason.RATE_LIMITED,
    StopReason.UNSAFE_URL,
}

NEXT_STEP = {
    StopReason.VERIFIED: "Review the verified recipe before enabling scheduled extraction.",
    StopReason.CAPTCHA: "Use a portal-approved public data feed or request authorized access.",
    StopReason.BOT_PROTECTION: "Ask the portal operator for an approved public data access route.",
    StopReason.AUTHENTICATION_REQUIRED: "Consult the portal operator about authorized data access.",
    StopReason.ACCESS_BLOCKED: "Review the access restriction with the portal operator.",
    StopReason.ROBOTS_DISALLOWED: "Review allowed paths or published data access options before a new run.",
    StopReason.ROBOTS_UNAVAILABLE: "Retry when the robots policy can be checked.",
    StopReason.RATE_LIMITED: "Wait for the portal's retry interval before starting another run.",
    StopReason.CANCELLED: "Start or resume an exploration to continue.",
    StopReason.UNSAFE_URL: "Use a public portal URL and review any blocked redirect.",
    StopReason.INSUFFICIENT_EVIDENCE: "Review the retained findings before retrying or choosing another public source.",
    StopReason.ERROR: "Review the error before trying again.",
    StopReason.FAILURE_LIMIT: "Review the last failed actions before trying again.",
    StopReason.REPEATED_TOOL_CALLS: "Review the observations before trying again.",
    StopReason.BROWSER_LIMIT: "Review the browser findings and adjust the navigation limit if further rendering is needed.",
}


async def finalize_exploration(result: FinalizationProposal, context: ExplorationContext) -> dict:
    ctx = context
    known = {event.get("evidence_id") for event in ctx.evidence}
    extras = set(result.supporting_evidence) - known
    if extras:
        return {"accepted": False, "error": "Supporting evidence must reference evidence IDs from this run"}
    if result.selected_method is ExtractionMethod.UNKNOWN:
        if result.recipe or result.validation_references or result.additional_exploration_required:
            return {
                "accepted": False,
                "error": "Insufficient-evidence finalization needs no recipe or pending work",
            }
        if ctx.best and ctx.best[1].get("verified"):
            return {
                "accepted": False,
                "error": "A verified candidate exists. Finalize it or explain its limitations.",
            }
        ctx.proposal = result
        ctx.stop = StopReason.INSUFFICIENT_EVIDENCE
        return {"accepted": True, "verified": False}
    if result.additional_exploration_required:
        return {"accepted": False, "error": "Complete the required validation before finalizing"}
    if not result.recipe or result.recipe.method != result.selected_method:
        return {"accepted": False, "error": "Provide a recipe matching the selected extraction method"}
    recipe = canonical_recipe(result.recipe)
    if not result.supporting_evidence:
        return {"accepted": False, "error": "Provide supporting evidence from this run"}
    if not result.validation_references or set(result.validation_references) - ctx.validations.keys():
        return {"accepted": False, "error": "Reference successful deterministic test_extraction evidence"}
    if not set(result.supporting_evidence).intersection(result.validation_references):
        return {"accepted": False, "error": "Supporting evidence must include a recipe validation reference"}
    verified = next(
        (
            ctx.validations[ref][1]
            for ref in result.validation_references
            if ctx.validations[ref][0] == recipe and ctx.validations[ref][1]["verified"]
        ),
        None,
    )
    if not verified:
        return {
            "accepted": False,
            "error": "The proposed recipe differs from the deterministically verified recipe",
        }
    ctx.best = recipe, verified
    ctx.proposal = result
    ctx.stop = StopReason.VERIFIED
    return {"accepted": True, "verified": True, "validation": verified}


def build_result(ctx: ExplorationContext, reason: StopReason) -> ExplorationResult:
    recipe, validation = ctx.best if ctx.best else (None, None)
    candidate_verified = bool(validation and validation.get("verified"))
    method = recipe.method if recipe else ExtractionMethod.UNKNOWN
    if reason is StopReason.VERIFIED and not candidate_verified:
        raise ToolError("Successful termination requires a deterministically verified candidate")
    if recipe:
        ctx.capabilities.best_extraction_method = recipe.method
    if ctx.proposal:
        ctx.capabilities.alternatives = list(
            dict.fromkeys(
                method
                for method in ctx.proposal.alternatives
                if method is not ExtractionMethod.UNKNOWN
                and method != ctx.capabilities.best_extraction_method
            )
        )
    if reason is StopReason.VERIFIED:
        status = "succeeded"
    elif reason is StopReason.CANCELLED:
        status = "cancelled"
    elif reason in BLOCKED:
        status = "blocked"
    elif reason is StopReason.ERROR:
        status = "failed"
    else:
        status = "incomplete"
    assumptions = []
    if validation and validation.get("reason"):
        assumptions.append(validation["reason"])
    if not candidate_verified:
        assumptions.append(
            "No extraction recipe retrieved and validated a live tender through every declared step."
        )
    if recipe and recipe.pagination is None:
        assumptions.append("Pagination and coverage beyond the first listing page are unverified.")
    if recipe and recipe.detail is None:
        assumptions.append("Detail retrieval is unverified.")
    if recipe and recipe.document_field is None:
        assumptions.append("Attachment retrieval is unverified.")
    if ctx.proposal:
        assumptions.extend(ctx.proposal.known_limitations)
    evidence = list(ctx.evidence)
    if ctx.proposal:
        evidence.append(
            {
                "tool": "finalization_proposal",
                "observation": sanitize(ctx.proposal.model_dump(mode="json", exclude={"recipe"})),
            }
        )
    return ExplorationResult(
        exploration_id=ctx.run_id,
        portal_id=ctx.portal_id,
        url=ctx.url,
        status=status,
        stop_reason=reason,
        usage=ctx.usage.model_copy(),
        duration_seconds=round(ctx.elapsed, 3),
        configuration=ctx.config,
        classification=ctx.capabilities,
        initial_observation=ctx.initial,
        best_candidate_method=method,
        candidate_verified=candidate_verified,
        recipe=recipe,
        validation=validation,
        evidence=sanitize(evidence),
        last_successful_action=ctx.last_action,
        last_website_state=sanitize(ctx.last_state),
        remaining_assumptions=sanitize(list(dict.fromkeys(assumptions))),
        recommended_next_step=NEXT_STEP.get(
            reason, "Review the findings and try again with an adjusted budget."
        ),
        resumed_from=ctx.resumed_from,
    )
