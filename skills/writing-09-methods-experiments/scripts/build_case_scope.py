"""Keep an auditable record when a case limits the scope of a manuscript revision."""

import re

from review_evidence import normalized


ELEMENTS = {'steps', 'setup', 'datasets', 'baselines', 'parameters', 'limitations'}
SCOPE = re.compile(r'\bonly\b|\bsolely\b|\bexclusively\b|limited to|restrict|out of scope|not applicable|'
                   r'does not (?:apply|require)|no (?:requirement|need|random|software)|'
                   r'not (?:required|needed|used)|without (?:random|software)|focus (?:on|solely)|'
                   r'仅|只需|只改|只写|只涉及|限于|聚焦|不适用|无需|无须|不要求|不涉及|未使用|未采用', re.I)


def scope_text(source, locator, case, inputs, evidence):
    if source == 'case.json' and locator.startswith('/'):
        node = case
        try:
            for key in locator[1:].split('/'):
                key = key.replace('~1', '/').replace('~0', '~')
                node = node[int(key)] if isinstance(node, list) and key.isdigit() else node[key]
        except (KeyError, IndexError, TypeError):
            return []
        if isinstance(node, str):
            return [node]
    if source == 'task.md':
        span = re.fullmatch(r'L([1-9]\d*)(?:-L?([1-9]\d*))?', locator)
        if span and (inputs / source).is_file():
            try:
                lines = (inputs / source).read_text(encoding='utf-8-sig').splitlines()
            except (OSError, UnicodeError):
                return []
            first, last = int(span[1]), int(span[2] or span[1])
            if first <= last <= len(lines):
                return [' '.join(lines[first-1:last])]
    return [entry['text'] for entry in evidence if entry['source'] == source and entry['locator'] == locator]


def apply_scope(facts, review, case, inputs, issues):
    requested = review.get('scope_exclusions', [])
    allowed = ELEMENTS | set(facts['missing_repro_fields']) | {
        'software', 'hardware', 'seeds', 'repeats', 'command', 'dataset_version', 'code_version', 'data_access'}
    exclusions = []
    if not isinstance(requested, list):
        issues.append({'code': 'invalid_scope_exclusions'})
        requested = []
    for idx, entry in enumerate(requested):
        if not isinstance(entry, dict) or not all(isinstance(entry.get(k), str) and entry[k].strip()
                                                 for k in ('field','source','locator','quote','reason')):
            issues.append({'code': 'invalid_scope_exclusion', 'row': idx})
            continue
        quote = normalized(entry['quote'])
        references = scope_text(entry['source'], entry['locator'], case, inputs, facts.get('evidence', []))
        if entry['field'] not in allowed or not SCOPE.search(quote) or not any(quote in normalized(t) for t in references):
            issues.append({'code': 'unsupported_scope_exclusion', 'row': idx, 'field': entry['field']})
            continue
        exclusions.append({k: entry[k] for k in ('field','source','locator','quote','reason')})
    fields = {e['field'] for e in exclusions}
    # An entire data section outside scope also removes its dataset-version question.
    inactive = fields | ({'dataset_version'} if 'datasets' in fields else set())
    facts['scope_exclusions'] = exclusions
    facts['out_of_scope_findings'] = [q for q in facts['author_questions'] if q['field'] in inactive]
    facts['author_questions'] = [q for q in facts['author_questions'] if q['field'] not in inactive]
    facts['missing_repro_fields'] = [field for field in facts['missing_repro_fields'] if field not in inactive]
    return fields
