from harness.guardrails import check_message


async def message_flags(message, deps):
    # The Agent's output contract cannot be disabled by a permissive adapter.
    flags = list(dict.fromkeys([*check_message(message), *await deps.harness.check_message(message)]))
    if not message.strip():
        flags = [*flags, "빈 안내문"]
    if "{고객명}" not in message:
        flags = [*flags, "고객명 자리표시 누락"]
    return flags


async def guardrail_check(state, deps):
    flags, rewrite = [], []
    manual = set(state.get("manual_drafts", []))
    drafts = dict(state["drafts"])
    for cid, message in drafts.items():
        if cid in state["decisions"]:
            continue
        found = await message_flags(message, deps)
        if found:
            flags.extend(f"{cid}: {flag}" for flag in found)
            if state["draft_attempts"].get(cid, 0) >= 3:
                manual.add(cid)
                drafts[cid] = ""  # Never expose an unsafe draft as approvable.
            else:
                rewrite.append(cid)
    return {"guardrail_flags": flags, "rewrite_customers": rewrite,
            "manual_drafts": sorted(manual), "drafts": drafts}
