import asyncio
import unittest
from starlette.testclient import TestClient
from app.main import app
from app import store
from app.engine import Engine

class StoreTests(unittest.TestCase):
    def setUp(self): store.seed()
    def test_read_only(self):
        with self.assertRaises(Exception): store.query('DELETE FROM customers')
        with self.assertRaises(Exception): store.query("ATTACH DATABASE ':memory:' AS other")
    def test_counts_match_independent_query(self):
        r=store.metric('X',30)
        count=store.query('''SELECT COUNT(DISTINCT e.customer_id) AS n FROM usage_events e
        JOIN customers c ON c.id=e.customer_id WHERE c.internal=0 AND c.created_at>=?
        AND e.occurred_at>=? AND e.product='X' AND e.event_name='product_used' ''',(r['cutoff'],r['cutoff']))[0]['n']
        self.assertEqual(r['active'],count)
        self.assertEqual(r['active']+r['inactive'],r['total'])

class FlowTests(unittest.TestCase):
    def test_api_validation(self):
        with TestClient(app) as c:
            self.assertEqual(c.get('/').status_code,200)
            self.assertEqual(c.post('/api/action',json={'command':'submit','text':''}).status_code,400)
            self.assertEqual(c.post('/api/action',json={'command':'show','id':'missing'}).status_code,404)
    def test_complete_flow(self):
        async def run():
            e=Engine();e.tasks={};e.transcript=[];e.drafts=[];e.save=lambda:None
            ignored=await e.submit('Hei alle sammen')
            self.assertTrue(ignored['ignored'])
            result=await e.submit('Hvor mange nye kunder bruker produkt X siste 30 dager?')
            await asyncio.gather(*list(e.jobs))
            task=e.tasks[result['id']]
            self.assertEqual(task['status'],'ready')
            self.assertTrue(all(s=='done' for s in task['agents'].values()))
            self.assertIn('product_used',task['evidence']['source'])
            duplicate=await e.submit('Hvor mange kunder bruker produkt X siste 30 dager?')
            self.assertTrue(duplicate['duplicate'])
            unclear=await e.submit('Hvor mange kunder bruker produkt Y?')
            self.assertEqual(e.tasks[unclear['id']]['status'],'clarify')
            cancel=await e.submit('Hvor mange kunder bruker produkt Z siste 7 dager?')
            e.tasks[cancel['id']]['status']='cancelled'
            await asyncio.gather(*list(e.jobs))
            self.assertEqual(e.tasks[cancel['id']]['status'],'cancelled')
        asyncio.run(run())

if __name__=='__main__': unittest.main()
