#!/usr/bin/env python3
"""Audit local GEO evidence; never sends a question or edits raw records.

python3 evidence_audit.py BATCH --output NEW_DIRECTORY
Read tasks.csv, answers/<task_id>_提取.json, observations/<task_id>.json,
and optional 段落引用.csv. Human annotations are claims, not automatic truth.
Only supported live consumer observations enter measured denominators.
"""
import argparse
import csv
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
import sys
from collections import Counter, defaultdict
from urllib.parse import urlsplit

DIMENSIONS = ("answer", "reference_panel", "landing_urls", "paragraph_map", "source_archives")
STAGES = ("reference_list", "body_citation", "brand_mention", "brand_recommendation", "brand_citation_support", "hard_facts")
STATES = {"complete", "partial", "missing", "unknown", "not_exposed", "not_applicable", "blocked"}
VALUES = {"yes", "not_observed", "unknown"}
FACT_VALUES = {"correct", "incorrect", "unverified", "not_applicable"}
REQUIRED_COVERAGE = {"reference_list": "reference_panel", "body_citation": "paragraph_map", "brand_mention": "answer", "brand_recommendation": "answer", "brand_citation_support": "paragraph_map", "hard_facts": "answer"}


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8-sig")) if path.exists() else {}


def compact(text):
    return re.sub(r"\s+", "", str(text or ""))


def aware_time(value):
    try:
        return bool(datetime.fromisoformat(str(value).replace("Z", "+00:00")).tzinfo)
    except (ValueError, TypeError):
        return False


def session_keys(note, raw):
    """Use both explicit identifiers; a page homepage is not a chat identifier."""
    keys = set()
    sid = note.get("session_id")
    url = note.get("conversation_url")
    if sid:
        keys.add(("id", str(sid)))
    if url:
        keys.add(("url", str(url)))
    if not keys and raw.get("url"):
        keys.add(("url", str(raw["url"])))
    return keys


def evidence_ok(entries, batch):
    """Check local existence/hash/locator only; human review owns meaning."""
    if not isinstance(entries, list) or not entries:
        return False
    for item in entries:
        if not isinstance(item, dict) or not item.get("path") or not item.get("locator"):
            return False
        path = Path(item["path"])
        path = path if path.is_absolute() else batch / path
        if not path.is_file():
            return False
        if item.get("sha256") and hashlib.sha256(path.read_bytes()).hexdigest() != item["sha256"]:
            return False
    return True


