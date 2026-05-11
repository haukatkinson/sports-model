<?php
$fighterA = $_GET['fighterA'] ?? '';
$fighterB = $_GET['fighterB'] ?? '';

$prediction = null;
$error = null;

if ($fighterA !== '' && $fighterB !== '') {
    $payload = json_encode([
        'fighterA' => $fighterA,
        'fighterB' => $fighterB,
    ]);

    $context = stream_context_create([
        'http' => [
            'method' => 'POST',
            'header' => "Content-Type: application/json\r\n",
            'content' => $payload,
            'timeout' => 10,
        ]
    ]);

    $response = @file_get_contents('http://localhost:5000/predict', false, $context);

    if ($response === false) {
        $error = 'Prediction service is unavailable. Make sure Flask API is running.';
    } else {
        $decoded = json_decode($response, true);
        if (json_last_error() !== JSON_ERROR_NONE) {
            $error = 'Invalid API response.';
        } else {
            $prediction = $decoded;
        }
    }
}
?>
<!doctype html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Prediction Result</title>
</head>
<body>
  <h1>Prediction Result</h1>
  <p><a href="index.php">Back</a></p>

  <?php if ($fighterA === '' || $fighterB === ''): ?>
    <p>Please provide both fighter names.</p>
  <?php elseif ($error): ?>
    <p><?= htmlspecialchars($error) ?></p>
  <?php else: ?>
    <h2><?= htmlspecialchars($fighterA) ?> vs <?= htmlspecialchars($fighterB) ?></h2>
    <p>Predicted winner: <strong><?= htmlspecialchars($prediction['winner']) ?></strong></p>
    <p><?= htmlspecialchars($fighterA) ?> win probability: <?= number_format(($prediction['probabilities'][$fighterA] ?? 0) * 100, 2) ?>%</p>
    <p><?= htmlspecialchars($fighterB) ?> win probability: <?= number_format(($prediction['probabilities'][$fighterB] ?? 0) * 100, 2) ?>%</p>
  <?php endif; ?>
</body>
</html>
