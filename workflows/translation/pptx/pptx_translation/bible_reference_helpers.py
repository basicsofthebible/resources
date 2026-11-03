"""Language-agnostic Bible book metadata and reference detection helpers.

The helper is built around canonical 66-book workflow codes plus per-language
book-name maps. Add future languages by inserting another entry in
``LANGUAGE_BOOK_MAPS`` using the same canonical book codes.
"""

from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple, Union

LanguageCodeInput = Union[str, Iterable[str], None]

CANONICAL_BOOKS = {'GEN': {'order': 1, 'osis': 'Gen'},
 'EXO': {'order': 2, 'osis': 'Exod'},
 'LEV': {'order': 3, 'osis': 'Lev'},
 'NUM': {'order': 4, 'osis': 'Num'},
 'DEU': {'order': 5, 'osis': 'Deut'},
 'JOS': {'order': 6, 'osis': 'Josh'},
 'JDG': {'order': 7, 'osis': 'Judg'},
 'RUT': {'order': 8, 'osis': 'Ruth'},
 '1SA': {'order': 9, 'osis': '1Sam'},
 '2SA': {'order': 10, 'osis': '2Sam'},
 '1KI': {'order': 11, 'osis': '1Kgs'},
 '2KI': {'order': 12, 'osis': '2Kgs'},
 '1CH': {'order': 13, 'osis': '1Chr'},
 '2CH': {'order': 14, 'osis': '2Chr'},
 'EZR': {'order': 15, 'osis': 'Ezra'},
 'NEH': {'order': 16, 'osis': 'Neh'},
 'EST': {'order': 17, 'osis': 'Esth'},
 'JOB': {'order': 18, 'osis': 'Job'},
 'PSA': {'order': 19, 'osis': 'Ps'},
 'PRO': {'order': 20, 'osis': 'Prov'},
 'ECC': {'order': 21, 'osis': 'Eccl'},
 'SNG': {'order': 22, 'osis': 'Song'},
 'ISA': {'order': 23, 'osis': 'Isa'},
 'JER': {'order': 24, 'osis': 'Jer'},
 'LAM': {'order': 25, 'osis': 'Lam'},
 'EZK': {'order': 26, 'osis': 'Ezek'},
 'DAN': {'order': 27, 'osis': 'Dan'},
 'HOS': {'order': 28, 'osis': 'Hos'},
 'JOL': {'order': 29, 'osis': 'Joel'},
 'AMO': {'order': 30, 'osis': 'Amos'},
 'OBA': {'order': 31, 'osis': 'Obad'},
 'JON': {'order': 32, 'osis': 'Jonah'},
 'MIC': {'order': 33, 'osis': 'Mic'},
 'NAM': {'order': 34, 'osis': 'Nah'},
 'HAB': {'order': 35, 'osis': 'Hab'},
 'ZEP': {'order': 36, 'osis': 'Zeph'},
 'HAG': {'order': 37, 'osis': 'Hag'},
 'ZEC': {'order': 38, 'osis': 'Zech'},
 'MAL': {'order': 39, 'osis': 'Mal'},
 'MAT': {'order': 40, 'osis': 'Matt'},
 'MRK': {'order': 41, 'osis': 'Mark'},
 'LUK': {'order': 42, 'osis': 'Luke'},
 'JHN': {'order': 43, 'osis': 'John'},
 'ACT': {'order': 44, 'osis': 'Acts'},
 'ROM': {'order': 45, 'osis': 'Rom'},
 '1CO': {'order': 46, 'osis': '1Cor'},
 '2CO': {'order': 47, 'osis': '2Cor'},
 'GAL': {'order': 48, 'osis': 'Gal'},
 'EPH': {'order': 49, 'osis': 'Eph'},
 'PHP': {'order': 50, 'osis': 'Phil'},
 'COL': {'order': 51, 'osis': 'Col'},
 '1TH': {'order': 52, 'osis': '1Thess'},
 '2TH': {'order': 53, 'osis': '2Thess'},
 '1TI': {'order': 54, 'osis': '1Tim'},
 '2TI': {'order': 55, 'osis': '2Tim'},
 'TIT': {'order': 56, 'osis': 'Titus'},
 'PHM': {'order': 57, 'osis': 'Phlm'},
 'HEB': {'order': 58, 'osis': 'Heb'},
 'JAS': {'order': 59, 'osis': 'Jas'},
 '1PE': {'order': 60, 'osis': '1Pet'},
 '2PE': {'order': 61, 'osis': '2Pet'},
 '1JN': {'order': 62, 'osis': '1John'},
 '2JN': {'order': 63, 'osis': '2John'},
 '3JN': {'order': 64, 'osis': '3John'},
 'JUD': {'order': 65, 'osis': 'Jude'},
 'REV': {'order': 66, 'osis': 'Rev'}}

