#!/usr/bin/env python3
"""Initialize a local GEO batch directory. Never contacts any website or sends a question.

Creates (in a directory that must not exist yet):
  questions.csv     the question snapshot used for this batch
  tasks.csv         the fixed plan: sample-major, question-major, platform-minor order
  platforms.json    per-platform runtime policy, filled in during the first round
  events/ locks/    reserved directories retained for existing project compatibility
  answers/ source_archives/ images/

round retains the old meaning: question position, NOT independent repetition.
sample_index identifies planned independent sessions; --repeats defaults to 1.
This creates a plan only; a task row is not evidence that a session was run.
"""

import argparse
import csv
import json
from pathlib import Path
import sys


DEFAULT_PLATFORMS = "豆包,DeepSeek,千问,元宝,文心"
TASK_FIELDS = ["task_id", "round", "question_id", "platform", "question", "sample_index"]


def load_questions(path):
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        names = set(reader.fieldnames or [])
        id_field = "question_id" if "question_id" in names else "题号"
        question_field = "question" if "question" in names else "问题"
        if id_field not in names or question_field not in names:
            raise ValueError("questions CSV must contain question_id/question or 题号/问题 columns")
        questions = []
        seen = set()
        for line, row in enumerate(reader, start=2):
            question_id = (row.get(id_field) or "").strip()
            question = row.get(question_field) or ""
            if "question_id" in names and "题号" in names:
                if (row.get("题号") or "").strip() != question_id:
                    raise ValueError(f"CSV row {line}: conflicting question_id and 题号")
            if "question" in names and "问题" in names:
                if (row.get("问题") or "") != question:
                    raise ValueError(f"CSV row {line}: conflicting question and 问题")
            if not question_id or not question.strip():
                raise ValueError(f"CSV row {line}: question_id and question must be nonempty")
            if question_id in seen:
                raise ValueError(f"CSV row {line}: duplicate question_id {question_id!r}")
            seen.add(question_id)
            questions.append({"question_id": question_id, "question": question})
    if not questions:
        raise ValueError("questions CSV contains no questions")
    return questions


def parse_platforms(value):
    platforms = [part.strip() for part in value.split(",")]
    if not platforms or any(not part for part in platforms):
        raise ValueError("platform names must be nonempty and comma-separated")
    if len(set(platforms)) != len(platforms):
        raise ValueError("platform names must be unique")
    return platforms


def write_csv(path, fields, rows):
    with path.open("x", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def initialize(questions_path, output, platforms, repeats=1):
    if repeats < 1:
        raise ValueError("repeats must be at least 1")
    questions = load_questions(questions_path)
    platforms = parse_platforms(platforms)
    if output.exists() or output.is_symlink():
        raise ValueError(f"output already exists; refusing to overwrite: {output}")
    output.mkdir(parents=True, exist_ok=False)  # exclusive: protects against a concurrent initializer
    write_csv(output / "questions.csv", ["question_id", "question"], questions)
    tasks = []
    for sample_index in range(1, repeats + 1):
        for round_no, question in enumerate(questions, start=1):
            for platform in platforms:
                tasks.append({
                    "task_id": f"task-{len(tasks) + 1:04d}",
                    "round": round_no,
                    "question_id": question["question_id"],
                    "platform": platform,
                    "question": question["question"],
                    "sample_index": sample_index,
                })
    write_csv(output / "tasks.csv", TASK_FIELDS, tasks)
    policy = {
        "schema_version": 2,
        "planned_repeats": repeats,
        "round_semantics": "question_position",
        "sample_index_semantics": "planned_independent_session",
        "platforms": {
            name: {
                # unknown | safe | unsafe: may a new chat be opened while the previous answer is still generating?
                "new_chat_while_generating": "unknown",
                "evidence": "",
                "entry_url": "",
                "capture_origin": "unknown",
                "account_label": "",
                "network_search": "unknown",
                "thinking_mode": "unknown",
                "memory": "unknown",
                "personalization": "unknown",
                "location_city": "",
                "settings_observed_at": "",
                "settings_evidence_ref": "",
            } for name in platforms
        },
    }
    with (output / "platforms.json").open("x", encoding="utf-8") as handle:
        json.dump(policy, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    for dirname in ("events", "locks", "answers", "source_archives", "images"):
        (output / dirname).mkdir()
    return len(questions), len(platforms), len(tasks)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--questions", required=True, type=Path, help="CSV with question_id,question")
    parser.add_argument("--output", required=True, type=Path, help="New batch directory; must not exist")
    parser.add_argument("--platforms", default=DEFAULT_PLATFORMS, help="Ordered comma-separated platforms")
    parser.add_argument("--repeats", type=int, default=1,
                        help="Planned independent sessions per question/platform (default: 1)")
    args = parser.parse_args(argv)
    try:
        question_count, platform_count, task_count = initialize(
            args.questions, args.output, args.platforms, args.repeats)
    except (OSError, ValueError, csv.Error) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(f"Created {args.output}: {question_count} questions × {platform_count} platforms × "
          f"{args.repeats} planned independent samples = {task_count} tasks; nothing sent. "
          "round is question position, sample_index is the planned repetition.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
