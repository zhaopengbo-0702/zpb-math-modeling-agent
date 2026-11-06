#!/usr/bin/env python3
"""Verify DOI metadata and export an evidence ledger and BibTeX; claims need source reading."""
from __future__ import annotations

import argparse
from datetime import date, datetime, timezone
import hashlib
from html import unescape
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import sys
import tempfile
import time
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen


class PlainText(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


def plain(value: str) -> str:
    parser = PlainText()
    parser.feed(unescape(value))
    return " ".join(" ".join(parser.parts).split())


def first(value: object) -> str:
    if isinstance(value, list):
        return str(value[0]) if value else ""
    return str(value) if value is not None else ""


def normalize_doi(value: str) -> str:
    value = value.strip()
    value = re.sub(r"^https?://(?:dx\.)?doi\.org/", "", value, flags=re.I)
    value = re.sub(r"^doi:\s*", "", value, flags=re.I)
    if not re.fullmatch(r"10\.\d{4,9}/\S+", value):
        raise ValueError(f"Invalid DOI: {value!r}")
    return value


def cache_name(doi: str) -> str:
    return hashlib.sha256(doi.casefold().encode("utf-8")).hexdigest() + ".json"


def atomic_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as stream:
        temporary = Path(stream.name)
        stream.write(text)
    try:
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def get_json(url: str, *, accept: str = "application/json", mailto: str = "", timeout: float = 25) -> tuple[dict, str]:
    agent = "zpb-math-modeling-agent/1.0"
    if mailto:
        agent += f" (mailto:{mailto})"
    request = Request(url, headers={"Accept": accept, "User-Agent": agent})
    for attempt in range(3):
        try:
            with urlopen(request, timeout=timeout) as response:
                return json.loads(response.read().decode("utf-8")), response.geturl()
        except HTTPError as error:
            if error.code not in {429, 500, 502, 503, 504} or attempt == 2:
                raise
        except (URLError, TimeoutError):
            if attempt == 2:
                raise
        time.sleep(0.5 * (2 ** attempt))
    raise RuntimeError("Unreachable retry state")


def publication_year(metadata: dict, as_of: date) -> tuple[int, str, dict]:
    dates = {}
    for field in ("published-print", "published", "issued", "published-online"):
        parts = metadata.get(field, {}).get("date-parts", [])
        if parts and parts[0]:
            raw = parts[0]
            try:
                stamp = date(int(raw[0]), int(raw[1]) if len(raw) > 1 else 1,
                             int(raw[2]) if len(raw) > 2 else 1)
            except (ValueError, TypeError) as error:
                raise ValueError(f"Invalid publication date in {field}") from error
            dates[field] = {"date_parts": raw, "not_future": stamp <= as_of}
    for field in ("published-print", "published", "issued", "published-online"):
        if field in dates and dates[field]["not_future"]:
            return int(dates[field]["date_parts"][0]), field, dates
    raise ValueError("No publication date on or before the verification date")


def validate_metadata(metadata: dict, doi: str, min_year: int, as_of: date) -> dict:
    if normalize_doi(str(metadata.get("DOI", ""))).casefold() != doi.casefold():
        raise ValueError("Crossref DOI does not match the requested DOI")
    kind = metadata.get("type")
    if kind not in {"journal-article", "book", "monograph", "proceedings-article", "book-chapter"}:
        raise ValueError(f"Not a supported formally published work type: {kind!r}")
    title = plain(first(metadata.get("title")))
    authors = metadata.get("author") or []
    if not title or not authors or any(not (item.get("family") or item.get("name")) for item in authors):
        raise ValueError("Metadata needs a title and identifiable authors")
    year, year_source, dates = publication_year(metadata, as_of)
    if year < min_year:
        raise ValueError(f"Publication year {year} is older than the minimum {min_year}")
    journal = plain(first(metadata.get("container-title")))
    pages = str(metadata.get("page") or metadata.get("article-number") or "")
    if kind == "journal-article" and (not journal or not metadata.get("volume") or not pages):
        raise ValueError("Journal metadata needs journal, volume and pages or article number")
    if kind in {"book", "monograph", "book-chapter"} and not metadata.get("publisher"):
        raise ValueError("Book metadata needs a publisher")
    if kind in {"proceedings-article", "book-chapter"} and not journal:
        raise ValueError("Conference/chapter metadata needs a container title")
    return {"doi": doi, "title": title, "authors": authors, "type": kind, "year": year,
            "year_source": year_source, "publication_dates": dates,
            "journal_or_container": journal, "volume": str(metadata.get("volume", "")),
            "issue": str(metadata.get("issue", "")), "pages_or_article_number": pages,
            "publisher": str(metadata.get("publisher", "")),
            "abstract": plain(str(metadata.get("abstract", ""))),
            "publisher_url": metadata.get("resource", {}).get("primary", {}).get("URL"),
            "metadata_status": "verified", "claim_status": "requires-agent-source-review"}


TEX_ESCAPE = {"\\": r"\textbackslash{}", "&": r"\&", "%": r"\%", "$": r"\$",
              "#": r"\#", "_": r"\_", "{": r"\{", "}": r"\}",
              "~": r"\textasciitilde{}", "^": r"\textasciicircum{}"}


def tex(value: str) -> str:
    return "".join(TEX_ESCAPE.get(character, character) for character in value)


def bibtex_entry(key: str, record: dict) -> str:
    kinds = {"journal-article": "article", "book": "book", "monograph": "book",
             "proceedings-article": "inproceedings", "book-chapter": "incollection"}
    authors = []
    for author in record["authors"]:
        if author.get("family"):
            authors.append(tex(author["family"]) + (", " + tex(author["given"]) if author.get("given") else ""))
        else:
            authors.append("{" + tex(author["name"]) + "}")
    fields = {"author": " and ".join(authors), "title": "{" + tex(record["title"]) + "}",
              "year": str(record["year"]), "doi": tex(record["doi"])}
    if record["type"] == "journal-article":
        fields["journal"] = tex(record["journal_or_container"])
    elif record["type"] in {"proceedings-article", "book-chapter"}:
        fields["booktitle"] = tex(record["journal_or_container"])
    for source, target in (("volume", "volume"), ("issue", "number"),
                           ("pages_or_article_number", "pages"), ("publisher", "publisher")):
        if record[source]:
            value = record[source]
            if target == "pages":
                value = re.sub(r"(?<=\d)[-\u2013](?=\d)", "--", value)
            fields[target] = tex(value)
    rows = [f"@{kinds[record['type']]}{{{key},"]
    rows.extend(f"  {name} = {{{value}}}," for name, value in fields.items())
    rows.append("}")
    return "\n".join(rows)


def fetch_record(doi: str, cache: Path, *, offline: bool, resolve_doi: bool,
                 mailto: str = "") -> dict:
    path = cache / cache_name(doi)
    if path.exists():
        payload = json.loads(path.read_text(encoding="utf-8"))
    elif offline:
        raise ValueError(f"Offline cache missing for DOI {doi}")
    else:
        url = "https://api.crossref.org/works/" + quote(doi, safe="")
        response, final_url = get_json(url, mailto=mailto)
        if not isinstance(response.get("message"), dict):
            raise ValueError("Crossref did not return a work metadata record")
        payload = {"doi": doi, "crossref_url": url, "retrieved_url": final_url,
                   "retrieved_utc": datetime.now(timezone.utc).isoformat(), "metadata": response["message"]}
    if resolve_doi and not payload.get("doi_resolution"):
        if offline:
            raise ValueError(f"Offline cache has no DOI.org second-channel check for {doi}")
        resolver_url = "https://doi.org/" + quote(doi, safe="/")
        resolved, final_url = get_json(resolver_url, accept="application/vnd.citationstyles.csl+json", mailto=mailto)
        if normalize_doi(str(resolved.get("DOI", ""))).casefold() != doi.casefold():
            raise ValueError("DOI.org returned a different DOI")
        expected = plain(first(payload["metadata"].get("title")))
        actual = plain(first(resolved.get("title")))
        normalized = lambda title: "".join(character.casefold() for character in title if character.isalnum())
        if normalized(expected) != normalized(actual):
            raise ValueError("DOI.org title does not match Crossref; inspect the sources manually")
        payload["doi_resolution"] = {"url": resolver_url, "retrieved_url": final_url,
                                     "doi": resolved["DOI"], "title": actual, "status": "matched"}
    if not offline:
        atomic_text(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    return payload


def verify_requests(requests: list[dict], *, cache: Path, min_year: int, as_of: date,
                    offline: bool = False, resolve_doi: bool = False, mailto: str = "") -> tuple[dict, str]:
    records, errors, entries = [], [], []
    seen_keys, seen_dois = set(), set()
    for request in requests:
        key = str(request.get("key", ""))
        try:
            if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_.:-]*", key):
                raise ValueError("BibTeX key must start with a letter and contain letters, digits, _, ., :, - only")
            doi = normalize_doi(str(request.get("doi", "")))
            if key in seen_keys or doi.casefold() in seen_dois:
                raise ValueError("Duplicate citation key or DOI")
            seen_keys.add(key)
            seen_dois.add(doi.casefold())
            payload = fetch_record(doi, cache, offline=offline, resolve_doi=resolve_doi, mailto=mailto)
            record = validate_metadata(payload["metadata"], doi, min_year, as_of)
            record.update(key=key, proposed_claim=request.get("claim", ""),
                          citation_location=request.get("location", ""),
                          crossref_url=payload.get("crossref_url"),
                          retrieved_utc=payload.get("retrieved_utc"),
                          doi_resolution=payload.get("doi_resolution"))
            records.append(record)
            entries.append(bibtex_entry(key, record))
        except (ValueError, OSError, KeyError, TypeError, HTTPError, URLError) as error:
            errors.append({"key": key, "doi": request.get("doi"), "error": str(error)})
    if not requests:
        errors.append({"error": "No reference requests supplied"})
    report = {"schema_version": 1, "verified_as_of": as_of.isoformat(), "min_year": min_year,
              "status": "passed" if not errors else "failed", "offline": offline,
              "records": records, "errors": errors,
              "note": "Metadata verification does not establish that a source supports the proposed claim. Read abstracts/full text and record the claim decision separately."}
    return report, "\n\n".join(entries) + ("\n" if entries else "")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("requests", type=Path, help="JSON array of {key, doi, claim, location}.")
    parser.add_argument("--output", type=Path, required=True, help="Evidence JSON, including explicit errors.")
    parser.add_argument("--bibtex", type=Path, help="Written only when every record passes.")
    parser.add_argument("--cache", type=Path, default=Path("notes/reference-cache"))
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--resolve-doi", action="store_true", help="Also compare DOI.org CSL metadata.")
    parser.add_argument("--as-of", type=date.fromisoformat, default=date.today())
    parser.add_argument("--min-year", type=int, help="Default: as-of year minus five.")
    parser.add_argument("--mailto", default="", help="Optional public contact in the HTTP user agent.")
    args = parser.parse_args(argv)
    if len({path.resolve() for path in (args.requests, args.output, args.bibtex) if path is not None}) != (3 if args.bibtex else 2):
        parser.error("Input, evidence and BibTeX paths must be different.")
    try:
        requests = json.loads(args.requests.read_text(encoding="utf-8"))
        if not isinstance(requests, list) or any(not isinstance(item, dict) for item in requests):
            raise ValueError("Request JSON must be an array of objects")
        report, bibliography = verify_requests(requests, cache=args.cache, min_year=args.min_year if args.min_year is not None else args.as_of.year - 5,
                                               as_of=args.as_of, offline=args.offline,
                                               resolve_doi=args.resolve_doi, mailto=args.mailto)
    except (ValueError, OSError) as error:
        parser.exit(1, f"Reference verification failed: {error}\n")
    atomic_text(args.output, json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    if report["status"] == "passed" and args.bibtex:
        atomic_text(args.bibtex, bibliography)
    print(f"{report['status']}: {len(report['records'])} metadata records; {len(report['errors'])} errors")
    print("Review source support before using any proposed claim in the manuscript.")
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    from _console import utf8_console
    utf8_console()
    sys.exit(main())