LANGUAGE_ALIASES = {
    "en": "en",
    "eng": "en",
    "english": "en",
    "es": "es",
    "spa": "es",
    "spanish": "es",
    "zh": "zh-Hans",
    "zh-cn": "zh-Hans",
    "zh_hans": "zh-Hans",
    "zh-hans": "zh-Hans",
    "simplified chinese": "zh-Hans",
    "simplified_chinese": "zh-Hans",
    "chinese simplified": "zh-Hans",
}

DEFAULT_LANGUAGE_CODES = ("en", "es", "zh-Hans")

LANGUAGE_BOOK_MAPS = {'en': {'GEN': {'name': 'Genesis', 'aliases': ['Gen', 'Ge', 'Gn']},
        'EXO': {'name': 'Exodus', 'aliases': ['Exod', 'Ex', 'Exo']},
        'LEV': {'name': 'Leviticus', 'aliases': ['Lev', 'Lv']},
        'NUM': {'name': 'Numbers', 'aliases': ['Num', 'Nm']},
        'DEU': {'name': 'Deuteronomy', 'aliases': ['Deut', 'Dt']},
        'JOS': {'name': 'Joshua', 'aliases': ['Josh', 'Jos']},
        'JDG': {'name': 'Judges', 'aliases': ['Judg', 'Jdg', 'Jgs']},
        'RUT': {'name': 'Ruth', 'aliases': ['Ruth', 'Ru']},
        '1SA': {'name': '1 Samuel', 'aliases': ['1 Sam', '1Sm', '1Sa']},
        '2SA': {'name': '2 Samuel', 'aliases': ['2 Sam', '2Sm', '2Sa']},
        '1KI': {'name': '1 Kings', 'aliases': ['1 Kgs', '1 Kg', '1 Ki']},
        '2KI': {'name': '2 Kings', 'aliases': ['2 Kgs', '2 Kg', '2 Ki']},
        '1CH': {'name': '1 Chronicles', 'aliases': ['1 Chr', '1 Chron', '1Ch']},
        '2CH': {'name': '2 Chronicles', 'aliases': ['2 Chr', '2 Chron', '2Ch']},
        'EZR': {'name': 'Ezra', 'aliases': ['Ezra', 'Ezr']},
        'NEH': {'name': 'Nehemiah', 'aliases': ['Neh', 'Ne']},
        'EST': {'name': 'Esther', 'aliases': ['Esth', 'Est']},
        'JOB': {'name': 'Job', 'aliases': ['Job', 'Jb']},
        'PSA': {'name': 'Psalms', 'aliases': ['Ps', 'Pss']},
        'PRO': {'name': 'Proverbs', 'aliases': ['Prov', 'Prv', 'Pr']},
        'ECC': {'name': 'Ecclesiastes', 'aliases': ['Eccl', 'Eccles', 'Ec']},
        'SNG': {'name': 'Song of Songs', 'aliases': ['Song', 'SS', 'So', 'Sg', 'Cant']},
        'ISA': {'name': 'Isaiah', 'aliases': ['Isa', 'Is']},
        'JER': {'name': 'Jeremiah', 'aliases': ['Jer', 'Je']},
        'LAM': {'name': 'Lamentations', 'aliases': ['Lam', 'La']},
        'EZK': {'name': 'Ezekiel', 'aliases': ['Ezek', 'Ezk', 'Ez', 'Eze']},
        'DAN': {'name': 'Daniel', 'aliases': ['Dan', 'Dn']},
        'HOS': {'name': 'Hosea', 'aliases': ['Hos', 'Ho']},
        'JOL': {'name': 'Joel', 'aliases': ['Joel', 'Joe', 'Jl']},
        'AMO': {'name': 'Amos', 'aliases': ['Amos', 'Am']},
        'OBA': {'name': 'Obadiah', 'aliases': ['Obad', 'Ob']},
        'JON': {'name': 'Jonah', 'aliases': ['Jonah', 'Jon']},
        'MIC': {'name': 'Micah', 'aliases': ['Mic', 'Mi']},
        'NAM': {'name': 'Nahum', 'aliases': ['Nah', 'Na']},
        'HAB': {'name': 'Habakkuk', 'aliases': ['Hab', 'Hb']},
        'ZEP': {'name': 'Zephaniah', 'aliases': ['Zeph', 'Zep']},
        'HAG': {'name': 'Haggai', 'aliases': ['Hag', 'Hg']},
        'ZEC': {'name': 'Zechariah', 'aliases': ['Zech', 'Zec']},
        'MAL': {'name': 'Malachi', 'aliases': ['Mal', 'Ml']},
        'MAT': {'name': 'Matthew', 'aliases': ['Matt', 'Mat', 'Mt']},
        'MRK': {'name': 'Mark', 'aliases': ['Mark', 'Mar', 'Mk']},
        'LUK': {'name': 'Luke', 'aliases': ['Luke', 'Lk', 'Lu', 'Luk']},
        'JHN': {'name': 'John', 'aliases': ['John', 'Jn', 'Jo']},
        'ACT': {'name': 'Acts', 'aliases': ['Acts', 'Ac']},
        'ROM': {'name': 'Romans', 'aliases': ['Rom', 'Rm', 'Ro']},
        '1CO': {'name': '1 Corinthians', 'aliases': ['1 Cor', '1 Co', '1C', '1Co', '1Cor']},
        '2CO': {'name': '2 Corinthians', 'aliases': ['2 Cor', '2 Co', '2C', '2Co', '2Cor']},
        'GAL': {'name': 'Galatians', 'aliases': ['Gal', 'Ga']},
        'EPH': {'name': 'Ephesians', 'aliases': ['Eph', 'Ep']},
        'PHP': {'name': 'Philippians', 'aliases': ['Phil', 'Php']},
        'COL': {'name': 'Colossians', 'aliases': ['Col', 'Co']},
        '1TH': {'name': '1 Thessalonians', 'aliases': ['1 Thess', '1 Thes', '1Th', '1Thess']},
        '2TH': {'name': '2 Thessalonians', 'aliases': ['2 Thess', '2 Thes', '2Th', '2Thess']},
        '1TI': {'name': '1 Timothy', 'aliases': ['1 Tim', '1 Tm', '1 Ti', '1Ti', '1Tim']},
        '2TI': {'name': '2 Timothy', 'aliases': ['2 Tim', '2 Tm', '2 Ti', '2Ti', '2Tim']},
        'TIT': {'name': 'Titus', 'aliases': ['Titus', 'Tit', 'Ti']},
        'PHM': {'name': 'Philemon', 'aliases': ['Phlm', 'Philem', 'Phm']},
        'HEB': {'name': 'Hebrews', 'aliases': ['Heb', 'He']},
        'JAS': {'name': 'James', 'aliases': ['Jas', 'Ja']},
        '1PE': {'name': '1 Peter', 'aliases': ['1 Pet', '1 Pt', '1P', '1Pe', '1Pet']},
        '2PE': {'name': '2 Peter', 'aliases': ['2 Pet', '2 Pt', '2P', '2Pe', '2Pet']},
        '1JN': {'name': '1 John', 'aliases': ['1 John', '1 Jn', '1 Jo', '1J', '1Jn', '1Jo']},
        '2JN': {'name': '2 John', 'aliases': ['2 John', '2 Jn', '2 Jo', '2J', '2Jn', '2Jo']},
        '3JN': {'name': '3 John', 'aliases': ['3 John', '3 Jn', '3 Jo', '3J', '3Jn', '3Jo']},
        'JUD': {'name': 'Jude', 'aliases': ['Jude', 'Ju']},
        'REV': {'name': 'Revelation', 'aliases': ['Rev', 'Rv', 'Re', 'Ap']}},
 'es': {'GEN': {'name': 'Génesis', 'aliases': ['Gn', 'Gén', 'Gen']},
        'EXO': {'name': 'Éxodo', 'aliases': ['Ex', 'Éx']},
        'LEV': {'name': 'Levítico', 'aliases': ['Lev', 'Lv']},
        'NUM': {'name': 'Números', 'aliases': ['Nm', 'Núm', 'Num']},
        'DEU': {'name': 'Deuteronomio', 'aliases': ['Dt', 'Deut']},
        'JOS': {'name': 'Josué', 'aliases': ['Jos']},
        'JDG': {'name': 'Jueces', 'aliases': ['Jue', 'Jc']},
        'RUT': {'name': 'Rut', 'aliases': ['Rut', 'Rt']},
        '1SA': {'name': '1 Samuel', 'aliases': ['1 Sam', '1 Sm', '1 S', '1Sa']},
        '2SA': {'name': '2 Samuel', 'aliases': ['2 Sam', '2 Sm', '2 S', '2Sa']},
        '1KI': {'name': '1 Reyes', 'aliases': ['1 R', '1 Re', '1 Rey']},
        '2KI': {'name': '2 Reyes', 'aliases': ['2 R', '2 Re', '2 Rey']},
        '1CH': {'name': '1 Crónicas', 'aliases': ['1 Cr', '1 Cro', '1 Cró']},
        '2CH': {'name': '2 Crónicas', 'aliases': ['2 Cr', '2 Cro', '2 Cró']},
        'EZR': {'name': 'Esdras', 'aliases': ['Esd', 'Es']},
        'NEH': {'name': 'Nehemías', 'aliases': ['Neh', 'Ne']},
        'EST': {'name': 'Ester', 'aliases': ['Est']},
        'JOB': {'name': 'Job', 'aliases': ['Job', 'Jb']},
        'PSA': {'name': 'Salmos', 'aliases': ['Sal']},
        'PRO': {'name': 'Proverbios', 'aliases': ['Pr', 'Prov', 'Pro']},
        'ECC': {'name': 'Eclesiastés', 'aliases': ['Ec', 'Ecl']},
        'SNG': {'name': 'Cantares', 'aliases': ['Cnt', 'Ct', 'Cant']},
        'ISA': {'name': 'Isaías', 'aliases': ['Is', 'Isa']},
        'JER': {'name': 'Jeremías', 'aliases': ['Jer', 'Jr']},
        'LAM': {'name': 'Lamentaciones', 'aliases': ['Lam', 'Lm']},
        'EZK': {'name': 'Ezequiel', 'aliases': ['Ez', 'Ezeq']},
        'DAN': {'name': 'Daniel', 'aliases': ['Dan', 'Dn']},
        'HOS': {'name': 'Oseas', 'aliases': ['Os']},
        'JOL': {'name': 'Joel', 'aliases': ['Jl']},
        'AMO': {'name': 'Amós', 'aliases': ['Am']},
        'OBA': {'name': 'Abdías', 'aliases': ['Abd', 'Ab']},
        'JON': {'name': 'Jonás', 'aliases': ['Jon']},
        'MIC': {'name': 'Miqueas', 'aliases': ['Miq', 'Mi']},
        'NAM': {'name': 'Nahúm', 'aliases': ['Nah', 'Na']},
        'HAB': {'name': 'Habacuc', 'aliases': ['Hab', 'Ha']},
        'ZEP': {'name': 'Sofonías', 'aliases': ['Sof', 'So']},
        'HAG': {'name': 'Hageo', 'aliases': ['Ageo', 'Hag', 'Ag']},
        'ZEC': {'name': 'Zacarías', 'aliases': ['Zac', 'Za']},
        'MAL': {'name': 'Malaquías', 'aliases': ['Mal', 'Ml']},
        'MAT': {'name': 'Mateo', 'aliases': ['Mat', 'Mt']},
        'MRK': {'name': 'Marcos', 'aliases': ['Mar', 'Mc']},
        'LUK': {'name': 'Lucas', 'aliases': ['Lc', 'Luc']},
        'JHN': {'name': 'Juan', 'aliases': ['Jn']},
        'ACT': {'name': 'Hechos', 'aliases': ['Hch', 'Hech', 'He']},
        'ROM': {'name': 'Romanos', 'aliases': ['Rom', 'Rm', 'Ro']},
        '1CO': {'name': '1 Corintios', 'aliases': ['1 Cor', '1 Co']},
        '2CO': {'name': '2 Corintios', 'aliases': ['2 Cor', '2 Co']},
        'GAL': {'name': 'Gálatas', 'aliases': ['Gl', 'Ga', 'Gál', 'Gal']},
        'EPH': {'name': 'Efesios', 'aliases': ['Ef', 'Efes']},
        'PHP': {'name': 'Filipenses', 'aliases': ['Flp', 'Fil']},
        'COL': {'name': 'Colosenses', 'aliases': ['Col']},
        '1TH': {'name': '1 Tesalonicenses', 'aliases': ['1 Ts', '1 Tes']},
        '2TH': {'name': '2 Tesalonicenses', 'aliases': ['2 Ts', '2 Tes']},
        '1TI': {'name': '1 Timoteo', 'aliases': ['1 Tim', '1 Tm', '1 Ti']},
        '2TI': {'name': '2 Timoteo', 'aliases': ['2 Tim', '2 Tm', '2 Ti']},
        'TIT': {'name': 'Tito', 'aliases': ['Tit', 'Tt']},
        'PHM': {'name': 'Filemón', 'aliases': ['Flm', 'Filem']},
        'HEB': {'name': 'Hebreos', 'aliases': ['Heb', 'Hb']},
        'JAS': {'name': 'Santiago', 'aliases': ['Stg', 'Stgo', 'St', 'Sant']},
        '1PE': {'name': '1 Pedro', 'aliases': ['1 P', '1 Pe']},
        '2PE': {'name': '2 Pedro', 'aliases': ['2 P', '2 Pe']},
        '1JN': {'name': '1 Juan', 'aliases': ['1 Jn']},
        '2JN': {'name': '2 Juan', 'aliases': ['2 Jn']},
        '3JN': {'name': '3 Juan', 'aliases': ['3 Jn']},
        'JUD': {'name': 'Judas', 'aliases': ['Jud', 'Jd', 'Jds']},
        'REV': {'name': 'Apocalipsis', 'aliases': ['Ap', 'Apoc']}},
 'zh-Hans': {'GEN': {'name': '创世记', 'aliases': ['创']},
             'EXO': {'name': '出埃及记', 'aliases': ['出']},
             'LEV': {'name': '利未记', 'aliases': ['利']},
             'NUM': {'name': '民数记', 'aliases': ['民']},
             'DEU': {'name': '申命记', 'aliases': ['申']},
             'JOS': {'name': '约书亚记', 'aliases': ['书']},
             'JDG': {'name': '士师记', 'aliases': ['士']},
             'RUT': {'name': '路得记', 'aliases': ['得']},
             '1SA': {'name': '撒母耳记上', 'aliases': ['撒上', '撒母耳上']},
             '2SA': {'name': '撒母耳记下', 'aliases': ['撒下', '撒母耳下']},
             '1KI': {'name': '列王纪上', 'aliases': ['王上', '列王上']},
             '2KI': {'name': '列王纪下', 'aliases': ['王下', '列王下']},
             '1CH': {'name': '历代志上', 'aliases': ['代上', '历上']},
             '2CH': {'name': '历代志下', 'aliases': ['代下', '历下']},
             'EZR': {'name': '以斯拉记', 'aliases': ['拉']},
             'NEH': {'name': '尼希米记', 'aliases': ['尼']},
             'EST': {'name': '以斯帖记', 'aliases': ['斯']},
             'JOB': {'name': '约伯记', 'aliases': ['伯']},
             'PSA': {'name': '诗篇', 'aliases': ['诗']},
             'PRO': {'name': '箴言', 'aliases': ['箴']},
             'ECC': {'name': '传道书', 'aliases': ['传']},
             'SNG': {'name': '雅歌', 'aliases': ['歌']},
             'ISA': {'name': '以赛亚书', 'aliases': ['赛']},
             'JER': {'name': '耶利米书', 'aliases': ['耶']},
             'LAM': {'name': '耶利米哀歌', 'aliases': ['哀']},
             'EZK': {'name': '以西结书', 'aliases': ['结']},
             'DAN': {'name': '但以理书', 'aliases': ['但']},
             'HOS': {'name': '何西阿书', 'aliases': ['何']},
             'JOL': {'name': '约珥书', 'aliases': ['珥']},
             'AMO': {'name': '阿摩司书', 'aliases': ['摩']},
             'OBA': {'name': '俄巴底亚书', 'aliases': ['俄']},
             'JON': {'name': '约拿书', 'aliases': ['拿']},
             'MIC': {'name': '弥迦书', 'aliases': ['弥']},
             'NAM': {'name': '那鸿书', 'aliases': ['鸿']},
             'HAB': {'name': '哈巴谷书', 'aliases': ['哈']},
             'ZEP': {'name': '西番雅书', 'aliases': ['番']},
             'HAG': {'name': '哈该书', 'aliases': ['该']},
             'ZEC': {'name': '撒迦利亚书', 'aliases': ['亚']},
             'MAL': {'name': '玛拉基书', 'aliases': ['玛']},
             'MAT': {'name': '马太福音', 'aliases': ['太']},
             'MRK': {'name': '马可福音', 'aliases': ['可']},
             'LUK': {'name': '路加福音', 'aliases': ['路']},
             'JHN': {'name': '约翰福音', 'aliases': ['约']},
             'ACT': {'name': '使徒行传', 'aliases': ['徒']},
             'ROM': {'name': '罗马书', 'aliases': ['罗']},
             '1CO': {'name': '哥林多前书', 'aliases': ['林前', '哥前']},
             '2CO': {'name': '哥林多后书', 'aliases': ['林后', '哥后']},
             'GAL': {'name': '加拉太书', 'aliases': ['加']},
             'EPH': {'name': '以弗所书', 'aliases': ['弗']},
             'PHP': {'name': '腓立比书', 'aliases': ['腓']},
             'COL': {'name': '歌罗西书', 'aliases': ['西']},
             '1TH': {'name': '帖撒罗尼迦前书', 'aliases': ['帖前']},
             '2TH': {'name': '帖撒罗尼迦后书', 'aliases': ['帖后']},
             '1TI': {'name': '提摩太前书', 'aliases': ['提前']},
             '2TI': {'name': '提摩太后书', 'aliases': ['提后']},
             'TIT': {'name': '提多书', 'aliases': ['多']},
             'PHM': {'name': '腓利门书', 'aliases': ['门']},
             'HEB': {'name': '希伯来书', 'aliases': ['来']},
             'JAS': {'name': '雅各书', 'aliases': ['雅']},
             '1PE': {'name': '彼得前书', 'aliases': ['彼前']},
             '2PE': {'name': '彼得后书', 'aliases': ['彼后']},
             '1JN': {'name': '约翰一书', 'aliases': ['约一', '约壹', '约翰一']},
             '2JN': {'name': '约翰二书', 'aliases': ['约二', '约贰', '约翰二']},
             '3JN': {'name': '约翰三书', 'aliases': ['约三', '约叁', '约翰三']},
             'JUD': {'name': '犹大书', 'aliases': ['犹']},
             'REV': {'name': '启示录', 'aliases': ['启']}}}


