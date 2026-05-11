from flask import Flask, jsonify, request

from predict import predict_fight

app = Flask(__name__)


@app.post('/predict')
def predict():
    payload = request.get_json(silent=True) or {}

    fighter_a = payload.get('fighterA')
    fighter_b = payload.get('fighterB')

    if not fighter_a or not fighter_b:
        return jsonify({'error': 'fighterA and fighterB are required'}), 400

    winner, probabilities = predict_fight(payload)

    return jsonify({
        'winner': winner,
        'probabilities': probabilities,
    })


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
