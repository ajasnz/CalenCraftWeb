import json, requests, os, datetime, time, logging
import regex as re
import recurring_ical_events as rie
from icalendar import Calendar, Event

log = logging.getLogger(__name__)


class CalendarBuilder:
    def __init__(self):
        """
        Initialize a CalendarBuilder instance.

        Sets `self.db` to None. Other attributes are set later in `build()`.
        """
        self.db = None

    def _init_db(self):
        """
        Placeholder for database initialization.

        This method currently performs no action and returns None. It exists
        as a hook where a database connection or adapter may be initialized
        and assigned to `self.db` by future implementations.
        """
        pass

    def _fetch_source_events(self):
        """
        Fetch iCalendar data from source URLs returned by the database.

        Calls `self.db.fetch_source_urls(...)` to obtain iterable pairs of
        `(id, url)`. For each URL it attempts an HTTP GET and parses the
        returned iCal text with `Calendar.from_ical`. On error it prints a
        message and skips that source.

        Returns a list of tuples `(id, icalendar.Calendar)` for successfully
        fetched and parsed sources.
        """

        sources_to_build = self.db.fetch_source_urls(
            user=self.user_slug,
            calendar=self.calendar_slug,
            view=self.view_slug,
            includes=["id", "url"],
        )

        events_by_source = []
        for id, url in sources_to_build:
            try:
                this_source_raw_ical = requests.get(
                    url, timeout=15, headers={"User-Agent": "CalenCraft/1.0"}
                ).text
                this_source_events = Calendar.from_ical(this_source_raw_ical)
                events_by_source.append((id, this_source_events))
            except Exception as e:
                log.warning("Error fetching source %s at %s: %s", id, url, e)
                continue
        return events_by_source

    def _normalize_datetime(self, value):
        """
        Normalize a date-like value to a `datetime.datetime` instance.

        - If `value` is a `datetime.datetime`, it is returned unchanged.
        - If `value` is a `datetime.date` (but not `datetime.datetime`), it
          is converted to a `datetime.datetime` at midnight.
        - For any other type, returns `None`.
        """

        if isinstance(value, datetime.datetime):
            return value
        if isinstance(value, datetime.date):
            return datetime.datetime.combine(value, datetime.time.min)
        return None

    def _expand_recurring_events(
        self, calendar, expansion_start=None, expansion_end=None
    ):
        """
        Expand recurring events in `calendar` into individual occurrences.

        - Enforces a maximum expansion window of 180 days.
        - If `expansion_start` is not provided, defaults to `datetime.now()`.
        - If `expansion_end` is not provided it is set to `expansion_start + 180 days`.
        - If `expansion_end` exceeds `expansion_start + 180 days`, it is clamped.

        If the `recurring_ical_events` library (`rie`) is unavailable, this
        function returns the raw VEVENT components via
        `calendar.walk("VEVENT")`. Otherwise it returns the occurrences
        produced by `rie.of(calendar, skip_bad_series=True).between(start, end)`.
        """
        # Hard ceiling — per-calendar config controls the practical window via feed.py/public.py
        max_days = 3650

        # Default expansion_start to now if not provided.
        if expansion_start is None:
            expansion_start = datetime.datetime.now()

        # If expansion_end not provided, set to expansion_start + 180 days
        if expansion_end is None:
            expansion_end = expansion_start + datetime.timedelta(days=max_days)

        # Clamp expansion_end to expansion_start + max_days
        max_end = expansion_start + datetime.timedelta(days=max_days)
        if expansion_end > max_end:
            expansion_end = max_end

        if rie is None:
            # Fallback: cannot expand without library; return raw VEVENTs
            return list(calendar.walk("VEVENT"))

        query = rie.of(calendar, skip_bad_series=True)
        return list(query.between(expansion_start, expansion_end))

    def _apply_filter_to_event(
        self,
        event,
        filter_property,
        filter_type,
        filter_pattern,
        filter_action,
        include_if_fails=False,
    ):
        """
        event: the calendar event to check as an icalendar Event object

        filter_property: the property of the event to check (e.g. "SUMMARY", "DESCRIPTION", "LOCATION", X-SOURCE-ID)

        filter_type: the type of filter to apply ("regex", "contains", "equals", "starts_with", "ends_with", "not_regex", "not_contains", "not_equals", "not_starts_with", "not_ends_with")

        filter_pattern: the pattern to check against (e.g. "Meeting", "Office", ".*Conference.*")

        filter_action: the action to take if the filter matches ("include", "exclude")

        include_if_fails: if True, the event will be included if the filter property is not present in the event. If False, the event will be excluded if the filter property is not present in the event.

        Returns True if the event should be included based on the filter, False if it should be excluded.

        """

        # "*" means "all events" — always matches, no condition check needed
        if filter_property == "*":
            return True

        if filter_property not in event:
            return True if include_if_fails else False

        property_value = str(event[filter_property])
        if filter_type == "regex":
            match = re.search(filter_pattern, property_value)
            return (
                (match is not None) if filter_action == "include" else (match is None)
            )
        elif filter_type == "contains":
            match = filter_pattern in property_value
            return match if filter_action == "include" else not match
        elif filter_type == "equals":
            match = filter_pattern == property_value
            return match if filter_action == "include" else not match
        elif filter_type == "starts_with":
            match = property_value.startswith(filter_pattern)
            return match if filter_action == "include" else not match
        elif filter_type == "ends_with":
            match = property_value.endswith(filter_pattern)
            return match if filter_action == "include" else not match
        elif filter_type == "not_regex":
            match = re.search(filter_pattern, property_value)
            return (
                (match is None) if filter_action == "include" else (match is not None)
            )
        elif filter_type == "not_contains":
            match = filter_pattern in property_value
            return not match if filter_action == "include" else match
        elif filter_type == "not_equals":
            match = filter_pattern == property_value
            return not match if filter_action == "include" else match
        elif filter_type == "not_starts_with":
            match = property_value.startswith(filter_pattern)
            return not match if filter_action == "include" else match
        elif filter_type == "not_ends_with":
            match = property_value.endswith(filter_pattern)
            return not match if filter_action == "include" else match

        return True if include_if_fails else False

    def _apply_rule_to_event(
        self,
        event,
        filter_property,
        filter_type,
        filter_pattern,
        target_property,
        action,
        target_value,
        include_if_fails=False,
        filter_action="include",
    ):
        """
        Conditionally apply a transformation rule to an `event`.

        - First evaluates the filter using `_apply_filter_to_event(...)` with
          the provided filter parameters and `include_if_fails`/`filter_action`.
        - If the filter check returns True, performs one of the following
          actions on `target_property`:
            - "append": append `target_value` to the existing value (or
              empty string if absent).
            - "prepend": prepend `target_value` to the existing value.
            - "replace": set `target_property` to `target_value`.
            - "delete": delete `target_property` from the event if present.

        Returns the (possibly modified) `event` object.
        """

        if self._apply_filter_to_event(
            event,
            filter_property,
            filter_type,
            filter_pattern,
            filter_action,
            include_if_fails,
        ):
            if action == "append":
                event[target_property] = event.get(target_property, "") + target_value
            elif action == "prepend":
                event[target_property] = target_value + event.get(target_property, "")
            elif action == "replace":
                event[target_property] = target_value
            elif action == "delete":
                if target_property in event:
                    del event[target_property]
        return event

    def build(
        self,
        user_slug,
        calendar_slug,
        view_slug,
        expansion_start=None,
        expansion_end=None,
    ):
        """
        Build and return an aggregated iCalendar for the given identifiers.

        - Stores `user_slug`, `calendar_slug`, `view_slug` and resets internal
            `rules` and `filters` containers.
        - Ensures `self.db` is initialized by calling `_init_db()` if needed.
        - Fetches source calendars via `_fetch_source_events()`.
        - Loads rules and filters from `self.db` for sources, calendar,
            and view, then applies filters and transformation rules to the
            fetched events. Filters and rules may be scoped to a specific
            source (via `source_id`) or applied to all sources.
        - Tags each event with an `X-SOURCE-ID` property when merging.
        - Expands recurring events using `_expand_recurring_events(...)`.
        - Constructs a final `icalendar.Calendar`, adds occurrences and
            returns the serialized iCal bytes via `to_ical()`.

        The method does not modify external state except through `self.db`
        method calls it invokes (e.g., fetching rules/filters).
        """

        self.user_slug = user_slug
        self.calendar_slug = calendar_slug
        self.view_slug = view_slug
        self.rules = {}
        self.filters = {}

        self.db = self.db or self._init_db()

        source_calendars = self._fetch_source_events()

        self.rules["sources"] = self.db.fetch_rules_for_source(
            user=self.user_slug,
            calendar=self.calendar_slug,
            order_by="source_id, priority",
        )
        self.rules["calendar"] = self.db.fetch_rules_for_calendar(
            user=self.user_slug, calendar=self.calendar_slug, order_by="priority"
        )
        self.rules["view"] = self.db.fetch_rules_for_view(
            user=self.user_slug,
            calendar=self.calendar_slug,
            view=self.view_slug,
            order_by="priority",
        )
        self.filters["sources"] = self.db.fetch_filters_for_source(
            user=self.user_slug,
            calendar=self.calendar_slug,
            order_by="source_id, priority",
        )
        self.filters["calendar"] = self.db.fetch_filters_for_calendar(
            user=self.user_slug, calendar=self.calendar_slug, order_by="priority"
        )
        self.filters["view"] = self.db.fetch_filters_for_view(
            user=self.user_slug,
            calendar=self.calendar_slug,
            view=self.view_slug,
            order_by="priority",
        )

        all_rules = self.rules["sources"] + self.rules["calendar"] + self.rules["view"]
        all_filters = (
            self.filters["sources"] + self.filters["calendar"] + self.filters["view"]
        )

        merged_calendar = Calendar()

        events_by_source = []
        for source_id, source_calendar in source_calendars:
            events = list(source_calendar.walk("vevent"))
            events_by_source.append((source_id, events))

        # Tag components with their source id
        for source_id, events in events_by_source:
            for event in events:
                event.add("X-SOURCE-ID", source_id)

        for filter in all_filters:
            source_id = filter.get("source_id")
            if source_id:
                for i, (sid, events) in enumerate(events_by_source):
                    if sid == source_id:
                        filtered_events = []
                        for event in events:
                            if self._apply_filter_to_event(
                                event,
                                filter["property"],
                                filter["type"],
                                filter["pattern"],
                                filter["action"],
                                include_if_fails=filter.get("include_if_fails", False),
                            ):
                                filtered_events.append(event)
                        events_by_source[i] = (sid, filtered_events)
            else:
                # Apply to all sources
                for i, (sid, events) in enumerate(events_by_source):
                    filtered_events = []
                    for event in events:
                        if self._apply_filter_to_event(
                            event,
                            filter["property"],
                            filter["type"],
                            filter["pattern"],
                            filter["action"],
                            include_if_fails=filter.get("include_if_fails", False),
                        ):
                            filtered_events.append(event)
                    events_by_source[i] = (sid, filtered_events)

        for rule in all_rules:
            source_id = rule.get("source_id")
            if source_id:
                for i, (sid, events) in enumerate(events_by_source):
                    if sid == source_id:
                        transformed_events = []
                        for event in events:
                            filter_action = rule.get("filter_action", "include")
                            transformed_event = self._apply_rule_to_event(
                                event,
                                rule["filter_property"],
                                rule["filter_type"],
                                rule["filter_pattern"],
                                rule["target_property"],
                                rule["action"],
                                rule["target_value"],
                                include_if_fails=rule.get("include_if_fails", False),
                                filter_action=filter_action,
                            )
                            transformed_events.append(transformed_event)
                        events_by_source[i] = (sid, transformed_events)
            else:
                # Apply to all sources
                for i, (sid, events) in enumerate(events_by_source):
                    transformed_events = []
                    for event in events:
                        filter_action = rule.get("filter_action", "include")
                        transformed_event = self._apply_rule_to_event(
                            event,
                            rule["filter_property"],
                            rule["filter_type"],
                            rule["filter_pattern"],
                            rule["target_property"],
                            rule["action"],
                            rule["target_value"],
                            include_if_fails=rule.get("include_if_fails", False),
                            filter_action=filter_action,
                        )
                        transformed_events.append(transformed_event)
                    events_by_source[i] = (sid, transformed_events)

        for source_id, events in events_by_source:
            for event in events:
                merged_calendar.add_component(event)

        occurrences = self._expand_recurring_events(
            merged_calendar,
            expansion_start=expansion_start,
            expansion_end=expansion_end,
        )

        final_calendar = Calendar()
        final_calendar.add("prodid", "-//CalenCraft//EN")
        final_calendar.add("version", "2.0")
        for event in occurrences:
            final_calendar.add_component(event)
        return final_calendar.to_ical()