def normalize_language_code(language_code: Optional[str]) -> Optional[str]:
    """Return a canonical language code for a supported Bible-book map."""
    if language_code is None:
        return None
    raw = str(language_code).strip()
    if raw in LANGUAGE_BOOK_MAPS:
        return raw
    normalized = raw.replace("_", "-").casefold()
    return LANGUAGE_ALIASES.get(normalized)


def normalize_language_codes(language_codes: LanguageCodeInput = None) -> Tuple[str, ...]:
    """Normalize a language-code input into a stable tuple of supported codes."""
    if language_codes is None:
        candidates = DEFAULT_LANGUAGE_CODES
    elif isinstance(language_codes, str):
        candidates = (language_codes,)
    else:
        candidates = tuple(language_codes)

    normalized_codes: List[str] = []
    for candidate in candidates:
        normalized = normalize_language_code(str(candidate))
        if normalized and normalized not in normalized_codes:
            normalized_codes.append(normalized)
    return tuple(normalized_codes)


def get_supported_language_codes() -> List[str]:
    """Return supported language codes for Bible-book names and aliases."""
    return sorted(LANGUAGE_BOOK_MAPS.keys())


def validate_language_maps() -> Dict[str, Any]:
    """Return validation details showing map coverage against canonical books."""
    canonical_codes = set(CANONICAL_BOOKS)
    language_details = {}
    for language_code, language_map in LANGUAGE_BOOK_MAPS.items():
        codes = set(language_map)
        language_details[language_code] = {
            "book_count": len(codes),
            "missing_codes": sorted(canonical_codes - codes),
            "extra_codes": sorted(codes - canonical_codes),
        }
    return {
        "canonical_book_count": len(canonical_codes),
        "language_count": len(LANGUAGE_BOOK_MAPS),
        "languages": language_details,
    }


