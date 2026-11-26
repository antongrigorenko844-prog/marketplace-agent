"""Exact approved Ozon package and pure financial preflight; no marketplace I/O.

The target prices/action IDs are immutable under ``scope_sha256``. ``enabled``,
readback records and requests are execution state, deliberately outside that hash.
Passing this financial preflight does not replace executor identity, freshness,
buyer-price, promotion-treatment, protection, write/readback or journal checks.
"""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
from pathlib import Path

DEFAULT_PLAN = Path(__file__).resolve().parent / 'data/ozon_promo_execution_20261127/approved_plan.json'
DECISION_ID = 'ozon-promo-96-20261127'
EXPECTED_COUNT = 96
RUNTIME_FIELDS = {'initial_verified', 'last_verified', 'requests'}


def _number(value):
    if isinstance(value, bool) or value in (None, ''):
        raise ValueError('Missing or invalid number')
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError('Invalid number') from exc
    if not result.is_finite():
        raise ValueError('Non-finite number')
    return result


def _money(value):
    result = _number(value)
    if result <= 0:
        raise ValueError('Price must be positive')
    return float(result.quantize(Decimal('.01')))


def _timestamp(value):
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc) if value.tzinfo else None
    try:
        result = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return result.astimezone(timezone.utc) if result.tzinfo else None
    except (AttributeError, ValueError, TypeError):
        return None


def _pid(value):
    if isinstance(value, bool) or not str(value).isdecimal() or int(value) <= 0:
        raise ValueError('Invalid product/action ID')
    return int(value)


def scope_digest(plan):
    """Hash approved content, excluding mutable execution/readback state."""
    payload = {key: plan.get(key) for key in ('schema_version', 'decision_id', 'approved',
               'approvalReference', 'approval_scope', 'source')}
    payload['items'] = [{k: v for k, v in row.items() if k not in RUNTIME_FIELDS}
                        for row in plan.get('items', [])]
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True,
                                    separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def validate_approved_plan(plan):
    """Raise on a changed scope, duplicate identity, widened target or missing approval."""
    if not isinstance(plan, dict) or plan.get('schema_version') != 1:
        raise ValueError('Unsupported approved-plan schema')
    if plan.get('decision_id') != DECISION_ID or plan.get('approved') is not True:
        raise ValueError('Exact package approval is required')
    if not isinstance(plan.get('approvalReference'), str) or not plan['approvalReference'].strip():
        raise ValueError('Missing owner approval reference')
    if not isinstance(plan.get('enabled'), bool):
        raise ValueError('enabled must be explicit boolean execution state')
    items = plan.get('items')
    if not isinstance(items, list) or len(items) != EXPECTED_COUNT:
        raise ValueError('Approved package must contain exactly 96 items')
    skus, pids = set(), set()
    for item in items:
        sku = item.get('sku')
        if not isinstance(sku, str) or not sku or sku != sku.strip() or sku in skus:
            raise ValueError('Missing, padded or duplicate exact SKU')
        skus.add(sku)
        pid = _pid(item.get('product_id'))
        if pid in pids:
            raise ValueError('Duplicate product ID')
        pids.add(pid)
        before, target = _money(item.get('before_price')), _money(item.get('target_price'))
        if _money(item.get('experiment_floor_rub')) != target:
            raise ValueError('Experiment floor must equal the exact approved target')
        for alias, canonical in (('price_before_rub', before), ('target_price_rub', target)):
            if alias in item and _money(item[alias]) != canonical:
                raise ValueError('Conflicting price aliases')
        group = item.get('strategy_code')
        if group not in ('A', 'B', 'C'):
            raise ValueError('Unapproved strategy group')
        if (group == 'A' and target <= before) or (group == 'B' and target != before) or (group == 'C' and target >= before):
            raise ValueError('Target does not match the approved strategy')
        aids = item.get('allowed_action_ids')
        if not isinstance(aids, list) or not aids or len(aids) != len(set(aids)):
            raise ValueError('Unique approved action IDs are required')
        for aid in aids:
            _pid(aid)
        expected = {x['action_id'] for x in item.get('approved_actions', [])}
        if set(aids) != expected:
            raise ValueError('Approved actions and IDs differ')
    if Counter(r['strategy_code'] for r in items) != {'A': 9, 'B': 29, 'C': 58}:
        raise ValueError('The approved 9/29/58 package changed')
    if plan.get('scope_sha256') != scope_digest(plan):
        raise ValueError('Immutable approved scope digest mismatch')
    return True


