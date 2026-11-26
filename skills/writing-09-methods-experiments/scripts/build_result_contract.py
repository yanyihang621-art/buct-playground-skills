"""Bind a case's declared output fields to actual values and check their declared shape."""

import re
from pathlib import Path


def contract_fields(contract):
    if isinstance(contract, list):
        fields = {}
        for item in contract:
            if isinstance(item, str):
                fields[item] = {}
            elif isinstance(item, dict):
                key = item.get('name') or item.get('field') or item.get('key')
                if isinstance(key, str):
                    fields[key] = item
        return fields
    if not isinstance(contract, dict):
        return {}
    if any(key in contract for key in ('properties', 'fields', 'required', 'required_fields')):
        props = contract.get('properties', contract.get('fields', {}))
        fields = dict(props) if isinstance(props, dict) else contract_fields(props)
        for key in contract_fields(contract.get('required', [])) | contract_fields(contract.get('required_fields', [])):
            fields.setdefault(key, {})
        return fields
    return {k: v for k, v in contract.items() if k not in ('type', '$schema', 'additionalProperties', 'description')}


def artifact_ref(root, value):
    if not isinstance(value, str) or not value.strip():
        raise ValueError('expected a file path under the output directory')
    path = (root / value).resolve()
    if not path.is_relative_to(root.resolve()) or not path.is_file() or not path.stat().st_size:
        raise ValueError('file missing, empty or outside output')
    if path == (root / 'result.json').resolve():
        raise ValueError('the result manifest cannot reference itself as a deliverable')
    return path.relative_to(root.resolve()).as_posix()


_TYPES = {'string': str, 'array': list, 'object': dict, 'integer': int, 'number': (float, int),
          'boolean': bool, 'null': type(None)}


def _check_shape(value, spec, path, issues):
    if isinstance(spec, str):
        spec = {'type': spec} if spec in _TYPES else {}
    if not isinstance(spec, dict):
        return
    kinds = spec.get('type', [])
    kinds = [kinds] if isinstance(kinds, str) else kinds
    accepted = [k for k in kinds if isinstance(k, str) and k in _TYPES] if isinstance(kinds, list) else []
    if accepted and not any(isinstance(value, _TYPES[k]) and not (k in ('integer', 'number') and isinstance(value, bool)) for k in accepted):
        issues.append({'code': 'contract_type_mismatch', 'field': path, 'expected': accepted})
        return
    if isinstance(spec.get('enum'), list) and value not in spec['enum']:
        issues.append({'code': 'contract_enum_mismatch', 'field': path, 'expected': spec['enum']})
    if isinstance(value, dict):
        required = spec.get('required', [])
        for key in required if isinstance(required, list) else []:
            if key not in value:
                issues.append({'code': 'missing_contract_field', 'field': f'{path}.{key}'})
        properties = spec.get('properties', {})
        for key, prop in properties.items() if isinstance(properties, dict) else []:
            if key in value:
                _check_shape(value[key], prop, f'{path}.{key}', issues)
        if spec.get('additionalProperties') is False and isinstance(properties, dict):
            for key in value.keys() - properties.keys():
                issues.append({'code': 'unexpected_contract_field', 'field': f'{path}.{key}'})
    if isinstance(value, list) and 'items' in spec:
        for i, item in enumerate(value):
            _check_shape(item, spec['items'], f'{path}[{i}]', issues)


def _validated_paths(value, key, out_dir, issues):
    if isinstance(value, list):
        return [_validated_paths(item, key.removesuffix('s'), out_dir, issues) for item in value]
    if isinstance(value, dict):
        return {k: _validated_paths(v, k, out_dir, issues) for k, v in value.items()}
    is_source = re.search(r'(?:^|_)(?:source|input|original|dataset|evidence)(?:_|$)', key, re.I)
    if isinstance(value, str) and not is_source and re.search(r'(?:^|_)(?:file|path|pdf|docx|png)$', key, re.I):
        try:
            return artifact_ref(out_dir, value)
        except (ValueError, OSError) as exc:
            issues.append({'code': 'invalid_contract_artifact', 'field': key, 'detail': str(exc)})
            return None
    return value


def _resolve_references(value, catalog, field, issues):
    if isinstance(value, list):
        return [_resolve_references(item, catalog, f'{field}[{i}]', issues) for i, item in enumerate(value)]
    if not isinstance(value, dict):
        return value
    if '$from' in value:
        name = value['$from']
        if len(value) == 1 and isinstance(name, str) and name in catalog and catalog[name] is not None:
            return catalog[name]
        issues.append({'code': 'invalid_contract_value_reference', 'field': field,
                       'detail': 'Expected only $from with the name of an available generated value.'})
        return None
    return {k: _resolve_references(v, catalog, f'{field}.{k}', issues) for k, v in value.items()}


def project_generated(value, spec):
    """Select fields from a richer generated record when the case requires a closed object."""
    if not isinstance(spec, dict):
        return value
    if isinstance(value, list):
        return [project_generated(item, spec.get('items')) for item in value]
    if isinstance(value, dict):
        props = spec.get('properties', {})
        if isinstance(props, dict):
            return {key: project_generated(val, props.get(key)) for key, val in value.items()
                    if spec.get('additionalProperties') is not False or key in props}
    return value


def bind_contract(result, contract, provided, catalog, out_dir):
    """Never use schema descriptions or example/default values as answers."""
    issues = []
    fields = contract_fields(contract)
    if isinstance(contract, dict) and 'required' in contract:
        required = contract['required']
        if not isinstance(required, list) or any(not contract_fields([item]) for item in required):
            issues.append({'code': 'invalid_contract_required', 'expected': 'array of field names'})
    if contract and not fields:
        issues.append({'code': 'unrecognized_output_contract', 'detail': 'Inspect the case and bind its requested outputs manually.'})
    for key, spec in fields.items():
        if key in result:
            value = project_generated(result[key], spec)
        elif key in provided:
            value = _resolve_references(provided[key], catalog, key, issues)
            if isinstance(provided[key], dict) and set(provided[key]) == {'$from'}:
                value = project_generated(value, spec)
        elif key in catalog and catalog[key] is not None:
            value = project_generated(catalog[key], spec)
        else:
            result[key] = None
            issues.append({'code': 'unfilled_contract_field', 'field': key})
            continue
        # Built-in source/change records describe inputs. Validate case-supplied
        # deliverable paths (including nested ones) and named output aliases.
        if key not in result:
            value = _validated_paths(value, key, out_dir, issues)
        result[key] = value
        _check_shape(value, spec, key, issues)
    return issues
