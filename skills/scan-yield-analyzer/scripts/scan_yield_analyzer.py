import argparse
import collections
import csv
import datetime
import gzip
import html
import json
import re
from pathlib import Path


def require(condition, message):
    if not condition:
        raise ValueError(message)


def read_devices(path):
    header = {}
    device = None
    opener = gzip.open if str(path).lower().endswith('.gz') else open
    with opener(path, 'rt', encoding='utf-8-sig') as source:
        for raw in source:
            line = raw.rstrip('\r\n')
            if device is not None and line.startswith('2_tname_testtime_SCN_'):
                device['scan_execution_records'].add(line[len('2_tname_testtime_'):])
                device['scan_execution_order'].append(line[len('2_tname_testtime_'):])
                continue
            if not line.startswith(('3_', '4_wafid_', '6_lotid_', '6_prgnm_', '5_lcode_')):
                continue
            parts = line.split('_', 2)
            tag = '_'.join(parts[:2])
            value = parts[2] if len(parts) == 3 else ''
            if tag in {'4_wafid', '6_lotid', '6_prgnm', '5_lcode'}:
                header[tag] = value
            elif line in {'3_lsep', '3_lend'}:
                if device is not None:
                    device['scan_execution_records'] = sorted(device['scan_execution_records'])
                    yield device
                device = {**header, 'scan_execution_records': set(), 'scan_execution_order': []} if line == '3_lsep' else None
            elif device is not None:
                if tag in {'3_tfail', '3_tpass'}:
                    require(re.fullmatch(r'\{\s*(?:\d+(?:\s*,\s*\d+)*)?\s*\}', value),
                            f'Invalid {tag} in {path}: {value}')
                    counters = [counter.strip() for counter in value[1:-1].split(',') if counter.strip()]
                    device[tag] = sorted(set(device.get(tag, [])) | set(counters), key=int)
                elif tag in {'3_wafxloc', '3_wafyloc', '3_dvtsteddt', '3_binn',
                             '3_curfbin', '3_curibin', '3_trslt'}:
                    device[tag] = value
    if device is not None:
        device['scan_execution_records'] = sorted(device['scan_execution_records'])
        yield device


def end_time(value):
    text = value.strip()
    for pattern in ('%Y%m%d%H%M%S', '%Y%m%d%H%M%S.%f', '%m/%d/%Y %H:%M:%S'):
        try:
            return datetime.datetime.strptime(text, pattern).replace(tzinfo=datetime.timezone.utc)
        except ValueError:
            pass
    if re.fullmatch(r'\d{10}(?:\.\d+)?', text):
        return datetime.datetime.fromtimestamp(float(text), tz=datetime.timezone.utc)
    try:
        result = datetime.datetime.fromisoformat(text.replace('Z', '+00:00'))
        return result.replace(tzinfo=datetime.timezone.utc) if result.tzinfo is None else result
    except ValueError as error:
        raise ValueError(f'Unsupported die end timestamp: {value}') from error


