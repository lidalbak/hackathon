import unittest

from app.conversation import TurnAssembler


class TurnTests(unittest.TestCase):
    def setUp(self):
        self.turns = []
        self.a = TurnAssembler(self.turns.append, clock=lambda: 0)

    def segment(self, start, end, text, final_delay=0.3):
        self.a.speech(True, start)
        self.a.speech(False, end)
        self.a.tick(end + 0.05)
        self.a.final(text, end + final_delay)

    def test_split_sentence_becomes_one_turn(self):
        self.segment(0.0, 2.0, 'Det hadde vært fint å se hvor mange nye kunder')
        self.a.tick(2.9)   # 0.9 s quiet: an unfinished sentence keeps the turn open
        self.segment(3.0, 5.0, 'som bruker produkt X siste 30 dager?')
        self.assertEqual(self.turns, [])
        self.a.tick(5.9)
        self.assertEqual(len(self.turns), 1)
        self.assertEqual(self.turns[0]['text'], 'Det hadde vært fint å se hvor mange nye kunder som bruker produkt X siste 30 dager?')
        self.assertEqual(self.turns[0]['segments'], 2)

    def test_dangling_word_waits_longer(self):
        self.segment(0.0, 1.0, 'Hvor mange kunder som')
        self.a.tick(3.0)
        self.assertEqual(self.turns, [])
        self.a.tick(3.7)
        self.assertEqual(len(self.turns), 1)

    def test_pause_after_question_starts_new_turn(self):
        self.segment(0.0, 1.5, 'Hvor mange bruker produkt X?')
        self.segment(2.6, 3.5, 'Bra, neste punkt.')
        self.assertEqual([t['text'] for t in self.turns], ['Hvor mange bruker produkt X?'])

    def test_waits_for_pending_transcript(self):
        self.a.speech(True, 0.0)
        self.a.speech(False, 1.0)
        self.a.tick(5.0)  # transcript not back yet: never close a turn without its text
        self.assertEqual(self.turns, [])
        self.a.final('Hvor mange bruker produkt Y?', 5.1)
        self.a.tick(6.0)
        self.assertEqual(len(self.turns), 1)

    def test_failed_and_empty_segments_produce_nothing(self):
        self.a.speech(True, 0.0)
        self.a.speech(False, 0.5)
        self.a.failed(0.6)
        self.a.final('', 0.7)
        self.a.tick(10)
        self.a.flush()
        self.assertEqual(self.turns, [])

    def test_flush_on_stop(self):
        self.segment(0.0, 1.0, 'Hvor mange bruker produkt Z')
        self.a.flush()
        self.assertEqual(len(self.turns), 1)


if __name__ == '__main__':
    unittest.main()
