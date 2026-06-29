#!/usr/bin/env python3
"""Transcript Media Bot

Redacts student-number style identifiers from a transcript PDF, renders the
redacted pages as media assets, copies supporting code/data/media files, and
creates a GitHub-ready repository folder and ZIP package.

Usage:
    python transcript_media_bot.py --transcript Transcripts.pdf --assets ./assets --output ./dist
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Tuple

import fitz


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def redact_transcript(input_pdf: Path, output_pdf: Path) -> List[dict]:
    doc = fitz.open(str(input_pdf))
    long_num = re.compile(r"\b\d{7,12}\b")
    redactions: List[dict] = []
    for page_index, page in enumerate(doc):
        words = page.get_text('words')
        lines: Dict[Tuple[int, int], list] = {}
        for w in words:
            x0, y0, x1, y1, text, block, line, wordno = w
            lines.setdefault((block, line), []).append(w)
            if long_num.fullmatch(text.strip()):
                rect = fitz.Rect(x0 - 2, y0 - 2, x1 + 2, y1 + 2)
                page.add_redact_annot(rect, fill=(0, 0, 0))
                redactions.append({'page': page_index + 1, 'type': 'long_numeric_identifier'})
        for (block, line), line_words in lines.items():
            line_text = ' '.join(w[4] for w in sorted(line_words, key=lambda x: x[0]))
            if 'Student Number' not in line_text:
                continue
            for (block2, line2), next_words in lines.items():
                if block2 != block or line2 not in {line + 1, line + 2}:
                    continue
                next_text = ' '.join(w[4] for w in sorted(next_words, key=lambda x: x[0]))
                numeric_words = [w for w in next_words if re.search(r'\d', w[4])]
                if numeric_words and long_num.search(next_text):
                    rect = fitz.Rect(
                        min(w[0] for w in numeric_words) - 2,
                        min(w[1] for w in numeric_words) - 2,
                        max(w[2] for w in numeric_words) + 2,
                        max(w[3] for w in numeric_words) + 2,
                    )
                    page.add_redact_annot(rect, fill=(0, 0, 0))
                    redactions.append({'page': page_index + 1, 'type': 'student_number_line_fallback'})
        page.apply_redactions(images=fitz.PDF_REDACT_IMAGE_PIXELS)
    doc.set_metadata({})
    output_pdf.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(output_pdf), garbage=4, deflate=True, clean=True)
    doc.close()
    return redactions


def render_pdf_pages(pdf: Path, out_dir: Path, dpi: int = 180) -> List[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    doc = fitz.open(str(pdf))
    matrix = fitz.Matrix(dpi / 72, dpi / 72)
    paths = []
    for i, page in enumerate(doc, start=1):
        pix = page.get_pixmap(matrix=matrix, alpha=False)
        target = out_dir / f'transcript_page_{i}.png'
        pix.save(str(target))
        paths.append(target)
    doc.close()
    return paths


def copy_assets(asset_dir: Path, repo_root: Path) -> List[Path]:
    copied = []
    code_exts = {'.py', '.ipynb'}
    data_exts = {'.tsv', '.csv', '.txt', '.json'}
    media_exts = {'.png', '.jpg', '.jpeg', '.webp', '.gif', '.svg', '.pdf'}
    for p in asset_dir.rglob('*'):
        if not p.is_file():
            continue
        if p.name.lower().startswith('transcripts'):
            continue
        if 'unredacted' in p.name.lower():
            continue
        if p.suffix.lower() in code_exts:
            target = repo_root / 'src' / p.name
        elif p.suffix.lower() in data_exts:
            target = repo_root / 'data' / p.name
        elif p.suffix.lower() in media_exts:
            target = repo_root / 'media' / 'supplied' / p.name
        else:
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(p, target)
        copied.append(target)
    return copied


def extract_text(pdf: Path) -> str:
    doc = fitz.open(str(pdf))
    text = '\n'.join(page.get_text() for page in doc)
    doc.close()
    return text


def make_manifest(repo_root: Path) -> list:
    rows = []
    for p in sorted(repo_root.rglob('*')):
        if p.is_file():
            rows.append({'path': str(p.relative_to(repo_root)), 'bytes': p.stat().st_size, 'sha256': sha256_file(p)})
    return rows


def zip_repo(repo_root: Path, zip_path: Path) -> None:
    with zipfile.ZipFile(zip_path, 'w', compression=zipfile.ZIP_DEFLATED) as z:
        for p in repo_root.rglob('*'):
            if p.is_file():
                z.write(p, p.relative_to(repo_root.parent))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--transcript', required=True, type=Path)
    parser.add_argument('--assets', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()

    repo_root = args.output / 'transcript_media_github_repo'
    if repo_root.exists():
        shutil.rmtree(repo_root)
    (repo_root / 'transcripts').mkdir(parents=True)
    redacted_pdf = repo_root / 'transcripts' / 'Justin-Ames_Gamache_Redacted_Transcript.pdf'
    redactions = redact_transcript(args.transcript, redacted_pdf)
    page_images = render_pdf_pages(redacted_pdf, repo_root / 'media' / 'transcript_pages')
    copied = copy_assets(args.assets, repo_root)
    text = extract_text(redacted_pdf)
    report = {
        'generated': datetime.now().isoformat(timespec='seconds'),
        'redacted_pdf': str(redacted_pdf.relative_to(repo_root)),
        'redaction_operations': len(redactions),
        'long_numeric_identifiers_in_extracted_text': re.findall(r'\d{7,12}', text),
        'rendered_pages': [str(p.relative_to(repo_root)) for p in page_images],
        'copied_assets': [str(p.relative_to(repo_root)) for p in copied],
    }
    (repo_root / 'docs').mkdir(exist_ok=True)
    (repo_root / 'docs' / 'redaction_report.json').write_text(json.dumps(report, indent=2))
    manifest = make_manifest(repo_root)
    (repo_root / 'docs' / 'manifest.json').write_text(json.dumps(manifest, indent=2))
    (repo_root / 'README.md').write_text('# Redacted Transcript Media Repository\n\nOriginal unredacted transcripts are excluded. Use the redacted PDF in `transcripts/`.\n')
    (repo_root / '.gitignore').write_text('Transcripts*.pdf\n*unredacted*\n__pycache__/\n*.pyc\n')
    zip_repo(repo_root, args.output / 'transcript_media_github_repo.zip')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