def load_final_dies(paths, tracking_path, program):
    require(len({Path(path).resolve() for path in paths}) == len(paths), 'Duplicate input file')
    latest = {}
    scopes = set()
    counts = collections.Counter()
    for path in paths:
        found = False
        for record in read_devices(path):
            found = True
            needed = {'6_lotid', '6_prgnm', '5_lcode', '4_wafid', '3_wafxloc', '3_wafyloc',
                      '3_dvtsteddt', '3_curfbin', '3_curibin', '3_trslt'}
            require(needed <= record.keys(), f'Missing die fields in {path}: {sorted(needed - record.keys())}')
            require(record['6_prgnm'] == program, f'TP mismatch in {path}; analyze different TPs separately')
            lot = record['6_lotid']
            scopes.add((lot, record['5_lcode']))
            counts[lot] += 1
            key = tuple(record[field] for field in ('6_lotid', '4_wafid', '3_wafxloc', '3_wafyloc'))
            stamp = end_time(record['3_dvtsteddt'])
            if key not in latest or stamp > end_time(latest[key]['3_dvtsteddt']):
                latest[key] = record
            elif stamp == end_time(latest[key]['3_dvtsteddt']):
                require(record == latest[key], f'Conflicting attempts with the same end time: {key}')
        require(found, f'No die records in {path}')
    require(len(scopes) == len({lot for lot, operation in scopes}),
            'Multiple operations for one lot; select one operation per lot')
    with Path(tracking_path).open(encoding='utf-8-sig', newline='') as source:
        reader = csv.DictReader(source)
        required = {'Lot', 'Operation', 'Within_LATO_Latest_Flag', 'Substructure_ID', 'Sort_Wafer_ID',
                    'Sort_X_Location', 'Sort_Y_Location', 'Functional_Bin', 'Interface_Bin', 'Data_Bin'}
        require(required <= set(reader.fieldnames or []), 'Tracking CSV is missing required AQUA columns')
        expected = [row for row in reader if (row['Lot'], row['Operation']) in scopes
                    and row['Within_LATO_Latest_Flag'] == 'Y' and row['Substructure_ID'] == 'UNIT']
    validated = []
    identities = set()
    for row in expected:
        key = tuple(row[field] for field in ('Lot', 'Sort_Wafer_ID', 'Sort_X_Location', 'Sort_Y_Location'))
        require(key not in identities, f'Duplicate latest tracking die: {key}')
        identities.add(key)
        require(key in latest, f'Tracking die missing from iTUFF: {key}')
        record = latest[key]
        for raw_key, csv_key in (('5_lcode', 'Operation'), ('3_curfbin', 'Functional_Bin'),
                                 ('3_curibin', 'Interface_Bin'), ('3_binn', 'Data_Bin')):
            require(record.get(raw_key, '') == row[csv_key], f'Tracking mismatch for {key}: {csv_key}')
        record.setdefault('3_binn', '')
        validated.append(record)
    require(identities == latest.keys() and bool(identities), 'Tracking and iTUFF final die populations differ')
    validation = [{'lot': lot, 'operation': operation, 'raw_records': counts[lot],
                   'final_dies': sum(record['6_lotid'] == lot for record in validated)}
                  for lot, operation in sorted(scopes)]
    return validated, validation


def excluded(name):
    return bool(name and re.search(r'HRY|SPOFI', name.rsplit('::', 1)[-1], re.IGNORECASE))


def load_tp(tp_root):
    root = Path(tp_root)
    text = (root / 'BinDefinitions.bdefs').read_text(encoding='utf-8-sig')
    soft_block = re.search(r'\bBinGroup\s+SoftBins\s*\{(.*?)\}', text, re.DOTALL)
    require(soft_block, 'SoftBins definitions not found')
    soft_bins = dict(re.findall(r'\bBin\s+(\w+)\s+(\d+)\s*:', soft_block[1]))
    leaf_bins = {}
    for match in re.finditer(r'\bLeafBin\s+(\w+)\s+(\d+)\s*:\s*"[^"]*"\s*,\s*(\w+)\s*;', text):
        name, counter, parent = match.groups()
        require(counter not in leaf_bins, f'Duplicate DB definition: {counter}')
        require(parent in soft_bins, f'Unknown FB parent for DB {counter}: {parent}')
        leaf_bins[counter] = {'name': name, 'fb': soft_bins[parent]}
    definitions = {}
    counter_map = {}
    paths = sorted((root / 'Modules').glob('*/*.mtpl'))
    require(paths, 'No Modules/*/*.mtpl files found')
    for path in paths:
        text = path.read_text(encoding='utf-8-sig')
        instances = set(re.findall(r'(?m)^\s*(?:CSharpTest|Test)\s+\S+\s+(\w+)\s*$', text))
        modes = collections.defaultdict(set)
        for match in re.finditer(r'(?m)^\s*FlowItem\s+\w+\s+(\w+)([^\r\n]*)$', text):
            modes[match[1]].add('EDC' if '@EDC' in match[2] else 'KILL')
        set_bins = set(re.findall(r'\bSetBin\s+b(\d+)_fail_\w+', text))
        for instance in instances:
            name = path.stem + '::' + instance
            require(name not in definitions, f'Duplicate instance: {name}')
            definitions[name] = {'module': path.stem, 'modes': sorted(modes[instance]),
                                 'declared_fail_counters': [], 'set_bins': set_bins}
        block = re.search(r'\bCounters\s*\{(.*?)\}', text, re.DOTALL)
        if not block:
            continue
        for counter, label in re.findall(r'\bn(\d+)_(\w+)\b', block[1]):
            if not re.match(r'(?:fail|pass)_', label, re.IGNORECASE):
                continue
            instance = re.sub(r'_\d+$', '', re.sub(r'^(?:fail|pass)_', '', label, flags=re.IGNORECASE))
            if instance not in instances:
                continue
            name = path.stem + '::' + instance
            require(counter not in counter_map or counter_map[counter] == name,
                    f'Ambiguous counter {counter}: multiple instances')
            counter_map[counter] = name
            if label.lower().startswith('fail_'):
                definitions[name]['declared_fail_counters'].append(counter)
    return definitions, counter_map, leaf_bins


