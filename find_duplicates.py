# Music Deduplicator (CLI) — создано с помощью AI (OpenCode / DeepSeek)
# https://github.com/velosipedf-cell/music-deduplicator

import os
import sys
import re
import shutil
from pathlib import Path
from collections import defaultdict

from mutagen.mp3 import MP3
from mutagen.easyid3 import EasyID3
from mutagen.id3 import ID3NoHeaderError


def normalize(s):
    if not s:
        return ""
    s = s.strip().lower()
    s = re.sub(r'\s*[\[\(]\s*(?:official|lyrics?|hd|hq|audio|video|music|clip|prod\.?|feat\.?|ft\.?|remastered|remix|edit|bonus|live|demo|instrumental|acoustic|clean|dirty|explicit|radio.?edit|extended)\b[^\]\)]*[\]\)]', '', s, flags=re.IGNORECASE)
    s = re.sub(r'\s*[\[\(]\d+[\]\)]', '', s)
    s = re.sub(r'\s+', ' ', s).strip()
    return s


def read_tags(filepath):
    try:
        tags = EasyID3(filepath)
    except ID3NoHeaderError:
        return None, None
    artist = tags.get('artist', [None])[0]
    title = tags.get('title', [None])[0]
    return artist, title


def get_bitrate(filepath):
    try:
        audio = MP3(filepath)
        if audio.info and audio.info.bitrate:
            return audio.info.bitrate // 1000
    except Exception:
        pass
    return 0


def find_mp3s(root_dir):
    return list(Path(root_dir).rglob('*.mp3'))


def delete_file(filepath, dry_run):
    if dry_run:
        print(f"  [DRY-RUN] Would delete: {filepath}")
    else:
        try:
            os.remove(filepath)
            print(f"  Deleted: {filepath}")
        except OSError as e:
            print(f"  ERROR deleting {filepath}: {e}")


def move_to_trash(filepath, dry_run):
    trash_dir = filepath.parent / "_duplicates"
    if dry_run:
        print(f"  [DRY-RUN] Would move to: {trash_dir / filepath.name}")
    else:
        trash_dir.mkdir(exist_ok=True)
        dest = trash_dir / filepath.name
        counter = 1
        while dest.exists():
            dest = trash_dir / f"{filepath.stem}_{counter}{filepath.suffix}"
            counter += 1
        try:
            shutil.move(str(filepath), str(dest))
            print(f"  Moved to: {dest}")
        except OSError as e:
            print(f"  ERROR moving {filepath}: {e}")


def confirm_action(prompt):
    while True:
        answer = input(f"{prompt} [y/n/q]: ").strip().lower()
        if answer in ('y', 'yes'):
            return True
        if answer in ('n', 'no'):
            return False
        if answer in ('q', 'quit'):
            print("Aborted by user.")
            sys.exit(0)


def main():
    dry_run = '--dry-run' in sys.argv or '-n' in sys.argv
    move_mode = '--move' in sys.argv or '-m' in sys.argv
    root = Path(sys.argv[1]) if len(sys.argv) > 1 and not sys.argv[1].startswith('-') else Path.cwd()
    use_filename_fallback = '--filename-fallback' in sys.argv or '-f' in sys.argv

    if not root.is_dir():
        print(f"Error: '{root}' is not a valid directory.")
        sys.exit(1)

    print(f"{'[DRY-RUN] ' if dry_run else ''}Scanning: {root}")
    print()

    mp3_files = find_mp3s(root)
    print(f"Found {len(mp3_files)} MP3 file(s)\n")

    groups = defaultdict(list)

    for fp in mp3_files:
        artist, title = read_tags(fp)
        artist_n = normalize(artist or '')
        title_n = normalize(title or '')

        if use_filename_fallback and (not artist_n or not title_n):
            stem = fp.stem
            parts = stem.rsplit(' - ', 1)
            if len(parts) == 2:
                if not artist_n:
                    artist_n = normalize(parts[0])
                if not title_n:
                    title_n = normalize(parts[1])

        key = (artist_n, title_n) if artist_n and title_n else fp.stem.lower()
        bitrate = get_bitrate(fp)
        groups[key].append((fp, bitrate, artist_n, title_n))

    duplicates_found = 0
    total_saved_bytes = 0

    for key, files in sorted(groups.items()):
        if len(files) < 2:
            continue

        files.sort(key=lambda x: x[1], reverse=True)

        best = files[0]
        rest = files[1:]

        duplicates_found += len(rest)

        artist_display = best[2] or "?"
        title_display = best[3] or "?"
        filename_display = key if not best[2] else ""

        print(f"{artist_display} — {title_display}{'  [' + filename_display + ']' if filename_display else ''}")
        print(f"  BEST:  {best[0].name}  ({best[1]} kbps)")

        for dup, br, _, _ in rest:
            print(f"  DUP:   {dup.name}  ({br} kbps)")
            total_saved_bytes += dup.stat().st_size

        if dry_run or move_mode:
            for dup, _, _, _ in rest:
                if move_mode:
                    move_to_trash(dup, dry_run)
                else:
                    delete_file(dup, dry_run)
        else:
            all_files_str = "\n".join(f"    {dup.name}" for dup, _, _, _ in rest)
            if confirm_action(f"Delete {len(rest)} duplicate(s)?\n{all_files_str}"):
                for dup, _, _, _ in rest:
                    delete_file(dup, dry_run=False)
        print()

    print("=" * 50)
    print(f"Duplicates found: {duplicates_found}")
    if total_saved_bytes:
        print(f"Space that could be freed: {total_saved_bytes / (1024*1024):.1f} MB")

    if not duplicates_found:
        print("No duplicates found.")


if __name__ == '__main__':
    main()
