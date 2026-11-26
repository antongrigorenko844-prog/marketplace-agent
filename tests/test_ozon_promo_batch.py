import copy
import unittest

from ozon_promo_batch import (
    build_approved_plan, evaluate_item, financial_comparison,
    scope_digest, validate_approved_plan,
)


NOW = '2026-11-26T18:30:00+00:00'


def make_row(i, group='C', known=True):
    sku, pid = 'Sku-' + str(i), i + 1000
    target = {'A': 1100, 'B': 1000, 'C': 960}[group]
    current = {
        'services_estimated': known, 'service_model': {'available': known,
             'percentage_rate_pct': 2.8, 'fixed_per_sold_unit_rub': 0},
        'by_scheme': {'fbs': {'commission_rate_pct': 47, 'acquiring_rate_pct': 1,
            'deductions': {'logistics_snapshot_max_rub': 100}}},
        'tax_rate_scenario': .06, 'unknown_costs': ['seller_packaging', 'warranty'],
    }
    action = {'action_id': 11, 'title': 'Approved action', 'group': 'candidates',
              'lifecycle': 'active', 'date_start': '2020-01-01T00:00:00Z',
              'date_end': '2030-01-01T00:00:00Z', 'cap_rub': 1200,
              'action_type': 'STOCK_DISCOUNT', 'eligible_for_price_comparison': True}
    scenario = {'action_ids': [11], 'economics': current}
    return {
        'sku': sku, 'product_id': pid, 'title': sku, 'strategy_code': group,
        'current_price': 1000, 'review_target_price': target, 'required_unit_growth_pct': 7,
        'observed_min_price': 900, 'old_price': 1400, 'auto_add_enabled': False,
        'economics': {'current': current, 'procurement': {'scenario_cost_rub': 100},
                      'historical': {'observations': {'sold': 3, 'returned': 0}}},
        'scenarios': {'preservation': scenario, 'closest_discount': scenario},
        'same_price_candidates': [{'action_id': 11}], 'actions': [action],
        'stock': {'status': 'OBSERVED', 'rows': [{'offer_id': sku, 'product_id': pid,
                    'stocks': [{'present': 2, 'reserved': 0, 'type': 'fbs'}]}]},
        'protection': {'enabled': False},
    }


def make_plan():
    rows = [make_row(i, 'A') for i in range(9)]
    rows += [make_row(i, 'B') for i in range(9, 38)]
    rows += [make_row(i, 'C') for i in range(38, 96)]
    cat = {'snapshot_at': NOW, 'rows': rows}
    return build_approved_plan(cat, 'Explicit exact package approval'), rows