def first_scan_failures(die, failed):
    order = die.get('scan_execution_order')
    calls = collections.defaultdict(list)
    observed = collections.defaultdict(set)
    failures = collections.defaultdict(set)
    for name in order or []:
        if not excluded(name):
            calls[name.split('::', 1)[0]].append(name)
    for name in set(die['scan_execution_records']) | failed.keys():
        if not excluded(name):
            module = name.split('::', 1)[0]
            observed[module].add(name)
            if name in failed:
                failures[module].add(name)
    first, uncertain = set(), set()
    for module, names in observed.items():
        counts = collections.Counter(calls[module])
        if order is None or set(counts) != names or any(counts[name] != 1 for name in failures[module]):
            uncertain.add(module)
            continue
        first.update(next(({name} for name in calls[module] if name in failures[module]), set()))
    return first, uncertain


def build_statistics(dies, validation, tp_root, program, scan_bins=('41', '42')):
    definitions, counter_map, leaf_bins = load_tp(tp_root)
    scan_names = {name for name, item in definitions.items() if item['module'].startswith('SCN_')}
    states = collections.defaultdict(lambda: {'timed': set(), 'observed': set(), 'failed': set(),
                                             'killed': set(), 'final_pass': set(), 'both': set(), 'counters': set(),
                                             'first_failed': set(), 'uncertain_order': set()})
    evidence = []
    for index, die in enumerate(dies):
        timed = set(die['scan_execution_records'])
        require(timed <= scan_names, f'Unknown scan execution instances: {sorted(timed - scan_names)}')
        failed = collections.defaultdict(set)
        passed = set()
        for counter in die.get('3_tfail', []):
            name = counter_map.get(counter)
            if name in scan_names:
                failed[name].add(counter)
        for counter in die.get('3_tpass', []):
            name = counter_map.get(counter)
            if name in scan_names:
                passed.add(name)
        first_failed, uncertain_modules = first_scan_failures(die, failed)
        primary = counter_map.get(die['3_binn'])
        if die['3_curibin'] in scan_bins:
            require(primary in failed, f'Scan IB has no matching final DB fail counter: {die["3_binn"]}')
        if primary in scan_names:
            require(primary in failed, f'Final scan DB absent from failed instances: {primary}')
            require(die['3_binn'] in leaf_bins, f'Final scan DB missing in TP: {die["3_binn"]}')
            require(leaf_bins[die['3_binn']]['fb'] == die['3_curfbin'], 'Final scan FB disagrees with TP')
        for name in timed | failed.keys() | passed:
            state = states[name]
            state['observed'].add(index)
            if name in first_failed:
                state['first_failed'].add(index)
            if name.split('::', 1)[0] in uncertain_modules:
                state['uncertain_order'].add(index)
            if name in timed:
                state['timed'].add(index)
            if name in failed:
                state['failed'].add(index)
                state['counters'].update(failed[name])
                if die['3_trslt'].lower() == 'pass':
                    state['final_pass'].add(index)
                if name in passed:
                    state['both'].add(index)
            if primary == name:
                state['killed'].add(index)
        evidence.append({'lot': die['6_lotid'], 'wafer': die['4_wafid'], 'x': die['3_wafxloc'],
                         'y': die['3_wafyloc'], 'final_fb': die['3_curfbin'], 'final_db': die['3_binn'],
                         'final_ib': die['3_curibin'], 'final_result': die['3_trslt'], 'primary_test': primary,
                         'fail_counters': die.get('3_tfail', []),
                         'scan_execution_order': die.get('scan_execution_order')})
    rows = []
    for name in sorted(scan_names):
        definition = definitions[name]
        if excluded(name):
            continue
        state = states[name]
        if not definition['declared_fail_counters'] and not state['observed']:
            continue
        require(len(definition['modes']) == 1,
                f'Missing or mixed FlowItem modes for {name}; split flow contexts before analysis')
        mode = definition['modes'][0]
        fbs = set()
        declared = sorted(set(definition['declared_fail_counters']), key=int)
        if mode == 'KILL':
            regular = [counter for counter in declared if not counter.startswith(('98', '99'))]
            require(set(regular) & definition['set_bins'], f'No regular failure SetBin verified for {name}')
            for counter in regular:
                require(counter in leaf_bins, f'DB missing from TP bin definitions: {counter}')
                leaf = leaf_bins[counter]
                require(leaf['name'].startswith('b' + counter + '_fail_' + name.replace('::', '_') + '_'),
                        f'DB instance disagrees with MTPL: {counter}')
                fbs.add(leaf['fb'])
        timed, observed, failed, killed = (state[key] for key in ('timed', 'observed', 'failed', 'killed'))
        require(killed <= failed <= observed, f'Invalid fail/kill sets for {name}')
        complete = bool(timed) and observed == timed
        first_complete = complete and not state['uncertain_order']
        failures = len(killed) if mode == 'KILL' else len(failed)
        rate = round(100 * (1 - failures / len(timed)), 4) if complete else None
        other = collections.defaultdict(lambda: {'dies': set(), 'counters': set()})
        for index in failed:
            for counter in evidence[index]['fail_counters']:
                other_name = counter_map.get(counter)
                if other_name == name or excluded(other_name):
                    continue
                label = other_name or 'Unmapped counter ' + counter
                other[label]['dies'].add(index)
                other[label]['counters'].add(counter)
        rows.append({'test': name, 'fb': ', '.join(sorted(fbs, key=int)), 'flow_mode': mode,
                     'total_dies': len(dies), 'tested_dies': len(timed), 'observed_dies': len(observed),
                     'counter_only_dies': len(observed - timed), 'counter_fail_dies': len(failed),
                     'first_scan_fail_dies': len(state['first_failed']) if first_complete else None,
                     'first_scan_fail_die_indexes': sorted(state['first_failed']) if first_complete else None,
                     'uncertain_scan_order_dies': len(state['uncertain_order']),
                     'final_kill_dies': len(killed), 'fail_on_final_pass_dies': len(state['final_pass']),
                     'both_pass_fail_dies': len(state['both']), 'yield_percent': rate,
                     'yield_basis': '1 - ' + ('Final Kill Dies' if mode == 'KILL' else 'Counter Fail Dies') + ' / Tested Dies',
                     'counter_yield_percent': round(100 * (1 - len(failed) / len(timed)), 4) if complete else None,
                     'fail_counters': sorted(state['counters'], key=int), 'declared_fail_counters': declared,
                     'failed_die_indexes': sorted(failed), 'nonkill_die_indexes': sorted(failed - killed),
                     'other_failures': [{'other_test_or_counter': label, 'affected_dies': len(values['dies']),
                                         'fail_counters': sorted(values['counters'], key=int)}
                                        for label, values in sorted(other.items())]})
    rows.sort(key=lambda row: (row['flow_mode'] != 'KILL', row['yield_percent'] is None,
                              (row['yield_percent'] or 0) * (1 if row['flow_mode'] == 'KILL' else -1), row['test']))
    zero_kill = [row for row in rows if row['counter_fail_dies'] and not row['final_kill_dies']]
    summary = {'total_dies': len(dies), 'observed_scan_tests': sum(bool(row['observed_dies']) for row in rows),
               'failing_tests_with_zero_final_kills': len(zero_kill),
               'zero_kill_EDC_tests': sum(row['flow_mode'] == 'EDC' for row in zero_kill),
               'zero_kill_KILL_tests': sum(row['flow_mode'] == 'KILL' for row in zero_kill),
               'dies_with_nonkill_scan_failures': len({index for row in rows for index in row['nonkill_die_indexes']}),
               'final_scan_kill_dies': sum(row['final_kill_dies'] for row in rows),
               'scan_ib_fail_dies': sum(die['3_curibin'] in scan_bins for die in dies)}
    return {'schema_version': 1, 'program': program, 'scan_bins': list(scan_bins), 'validation': validation,
            'summary': summary, 'rows': rows, 'evidence': evidence}


