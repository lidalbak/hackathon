"""Evaluation set for meeting speech, as a transcription model would write it."""
import asyncio
import unittest

from app import interpret
from app.engine import Engine


def task(id, product, days, status='working', age=10, shown=False):
    return {'id': id, 'product': product, 'days': days, 'status': status, 'age_s': age, 'shown': shown, 'question': ''}


X30 = task('a', 'X', 30)

CASES = [
    # (turn, recent tasks newest first, expected action, products, days, target)
    ('Det hadde vært fint å se hvor mange nye kunder som faktisk bruker produkt X, siste 30 dager.', [], 'query', ['X'], 30, None),
    ('Hvor mange kunder bruker produkt eks siste tretti dager?', [], 'query', ['X'], 30, None),
    ('Hvor mange bruker Z-produktet den siste måneden?', [], 'query', ['Z'], 30, None),
    ('Hvor stor andel av nye kunder har brukt produkt sett siste kvartal?', [], 'query', ['Z'], 90, None),
    ('Hvor mange bruker produkt X og Y siste 90 dager?', [], 'query', ['X', 'Y'], 90, None),
    ('Hvor mange bruker produkt X, nei, produkt Y, siste 30 dager?', [], 'query', ['Y'], 30, None),
    ('Hvor mange bruker produkt X siste 30 dager, eller rettere sagt 90 dager?', [], 'query', ['X'], 90, None),
    ('Skal vi ikke sjekke hvor mange som bruker produkt Z siste uke?', [], 'query', ['Z'], 7, None),
    ('Hvor mange kunder bruker produkt Y?', [], 'query', ['Y'], None, None),
    ('Vi burde også se på bruken siste kvartal.', [], 'query', [None], 90, None),
    ('Og hva med produkt Y?', [X30], 'query', ['Y'], 30, None),
    ('Samme for 90 dager?', [X30], 'query', ['X'], 90, None),
    ('Nei vent, jeg mente produkt Z, ikke Y.', [task('b', 'Y', 30), X30], 'amend', ['Z'], None, 'b'),
    ('Jeg mente 90 dager.', [X30], 'amend', [], 90, 'a'),
    ('Produkt Y.', [task('c', None, 90, 'clarify')], 'clarify', ['Y'], None, 'c'),
    ('Siste 30 dager.', [task('c', 'Y', None, 'clarify')], 'clarify', [], 30, 'c'),
    ('Glem det.', [X30], 'cancel', [], None, 'a'),
    ('Vi trenger ikke produkt X likevel.', [X30], 'cancel', [], None, 'a'),
    ('Kan du lage et GitHub-issue av det?', [task('a', 'X', 30, 'ready')], 'draft', [], None, 'a'),
    # Never a task:
    ('Vi trenger ikke å sjekke produkt X siste 7 dager, det er for kort tid.', [], 'ignore', [], None, None),
    ('Ikke undersøk produkt Y nå.', [], 'ignore', [], None, None),
    ('Ok, skal vi starte? Vi går gjennom lanseringen først.', [], 'ignore', [], None, None),
    ('Hvor mange møter har vi igjen denne uka?', [], 'ignore', [], None, None),
    ('Hvor mange kunder har vi?', [], 'ignore', [], None, None),
    ('Glem det.', [], 'ignore', [], None, None),
]


class RuleEvaluation(unittest.TestCase):
    def test_cases(self):
        failures = []
        for text, tasks, action, products, days, target in CASES:
            d = interpret.rules(text, {'tasks': tasks, 'last_product': None})
            got = (d['action'], d['products'], d['days'], d['target'])
            if got != (action, products, days, target):
                failures.append(f'{text!r}: got {got}, expected {(action, products, days, target)}')
        self.assertEqual(failures, [])

    def test_uncertainty_is_explained(self):
        d = interpret.rules('Hvor mange bruker produktet forrige måned?', {'tasks': [], 'last_product': 'Z'})
        self.assertEqual((d['products'], d['days']), (['Z'], 30))
        self.assertEqual(len(d['notes']), 2)  # product from context + "forrige måned" as 30 days
        d = interpret.rules('Hvor mange bruker produkt X siste 14 dager?', {'tasks': [], 'last_product': None})
        self.assertIsNone(d['days'])
        self.assertIn('støttes ikke', d['notes'][0])

    def test_model_output_is_validated(self):
        ctx = {'tasks': [task('a', 'X', 30)]}
        ok = interpret.validate({'action': 'amend', 'products': ['Z'], 'days': None, 'target_task_id': 'a', 'notes': []}, ctx)
        self.assertEqual((ok['action'], ok['products'], ok['interpreter']), ('amend', ['Z'], 'OpenAI'))
        for bad in ({'action': 'amend', 'target_task_id': 'nope'}, {'action': 'delete_db'},
                    {'action': 'draft', 'target_task_id': 'a'}):
            with self.assertRaises(ValueError):
                interpret.validate(bad, ctx)


class EngineConversationTests(unittest.TestCase):
    def test_meeting_without_double_tasks(self):
        async def run():
            e = Engine()
            e.tasks, e.transcript, e.drafts, e.pace = {}, [], [], 0
            e.save = lambda: None
            say = lambda text: e.submit(text, source='mic')
            first = await say('Det hadde vært fint å se hvor mange nye kunder som bruker produkt X siste 30 dager.')
            self.assertEqual(first['outcome']['kind'], 'task')
            again = await say('Ja, hvor mange nye kunder bruker produkt X den siste måneden?')
            self.assertTrue(again['duplicate'])
            follow = await say('Og hva med produkt Y?')
            self.assertEqual(follow['outcome']['kind'], 'followup')
            fix = await say('Nei vent, jeg mente produkt Z, ikke Y.')
            self.assertEqual((fix['outcome']['kind'], fix['id']), ('amend', follow['id']))
            ask = await say('Vi burde også se på bruken siste kvartal.')
            self.assertEqual(ask['outcome']['kind'], 'clarify')
            answer = await say('Produkt Y.')
            self.assertEqual((answer['outcome']['kind'], answer['id']), ('clarify_answer', ask['id']))
            self.assertTrue((await say('Vi trenger ikke å sjekke produkt X siste 7 dager.'))['ignored'])
            await asyncio.gather(*list(e.jobs), return_exceptions=True)
            specs = sorted((t['product'], t['days'], t['status']) for t in e.tasks.values())
            self.assertEqual(specs, [('X', 30, 'ready'), ('Y', 90, 'ready'), ('Z', 30, 'ready')])
            x = next(t for t in e.tasks.values() if t['product'] == 'X')
            self.assertEqual(x['mentions'], 2)
            z = next(t for t in e.tasks.values() if t['product'] == 'Z')
            self.assertIn('produkt Y → Z', ' '.join(z['notes']))
            # A correction applies to the latest question; landing on an existing one merges instead of duplicating.
            fix = await say('Nei, jeg mente produkt X.')
            self.assertEqual((fix['outcome']['kind'], fix['outcome']['change']), ('amend', 'produkt Y → X'))
            merge = await say('Jeg mente 30 dager.')
            self.assertEqual((merge['outcome']['kind'], merge['id']), ('amend', x['id']))
            await asyncio.gather(*list(e.jobs), return_exceptions=True)
            self.assertEqual(len([t for t in e.tasks.values() if t['status'] != 'cancelled']), 2)
            draft = await say('Lag et GitHub-issue av det.')
            self.assertEqual(draft['outcome']['kind'], 'draft')
            self.assertEqual(len(e.drafts), 1)
        asyncio.run(run())


if __name__ == '__main__':
    unittest.main()
