"""Meeting turn -> one action for the engine.

Actions: ignore | query | amend | cancel | clarify | draft.
- query:   a request for the supported metric (one task per product mentioned)
- amend:   a correction of a recent task ("nei, jeg mente produkt Z")
- cancel:  "glem det", "vi trenger ikke det likevel"
- clarify: a spoken answer to Room's question ("Produkt Y.", "siste 30 dager")
- draft:   "lag et GitHub-issue av det"

Rules run locally and handle Norwegian speech as transcribed (letter names like "eks",
spelled-out numbers, self-corrections). With OPENAI_API_KEY the turn plus recent context
can be sent to a model instead; its answer is validated and falls back to the rules.
"""
import json
import os
import re

import httpx

PRODUCTS = ('X', 'Y', 'Z')
DAYS = (7, 30, 90)
LETTERS = {'x': 'X', 'eks': 'X', 'ex': 'X', 'y': 'Y', 'z': 'Z', 'sett': 'Z', 'zett': 'Z', 'zed': 'Z'}
LETTER = r'(x|y|z|eks|ex|sett|zett|zed)'

PRODUCT_RE = re.compile(r'\b(?:produkt(?:et|ene|er)?|product)\s*[-–]?\s*' + LETTER + r'\b'
                        r'|\b([xyz])\s*-\s*produkt(?:et)?\b|\b([xyz])\s+produktet\b', re.I)
LIST_CONT_RE = re.compile(r'\s*(?:,|\bog\b|\beller\b|\bsamt\b|\bog også\b)\s*(?:produkt\s*)?([xyz])\b', re.I)
BARE_FOLLOWUP_RE = re.compile(r'^\W*(?:ja,?\s*|ok,?\s*)?(?:og\s+|men\s+)?(?:hva\s+(?:så\s+)?med|enn|og\s+for|samme\s+for|'
                              r'det\s+samme\s+for|også\s+for|for)\s+(?:produkt\s*)?([xyz])\b', re.I)
BARE_ONLY_RE = re.compile(r'^\W*(?:produkt\s*)?([xyz])\W*$', re.I)
PRODUCT_REF_RE = re.compile(r'\b(produktet|det\s+produktet|samme\s+produkt(?:et)?)\b', re.I)

PERIODS = [
    (7, re.compile(r'\b(?:7|sju|syv)\s*(?:dager|dagene|døgn|d)\b', re.I), True),
    (30, re.compile(r'\b(?:30|tretti|tredve)\s*(?:dager|dagene|døgn|d)\b', re.I), True),
    (90, re.compile(r'\b(?:90|nitti)\s*(?:dager|dagene|døgn|d)\b', re.I), True),
    (7, re.compile(r'\b(?:siste|forrige|denne|en|ei|den\s+siste|sist)\s+(?:uke|uka|uken|veka)\b|\bi\s+(?:uke|uka|uken)\b', re.I), False),
    (30, re.compile(r'\b(?:siste|forrige|denne|en|den\s+siste)\s+(?:måned|måneden|mnd)\b|\bi\s+måneden\b|\b(?:fire|4)\s+uker\b', re.I), False),
    (90, re.compile(r'\b(?:siste|forrige|dette|et|det\s+siste)\s+kvartal(?:et)?\b|\b(?:tre|3)\s+måneder\b|\b(?:13|tretten)\s+uker\b', re.I), False),
]
UNSUPPORTED_PERIOD_RE = re.compile(r'\b(\d+|to|fem|seks|åtte|ti|tolv|fjorten|seksti|hundre)\s+(dager|uker|måneder|år)\b|'
                                   r'\bi\s+år\b|\bi\s+fjor\b|\b(?:hele\s+)?året\b|\bi\s+går\b|\bi\s+dag\b', re.I)

ASK_RE = re.compile(r'\b(hvor\s+mange|antall(?:et)?|andel(?:en)?|hvor\s+stor\s+(?:del|andel)|prosent(?:en|andel)?|'
                    r'hvor\s+mye|tall(?:ene)?\s+på|how\s+many|number\s+of|share\s+of)\b', re.I)
LOOK_RE = re.compile(r'\b(se|sjekke?|sjekk|finne\s+ut|undersøke?|hente|vise?|telle|tell|grave|titt|check|look|show)\b', re.I)
USAGE_RE = re.compile(r'\b(bruk\w*|aktiv\w*|adopsjon\w*|usage|adoption|uses?|using|used)\b', re.I)

CANCEL_RE = re.compile(r'\b(glem\s+det|glem\s+den|dropp\s+det|dropp\s+den|avbryt(?:\s+det)?|stopp\s+(?:det|den|undersøkelsen)|'
                       r'la\s+(?:det|den)\s+være|ikke\s+bry\s+deg|det\s+trenger\s+vi\s+ikke|vi\s+trenger\s+ikke\s+det|'
                       r'ikke\s+nødvendig\s+likevel|det\s+tar\s+vi\s+(?:senere|en\s+annen\s+gang|etterpå))\b', re.I)