def columns(mode):
    test = [('Test', 'test'), ('TP Flow Mode', 'flow_mode')]
    population = [('Total Dies', 'total_dies'), ('Tested Dies', 'tested_dies'),
                  ('Counter Fail Dies', 'counter_fail_dies'), ('First Scan Fail Dies', 'first_scan_fail_dies'),
                  ('Fail on Final PASS Dies', 'fail_on_final_pass_dies')]
    kill = ('Final Kill Dies', 'final_kill_dies')
    tail = [('Observed Fail Counters', 'fail_counters')]
    if mode == 'KILL':
        return [('FB', 'fb')] + test + [kill, ('Final Kill Yield', 'yield_percent')] + population + tail
    return test + population + [('Counter Yield', 'yield_percent'), kill] + tail


def scalar(value):
    if value is None:
        return ''
    return ', '.join(map(str, value)) if isinstance(value, list) else str(value)


def write_csv(path, fields, records):
    with path.open('w', encoding='utf-8-sig', newline='') as target:
        writer = csv.DictWriter(target, fieldnames=fields)
        writer.writeheader()
        for record in records:
            writer.writerow({field: scalar(record.get(field)) for field in fields})


def write_report(data, output):
    require(data.get('schema_version') == 1, 'Unsupported statistics cache schema')
    report = Path(output)
    require(report.suffix.lower() == '.html', '--output must end in .html')
    report.parent.mkdir(parents=True, exist_ok=True)
    rows = data['rows']
    for row in rows:
        row.setdefault('first_scan_fail_dies', None)
        previous_alias = row.pop('good_die_counter_fail_dies', None)
        row.setdefault('fail_on_final_pass_dies', previous_alias)
    groups = {mode: [row for row in rows if row['flow_mode'] == mode] for mode in ('KILL', 'EDC')}
    require(sum(map(len, groups.values())) == len(rows), 'Unexpected flow mode in cache')
    for mode, records in groups.items():
        records.sort(key=lambda row: (row['yield_percent'] is None,
                                     (row['yield_percent'] or 0) * (1 if mode == 'KILL' else -1), row['test']))
    data['rows'] = groups['KILL'] + groups['EDC']
    require(not any(excluded(row['test']) for row in rows), 'Excluded instance in statistics cache')
    paths = {name: report.with_name(report.stem + suffix) for name, suffix in (
        ('statistics', '_statistics.csv'), ('kill', '_kill.csv'), ('edc', '_edc.csv'),
        ('overlap', '_overlap.csv'), ('cache', '.json'))}
    audit = ['observed_dies', 'counter_only_dies', 'both_pass_fail_dies', 'yield_basis',
             'counter_yield_percent', 'declared_fail_counters', 'uncertain_scan_order_dies']
    write_csv(paths['statistics'], [key for label, key in columns('KILL')] + audit, data['rows'])
    for mode, records in groups.items():
        write_csv(paths[mode.lower()], [key for label, key in columns(mode)] + audit, records)
    overlap = [{'scan_test': row['test'], 'flow_mode': row['flow_mode'],
                'final_kill_dies': row['final_kill_dies'], **other}
               for row in data['rows'] for other in row['other_failures']]
    write_csv(paths['overlap'], ['scan_test', 'flow_mode', 'final_kill_dies',
                                'other_test_or_counter', 'affected_dies', 'fail_counters'], overlap)
    paths['cache'].write_text(json.dumps(data, indent=2), encoding='utf-8')
    escape = html.escape

    def table(records, mode):
        fields = columns(mode)
        body = []
        for row in records:
            cells = []
            for label, key in fields:
                value = scalar(row[key])
                if key == 'yield_percent':
                    value = 'N/A' if row[key] is None else f'{row[key]:.2f}%'
                elif key in {'first_scan_fail_dies', 'fail_on_final_pass_dies'} and row[key] is None:
                    value = 'N/A'
                elif key == 'fail_counters' and not value:
                    value = 'None observed'
                cells.append('<td' + (" class='name'" if key == 'test' else '') + '>' + escape(value) + '</td>')
            others = row['other_failures']
            detail = 'N/A'
            if others:
                detail_rows = ''.join('<tr><td class="name">' + escape(other['other_test_or_counter'])
                                      + '</td><td>' + str(other['affected_dies']) + '</td><td>'
                                      + escape(scalar(other['fail_counters'])) + '</td></tr>' for other in others)
                detail = ('<details><summary>' + str(len(others)) + ' other tests / unmapped counter IDs</summary>'
                          + '<div class="scroll"><table><tr><th>Other test / counter</th><th>Affected dies</th>'
                          + '<th>Fail counters</th></tr>' + detail_rows + '</table></div></details>')
            body.append('<tr>' + ''.join(cells) + '<td>' + detail + '</td></tr>')
        header = ''.join('<th>' + escape(label) + '</th>' for label, key in fields)
        return ('<div class="scroll"><table><thead><tr>' + header + '<th>Other Fail Tests / Counters</th></tr></thead>'
                + '<tbody>' + ''.join(body) + '</tbody></table></div>')

    document = '''<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Combined Scan Statistics</title>
<style>body{font-family:Calibri,"Segoe UI",sans-serif;margin:24px;color:#20242a;background:#f7f8fa;letter-spacing:0}
h1{font-size:24px}h2{font-size:19px;margin-top:30px}p{line-height:1.5;max-width:1200px}
table{border-collapse:collapse;width:100%;font-size:13px;background:white}th,td{padding:8px;border:1px solid #ced4db;
text-align:left;vertical-align:top}th{background:#e7ecef;white-space:nowrap}td.name{min-width:260px;max-width:540px;
overflow-wrap:anywhere}a{color:#126b62}.scroll{max-width:100%;overflow-x:auto}summary{cursor:pointer;min-width:190px}
details{max-width:800px}section{margin-bottom:28px}@media(max-width:600px){body{margin:12px}h1{font-size:21px}}
</style></head><body><h1>Combined Scan Statistics: HRY / SPOFI Excluded</h1>'''
    lots = ', '.join(sorted({item['lot'] for item in data['validation']}))
    document += '<p>TP: ' + escape(data['program']) + '. Lots: ' + escape(lots) + '.</p>'
    document += '''<p>Lots are combined by exact module::instance, with source lot retained in each die identity.
Tested Dies counts distinct dies with an exact testtime execution record. Fail counters are deduplicated per die,
including repeated calls and multiple exits. HRY/SPOFI instances are excluded from item statistics and mapped overlap details.</p>
<p>Fail on Final PASS Dies counts distinct final PASS dies with a fail counter for this exact instance,
regardless of final IB number. It is a subset of Counter Fail Dies, without removing earlier scan failures.
Repeated fail counters count once per die; final non-PASS dies are excluded even if this instance did not kill them.
It does not change Tested Dies, First Scan Fail Dies or either yield formula.</p>
<p>First Scan Fail Dies removes dies already failing an earlier retained scan instance in the same module.
Order follows each die's actual testtime execution sequence, not declaration order or yield sorting.
All retained scan instances participate, including earlier chain/stuck-at items and both KILL/EDC modes,
even when the displayed rows are filtered. Modules are independent; HRY/SPOFI do not suppress later items.
Missing order, incomplete execution evidence, or repeated calls of a failing instance make this count N/A.
Legacy caches without this metric show N/A and require fresh analysis. Existing yield formulas are unchanged.</p>
<p>KILL Final Kill Yield = 1 - Final Kill Dies / Tested Dies, sorted ascending.
EDC Counter Yield = 1 - Counter Fail Dies / Tested Dies, sorted descending. N/A is last in each group.
Final Kill Dies means the final DB resolves to that exact instance. KILL indicates a regular failure SetBin configuration,
not that every counter failure kills. EDC is a TP flow configuration, not a synonym for zero final kills.</p>
<p>Rates are recomputed from combined counts, not averaged across lots. Tests without execution, or with counter evidence
without matching execution evidence, have N/A yield. These are per-item rates, not lot yield.
Failures overlap across tests and must not be added as lot yield loss. Unknown counter IDs remain numeric;
their instance type cannot be checked for HRY/SPOFI exclusion.</p>'''
    document += '<p>' + ' | '.join('<a download href="' + escape(path.name, quote=True) + '">' + escape(name)
                                  + '</a>' for name, path in (('All statistics CSV', paths['statistics']),
                                                             ('Other-failure CSV', paths['overlap']))) + '</p>'
    summary_columns = [('Total Dies', 'total_dies'), ('Observed Scan Tests', 'observed_scan_tests'),
                       ('Failing Tests With Zero Final Kills', 'failing_tests_with_zero_final_kills'),
                       ('EDC Subset', 'zero_kill_EDC_tests'), ('KILL Subset', 'zero_kill_KILL_tests'),
                       ('Dies With Non-Kill Scan Failures', 'dies_with_nonkill_scan_failures'),
                       ('Final Scan Kill Dies', 'final_scan_kill_dies')]
    document += '<div class="scroll"><table><tr>' + ''.join('<th>' + label + '</th>' for label, key in summary_columns)
    document += '</tr><tr>' + ''.join('<td>' + str(data['summary'][key]) + '</td>' for label, key in summary_columns) + '</tr></table></div>'
    for mode, records in groups.items():
        observed = [row for row in records if row['observed_dies']]
        section_id = 'bin-tests' if mode == 'KILL' else 'edc-tests'
        document += '<section id="' + section_id + '"><h2>' + mode + ' (' + str(len(observed)) + ' observed instances)</h2>'
        document += '<p><a download href="' + escape(paths[mode.lower()].name, quote=True) + '">' + mode + ' CSV</a></p>'
        document += table(observed, mode) + '</section>'
    unseen = sum(not row['observed_dies'] for row in rows)
    document += '<p>' + str(unseen) + ' unexecuted instances are retained in CSV and JSON with N/A yield.</p></body></html>'
    report.write_text(document, encoding='utf-8')
    return {'report': str(report), **{name: str(path) for name, path in paths.items()}, 'summary': data['summary']}