class ApprovedBatchTests(unittest.TestCase):
    def setUp(self):
        self.plan, self.rows = make_plan()

    def item(self, group='C'):
        index = {'A': 0, 'B': 9, 'C': 38}[group]
        return self.plan['items'][index], self.rows[index]

    def test_exact_package_and_floor_digest(self):
        self.assertTrue(validate_approved_plan(self.plan))
        for field in ('target_price', 'experiment_floor_rub'):
            changed = copy.deepcopy(self.plan)
            changed['items'][0][field] -= 1
            with self.assertRaises(ValueError):
                validate_approved_plan(changed)
        changed = copy.deepcopy(self.plan)
        changed['items'][0]['allowed_action_ids'].append(99)
        with self.assertRaises(ValueError):
            validate_approved_plan(changed)

    def test_runtime_state_does_not_widen_approval(self):
        old = scope_digest(self.plan)
        self.plan['enabled'] = True
        self.plan['items'][0]['last_verified'] = {'observed_at': NOW}
        self.plan['items'][0]['requests'] = [{'operation': 'price'}]
        self.assertEqual(old, scope_digest(self.plan))
        self.assertTrue(validate_approved_plan(self.plan))

    def test_unknown_services_hold_only_affected_sku(self):
        item, row = self.item()
        row['economics']['current']['services_estimated'] = False
        result = evaluate_item(item, row, now=NOW)
        self.assertEqual(result['status'], 'HOLD')
        self.assertIn('SERVICE_OR_FINANCIAL_EVIDENCE_REQUIRED', result['blockers'])
        other = evaluate_item(self.plan['items'][39], self.rows[39], now=NOW)
        self.assertTrue(other['price_write_allowed'])

    def test_sourced_services_recover_without_double_acquiring(self):
        item, row = self.item()
        row['economics']['current']['services_estimated'] = False
        evidence = {'sku': item['sku'], 'verified': True, 'source_reference': 'exact fee ledger',
                    'kind': 'observed_sku_fees', 'percentage_rate_pct': 2.8,
                    'fixed_per_sold_unit_rub': 0}
        result = evaluate_item(item, row, evidence, now=NOW)
        self.assertEqual(result['status'], 'READY')
        self.assertEqual(result['financial_check']['baseline_partial_contribution_rub'], 232)
        self.assertEqual(result['financial_check']['target_partial_contribution_rub'], 214.72)

    def test_recovered_costs_crossing_ten_percent_hold(self):
        item, row = self.item()
        row['economics']['current']['services_estimated'] = False
        evidence = {'sku': item['sku'], 'verified': True, 'source_reference': 'exact fee ledger',
                    'kind': 'observed_sku_fees', 'percentage_rate_pct': 10,
                    'fixed_per_sold_unit_rub': 50}
        result = evaluate_item(item, row, evidence, now=NOW)
        self.assertIn('UPDATED_UNIT_GROWTH_EXCEEDS_APPROVED_PACKAGE_SCOPE', result['blockers'])
        self.assertFalse(result['price_write_allowed'])

    def test_same_price_official_no_incremental_cost_exception(self):
        item, row = self.item('B')
        row['economics']['current']['services_estimated'] = False
        evidence = {'sku': item['sku'], 'verified': True, 'source_reference': 'official applicable terms',
                    'kind': 'official_no_incremental_cost', 'no_incremental_seller_cost': True,
                    'seller_price_unchanged': True, 'action_ids': [11]}
        result = evaluate_item(item, row, evidence, now=NOW)
        self.assertEqual(result['ready_action_ids'], [11])
        self.assertFalse(result['price_write_allowed'])
        self.assertFalse(result['financial_check']['full_profit_verified'])

    def test_future_does_not_trigger_early_price_reduction(self):
        item, row = self.item()
        row['actions'][0]['date_start'] = '2026-12-02T21:00:00Z'
        result = evaluate_item(item, row, now=NOW)
        self.assertEqual(result['status'], 'DEFERRED')
        self.assertFalse(result['price_write_allowed'])
        self.assertEqual(result['deferred_action_ids'], [11])

    def test_stock_zero_and_identity_conflicts_hold(self):
        item, row = self.item()
        row['stock']['rows'][0]['stocks'][0]['present'] = 0
        self.assertIn('NO_POSITIVE_OBSERVED_STOCK', evaluate_item(item, row, now=NOW)['blockers'])
        row['stock']['rows'][0]['stocks'][0]['present'] = 2
        row['stock']['rows'][0]['product_id'] += 1
        self.assertIn('NO_POSITIVE_OBSERVED_STOCK', evaluate_item(item, row, now=NOW)['blockers'])

    def test_lower_live_cap_does_not_select_lower_price(self):
        item, row = self.item()
        row['actions'][0]['cap_rub'] = item['target_price'] - 1
        result = evaluate_item(item, row, now=NOW)
        self.assertEqual(result['status'], 'HOLD')
        self.assertEqual(result['target_price'], item['target_price'])
        self.assertFalse(result['price_write_allowed'])

    def test_unlisted_action_not_adopted_and_price_drift_blocks(self):
        item, row = self.item()
        row['actions'][0]['action_id'] = 12
        self.assertEqual(evaluate_item(item, row, now=NOW)['ready_action_ids'], [])
        row['actions'][0]['action_id'] = 11
        row['current_price'] = 1001
        self.assertIn('SELLER_PRICE_DRIFT', evaluate_item(item, row, now=NOW)['blockers'])

    def test_exact_case_identity_and_no_input_mutation(self):
        item, row = self.item()
        original = copy.deepcopy(row)
        evaluate_item(item, row, now=NOW)
        self.assertEqual(row, original)
        row['sku'] = row['sku'].lower()
        self.assertIn('EXACT_IDENTITY_MISMATCH', evaluate_item(item, row, now=NOW)['blockers'])

    def test_zero_fee_claim_needs_explicit_source_scope(self):
        item, row = self.item()
        row['economics']['current']['services_estimated'] = False
        evidence = {'sku': item['sku'], 'verified': True, 'source_reference': 'something',
                    'kind': 'official_programme_terms', 'percentage_rate_pct': 0,
                    'fixed_per_sold_unit_rub': 0}
        self.assertFalse(financial_comparison(item, row, evidence)['valid'])


if __name__ == '__main__':
    unittest.main()