NEG_REQUEST_RE = re.compile(r'\b(trenger|behøver|må|skal|bør|burde)\s+(?:vi\s+|du\s+|dere\s+|room\s+)?ikke\b|'
                            r'\bikke\s+(?:sjekk|undersøk|se\b|lag|hent|vis|start|kjør|tell|finn)\w*|'
                            r'\bdropp\b|\bdet\s+er\s+ikke\s+(?:nødvendig|viktig|vits)\b|\bikke\s+nødvendig\b|\bingen\s+grunn\s+til\b', re.I)
SUGGESTION_RE = re.compile(r'^\W*(?:skal|bør|burde|kan|kunne)\s+(?:vi|du|dere|noen|room)\s+ikke\b.*\?\s*$', re.I | re.S)
LIKEVEL_RE = re.compile(r'\b(likevel|allikevel)\b', re.I)
STRONG_CORRECTION_RE = re.compile(r'\b(jeg\s+mente|mente|jeg\s+mener|rettelse|unnskyld|sorry|beklager|eller\s+nei|'
                                  r'rettere\s+sagt|nei\s+vent|feil\s+produkt|feil\s+periode)\b', re.I)
WEAK_CORRECTION_LEAD_RE = re.compile(r'^\W*(nei|vent)\b', re.I)
INLINE_CORRECTION_RE = re.compile(r'\b(nei|nei\s+vent|unnskyld|sorry|jeg\s+mener|jeg\s+mente|mener|eller\s+nei|rettere\s+sagt|beklager)\b', re.I)
FOLLOWUP_RE = re.compile(r'^\W*(?:ja,?\s*|ok,?\s*)?(?:og|men)?\s*(?:hva\s+(?:så\s+)?med|enn|og\s+for|samme\s+for|det\s+samme\s+for|'
                         r'også\s+for|gjør\s+det\s+samme|samme\s+sak|og)\b', re.I)
ISSUE_RE = re.compile(r'\b(lag|lage|opprett|opprette|skriv|skrive)\b.{0,40}\b(issue|github|sak|ticket)\w*', re.I | re.S)


def normalize(text):
    return ' '.join(str(text).replace('–', '-').replace('«', '"').replace('»', '"').split())


def _negated(text, start):
    return bool(re.search(r'\bikke\s+(?:for\s+|på\s+)?$', text[max(0, start - 14):start], re.I))


def find_products(text):
    """[(position, product, negated)] in speaking order."""
    found = {}
    for m in PRODUCT_RE.finditer(text):
        letter = next(g for g in m.groups() if g)
        found[m.start()] = (m.start(), LETTERS[letter.lower()], _negated(text, m.start()))
        pos = m.end()
        while (cont := LIST_CONT_RE.match(text, pos)):
            found.setdefault(cont.start(1), (cont.start(1), LETTERS[cont.group(1).lower()], _negated(text, cont.start(1))))
            pos = cont.end()
    for regex in (BARE_FOLLOWUP_RE, BARE_ONLY_RE):
        m = regex.match(text)
        if m:
            found.setdefault(m.start(1), (m.start(1), LETTERS[m.group(1).lower()], False))
    return sorted(found.values())


def find_periods(text):
    found = []
    for days, regex, explicit in PERIODS:
        for m in regex.finditer(text):
            found.append((m.start(), days, m.group(0), explicit))
    return sorted(found)


def _dedupe(values):
    out = []
    for v in values:
        if v not in out:
            out.append(v)
    return out


def decision(action, reason, **fields):
    base = {'action': action, 'products': [], 'days': None, 'target': None, 'notes': [], 'reason': reason,
            'interpreter': 'regler', 'followup': False}
    base.update(fields)
    return base