def _normalize_alias(alias: str) -> str:
    """Normalize a Bible book alias for exact lookup."""
    normalized = re.sub(r"\s+", " ", str(alias or "").replace(".", " ").strip())
    return normalized.casefold()


def _compact_alias(alias: str) -> str:
    """Return a no-space/no-period alias variant for compact references."""
    return re.sub(r"[\s.]+", "", str(alias or "")).casefold()


def _compact_numbered_aliases(aliases: Iterable[str]) -> List[str]:
    """Return compact variants for aliases such as '1 Cor' -> '1Cor'."""
    compact_aliases = []
    for alias in aliases:
        alias_text = str(alias)
        if re.match(r"^[1-3]\s+", alias_text):
            compact_aliases.append(re.sub(r"\s+", "", alias_text))
    return compact_aliases


def _language_entries_for_book(book_code: str, language_codes: Tuple[str, ...]) -> List[Tuple[str, Mapping[str, Any]]]:
    entries = []
    for language_code in language_codes:
        entry = LANGUAGE_BOOK_MAPS.get(language_code, {}).get(book_code)
        if entry:
            entries.append((language_code, entry))
    return entries


def get_book_name(book_code: str, language_code: str = "en") -> Optional[str]:
    """Return a book name for a canonical code in the requested language."""
    canonical_code = str(book_code or "").upper()
    normalized_language = normalize_language_code(language_code)
    if not normalized_language:
        return None
    entry = LANGUAGE_BOOK_MAPS.get(normalized_language, {}).get(canonical_code)
    if not entry:
        return None
    return str(entry.get("name") or "") or None


