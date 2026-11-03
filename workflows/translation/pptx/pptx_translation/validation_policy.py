"""Versioned deterministic text policy; numeric observations are not verdicts.

Policy 2 is frozen for historical request/validation replay. Policy 3 changes only
blanket digit equality to advisory evidence. Policy 4 allows ordinary ASCII spaces
in text spans; explicit structural items and significant Unicode remain protected.
"""
import re
import unicodedata

LEGACY = 2
CURRENT = 4
SUPPORTED = (LEGACY, 3, CURRENT)


def require_policy(version):
    if type(version) is not int or version not in SUPPORTED:
        raise ValueError('Unsupported translation validation policy.')
    return version


def payload_policy(payload, default=CURRENT):
    metadata=payload.get('metadata',{})
    if 'validation_policy_version' in metadata:
        return require_policy(metadata['validation_policy_version'])
    # Existing fallback records can use response contract 1; both historical
    # response contracts predate policy-3 advisories and keep the legacy rules.
    contract=metadata.get('response_contract_version')
    if type(contract) is int and contract in (1,2):return LEGACY
    return require_policy(default if contract is None else contract)


def text_error(source, translated, *, allow_structure=False, policy_version=CURRENT, check_tokens=True):
    require_policy(policy_version)
    if not isinstance(translated,str) or not translated.strip():return 'missing_or_blank_text'
    forbidden=(r'[\x00-\x08\x0a\x0c-\x1f\ud800-\udfff\ufffe\uffff]' if allow_structure
               else r'[\x00-\x1f\ud800-\udfff\ufffe\uffff]')
    if re.search(forbidden,translated):return 'invalid_control_character'
    if policy_version<4:
        if any(re.search(p,source).group()!=re.search(p,translated).group() for p in (r'^\s*',r'\s*$')):
            return 'boundary_whitespace_changed'
    else:
        # Ordinary spaces are linguistic; explicit structural spans are copied
        # locally. Other whitespace and format/directional characters stay locked.
        def significant(text):
            return [c for c in text if (c.isspace() and c!=' ') or unicodedata.category(c)=='Cf']
        def edge(text):
            prefix=[]
            for c in text:
                if not c.isspace() and unicodedata.category(c)!='Cf':break
                if c!=' ':prefix.append(c)
            return prefix
        if (significant(source)!=significant(translated) or edge(source)!=edge(translated)
                or edge(source[::-1])!=edge(translated[::-1])):return 'significant_unicode_changed'
    if policy_version==LEGACY and re.findall(r'\d+',source)!=re.findall(r'\d+',translated):
        return 'numeric_markers_changed'
    if check_tokens and re.findall(r'\v|\t| {2,}',source)!=re.findall(r'\v|\t| {2,}',translated):
        return 'structural_tokens_changed'
    return None


def numeric_advisories(unit, result, *, policy_version=3):
    """Reproducible observations bound by the caller to the text/span digest.

    No normalization or language-specific interpretation changes candidate text.
    Per-span evidence avoids duplicating the same observation at paragraph level.
    """
    warnings=[]
    def observe(source,translated,span_id=None):
        before,after=re.findall(r'\d+',source),re.findall(r'\d+',translated)
        if before!=after:
            item=dict(code='numeric_markers_changed',policy_version=policy_version,unit_key=unit['unit_key'],
                      source_numbers=before,translated_numbers=after)
            if span_id is not None:item['span_id']=span_id
            warnings.append(item)
    if 'styled_spans' in unit:
        answers={s['span_id']:s['text'] for s in result['primary_span_translations']}
        for span in unit['styled_spans']:
            if span['kind']=='text':observe(span['text'],answers[span['span_id']],span['span_id'])
    if not warnings:observe(unit['source_text'],result['primary_translation'])
    return warnings
