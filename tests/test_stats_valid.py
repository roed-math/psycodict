# -*- coding: utf-8 -*-
"""
``meta_tables.stats_valid`` records whether a table's counts and statistics
describe its current data.  Every write that changes the data sets it to
false; these tests guard the other direction -- that a refresh sets it back
to true -- and the reload paths, which used to discard the registered
statistics (``restat=False``) or the refreshed ones (``reload_all``).
"""
import pytest

from conftest import sample_row
from psycodict import SQL, Identifier


@pytest.fixture
def saving_table(table_factory):
    """A filled table whose stats object persists what it computes."""
    table = table_factory()
    table.insert_many([sample_row(i) for i in range(30)])
    table.stats.saving = True
    table.stats.add_stats(["flag"])
    return table


def stored_flag(db, name):
    return db._execute(
        SQL("SELECT stats_valid FROM meta_tables WHERE name = %s"), [name]
    ).fetchone()[0]


def registered(table):
    """The statistics refresh_stats would regenerate."""
    return [tuple(cols) for cols, _ccols, _cvals, _threshold in table.stats._status()[0]]


def row_count(table, name):
    return table._execute(SQL("SELECT COUNT(*) FROM {0}").format(Identifier(name))).fetchone()[0]


def flag_split(n):
    return {True: len([i for i in range(n) if i % 3 == 0]),
            False: len([i for i in range(n) if i % 3 != 0])}


def test_refresh_restores_the_flag(db, saving_table):
    name = saving_table.search_table
    saving_table.insert_many([sample_row(i) for i in range(30, 40)], restat=False)
    assert stored_flag(db, name) is False
    saving_table.stats.refresh_stats()
    assert stored_flag(db, name) is True
    assert saving_table._stats_valid is True
    # a write that refreshes leaves it true, one that does not breaks it
    saving_table.insert_many([sample_row(40)])
    assert stored_flag(db, name) is True
    saving_table.upsert({"label": "l0"}, {"flag": False})
    assert stored_flag(db, name) is False


def test_break_does_not_trust_a_stale_cached_flag(db, saving_table):
    # Another connection's refresh sets the stored flag; this object still
    # holds the false it read earlier, and must not skip the update.
    name = saving_table.search_table
    saving_table.stats.refresh_stats()
    saving_table._stats_valid = False
    saving_table.upsert({"label": "l0"}, {"flag": False})
    assert stored_flag(db, name) is False


@pytest.mark.parametrize("inplace", [True, False])
def test_update_from_file_sets_the_flag(db, saving_table, tmp_path, inplace):
    name = saving_table.search_table
    datafile = str(tmp_path / "flags.txt")
    with open(datafile, "w") as F:
        F.write("label|flag\ntext|boolean\n\nl1|t\n")
    saving_table.stats.refresh_stats()
    saving_table.update_from_file(datafile, "label", inplace=inplace, restat=False)
    assert stored_flag(db, name) is False
    saving_table.update_from_file(datafile, "label", inplace=inplace, restat=True)
    assert stored_flag(db, name) is True


def test_reload_without_restat_keeps_the_registered_stats(db, saving_table, tmp_path):
    # With saving on, the swap used to put empty counts and stats tables in
    # place, so the statistics were lost and no refresh could rebuild them.
    name = saving_table.search_table
    searchfile = str(tmp_path / "search.txt")
    saving_table.copy_to(searchfile)
    saving_table.reload(searchfile, restat=False)
    table = db[name]
    table.stats.saving = True
    assert registered(table) == [("flag",)]
    assert table.stats.column_counts("flag") == flag_split(30)
    # the carried-over statistics describe the old data
    assert stored_flag(db, name) is False
    table.stats.refresh_stats()
    assert table.stats.column_counts("flag") == flag_split(30)
    assert stored_flag(db, name) is True
    table.cleanup_from_reload()


def test_reload_with_restat_marks_the_stats_valid(db, saving_table, tmp_path):
    name = saving_table.search_table
    saving_table.insert_many([sample_row(i) for i in range(30, 40)], restat=False)
    searchfile = str(tmp_path / "search.txt")
    saving_table.copy_to(searchfile)
    saving_table.reload(searchfile, restat=True)
    table = db[name]
    assert stored_flag(db, name) is True
    assert table.stats.column_counts("flag") == flag_split(40)
    table.cleanup_from_reload()


def test_reload_all_publishes_refreshed_stats(db, saving_table, tmp_path):
    # Without counts and stats files, reload refreshes the statistics into
    # _tmp tables; reload_all used to swap in only the tables it had files
    # for, leaving the live statistics stale and the refreshed ones orphaned.
    name = saving_table.search_table
    folder = tmp_path / "data"
    db.copy_to([name], str(folder))
    (folder / (name + "_counts.txt")).unlink()
    (folder / (name + "_stats.txt")).unlink()
    # Make the stored statistics stale, so that publishing the refreshed
    # ones is visible.
    saving_table.upsert({"label": "l0"}, {"flag": False})
    db.reload_all(str(folder), restat=True)
    table = db[name]
    assert table.stats.column_counts("flag") == flag_split(30)
    assert stored_flag(db, name) is True
    assert not table._table_exists(table.stats.counts + "_tmp")
    assert not table._table_exists(table.stats.stats + "_tmp")
    table.cleanup_from_reload()


def test_reload_all_takes_the_flag_from_the_metafile(db, saving_table, tmp_path):
    # Statistics loaded from files are only as fresh as the database they were
    # exported from, which its meta_tables row records.
    name = saving_table.search_table
    saving_table.upsert({"label": "l0"}, {"flag": False})
    assert stored_flag(db, name) is False
    folder = tmp_path / "data"
    db.copy_to([name], str(folder))
    db.reload_all(str(folder))
    assert stored_flag(db, name) is False
    db[name].cleanup_from_reload()
