"""Calculate and screen a proposed pond without rerunning terrain analysis."""
from flask import Blueprint, current_app, jsonify, request
from services.pond_design import design_pond

pond_design_bp = Blueprint('pond_design', __name__)


@pond_design_bp.post('/designPond')
def design():
    try:
        result = design_pond(request.get_json(silent=True),
                             water_buffer_m=current_app.config['WATERWAY_BUFFER_M'])
    except ValueError as exc:
        return jsonify({'status': 'error', 'error': str(exc)}), 400
    return jsonify(result)


@pond_design_bp.post('/simulatePond')
def simulate():
    from services.storage import simulate_pond
    from services.rainfall import RainfallDataError
    try:
        result = simulate_pond(request.get_json(silent=True))
    except RainfallDataError as exc:
        return jsonify({'status': 'error', 'error': str(exc)}), 503
    except ValueError as exc:
        return jsonify({'status': 'error', 'error': str(exc)}), 400
    return jsonify(result)