def load_approved_plan(path=None):
    plan = json.loads(Path(path or DEFAULT_PLAN).read_text(encoding='utf-8'))
    validate_approved_plan(plan)
    return plan


def _stock_positive(row):
    source = row.get('stock') or {}
    if source.get('status') != 'OBSERVED':
        return False
    for raw in source.get('rows', []):
        if raw.get('offer_id') not in (None, row.get('sku')):
            return False
        pid = raw.get('product_id') or raw.get('id')
        try:
            if pid is not None and _pid(pid) != _pid(row.get('product_id')):
                return False
        except ValueError:
            return False
    try:
        return any(_number(s.get('present', 0)) > 0 for raw in source.get('rows', []) for s in raw.get('stocks', []))
    except ValueError:
        return False


def _action_record(action):
    return {key: deepcopy(action.get(key)) for key in (
        'action_id', 'title', 'action_type', 'group', 'lifecycle', 'date_start', 'date_end',
        'cap_rub', 'observed_action_price', 'current_boost', 'max_boost', 'min_boost',
        'price_max_elastic', 'price_min_elastic', 'eligible_for_price_comparison')}


def build_approved_plan(catalogue, approval_reference, *, source=None, selection_csv_rows=None):
    """Build once from the accepted snapshot. Never reselect candidates during execution."""
    rows = catalogue['rows']
    selected = [r for r in rows if r['strategy_code'] in ('A', 'B') or
                (r['strategy_code'] == 'C' and r['required_unit_growth_pct'] is not None
                 and _number(r['required_unit_growth_pct']) <= 10)]
    csv_map = {r['sku']: r for r in selection_csv_rows} if selection_csv_rows is not None else None
    if csv_map is not None:
        csv_selected = {r['sku'] for r in selection_csv_rows if r['strategy_code'] in ('A', 'B') or
                        (r['strategy_code'] == 'C' and r['required_unit_growth_pct'] not in ('', None)
                         and _number(r['required_unit_growth_pct']) <= 10)}
        if csv_selected != {r['sku'] for r in selected} or len(csv_map) != len(selection_csv_rows):
            raise ValueError('Canonical CSV exact selection differs')
    plan = {
        'schema_version': 1, 'decision_id': DECISION_ID, 'approved': True, 'enabled': False,
        'approvalReference': approval_reference,
        'approval_scope': 'Exact 96 SKUs, target prices and listed action IDs; final evidence checks required. No universal margin or profit floor approved.',
        'source': deepcopy(source or {'snapshot_at': catalogue['snapshot_at']}), 'items': [],
    }
    for row in selected:
        sku, group = row['sku'], row['strategy_code']
        before, target = _money(row['current_price']), _money(row['review_target_price'])
        if csv_map is not None:
            c = csv_map[sku]
            if (_pid(c['product_id']) != row['product_id'] or c['strategy_code'] != group
                    or _money(c['current_price']) != before or _money(c['review_target_price']) != target):
                raise ValueError('Canonical CSV exact price/identity differs: ' + sku)
        if group == 'A':
            scenario = row['scenarios']['preservation']
            aids = scenario['action_ids']
        elif group == 'B':
            scenario = row['economics']['current']
            aids = [a['action_id'] for a in row['same_price_candidates']]
        else:
            scenario = row['scenarios']['closest_discount']
            aids = scenario['action_ids']
        actions = [_action_record(a) for a in row['actions'] if a['action_id'] in set(aids)]
        unique = {a['action_id']: a for a in actions}
        existing = [_action_record(a) for a in row['actions'] if a['group'] == 'participants']
        holds = []
        if not _stock_positive(row):
            holds.append('NO_POSITIVE_OBSERVED_STOCK')
        if not row['economics']['current'].get('services_estimated'):
            holds.append('SERVICE_EVIDENCE_REQUIRED')
        if all(a['lifecycle'] != 'active' for a in actions):
            holds.append('NO_CURRENTLY_ACTIVE_APPROVED_ACTION')
        fields = {
            'price': before, 'min_price': row.get('observed_min_price'),
            'old_price': row.get('old_price'), 'currency_code': 'RUB',
            'auto_add_to_ozon_actions_list_enabled': row.get('auto_add_enabled'),
            'min_price_for_auto_actions_enabled': row.get('protection', {}).get('enabled'),
        }
        item = {
            'decision_id': DECISION_ID, 'sku': sku, 'product_id': row['product_id'],
            'title': row['title'], 'strategy_code': group,
            'before_price': before, 'target_price': target,
            'price_before_rub': before, 'target_price_rub': target,
            'experiment_floor_rub': target, 'financial_break_even_claim': False,
            'allowed_action_ids': sorted(unique), 'approved_actions': [unique[k] for k in sorted(unique)],
            'existing_actions': existing,
            'candidate_actions': [a for a in unique.values() if a['group'] == 'candidates'],
            'economics': {'procurement': deepcopy(row['economics']['procurement']),
                          'current': deepcopy(row['economics']['current']),
                          'target': deepcopy(scenario.get('economics', scenario)),
                          'historical_observations': deepcopy(row['economics']['historical'].get('observations')),
                          'source_service_model': deepcopy(row['economics']['current'].get('service_model'))},
            'source_stock': deepcopy(row['stock']), 'initial_holds': holds,
            'initial_verified': {'decision_id': DECISION_ID, 'observed_at': catalogue['snapshot_at'],
                                 'source': 'approved source snapshot; executor must refresh before enabling', 'fields': fields},
            'last_verified': None, 'requests': [],
            'constraints': {'new_lower_cap_requires_new_approval': True,
                            'new_action_id_requires_new_approval': True,
                            'max_required_unit_growth_pct': 10 if group == 'C' else None,
                            'services_must_be_supported_or_same_price_no_incremental_cost_proven': True,
                            'full_profit_verified': False},
        }
        plan['items'].append(item)
    plan['scope_sha256'] = scope_digest(plan)
    validate_approved_plan(plan)
    return plan


