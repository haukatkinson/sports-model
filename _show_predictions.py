import json
from pathlib import Path

pred = json.loads(Path('data/nearest_event_predictions.json').read_text(encoding='utf-8'))
meta = pred.get('__meta__', {})
print('Generated:', meta.get('generated_at', '?')[:16])
print('Total Fights:', meta.get('total_fights', '?'))
print()

items = [(k, v) for k, v in pred.items() if k != '__meta__']
items.sort(key=lambda x: abs(0.5 - list(x[1]['probabilities'].values())[0]), reverse=True)

for idx, (_, fight_data) in enumerate(items, 1):
    probs = fight_data['probabilities']
    names = list(probs.keys())
    if len(names) < 2:
        continue
    a, b = names[0], names[1]
    pa, pb = probs[a], probs[b]
    winner = fight_data['winner']
    method_probs = fight_data.get('predicted_method_probabilities', {})
    
    print('{:2d}. {:25s} vs {:25s}'.format(idx, a, b))
    print('    Model: {}: {:5.1f}%  |  {}: {:5.1f}%'.format(a, pa*100, b, pb*100))
    print('    Pick: {}'.format(winner))
    ko = method_probs.get('KO/TKO', 0)
    sub = method_probs.get('Submission', 0)
    dec = method_probs.get('Decision', 0)
    print('    Method: KO/TKO {:3.0f}%  |  SUB {:3.0f}%  |  DEC {:3.0f}%'.format(ko*100, sub*100, dec*100))
    print()
