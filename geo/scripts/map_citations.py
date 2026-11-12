#!/usr/bin/env python3
"""Export evidence-backed paragraph mappings and unverified source candidates.

Legacy CLI and the first seven CSV columns are retained:
  python3 map_citations.py BATCH --brand 品牌全称,品牌简称 --own 发布台账.csv

Legacy text/links cannot prove a paragraph-to-source relation. Such links are
exported as 候选来源, with no invented paragraph. Keep captures immutable. Put
manual citations in observations/<task_id>.json; optional capture_file selects
a new, batch-relative capture snapshot from the same conversation. Missing
explicit snapshots are errors, never a reason to silently use an older file.
Legacy captures that already contain citations are still read. Citation fields:
  paragraph_id, paragraph_text, source_url, source_id, marker,
  evidence_method, verified, evidence_ref (or evidence_refs/provenance).
Only verified=true plus an explicit relation, an allowed method, and a recorded
evidence locator is 已确认. This validates the record's completeness; a human
must still check the locator. It does not prove the citation caused a mention.
Every exported row ends with capture_sha256, observation_sha256, capture_file.
Rebuild the derived CSV whenever either source changes; stale rows are ineligible.
"""

import argparse
import csv
import hashlib
import json
from pathlib import Path
import re
import sys
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


FIELDS = ["题号", "平台", "段落", "提到品牌", "网址", "我方文章", "段落原文",
          "task_id", "sample_index", "引用状态", "证据方法", "作品ID", "证据定位",
          "source_id", "marker", "身份匹配", "备注",
          "capture_sha256", "observation_sha256", "capture_file"]
VERIFIED_METHODS = {"manual_verified", "dom_explicit_link"}
TRACKING_KEYS = {"fbclid", "gclid", "dclid", "msclkid", "mc_cid", "mc_eid"}


def canonical_url(value):
    """Strict comparison key. Retain business queries, scheme, path and ordering."""
    if not isinstance(value, str):
        return ""
    try:
        u = urlsplit(value.strip())
        if u.scheme.lower() not in {"http", "https"} or not u.hostname or u.username or u.password:
            return ""
        host = u.hostname.lower()
        if ":" in host:
            host = f"[{host}]"
        port = u.port
        if port and not ((u.scheme.lower() == "https" and port == 443)
                         or (u.scheme.lower() == "http" and port == 80)):
            host += f":{port}"
        query = [(k, v) for k, v in parse_qsl(u.query, keep_blank_values=True)
                 if not k.lower().startswith("utm_") and k.lower() not in TRACKING_KEYS]
        return urlunsplit((u.scheme.lower(), host, u.path or "/", urlencode(query), ""))
    except (ValueError, TypeError):
        return ""


def site_article_identity(value):
    """Recognize only explicit, known publisher ID routes; never substring-match."""
    clean = canonical_url(value)
    if not clean:
        return None
    u = urlsplit(clean)
    host, path = u.hostname, u.path
    patterns = {
        "zhuanlan.zhihu.com": ("知乎", r"/p/(\d+)/?"),
        "www.zhihu.com": ("知乎", r"/question/\d+/answer/(\d+)/?"),
        "www.sohu.com": ("搜狐", r"/a/(\d+)_\d+/?"),
        "www.toutiao.com": ("头条", r"/(?:article/|i)(\d+)/?"),
        "m.toutiao.com": ("头条", r"/(?:article/|i)(\d+)/?"),
    }
    if host in patterns:
        platform, pattern = patterns[host]
        match = re.fullmatch(pattern, path)
        return (platform, match.group(1)) if match else None
    if host == "baijiahao.baidu.com" and path.rstrip("/") == "/s":
        values = [v for k, v in parse_qsl(u.query) if k == "id"]
        if len(values) == 1 and values[0].isdigit():
            return ("百家号", values[0])
    return None


def platform_key(value):
    return {"搜狐号": "搜狐", "今日头条": "头条", "头条号": "头条"}.get(value, value)


def load_own(path):
    if not path:
        return []
    with Path(path).open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    for row in rows:
        row["_urls"] = {canonical_url(row.get(key, "")) for key in
                        ("公开链接", "已确认落地链接", "已确认落地URL")}
        row["_urls"].discard("")
        row["_platform"] = platform_key((row.get("平台") or "").strip())
        row["_id"] = (row.get("作品ID") or "").strip()
    return rows


