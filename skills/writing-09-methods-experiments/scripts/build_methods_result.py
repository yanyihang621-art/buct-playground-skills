#!/usr/bin/env python3
"""Publish a case-authored experimental chapter and its verifiable outputs."""

import argparse
import difflib
import json
from pathlib import Path

from build_case_scope import apply_scope
from build_office_artifacts import render_office
from build_result_contract import artifact_ref, bind_contract
from coverage_evidence import classify_coverage
from draft_methods_section import render_methods_chapter, render_parameter_table_md, render_repro_and_questions_md
from exp_utils import write_json_atomic, write_text_lf
from extract_experiment_facts import extract_experiment_facts
from source_labels import expand_labels
from review_evidence import apply_review
from verify_methods_exp import verify_methods_and_experiments


def load_object(filename, issues, code):
    if filename is None:
        return {}
    try:
        obj = json.loads(Path(filename).read_text(encoding='utf-8-sig'))
        if not isinstance(obj, dict):
            raise ValueError('expected a JSON object')
        return obj
    except (OSError, ValueError) as exc:
        issues.append({'code': code, 'detail': str(exc)})
        return {}


def read_draft(filename, issues):
    if filename is None:
        return ''
    try:
        draft = Path(filename).read_text(encoding='utf-8-sig')
        if not draft.strip():
            raise ValueError('draft is empty')
        return draft
    except (OSError, ValueError) as exc:
        issues.append({'code': 'draft_unavailable', 'detail': str(exc)})
        return ''


def register_attachments(output, requested, artifacts, issues):
    if not isinstance(requested, dict):
        issues.append({'code': 'invalid_review_artifacts', 'detail': 'Expected an object of names and paths.'})
        return
    for name, path in requested.items():
        try:
            ref = artifact_ref(output, path)
            if name in artifacts and artifacts[name] != ref:
                raise ValueError('name already assigned to a pipeline output')
            artifacts[name] = ref
        except (ValueError, OSError) as exc:
            issues.append({'code': 'invalid_review_artifact', 'field': name, 'detail': str(exc)})


def diff_manuscript(original_file, input_dir, chapter, output, artifacts, changes, issues):
    if original_file is None:
        return
    try:
        path = (input_dir / original_file).resolve()
        ref = path.relative_to(input_dir.resolve()).as_posix()
        old = path.read_text(encoding='utf-8-sig')
        diff = list(difflib.unified_diff(old.splitlines(), chapter.splitlines(),
                                         fromfile=ref, tofile='methods_and_experiments.md', lineterm=''))
        if diff:
            write_text_lf(output / 'manuscript.diff', '\n'.join(diff) + '\n')
            artifacts['manuscript_diff'] = 'manuscript.diff'
        changes.append({'action': 'compared_original_manuscript', 'source': ref, 'changed': bool(diff)})
    except (ValueError, OSError) as exc:
        issues.append({'code': 'original_manuscript_unavailable', 'detail': str(exc)})


def review_list(review, field, issues):
    entries = review.get(field, [])
    if not isinstance(entries, list):
        issues.append({'code': 'invalid_review_list', 'field': field})
        return []
    return entries


