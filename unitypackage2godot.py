#!/usr/bin/env python3
"""Safely restore standard Unity packages into a Godot-oriented asset tree.

Python 3.9+, standard library only. No Unity, Godot, Blender or 7zip required.
This organizes files; it does NOT convert Unity materials, prefabs or shaders.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import sys
import tarfile
import tempfile
from collections import Counter, defaultdict

VERSION = '1.0.0'
CATEGORIES = {
    'models': '.fbx .obj .gltf .glb .dae .blend'.split(),
    'textures': '.png .jpg .jpeg .tga .tif .tiff .bmp .webp .exr .hdr .dds .svg .ktx .ktx2 .psd'.split(),
    'audio': '.wav .ogg .mp3 .flac .aiff .aif'.split(),
    'fonts': '.ttf .otf .woff .woff2'.split(),
    'video': '.ogv .mp4 .webm .mov'.split(),
    'documentation': '.txt .md .pdf .html .htm .rtf'.split(),
    'godot_resources': '.tres .res .tscn .scn .gd .gdshader .gdshaderinc'.split(),
}
EXT_CATEGORY = {ext: category for category, exts in CATEGORIES.items() for ext in exts}
GUID = re.compile(r'^[0-9a-fA-F]{32}$')
GUID_REF = re.compile(r'\bguid:\s*([0-9a-fA-F]{32})\b')
NOTES = '''# Unity package organized for Godot

Copy the `assets` directory into your Godot project. `unity_reference` is
excluded from Godot scanning with `.gdignore`. The manifest records every
source GUID, original Unity path, destination, byte count and SHA-256.

The source package is never modified. Output must be a new directory.
Files are copied byte-for-byte: this is NOT an engine conversion.
Unity .mat, .prefab, .unity, .asset, .anim, shaders and scripts stay in reference.
Create Godot materials and scenes in the empty materials/ and scenes/ folders.
Metadata and previews are retained under unity_reference/entries/<GUID>/.
Small text references are indexed in manifest.json; missing dependencies are reported.

Texture suffixes and packed channels have no universal meaning. Inspect the
original shader/documentation before mapping RMA, ORM, mask or normal textures.
No material assignment, LOD switching, collision conversion, animation conversion,
normal-map flipping, channel repacking or image resizing is performed.

Categories are based on file extensions, not a promise of Godot compatibility.
Some formats need conversion or external tools for your Godot version.
Categorized layout can break relative references in OBJ/MTL, glTF/bin, FBX, etc.
Use --layout original to preserve relative paths, including auxiliary files.
Unity GUID bindings still require conversion with either layout.
Unity C# scripts are retained as reference, not copied into Godot's build path.

Nested .unitypackage files are retained under unity_reference/original/.
Run this script separately on those packages if needed; pipeline variants often
contain duplicate assets, so they are deliberately not merged automatically.
'''


def safe_parts(value):
    """Portable relative path; reject archive traversal and Windows path tricks."""
    if not value or '\\' in value or any(ord(c) < 32 for c in value):
        raise ValueError(f'Unsafe path: {value!r}')
    path = PurePosixPath(value)
    if path.is_absolute() or '..' in path.parts or any(':' in p for p in path.parts):
        raise ValueError(f'Unsafe path: {value!r}')
    if not path.parts:
        raise ValueError(f'Empty path: {value!r}')
    return path.parts


def category(path):
    if path.name.lower().startswith(('license', 'licence', 'readme', 'copyright')):
        return 'documentation'
    return EXT_CATEGORY.get(path.suffix.lower(), 'unity_reference')


def unpack(package, staging, max_bytes):
    """Stream to private staging; never call extract/extractall or follow links."""
    total = 0
    count = 0
    with tarfile.open(package, 'r|*') as archive:
        for member in archive:
            parts = safe_parts(member.name)
            if member.isdir():
                continue
            if not member.isfile():
                raise ValueError(f'Links and special archive entries are unsupported: {member.name}')
            if len(parts) != 2 or not GUID.fullmatch(parts[0]):
                raise ValueError(f'Not a standard GUID-based Unity package entry: {member.name}')
            if parts[1] not in ('asset', 'asset.meta', 'pathname', 'preview.png'):
                raise ValueError(f'Unexpected Unity package entry: {member.name}')
            total += member.size
            count += 1
            if total > max_bytes or count > 1000000:
                raise ValueError('Archive exceeds extraction budget; increase --max-gib if appropriate.')
            if parts[1] in ('pathname', 'asset.meta') and member.size > 16 * 1024 * 1024:
                raise ValueError(f'Oversized metadata: {member.name}')
            destination = staging.joinpath(*parts)
            destination.parent.mkdir(parents=True, exist_ok=True)
            with archive.extractfile(member) as source, destination.open('xb') as target:
                shutil.copyfileobj(source, target, 1024 * 1024)
    if not count:
        raise ValueError('Empty package.')


def collect(staging):
    entries = []
    seen_paths = set()
    for folder in sorted(staging.iterdir()):
        pathname = folder / 'pathname'
        if not pathname.is_file():
            raise ValueError(f'Missing pathname in {folder.name}')
        value = pathname.read_text(encoding='utf-8-sig').rstrip('\x00\r\n')
        parts = safe_parts(value)
        if parts[0] not in ('Assets', 'Packages'):
            raise ValueError(f'Expected Assets/ or Packages/ path: {value!r}')
        key = '/'.join(parts).casefold()
        if key in seen_paths:
            raise ValueError(f'Duplicate Unity path (case-insensitive): {value!r}')
        seen_paths.add(key)
        meta = folder / 'asset.meta'
        folder_asset = meta.exists() and re.search(r'^folderAsset:\s*yes\s*$', meta.read_text(errors='replace'), re.M)
        asset = folder / 'asset'
        if folder_asset:
            entries.append({'guid': folder.name, 'unity_path': value, 'category': 'folder', 'folder': folder})
        elif asset.is_file():
            with asset.open('rb') as stream:
                if stream.read(100).startswith(b'version https://git-lfs.github.com/spec/v1'):
                    raise ValueError(f'Git LFS pointer instead of file bytes: {value}')
            entries.append({'guid': folder.name, 'unity_path': value,
                            'category': category(PurePosixPath(value)), 'folder': folder})
        else:
            raise ValueError(f'Missing asset bytes: {value}')
    return entries


def plan(entries, pack, layout):
    roots = {}
    groups = defaultdict(list)
    for entry in entries:
        if entry['category'] not in ('folder', 'unity_reference'):
            groups[entry['category']].append(str(PurePosixPath(entry['unity_path']).parent))
    if layout == 'categorized':
        roots = {key: PurePosixPath(os.path.commonpath(paths)) for key, paths in groups.items()}
    destinations = set()
    for entry in entries:
        kind = entry['category']
        original = PurePosixPath(entry['unity_path'])
        if kind == 'folder':
            entry['destination'] = None
            continue
        if layout == 'original':
            destination = PurePosixPath('assets', pack, 'source', *original.parts)
        elif kind == 'unity_reference':
            destination = PurePosixPath('unity_reference', 'original', *original.parts)
        else:
            relative = original.relative_to(roots[kind])
            destination = PurePosixPath('assets', pack, kind, *relative.parts)
        entry['destination'] = str(destination)
        key = str(destination).casefold()
        if key in destinations:
            raise ValueError(f'Destination collision: {destination}')
        destinations.add(key)
    return roots


def copy_hashed(source, target):
    target.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256()
    size = 0
    with source.open('rb') as incoming, target.open('xb') as outgoing:
        for block in iter(lambda: incoming.read(1024 * 1024), b''):
            outgoing.write(block)
            digest.update(block)
            size += len(block)
    return size, digest.hexdigest()


def write_output(entries, output, pack, layout, package, max_text):
    output.mkdir()
    (output / 'unity_reference').mkdir()
    (output / 'unity_reference/.gdignore').touch()
    known = {e['guid'].lower() for e in entries}
    missing = set()
    manifest = []
    for entry in entries:
        row = {key: value for key, value in entry.items() if key != 'folder'}
        folder = entry['folder']
        references = set()
        if row['destination']:
            asset = folder / 'asset'
            row['bytes'], row['sha256'] = copy_hashed(asset, output / row['destination'])
            if asset.stat().st_size <= max_text:
                data = asset.read_bytes()
                if b'\0' not in data:
                    references.update(GUID_REF.findall(data.decode('utf-8', errors='replace')))
        extras = []
        for name in ('pathname', 'asset.meta', 'preview.png'):
            source = folder / name
            if source.is_file():
                target = Path('unity_reference/entries') / row['guid'] / name
                copy_hashed(source, output / target)
                extras.append(target.as_posix())
                if name == 'asset.meta':
                    references.update(GUID_REF.findall(source.read_text(errors='replace')))
        row['reference_files'] = extras
        row['referenced_guids'] = sorted(set(x.lower() for x in references))
        row['unresolved_guids'] = [g for g in row['referenced_guids'] if g not in known and int(g, 16) != 0]
        missing.update(row['unresolved_guids'])
        manifest.append(row)
    for name in ('materials', 'scenes'):
        (output / 'assets' / pack / name).mkdir(parents=True, exist_ok=True)
    if layout == 'original':
        # Ignore the interchange tree so Unity C# files cannot enter a Godot build.
        tree = output / 'assets' / pack / 'source'
        tree.mkdir(parents=True, exist_ok=True)
        (tree / '.gdignore').touch()
    report = {'version': VERSION, 'source_package': str(package), 'layout': layout,
              'pack': pack, 'unresolved_guids': sorted(missing), 'entries': manifest}
    (output / 'manifest.json').write_text(json.dumps(report, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    note = NOTES
    if layout == 'original':
        note += '\nORIGINAL LAYOUT: assets/<pack>/source is also ignored by Godot. It preserves\nall files and relative paths as an interchange/reference tree. Convert with\nBlender or copy a suitable subset into an active Godot folder for importing.\n'
    (output / 'IMPORT_NOTES.md').write_text(note, encoding='utf-8')
    return missing


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('package', type=Path, help='.unitypackage file')
    parser.add_argument('-o', '--output', type=Path, required=True, help='New output directory (never overwritten)')
    parser.add_argument('--name', help='Asset folder name; defaults to package filename')
    parser.add_argument('--layout', choices=('categorized', 'original'), default='categorized',
                        help='categorized: Godot-oriented folders; original: ignored interchange tree preserving all paths')
    parser.add_argument('--dry-run', action='store_true', help='Inspect and print destinations; create no output directory')
    parser.add_argument('--temp-dir', type=Path, help='Temporary extraction parent; needs space for uncompressed package')
    parser.add_argument('--max-gib', type=float, default=64, help='Maximum uncompressed archive size (default: 64 GiB)')
    args = parser.parse_args(argv)
    if not args.package.is_file():
        parser.error('Package does not exist.')
    # Resolve parent only: do not follow an output symlink, even a dangling one.
    output = args.output.expanduser().absolute()
    if os.path.lexists(output):
        parser.error('Output already exists. Choose a new directory.')
    pack = args.name or args.package.stem
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*', pack) or pack in ('.', '..'):
        parser.error('--name must contain only letters, digits, underscores, hyphens and dots, starting with a letter or digit.')
    if not 0 < args.max_gib < float('inf'):
        parser.error('--max-gib must be a finite positive number.')
    try:
        with tempfile.TemporaryDirectory(prefix='unitypackage_', dir=args.temp_dir) as temporary:
            print('Inspecting package (streaming to temporary disk storage)...', flush=True)
            staging = Path(temporary)
            unpack(args.package, staging, int(args.max_gib * 1024**3))
            entries = collect(staging)
            plan(entries, pack, args.layout)
            print('Assets:', dict(sorted(Counter(e['category'] for e in entries).items())))
            if args.dry_run:
                for entry in entries:
                    print(f"{entry['unity_path']} -> {entry['destination'] or '[folder metadata]'}")
                return 0
            output.parent.mkdir(parents=True, exist_ok=True)
            # Build privately, then publish; failed runs leave no partial output tree.
            with tempfile.TemporaryDirectory(prefix='.godot_stage_', dir=output.parent) as destination:
                result = Path(destination) / 'result'
                missing = write_output(entries, result, pack, args.layout, args.package.resolve(), 8 * 1024**2)
                if os.path.lexists(output):
                    raise ValueError('Output appeared during processing; refusing to overwrite.')
                result.rename(output)
            print(f'Created: {output}')
            print(f'Unresolved GUIDs: {len(missing)} (may include Unity built-ins or external dependencies).')
            print('See manifest.json and IMPORT_NOTES.md. No engine conversion was performed.')
        return 0
    except (OSError, ValueError, tarfile.TarError, EOFError, UnicodeError) as error:
        print(f'Error: {error}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())