def get_language_aliases(language_code: str, include_names: bool = False) -> List[str]:
    """Return aliases for one supported Bible-book language map."""
    normalized_language = normalize_language_code(language_code)
    if not normalized_language:
        return []
    aliases = []
    for entry in LANGUAGE_BOOK_MAPS.get(normalized_language, {}).values():
        if include_names and entry.get("name"):
            aliases.append(str(entry["name"]))
        aliases.extend(entry.get("aliases", []))
    return sorted(set(aliases), key=lambda alias: (-len(alias), alias.casefold(), alias))


def get_all_reference_aliases(
    language_codes: LanguageCodeInput = None,
    include_names: bool = True,
    include_compact_numbered: bool = True,
) -> List[str]:
    """Return all aliases used for Bible-reference detection."""
    active_language_codes = normalize_language_codes(language_codes)
    aliases: List[str] = []
    for book_code in CANONICAL_BOOKS:
        for _language_code, entry in _language_entries_for_book(book_code, active_language_codes):
            if include_names and entry.get("name"):
                aliases.append(str(entry["name"]))
            aliases.extend(entry.get("aliases", []))
    if include_compact_numbered:
        aliases.extend(_compact_numbered_aliases(aliases))
    return sorted(set(aliases), key=lambda alias: (-len(alias), alias.casefold(), alias))


