"""Place search for map navigation; independent of terrain analysis."""
from flask import Blueprint, jsonify, request
from services.places import search_places, PlaceSearchError

places_bp = Blueprint('places', __name__)


@places_bp.get('/places/search')
def search():
    try:
        results = search_places(request.args.get('q', ''))
    except PlaceSearchError as exc:
        return jsonify({'error': str(exc)}), 503
    except ValueError as exc:
        return jsonify({'error': str(exc)}), 400
    return jsonify({'results': results})
