"""Tests for classify_intent from src/ctx/retrieval/intent.py."""
from __future__ import annotations
import pytest
from src.ctx.retrieval.intent import classify_intent, TASKS


class TestClassifyIntentReturnType:
    def test_returns_tuple(self):
        result = classify_intent("explain this function")
        assert isinstance(result, tuple)
        assert len(result) == 2

    def test_task_is_string(self):
        task, conf = classify_intent("explain this function")
        assert isinstance(task, str)

    def test_confidence_is_float(self):
        task, conf = classify_intent("explain this function")
        assert isinstance(conf, float)

    def test_task_is_in_known_tasks(self):
        task, conf = classify_intent("explain this function")
        assert task in TASKS

    def test_confidence_in_range(self):
        task, conf = classify_intent("explain this")
        assert 0.0 <= conf <= 1.0


class TestClassifyIntentExplain:
    def test_explain_keyword(self):
        task, conf = classify_intent("explain how this works")
        assert task == "explain"

    def test_what_does_keyword(self):
        task, conf = classify_intent("what does this function do")
        assert task == "explain"

    def test_how_does_keyword(self):
        task, conf = classify_intent("how does the store work")
        assert task == "explain"

    def test_describe_keyword(self):
        task, conf = classify_intent("describe the architecture")
        assert task == "explain"

    def test_overview_keyword(self):
        task, conf = classify_intent("give me an overview")
        assert task == "explain"

    def test_understand_keyword(self):
        task, conf = classify_intent("help me understand this code")
        assert task == "explain"


class TestClassifyIntentBugfix:
    def test_bug_keyword(self):
        task, conf = classify_intent("there is a bug in this code")
        assert task == "bugfix"

    def test_fix_keyword(self):
        task, conf = classify_intent("fix the error in search")
        assert task == "bugfix"

    def test_error_keyword(self):
        task, conf = classify_intent("getting an error when running")
        assert task == "bugfix"

    def test_crash_keyword(self):
        task, conf = classify_intent("the app has a crash on startup")
        assert task == "bugfix"

    def test_exception_keyword(self):
        task, conf = classify_intent("IndexError exception in extractor")
        assert task == "bugfix"

    def test_traceback_keyword(self):
        task, conf = classify_intent("I see a traceback")
        assert task == "bugfix"

    def test_not_working_phrase(self):
        task, conf = classify_intent("why is this not working")
        assert task == "bugfix"

    def test_broken_keyword(self):
        task, conf = classify_intent("the pipeline is broken")
        assert task == "bugfix"


class TestClassifyIntentRefactor:
    def test_refactor_keyword(self):
        task, conf = classify_intent("refactor this module")
        assert task == "refactor"

    def test_rename_keyword(self):
        task, conf = classify_intent("rename this function")
        assert task == "refactor"

    def test_extract_keyword(self):
        task, conf = classify_intent("extract this logic into a helper")
        assert task == "refactor"

    def test_clean_keyword(self):
        task, conf = classify_intent("clean up this messy code")
        assert task == "refactor"

    def test_simplify_keyword(self):
        task, conf = classify_intent("simplify this function")
        assert task == "refactor"

    def test_improve_keyword(self):
        task, conf = classify_intent("improve the code structure")
        assert task == "refactor"


class TestClassifyIntentGenerateTest:
    def test_tests_keyword(self):
        task, conf = classify_intent("write tests for this function")
        assert task == "generate_test"

    def test_test_keyword(self):
        task, conf = classify_intent("add a test for the parser")
        assert task == "generate_test"

    def test_pytest_keyword(self):
        task, conf = classify_intent("pytest coverage for budget")
        assert task == "generate_test"

    def test_generate_tests_phrase(self):
        task, conf = classify_intent("generate tests for the store")
        assert task == "generate_test"

    def test_specs_keyword(self):
        task, conf = classify_intent("write specs for this module")
        assert task == "generate_test"

    def test_coverage_keyword(self):
        task, conf = classify_intent("increase coverage")
        assert task == "generate_test"


class TestClassifyIntentNavigate:
    def test_find_keyword(self):
        task, conf = classify_intent("find where the budget is defined")
        assert task in ("navigate", "explain", "bugfix")  # "find" matches navigate

    def test_where_keyword(self):
        task, conf = classify_intent("where is the store module")
        assert task == "navigate"

    def test_list_keyword(self):
        task, conf = classify_intent("list all functions in the indexer")
        assert task == "navigate"

    def test_locate_keyword(self):
        task, conf = classify_intent("locate the config file")
        assert task == "navigate"


class TestClassifyIntentDefault:
    def test_empty_string_defaults_to_explain(self):
        task, conf = classify_intent("")
        assert task == "explain"
        assert conf == pytest.approx(0.3)

    def test_unknown_query_defaults_to_explain(self):
        task, conf = classify_intent("xyzzy frobulator blorp")
        assert task == "explain"

    def test_confidence_low_for_default(self):
        task, conf = classify_intent("")
        assert conf < 0.5

    def test_case_insensitive_bugfix(self):
        task, conf = classify_intent("BUG in the parser module")
        assert task == "bugfix"

    def test_case_insensitive_explain(self):
        task, conf = classify_intent("EXPLAIN how this works")
        assert task == "explain"

    def test_high_confidence_explicit_match(self):
        task, conf = classify_intent("generate tests for this module")
        assert conf >= 0.9
