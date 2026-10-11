#!/usr/bin/env python3
"""Read-only six-domain campaign guidance; never fabricates a task or Seed."""

from __future__ import annotations

from collections import Counter
from pathlib import Path

import topify_common as common
import topify_seed


PLAYBOOK = Path(__file__).resolve().parents[1] / "references/domain-production.json"
FIELDS = frozenset({
    "target_count", "source_pools", "task_mechanisms", "snapshot_artifacts",
    "local_feedback", "cheap_controls", "reject_if",
})


def load_playbook(path: str | Path = PLAYBOOK) -> dict:
    document = common.load_json(path)
    if (set(document) != {"schema_version", "planning_basis", "domains"}
            or document.get("schema_version") != 1
            or not isinstance(document.get("planning_basis"), str)
            or not document["planning_basis"].strip()
            or not isinstance(document.get("domains"), dict)
            or set(document["domains"]) != common.CATEGORIES):
        raise common.PipelineError("domain playbook must cover the six DOCX categories")
    for category, spec in document["domains"].items():
        if (not isinstance(spec, dict) or set(spec) != FIELDS
                or spec["target_count"] != topify_seed.DEFAULT_VECTOR[category]):
            raise common.PipelineError(f"domain playbook contract invalid for {category}")
        for field in FIELDS - {"target_count"}:
            values = spec[field]
            if (not isinstance(values, list) or not values
                    or not all(isinstance(value, str) and value.strip() for value in values)
                    or len(values) != len(set(values))):
                raise common.PipelineError(f"domain {category} needs distinct {field} entries")
        if len(spec["task_mechanisms"]) < 2 or len(spec["cheap_controls"]) < 2:
            raise common.PipelineError(f"domain {category} needs multiple mechanisms and controls")
    common.canonical_sha256(document)
    return document


def _eligible(root: Path) -> tuple[dict[str, list[dict]], list[str]]:
    by_domain = {category: [] for category in common.CATEGORIES}
    by_seed = {}
    shelved = set()
    decisions = root / "seed-family-decisions"
    if decisions.is_symlink() or decisions.exists() and not decisions.is_dir():
        raise common.PipelineError("campaign seed-family-decisions directory is unsafe")
    if decisions.is_dir():
        for path in sorted(decisions.glob("*.json")):
            item = common.load_json(path)
            seed_id = item.get("seed_id")
            if (not isinstance(seed_id, str) or path.name != topify_seed._safe_name(seed_id)
                    or item.get("status") != "shelved"):
                raise common.PipelineError(f"invalid shelved Seed receipt: {path.name}")
            shelved.add(seed_id)
    for dirname, expected_status in (("provisionals", "prototype-candidate"),
                                     ("screened", "rights-and-dedup-screened")):
        directory = root / dirname
        if directory.is_symlink() or not directory.is_dir():
            raise common.PipelineError(f"campaign {dirname} directory is unsafe")
        for path in sorted(directory.glob("*.json")):
            card = common.load_json(path)
            seed_id = card.get("seed_id")
            if (not isinstance(seed_id, str)
                    or path.name != topify_seed._safe_name(seed_id)
                    or card.get("status") != expected_status
                    or card.get("category") not in common.CATEGORIES
                    or not seed_id.startswith(card["category"] + "/")):
                raise common.PipelineError(f"invalid {dirname} Seed Card: {path.name}")
            by_seed[seed_id] = {"seed_id": seed_id, "category": card["category"],
                                "screen_status": expected_status}
    for item in by_seed.values():
        if item["seed_id"] in shelved:
            continue
        by_domain[item["category"]].append(item)
    for values in by_domain.values():
        values.sort(key=lambda row: (row["screen_status"] != "rights-and-dedup-screened",
                                     row["seed_id"]))
    return by_domain, sorted(shelved)


def _next_author_identity(root: Path, item: dict, claims: list[dict]) -> dict | None:
    """Advance one measured family identity at a time, without replaying claims."""
    seed_id = item["seed_id"]
    stem = seed_id.replace("/", "__")
    folder = root / "candidates" / stem
    versions = []
    for path in sorted(folder.glob("attempt-*/report.json")):
        receipt = common.load_json(path)
        if receipt.get("seed_id") != seed_id or not isinstance(receipt.get("task_sha256"), str):
            raise common.PipelineError("candidate history is inconsistent with its Seed")
        if not versions or receipt["task_sha256"] != versions[-1]["task_sha256"]:
            versions.append(receipt)
        else:
            versions[-1] = receipt  # Same task version was re-audited.
    prior_claims = [claim for claim in claims if claim.get("seed_id") == seed_id]

    def attempt_for(revision: int, parent_hash: str | None) -> int | None:
        matching = [claim for claim in prior_claims
                    if claim.get("revision_index", 0) == revision
                    and claim.get("parent_task_sha256") == parent_hash]
        if not matching:
            return 1
        if (len(matching) == 1 and (matching[0].get("status") == "failed"
             or matching[0].get("status") == "collected"
             and matching[0].get("result_status") in {"failed", "no-candidate"})):
            return 2
        return None

    if not versions:
        attempt = attempt_for(0, None)
        if attempt is None:
            return None  # An unfinished author is not a measured revision.
        return {**item, "revision_index": 0, "parent_task_sha256": None,
                "workspace": str(root / "authoring-workspaces" /
                                 (stem if attempt == 1 else stem + "-retry-2")),
                **({"author_attempt": 2} if attempt == 2 else {})}
    # Old manually authored multi-identity families do not gain invented
    # parent links. Require a separate human lineage review before migration.
    if len(versions) > 1 and any("revision_index" not in row for row in versions):
        return None
    parent = versions[-1]
    card_path = root / "screened" / topify_seed._safe_name(seed_id)
    if not card_path.is_file():
        card_path = root / "provisionals" / topify_seed._safe_name(seed_id)
    if (parent.get("source_identity_sha256") !=
            topify_seed._source_identity_sha256(common.load_json(card_path))):
        # Do not treat a changed source or an unbound historical candidate as
        # the same-family parent without a separate main-agent lineage review.
        return None
    revision = parent.get("revision_index", 0)
    if type(revision) is not int or revision not in (0, 1, 2):
        raise common.PipelineError("candidate revision index is invalid")
    if revision == 2 or parent.get("status") not in {"revise", "review-needed"}:
        return None
    next_revision = revision + 1
    attempt = attempt_for(next_revision, parent["task_sha256"])
    if attempt is None:
        return None
    return {**item, "revision_index": next_revision,
            "parent_task_sha256": parent["task_sha256"],
            "parent_task_id": parent.get("task_id"),
            "parent_task_workspace": parent.get("task_workspace"),
            "parent_status": parent["status"],
            "workspace": str(root / "authoring-workspaces" /
                             (f"{stem}-revision-{next_revision}"
                              + ("-retry-2" if attempt == 2 else ""))),
            **({"author_attempt": 2} if attempt == 2 else {})}


