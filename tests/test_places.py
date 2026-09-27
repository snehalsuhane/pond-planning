import io
import json

import pytest
from app import create_app
import services.places as places


@pytest.fixture
def search_client(monkeypatch):
    places._CACHE.clear()
    monkeypatch.setattr(places, '_LAST_REQUEST', 0.)
    monkeypatch.setattr(places.time, 'sleep', lambda seconds: None)
    monkeypatch.delenv('NOMINATIM_ENDPOINT', raising=False)
    yield create_app().test_client()
    places._CACHE.clear()


def test_search_normalizes_coordinates_and_caches_query(search_client, monkeypatch):
    calls = []
    def fetch(request, **kwargs):
        calls.append(request)
        return io.BytesIO(json.dumps([{'display_name':'Test Village', 'lat':'21.2', 'lon':'81.3',
                                      'boundingbox':['21.1','21.3','81.2','81.4']}]).encode())
    monkeypatch.setattr(places, 'urlopen', fetch)
    first = search_client.get('/api/places/search', query_string={'q':'Test Village'})
    second = search_client.get('/api/places/search', query_string={'q':' test   village '})
    assert first.status_code == 200 and first.json == second.json
    assert len(calls) == 1
    assert calls[0].get_header('User-agent').startswith('VillagePondPlanner/')
    assert first.json['results'][0]['bounds'] == [[21.1,81.2],[21.3,81.4]]
    assert first.json['results'][0]['latitude'] == 21.2


@pytest.mark.parametrize('query', ['', 'x', ' '*5, 'x'*201])
def test_invalid_query_does_not_reach_provider(search_client, monkeypatch, query):
    monkeypatch.setattr(places, 'urlopen', lambda *args, **kwargs: pytest.fail('Unexpected request'))
    assert search_client.get('/api/places/search', query_string={'q':query}).status_code == 400


def test_empty_search_results(search_client, monkeypatch):
    monkeypatch.setattr(places, 'urlopen', lambda *a, **kw: io.BytesIO(b'[]'))
    assert search_client.get('/api/places/search?q=nowhere').json == {'results':[]}


@pytest.mark.parametrize('data', [b'not json', b'{}', b'[{"lat":"NaN","lon":"81","display_name":"bad"}]'])
def test_bad_provider_response_not_cached(search_client, monkeypatch, data):
    monkeypatch.setattr(places, 'urlopen', lambda *a, **kw: io.BytesIO(data))
    assert search_client.get('/api/places/search?q=village').status_code == 503
    assert not places._CACHE


def test_outage_returns_retryable_message(search_client, monkeypatch):
    def fail(*args, **kwargs):
        raise OSError('Network is unreachable')
    monkeypatch.setattr(places, 'urlopen', fail)
    response = search_client.get('/api/places/search?q=village')
    assert response.status_code == 503
    assert 'coordinates' in response.json['error']


def test_rate_limit_and_configurable_endpoint(search_client, monkeypatch):
    clock = [100.]
    starts = []
    monkeypatch.setattr(places.time, 'monotonic', lambda: clock[0])
    monkeypatch.setattr(places.time, 'sleep', lambda seconds: clock.__setitem__(0, clock[0]+seconds))
    monkeypatch.setenv('NOMINATIM_ENDPOINT', 'https://example.test/search')
    def fetch(request, **kwargs):
        starts.append(clock[0])
        assert request.full_url.startswith('https://example.test/search?')
        return io.BytesIO(b'[]')
    monkeypatch.setattr(places, 'urlopen', fetch)
    search_client.get('/api/places/search?q=one')
    search_client.get('/api/places/search?q=two')
    assert starts[1]-starts[0] >= 1
