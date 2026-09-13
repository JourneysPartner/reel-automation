"""
国税庁ソースデータベースから、トピックに関連する公式情報を取得する。
hp-vlog プロジェクトのクロール済みデータ（data/nta-sources/）を参照し、
カルーセル生成時に正確な税務根拠をプロンプトへ注入する。
"""

from __future__ import annotations

import json
import os
import re
import unicodedata
from pathlib import Path
from typing import Any

_DEFAULT_SOURCES_DIR = Path(
    os.environ.get(
        "NTA_SOURCES_DIR",
        str(
            Path(os.environ.get("USERPROFILE", os.environ.get("HOME", "")))
            / "HP・LP作成"
            / "hp-vlog"
            / "data"
            / "nta-sources"
        ),
    )
)

# nta-qa は nta-sources の兄弟ディレクトリ。個別に上書きもできる。
_DEFAULT_QA_DIR = Path(
    os.environ.get("NTA_QA_DIR", str(_DEFAULT_SOURCES_DIR.parent / "nta-qa"))
)

# ペルソナごとに参照する税目を絞る。
# ※ 絞りすぎると「正解の資料が採点前に捨てられる」事故が起きる（過去2件発生）。
#    - wealth_holder に shotoku が無く、外国税額控除(No.1240)が引けなかった
#    - influencer に shohi が無く、越境デジタル役務の資料が全件除外された
#    消費税(shohi)はどのペルソナにも関係しうるため、全ペルソナで参照可能にしている。
_PERSONA_TO_CATEGORIES: dict[str, list[str]] = {
    "ec_seller": ["shohi", "shotoku"],
    "freelancer": ["shotoku", "shohi"],
    "influencer": ["shotoku", "gensen", "shohi"],
    "smb_owner": ["shotoku", "shohi", "hojin"],
    "wealth_holder": ["shotoku", "sozoku", "zoyo", "hyoka", "shohi"],
}

_STOP_WORDS = frozenset(
    [
        "について", "場合", "とは", "制度", "取扱い", "方法", "手続き",
        "消費税", "所得税", "相続税", "贈与税", "法人税", "源泉所得税",
        "こと", "もの", "ため", "など", "等", "する", "した", "して",
    ]
)

_RE_KANJI = re.compile(r"[一-鿿々ヶ]+")
_RE_KATAKANA = re.compile(r"[゠-ヿー]+")


def _normalize(text: str) -> str:
    return unicodedata.normalize("NFKC", text).lower()


def _tokenize(text: str) -> set[str]:
    normalized = _normalize(text)
    tokens: set[str] = set()
    for m in _RE_KANJI.finditer(normalized):
        word = m.group()
        if len(word) >= 2 and word not in _STOP_WORDS:
            tokens.add(word)
        if len(word) >= 2:
            for i in range(len(word) - 1):
                bigram = word[i : i + 2]
                if bigram not in _STOP_WORDS:
                    tokens.add(bigram)
    for m in _RE_KATAKANA.finditer(normalized):
        if len(m.group()) >= 2:
            tokens.add(m.group())
    return tokens


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a and not b:
        return 0.0
    shared = len(a & b)
    return shared / (len(a) + len(b) - shared or 1)


def _load_index(sources_dir: Path, qa_dir: Path | None = None) -> list[dict] | None:
    """nta-sources と nta-qa の index を読み、由来を _base_dir に持たせて統合する。"""
    merged: list[dict] = []
    for base_dir in [sources_dir, qa_dir or _DEFAULT_QA_DIR]:
        index_path = base_dir / "index.json"
        if not index_path.exists():
            continue
        try:
            data = json.loads(index_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue  # 壊れた index は無視して他方を使う
        entries = data.get("entries") if isinstance(data, dict) else None
        for e in entries or []:
            if e:
                merged.append({**e, "_base_dir": str(base_dir)})
    return merged or None


def _load_source_file(base_dir: Path | str, file_path: str) -> dict | None:
    full_path = Path(base_dir) / file_path
    if not full_path.exists():
        return None
    try:
        return json.loads(full_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _build_excerpt(source: dict) -> str:
    """タックスアンサー/質疑応答は sections、Q&A・パンフレットは body に本文を持つ。"""
    sections = source.get("sections", {}) or {}
    parts: list[str] = []
    overview = sections.get("概要", "")
    calc_info = sections.get("計算方法・計算式", "")
    target_info = sections.get("対象者または対象物", "")
    notes = sections.get("注意事項", "")
    if overview:
        parts.append(overview[:1500])
    if calc_info:
        parts.append(calc_info[:800])
    if target_info and len(target_info) < 500:
        parts.append(target_info)
    if notes and len(notes) < 500:
        parts.append(notes)
    if not parts and source.get("body"):
        parts.append(str(source["body"])[:1800])
    return "\n".join(parts)


def resolve_nta_sources(
    post: dict[str, Any],
    *,
    max_sources: int = 3,
    sources_dir: Path | None = None,
) -> dict[str, Any]:
    """schedule.yaml のエントリからトピックに関連する NTA ソースを検索する。"""
    src_dir = sources_dir or _DEFAULT_SOURCES_DIR
    entries = _load_index(src_dir)
    if entries is None:
        return {"refs": [], "ref_text": ""}

    query_text = " ".join(
        filter(None, [post.get("topic"), post.get("angle"), post.get("target_persona")])
    )
    query_tokens = _tokenize(query_text)
    categories = _PERSONA_TO_CATEGORIES.get(post.get("target_persona", ""), [])

    accepted_types = {"taxanswer", "qa"}
    pool = [
        e
        for e in entries
        if e
        and e.get("type") in accepted_types
        and not e.get("deleted")
        and e.get("title")
        and e.get("url")
        and (not categories or e.get("tax_category_code") in categories)
    ]

    scored = sorted(
        [
            {**e, "score": _jaccard(query_tokens, _tokenize(e["title"]))}
            for e in pool
        ],
        key=lambda x: -x["score"],
    )[:max_sources]

    refs: list[dict[str, Any]] = []
    for candidate in scored:
        if candidate["score"] < 0.15:
            continue
        source = _load_source_file(candidate.get("_base_dir", src_dir), candidate["file_path"])
        if not source:
            continue

        refs.append(
            {
                "no": source.get("id", ""),
                "title": source.get("title_full") or source.get("title", ""),
                "url": source.get("url", ""),
                # タックスアンサーは law_version、Q&A・パンフレットは source_label
                "law_version": source.get("law_version") or source.get("source_label", ""),
                "excerpt": _build_excerpt(source),
                "score": candidate["score"],
            }
        )

    if not refs:
        return {"refs": [], "ref_text": ""}

    lines = ["【税務参考資料（国税庁タックスアンサーより）】"]
    for ref in refs:
        lines.append(f"\n■ {ref['title']}（{ref['law_version']}）")
        lines.append(f"  URL: {ref['url']}")
        lines.append(ref["excerpt"])
    lines.append(
        "\n※ 上記の参考資料に記載された数値・税率・要件は正確な公式情報です。"
        "スライドに数字を使う場合は必ずこの資料と整合させてください。"
        "資料にない数字を推測で入れないでください。"
    )

    return {"refs": refs, "ref_text": "\n".join(lines)}