def plan(workspace: str | Path, playbook_path: str | Path = PLAYBOOK,
         *, limit: int = 4) -> dict:
    if type(limit) is not int or not 1 <= limit <= 4:
        raise common.PipelineError("domain plan capacity must be between one and four authors")
    root, campaign = topify_seed.load_campaign(workspace)
    playbook = load_playbook(playbook_path)
    status = topify_seed.campaign_status(root)
    available, shelved = _eligible(root)
    dispatched = set()
    prior_claims = []
    claims = root / "author-jobs"
    if claims.is_symlink() or claims.exists() and not claims.is_dir():
        raise common.PipelineError("author job claims directory is unsafe")
    if claims.is_dir():
        for file in sorted(claims.glob("*.json")):
            item = common.load_json(file)
            seed_id = item.get("seed_id")
            if isinstance(seed_id, str):
                topify_seed._safe_name(seed_id)
                dispatched.add(seed_id)
                prior_claims.append(item)
    for category in available:
        planned = [_next_author_identity(root, item, prior_claims) for item in available[category]]
        available[category] = sorted((item for item in planned if item is not None),
                                     key=lambda row: (row["revision_index"] == 0,
                                                      row["screen_status"] != "rights-and-dedup-screened",
                                                      row["seed_id"]))
    eligible_counts = {category: len(items) for category, items in available.items()}

    targets = campaign["prototype_targets"]
    current = status["counts"]
    progress = {category: max(current["candidate_seeds"].get(category, 0),
                              current["prototypes"].get(category, 0),
                              current["promoted"].get(category, 0))
                for category in common.CATEGORIES}
    categories = sorted(common.CATEGORIES, key=lambda cat: (
        not any(item["revision_index"] > 0 for item in available[cat]),
        bool(progress[cat]), progress[cat] / max(1, targets[cat]),
        -targets[cat], cat))
    selected = []
    # First pass covers distinct under-represented domains; only a later pass
    # reuses a domain when other eligible domains have no remaining Seeds.
    while len(selected) < limit:
        picked = False
        for category in categories:
            if (not available[category] or (targets[category] <= progress[category]
                                           and available[category][0]["revision_index"] == 0)
                    or len(selected) >= limit):
                continue
            item = available[category].pop(0)
            play = playbook["domains"][category]
            selected.append({**item, "cheap_controls": play["cheap_controls"],
                             "task_mechanisms": play["task_mechanisms"],
                             "needs_main_review": True})
            if item["revision_index"] == 0:
                progress[category] += 1
            picked = True
        if not picked:
            break
    candidate_counts = Counter(item["category"] for item in selected)
    promoted = current["promoted"]
    coverage_ready = (sum(promoted.values()) >= campaign["config"]["target_task_count"]
                      and all(promoted.get(category, 0) > 0 for category in common.CATEGORIES))
    return {"schema_version": 1,
            "campaign_id": campaign["config"]["campaign_id"],
            "campaign_coverage_status": "complete" if coverage_ready else "incomplete",
            "task_generation_status": ("authoring-suggestions-only" if selected
                                       else "no-eligible-seeds"),
            "category_targets": campaign["config"]["category_targets"],
            "eligible_by_domain": {category: eligible_counts[category]
                                   for category in sorted(common.CATEGORIES)},
            "selected_by_domain": dict(sorted(candidate_counts.items())),
            "selected": selected,
            "unavailable_domains": sorted(cat for cat in common.CATEGORIES
                                           if not eligible_counts[cat]),
            "already_dispatched_seed_ids": sorted(dispatched),
            "shelved_seed_ids": shelved,
            "discovery_needed": {
                cat: playbook["domains"][cat]["source_pools"]
                for cat in sorted(common.CATEGORIES)
                if not eligible_counts[cat]
            },
            "shortfalls": status["shortfalls"],
            "caveat": "Planning from screened/provisional Seed evidence only; this creates no tasks, grants no rights, and does not approve a task family."}
