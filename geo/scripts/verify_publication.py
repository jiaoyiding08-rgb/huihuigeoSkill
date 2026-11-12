#!/usr/bin/env python3
"""Compare approved passages with an archived public text, without network access.

python3 verify_publication.py 发布核验.json --output NEW.json
Paths in the manifest are relative to that manifest. This checks text and
human-recorded access evidence, never crawler access, indexing or AI use.
"""
import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
import sys
from urllib.parse import urlsplit


def normalize(text):
    return re.sub(r"\s+", "", text or "")


def read_snapshot(path):
    text = path.read_text(encoding="utf-8-sig")
    if path.suffix.lower() == ".json":
        data = json.loads(text)
        if not isinstance(data, dict) or not isinstance(data.get("text"), str):
            raise ValueError("snapshot JSON must contain a string text field")
        return data["text"]
    return text


def aware(value):
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).tzinfo is not None
    except (ValueError, TypeError):
        return False


def verify(path):
    spec = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(spec, dict):
        raise ValueError("manifest must be a JSON object")
    if spec.get("schema_version") != 1:
        raise ValueError("unsupported manifest schema")
    if not spec.get("work_id"):
        raise ValueError("work_id required; use a stable internal ID while platform ID is unknown")
    approved = spec.get("approved_file")
    captured = spec.get("snapshot_file")
    if not approved or not captured:
        raise ValueError("approved_file and snapshot_file are required")
    def resolve(value):
        p = Path(value)
        return p if p.is_absolute() else path.parent / p
    original_path, snapshot_path = resolve(approved), resolve(captured)
    original, snapshot = read_snapshot(original_path), read_snapshot(snapshot_path)
    approved_hash = hashlib.sha256(original_path.read_bytes()).hexdigest()
    snapshot_hash = hashlib.sha256(snapshot_path.read_bytes()).hexdigest()
    expected = spec.get("approved_sha256")
    approved_version_match = expected == approved_hash if expected else None
    if expected and not approved_version_match:
        raise ValueError("approved source hash changed; make a new review manifest")
    sections = spec.get("sections", [])
    if not isinstance(sections, list) or not sections or not all(isinstance(s, dict) for s in sections):
        raise ValueError("at least one approved passage is required")
    ids = [s.get("id") for s in sections]
    if any(not i for i in ids) or len(ids) != len(set(ids)):
        raise ValueError("section IDs must be nonempty and unique")
    rows = []
    for section in sections:
        quote = section.get("quote", "")
        if not isinstance(quote, str) or not normalize(quote):
            raise ValueError("every section must have a nonempty quote")
        in_approved = normalize(quote) in normalize(original)
        in_public = normalize(quote) in normalize(snapshot)
        rows.append({"id": section["id"], "kind": section.get("kind", "unknown"), "fact_ids": section.get("fact_ids", []), "quote": quote, "in_approved": in_approved, "in_public_snapshot": in_public, "status": "matched" if in_approved and in_public else "needs_review", "quote_sha256": hashlib.sha256(quote.encode()).hexdigest()})
    public_url = spec.get("public_url", "")
    url = urlsplit(public_url)
    access = spec.get("access", {})
    if not isinstance(access, dict):
        raise ValueError("access must be a JSON object")
    access_path = resolve(access["evidence_file"]) if access.get("evidence_file") else None
    evidence_present = bool(access_path and access_path.is_file())
    # The assertion is explicitly human-reported; a URL or snapshot alone is insufficient.
    readability_recorded = bool(url.scheme in {"https", "http"} and url.hostname and access.get("status") == "readable" and access.get("context") and access.get("locator") and aware(spec.get("checked_at")) and evidence_present)
    return {"schema_version": 1, "work_id": spec["work_id"], "public_url": public_url, "published_at": spec.get("published_at"), "checked_at": spec.get("checked_at"), "evidence_kind": spec.get("evidence_kind", "unknown"), "public_readable": "human_observed" if readability_recorded and spec.get("evidence_kind") == "live_capture" else "unverified", "access_assertion_supported_by_local_record": readability_recorded, "approved_sha256": approved_hash, "snapshot_sha256": snapshot_hash, "approved_version_match": approved_version_match, "sections": rows, "all_sections_match": all(r["status"] == "matched" for r in rows), "crawl_seen": "unverified", "index_seen": "unverified", "ai_citation_seen": "unverified", "limitations": ["仅检查指定关键段；没有进行网页访问或全篇事实核验。", "忽略空白比较；图片文字、排版、顺序及省略的段落仍需人工检查。", "公开可读来自所登记的现场证据，爬虫、收录和 AI 引用均需独立证据。"]}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("manifest", type=Path)
    ap.add_argument("--output", type=Path, required=True)
    a = ap.parse_args()
    try:
        if a.output.exists():
            raise ValueError("output already exists; keep the previous result and use a new path")
        report = verify(a.manifest.resolve())
        a.output.parent.mkdir(parents=True, exist_ok=True)
        with a.output.open("x", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
            f.write("\n")
    except (OSError, ValueError, TypeError, KeyError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(f"passages={len(report['sections'])}; matched={report['all_sections_match']}; public_readable={report['public_readable']}")
    return 0 if report["all_sections_match"] else 1


if __name__ == "__main__":
    sys.exit(main())
