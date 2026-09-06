from flask import jsonify, session

def json_response(data=None, message=None, success=True, status_code=200, **extra):
    response = {
        'success': success,
        'message': message or ('Operation successful' if success else 'An error occurred'),
        'data': data
    }
    if extra:
        response.update(extra)
    return jsonify(response), status_code

def error_response(message, status_code=400):
    return json_response(data=None, message=message, success=False, status_code=status_code)

