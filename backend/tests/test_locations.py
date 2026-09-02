"""Resolving a board's location string, and storing the result.

The cases here are all real strings from the corpus, which is why several of
them look like typos. "Berlin Metropolitain Area" is spelled that way on the
board.
"""

from __future__ import annotations

import sqlite3

import pytest

from agent_app.core.locations import (
    COUNTRIES,
    REGIONS,
    parse_locations,
    region_for_country,
)
from agent_app.ingest.locations import (
    coverage,
    index_pending_locations,
    index_posting,
    reindex_all_locations,
    top_unresolved,
)


def _labels(raw: str) -> list[str]:
    return [p.label for p in parse_locations(raw)]


def _places(raw: str) -> list[tuple[str | None, str | None]]:
    return [(p.country, p.region) for p in parse_locations(raw)]


class TestParsing:
    @pytest.mark.parametrize(
        ("raw", "country"),
        [
            ("Zurich", "CH"),
            ("Zürich", "CH"),
            ("Zurich, Switzerland", "CH"),
            ("CH-Zurich", "CH"),
            ("Hybrid - Zurich", "CH"),
            ("Zurich (On-site)", "CH"),
        ],
    )
    def test_one_city_written_six_ways(self, raw: str, country: str) -> None:
        """The whole reason this module exists.

        Stored raw these are six different places, so a Zurich filter finds a
        fraction of the Zurich postings.
        """
        assert _places(raw) == [(country, "europe")]

    def test_a_semicolon_separates_two_places(self) -> None:
        assert _labels("London; Berlin") == ["London, United Kingdom", "Berlin, Germany"]

    def test_a_comma_does_not(self) -> None:
        """ "Zurich, Switzerland" is one place, not two."""
        assert _labels("Zurich, Switzerland") == ["Zurich, Switzerland"]

    def test_a_dash_splits_only_when_both_sides_are_cities(self) -> None:
        """The board uses one character for two meanings.

        "Massachusetts - Boston" is one place written general-first; "Zurich -
        Basel" is a list. Whether both sides name a city is what tells them
        apart.
        """
        assert _labels("Massachusetts - Boston") == ["Boston, United States"]
        assert _labels("Zurich - Basel") == ["Zurich, Switzerland", "Basel, Switzerland"]

    def test_a_region_resolves_without_inventing_a_country(self) -> None:
        """ "Remote - EMEA" is a real answer to where, and it is not a country."""
        assert _places("Remote - EMEA") == [(None, "europe")]
        assert _places("APAC") == [(None, "asia")]

    def test_placeless_strings_produce_nothing(self) -> None:
        """`remote` on the posting already carries this; a row would add noise."""
        for raw in ("Remote", "Anywhere", "Multiple Locations", "", "   "):
            assert parse_locations(raw) == []

    def test_an_unknown_place_is_kept_rather_than_dropped(self) -> None:
        """A location the table does not know is a gap to see, not a row to lose."""
        parsed = parse_locations("Vulcan Shipyards, Mars")
        assert len(parsed) == 1
        assert parsed[0].raw.startswith("Vulcan")
        assert not parsed[0].resolved

    def test_a_city_inside_a_longer_phrase_is_found(self) -> None:
        assert _places("Moorgate London") == [("GB", "europe")]
        assert _places("Anywhere in France") == [("FR", "europe")]

    def test_umlauts_resolve_spelled_either_way(self) -> None:
        """Boards write both "München" and "Muenchen"; accent-stripping alone
        turns the first into "munchen", which matches neither."""
        assert _places("München") == [("DE", "europe")]
        assert _places("Muenchen") == [("DE", "europe")]
        assert _places("Köln; Nürnberg") == [("DE", "europe"), ("DE", "europe")]

    def test_a_us_state_pins_the_country_without_a_known_city(self) -> None:
        assert _places("Kent, Washington") == [("US", "north_america")]
        assert _places("Cary, North Carolina") == [("US", "north_america")]

    def test_every_country_sits_in_a_declared_region(self) -> None:
        for code, (_name, region) in COUNTRIES.items():
            assert region in REGIONS, f"{code} has an unknown region {region!r}"
            assert region_for_country(code) == region

    def test_duplicates_within_one_string_collapse(self) -> None:
        assert len(parse_locations("Berlin; Berlin, Germany")) == 1


class TestIndexing:
    @staticmethod
    def _add(conn: sqlite3.Connection, posting_id: str, location: str | None) -> None:
        conn.execute(
            "INSERT INTO postings (id, source, company, title, location, url, body, "
            "body_hash, first_seen, last_seen) VALUES (?, 'greenhouse', 'Acme', "
            "'Intern', ?, 'https://example.com', 'body', 'h', "
            "'2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')",
            (posting_id, location),
        )

    def test_indexing_is_idempotent(self, conn: sqlite3.Connection) -> None:
        """The second pass has nothing to do, including for placeless postings.

        A posting whose string resolves to nothing would look pending forever
        without the sentinel row, which is the case this pins.
        """
        self._add(conn, "greenhouse:1", "Zurich, Switzerland")
        self._add(conn, "greenhouse:2", "Remote")
        conn.commit()

        first = index_pending_locations(conn)
        assert first.pending == 2
        assert index_pending_locations(conn).pending == 0

    def test_a_multi_city_posting_is_filterable_by_either(self, conn: sqlite3.Connection) -> None:
        self._add(conn, "greenhouse:1", "London; Berlin")
        conn.commit()
        index_pending_locations(conn)

        countries = {
            row[0]
            for row in conn.execute(
                "SELECT country FROM posting_locations WHERE posting_id = 'greenhouse:1'"
            )
        }
        assert countries == {"GB", "DE"}

    def test_reindexing_replaces_rather_than_duplicates(self, conn: sqlite3.Connection) -> None:
        """The table is a cache of what the parser currently knows, so widening
        the city table has to be re-runnable without piling up rows."""
        self._add(conn, "greenhouse:1", "Paris, France")
        conn.commit()
        index_pending_locations(conn)
        before = conn.execute("SELECT count(*) FROM posting_locations").fetchone()[0]

        reindex_all_locations(conn)
        after = conn.execute("SELECT count(*) FROM posting_locations").fetchone()[0]
        assert before == after == 1

    def test_editing_a_posting_replaces_its_places(self, conn: sqlite3.Connection) -> None:
        """A manual posting edited from Zurich to Berlin must not stay in both."""
        self._add(conn, "manual:1", "Zurich")
        conn.commit()
        with conn:
            index_posting(conn, "manual:1", "Zurich")
        with conn:
            index_posting(conn, "manual:1", "Berlin, Germany")

        rows = conn.execute(
            "SELECT country FROM posting_locations WHERE posting_id = 'manual:1'"
        ).fetchall()
        assert [row[0] for row in rows] == ["DE"]

    def test_coverage_and_worklist_report_the_gap(self, conn: sqlite3.Connection) -> None:
        self._add(conn, "greenhouse:1", "Berlin, Germany")
        self._add(conn, "greenhouse:2", "Vulcan Shipyards, Mars")
        conn.commit()
        index_pending_locations(conn)

        assert coverage(conn)["with_country"] == 1
        assert coverage(conn)["unresolved"] == 1
        assert top_unresolved(conn)[0][0].startswith("Vulcan")