def _service_terms(item, row, evidence):
    current = row.get('economics', {}).get('current', {})
    model = current.get('service_model') or {}
    if current.get('services_estimated') is True and model.get('available') is True:
        return (_number(model['percentage_rate_pct']), _number(model['fixed_per_sold_unit_rub']),
                'observed_exact_sku_services', False)
    if not isinstance(evidence, dict) or evidence.get('sku') != item['sku'] or evidence.get('verified') is not True or not evidence.get('source_reference'):
        return None, None, 'missing', False
    kind = evidence.get('kind')
    if kind == 'official_no_incremental_cost':
        valid = (item['strategy_code'] == 'B' and item['target_price'] == item['before_price']
                 and evidence.get('no_incremental_seller_cost') is True
                 and evidence.get('seller_price_unchanged') is True
                 and set(item['allowed_action_ids']).issubset(set(evidence.get('action_ids') or [])))
        return None, None, kind if valid else 'invalid', valid
    if kind not in ('observed_sku_fees', 'official_programme_terms'):
        return None, None, 'invalid', False
    try:
        rate = _number(evidence['percentage_rate_pct'])
        fixed = _number(evidence['fixed_per_sold_unit_rub'])
        if rate < 0 or fixed < 0 or rate >= 100:
            raise ValueError('Invalid service terms')
        if rate == 0 and fixed == 0 and evidence.get('zero_services_confirmed') is not True:
            raise ValueError('Zero services require explicit evidence')
        return rate, fixed, kind, False
    except (ValueError, KeyError):
        return None, None, 'invalid', False


