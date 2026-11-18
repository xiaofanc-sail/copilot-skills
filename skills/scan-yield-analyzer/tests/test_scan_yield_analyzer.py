import csv
import gzip
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts' / 'scan_yield_analyzer.py'
SPEC = importlib.util.spec_from_file_location('scan_yield_analyzer', SCRIPT)
analyzer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(analyzer)


class AnalyzerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def record(self, lot='LOT_A', time='20260101010000', db='41510000', fb='4151', ib='41'):
        return (f'6_lotid_{lot}\n6_prgnm_TEST_TP\n5_lcode_100\n4_wafid_1\n3_lsep\n'
                f'3_wafxloc_2\n3_wafyloc_3\n3_dvtsteddt_{time}\n3_binn_{db}\n'
                f'3_curfbin_{fb}\n3_curibin_{ib}\n3_trslt_Fail\n'
                f'3_tfail_{{{db},{db}}}\n3_tpass_{{}}\n'
                '2_tname_testtime_SCN_SAMPLE::CHAIN\n3_lend\n')

    def tracking(self, lots=('LOT_A',), fb='4151', ib='41', db='41510000'):
        path = self.root / 'tracking.csv'
        fields = ['Lot', 'Operation', 'Within_LATO_Latest_Flag', 'Substructure_ID', 'Sort_Wafer_ID',
                  'Sort_X_Location', 'Sort_Y_Location', 'Functional_Bin', 'Interface_Bin', 'Data_Bin']
        with path.open('w', newline='', encoding='utf-8') as target:
            writer = csv.DictWriter(target, fieldnames=fields)
            writer.writeheader()
            for lot in lots:
                writer.writerow(dict(zip(fields, [lot, '100', 'Y', 'UNIT', '1', '2', '3', fb, ib, db])))
        return path

    def test_concatenated_gzip_and_retest(self):
        path = self.root / 'merged.ITF.gz'
        path.write_bytes(gzip.compress(self.record(time='20251231010000').encode())
                         + gzip.compress(self.record().encode()))
        records = list(analyzer.read_devices(path))
        self.assertEqual(len(records), 2)
        self.assertEqual(records[0]['3_tfail'], ['41510000'])
        dies, validation = analyzer.load_final_dies([path], self.tracking(), 'TEST_TP')
        self.assertEqual(len(dies), 1)
        self.assertEqual(dies[0]['3_dvtsteddt'], '20260101010000')
        self.assertEqual(validation[0]['raw_records'], 2)

    def test_counter_sets_allow_padding_and_empty_whitespace(self):
        path = self.root / 'padded.ITF'
        text = self.record().replace('3_tfail_{41510000,41510000}', '3_tfail_{ 41510000, 41510000 }')
        path.write_text(text.replace('3_tpass_{}', '3_tpass_{  }'), encoding='utf-8')
        record = next(analyzer.read_devices(path))
        self.assertEqual(record['3_tfail'], ['41510000'])
        self.assertEqual(record['3_tpass'], [])
        path.write_text(text.replace('3_tfail_{ 41510000, 41510000 }', '3_tfail_{  }'), encoding='utf-8')
        self.assertEqual(next(analyzer.read_devices(path))['3_tfail'], [])

    def test_lot_is_part_of_die_identity(self):
        paths = []
        for lot in ('LOT_A', 'LOT_B'):
            path = self.root / (lot + '.ITF')
            path.write_text(self.record(lot=lot), encoding='utf-8')
            paths.append(path)
        dies, _ = analyzer.load_final_dies(paths, self.tracking(('LOT_A', 'LOT_B')), 'TEST_TP')
        self.assertEqual(len(dies), 2)

    def test_execution_order_preserves_flow_and_repeated_calls(self):
        path = self.root / 'ordered.ITF'
        text = self.record().replace('2_tname_testtime_SCN_SAMPLE::CHAIN',
                                    '2_tname_testtime_SCN_SAMPLE::Z_LAST_ALPHABETICALLY\n'
                                    '2_tname_testtime_SCN_SAMPLE::CHAIN\n'
                                    '2_tname_testtime_SCN_SAMPLE::Z_LAST_ALPHABETICALLY')
        path.write_text(text, encoding='utf-8')
        record = next(analyzer.read_devices(path))
        self.assertEqual(record['scan_execution_order'], ['SCN_SAMPLE::Z_LAST_ALPHABETICALLY',
                                                        'SCN_SAMPLE::CHAIN',
                                                        'SCN_SAMPLE::Z_LAST_ALPHABETICALLY'])
        self.assertEqual(record['scan_execution_records'], ['SCN_SAMPLE::CHAIN',
                                                          'SCN_SAMPLE::Z_LAST_ALPHABETICALLY'])

    def test_tracking_mismatch_fails_closed(self):
        path = self.root / 'merged.ITF'
        path.write_text(self.record(), encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'Functional_Bin'):
            analyzer.load_final_dies([path], self.tracking(fb='9999'), 'TEST_TP')

    def test_missing_db_requires_matching_empty_tracking_db(self):
        path = self.root / 'empty_db.ITF'
        text = self.record(fb='9321', ib='93').replace('3_binn_41510000\n', '')
        path.write_text(text.replace('3_tfail_{41510000,41510000}', '3_tfail_{  }'), encoding='utf-8')
        dies, _ = analyzer.load_final_dies([path], self.tracking(fb='9321', ib='93', db=''), 'TEST_TP')
        self.assertEqual(len(dies), 1)
        self.assertEqual(dies[0]['3_binn'], '')
        with self.assertRaisesRegex(ValueError, 'Data_Bin'):
            analyzer.load_final_dies([path], self.tracking(fb='9321', ib='93'), 'TEST_TP')

    def test_scan_ib_still_requires_final_db(self):
        dies = self.sample_dies()
        dies[0]['3_binn'] = ''
        with self.assertRaisesRegex(ValueError, 'Scan IB has no matching final DB'):
            analyzer.build_statistics(dies, [], self.tp(), 'TEST_TP')

    def test_tp_mismatch_fails_closed(self):
        path = self.root / 'merged.ITF'
        path.write_text(self.record(), encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'TP mismatch'):
            analyzer.load_final_dies([path], self.tracking(), 'OTHER_TP')

    def test_total_bin_tokens_are_not_final_bins(self):
        path = self.root / 'merged.ITF'
        path.write_text(self.record().replace('3_lend', '3_curftbin_9999\n3_curdtbin_99990000\n3_lend'), encoding='utf-8')
        record = next(analyzer.read_devices(path))
        self.assertEqual(record['3_curfbin'], '4151')
        self.assertEqual(record['3_binn'], '41510000')

    def tp(self):
        root = self.root / 'TEST_TP'
        module = root / 'Modules' / 'SCN_SAMPLE'
        module.mkdir(parents=True)
        instances = {'CHAIN': ('41510000', False), 'CHAIN_RECOVERY': ('41520000', False),
                     'COLLECT': ('42510000', True), 'CLEAN': ('42520000', True),
                     'UNSEEN': ('42530000', True), 'HRY_SAMPLE': ('42540000', True),
                     'SPOFI_SAMPLE': ('42550000', True)}
        text = ''
        counters = []
        leaves = []
        for instance, (counter, edc) in instances.items():
            text += f'CSharpTest Sample {instance}\n{{}}\nFlowItem flow_{instance} {instance}'
            text += ' @EDC\n' if edc else '\n'
            text += f'{{\nSetBin b{counter}_fail_{instance}_0;\n}}\n'
            counters.append(f'n{counter}_fail_{instance}_0')
            leaves.append(f'LeafBin b{counter}_fail_SCN_SAMPLE_{instance}_0 {counter}: "sample", shared;')
        text += 'Counters {\n' + '\n'.join(counters) + '\n}\n'
        (module / 'SCN_SAMPLE.mtpl').write_text(text, encoding='utf-8')
        (root / 'BinDefinitions.bdefs').write_text('BinGroup SoftBins { Bin shared 4151: "shared"; }\n'
                                                   + '\n'.join(leaves), encoding='utf-8')
        return root

    def sample_dies(self):
        path = self.root / 'sample.ITF'
        path.write_text(self.record(), encoding='utf-8')
        first = next(analyzer.read_devices(path))
        second = {**first, '6_lotid': 'LOT_B', '3_binn': '1', '3_curfbin': '1',
                  '3_curibin': '1', '3_trslt': 'Pass'}
        names = ['CHAIN', 'CHAIN_RECOVERY', 'COLLECT', 'CLEAN', 'HRY_SAMPLE', 'SPOFI_SAMPLE']
        for die in (first, second):
            die['scan_execution_records'] = ['SCN_SAMPLE::' + name for name in names]
            die['scan_execution_order'] = list(die['scan_execution_records'])
            die['3_tfail'] = ['41510000', '41520000', '42510000', '42540000', '42550000', '12345678901']
        return [first, second]

    def test_first_scan_fail_dies_follow_each_die_flow_not_yield_order(self):
        dies = self.sample_dies()
        dies[1]['scan_execution_order'].reverse()
        data = analyzer.build_statistics(dies, [], self.tp(), 'TEST_TP')
        rows = {row['test'].split('::')[1]: row for row in data['rows']}
        self.assertEqual(rows['CHAIN']['first_scan_fail_dies'], 1)
        self.assertEqual(rows['CHAIN']['first_scan_fail_die_indexes'], [0])
        self.assertEqual(rows['CHAIN_RECOVERY']['first_scan_fail_dies'], 0)
        self.assertEqual(rows['COLLECT']['first_scan_fail_dies'], 1)
        self.assertEqual(rows['COLLECT']['first_scan_fail_die_indexes'], [1])
        self.assertEqual(rows['CLEAN']['first_scan_fail_dies'], 0)
        self.assertIsNone(rows['UNSEEN']['first_scan_fail_dies'])
        self.assertEqual(rows['CHAIN']['counter_fail_dies'], 2)
        self.assertEqual(rows['COLLECT']['yield_percent'], 0)
        result = analyzer.write_report(data, self.root / 'first.html')
        text = Path(result['report']).read_text(encoding='utf-8')
        self.assertEqual(text.count('<th>First Scan Fail Dies</th>'), 2)
        with Path(result['edc']).open(encoding='utf-8-sig', newline='') as source:
            exported = {row['test'].split('::')[1]: row for row in csv.DictReader(source)}
        self.assertEqual(exported['COLLECT']['first_scan_fail_dies'], '1')
        self.assertEqual(exported['UNSEEN']['first_scan_fail_dies'], '')

    def test_first_scan_fail_dies_exclude_hry_spofi_and_other_modules(self):
        die = {'scan_execution_records': ['SCN_A::HRY_X', 'SCN_A::SPOFI_X', 'SCN_A::PASS',
                                         'SCN_A::LATE', 'SCN_B::LATE']}
        die['scan_execution_order'] = list(die['scan_execution_records'])
        failed = dict.fromkeys(['SCN_A::HRY_X', 'SCN_A::SPOFI_X', 'SCN_A::LATE', 'SCN_B::LATE'])
        first, uncertain = analyzer.first_scan_failures(die, failed)
        self.assertEqual(first, {'SCN_A::LATE', 'SCN_B::LATE'})
        self.assertEqual(uncertain, set())

    def test_first_scan_fail_dies_are_na_for_missing_or_ambiguous_order(self):
        root = self.tp()
        for scenario in ('missing_order', 'missing_execution', 'repeated_failure'):
            with self.subTest(scenario=scenario):
                dies = self.sample_dies()
                if scenario == 'missing_order':
                    dies[0].pop('scan_execution_order')
                elif scenario == 'missing_execution':
                    dies[0]['scan_execution_order'].remove('SCN_SAMPLE::CHAIN')
                    dies[0]['scan_execution_records'].remove('SCN_SAMPLE::CHAIN')
                else:
                    dies[0]['scan_execution_order'].append('SCN_SAMPLE::CHAIN')
                data = analyzer.build_statistics(dies, [], root, 'TEST_TP')
                collect = next(row for row in data['rows'] if row['test'] == 'SCN_SAMPLE::COLLECT')
                self.assertIsNone(collect['first_scan_fail_dies'])
                self.assertIsNone(collect['first_scan_fail_die_indexes'])
                self.assertEqual(collect['uncertain_scan_order_dies'], 1)
                self.assertEqual(collect['counter_fail_dies'], 2)
                self.assertEqual(collect['yield_percent'], 0)

    def test_legacy_cache_first_scan_fail_dies_are_na(self):
        data = analyzer.build_statistics(self.sample_dies(), [], self.tp(), 'TEST_TP')
        for row in data['rows']:
            row.pop('first_scan_fail_dies')
        result = analyzer.write_report(data, self.root / 'legacy.html')
        with Path(result['statistics']).open(encoding='utf-8-sig', newline='') as source:
            self.assertTrue(all(row['first_scan_fail_dies'] == '' for row in csv.DictReader(source)))
        cached = json.loads(Path(result['cache']).read_text(encoding='utf-8'))
        self.assertTrue(all(row['first_scan_fail_dies'] is None for row in cached['rows']))

    def test_statistics_formulas_and_sort_order(self):
        data = analyzer.build_statistics(self.sample_dies(), [], self.tp(), 'TEST_TP')
        kill = [row for row in data['rows'] if row['flow_mode'] == 'KILL']
        edc = [row for row in data['rows'] if row['flow_mode'] == 'EDC']
        self.assertEqual([row['yield_percent'] for row in kill], [50, 100])
        self.assertEqual([row['yield_percent'] for row in edc], [100, 0, None])
        self.assertEqual(kill[1]['counter_fail_dies'], 2)
        self.assertEqual(kill[1]['final_kill_dies'], 0)
        self.assertEqual(kill[1]['fb'], '4151')
        self.assertEqual(data['summary']['total_dies'], 2)
        self.assertEqual(data['summary']['final_scan_kill_dies'], 1)

    def test_fail_on_final_pass_dies_deduplicate_without_alias_column(self):
        dies = self.sample_dies()
        dies[1]['3_curibin'] = '5'
        dies[1]['3_trslt'] = 'pAsS'
        dies[1]['3_tfail'] = dies[1]['3_tfail'] + ['42510000', '42510000']
        dies.append({**dies[1], '6_lotid': 'LOT_C', '3_trslt': 'Fail'})
        data = analyzer.build_statistics(dies, [], self.tp(), 'TEST_TP')
        rows = {row['test'].split('::')[1]: row for row in data['rows']}
        for name in ('CHAIN', 'CHAIN_RECOVERY', 'COLLECT'):
            self.assertEqual(rows[name]['counter_fail_dies'], 3)
            self.assertNotIn('good_die_counter_fail_dies', rows[name])
            self.assertEqual(rows[name]['fail_on_final_pass_dies'], 1)
        self.assertEqual(rows['COLLECT']['first_scan_fail_dies'], 0)
        self.assertEqual(rows['COLLECT']['yield_percent'], 0)
        self.assertEqual(rows['CLEAN']['fail_on_final_pass_dies'], 0)
        self.assertEqual(rows['UNSEEN']['fail_on_final_pass_dies'], 0)
        result = analyzer.write_report(data, self.root / 'good.html')
        text = Path(result['report']).read_text(encoding='utf-8')
        self.assertEqual(text.count('<th>Fail on Final PASS Dies</th>'), 2)
        self.assertNotIn('Good Die Counter Fail Dies', text)
        for mode in ('kill', 'edc', 'statistics'):
            with Path(result[mode]).open(encoding='utf-8-sig', newline='') as source:
                reader = csv.DictReader(source)
                self.assertEqual(reader.fieldnames.count('fail_on_final_pass_dies'), 1)
                self.assertNotIn('good_die_counter_fail_dies', reader.fieldnames)
                exported = list(reader)
            self.assertTrue(exported)
            self.assertTrue(all(int(row['fail_on_final_pass_dies']) ==
                                rows[row['test'].split('::')[1]]['fail_on_final_pass_dies'] for row in exported))
        cached = json.loads(Path(result['cache']).read_text(encoding='utf-8'))
        rendered = analyzer.write_report(cached, self.root / 'good_cached.html')
        self.assertEqual(Path(result['statistics']).read_bytes(), Path(rendered['statistics']).read_bytes())

    def test_fail_on_final_pass_dies_migrates_alias_cache_and_handles_missing_metric(self):
        data = analyzer.build_statistics(self.sample_dies(), [], self.tp(), 'TEST_TP')
        expected = {row['test']: row['fail_on_final_pass_dies'] for row in data['rows']}
        for row in data['rows']:
            row['good_die_counter_fail_dies'] = row.pop('fail_on_final_pass_dies')
        data['rows'][0].pop('good_die_counter_fail_dies')
        data['rows'][1]['fail_on_final_pass_dies'] = expected[data['rows'][1]['test']]
        data['rows'][1]['good_die_counter_fail_dies'] = 999
        result = analyzer.write_report(data, self.root / 'legacy_good.html')
        self.assertIsNone(data['rows'][0]['fail_on_final_pass_dies'])
        for row in data['rows'][1:]:
            self.assertEqual(row['fail_on_final_pass_dies'], expected[row['test']])
        self.assertTrue(all('good_die_counter_fail_dies' not in row for row in data['rows']))
        with Path(result['statistics']).open(encoding='utf-8-sig', newline='') as source:
            self.assertEqual(next(csv.DictReader(source))['fail_on_final_pass_dies'], '')
        cached = json.loads(Path(result['cache']).read_text(encoding='utf-8'))
        self.assertTrue(all('good_die_counter_fail_dies' not in row for row in cached['rows']))
        self.assertIn('<td>N/A</td>', Path(result['report']).read_text(encoding='utf-8'))

    def test_exclusion_and_unknown_overlap_counters(self):
        data = analyzer.build_statistics(self.sample_dies(), [], self.tp(), 'TEST_TP')
        self.assertFalse(any(analyzer.excluded(row['test']) for row in data['rows']))
        row = data['rows'][0]
        labels = [other['other_test_or_counter'] for other in row['other_failures']]
        self.assertNotIn(row['test'], labels)
        self.assertFalse(any(analyzer.excluded(label) for label in labels))
        self.assertIn('Unmapped counter 12345678901', labels)
        self.assertEqual(row['fail_on_final_pass_dies'], 1)

    def test_missing_execution_is_na_not_negative_yield(self):
        dies = self.sample_dies()
        dies[1]['scan_execution_records'].remove('SCN_SAMPLE::CHAIN')
        data = analyzer.build_statistics(dies, [], self.tp(), 'TEST_TP')
        chain = next(row for row in data['rows'] if row['test'] == 'SCN_SAMPLE::CHAIN')
        self.assertIsNone(chain['yield_percent'])
        self.assertEqual(chain['counter_only_dies'], 1)

    def test_missing_regular_setbin_is_not_called_kill(self):
        root = self.tp()
        path = root / 'Modules' / 'SCN_SAMPLE' / 'SCN_SAMPLE.mtpl'
        path.write_text(path.read_text().replace('SetBin b41510000_fail_CHAIN_0;', ''), encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'No regular failure SetBin'):
            analyzer.build_statistics(self.sample_dies(), [], root, 'TEST_TP')

    def test_mixed_flow_modes_fail_closed(self):
        root = self.tp()
        path = root / 'Modules' / 'SCN_SAMPLE' / 'SCN_SAMPLE.mtpl'
        path.write_text(path.read_text() + '\nFlowItem another CHAIN @EDC\n{}\n', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'mixed FlowItem'):
            analyzer.build_statistics(self.sample_dies(), [], root, 'TEST_TP')

    def test_standalone_report_csv_and_cached_render(self):
        data = analyzer.build_statistics(self.sample_dies(), [], self.tp(), 'TEST_TP')
        data['validation'] = [{'lot': '<synthetic & lot>'}]
        output = self.root / 'reports' / 'scan.html'
        result = analyzer.write_report(data, output)
        text = output.read_text(encoding='utf-8')
        self.assertIn('&lt;synthetic &amp; lot&gt;', text)
        kill = text.split('<section id="bin-tests">', 1)[1].split('</section>', 1)[0]
        edc = text.split('<section id="edc-tests">', 1)[1].split('</section>', 1)[0]
        self.assertIn('<th>FB</th><th>Test</th><th>TP Flow Mode</th><th>Final Kill Dies</th><th>Final Kill Yield</th>', kill)
        self.assertNotIn('<th>FB</th>', edc)
        self.assertIn('<th>Counter Yield</th>', edc)
        self.assertNotIn('Fail Without This Final Kill', text)
        with Path(result['kill']).open(encoding='utf-8-sig', newline='') as source:
            reader = csv.DictReader(source)
            self.assertEqual(reader.fieldnames[:5], ['fb', 'test', 'flow_mode', 'final_kill_dies', 'yield_percent'])
            self.assertEqual([float(row['yield_percent']) for row in reader], [50, 100])
        with Path(result['edc']).open(encoding='utf-8-sig', newline='') as source:
            rows = list(csv.DictReader(source))
            self.assertEqual([row['yield_percent'] for row in rows], ['100.0', '0.0', ''])
        cached = json.loads(Path(result['cache']).read_text(encoding='utf-8'))
        second = self.root / 'second.html'
        rendered = analyzer.write_report(cached, second)
        self.assertTrue(second.exists())
        self.assertEqual([row['first_scan_fail_dies'] for row in cached['rows']], [2, 0, 0, 0, None])
        self.assertEqual(cached['evidence'][0]['scan_execution_order'],
                 self.sample_dies()[0]['scan_execution_order'])
        self.assertEqual(Path(rendered['statistics']).read_bytes(), Path(result['statistics']).read_bytes())

    def test_cli_analysis_and_render(self):
        root = self.tp()
        path = self.root / 'input.ITF'
        path.write_text(self.record(), encoding='utf-8')
        output = self.root / 'cli.html'
        process = subprocess.run([sys.executable, str(SCRIPT), str(path), '--tracking', str(self.tracking()),
                                  '--tp-root', str(root), '--output', str(output)], capture_output=True, text=True)
        self.assertEqual(process.returncode, 0, process.stderr)
        result = json.loads(process.stdout)
        self.assertEqual(result['summary']['total_dies'], 1)
        rendered = subprocess.run([sys.executable, str(SCRIPT), '--render', result['cache'], '--output',
                                   str(self.root / 'cached.html')], capture_output=True, text=True)
        self.assertEqual(rendered.returncode, 0, rendered.stderr)

    def test_empty_edc_group_still_exports_header(self):
        data = analyzer.build_statistics(self.sample_dies(), [], self.tp(), 'TEST_TP')
        data['rows'] = [row for row in data['rows'] if row['flow_mode'] == 'KILL']
        result = analyzer.write_report(data, self.root / 'no_edc.html')
        with Path(result['edc']).open(encoding='utf-8-sig', newline='') as source:
            reader = csv.DictReader(source)
            self.assertIn('yield_percent', reader.fieldnames)
            self.assertEqual(list(reader), [])


if __name__ == '__main__':
    unittest.main()