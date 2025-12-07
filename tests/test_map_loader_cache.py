import os
import pickle
from datetime import datetime, timedelta

import pytest

from src.copepilot.map_loader import MapLoader


TEST_OSM = """<?xml version='1.0' encoding='UTF-8'?>
<osm version='0.6' generator='copepilot-test'>
  <node id='1' lat='51.4600' lon='-2.4600'/>
  <node id='2' lat='51.4610' lon='-2.4610'/>
  <node id='3' lat='51.4620' lon='-2.4620'/>
  <way id='10'>
    <nd ref='1'/>
    <nd ref='2'/>
    <nd ref='3'/>
    <tag k='highway' v='secondary'/>
    <tag k='name' v='Test Way'/>
  </way>
</osm>
"""


def _write_test_osm(pbf_path):
    pbf_path.write_text(TEST_OSM)
    older = (datetime.now() - timedelta(seconds=5)).timestamp()
    os.utime(pbf_path, (older, older))


def test_cache_saved_and_reused(tmp_path, monkeypatch):
    pbf_path = tmp_path / "tiny.osm"
    _write_test_osm(pbf_path)

    loader = MapLoader(pbf_path)
    network = loader.load_around(51.4610, -2.4610, 1000)

    cache_path = pbf_path.with_suffix(".roads.pkl")
    assert cache_path.exists(), "Cache file should be created after first load"
    assert len(network.ways) == 1
    assert network.ways[10].name == "Test Way"

    def _fail_extract(self):
        raise AssertionError("PBF should load from cache when cache is newer")

    monkeypatch.setattr(MapLoader, "_extract_all_roads", _fail_extract)

    loader2 = MapLoader(pbf_path)
    network2 = loader2.load_around(51.4610, -2.4610, 1000)

    assert len(network2.ways) == 1
    assert network2.ways[10].name == "Test Way"


def test_sqlite_cache_created_and_preferred(tmp_path, monkeypatch):
    pbf_path = tmp_path / "tiny.osm"
    _write_test_osm(pbf_path)

    loader = MapLoader(pbf_path)
    loader.load_around(51.4610, -2.4610, 1000)

    sqlite_cache = pbf_path.with_suffix(".roads.sqlite")
    pickle_cache = pbf_path.with_suffix(".roads.pkl")

    assert sqlite_cache.exists(), "SQLite cache should be created alongside pickle cache"
    assert pickle_cache.exists(), "Pickle cache should still be produced for compatibility"

    called = {"region": 0}
    original_region = MapLoader._load_region_from_sqlite

    def _wrapped_region(self, path, lat, lon, radius):
        called["region"] += 1
        return original_region(self, path, lat, lon, radius)

    monkeypatch.setattr(MapLoader, "_load_region_from_sqlite", _wrapped_region, raising=False)
    monkeypatch.setattr(
        MapLoader,
        "_get_full_network",
        lambda self: (_ for _ in ()).throw(AssertionError("Should not fall back")),
        raising=False,
    )

    loader2 = MapLoader(pbf_path)
    loader2.load_around(51.4610, -2.4610, 1000)

    assert called["region"] == 1, "SQLite region loader should satisfy queries"


def test_sqlite_and_pickle_networks_match(tmp_path):
    pbf_path = tmp_path / "tiny.osm"
    _write_test_osm(pbf_path)

    loader = MapLoader(pbf_path)
    expected = loader.load_around(51.4610, -2.4610, 1000)

    sqlite_cache = pbf_path.with_suffix(".roads.sqlite")
    pickle_cache = pbf_path.with_suffix(".roads.pkl")

    sqlite_network = loader._load_from_sqlite(sqlite_cache)
    with open(pickle_cache, "rb") as f:
        pickle_network = pickle.load(f)

    assert len(sqlite_network.nodes) == len(pickle_network.nodes) == len(expected.nodes)
    assert len(sqlite_network.ways) == len(pickle_network.ways) == len(expected.ways)
    assert sqlite_network.ways[10].nodes == pickle_network.ways[10].nodes
    assert sqlite_network.ways[10].name == pickle_network.ways[10].name