def match_own(url, ledger):
    key = canonical_url(url)
    if not key:
        return "", "", "未知"
    matches = [row for row in ledger if key in row["_urls"]]
    method = "公开链接精确匹配"
    if not matches:
        identity = site_article_identity(url)
        if identity:
            platform, article_id = identity
            matches = [row for row in ledger
                       if row["_platform"] == platform and row["_id"] == article_id]
        method = "同平台完整作品ID"
    unique = {(row.get("标题", ""), row["_id"]) for row in matches}
    if len(unique) == 1:
        title, article_id = next(iter(unique))
        return title, article_id, method
    if len(unique) > 1:
        return "", "", "台账冲突待核实"
    return "", "", "未匹配（不代表他方）"


def evidence_refs(citation):
    refs = []
    for key in ("evidence_ref", "evidence_refs"):
        item = citation.get(key)
        if isinstance(item, str) and item.strip():
            refs.append(item.strip())
        elif isinstance(item, list):
            refs.extend(x.strip() for x in item if isinstance(x, str) and x.strip())
    provenance = citation.get("provenance")
    if isinstance(provenance, str) and provenance.strip():
        refs.append(provenance.strip())
    elif isinstance(provenance, dict):
        for key in ("evidence_ref", "evidence_refs", "path", "screenshot", "selector"):
            item = provenance.get(key)
            if isinstance(item, str) and item.strip():
                refs.append(item.strip())
            elif isinstance(item, list):
                refs.extend(x.strip() for x in item if isinstance(x, str) and x.strip())
    return list(dict.fromkeys(refs))


def string_value(obj, key):
    value = obj.get(key, "")
    return str(value) if value is not None and isinstance(value, (str, int)) else ""


def make_rows(task, data, brands, ledger, input_path):
    rows = []
    related_urls = set()
    task_id = task["task_id"]
    sample = task.get("sample_index") or "1"
    base = {"题号": task.get("question_id", task.get("题号", "")),
            "平台": task.get("platform", task.get("平台", "")),
            "task_id": task_id, "sample_index": sample}
    citations = data.get("citations", [])
    if not isinstance(citations, list):
        raise ValueError(f"{input_path}: citations must be an array")
    for index, citation in enumerate(citations):
        if not isinstance(citation, dict):
            raise ValueError(f"{input_path}: citations[{index}] must be an object")
        paragraph = string_value(citation, "paragraph_text").strip()
        paragraph_id = string_value(citation, "paragraph_id")
        url = string_value(citation, "source_url")
        method = string_value(citation, "evidence_method")
        refs = evidence_refs(citation)
        captured_text = data.get("text", "")
        paragraph_in_capture = (isinstance(captured_text, str) and bool(paragraph)
                                and re.sub(r"\s+", "", paragraph)
                                in re.sub(r"\s+", "", captured_text))
        verified = (citation.get("verified") is True and bool(paragraph_id)
                    and bool(paragraph) and bool(canonical_url(url))
                    and method in VERIFIED_METHODS and bool(refs) and paragraph_in_capture)
        title, article_id, identity = match_own(url, ledger)
        notes = []
        if not verified:
            if citation.get("verified") is not True:
                notes.append("关系未核验")
            if not paragraph_id or not paragraph:
                notes.append("缺段落原文或定位")
            if not canonical_url(url):
                notes.append("缺有效来源网址")
            if method not in VERIFIED_METHODS:
                notes.append("证据方法未获确认")
            if not refs:
                notes.append("缺证据定位")
            if not paragraph_in_capture:
                notes.append("段落原文未在捕获文本中找到")
        rows.append({**base, "段落": paragraph_id,
                     "提到品牌": ("是" if any(b in paragraph for b in brands) else "否") if paragraph else "未知",
                     "网址": url, "我方文章": title, "段落原文": paragraph,
                     "引用状态": "已确认" if verified else "待核实映射",
                     "证据方法": method or "unknown", "作品ID": article_id,
                     "证据定位": " | ".join(refs),
                     "source_id": string_value(citation, "source_id"),
                     "marker": string_value(citation, "marker"),
                     "身份匹配": identity, "备注": "；".join(notes)})
        if canonical_url(url):
            related_urls.add(canonical_url(url))

    # Keep every raw link node in the capture. The derived candidate table may
    # group exact comparison keys but records all original node indices.
    links = data.get("links", [])
    if not isinstance(links, list):
        raise ValueError(f"{input_path}: links must be an array")
    candidates = {}
    for index, link in enumerate(links):
        if not isinstance(link, dict):
            continue
        url = link.get("h", link.get("url", ""))
        key = canonical_url(url)
        if not key or key in related_urls:
            continue
        entry = candidates.setdefault(key, {"url": url, "indices": []})
        entry["indices"].append(index)
    for entry in candidates.values():
        title, article_id, identity = match_own(entry["url"], ledger)
        locators = [f"{input_path}#/links/{i}" for i in entry["indices"]]
        rows.append({**base, "段落": "", "提到品牌": "未知", "网址": entry["url"],
                     "我方文章": title, "段落原文": "", "引用状态": "候选来源",
                     "证据方法": "page_link_candidate", "作品ID": article_id,
                     "证据定位": " | ".join(locators), "source_id": "", "marker": "",
                     "身份匹配": identity,
                     "备注": "页面外链不能证明参考列表收录或正文引用"})
    return rows