def build_alias_lookup(
    language_codes: LanguageCodeInput = None,
    include_names: bool = True,
    include_compact_numbered: bool = True,
) -> Dict[str, Dict[str, str]]:
    """Build normalized alias -> canonical book metadata lookup."""
    active_language_codes = normalize_language_codes(language_codes)
    lookup: Dict[str, Dict[str, str]] = {}

    for book_code in CANONICAL_BOOKS:
        for language_code, entry in _language_entries_for_book(book_code, active_language_codes):
            aliases: List[str] = []
            if include_names and entry.get("name"):
                aliases.append(str(entry["name"]))
            aliases.extend(entry.get("aliases", []))
            if include_compact_numbered:
                aliases.extend(_compact_numbered_aliases(aliases))

            for alias in aliases:
                if not alias:
                    continue
                payload = {"book_code": book_code, "language_code": language_code, "alias": str(alias)}
                lookup.setdefault(_normalize_alias(alias), payload)
                lookup.setdefault(_compact_alias(alias), payload)

    return lookup


def get_book_code_for_alias(alias: Any, language_codes: LanguageCodeInput = None) -> Optional[str]:
    """Return the canonical workflow code for a Bible book alias/name."""
    if alias is None:
        return None
    alias_text = str(alias)
    lookup = build_alias_lookup(language_codes=language_codes)
    match = lookup.get(_normalize_alias(alias_text)) or lookup.get(_compact_alias(alias_text))
    if not match:
        return None
    return match["book_code"]


