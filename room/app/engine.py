import asyncio
import json
import os
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from . import interpret, store
from .config import retention

ACTIVE = ('queued', 'working', 'clarify')
DUPLICATE_WINDOW_S = 20 * 60


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def age_s(iso):
    try:
        return (datetime.now(timezone.utc) - datetime.fromisoformat(iso)).total_seconds()
    except (TypeError, ValueError):
        return 1e9


class Engine:
    def __init__(self, path=None, transcript_path=None):
        self.tasks = {}
        self.transcript = []
        self.jobs = set()
        self.job_for = {}
        self.drafts = []
        self.listeners = []
        self._lock = None
        self.path = path or store.ROOT / 'data' / 'session.json'
        self.transcript_path = transcript_path or store.ROOT / 'data' / 'transcript.json'
        self.pace = float(os.getenv('ROOM_DEMO_PACE_S', '0.8'))
        if self.path.exists():
            try:
                saved = json.loads(self.path.read_text())
                self.tasks = saved.get('tasks', {})
                self.drafts = saved.get('drafts', [])
                for task in self.tasks.values():
                    if task['status'] in ('working', 'queued'):
                        task['status'] = 'error'
                        task['error'] = 'Serveren ble startet på nytt. Send spørsmålet igjen.'
            except (ValueError, OSError):
                pass
        if retention().transcript == 'disk' and self.transcript_path.exists():
            try:
                self.transcript = json.loads(self.transcript_path.read_text())
            except (ValueError, OSError):
                pass

    @property
    def lock(self):
        if self._lock is None:
            self._lock = asyncio.Lock()
        return self._lock

    def save(self):
        self.path.parent.mkdir(exist_ok=True)
        tmp = self.path.with_suffix('.tmp')
        tmp.write_text(json.dumps({'tasks': self.tasks, 'drafts': self.drafts}, ensure_ascii=False))
        tmp.replace(self.path)
        if retention().transcript == 'disk':
            tmp = self.transcript_path.with_suffix('.tmp')
            tmp.write_text(json.dumps(self.transcript, ensure_ascii=False))
            tmp.replace(self.transcript_path)
        self.changed()

    def changed(self):
        for listener in self.listeners:
            listener()

    def clear_transcript(self):
        self.transcript = []
        if self.transcript_path.exists():
            self.transcript_path.unlink()
        self.save()

    def state(self):
        return {'tasks': list(self.tasks.values()), 'transcript': self.transcript[-80:], 'drafts': self.drafts,
                'mode': 'AI · OpenAI-tolkning' if os.getenv('OPENAI_API_KEY') else 'Demo · regelstyrt tolkning',
                'hardware': 'Simulert · ingen boks tilkoblet'}

    # ------------------------------------------------------------------ turns

    def context(self):
        tasks = sorted(self.tasks.values(), key=lambda t: t['created_at'], reverse=True)
        ctx_tasks = [{'id': t['id'], 'product': t['product'], 'days': t['days'], 'status': t['status'],
                      'shown': t.get('shown', False), 'shown_at': t.get('shown_at'), 'question': t['question'],
                      'age_s': age_s(t.get('updated_at', t['created_at']))}
                     for t in tasks[:10]]
        turns = [t for t in self.transcript[-8:] if age_s(t['at']) < 300]
        last_product = None
        for turn in reversed(turns):
            mentioned = [p for _, p, neg in interpret.find_products(interpret.normalize(turn['text'])) if not neg]
            if mentioned:
                last_product = mentioned[-1]
                break
        if not last_product:
            last_product = next((t['product'] for t in ctx_tasks if t['product'] and t['age_s'] < 300), None)
        return {'tasks': ctx_tasks, 'turns': [t['text'] for t in turns], 'last_product': last_product}

    async def submit(self, text, source='text'):
        turn = {'text': text, 'source': source, 'segments': 1, 'started_at': now_iso(), 'ended_at': now_iso()}
        outcome = await self.handle_turn(turn)
        if outcome['kind'] == 'ignored':
            return {'ignored': True, 'outcome': outcome}
        result = {'id': outcome.get('task_id'), 'outcome': outcome}
        if outcome['kind'] == 'duplicate':
            result['duplicate'] = True
        return result

    async def handle_turn(self, turn):
        """Interpret one complete turn with recent context and apply exactly one action."""
        async with self.lock:
            entry = {'id': uuid.uuid4().hex[:10], 'text': turn['text'], 'source': turn.get('source', 'mic'),
                     'segments': turn.get('segments', 1), 'at': turn.get('started_at') or now_iso(),
                     'ended_at': turn.get('ended_at') or now_iso(), 'outcome': {'kind': 'pending'}}
            ctx = self.context()
            self.transcript.append(entry)
            del self.transcript[:-500]
            self.changed()
            decision = await interpret.decide(turn['text'], ctx)
            entry['outcome'] = self.apply(decision, entry, ctx)
            entry['interpreter'] = decision['interpreter']
            self.save()
            return entry['outcome']

    def apply(self, d, turn, ctx):
        action = d['action']
        target = self.tasks.get(d.get('target') or '')
        if action == 'ignore':
            return {'kind': 'ignored', 'reason': d['reason']}
        if action == 'cancel' and target:
            self.cancel(target, f'Avbrutt i møtet: «{turn["text"]}»')
            return {'kind': 'cancel', 'task_id': target['id'], 'reason': d['reason']}
        if action == 'draft' and target and target['status'] == 'ready':
            self.make_draft(target)
            self.log(target, 'draft', 'GitHub-utkast laget etter muntlig ønske', turn)
            return {'kind': 'draft', 'task_id': target['id'], 'reason': d['reason']}
        if action in ('amend', 'clarify') and target:
            return self.amend(target, d, turn)
        if action == 'query':
            outcomes = [self.query(product, d['days'], d, turn, ctx) for product in (d['products'] or [None])]
            first = outcomes[0]
            if len(outcomes) > 1:
                first = dict(first, task_ids=[o['task_id'] for o in outcomes], kind='task')
            return first
        return {'kind': 'ignored', 'reason': 'Fant ikke oppgaven det ble henvist til.'}

    def find_duplicate(self, product, days, exclude=None):
        for task in sorted(self.tasks.values(), key=lambda t: t['created_at'], reverse=True):
            if task['id'] == exclude or task['product'] != product or task['days'] != days:
                continue
            if task['status'] in ACTIVE or (task['status'] == 'ready' and age_s(task['created_at']) < DUPLICATE_WINDOW_S):
                return task
        return None

    def query(self, product, days, d, turn, ctx):
        duplicate = self.find_duplicate(product, days)
        if duplicate:
            duplicate['mentions'] = duplicate.get('mentions', 1) + 1
            self.log(duplicate, 'repeat', 'Samme spørsmål stilt igjen', turn)
            return {'kind': 'duplicate', 'task_id': duplicate['id'], 'reason': 'Gjentakelse av en aktiv oppgave.'}
        task = {'id': uuid.uuid4().hex[:12], 'question': turn['text'], 'product': product, 'days': days,
                'status': 'clarify' if not product or not days else 'queued',
                'agents': {'code': 'waiting', 'data': 'waiting', 'presentation': 'waiting'},
                'shown': False, 'created_at': now_iso(), 'updated_at': now_iso(),
                'source': turn.get('source', 'mic'), 'turn_id': turn['id'], 'notes': list(d['notes']),
                'interpreter': d['interpreter'], 'followup': d.get('followup', False), 'mentions': 1,
                'context': ctx['turns'][-3:], 'history': []}
        self.tasks[task['id']] = task
        self.log(task, 'created', 'Oppfølgingsspørsmål' if task['followup'] else 'Spørsmål oppdaget', turn)
        if task['status'] == 'queued':
            self.launch(task)
        kind = 'clarify' if task['status'] == 'clarify' else ('followup' if task['followup'] else 'task')
        return {'kind': kind, 'task_id': task['id'], 'reason': d['reason']}

    def amend(self, task, d, turn):
        before = (task['product'], task['days'])
        product = next((p for p in d['products'] if p), None) or task['product']
        days = d['days'] or task['days']
        if (product, days) == before and task['status'] != 'clarify':
            self.log(task, 'repeat', 'Rettelse uten endring', turn)
            return {'kind': 'duplicate', 'task_id': task['id'], 'reason': 'Rettelsen endret ingenting.'}
        duplicate = self.find_duplicate(product, days, exclude=task['id']) if product and days else None
        if duplicate:
            self.cancel(task, f'Rettet til samme spørsmål som en annen oppgave: «{turn["text"]}»')
            self.log(duplicate, 'repeat', 'En rettelse pekte hit', turn)
            return {'kind': 'amend', 'task_id': duplicate['id'], 'reason': 'Rettelsen tilsvarte en eksisterende oppgave.'}
        changes = []
        if product != before[0]:
            changes.append(f'produkt {before[0] or "?"} → {product}')
        if days != before[1]:
            changes.append(f'{before[1] or "?"} → {days} dager')
        self.stop_job(task)
        task.update(product=product, days=days, shown=False, updated_at=now_iso(),
                    agents={'code': 'waiting', 'data': 'waiting', 'presentation': 'waiting'})
        for key in ('result', 'evidence', 'summary', 'error', 'cancel_reason'):
            task.pop(key, None)
        task['notes'] = task.get('notes', []) + list(d['notes'])
        was_clarify = d['action'] == 'clarify'
        if was_clarify:
            task['notes'].append('Avklart muntlig: ' + ', '.join(changes) + '.')
        else:
            task['notes'].append('Rettet i møtet: ' + ', '.join(changes) + '.')
        task['status'] = 'queued' if product and days else 'clarify'
        self.log(task, 'clarified' if was_clarify else 'amended', ', '.join(changes), turn)
        if task['status'] == 'queued':
            self.launch(task)
        return {'kind': 'clarify_answer' if was_clarify else 'amend', 'task_id': task['id'],
                'reason': d['reason'], 'change': ', '.join(changes)}

    def log(self, task, kind, text, turn=None):
        task.setdefault('history', []).append({'at': now_iso(), 'kind': kind, 'text': text,
                                               'turn': turn['text'] if turn else None})

    def cancel(self, task, reason='Avbrutt'):
        self.stop_job(task)
        task['status'] = 'cancelled'
        task['cancel_reason'] = reason
        task['updated_at'] = now_iso()
        self.log(task, 'cancelled', reason)

    def stop_job(self, task):
        job = self.job_for.pop(task['id'], None)
        if job and not job.done():
            job.cancel()

    def make_draft(self, task):
        if any(d['task_id'] == task['id'] for d in self.drafts):
            return
        r = task['result']
        notes = ''.join(f'- {n}\n' for n in task.get('notes', []))
        self.drafts.append({'task_id': task['id'], 'title': f"Undersøk aktivering for produkt {r['product']}",
            'body': f"## Bakgrunn\nSpørsmål fra møtet: «{task['question']}»\n\n## Observasjon (demodata)\n{task['summary']}\n"
                    f"Periode fra {r['cutoff']}. Interne kontoer er ekskludert.\n\n"
                    + (f"## Tolkning og usikkerhet\n{notes}\n" if notes else '')
                    + "## Definisjon\nMinst én product_used-hendelse i perioden. Kilde: app/product.py.\n\n"
                    "## Akseptansekriterier\n- [ ] Undersøk årsaker uten å anta kausalitet fra grafen\n"
                    "- [ ] Foreslå ett målbart eksperiment\n\n"
                    f"## SQL\n```sql\n{r['sql']}\n```\nParametere: {json.dumps(r['parameters'])}"})

    # ------------------------------------------------------------------ agents

    def launch(self, task):
        job = asyncio.create_task(self.run(task))
        self.jobs.add(job)
        self.job_for[task['id']] = job
        job.add_done_callback(self.jobs.discard)

    async def run(self, task):
        if task['status'] == 'cancelled':
            return
        task['status'] = 'working'
        timings = {}
        self.save()

        async def code_agent():
            task['agents']['code'] = 'working'
            self.changed()
            started = time.perf_counter()
            # Real source read, off the event loop. In demo mode no LLM inference.
            source = await asyncio.to_thread((Path(__file__).parent / 'product.py').read_text)
            timings['code_ms'] = round((time.perf_counter() - started) * 1000, 1)
            await asyncio.sleep(self.pace)
            task['evidence'] = {'path': 'app/product.py', 'source': source}
            task['agents']['code'] = 'done'
            self.changed()

        async def data_agent():
            task['agents']['data'] = 'working'
            self.changed()
            started = time.perf_counter()
            result = await asyncio.to_thread(store.metric, task['product'], task['days'])
            timings['sql_ms'] = round((time.perf_counter() - started) * 1000, 1)
            await asyncio.sleep(self.pace * 1.6)
            task['result'] = result
            task['agents']['data'] = 'done'
            self.changed()

        try:
            await asyncio.gather(code_agent(), data_agent())
            if task['status'] == 'cancelled':
                return
            task['agents']['presentation'] = 'working'
            self.changed()
            await asyncio.sleep(self.pace * 0.6)
            r = task['result']
            task['summary'] = f"{r['active']} av {r['total']} nye eksterne kunder brukte produkt {r['product']} siste {r['days']} dager."
            task['timings'] = timings
            task['agents']['presentation'] = 'done'
            task['status'] = 'ready'
            task['ready_at'] = now_iso()
        except asyncio.CancelledError:
            return
        except Exception as exc:
            task['status'] = 'error'
            task['error'] = str(exc)
        finally:
            if self.job_for.get(task['id']) is asyncio.current_task():
                self.job_for.pop(task['id'], None)
        self.save()


engine = Engine()
