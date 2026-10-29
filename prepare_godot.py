#!/usr/bin/env python3
"""Restore this repository's GUID assets into a new Godot staging directory."""
import argparse
import csv
from collections import Counter
from pathlib import Path, PurePosixPath
import shutil


def family(name):
    name = name.lower()
    for token, folder in [('camera', 'cameras'), ('computer_01', 'computers/01'),
                          ('computer_02', 'computers/02'), ('computer_03', 'computers/03'),
                          ('computer_14', 'computers/14'), ('electronics', 'electronics'),
                          ('monitor', 'monitors'), ('projector', 'projectors'),
                          ('retro_tech', 'retro_tech')]:
        if token in name:
            return folder
    return 'demo'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--output', type=Path, required=True, help='New directory; never overwrites existing output')
    args = parser.parse_args()
    source, output = args.source.resolve(), args.output.resolve()
    if output.exists():
        parser.error('Output already exists. Choose a new directory.')
    records, jobs, targets = [], [], set()
    for pathname in sorted(source.glob('*/pathname')):
        original = pathname.read_text(encoding='utf-8-sig').strip()
        path = PurePosixPath(original)
        if path.is_absolute() or '..' in path.parts or '\\' in original or not path.parts or path.parts[0] != 'Assets':
            parser.error(f'Unsafe asset path: {original!r}')
        asset = pathname.parent / 'asset'
        meta = pathname.parent / 'asset.meta'
        if not asset.is_file():
            if meta.exists() and 'folderAsset: yes' in meta.read_text(errors='replace'):
                continue
            parser.error(f'Missing asset: {original}')
        with asset.open('rb') as stream:
            if stream.read(100).startswith(b'version https://git-lfs.github.com/spec/v1'):
                parser.error('Git LFS pointers detected. Run git lfs pull first.')
        suffix = path.suffix.lower()
        if suffix in ('.fbx', '.png'):
            kind = 'models' if suffix == '.fbx' else 'textures'
            target = Path('assets/retro80s_technology') / kind / family(path.name) / path.name
        elif suffix == '.unitypackage':
            records.append((pathname.parent.name, original, '', 'nested package retained in source'))
            continue
        else:
            target = Path('unity_reference') / Path(*path.parts)
        if str(target).casefold() in targets:
            parser.error(f'Duplicate destination: {target}')
        targets.add(str(target).casefold())
        jobs.append((asset, target))
        if meta.is_file():
            jobs.append((meta, Path('unity_reference/metadata') / Path(*path.parts).with_name(path.name + '.meta')))
        records.append((pathname.parent.name, original, target.as_posix(), 'copied'))
    if not records:
        parser.error('No GUID/pathname entries found in source.')
    output.mkdir(parents=True)
    for asset, target in jobs:
        dest = output / target
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(asset, dest)
    for folder in ('materials', 'scenes'):
        (output / 'assets/retro80s_technology' / folder).mkdir(parents=True, exist_ok=True)
    (output / 'unity_reference').mkdir(exist_ok=True)
    (output / 'unity_reference/.gdignore').touch()
    with (output / 'asset_manifest.csv').open('w', newline='') as stream:
        writer = csv.writer(stream)
        writer.writerow(('guid', 'unity_path', 'destination', 'status'))
        writer.writerows(records)
    (output / 'IMPORT_NOTES.md').write_text('''# Retro 80s Technology: Godot staging\n\nCopy `assets/retro80s_technology` into your Godot project. Original filenames are retained.\nModels and textures are grouped by family; computers are further grouped by set number.\n\nThis is organization, not a completed material or prefab conversion. Unity materials,\nprefabs, shaders and scene data are retained in `unity_reference`, excluded from Godot\nimport by `.gdignore`. Metadata keeps GUIDs available for later conversion.\nThe two nested Built-in/URP packages remain untouched in the source repository.\n\nTexture suffixes: ALB = albedo, NRM = normal, EMM = emission. RMA is packed data;\nverify its channel mapping in the supplied shader before assigning it in Godot.\nMaterials and scenes directories are reserved for Godot resources.\nNo model units, orientation, material bindings, collisions or texture channels were changed.\nFBX import and final appearance must be checked in your installed Godot version.\n`asset_manifest.csv` records original paths, GUIDs and destinations.\n''', encoding='utf-8')
    counts = Counter(Path(row[1]).suffix.lower() for row in records if row[3] == 'copied')
    print(f'Created {output}')
    print(dict(sorted(counts.items())))
    print('Original source files unchanged. See IMPORT_NOTES.md.')


if __name__ == '__main__':
    main()