def main(argv=None):
    parser = argparse.ArgumentParser(description='Combined scan counter and final-kill yield analysis.')
    parser.add_argument('ituff', nargs='*', help='Merged raw .ITF or concatenated .ITF.gz files')
    parser.add_argument('--tracking', help='AQUA DEVICE_TRACKING CSV')
    parser.add_argument('--tp-root', help='TP directory containing BinDefinitions.bdefs and Modules')
    parser.add_argument('--program', help='Expected iTUFF program name (default: TP directory name)')
    parser.add_argument('--scan-bin', action='append', help='Expected scan interface bin; repeatable (default: 41, 42)')
    parser.add_argument('--output', required=True, help='Output HTML path; other artifacts use the same stem')
    parser.add_argument('--render', help='Regenerate artifacts from this script\'s saved JSON without rereading logs')
    args = parser.parse_args(argv)
    if args.render:
        if args.ituff or args.tracking or args.tp_root or args.program or args.scan_bin:
            parser.error('--render cannot be combined with analysis input options')
    elif not (args.ituff and args.tracking and args.tp_root):
        parser.error('Analysis requires iTUFF files, --tracking and --tp-root')
    try:
        if args.render:
            data = json.loads(Path(args.render).read_text(encoding='utf-8'))
        else:
            program = args.program or Path(args.tp_root).name
            dies, validation = load_final_dies(args.ituff, args.tracking, program)
            data = build_statistics(dies, validation, args.tp_root, program, args.scan_bin or ['41', '42'])
        result = write_report(data, args.output)
    except (OSError, ValueError, KeyError) as error:
        parser.exit(1, f'Analysis failed: {error}\n')
    print(json.dumps(result, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())