def run_methods_pipeline(input_dir, work_dir, output_dir, *, case_path=None, draft_file=None,
                         language=None, review_file=None, contract_values=None,
                         original_file=None, docx=None, pdf=None):
    inputs, work, output = Path(input_dir), Path(work_dir) / 'methods', Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    issues = []
    case = load_object(case_path or inputs / 'case.json', issues, 'case_unavailable')
    case_id = case.get('case_id')
    if case_id is None:
        issues.append({'code': 'missing_case_id'})
    lang = str(language or case.get('language') or 'en')
    review = load_object(review_file, issues, 'review_unavailable')
    values = load_object(contract_values, issues, 'contract_values_unavailable')
    extracted = extract_experiment_facts(inputs, case)
    draft = read_draft(draft_file, issues)
    draft, inline_links, label_issues = expand_labels(draft, extracted['facts'].get('evidence', []))
    issues.extend(label_issues)
    if inline_links and isinstance(review.get('claim_sources', []), list):
        review['claim_sources'] = review.get('claim_sources', []) + inline_links
    facts, table, claims, review_issues = apply_review(extracted['facts'], extracted['parameter_table'], review, draft)
    issues.extend(review_issues)
    out_of_scope = apply_scope(facts, review, case, inputs, issues)
    coverage, coverage_issues = classify_coverage(draft, claims, review.get('coverage_links', {}), out_of_scope)
    facts['coverage_evidence'] = coverage
    issues.extend(coverage_issues)
    sources = facts.get('scanned_sources', [])
    artifacts = {}
    changes = review_list(review, 'change_records', issues).copy()
    issues.extend(review_list(review, 'uncertainties', issues))
    questions = facts['author_questions'] + review_list(review, 'author_questions', issues)
    gaps = list(facts.get('missing_repro_fields', []))
    conflicts = facts.get('conflicts', [])
    if gaps:
        issues.append({'code': 'missing_reproducibility_fields', 'fields': gaps})
    issues.extend(conflicts)
    for item in facts.get('ingestion_issues', []):
        issues.append({'code': 'source_read_limit', 'detail': item})

    chapter = ''
    repro = ''
    qa = {'passed': False, 'checks': {'experimental_materials': False}, 'violations': [],
          'metrics': {'parameter_count': 0, 'parameter_prose_consistency': None}}
    if sources:
        if not draft:
            issues.append({'code': 'source_outline_only', 'detail': 'An author-written case-specific manuscript is still required.'})
        elif not claims:
            issues.append({'code': 'missing_claim_sources', 'detail': 'Attach source-checked claim records for the manuscript.'})
        chapter = draft or render_methods_chapter(facts, table, language=lang)
        is_zh = lang.lower().startswith('zh')
        repro = render_repro_and_questions_md(dict(facts, author_questions=questions), is_zh=is_zh)
        table_md = render_parameter_table_md(table, is_zh=is_zh)
        for key, filename, content in [('methods_and_experiments_md', 'methods_and_experiments.md', chapter),
                                       ('parameter_table_md', 'parameter_table.md', table_md),
                                       ('reproducibility_and_questions_md', 'reproducibility_and_questions.md', repro)]:
            write_text_lf(output / filename, content)
            artifacts[key] = filename
        generated, render_issues = render_office(chapter, output, docx, pdf)
        artifacts.update(generated)
        issues.extend(render_issues)
        changes.append({'action': 'packaged_authored_chapter' if draft else 'generated_evidence_outline',
                        'artifact': artifacts['methods_and_experiments_md'], 'verified_source_links': len(claims)})
        if draft:
            diff_manuscript(original_file, inputs, chapter, output, artifacts, changes, issues)
        missing = [key for key in ('steps', 'datasets', 'baselines', 'limitations')
                   if key not in out_of_scope and not facts.get(key) and not coverage.get(key)]
        if missing:
            issues.append({'code': 'missing_experiment_elements', 'fields': missing})
        qa = verify_methods_and_experiments(chapter, repro, facts, table,
                                             status_claimed='partial' if issues else 'completed', language=lang)
        qa['linked_topic_counts'] = {topic: len(rows) for topic, rows in coverage.items()}
        if coverage_issues:
            qa['checks']['source_linked_coverage'] = False
            qa['violations'].extend({'check': 'source_linked_coverage', **item} for item in coverage_issues)
            qa['passed'] = False
        if not qa['passed']:
            issues.append({'code': 'qa_violations', 'items': qa['violations']})
    else:
        issues.append({'code': 'empty_experimental_inputs', 'detail': 'No readable scientific materials were indexed.'})

    for key, filename, obj in [('experiment_facts_json', 'experiment_facts.json', facts),
                               ('parameter_table_json', 'parameter_table.json', table),
                               ('qa_report_json', 'qa_report.json', qa),
                               ('claim_sources_json', 'claim_sources.json', claims)]:
        write_json_atomic(output / filename, obj)
        artifacts[key] = filename
        if key != 'claim_sources_json':
            write_json_atomic(work / filename, obj)
    register_attachments(output, review.get('artifacts', {}), artifacts, issues)
    status = 'refused' if not sources else 'partial' if issues else 'completed'
    result = {'schema_version': 'writing-09-methods-result/v2', 'challenge_id': 'product-arena-05-64faa4a2',
              'case_id': case_id, 'status': status, 'language': lang, 'summary': '',
              'metrics': qa['metrics'], 'artifacts': artifacts,
              'sources': [{'file': source} for source in sources], 'claim_sources': claims, 'change_records': changes,
              'scope_exclusions': facts['scope_exclusions'],
              'uncertainties': issues, 'author_questions': questions,
              'verification': {'passed': bool(qa['passed']), 'check_count': len(qa['checks']),
                               'failed_checks': [k for k, passed in qa['checks'].items() if not passed]}}
    catalog = dict(facts, **{'parameters': table['parameters'], 'parameter_table': table,
                            'manuscript': chapter, 'chapter': chapter, 'markdown': chapter,
                            'methods_and_experiments': chapter, 'main_result': chapter,
                            'evidence': claims, 'source_map': claims, 'reproducibility_checklist': repro,
                            'manuscript_path': artifacts.get('methods_and_experiments_md')})
    catalog.update(artifacts)
    contract = case.get('output_contract')
    contract_issues = bind_contract(result, contract, values, catalog, output)
    if contract_issues:
        issues.extend(contract_issues)
        if status == 'completed':
            result['status'] = 'partial'
        result['verification']['passed'] = False
        result['verification']['failed_checks'].append('output_contract')
    result['verification']['contract_issues'] = contract_issues
    result['summary'] = (f"{result['status']}. {len(sources)} source file(s), {table['parameter_count']} parameter(s), "
                         f"{len(claims)} verified manuscript-source link(s), {len(questions)} author question(s).")
    if isinstance(contract, dict) and contract.get('additionalProperties') is False and isinstance(contract.get('properties'), dict):
        write_json_atomic(output / 'methods_diagnostics.json', result)
        delivered = {k: result[k] for k in dict.fromkeys(['case_id', *contract['properties'], *contract.get('required', [])]) if k in result}
    else:
        delivered = result
    write_json_atomic(output / 'result.json', delivered)
    return {'result': result, 'facts': facts, 'param_table': table, 'chapter_md': chapter,
            'repro_md': repro, 'qa_report': qa}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input-dir', default='/input')
    parser.add_argument('--work-dir', default='/app/work')
    parser.add_argument('--output-dir', default='/app/output')
    parser.add_argument('--case', dest='case_path')
    parser.add_argument('--draft-file')
    parser.add_argument('--language')
    parser.add_argument('--review-file', help='Claim links, authored changes, uncertainties, questions, and artifact paths.')
    parser.add_argument('--contract-values', help='JSON object containing case-specific answers keyed by output field.')
    parser.add_argument('--original-file', help='Original text manuscript inside input-dir for a real revision diff.')
    parser.add_argument('--docx', nargs='?', const='methods_and_experiments.docx')
    parser.add_argument('--pdf', nargs='?', const='methods_and_experiments.pdf')
    result = run_methods_pipeline(**vars(parser.parse_args()))['result']
    print(json.dumps({'status': result['status'], 'summary': result['summary'],
                      'verification': result['verification']}, ensure_ascii=False))
    return 0 if result['status'] != 'refused' and result['verification']['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