def financial_comparison(item, row, service_evidence=None):
    """Reprice the exact approved before/target with fresh scheme deductions."""
    current = row.get('economics', {}).get('current', {})
    rate, fixed, source, same_price_exception = _service_terms(item, row, service_evidence)
    known = rate is not None and fixed is not None
    schemes = current.get('by_scheme') or {}
    cost = row.get('economics', {}).get('procurement', {}).get('scenario_cost_rub')
    result = {'services_supported': known, 'service_basis': source,
              'same_price_no_incremental_cost_supported': same_price_exception,
              'full_profit_verified': False, 'metric_kind': 'partial_contribution_not_net_profit',
              'unknown_costs': deepcopy(current.get('unknown_costs') or []), 'by_scheme': {}}
    if known:
        result['unknown_costs'] = [x for x in result['unknown_costs'] if x != 'current_marketplace_service_costs']
    if isinstance(service_evidence, dict) and source != 'observed_exact_sku_services':
        result['service_evidence_reference'] = service_evidence.get('source_reference')
        result['service_evidence_assumptions'] = deepcopy(service_evidence.get('assumptions') or [])
    if not schemes or cost is None or _number(cost) <= 0:
        return {**result, 'baseline_partial_contribution_rub': None, 'target_partial_contribution_rub': None,
                'required_unit_growth_pct': None, 'valid': False}
    for scheme, projection in schemes.items():
        deductions = projection.get('deductions') or {}
        c = _number(cost)
        commission, acquiring = _number(projection['commission_rate_pct']) / 100, _number(projection['acquiring_rate_pct']) / 100
        tax = _number(current['tax_rate_scenario'])
        logistics = _number(deductions['logistics_snapshot_max_rub'])
        # Missing services remain unknown; the subtotal is not labelled complete.
        retention = 1 - commission - acquiring - tax - (rate / 100 if known else 0)
        total_fixed = c + logistics + (fixed if known else 0)
        before = _number(item['before_price']) * retention - total_fixed
        target = _number(item['target_price']) * retention - total_fixed
        result['by_scheme'][scheme] = {'baseline_partial_contribution_rub': float(before.quantize(Decimal('.01'))),
                                      'target_partial_contribution_rub': float(target.quantize(Decimal('.01')))}
    baseline = min(x['baseline_partial_contribution_rub'] for x in result['by_scheme'].values())
    target = min(x['target_partial_contribution_rub'] for x in result['by_scheme'].values())
    growth = float((_number(baseline) / _number(target) - 1) * 100) if baseline > 0 and target > 0 else None
    result.update({'baseline_partial_contribution_rub': baseline, 'target_partial_contribution_rub': target,
                   'required_unit_growth_pct': round(growth, 4) if growth is not None else None,
                   'profit_on_cost_pct': round(target / float(cost) * 100, 4),
                   'margin_on_revenue_pct': round(target / item['target_price'] * 100, 4),
                   'valid': known or same_price_exception})
    return result


def evaluate_item(item, live_catalogue_row, service_evidence=None, *, now=None):
    """Pure per-SKU gate. Executor must independently perform all write safeguards."""
    row = live_catalogue_row
    now = _timestamp(now) if now is not None else datetime.now(timezone.utc)
    if now is None:
        raise ValueError('Timezone-aware current time required')
    blockers, warnings, ready, deferred = [], [], [], []
    target = _money(item['target_price'])
    if row.get('sku') != item.get('sku') or row.get('product_id') != item.get('product_id'):
        blockers.append('EXACT_IDENTITY_MISMATCH')
    try:
        live_price = _money(row.get('current_price'))
    except ValueError:
        live_price = None
        blockers.append('INVALID_SELLER_PRICE')
    if live_price not in (_money(item['before_price']), target):
        blockers.append('SELLER_PRICE_DRIFT')
    if not _stock_positive(row):
        blockers.append('NO_POSITIVE_OBSERVED_STOCK')
    actions = {a['action_id']: a for a in row.get('actions', [])}
    for aid in item['allowed_action_ids']:
        action = actions.get(aid)
        if not action:
            warnings.append('APPROVED_ACTION_NOT_CURRENTLY_LISTED:' + str(aid))
            continue
        start, end = _timestamp(action.get('date_start')), _timestamp(action.get('date_end'))
        if start is None or end is None or end < start:
            warnings.append('ACTION_DATES_UNVERIFIED:' + str(aid)); continue
        if end < now:
            warnings.append('APPROVED_ACTION_EXPIRED:' + str(aid)); continue
        cap = action.get('cap_rub')
        if cap is None or _number(cap) < _number(target):
            warnings.append('ACTION_CAP_BELOW_APPROVED_TARGET:' + str(aid)); continue
        if (action.get('eligible_for_price_comparison') is not True
                or action.get('additional_discount_or_unknown_type') or action.get('quarantined_or_failed')):
            warnings.append('ACTION_TERMS_REQUIRE_SEPARATE_REVIEW:' + str(aid)); continue
        if start > now:
            deferred.append(aid)
        else:
            ready.append(aid)
    try:
        financial = financial_comparison(item, row, service_evidence)
    except (ValueError, KeyError, TypeError, ArithmeticError):
        financial = {'valid': False, 'metric_kind': 'partial_contribution_not_net_profit', 'full_profit_verified': False}
        blockers.append('INVALID_FINANCIAL_INPUT')
    if not financial.get('valid'):
        blockers.append('SERVICE_OR_FINANCIAL_EVIDENCE_REQUIRED')
    value = financial.get('target_partial_contribution_rub')
    if value is None or value <= 0:
        blockers.append('NONPOSITIVE_OR_UNKNOWN_PARTIAL_CONTRIBUTION')
    growth = financial.get('required_unit_growth_pct')
    if item['strategy_code'] == 'C' and (growth is None or growth > 10):
        blockers.append('UPDATED_UNIT_GROWTH_EXCEEDS_APPROVED_PACKAGE_SCOPE')
    if item['strategy_code'] == 'A' and (growth is None or growth > 0):
        blockers.append('RAISE_DOES_NOT_PRESERVE_PARTIAL_UNIT_ECONOMICS')
    if not ready and not deferred:
        blockers.append('NO_ELIGIBLE_APPROVED_ACTION')
    if blockers:
        status = 'HOLD'
    elif not ready:
        status = 'DEFERRED'
    else:
        status = 'PARTIAL' if deferred or warnings else 'READY'
    allowed = not blockers and bool(ready)
    return {'sku': item['sku'], 'product_id': item['product_id'], 'status': status,
            'price_write_allowed': allowed and live_price != target,
            'ready_action_ids': ready if allowed else [], 'deferred_action_ids': deferred,
            'blockers': sorted(set(blockers)), 'warnings': sorted(set(warnings)),
            'financial_check': financial, 'target_price': target, 'experiment_floor_rub': target,
            'executor_safeguards_still_required': True}


