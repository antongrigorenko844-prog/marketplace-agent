from hashlib import sha256
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import openpyxl

from video_copy import CATALOG, MANIFEST, PLATFORMS, build_copy, keyword_evidence


ROOT = Path(__file__).resolve().parents[1]


class VideoCopyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.queue = json.loads((ROOT/"data/designer_video_queue.json").read_text())

    def example(self):
        clips=json.loads((ROOT/MANIFEST).read_text())["clips"]
        if not clips:
            self.skipTest("Нет подготовленных роликов")
        path,clip=next(iter(clips.items()))
        return {"file":path,"articles":clip["articles"]}

    def test_each_queued_path_is_unique_and_reviewed(self):
        manifest=json.loads((ROOT/MANIFEST).read_text())
        paths=[e["file"] for e in self.queue]
        self.assertEqual(len(paths),len(set(paths)))
        self.assertTrue(set(paths)<=manifest["clips"].keys())
        wb=openpyxl.load_workbook(ROOT/"data/ozon_catalog.xlsx",read_only=True,data_only=True)
        try:
            catalog={r[0] for r in wb.active.values}
        finally:
            wb.close()
        for e in self.queue:
            with self.subTest(sku=e["articles"][0]):
                self.assertTrue(e["articles"])
                self.assertTrue(set(e["articles"])<=catalog)
                sections=build_copy(e,ROOT)
                self.assertEqual(tuple(sections),PLATFORMS)
                self.assertEqual(len({s["title"] for s in sections.values()}),3)
                self.assertEqual(len({s["description"] for s in sections.values()}),3)
                for name,s in sections.items():
                    self.assertEqual(tuple(s),("title","description","hashtags"))
                    self.assertLessEqual(len(s["title"]),100)
                    self.assertLessEqual(len(s["description"]),600)
                    self.assertTrue(all(tag.startswith("#") and tag[1:].isalnum()
                                        for tag in s["hashtags"].split()))
                    self.assertIn("#LEOPARTS",s["hashtags"].split())
                    if name!="TikTok":
                        self.assertIn(CATALOG,s["description"])
                    self.assertLess(len(s["title"]+s["description"]+s["hashtags"]),1000)
                    self.assertTrue(any(term["phrase"] in s["description"].casefold()
                                        for term in keyword_evidence(e,ROOT)))

    def test_direct_wordstat_values_and_workbook_audit_trail(self):
        wb=openpyxl.load_workbook(ROOT/"data/seo_keywords.xlsx",read_only=True,data_only=True)
        try:
            rows=list(wb["Семантика"].values)
        finally:
            wb.close()
        for e in self.queue:
            evidence=keyword_evidence(e,ROOT)
            self.assertTrue(evidence)
            self.assertEqual(evidence,sorted(evidence,key=lambda term:-term["count"]))
            for term in evidence:
                with self.subTest(sku=e["articles"][0],phrase=term["phrase"]):
                    # Cache may be refreshed independently; the dated workbook reference
                    # below verifies the original editorial decision.
                    self.assertGreaterEqual(term["count"],0)
                    self.assertEqual(term["cache_key"],term["phrase"]+"|225|DEVICE_ALL")
                    row=rows[term["sheet_row"]-1]
                    self.assertEqual(row[1:5],(term["phrase"],term["reviewed_count"],
                                                "wordstat_api",term["added"]))

    def test_plate_uses_specific_low_frequency_phrase_when_queued(self):
        e=next((row for row in self.queue if row["articles"]==["0AM325467J"]),None)
        if e is None:
            self.skipTest("В действующей очереди нет сепараторной пластины")
        clips=build_copy(e,ROOT)
        for section in clips.values():
            self.assertIn("0AM325467J",section["title"]+" "+section["description"])
            self.assertNotIn("DQ500",str(section).upper())
        self.assertEqual(keyword_evidence(e,ROOT)[0]["phrase"],"сепараторная пластина dq200")

    def test_unreviewed_path_changed_file_and_article_set_fail(self):
        e=self.example()
        for bad in ({**e,"file":"../unrelated.mp4"},
                    {**e,"articles":e["articles"][:-1]}, {**e,"articles":["unmapped"]}):
            with self.assertRaises(ValueError):
                build_copy(bad,ROOT)
        with TemporaryDirectory() as folder:
            changed=Path(folder)/e["file"]
            changed.parent.mkdir(parents=True)
            changed.write_bytes(b"different video")
            local_manifest=Path(folder)/MANIFEST
            local_manifest.parent.mkdir()
            local_manifest.write_bytes((ROOT/MANIFEST).read_bytes())
            with self.assertRaisesRegex(ValueError,"Содержимое ролика"):
                build_copy(e,Path(folder))

    def test_unavailable_frequency_cannot_be_substituted(self):
        with TemporaryDirectory() as folder:
            base=Path(folder)
            (base/"data").mkdir()
            (base/MANIFEST).write_bytes((ROOT/MANIFEST).read_bytes())
            (base/"data/wordstat_cache.json").write_text("{}")
            with self.assertRaisesRegex(ValueError,"нет частотности"):
                keyword_evidence(self.example(),base)

    def test_tenth_clip_added_only_through_manifest(self):
        with TemporaryDirectory() as folder:
            base=Path(folder)
            e={"file":"photos/new-article/second-video.mp4","articles":["new-article"]}
            p=base/e["file"]
            p.parent.mkdir(parents=True)
            p.write_bytes(b"synthetic media for offline test")
            (base/"data").mkdir()
            term="проверочная фраза"
            (base/"data/wordstat_cache.json").write_text(json.dumps({term+"|225|DEVICE_ALL":{"totalCount":"4"}}))
            sections={s:{"title":s+" тест","description":"Проверочная фраза для "+s} for s in PLATFORMS}
            clip={"articles":e["articles"],"sha256":sha256(p.read_bytes()).hexdigest(),"visual_fact":"fixture",
                  "keywords":[{"phrase":term,"count":4,"sheet_row":5,"added":"2026-09-09"}],
                  "platforms":sections,"tags":["Проверка"]}
            def save():
                (base/MANIFEST).write_text(json.dumps({"version":1,"clips":{e["file"]:clip}}))
            save()
            self.assertEqual(tuple(build_copy(e,base)),PLATFORMS)
            sections["TikTok"]["description"]="Лишние слова"
            save()
            with self.assertRaisesRegex(ValueError,"TikTok"):
                build_copy(e,base)


if __name__=="__main__":
    unittest.main()
