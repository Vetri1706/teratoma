import math
import random
import unittest
from src.query import ColumnarChunk, ColumnType, ColumnVector, MorselScheduler, QueryEngine, QuerySnapshot, SelectionVector, WorkStealingRing


class ColumnTests(unittest.TestCase):
    def test_contiguous_typed_vectors_and_bitmap_boundaries(self):
        values = [None if index % 7 == 0 else index for index in range(1024)]
        vector = ColumnVector.from_values(values)
        self.assertEqual(vector.dtype, ColumnType.INT64)
        self.assertEqual(vector.data.format, "q")
        self.assertEqual(vector.data.itemsize, 8)
        self.assertTrue(vector.data.contiguous)
        self.assertTrue(vector.data.readonly)
        self.assertEqual(len(vector.validity.words), 16)
        self.assertEqual(vector.validity.words.itemsize, 8)
        self.assertEqual([vector.value_at(index) for index in range(1024)], values)
        with self.assertRaises(IndexError):
            vector.value_at(-1)
        with self.assertRaises(IndexError):
            vector.value_at(1024)
        with self.assertRaises(ValueError):
            ColumnVector.from_values(list(range(1025)))

    def test_float_promotion_utf8_and_null(self):
        floats = ColumnVector.from_values([1, 1.5, None])
        self.assertEqual(floats.dtype, ColumnType.FLOAT64)
        self.assertEqual(floats.data.format, "d")
        self.assertEqual([floats.value_at(index) for index in range(3)], [1.0, 1.5, None])
        strings = ColumnVector.from_values(["", "hé🌍", None, "zero\x00byte"])
        self.assertEqual(strings.data.format, "B")
        self.assertEqual(strings.offsets.format, "Q")
        self.assertEqual([strings.value_at(index) for index in range(4)], ["", "hé🌍", None, "zero\x00byte"])
        self.assertEqual(list(ColumnVector.from_values([None] * 65).validity.words), [0, 0])

    def test_selection_reuses_buffers(self):
        chunk = ColumnarChunk.from_rows([{"value": value} for value in [None, 0, 4, 8, 12]])
        selected = SelectionVector.scan(chunk).filter("value", ">", 3).filter("value", "<", 10)
        self.assertIs(selected.chunk, chunk)
        self.assertIs(selected.chunk.columns["value"].data, chunk.columns["value"].data)
        self.assertEqual(selected.indices, (2, 3))
        self.assertEqual(SelectionVector.scan(chunk).filter("value", "is_null", None).indices, (0,))

    def test_snapshot_is_detached_and_immutable(self):
        rows = [{"name": "a", "value": 1}]
        snapshot = QuerySnapshot.from_rows(rows)
        rows[0]["value"] = 99
        rows.append({"name": "b", "value": 5})
        self.assertEqual(list(snapshot.iter_rows()), [{"name": "a", "value": 1}])
        with self.assertRaises(TypeError):
            snapshot.chunks[0].columns["value"].data[0] = 9
        with self.assertRaises(TypeError):
            snapshot.schema["new"] = ColumnType.INT64

    def test_schema_and_scalar_validation(self):
        for value in [True, {}, [], math.nan, math.inf]:
            with self.subTest(value=value), self.assertRaises((TypeError, ValueError)):
                ColumnVector.from_values([value])
        with self.assertRaises(OverflowError):
            ColumnVector.from_values([1 << 63])
        with self.assertRaises(TypeError):
            ColumnVector.from_values([1, "a"])
        with self.assertRaises(TypeError):
            QuerySnapshot.from_rows([{"value": 1.5}], {"value": int})
        with self.assertRaises(ValueError):
            QuerySnapshot.from_rows([{"value": 1}, {"other": 2}])
        with self.assertRaises(ValueError):
            QuerySnapshot.from_rows([], {"value": "Boolean"})


class MorselTests(unittest.TestCase):
    def test_ring_wraparound_owner_and_thief_semantics(self):
        ring = WorkStealingRing(3)
        for value in range(3):
            self.assertTrue(ring.push(value))
        self.assertFalse(ring.push(3))
        self.assertEqual(ring.steal(), 0)
        self.assertTrue(ring.push(3))
        self.assertEqual(ring.pop(), 3)
        self.assertEqual(ring.steal(), 1)
        self.assertEqual(ring.pop(), 2)
        for value in range(100):
            self.assertTrue(ring.push(value))
            self.assertEqual(ring.pop() if value % 2 else ring.steal(), value)
        with self.assertRaises(IndexError):
            ring.pop()
        with self.assertRaises(IndexError):
            ring.steal()

    def test_ring_matches_independent_list_model(self):
        ring, model = WorkStealingRing(7), []
        rng = random.Random(831)
        for ordinal in range(10000):
            action = rng.randrange(3)
            if action == 0:
                accepted = ring.push(ordinal)
                self.assertEqual(accepted, len(model) < 7)
                if accepted:
                    model.append(ordinal)
            elif model:
                self.assertEqual(ring.pop() if action == 1 else ring.steal(), model.pop() if action == 1 else model.pop(0))
            self.assertEqual(len(ring), len(model))

    def test_scheduler_steals_without_loss_and_applies_backpressure(self):
        scheduler = MorselScheduler(workers=5, ring_capacity=3)
        observed = []

        def execute(task):
            observed.append(task)
            return task * task

        self.assertEqual(scheduler.run(range(500), execute), [value * value for value in range(500)])
        self.assertEqual(sorted(observed), list(range(500)))
        self.assertGreater(scheduler.stats.steals, 0)
        self.assertGreater(scheduler.stats.backpressure_events, 0)
        self.assertLessEqual(scheduler.stats.peak_ring_depth, 3)
        self.assertEqual(scheduler.stats.submitted, scheduler.stats.completed)
        self.assertEqual(scheduler.run([], execute), [])
        self.assertEqual(scheduler.stats.completed, 0)


