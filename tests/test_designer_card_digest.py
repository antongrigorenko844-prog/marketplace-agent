from datetime import date, datetime, timedelta, timezone
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

import designer_card_digest as d

MON = datetime(2025,12,29,6,5,tzinfo=timezone.utc)
WEEK = '2026-W01'


def task(i=1,status='assigned',due='2025-12-28'):
    return {'id':f'C-{i:010X}','sku':f'SKU-{i}','platform':'Ozon','slide':2,'version':3,
            'status':status,'deadline':due,'comment':'private-review-text','chat_id':123456789}


class WeeklyDigestTest(unittest.TestCase):
    def setUp(self):
        tmp=TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        self.root=Path(tmp.name); self.tasks=[task(1),task(2),task(3)]; self.delivered=[]
        short=patch.object(d,'MAX_BODY',72);short.start();self.addCleanup(short.stop)

    def run_digest(self,now=MON,sender=None,save=d.persist_file,week=None,loader=None):
        return d.send_week(self.root,now,loader or (lambda:self.tasks),lambda:sender or self.delivered.append,
                           save,resume_week=week)

    def test_moscow_time_iso_year_and_empty_week(self):
        for dt in (MON-timedelta(seconds=1),MON-timedelta(minutes=6),MON-timedelta(days=1),MON+timedelta(days=1)):
            self.assertFalse(self.run_digest(dt,loader=Mock(side_effect=AssertionError))['scheduled'])
        self.assertFalse((self.root/d.LEDGER).exists())
        self.tasks=[];self.assertTrue(self.run_digest()['ok']);self.assertIn(WEEK,self.delivered[0])
        for s in ('нет открытых','из переписки не подтверждены','Мои правки','Задача ID'):self.assertIn(s,self.delivered[0])
        self.assertEqual(d.iso_week(date(2021,1,1)),'2020-W53')

    def test_all_morning_down_catches_up_late_monday_once(self):
        afternoon=MON.replace(hour=12,minute=45)  # 15:45 Moscow, morning had no runs
        self.assertTrue(self.run_digest(now=afternoon)['ok'])
        self.assertEqual(len(self.delivered),3)
        last_minute=MON.replace(hour=20,minute=59,second=59)
        self.assertTrue(self.run_digest(now=last_minute,loader=Mock(side_effect=AssertionError))['ok'])
        self.assertEqual(len(self.delivered),3)
        tuesday=last_minute+timedelta(seconds=1)
        self.assertFalse(self.run_digest(now=tuesday)['scheduled'])
        self.assertEqual(len(self.delivered),3)

    def test_old_overdue_due_today_unscheduled_and_closed(self):
        rows=[task(1,'assigned','2024-03-02'),task(2,'returned','2025-12-29'),task(3,'submitted',None),task(4,'accepted','2024-01-01')]
        visible='\n'.join(d.render(rows,MON.date()))
        for val in ['ПРОСРОЧЕНО 02.03.2024','сегодня 29.12.2025','без срока','на доработке','на проверке','открыто 3','просрочено 1','Ozon · слайд 2 · v03']: self.assertIn(val,visible)
        for val in ['SKU-4','private-review-text','123456789']: self.assertNotIn(val,visible)
        self.assertLess(visible.index('SKU-1'),visible.index('SKU-2'))

    def test_exact_supported_catalog_names_with_spaces_and_nonbreaking_hyphen(self):
        skus=['02E 305 045','0BH325183 B','123014‑AF']
        text='\n'.join(d.render([dict(task(i),sku=s) for i,s in enumerate(skus)],MON.date()))
        for s in skus:self.assertIn(s,text)

    def test_render_list_actual_registry_and_no_dedup_writes(self):
        folder=self.root/'data';folder.mkdir();f=folder/'designer_card_tasks.json'
        f.write_text(json.dumps({'version':1,'tasks':{r['id']:r for r in self.tasks},'events':{},'notifications':{}}))
        before=f.read_bytes();self.assertIn('SKU-1','\n'.join(d.render_list(self.root,MON)))
        self.assertFalse((self.root/d.LEDGER).exists());self.assertEqual(f.read_bytes(),before)

    def test_repeat_uses_immutable_snapshot(self):
        self.assertTrue(self.run_digest()['ok']);self.assertEqual(len(self.delivered),3)
        self.tasks=[task(42)];self.assertTrue(self.run_digest(loader=Mock(side_effect=AssertionError))['ok'])
        self.assertEqual(len(self.delivered),3); self.assertNotIn('private-review-text',(self.root/d.LEDGER).read_text())

    def test_explicit_rejection_does_not_block_other_parts(self):
        def send(text):
            if 'часть 2/3' in text: raise d.Rejected(400)
            self.delivered.append(text)
        result=self.run_digest(sender=send);self.assertEqual(result['parts'],{'sent':2,'pending':1});self.assertFalse(result['ok'])
        self.assertTrue(self.run_digest()['ok']);self.assertEqual([x.split('часть ')[1][:3] for x in self.delivered],['1/3','3/3','2/3'])

    def test_ambiguous_send_does_not_duplicate_and_saves_remaining_parts(self):
        def send(text):
            self.delivered.append(text)
            if 'часть 2/3' in text: raise TimeoutError('private-provider-message')
        r=self.run_digest(sender=send);self.assertEqual(r['review'],[{'week':WEEK,'part':2}]);self.assertEqual(len(self.delivered),3)
        self.run_digest();self.assertEqual(len(self.delivered),3)
        with self.assertRaises(d.DigestError):d.resolve(self.root,WEEK,1,True,d.persist_file)
        d.resolve(self.root,WEEK,2,True,d.persist_file);self.assertTrue(self.run_digest()['ok']);self.assertEqual(len(self.delivered),3)
        self.assertNotIn('private-provider-message',(self.root/d.LEDGER).read_text())

    def test_explicit_not_delivered_can_resume_on_tuesday(self):
        def send(text):
            if 'часть 1/3' in text: raise OSError()
            self.delivered.append(text)
        self.run_digest(sender=send);d.resolve(self.root,WEEK,1,False,d.persist_file)
        self.assertTrue(self.run_digest(now=MON+timedelta(days=1),week=WEEK)['ok']);self.assertEqual(len(self.delivered),3)

    def test_rate_limit_persists_cooldown_and_does_not_drop_later_parts(self):
        def limited(_):raise d.Rejected(429,180)
        self.assertFalse(self.run_digest(sender=limited)['ok']);r=self.run_digest(now=MON+timedelta(seconds=35));self.assertFalse(r['ok']);self.assertEqual(self.delivered,[])
        self.assertTrue(self.run_digest(now=MON+timedelta(minutes=4))['ok']);self.assertEqual(len(self.delivered),3)

    def test_ack_checkpoint_failure_leaves_precommitted_claim_no_duplicate(self):
        def crash(root,state):
            if state['weeks'][WEEK][0]['status']=='sent':raise OSError('after-send')
            d.persist_file(root,state)
        with self.assertRaises(OSError):self.run_digest(save=crash)
        self.assertEqual(d.read_state(self.root)['weeks'][WEEK][0]['status'],'sending')
        r=self.run_digest();self.assertEqual(r['parts'],{'sending':1,'sent':2});self.assertEqual(len(self.delivered),3)

    def test_failed_claim_checkpoint_cannot_send(self):
        def no_remote(root,state):
            if state['weeks'][WEEK][0]['status']=='sending':raise OSError('push refused')
            d.persist_file(root,state)
        with self.assertRaises(OSError):self.run_digest(save=no_remote)
        self.assertEqual(self.delivered,[]);self.assertTrue(self.run_digest()['ok'])

    def test_bounded_telegram_text_and_previous_week_loss(self):
        with patch.object(d,'MAX_BODY',3550):
            msgs=d.render([task(i) for i in range(220)],MON.date());self.assertGreater(len(msgs),1);self.assertTrue(all(d.units(msg)<=4096 for msg in msgs))
        self.run_digest(sender=lambda _: (_ for _ in ()).throw(d.Rejected(403)))
        self.tasks=[];self.run_digest(now=MON+timedelta(days=7));self.assertEqual(len(self.delivered),4);self.assertIn('2026-W02',self.delivered[-1])

    def test_parallel_local_entry_and_corrupt_store_fail_closed(self):
        with d.exclusive(self.root),self.assertRaisesRegex(d.DigestError,'busy'):self.run_digest()
        (self.root/'data').mkdir();(self.root/d.LEDGER).write_text('{broken')
        with self.assertRaisesRegex(d.DigestError,'invalid_digest_state'):self.run_digest()
        self.assertEqual(self.delivered,[])

    def test_telegram_receipt_requires_positive_id_and_rejects_rate_limit_without_leaks(self):
        import sys
        from types import SimpleNamespace
        bot=SimpleNamespace(_chat_id=lambda:1234,_token=lambda:'synthetic')
        http=Mock()
        with patch.dict(sys.modules,{'designer_bot':bot,'requests':http}):
            send=d.telegram_sender()
            http.post.return_value.status_code=200
            http.post.return_value.json.return_value={'ok':True,'result':{}}
            with self.assertRaisesRegex(d.DigestError,'unconfirmed_delivery'):send('private-part')
            http.post.return_value.json.return_value={'ok':True,'result':{'message_id':77}}
            send('private-part')
            http.post.return_value.status_code=429
            http.post.return_value.json.return_value={'ok':False,'error_code':429,'parameters':{'retry_after':45}}
            with self.assertRaises(d.Rejected) as err:send('private-part')
            self.assertEqual(err.exception.retry_after,45)
            self.assertFalse(http.post.call_args.kwargs['allow_redirects'])