def evaluate_plan(plan, live_catalogue, service_evidence=None, *, now=None):
    """Evaluate exact approved items; never add another newly attractive SKU."""
    validate_approved_plan(plan)
    by_sku = {}
    for row in live_catalogue.get('rows', []):
        sku = row.get('sku')
        if sku in by_sku:
            raise ValueError('Duplicate live exact SKU')
        by_sku[sku] = row
    supplied = service_evidence or {}
    evidence_by_sku = supplied.get('evidence_by_sku', supplied)
    results = []
    for item in plan['items']:
        row = by_sku.get(item['sku'])
        if row is None:
            result = {'sku': item['sku'], 'product_id': item['product_id'], 'status': 'HOLD',
                      'price_write_allowed': False, 'ready_action_ids': [], 'deferred_action_ids': [],
                      'blockers': ['APPROVED_SKU_NOT_IN_FRESH_CATALOGUE']}
        else:
            result = evaluate_item(item, row, evidence_by_sku.get(item['sku']), now=now)
        results.append(result)
    return {'schema_version': 1, 'decision_id': plan['decision_id'], 'scope_sha256': plan['scope_sha256'],
            'snapshot_at': live_catalogue.get('snapshot_at'), 'read_only': True,
            'execution_safeguards_still_required': True, 'count': len(results), 'items': results,
            'summary': {'statuses': dict(Counter(r['status'] for r in results)),
                        'price_write_candidates': sum(r['price_write_allowed'] for r in results),
                        'action_candidate_skus': sum(bool(r['ready_action_ids']) for r in results),
                        'blockers': dict(Counter(b for r in results for b in r['blockers']))}}


def main():
    import argparse
    parser = argparse.ArgumentParser(description='Read-only exact package financial preflight')
    parser.add_argument('--plan', default=str(DEFAULT_PLAN))
    parser.add_argument('--catalogue', required=True)
    parser.add_argument('--service-evidence')
    parser.add_argument('--as-of', required=True, help='Timezone-aware timestamp for promotion lifecycle')
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    catalogue = json.loads(Path(args.catalogue).read_text(encoding='utf-8'))
    evidence = json.loads(Path(args.service_evidence).read_text(encoding='utf-8')) if args.service_evidence else None
    result = evaluate_plan(load_approved_plan(args.plan), catalogue, evidence, now=args.as_of)
    destination = Path(args.output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(result['summary'], ensure_ascii=False))


if __name__ == '__main__':
    main()