def write_csv(path, rows, fields):
    with path.open("x", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def audit(batch, output):
    with (batch / "tasks.csv").open(encoding="utf-8-sig", newline="") as f:
        tasks = list(csv.DictReader(f))
    if not tasks:
        raise ValueError("tasks.csv has no tasks")
    ids = [t.get("task_id", "") for t in tasks]
    if any(not re.fullmatch(r"[A-Za-z0-9_-]+", x) for x in ids) or len(ids) != len(set(ids)):
        raise ValueError("task_id must be unique and filename-safe")
    if output.exists():
        raise ValueError(f"output exists; choose a new directory: {output}")
    mapped = defaultdict(list)
    mapping_path = batch / "段落引用.csv"
    if mapping_path.exists():
        with mapping_path.open(encoding="utf-8-sig", newline="") as f:
            for row in csv.DictReader(f):
                if row.get("引用状态") == "已确认":
                    mapped[row.get("task_id", "")].append(row)
    records = []
    input_hashes = {}
    for task in tasks:
        tid = task["task_id"]
        note_path = batch / "observations" / f"{tid}.json"
        note_bytes = note_path.read_bytes() if note_path.exists() else b""
        note = json.loads(note_bytes.decode("utf-8-sig")) if note_bytes else {}
        if not isinstance(note, dict):
            raise ValueError(f"observation must be a JSON object: {tid}")
        if note and note.get("task_id") != tid:
            raise ValueError(f"observation task_id mismatch: {tid}")
        if note and note.get("schema_version") != 1:
            raise ValueError(f"unsupported observation schema: {tid}")
        capture_path = batch / "answers" / f"{tid}_提取.json"
        if "capture_file" in note:
            selection = Path(note["capture_file"])
            capture_path = (batch / selection).resolve()
            if not note["capture_file"] or selection.is_absolute() or not capture_path.is_relative_to(batch.resolve()) or not capture_path.is_file():
                raise ValueError(f"invalid or missing selected capture: {tid}")
        capture_bytes = capture_path.read_bytes() if capture_path.exists() else b""
        raw = json.loads(capture_bytes.decode("utf-8-sig")) if capture_bytes else {}
        if not isinstance(raw, dict) or not isinstance(note, dict):
            raise ValueError(f"raw and observation must be JSON objects: {tid}")
        input_hashes[tid] = {"capture_sha256": hashlib.sha256(capture_bytes).hexdigest() if capture_bytes else "", "observation_sha256": hashlib.sha256(note_bytes).hexdigest() if note_bytes else "", "capture_file": capture_path.relative_to(batch).as_posix()}
        records.append((task, raw, note))
    sessions = Counter()
    for task, raw, note in records:
        if note.get("evidence_kind") == "live_capture":
            for identity in session_keys(note, raw):
                sessions[(task.get("platform", task.get("平台", "")), identity)] += 1
    summaries, gaps, actions = [], [], []
    groups = {}
    coverage_rows = {dim: {"planned": len(tasks), "states": Counter(), "captured_known": 0, "expected_known": 0, "samples_with_known_count": 0} for dim in DIMENSIONS}
    for task, raw, note in records:
        tid = task["task_id"]
        question = task.get("question_id", task.get("题号", ""))
        platform = task.get("platform", task.get("平台", ""))
        kind = note.get("evidence_kind", "unknown")
        origin = note.get("capture_origin", "unknown")
        ctx = note.get("context", {})
        ev = note.get("evidence", {})
        coverage = note.get("coverage", {})
        if any(not isinstance(v, dict) for v in (ctx, ev, coverage, note.get("outcomes", {}))):
            raise ValueError(f"{tid}: context, evidence, coverage and outcomes must be objects")
        outcomes = {s: note.get("outcomes", {}).get(s, "unverified" if s == "hard_facts" else "unknown") for s in STAGES}
        issues = []
        states = {}
        for dim in DIMENSIONS:
            cv = coverage.get(dim, {})
            if not isinstance(cv, dict):
                raise ValueError(f"{tid}: coverage.{dim} must be an object")
            state = cv.get("state", "unknown")
            if state not in STATES:
                raise ValueError(f"{tid}: invalid coverage state {state}")
            states[dim] = state
            stats = coverage_rows[dim]
            stats["states"][state] += 1
            captured, expected = cv.get("captured"), cv.get("expected")
            if captured is not None or expected is not None:
                if captured is not None and (type(captured) is not int or captured < 0):
                    raise ValueError(f"{tid}: invalid captured count")
                if expected is not None and (type(expected) is not int or expected < 0):
                    raise ValueError(f"{tid}: invalid expected count")
                if captured is not None and expected is not None:
                    if captured > expected:
                        raise ValueError(f"{tid}: captured > expected")
                    if state == "complete" and captured != expected:
                        raise ValueError(f"{tid}: complete coverage has unequal counts")
                    stats["captured_known"] += captured
                    stats["expected_known"] += expected
                    stats["samples_with_known_count"] += 1
            if state in {"partial", "missing", "unknown", "blocked"}:
                gaps.append({"task_id": tid, "question_id": question, "platform": platform, "dimension": dim, "state": state, "action": "报告限制，保留原会话" if state == "blocked" else "只补本项，保留原始文件", "evidence_kind": kind, "reason": cv.get("note", "")})
        identities = session_keys(note, raw)
        basic_reasons = []
        if kind != "live_capture" or origin != "consumer_ui":
            basic_reasons.append("非现场消费者入口")
        if not aware_time(note.get("observed_at")):
            basic_reasons.append("缺带时区的采样时间")
        if not identities:
            basic_reasons.append("缺会话定位")
        elif kind == "live_capture" and any(sessions[(platform, identity)] > 1 for identity in identities):
            basic_reasons.append("复用了同一会话")
        if ctx.get("independent_session") is not True:
            basic_reasons.append("独立会话未确认")
        if not ctx.get("entry") or ctx.get("web") not in {"on", "off"}:
            basic_reasons.append("入口或联网设置未知")
        if states["answer"] != "complete" or not isinstance(raw.get("text"), str) or not raw.get("text") or not evidence_ok(ev.get("answer"), batch):
            basic_reasons.append("缺完整正文及其存档证据")
        fresh_mappings = [r for r in mapped[tid] if all(r.get(k, "") == v for k, v in input_hashes[tid].items())]
        if len(fresh_mappings) < len(mapped[tid]):
            issues.append("映射表输入指纹过期或缺失，重新运行 map_citations.py")
        confirmed = [r for r in fresh_mappings if r.get("身份匹配") in {"公开链接精确匹配", "同平台完整作品ID"}]
        recommendation = note.get("recommendation", {})
        if not isinstance(recommendation, dict):
            raise ValueError(f"{tid}: recommendation must be an object")
        decisions = {}
        for stage, value in outcomes.items():
            if value not in (FACT_VALUES if stage == "hard_facts" else VALUES):
                raise ValueError(f"{tid}: invalid outcome {stage}={value}")
            ok = not basic_reasons and value not in {"unknown", "unverified"}
            reasons = list(basic_reasons)
            if ok and states[REQUIRED_COVERAGE[stage]] != "complete":
                reasons.append("该层覆盖未完整")
                ok = False
            if ok and not evidence_ok(ev.get(stage), batch):
                reasons.append("缺本层证据文件或定位")
                ok = False
            if ok and stage in {"body_citation", "brand_citation_support"} and value == "yes" and not confirmed:
                reasons.append("没有已确认的我方段落引用映射")
                ok = False
            if ok and stage == "brand_recommendation" and value == "yes":
                quote = compact(recommendation.get("quote"))
                if outcomes["brand_mention"] != "yes":
                    reasons.append("推荐与品牌提及状态冲突")
                    ok = False
                if not quote or quote not in compact(raw.get("text")) or not recommendation.get("position"):
                    reasons.append("推荐原句或位置未核对")
                    ok = False
            if ok and stage == "brand_citation_support" and value == "yes":
                quote = compact(recommendation.get("quote"))
                if outcomes["body_citation"] != "yes" or outcomes["brand_recommendation"] != "yes" or outcomes["brand_mention"] != "yes":
                    reasons.append("支持关系与正文引用或推荐状态冲突")
                    ok = False
                if not quote or not any(quote in compact(r.get("段落原文")) for r in confirmed):
                    reasons.append("推荐句没有对应到我方引用段")
                    ok = False
            if ok and stage == "hard_facts" and value in {"correct", "incorrect"}:
                facts = note.get("fact_checks", [])
                if note.get("hard_fact_review_complete") is not True or not isinstance(facts, list) or not facts or not all(isinstance(f, dict) and f.get("claim") and compact(f["claim"]) in compact(raw.get("text")) and f.get("fact_id") and f.get("verdict") in {"correct", "incorrect"} and evidence_ok(f.get("evidence"), batch) for f in facts):
                    reasons.append("硬事实尚未逐条核对")
                    ok = False
                elif (value == "correct") != all(f["verdict"] == "correct" for f in facts):
                    reasons.append("硬事实总状态与明细冲突")
                    ok = False
            decisions[stage] = ok
            if not ok and value not in {"unknown", "unverified"}:
                issues.append(stage + ":" + "、".join(reasons))
        key_data = {"question_id": question, "question_type": note.get("question_type", "unknown"), "platform": platform, "phase": note.get("phase", "unknown"), "window": note.get("window") or str(note.get("observed_at") or "unknown")[:10], "entry": ctx.get("entry", "unknown"), "model": ctx.get("model", "unknown"), "thinking_mode": ctx.get("thinking_mode", "unknown"), "web": ctx.get("web", "unknown"), "memory": ctx.get("memory", "unknown"), "personalization": ctx.get("personalization", "unknown"), "account_alias": ctx.get("account_alias", "unknown"), "region": ctx.get("region", "unknown")}
        key = json.dumps(key_data, ensure_ascii=False, sort_keys=True)
        if key not in groups:
            groups[key] = {**key_data, "planned": 0, "eligible_answers": 0, "excluded": Counter(), "stages": {s: {"yes": 0, "not_observed": 0, "unknown_or_excluded": 0, "correct": 0, "incorrect": 0, "not_applicable": 0} for s in STAGES}, "accurate_recommendation": {"numerator": 0, "denominator": 0}, "accurate_recommendation_with_own_support": {"numerator": 0, "denominator": 0}}
        g = groups[key]
        g["planned"] += 1
        g["eligible_answers"] += int(not basic_reasons)
        for reason in basic_reasons:
            g["excluded"][reason] += 1
        for s in STAGES:
            g["stages"][s][outcomes[s] if decisions[s] else "unknown_or_excluded"] += 1
        # A positive recommendation with unreviewed facts remains unknown.
        # An observed lack of recommendation is a valid negative for this metric.
        rec = outcomes["brand_recommendation"]
        rec_known = decisions["brand_recommendation"]
        correct = outcomes["hard_facts"] == "correct" and decisions["hard_facts"]
        rec_eligible = rec_known and (rec == "not_observed" or decisions["hard_facts"] and outcomes["hard_facts"] in {"correct", "incorrect"})
        supported_eligible = rec_eligible and (rec == "not_observed" or decisions["brand_citation_support"])
        for metric, eligible, hit in [("accurate_recommendation", rec_eligible, rec == "yes" and correct), ("accurate_recommendation_with_own_support", supported_eligible, rec == "yes" and correct and outcomes["brand_citation_support"] == "yes")]:
            g[metric]["denominator"] += int(eligible)
            g[metric]["numerator"] += int(eligible and hit)
        action = "先补本题缺失证据，再判断内容"
        hypothesis = "采集覆盖不明时，无法判定内容问题"
        if outcomes["hard_facts"] == "incorrect":
            action = "逐条对照事实库，定位错误来源并建纠错任务"
            hypothesis = "可能存在错名、版本混用或来源错误，需检查原句"
        elif outcomes["body_citation"] == "yes" and outcomes["brand_mention"] == "not_observed":
            action = "先核题意是否需要品牌，再对照被引方法段及同页品牌段"
            hypothesis = "题意、品牌相关性、段落可读性和生成波动均待检查"
        elif outcomes["brand_recommendation"] == "yes":
            action = "保留有效段落，核对推荐句的引用关系与硬事实"
            hypothesis = "同时出现品牌和来源仍需确认是否相互支持"
        elif outcomes["reference_list"] == "yes":
            action = "补正文标记与来源映射，核实来源支持哪个回答段"
            hypothesis = "参考列表出现本身不足以说明正文使用"
        targets = {(r.get("网址", ""), r.get("段落原文", "")): r for r in confirmed} or {("", ""): {}}
        for (target_url, target_paragraph), target in targets.items():
            actions.append({"task_id": tid, "question_id": question, "platform": platform, "evidence_kind": kind, "target_url": target_url, "source_channel": urlsplit(target_url).hostname or "", "work_id": target.get("作品ID", ""), "target_paragraph": target_paragraph, "action": action, "hypothesis": hypothesis, "state": "待人工决定"})
        summaries.append({"task_id": tid, "question_id": question, "platform": platform, "sample_index": task.get("sample_index", "1"), "evidence_kind": kind, "capture_origin": origin, "observed_at": note.get("observed_at", ""), "eligible_answer": "yes" if not basic_reasons else "no", **outcomes, "counted_stages": ",".join(s for s in STAGES if decisions[s]), "gaps": "; ".join(basic_reasons + issues)})
    for g in groups.values():
        for metric in ("accurate_recommendation", "accurate_recommendation_with_own_support"):
            m = g[metric]
            m["rate"] = m["numerator"] / m["denominator"] if m["denominator"] else None
        for stage, values in g["stages"].items():
            if stage == "hard_facts":
                n, d = values["correct"], values["correct"] + values["incorrect"]
            else:
                n, d = values["yes"], values["yes"] + values["not_observed"]
            values.update(numerator=n, denominator=d, rate=n / d if d else None)
    output.mkdir(parents=True, exist_ok=False)
    write_csv(output / "样本分层.csv", summaries, list(summaries[0]))
    write_csv(output / "补采清单.csv", gaps, ["task_id", "question_id", "platform", "dimension", "state", "action", "evidence_kind", "reason"])
    write_csv(output / "下一步候选.csv", actions, list(actions[0]))
    report = {"schema_version": 1, "planned_tasks": len(tasks), "raw_files_found": sum(bool(raw) for _, raw, _ in records), "observation_notes_found": sum(bool(n) for _, _, n in records), "coverage": coverage_rows, "groups": list(groups.values()), "limitations": ["只检查本地文件、字段和已登记关系；不能代替人工真实性与支持度复核。", "unknown、未暴露、失败及材料转述不当作未推荐。", "各题、入口、设置和时间窗单列；重复会话全部排除。", "没有运行真实问答就没有效果结论；未抓取和未收录不能从答案缺链推断。"]}
    (output / "证据审查.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("batch", type=Path)
    ap.add_argument("--output", type=Path, required=True)
    a = ap.parse_args()
    try:
        report = audit(a.batch.resolve(), a.output.resolve())
    except (OSError, ValueError, TypeError, KeyError, csv.Error) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    eligible = sum(g["eligible_answers"] for g in report["groups"])
    print(f"planned={report['planned_tasks']}; eligible_live_answers={eligible}; output={a.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
