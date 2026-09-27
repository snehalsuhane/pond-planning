"""Main OSM API conversion, completeness, bounded downloads and cache behavior."""
import io
from urllib.error import HTTPError
import pytest
from services import osm_water
from services.waterways import WaterwayDataError

XML = b'''<osm version="0.6"><node id="1" lon="81.1" lat="21.1"/>
<node id="2" lon="81.2" lat="21.2"/>
<way id="3"><nd ref="1"/><nd ref="2"/><tag k="waterway" v="stream"/></way>
<way id="4"><nd ref="1"/><nd ref="2"/><tag k="highway" v="track"/></way></osm>'''


class Response(io.BytesIO):
    headers = {}


@pytest.fixture(autouse=True)
def cache(monkeypatch, tmp_path):
    monkeypatch.setenv('WATERWAY_CACHE_DIR', str(tmp_path))
    monkeypatch.delenv('WATERWAY_PROVIDER', raising=False)


def test_map_download_filters_tags_and_reuses_covering_cache(monkeypatch):
    calls = []
    def fetch(req, **kwargs):
        calls.append(req)
        return Response(XML)
    monkeypatch.setattr(osm_water, 'urlopen', fetch)
    result = osm_water.fetch_osm_water((21,81,21.3,81.3))
    assert len(result['elements']) == 1
    assert result['elements'][0]['geometry'] == [{'lon':81.1,'lat':21.1},{'lon':81.2,'lat':21.2}]
    assert result['source'] == 'OpenStreetMap map API'
    assert 'vertices' in result['coverage_note']
    assert calls[0].get_method() == 'GET'
    assert 'bbox=81.0%2C21.0%2C81.3%2C21.3' in calls[0].full_url
    assert osm_water.fetch_osm_water((21.05,81.05,21.25,81.25)) == result
    assert len(calls) == 1
    osm_water.fetch_osm_water((21.2,81.2,21.4,81.4))
    assert len(calls) == 2


def test_missing_water_relation_members_fetched_from_full_endpoint(monkeypatch):
    relation = b'<relation id="10"><member type="way" ref="3" role="outer"/><tag k="natural" v="water"/></relation>'
    full = b'<osm><node id="1" lon="81" lat="21"/><node id="2" lon="81.01" lat="21"/><node id="5" lon="81.01" lat="21.01"/><way id="3"><nd ref="1"/><nd ref="2"/><nd ref="5"/><nd ref="1"/></way>' + relation + b'</osm>'
    calls = []
    def fetch(req, **kwargs):
        calls.append(req.full_url)
        return Response(full if req.full_url.endswith('/relation/10/full') else b'<osm>'+relation+b'</osm>')
    monkeypatch.setattr(osm_water, 'urlopen', fetch)
    result = osm_water.fetch_osm_water((21,81,21.3,81.3))
    assert calls[-1].endswith('/relation/10/full')
    assert len(result['elements'][0]['members'][0]['geometry']) == 4


@pytest.mark.parametrize('xml', [b'<html>oops</html>', b'<osm><error>timeout</error></osm>', b'<osm>',
    b'<osm><way id="3"><nd ref="1"/><nd ref="2"/><tag k="waterway" v="river"/></way></osm>',
    b'<osm><node id="1" lon="81" lat="21"><tag k="natural" v="water"/></node></osm>'])
def test_incomplete_map_is_not_cached(monkeypatch, tmp_path, xml):
    monkeypatch.setattr(osm_water, 'urlopen', lambda *a, **kw: Response(xml))
    with pytest.raises(WaterwayDataError):
        osm_water.fetch_osm_water((21,81,21.3,81.3))
    assert not list(tmp_path.rglob('*.json'))


def test_dense_map_splits_but_deduplicates_objects(monkeypatch):
    calls = []
    def fetch(req, **kwargs):
        calls.append(req.full_url)
        if len(calls) == 1:
            raise HTTPError(req.full_url,400,'Bad Request',{},io.BytesIO(b'Too many nodes'))
        return Response(XML)
    monkeypatch.setattr(osm_water, 'urlopen', fetch)
    result = osm_water.fetch_osm_water((21,81,21.3,81.3))
    assert len(calls) == 5
    assert len(result['elements']) == 1


def test_rate_limit_is_retried_briefly_then_fails(monkeypatch):
    calls = []
    def fetch(req, **kwargs):
        calls.append(1)
        raise HTTPError(req.full_url,429,'Too Many Requests',{},io.BytesIO(b'Rate limit'))
    monkeypatch.setattr(osm_water, 'urlopen', fetch)
    monkeypatch.setattr(osm_water.time, 'sleep', lambda *args: None)
    with pytest.raises(WaterwayDataError):
        osm_water.fetch_osm_water((21,81,21.3,81.3))
    assert len(calls) == 3


def test_expired_cache_not_used_as_success(monkeypatch):
    monkeypatch.setattr(osm_water.time, 'time', lambda: 100000.)
    monkeypatch.setattr(osm_water, 'urlopen', lambda *a, **kw: Response(XML))
    osm_water.fetch_osm_water((21,81,21.3,81.3))
    monkeypatch.setattr(osm_water.time, 'time', lambda: 200000.)
    def fail(*a, **kw):
        raise TimeoutError('offline')
    monkeypatch.setattr(osm_water, 'urlopen', fail)
    with pytest.raises(WaterwayDataError):
        osm_water.fetch_osm_water((21.05,81.05,21.25,81.25))


def test_main_osm_is_default_and_overpass_is_explicit(monkeypatch):
    from services import waterways
    monkeypatch.setattr(osm_water, 'fetch_osm_water', lambda *a, **kw: 'osm')
    monkeypatch.setattr(waterways, 'fetch_overpass_waterways', lambda *a, **kw: 'overpass')
    assert waterways.fetch_waterways((21,81,21.3,81.3)) == 'osm'
    monkeypatch.setenv('WATERWAY_PROVIDER','overpass')
    assert waterways.fetch_waterways((21,81,21.3,81.3)) == 'overpass'