def rules(text, ctx):
    """Deterministic interpretation. ctx = {'tasks': [newest first, with 'age_s'], 'last_product': 'X'|None}."""
    t = normalize(text)
    words = len(t.split())
    mentions = find_products(t)
    periods = find_periods(t)
    positive = [p for _, p, neg in mentions if not neg]
    negated = [p for _, p, neg in mentions if neg]
    tasks = ctx.get('tasks', [])
    recent = [x for x in tasks if x['age_s'] < 180]
    cancellable = [x for x in recent if x['status'] in ('queued', 'working', 'clarify') or (x['status'] == 'ready' and not x['shown'])]
    notes = []

    days = None
    if periods:
        _, days, phrase, explicit = periods[-1]
        if len({d for _, d, _, _ in periods}) > 1:
            notes.append(f'Flere perioder nevnt; brukte den siste («{phrase}»).')
        elif not explicit:
            notes.append(f'«{phrase}» tolket som siste {days} dager.')
    unsupported = UNSUPPORTED_PERIOD_RE.search(t) if days is None else None

    ask, look, usage = ASK_RE.search(t), LOOK_RE.search(t), USAGE_RE.search(t)
    query_intent = bool((ask or look) and (usage or mentions))

    # 1. Explicit cancellation of something Room is working on.
    if CANCEL_RE.search(t):
        target = next((x for x in cancellable if positive and x['product'] in positive), None) or (cancellable[0] if cancellable else None)
        if target:
            return decision('cancel', 'Deltakerne ba Room droppe oppgaven.', target=target['id'])
        if not query_intent:
            return decision('ignore', 'Avbrytelse uten aktiv oppgave.')

    # 2. Negated requests never create tasks ("vi trenger ikke å sjekke ...").
    if NEG_REQUEST_RE.search(t) and not SUGGESTION_RE.search(t):
        if LIKEVEL_RE.search(t):
            target = next((x for x in cancellable if (not positive or x['product'] in positive) and (days is None or x['days'] == days)), None)
            if target:
                return decision('cancel', 'Oppgaven ble trukket tilbake («likevel»).', target=target['id'])
        return decision('ignore', 'Nektet forespørsel: ingen oppgave opprettet.')

    # 3. Spoken request for an issue draft from the latest finding.
    if ISSUE_RE.search(t):
        ready = [x for x in tasks if x['status'] == 'ready']
        shown = sorted((x for x in ready if x.get('shown_at')), key=lambda x: x['shown_at'], reverse=True)
        target = (shown or ready or [None])[0]
        if target:
            return decision('draft', 'Deltakerne ba om et GitHub-utkast.', target=target['id'])

    # 4. Correction of a recent task. In-turn self-corrections are handled under query.
    first_mention = min([pos for pos, _, _ in mentions] + [pos for pos, _, _, _ in periods] or [len(t)])
    strong = STRONG_CORRECTION_RE.search(t)
    cross_turn = (strong and strong.start() < first_mention) or (WEAK_CORRECTION_LEAD_RE.match(t) and not query_intent) \
        or (negated and positive and not query_intent)
    if cross_turn and (positive or days):
        target = next((x for x in recent if negated and x['product'] in negated), None) \
            or next((x for x in recent if x['age_s'] < 120 and x['status'] != 'cancelled'), None)
        if target:
            new_product = positive[-1] if positive else None
            return decision('amend', 'Rettelse av forrige spørsmål.', target=target['id'],
                            products=[new_product] if new_product else [], days=days, notes=notes)

    # 5. Spoken answer to Room's clarification question.
    clarify = next((x for x in recent if x['status'] == 'clarify'), None)
    if clarify and (positive or days) and not query_intent and words <= 12:
        return decision('clarify', 'Svar på Rooms avklaringsspørsmål.', target=clarify['id'],
                        products=positive[-1:], days=days, notes=notes)

    # 6. A request, possibly a follow-up that inherits from the previous question.
    followup = bool(FOLLOWUP_RE.match(t)) and bool(recent) and bool(positive or days)
    if query_intent or followup or (positive and days and '?' in t):
        products = _dedupe(positive) or _dedupe(negated)
        if len(products) > 1:
            between = [m for m in INLINE_CORRECTION_RE.finditer(t)
                       if mentions[0][0] < m.start() < mentions[-1][0]]
            if between:
                notes.append(f'Rettet underveis fra produkt {products[0]} til {products[-1]}.')
                products = products[-1:]
        if negated and not positive:
            notes.append('Spørsmålet gjelder kunder uten bruk. Grafen viser begge gruppene.')
        base = recent[0] if recent else None
        if not products and followup and base and base['product']:
            products = [base['product']]
            notes.append(f'Produkt {base["product"]} hentet fra forrige spørsmål.')
        if not products and PRODUCT_REF_RE.search(t) and ctx.get('last_product'):
            products = [ctx['last_product']]
            notes.append(f'«{PRODUCT_REF_RE.search(t).group(0)}» tolket som produkt {ctx["last_product"]} fra samtalen.')
        if days is None and followup and base and base['days']:
            days = base['days']
            notes.append(f'Periode ({days} dager) hentet fra forrige spørsmål.')
        if days is None and unsupported:
            notes.append(f'«{unsupported.group(0)}» støttes ikke ennå. Velg 7, 30 eller 90 dager.')
        return decision('query', 'Produktspørsmål oppdaget.', products=products[:3] or [None], days=days,
                        notes=notes, followup=followup and not query_intent)

    return decision('ignore', 'Ingen støttet forespørsel i replikken.')


