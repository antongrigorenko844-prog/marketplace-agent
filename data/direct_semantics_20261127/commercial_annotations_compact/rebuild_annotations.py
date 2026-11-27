"""Rebuild full query JSONL from pinned raw files and compact annotations (stdlib only)."""
import argparse
import csv
import hashlib
import json
from pathlib import Path


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--raw-dir', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--compare', type=Path, help='Optional original full JSONL for exact object comparison')
    args = parser.parse_args()
    base = Path(__file__).resolve().parent
    manifest = json.loads((base / 'rebuild_manifest.json').read_text())
    for item in manifest['annotation_files'] + [manifest['dictionary'], manifest['schema']]:
        assert sha(base / item['file']) == item['sha256'], 'Artifact hash mismatch: ' + item['file']
    source = manifest['source']
    assert sha(args.raw_dir / source['index']['file']) == source['index']['sha256'], 'Raw index hash mismatch'
    index = json.loads((args.raw_dir / source['index']['file']).read_text())
    raw, locations = [], []
    for part in source['parts']:
        path = args.raw_dir / part['file']
        assert sha(path) == part['sha256'], 'Raw part hash mismatch: ' + part['file']
        data = json.loads(path.read_text())
        assert len(data['rows']) == part['rows'] and data['row_offset'] == part['row_offset']
        for local, row in enumerate(data['rows']):
            raw.append(row)
            locations.append({'part': part['file'], 'row_index_in_part': local, 'global_row_index': part['row_offset'] + local})
    assert len(raw) == manifest['rows'] == index['rows']
    dictionary = json.loads((base / manifest['dictionary']['file']).read_text())
    annotations = []
    for item in manifest['annotation_files']:
        with (base / item['file']).open(newline='') as stream:
            chunk = list(csv.DictReader(stream))
        assert len(chunk) == item['rows']
        annotations.extend(chunk)
    assert len(annotations) == len(raw)
    comparator = args.compare.open() if args.compare else None
    canonical_hash = hashlib.sha256()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('w') as output:
        for i, (row, annotation) in enumerate(zip(raw, annotations)):
            assert int(annotation['row_index']) == i == locations[i]['global_row_index']
            semantic = dict(dictionary['intent_codes'][annotation['intent_code']])
            for dimension in ['review', 'confidence', 'evidence', 'routing', 'decision']:
                semantic.update(dictionary[dimension + '_codes'][annotation[dimension + '_id']])
            semantic = {field: semantic[field] for field in dictionary['semantic_field_order']}
            record = {'source': locations[i], 'period_kind': 'today_partial' if row['Date'] == index['today'] else 'completed_days',
                      'raw': row, 'semantic_review': semantic}
            canonical_hash.update((json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(',', ':')) + '\n').encode())
            if comparator:
                assert json.loads(next(comparator)) == record, 'Record mismatch at ' + str(i)
            output.write(json.dumps(record, ensure_ascii=False, separators=(',', ':')) + '\n')
    if comparator:
        assert not comparator.read(), 'Original JSONL contains extra rows'
        comparator.close()
    assert canonical_hash.hexdigest() == manifest['expected_canonical_records_sha256'], 'Restored meaning differs'
    result = {'rows': len(raw), 'all_record_fields_equal_to_original': bool(args.compare),
              'canonical_records_sha256': canonical_hash.hexdigest(), 'source_commit': source['commit'],
              'output_bytes': args.output.stat().st_size, 'output_sha256': sha(args.output),
              'external_mutations': 0}
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
