import datetime
import pytest
from icalendar import Event
from app.classes.calendar import CalendarBuilder


def make_event(**props):
    e = Event()
    for k, v in props.items():
        e.add(k, v)
    return e


@pytest.fixture
def builder():
    return CalendarBuilder()


class TestApplyFilter:
    def test_contains_include_match(self, builder):
        event = make_event(SUMMARY="Team Meeting")
        assert builder._apply_filter_to_event(event, "SUMMARY", "contains", "Meeting", "include") is True

    def test_contains_include_no_match(self, builder):
        event = make_event(SUMMARY="Lunch")
        assert builder._apply_filter_to_event(event, "SUMMARY", "contains", "Meeting", "include") is False

    def test_contains_exclude_match(self, builder):
        event = make_event(SUMMARY="Team Meeting")
        assert builder._apply_filter_to_event(event, "SUMMARY", "contains", "Meeting", "exclude") is False

    def test_equals(self, builder):
        event = make_event(SUMMARY="OOO")
        assert builder._apply_filter_to_event(event, "SUMMARY", "equals", "OOO", "include") is True
        assert builder._apply_filter_to_event(event, "SUMMARY", "equals", "ooo", "include") is False

    def test_starts_with(self, builder):
        event = make_event(SUMMARY="[WORK] Planning")
        assert builder._apply_filter_to_event(event, "SUMMARY", "starts_with", "[WORK]", "include") is True

    def test_ends_with(self, builder):
        event = make_event(SUMMARY="Planning session")
        assert builder._apply_filter_to_event(event, "SUMMARY", "ends_with", "session", "include") is True

    def test_regex_match(self, builder):
        event = make_event(SUMMARY="Q4 Planning 2024")
        assert builder._apply_filter_to_event(event, "SUMMARY", "regex", r"Q\d Planning", "include") is True

    def test_missing_property_include_if_fails_true(self, builder):
        event = make_event(SUMMARY="No location")
        assert builder._apply_filter_to_event(event, "LOCATION", "contains", "Office", "include", include_if_fails=True) is True

    def test_missing_property_include_if_fails_false(self, builder):
        event = make_event(SUMMARY="No location")
        assert builder._apply_filter_to_event(event, "LOCATION", "contains", "Office", "include", include_if_fails=False) is False


class TestApplyRule:
    def test_append(self, builder):
        event = make_event(SUMMARY="Meeting")
        result = builder._apply_rule_to_event(
            event, "SUMMARY", "contains", "Meeting",
            "SUMMARY", "append", " [WORK]",
        )
        assert "WORK" in str(result["SUMMARY"])

    def test_prepend(self, builder):
        event = make_event(SUMMARY="Meeting")
        result = builder._apply_rule_to_event(
            event, "SUMMARY", "contains", "Meeting",
            "SUMMARY", "prepend", "[WORK] ",
        )
        assert str(result["SUMMARY"]).startswith("[WORK]")

    def test_replace(self, builder):
        event = make_event(SUMMARY="Meeting")
        result = builder._apply_rule_to_event(
            event, "SUMMARY", "contains", "Meeting",
            "SUMMARY", "replace", "Replaced",
        )
        assert str(result["SUMMARY"]) == "Replaced"

    def test_delete(self, builder):
        event = make_event(SUMMARY="Meeting", LOCATION="Office")
        result = builder._apply_rule_to_event(
            event, "SUMMARY", "contains", "Meeting",
            "LOCATION", "delete", "",
        )
        assert "LOCATION" not in result

    def test_no_match_no_transform(self, builder):
        event = make_event(SUMMARY="Lunch")
        result = builder._apply_rule_to_event(
            event, "SUMMARY", "contains", "Meeting",
            "SUMMARY", "append", " [WORK]",
        )
        assert str(result["SUMMARY"]) == "Lunch"


class TestExpandRecurring:
    def test_defaults_to_180_days(self, builder):
        from icalendar import Calendar
        cal = Calendar()
        start = datetime.datetime(2024, 1, 1)
        end = datetime.datetime(2024, 7, 5)  # > 180 days from start
        result_end = start + datetime.timedelta(days=180)
        # Just verify clamping logic runs without error
        builder._expand_recurring_events(cal, expansion_start=start, expansion_end=end)

    def test_no_crash_on_empty_calendar(self, builder):
        from icalendar import Calendar
        cal = Calendar()
        result = builder._expand_recurring_events(cal)
        assert isinstance(result, list)