class QueryTests(unittest.TestCase):
    aggregates = {"sum": ("sum", "value"), "count": ("count", None), "nonnull": ("count", "value"),
                  "minimum": ("min", "value"), "maximum": ("max", "value"), "average": ("avg", "value")}

    def test_grouped_null_semantics(self):
        rows = [{"group": "a", "value": 2}, {"group": "a", "value": None}, {"group": "a", "value": 4},
                {"group": "b", "value": None}, {"group": None, "value": 8}]
        self.assertEqual(QueryEngine().execute(rows, ["group"], self.aggregates), [
            {"group": "a", "sum": 6, "count": 3, "nonnull": 2, "minimum": 2, "maximum": 4, "average": 3.0},
            {"group": "b", "sum": None, "count": 1, "nonnull": 0, "minimum": None, "maximum": None, "average": None},
            {"group": None, "sum": 8, "count": 1, "nonnull": 1, "minimum": 8, "maximum": 8, "average": 8.0}])

    def test_empty_global_and_grouped_aggregates(self):
        expected = [{"sum": None, "count": 0, "nonnull": 0, "minimum": None, "maximum": None, "average": None}]
        engine = QueryEngine()
        self.assertEqual(engine.execute([], [], self.aggregates), expected)
        self.assertEqual(engine.execute([], ["group"], self.aggregates), [])
        self.assertEqual(engine.execute([{"value": 1}], [], self.aggregates, [("value", ">", 5)]), expected)
        snapshot = engine.snapshot([], {"value": int})
        self.assertEqual(engine.execute_snapshot(snapshot, [], self.aggregates), expected)
        with self.assertRaises(ValueError):
            engine.execute_snapshot(snapshot, [], {"bad": ("sum", "missing")})

    def test_multichunk_against_independent_relational_oracle(self):
        rows = [{"group": "group-" + str(index % 13), "value": None if index % 17 == 0 else index - 1800,
                 "label": "λ" + str(index % 7)} for index in range(4099)]
        expected = {}
        for row in rows:
            if row["value"] is None or row["value"] < -1200 or row["label"] == "λ3":
                continue
            expected.setdefault(row["group"], []).append(row["value"])
        oracle = [{"group": group, "sum": sum(values), "count": len(values), "nonnull": len(values),
                   "minimum": min(values), "maximum": max(values), "average": sum(values) / len(values)}
                  for group, values in expected.items()]
        snapshot = QuerySnapshot.from_rows(rows)
        self.assertEqual([chunk.length for chunk in snapshot.chunks], [1024, 1024, 1024, 1024, 3])
        for workers, morsel in [(1, 1), (2, 17), (4, 128), (7, 2048)]:
            with self.subTest(workers=workers, morsel=morsel):
                engine = QueryEngine(workers, morsel, ring_capacity=3)
                result = engine.execute_snapshot(snapshot, ["group"], self.aggregates,
                                                [("value", ">=", -1200), ("label", "!=", "λ3")])
                self.assertEqual(result, oracle)

    def test_float_result_stability_across_schedules(self):
        rows = [{"value": value, "group": index % 3}
                for index, value in enumerate([1e16, 1.0, -1e16, 0.25, None] * 517)]
        baseline = QueryEngine(1, 1).execute(rows, ["group"], self.aggregates)
        for workers, morsel in [(2, 3), (5, 64), (9, 4096)]:
            self.assertEqual(QueryEngine(workers, morsel).execute(rows, ["group"], self.aggregates), baseline)

    def test_string_extrema_and_null_filters(self):
        rows = [{"value": value} for value in ["z", None, "é", "a"]]
        engine = QueryEngine()
        self.assertEqual(engine.execute(rows, [], {"lo": ("min", "value"), "hi": ("max", "value")}), [{"lo": "a", "hi": "é"}])
        self.assertEqual(engine.execute(rows, [], {"n": ("count", None)}, [("value", "is_null", None)]), [{"n": 1}])
        self.assertEqual(engine.execute(rows, [], {"n": ("count", None)}, [("value", "is_not_null", None)]), [{"n": 3}])
        self.assertEqual(engine.execute(rows, [], {"n": ("count", None)}, [("value", "=", None)]), [{"n": 0}])

    def test_float_aggregate_overflow_is_explicit(self):
        with self.assertRaises(OverflowError):
            QueryEngine().execute([{"value": 1e308}, {"value": 1e308}], [], {"s": ("sum", "value")})

    def test_invalid_query_is_rejected_before_execution(self):
        rows = [{"value": 2, "text": "a"}]
        invalid = [(["missing"], {"n": ("count", None)}, []), (["value", "value"], {"n": ("count", None)}, []),
                   (["value"], {"value": ("sum", "value")}, []), ([], {"n": ("sum", "text")}, []),
                   ([], {"n": ("median", "value")}, []), ([], {"n": ("sum", None)}, []),
                   ([], {"n": ("count", None)}, [("value", "=", "2")]),
                   ([], {"n": ("count", None)}, [("value", "approximately", 2)]),
                   ([], {"n": ("count", None)}, [("value", "is_null", 2)]), ([], {}, [])]
        for groups, aggregates, filters in invalid:
            with self.subTest(groups=groups, aggregates=aggregates, filters=filters), self.assertRaises((TypeError, ValueError)):
                QueryEngine().execute(rows, groups, aggregates, filters)
        for options in [{"workers": 0}, {"workers": True}, {"morsel_size": 0}, {"ring_capacity": -1}]:
            with self.assertRaises(ValueError):
                QueryEngine(**options)


if __name__ == "__main__":
    unittest.main()