def has_signal(text, ctx):
    """Cheap pre-filter so ordinary chatter is never sent to a model."""
    t = normalize(text)
    return bool(find_products(t) or find_periods(t) or ASK_RE.search(t) or CANCEL_RE.search(t) or ISSUE_RE.search(t)
                or STRONG_CORRECTION_RE.search(t) or any(x['status'] == 'clarify' for x in ctx.get('tasks', [])))


SCHEMA = {
    'type': 'object', 'additionalProperties': False,
    'required': ['action', 'products', 'days', 'target_task_id', 'notes'],
    'properties': {
        'action': {'type': 'string', 'enum': ['ignore', 'query', 'amend', 'cancel', 'clarify', 'draft']},
        'products': {'type': 'array', 'items': {'type': 'string', 'enum': list(PRODUCTS)}},
        'days': {'anyOf': [{'type': 'integer', 'enum': list(DAYS)}, {'type': 'null'}]},
        'target_task_id': {'anyOf': [{'type': 'string'}, {'type': 'null'}]},
        'notes': {'type': 'array', 'items': {'type': 'string'}},
    },
}

INSTRUCTIONS = '''You interpret one turn of a Norwegian product meeting for Room, a meeting assistant.
Room supports exactly one metric: distinct new external customers who used product X, Y or Z within the last 7, 30 or 90 days.
Return JSON only. Choose one action:
- query: someone asks for that metric (also indirect wishes like "det hadde vært fint å se ..."). One product per entry; never invent a missing product or window (use [] / null and Room will ask). For follow-ups ("og hva med Y?") inherit missing fields from the most recent task.
- amend: the turn corrects a recent task ("nei, jeg mente Z"). Set target_task_id and only the corrected fields.
- cancel: the group withdraws a task ("glem det", "vi trenger ikke det likevel"). Set target_task_id.
- clarify: the turn answers Room's open clarification question (a task with status clarify). Set target_task_id.
- draft: the group asks for a GitHub issue from a ready result. Set target_task_id.
- ignore: everything else, including negated, hypothetical-only or unsupported requests.
notes: at most two short Norwegian sentences explaining uncertain interpretations (e.g. "«forrige måned» tolket som 30 dager").'''


async def model_decide(text, ctx, client=None):
    payload = {
        'turn': text,
        'recent_turns': ctx.get('turns', [])[-6:],
        'tasks': [{k: x[k] for k in ('id', 'product', 'days', 'status', 'question')} for x in ctx.get('tasks', [])[:6]],
    }
    body = {'model': os.getenv('OPENAI_MODEL', 'gpt-4.1-mini'), 'instructions': INSTRUCTIONS,
            'input': json.dumps(payload, ensure_ascii=False),
            'text': {'format': {'type': 'json_schema', 'name': 'room_action', 'strict': True, 'schema': SCHEMA}}}
    owns = client is None
    client = client or httpx.AsyncClient(timeout=20)
    try:
        response = await client.post('https://api.openai.com/v1/responses',
                                     headers={'Authorization': 'Bearer ' + os.environ['OPENAI_API_KEY']}, json=body)
        response.raise_for_status()
        output = ''.join(p.get('text', '') for item in response.json().get('output', [])
                         for p in item.get('content', []) if p.get('type') == 'output_text')
        return validate(json.loads(output), ctx)
    finally:
        if owns:
            await client.aclose()


def validate(value, ctx):
    ids = {x['id']: x for x in ctx.get('tasks', [])}
    action = value.get('action')
    products = [p for p in value.get('products') or [] if p in PRODUCTS][:3]
    days = value.get('days') if value.get('days') in DAYS else None
    target = value.get('target_task_id')
    notes = [str(n)[:160] for n in (value.get('notes') or [])][:2]
    if action not in ('ignore', 'query', 'amend', 'cancel', 'clarify', 'draft'):
        raise ValueError('unknown action')
    if action in ('amend', 'cancel', 'clarify', 'draft') and target not in ids:
        raise ValueError('target is not a known task')
    if action == 'draft' and ids[target]['status'] != 'ready':
        raise ValueError('draft target is not ready')
    return decision(action, 'Tolket av modell.', products=products or ([None] if action == 'query' else []), days=days,
                    target=target, notes=notes, interpreter='OpenAI')


async def decide(text, ctx):
    if os.getenv('OPENAI_API_KEY') and os.getenv('ROOM_INTERPRETER', 'model') == 'model' and has_signal(text, ctx):
        try:
            return await model_decide(text, ctx)
        except Exception as exc:  # Model unavailable or invalid: the rules still work.
            result = rules(text, ctx)
            result['notes'] = result['notes'] + [f'Modelltolkning feilet ({type(exc).__name__}); brukte regler.']
            return result
    return rules(text, ctx)