def load_capture(batch, task_id):
    """Read raw capture and optional review sidecar without rewriting either."""
    note_path = batch / "observations" / f"{task_id}.json"
    note = {}
    note_bytes = None
    if note_path.exists():
        note_bytes = note_path.read_bytes()
        note = json.loads(note_bytes.decode("utf-8-sig"))
        if not isinstance(note, dict):
            raise ValueError(f"{note_path}: observation must be a JSON object")
    selected = "capture_file" in note
    if selected:
        value = note["capture_file"]
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{note_path}: capture_file must be a nonempty batch-relative path")
        relative = Path(value)
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError(f"{note_path}: capture_file must stay within the batch")
    else:
        relative = Path("answers") / f"{task_id}_提取.json"
    source = batch / relative
    try:
        source.resolve().relative_to(batch.resolve())
    except ValueError:
        raise ValueError(f"{note_path}: capture_file resolves outside the batch") from None
    if not source.is_file():
        if selected:
            raise ValueError(f"{task_id}: selected capture_file is missing or not a file: {relative}; "
                             "refusing to fall back to the old capture")
        return None, relative, {}
    capture_bytes = source.read_bytes()
    data = json.loads(capture_bytes.decode("utf-8-sig"))
    if not isinstance(data, dict):
        raise ValueError(f"{relative}: capture must be a JSON object")
    raw_citations = data.get("citations", [])
    reviewed_citations = note.get("citations", [])
    if not isinstance(raw_citations, list) or not isinstance(reviewed_citations, list):
        raise ValueError(f"{task_id}: citations in capture and observation must be arrays")
    # Exact duplicates can arise when migrating old annotations to a sidecar.
    # Combine in memory only and retain differing evidence records for review.
    merged, seen = [], set()
    for citation in raw_citations + reviewed_citations:
        key = json.dumps(citation, sort_keys=True, ensure_ascii=False)
        if key not in seen:
            seen.add(key)
            merged.append(citation)
    fingerprints = {
        "capture_sha256": hashlib.sha256(capture_bytes).hexdigest(),
        "observation_sha256": hashlib.sha256(note_bytes).hexdigest() if note_bytes is not None else "",
        "capture_file": relative.as_posix(),
    }
    return {**data, "citations": merged}, relative, fingerprints


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("batch", type=Path)
    parser.add_argument("--brand", required=True)
    parser.add_argument("--own", default="")
    parser.add_argument("--platform", default="DeepSeek", help="Platform name, or * for all")
    parser.add_argument("--output", type=Path, help="Derived CSV; defaults to BATCH/段落引用.csv")
    args = parser.parse_args(argv)
    brands = [b.strip() for b in args.brand.split(",") if b.strip()]
    if not brands:
        parser.error("--brand must contain at least one nonempty name")
    try:
        ledger = load_own(args.own)
        with (args.batch / "tasks.csv").open(encoding="utf-8-sig", newline="") as handle:
            tasks = list(csv.DictReader(handle))
        rows, missing = [], []
        for task in tasks:
            platform = task.get("platform", task.get("平台", ""))
            if args.platform != "*" and platform != args.platform:
                continue
            task_id = task.get("task_id", "")
            if not task_id or Path(task_id).name != task_id or task_id in {".", ".."}:
                raise ValueError("task_id must be a nonempty filename component")
            data, relative, fingerprints = load_capture(args.batch, task_id)
            if data is None:
                missing.append(task_id)
                continue
            rows.extend({**row, **fingerprints} for row in
                        make_rows(task, data, brands, ledger, relative.as_posix()))
        output = args.output or args.batch / "段落引用.csv"
        with output.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=FIELDS)
            writer.writeheader()
            writer.writerows(rows)
        confirmed = sum(row["引用状态"] == "已确认" for row in rows)
        print(f"写出 {output}：{len(rows)} 行，其中已确认关系 {confirmed}；"
              f"缺提取文件 {len(missing)}。候选和待核实行不可计入正文引用。")
        if missing:
            print("缺项 task_id：" + ", ".join(missing))
        print("已确认表示记录具备明确关系和证据定位，仍须人工核对证据；不表示真实推荐或效果提升。")
        return 0
    except (OSError, ValueError, csv.Error, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
