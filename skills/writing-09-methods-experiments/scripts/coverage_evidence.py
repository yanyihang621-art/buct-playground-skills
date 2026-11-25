"""Classify linked body claims without inventing structured experimental records."""

from bisect import bisect_right
from collections import defaultdict
import re

from review_evidence import normalized
from verify_methods_exp import GAP, HEAD, TOPICS


SENSE = {
    'steps': r'\b(?:filter|rinse|wash|mix|centrifug|extract|collect|transfer|weigh|normaliz|normali[sz]|fit|train|process|segment|align|remove|screen|apply|applie|pipett|incubat|heat|cool|record|stir|repeat|dilut|partition|calculat)\w*|过滤|混合|称取|加入|提取|洗涤|采集|归一化|训练|拟合|处理|去除|离心|计算|转移|制备',
    'datasets': r'\b(?:dataset|cohort|specimen|participant|subjects?|sample set|batch(?:es)?|split|fold|partition|corpus|population)\b|数据集|队列|受试者|标本|样本集|批次|划分|语料',
    'baselines': r'\b(?:baseline|control|placebo|comparator|blank|untreated|ablation|compared|versus)\b|对照|空白组|基线|消融|未经处理|对比|比较',
    'limitations': r'\b(?:limited|limitation|restricted|outside|excluded?|extrapolat\w*|unreliable|fails?|inapplicable|only|cannot|contains? no|did not (?:cover|include)|does not (?:cover|include)|interfer\w*)\b|仅适用|局限|限于|无法|未覆盖|不可|失效|干扰|不适用|不包括',
    'setup': TOPICS['setup'],
    'parameters': TOPICS['parameters'] + r'|\b\w+\s*=\s*[+-]?\d',
}


def manuscript_locations(text):
    offsets, rows, offset, heading, fence = [], [], 0, '', ''
    for line in text.splitlines():
        line = normalized(line)
        delimiter = re.match(r'^(`{3,}|~{3,})', line)
        is_code = bool(fence) or bool(delimiter)
        if delimiter:
            mark = delimiter[1]
            if not fence:
                fence = mark
            elif mark[0] == fence[0] and len(mark) >= len(fence):
                fence = ''
        match = HEAD.match(line) if not is_code else None
        if match:
            heading = match[1]
        offsets.append(offset)
        rows.append((line, heading, not match and not is_code))
        offset += len(line) + 1
    return ' '.join(row[0] for row in rows), offsets, rows


def linked_body(link, locations):
    manuscript, offsets, rows = locations
    claim = normalized(link['claim'])
    lineno = link.get('line')
    start = offsets[lineno-1] if lineno else 0
    text = rows[lineno-1][0] if lineno else manuscript
    hit = None
    for i, match in enumerate(re.finditer(re.escape(claim), text), 1):
        if i == link.get('occurrence', 1):
            hit = match
            break
    if not hit:
        return None
    first = bisect_right(offsets, start + hit.start()) - 1
    last = bisect_right(offsets, start + hit.end()-1) - 1
    if any(not body for _, _, body in rows[first:last+1]):
        return None
    return first + 1, rows[first][1]


def substantive(claim):
    # Standalone labels, bare measurements and generic gap notices cannot supply a topic.
    return (len(re.findall(r'[A-Za-z][\w-]*', claim)) >= 3 or
            len(re.findall(r'[\u3400-\u9fff]', claim)) >= 6) and not re.search(GAP, claim, re.I)


def classify_coverage(manuscript, valid, requested, excluded):
    evidence, errors = defaultdict(list), []
    by_claim = defaultdict(list)
    locations = manuscript_locations(manuscript)
    for index, link in enumerate(valid):
        body = linked_body(link, locations)
        if body and substantive(link['claim']):
            by_claim[normalized(link['claim'])].append((index, link, *body))
    explicit = {}
    if not isinstance(requested, dict):
        errors.append({'code': 'invalid_coverage_links', 'expected': 'object of topic to list of claims'})
        requested = {}
    for topic, items in requested.items():
        if topic not in SENSE or topic in excluded or not isinstance(items, list) or not items:
            errors.append({'code': 'invalid_coverage_topic', 'topic': topic})
            continue
        explicit[topic] = set()
        for index, item in enumerate(items):
            claim = normalized(item) if isinstance(item, str) else ''
            if claim not in by_claim:
                errors.append({'code': 'unverified_coverage_claim', 'topic': topic, 'row': index})
            elif claim in explicit[topic]:
                errors.append({'code': 'duplicate_coverage_claim', 'topic': topic, 'row': index})
            else:
                explicit[topic].add(claim)
    for topic, cues in SENSE.items():
        if topic in excluded:
            continue
        for claim in explicit.get(topic, by_claim):
            links = by_claim[claim]
            has_cue = re.search(cues, claim, re.I)
            topic_re = TOPICS['limits' if topic == 'limitations' else topic]
            matches = []
            for index, link, line, heading in links:
                quote = normalized(link['quote'])
                source_cue = re.search(cues, quote, re.I)
                heading_cue = re.search(topic_re, heading, re.I)
                # A topic word in a heading alone is insufficient. A concise
                # translated claim may use the section title plus a source-topic cue.
                if (has_cue and (source_cue or claim.casefold() in quote.casefold())) or (heading_cue and source_cue):
                    matches.append({'claim_index': index, 'line': line, 'source': link['source'], 'locator': link['locator']})
            if matches:
                evidence[topic].append({'claim': claim, 'mode': 'explicit' if topic in explicit else 'auto', 'links': matches})
            elif topic in explicit:
                errors.append({'code': 'coverage_topic_mismatch', 'topic': topic, 'claim': claim})
    return dict(evidence), errors