def get_book_metadata(book_code: str) -> Optional[Dict[str, Any]]:
    """Return canonical and per-language metadata for a Bible book code."""
    canonical_code = str(book_code or "").upper()
    if canonical_code not in CANONICAL_BOOKS:
        return None
    return {
        "book_code": canonical_code,
        **CANONICAL_BOOKS[canonical_code],
        "names": {
            language_code: language_map[canonical_code].get("name")
            for language_code, language_map in LANGUAGE_BOOK_MAPS.items()
            if canonical_code in language_map
        },
        "aliases": {
            language_code: list(language_map[canonical_code].get("aliases", []))
            for language_code, language_map in LANGUAGE_BOOK_MAPS.items()
            if canonical_code in language_map
        },
    }


def _alias_regex_fragment(alias: str) -> str:
    escaped = re.escape(str(alias).strip())
    escaped = escaped.replace(r"\ ", r"\s*")
    return escaped


def build_book_alias_regex(
    language_codes: LanguageCodeInput = None,
    include_names: bool = True,
) -> str:
    """Build a regex alternation for known Bible book aliases."""
    aliases = get_all_reference_aliases(language_codes=language_codes, include_names=include_names)
    return r"(?:" + "|".join(_alias_regex_fragment(alias) for alias in aliases) + r")"


def build_bible_reference_regex(
    language_codes: LanguageCodeInput = None,
    include_names: bool = True,
) -> re.Pattern:
    """Build a compiled regex that finds Bible references in text."""
    book_alias = build_book_alias_regex(language_codes=language_codes, include_names=include_names)
    reference = (
        r"\d+"
        r"(?:[:：]\d+[A-Za-z]?)?"
        r"(?:\s*[-–—－,，]\s*\d+(?:[:：]\d+[A-Za-z]?)?)*"
    )
    version = r"(?:\s+(?P<version>(?-i:[A-Z]{2,6})))?"
    return re.compile(
        rf"(?<![A-Za-z0-9])(?P<book>{book_alias})\.?\s*(?P<reference>{reference}){version}(?![A-Za-z0-9])",
        flags=re.IGNORECASE,
    )


def find_bible_references(
    text: str,
    language_codes: LanguageCodeInput = None,
    include_names: bool = True,
) -> List[Dict[str, Any]]:
    """Return structured Bible-reference matches found in text."""
    source = str(text or "")
    active_language_codes = normalize_language_codes(language_codes)
    pattern = build_bible_reference_regex(language_codes=active_language_codes, include_names=include_names)
    alias_lookup = build_alias_lookup(language_codes=active_language_codes, include_names=include_names)
    matches: List[Dict[str, Any]] = []

    for match in pattern.finditer(source):
        book_alias = match.group("book")
        alias_match = alias_lookup.get(_normalize_alias(book_alias)) or alias_lookup.get(_compact_alias(book_alias))
        if not alias_match:
            continue
        book_code = alias_match["book_code"]
        metadata = get_book_metadata(book_code) or {}
        matches.append(
            {
                "matched_text": match.group(0),
                "book_alias": book_alias,
                "book_code": book_code,
                "matched_language": alias_match.get("language_code"),
                "reference": match.group("reference"),
                "version": match.groupdict().get("version"),
                "start": match.start(),
                "end": match.end(),
                "names": metadata.get("names", {}),
                "aliases": metadata.get("aliases", {}),
            }
        )

    return matches


def contains_bible_reference(
    text: str,
    language_codes: LanguageCodeInput = None,
    include_names: bool = True,
) -> bool:
    """Return True when text contains a recognized Bible reference."""
    return bool(find_bible_references(text, language_codes=language_codes, include_names=include_names))


def is_bible_reference_like(
    text: str,
    language_codes: LanguageCodeInput = None,
    include_names: bool = True,
) -> bool:
    """Return True when the whole text is a Bible reference or bracketed reference."""
    stripped = str(text or "").strip()
    if not stripped:
        return False

    unwrapped = stripped
    if stripped.startswith("[") and "]" in stripped:
        closing_index = stripped.index("]")
        if stripped[closing_index + 1 :].strip():
            return False
        unwrapped = stripped[1:closing_index]

    matches = find_bible_references(
        unwrapped,
        language_codes=language_codes,
        include_names=include_names,
    )
    if len(matches) != 1:
        return False
    match = matches[0]
    return match["start"] == 0 and match["end"] == len(unwrapped.strip())


if __name__ == "__main__":
    print("Canonical book count:", len(CANONICAL_BOOKS))
    print("Supported languages:", get_supported_language_codes())
    print("Map validation:", validate_language_maps())
    print("Reference aliases:", len(get_all_reference_aliases()))
    print("Rev ->", get_book_code_for_alias("Rev"))
    print("Ap ->", get_book_code_for_alias("Ap"))
    print("启 ->", get_book_code_for_alias("启"))
    print("2Co ->", get_book_code_for_alias("2Co"))
